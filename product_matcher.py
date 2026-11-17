import re
from difflib import SequenceMatcher
from typing import Any


UNIT_TO_GRAMS = {
    "g": 1,
    "gram": 1,
    "grams": 1,
    "kg": 1000,
    "kilogram": 1000,
    "kilograms": 1000,
    "ml": 1,
    "millilitre": 1,
    "millilitres": 1,
    "l": 1000,
    "litre": 1000,
    "litres": 1000,
}


def find_best_product(ingredient: Any, products: list[dict[str, Any]]):
    if not products:
        return None

    ingredient_name = _ingredient_name(ingredient)
    requested_grams = _ingredient_quantity_grams(ingredient)
    scored = []

    for product in products:
        product_name = _product_name(product)
        if not product_name:
            continue

        score = _name_score(ingredient_name, product_name)
        available_grams = _product_quantity_grams(product)

        if requested_grams and available_grams:
            ratio = abs(requested_grams - available_grams) / requested_grams
            if ratio <= 0.25:
                score += 5
            elif ratio <= 0.5:
                score += 2

        scored.append((score, product))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_product = scored[0]
    return best_product if best_score >= 2 else None


def _ingredient_name(ingredient: Any) -> str:
    name = getattr(ingredient, "name", None)
    if name is None and isinstance(ingredient, dict):
        name = ingredient.get("name")
    return str(name or "").lower().strip()


def _ingredient_quantity_grams(ingredient: Any) -> float | None:
    quantity = getattr(ingredient, "quantity", None)
    unit = getattr(ingredient, "unit", None)

    if isinstance(ingredient, dict):
        quantity = ingredient.get("quantity", quantity)
        unit = ingredient.get("unit", unit)

    return _to_grams(quantity, unit)


def _product_name(product: dict[str, Any]) -> str:
    for key in ("name", "title", "display_name", "product_name"):
        value = product.get(key)
        if value:
            return str(value).lower().strip()
    return ""


def _product_quantity_grams(product: dict[str, Any]) -> float | None:
    for key in ("quantity_g", "weight_g", "net_weight_g"):
        value = _as_float(product.get(key))
        if value:
            return value

    for key in ("quantity", "weight", "size", "pack_size", "name", "title"):
        parsed = _parse_quantity_text(str(product.get(key) or ""))
        if parsed:
            return parsed

    return None


def _name_score(ingredient_name: str, product_name: str) -> float:
    if not ingredient_name or not product_name:
        return 0

    score = SequenceMatcher(None, ingredient_name, product_name).ratio() * 5

    if ingredient_name in product_name:
        score += 6

    ingredient_tokens = set(_tokens(ingredient_name))
    product_tokens = set(_tokens(product_name))
    if ingredient_tokens:
        score += len(ingredient_tokens & product_tokens) / len(ingredient_tokens) * 5

    return score


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _parse_quantity_text(value: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|grams?|l|litres?|ml)\b", value, re.I)
    if not match:
        return None

    return _to_grams(match.group(1), match.group(2))


def _to_grams(quantity: Any, unit: Any) -> float | None:
    amount = _as_float(quantity)
    if amount is None:
        return None

    normalized_unit = str(unit or "").lower().strip()
    multiplier = UNIT_TO_GRAMS.get(normalized_unit)
    if not multiplier:
        return None

    return amount * multiplier


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
