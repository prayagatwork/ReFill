"""ReFill's Instagram DM bot: reel in, quick-commerce basket out.

Conversation contract:

1. A user shares a cooking reel into the @ReFill DM.
2. ReFill reads the reel, extracts the ingredients, and replies
   "N products found" with one quick reply per quick-commerce app.
3. The user taps an app. ReFill builds a basket for it and DMs a link to the
   hosted basket page, where every item is one tap away from the store.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import ig_client
import reel as reel_reader
import sessions
from ecommerce import (
    ProductSearchError,
    SUPPORTED_STORES,
    build_search_links,
    get_store_capability,
    search_products,
    store_name,
)
from ig_client import IncomingMessage
from product_matcher import find_best_product
from recipe_ai import RecipeExtractionError, extract_recipe


logger = logging.getLogger("refill.bot")

STORE_OPTIONS = ("jiomart", "blinkit", "zepto", "instamart")
STORE_PAYLOAD_PREFIX = "REFILL_STORE:"
STATE_AWAITING_STORE = "awaiting_store"

HELP_TEXT = (
    "Hi, I'm ReFill \U0001f373\n\n"
    "Share any cooking reel with me — tap the paper plane on the reel and "
    "send it here. I'll read the recipe, pull out every ingredient, and build "
    "you a ready-to-checkout basket on JioMart, Blinkit, Zepto, or Instamart."
)

PASTED_LINK_TEXT = (
    "That's a reel link, and Instagram only lets me open reels that are shared "
    "with me directly. Tap the paper plane on the reel and send it to this chat "
    "instead — then I'll read it."
)

MISSING_MEDIA_TEXT = (
    "Instagram sent me the reel but not the video itself, so I couldn't read it. "
    "Please share it once more."
)

UNSUPPORTED_ATTACHMENT_TEXT = (
    "I can only read cooking reels right now. Share a recipe reel and I'll turn "
    "it into a basket."
)

UNREADABLE_TEXT = (
    "I couldn't get any recipe text out of that reel. Reply with the ingredient "
    "list in your own words and I'll build the basket from that."
)

EXTRACTION_FAILED_TEXT = (
    "Something went wrong while reading that recipe. Try sharing the reel again "
    "in a moment."
)

NO_INGREDIENTS_TEXT = (
    "That reel didn't look like a cooking recipe to me — I found no ingredients. "
    "Try another reel, or reply with the ingredients yourself."
)

EXPIRED_TEXT = (
    "That basket has expired. Share the recipe reel again and I'll rebuild it."
)


def get_public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


def basket_url(token: str) -> str:
    return f"{get_public_base_url()}/b/{token}"


def store_label(store: str) -> str:
    return store_name(store)


def store_quick_replies() -> list[tuple[str, str]]:
    return [(store_label(store), f"{STORE_PAYLOAD_PREFIX}{store}") for store in STORE_OPTIONS]


def handle_message(message: IncomingMessage) -> dict[str, Any]:
    """Route one inbound DM. Returns a summary of what the bot did."""
    if message.is_echo or not message.sender_id:
        return {"action": "ignored"}

    store = store_from_payload(message.quick_reply_payload)
    if store:
        return _handle_store_choice(message.sender_id, store)

    if message.has_reel:
        return _handle_reel(message)

    return _handle_text(message)


def store_from_payload(payload: str) -> str | None:
    if not payload or not payload.startswith(STORE_PAYLOAD_PREFIX):
        return None

    store = payload[len(STORE_PAYLOAD_PREFIX):].strip().lower()
    return store if store in SUPPORTED_STORES else None


def _handle_text(message: IncomingMessage) -> dict[str, Any]:
    if reel_reader.find_reel_link(message.text):
        _send_text(message.sender_id, PASTED_LINK_TEXT)
        return {"action": "pasted_link"}

    if message.attachment_type in ig_client.REEL_ATTACHMENT_TYPES:
        _send_text(message.sender_id, MISSING_MEDIA_TEXT)
        return {"action": "missing_media"}

    if message.attachment_type:
        _send_text(message.sender_id, UNSUPPORTED_ATTACHMENT_TEXT)
        return {"action": "unsupported_attachment"}

    _send_text(message.sender_id, HELP_TEXT)
    return {"action": "help"}


def _handle_reel(message: IncomingMessage) -> dict[str, Any]:
    _send_text(message.sender_id, "Got the reel \U0001f373 Reading the recipe...")

    source = reel_reader.build_reel_text(
        caption=message.media_title,
        media_url=message.media_url,
        extra_text=message.text,
    )

    if not source["text"]:
        _send_text(message.sender_id, source["warning"] or UNREADABLE_TEXT)
        return {"action": "reel_unreadable"}

    try:
        recipe = extract_recipe(source["text"])
    except RecipeExtractionError as exc:
        logger.warning("recipe extraction failed: %s", exc)
        _send_text(message.sender_id, EXTRACTION_FAILED_TEXT)
        return {"action": "extraction_failed"}

    ingredients = [ingredient.model_dump() for ingredient in recipe.ingredients]
    if not ingredients:
        _send_text(message.sender_id, NO_INGREDIENTS_TEXT)
        return {"action": "no_ingredients"}

    sessions.save_session(
        message.sender_id,
        STATE_AWAITING_STORE,
        {
            "recipe_name": recipe.recipe_name,
            "servings": recipe.servings,
            "ingredients": ingredients,
            "reel_sources": source["sources"],
        },
    )

    prompt = (
        f"\U0001f6d2 {len(ingredients)} products found in "
        f"“{recipe.recipe_name}”.\n\nWhich app should I fill?"
    )
    if source["warning"]:
        prompt = f"{prompt}\n\n({source['warning']})"

    _send_quick_replies(message.sender_id, prompt, store_quick_replies())

    return {
        "action": "awaiting_store",
        "product_count": len(ingredients),
        "recipe_name": recipe.recipe_name,
    }


def _handle_store_choice(sender_id: str, store: str) -> dict[str, Any]:
    session = sessions.load_session(sender_id)
    if not session or session["state"] != STATE_AWAITING_STORE:
        _send_text(sender_id, EXPIRED_TEXT)
        return {"action": "session_expired"}

    payload = session["payload"]
    items = build_basket_items(payload.get("ingredients") or [], store)

    token = sessions.create_basket(
        sender_id,
        store,
        {
            "recipe_name": payload.get("recipe_name") or "Recipe basket",
            "servings": payload.get("servings"),
            "reel_sources": payload.get("reel_sources") or [],
            "items": items,
        },
    )
    sessions.clear_session(sender_id)

    label = store_label(store)
    url = basket_url(token)
    capability = get_store_capability(store)

    if capability["can_cart_fill"]:
        how = (
            f"Open it and hit “Auto-fill {label} cart” — the ReFill extension drops "
            f"every item into your {label} cart. Tap-per-item is there as a backup."
        )
    else:
        how = (
            f"Open it and tap each item to drop it into {label}. "
            f"{label} has no public cart link, so the items open in its search."
        )

    _send_text(
        sender_id,
        f"✅ Your {label} basket is ready — {len(items)} items.\n\n{url}\n\n"
        f"{how} Prices and stock are whatever your address shows.",
    )

    return {
        "action": "basket_ready",
        "store": store,
        "token": token,
        "url": url,
        "item_count": len(items),
        "best_handoff": capability["best_handoff"],
    }


def build_basket_items(ingredients: list[dict], store: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for ingredient in ingredients:
        name = str((ingredient or {}).get("name") or "").strip()
        if not name:
            continue

        items.append(
            {
                "name": name,
                "quantity": ingredient.get("quantity"),
                "unit": ingredient.get("unit"),
                "confidence": ingredient.get("confidence"),
                "matched_product": _match_product(ingredient),
                "search_url": build_search_links(name, store).get(store, ""),
            }
        )

    return items


def _match_product(ingredient: dict) -> dict | None:
    """Attach a concrete product when a catalog API is configured, else None."""
    try:
        products = search_products(str(ingredient.get("name") or ""))
    except ProductSearchError:
        return None

    return find_best_product(ingredient, products)


def _send_text(recipient_id: str, text: str) -> None:
    try:
        ig_client.send_text(recipient_id, text)
    except ig_client.InstagramSendError as exc:
        logger.warning("could not DM %s: %s", recipient_id, exc)


def _send_quick_replies(
    recipient_id: str,
    text: str,
    options: list[tuple[str, str]],
) -> None:
    try:
        ig_client.send_quick_replies(recipient_id, text, options)
    except ig_client.InstagramSendError as exc:
        logger.warning("could not DM quick replies to %s: %s", recipient_id, exc)
