import json
import os
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

import bot
import ig_client
import sessions
from transcript import (
    TranscriptBlockedError,
    TranscriptError,
    UnsupportedSourceError,
    detect_source,
    get_transcript_config,
    get_transcript,
)
from recipe_ai import RecipeExtractionError, extract_recipe, get_ai_config
from reel import get_reel_config
from ecommerce import (
    ProductSearchError,
    build_search_links,
    get_store_capabilities,
    get_store_capability,
    get_supported_stores,
    search_products,
)
from product_matcher import find_best_product


app = FastAPI(
    title="ReFill API",
    version="0.1.0",
    description="Turn a cooking video transcript into a grocery-ready ingredient list.",
)

FRONTEND_DIR = Path(__file__).parent / "frontend"

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

if FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")


class RecipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(
        default=None,
        description="YouTube video, YouTube Shorts, or YouTube share URL.",
    )
    transcript: str | None = Field(
        default=None,
        description="Optional transcript override for testing or unsupported video sources.",
    )
    store: Literal["all", "jiomart", "zepto", "instamart", "blinkit"] = "all"
    include_product_search: bool = Field(
        default=False,
        description="Call a configured product search API. Off by default because quick-commerce public cart APIs are not reliable.",
    )

    @model_validator(mode="after")
    def require_url_or_transcript(self):
        if self.url is not None:
            self.url = self.url.strip() or None
        if self.transcript is not None:
            self.transcript = self.transcript.strip() or None

        if not self.url and not self.transcript:
            raise ValueError("Provide either url or transcript.")
        return self


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ai": get_ai_config(),
        "transcript": get_transcript_config(),
        "instagram": ig_client.get_config(),
        "reel": get_reel_config(),
        "supported_video_sources": [
            "youtube",
            "youtube_shorts",
            "instagram_reel_dm_share",
        ],
        "unsupported_video_sources": ["instagram_reel_public_url"],
        "commerce_mode": "search_links",
        "stores": get_supported_stores(),
        "store_capabilities": get_store_capabilities(),
        "public_base_url": bot.get_public_base_url(),
    }


@app.get("/", include_in_schema=False)
def root():
    if FRONTEND_DIR.exists():
        return RedirectResponse("/ui/")
    return {"status": "ok"}


@app.post("/recipe")
def process_recipe(request: RecipeRequest):
    source_type = detect_source(request.url) if request.url else "manual_transcript"

    try:
        transcript = request.transcript or get_transcript(request.url or "")
    except UnsupportedSourceError as exc:
        raise _http_error(422, "unsupported_source", str(exc)) from exc
    except ValueError as exc:
        raise _http_error(422, "invalid_video_url", str(exc)) from exc
    except TranscriptBlockedError as exc:
        raise _http_error(424, "youtube_transcript_blocked", str(exc)) from exc
    except TranscriptError as exc:
        raise _http_error(424, "transcript_unavailable", str(exc)) from exc

    try:
        recipe = extract_recipe(transcript)
    except RecipeExtractionError as exc:
        raise _http_error(502, "recipe_extraction_failed", str(exc)) from exc

    items = []

    for ingredient in recipe.ingredients:
        products = []
        product_search_error = None

        if request.include_product_search:
            try:
                products = search_products(ingredient.name)
            except ProductSearchError:
                product_search_error = "Product search failed."

        best_product = find_best_product(ingredient, products)
        search_links = build_search_links(ingredient.name, request.store)

        item = {
            "ingredient": ingredient.model_dump(),
            "matched_product": best_product,
            "search_links": search_links,
        }
        if product_search_error:
            item["product_search_error"] = product_search_error

        items.append(item)

    return {
        "source": {
            "type": source_type,
            "url": request.url,
            "transcript_chars": len(transcript),
        },
        "recipe": recipe.model_dump(),
        "cart": {
            "mode": "search_links",
            "store": request.store,
            "items": items,
            "note": "Direct cart creation requires official partner APIs or user-authenticated store integrations.",
        },
    }


@app.get("/webhook/instagram", include_in_schema=False)
def instagram_webhook_verify(request: Request):
    """Meta's subscription handshake: echo hub.challenge when the token matches."""
    params = request.query_params
    challenge = ig_client.verify_subscription(
        params.get("hub.mode"),
        params.get("hub.verify_token"),
        params.get("hub.challenge"),
    )

    if challenge is None:
        raise _http_error(
            403,
            "webhook_verification_failed",
            "hub.verify_token did not match IG_VERIFY_TOKEN.",
        )

    return PlainTextResponse(challenge)


@app.post("/webhook/instagram")
async def instagram_webhook(request: Request, background: BackgroundTasks):
    """Accept Instagram DM events.

    Meta retries anything slower than a few seconds, so the reel download, the
    transcription, and the LLM call all run after the 200 is on the wire.
    """
    raw_body = await request.body()

    if not ig_client.verify_signature(raw_body, request.headers.get("x-hub-signature-256")):
        raise _http_error(
            403,
            "invalid_signature",
            "X-Hub-Signature-256 did not match IG_APP_SECRET.",
        )

    try:
        payload = json.loads(raw_body or b"{}")
    except ValueError as exc:
        raise _http_error(400, "invalid_payload", "Webhook body was not JSON.") from exc

    queued = 0
    for message in ig_client.parse_events(payload):
        if message.is_echo:
            continue
        if not sessions.claim_event(message.message_id):
            continue

        background.add_task(bot.handle_message, message)
        queued += 1

    return {"status": "ok", "queued": queued}


@app.get("/api/basket/{token}")
def read_basket(token: str):
    basket = sessions.load_basket(token)
    if basket is None:
        raise _http_error(404, "basket_not_found", "That basket link has expired.")

    store = basket.get("store", "")
    capability = get_store_capability(store)
    return {
        "token": token,
        "store": store,
        "store_name": capability["name"],
        "capability": capability,
        "recipe_name": basket.get("recipe_name") or "Recipe basket",
        "servings": basket.get("servings"),
        "reel_sources": basket.get("reel_sources") or [],
        "items": basket.get("items") or [],
    }


@app.get("/b/{token}", include_in_schema=False)
def basket_page(token: str):
    page = FRONTEND_DIR / "basket.html"
    if not page.exists():
        raise _http_error(404, "basket_page_missing", "Basket page is not deployed.")
    return FileResponse(page)


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
