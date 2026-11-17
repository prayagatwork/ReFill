import os
from typing import Any
from urllib.parse import quote_plus

import requests


ECOMMERCE_SEARCH_URL = os.getenv("ECOMMERCE_SEARCH_URL")
ECOMMERCE_CART_URL = os.getenv("ECOMMERCE_CART_URL")
ECOMMERCE_TOKEN = os.getenv("ECOMMERCE_TOKEN")


class ProductSearchError(RuntimeError):
    pass


# Handoff tiers, best first. There is no public "add to cart" API or deeplink for
# any Indian quick-commerce app, so a store can only ever be as good as:
#   cart_fill -> a browser extension runs on the store's own domain and writes
#                the real web cart (Blinkit today).
#   deeplink  -> the search URL is a universal link: it opens the native app to
#                that search on mobile, and the website on desktop. Every store
#                supports this. It is per-item, not a whole cart.
# Promote a store by adding "cart_fill" once its extension content script exists;
# the basket page and the DM copy upgrade automatically.
HANDOFF_CART_FILL = "cart_fill"
HANDOFF_DEEPLINK = "deeplink"

SUPPORTED_STORES = {
    "jiomart": {
        "name": "JioMart",
        "search_url": "https://www.jiomart.com/products?q={query}",
        "home_url": "https://www.jiomart.com/",
        "handoff": [HANDOFF_DEEPLINK],
    },
    "zepto": {
        "name": "Zepto",
        "search_url": "https://www.zeptonow.com/search?query={query}",
        "home_url": "https://www.zeptonow.com/",
        "handoff": [HANDOFF_DEEPLINK],
    },
    "instamart": {
        "name": "Instamart",
        "search_url": "https://www.swiggy.com/instamart/search?query={query}",
        "home_url": "https://www.swiggy.com/instamart",
        "handoff": [HANDOFF_DEEPLINK],
    },
    "blinkit": {
        "name": "Blinkit",
        "search_url": "https://blinkit.com/s/?q={query}",
        "home_url": "https://blinkit.com/",
        "handoff": [HANDOFF_CART_FILL, HANDOFF_DEEPLINK],
    },
}


def get_supported_stores() -> list[str]:
    return list(SUPPORTED_STORES)


def store_name(store: str) -> str:
    config = SUPPORTED_STORES.get(store) or {}
    return str(config.get("name") or store.title())


def store_home_url(store: str) -> str:
    config = SUPPORTED_STORES.get(store) or {}
    return str(config.get("home_url") or "")


def get_store_capability(store: str) -> dict[str, Any]:
    """Describe the best handoff a store supports, for the basket UI and DM copy."""
    config = SUPPORTED_STORES.get(store) or {}
    handoff = list(config.get("handoff") or [HANDOFF_DEEPLINK])

    return {
        "store": store,
        "name": store_name(store),
        "home_url": store_home_url(store),
        "handoff": handoff,
        "best_handoff": handoff[0] if handoff else HANDOFF_DEEPLINK,
        "can_cart_fill": HANDOFF_CART_FILL in handoff,
    }


def get_store_capabilities() -> dict[str, dict[str, Any]]:
    return {store: get_store_capability(store) for store in SUPPORTED_STORES}



def build_search_links(query: str, store: str = "all") -> dict[str, str]:
    query = quote_plus(query.strip())
    if not query:
        return {}

    stores = SUPPORTED_STORES
    if store != "all":
        stores = {store: SUPPORTED_STORES[store]} if store in SUPPORTED_STORES else {}

    return {
        key: config["search_url"].format(query=query)
        for key, config in stores.items()
    }


def search_products(query: str) -> list[dict[str, Any]]:
    """Search a configured product API.

    Quick-commerce stores generally do not expose stable public cart APIs. By
    default this function returns no products and the API emits search links.
    Configure ECOMMERCE_SEARCH_URL to plug in a partner API, scraper service, or
    local catalog endpoint.
    """
    if not ECOMMERCE_SEARCH_URL:
        return []

    headers = {
        "accept": "application/json",
        "user-agent": "ReFill/0.1",
    }
    if ECOMMERCE_TOKEN:
        headers["authorization"] = f"Bearer {ECOMMERCE_TOKEN}"

    try:
        response = requests.get(
            ECOMMERCE_SEARCH_URL,
            params={"q": query},
            headers=headers,
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProductSearchError("Product search failed.") from exc

    return _normalize_product_payload(payload)


def add_to_cart(product_id: str, quantity: int = 1) -> dict[str, Any]:
    if not ECOMMERCE_CART_URL:
        raise RuntimeError("ECOMMERCE_CART_URL is not configured.")

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": "ReFill/0.1",
    }
    if ECOMMERCE_TOKEN:
        headers["authorization"] = f"Bearer {ECOMMERCE_TOKEN}"

    payload = {
        "product_id": product_id,
        "quantity": quantity,
    }

    response = requests.post(
        ECOMMERCE_CART_URL,
        json=payload,
        headers=headers,
        timeout=8,
    )
    response.raise_for_status()
    return response.json()


def _normalize_product_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("products", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, (dict, list)):
        return _normalize_product_payload(data)

    return []
