import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from ecommerce import ProductSearchError, build_search_links
from product_matcher import find_best_product
from recipe_ai import Ingredient, Recipe, _parse_recipe_json
from transcript import (
    TranscriptBlockedError,
    _is_youtube_block_error,
    get_transcript_config,
    get_youtube_video_id,
)


class ReFillApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_recipe_from_transcript(self):
        recipe = Recipe(
            recipe_name="Paneer Tomato Curry",
            servings=2,
            ingredients=[
                Ingredient(
                    name="tomato",
                    quantity=500,
                    unit="g",
                    confidence=0.95,
                )
            ],
        )

        with patch("app.extract_recipe", return_value=recipe), patch(
            "app.search_products",
            return_value=[{"name": "Fresh Tomato 500 g", "quantity_g": 500}],
        ):
            response = self.client.post(
                "/recipe",
                json={
                    "transcript": "Add 500 g tomato and cook it down.",
                    "store": "blinkit",
                    "include_product_search": True,
                },
            )

        body = response.json()
        item = body["cart"]["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["source"]["type"], "manual_transcript")
        self.assertEqual(item["matched_product"]["name"], "Fresh Tomato 500 g")
        self.assertEqual(list(item["search_links"]), ["blinkit"])

    def test_recipe_requires_source(self):
        response = self.client.post("/recipe", json={})

        self.assertEqual(response.status_code, 422)

    def test_instagram_without_transcript_is_explicitly_unsupported(self):
        response = self.client.post(
            "/recipe",
            json={"url": "https://www.instagram.com/reel/ABC123/"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_source")

    def test_youtube_transcript_ip_block_is_actionable(self):
        with patch(
            "app.get_transcript",
            side_effect=TranscriptBlockedError(
                "YouTube blocked transcript retrieval from this server IP. "
                "Paste the transcript manually or configure a rotating residential proxy."
            ),
        ):
            response = self.client.post(
                "/recipe",
                json={"url": "https://www.youtube.com/watch?v=abcdefghijk"},
            )

        detail = response.json()["detail"]

        self.assertEqual(response.status_code, 424)
        self.assertEqual(detail["code"], "youtube_transcript_blocked")
        self.assertIn("Paste the transcript", detail["message"])

    def test_product_search_failure_does_not_break_recipe(self):
        recipe = Recipe(
            recipe_name="Tomato Rice",
            servings=None,
            ingredients=[
                Ingredient(
                    name="tomato",
                    quantity=None,
                    unit=None,
                    confidence=0.9,
                )
            ],
        )

        with patch("app.extract_recipe", return_value=recipe), patch(
            "app.search_products",
            side_effect=ProductSearchError("catalog down"),
        ):
            response = self.client.post(
                "/recipe",
                json={
                    "transcript": "Add tomato to rice.",
                    "include_product_search": True,
                },
            )

        item = response.json()["cart"]["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(item["product_search_error"], "Product search failed.")
        self.assertIsNone(item["matched_product"])


class HelperTests(unittest.TestCase):
    def test_youtube_id_parsing(self):
        self.assertEqual(
            get_youtube_video_id("https://www.youtube.com/watch?v=abcdefghijk"),
            "abcdefghijk",
        )
        self.assertEqual(
            get_youtube_video_id("https://youtube.com/shorts/abcdefghijk"),
            "abcdefghijk",
        )
        self.assertEqual(
            get_youtube_video_id("https://youtu.be/abcdefghijk?t=10"),
            "abcdefghijk",
        )

    def test_build_search_links(self):
        links = build_search_links("red onion", "all")

        self.assertEqual(set(links), {"jiomart", "zepto", "instamart", "blinkit"})
        self.assertIn("red+onion", links["jiomart"])

    def test_product_matcher_handles_missing_quantities(self):
        product = find_best_product(
            Ingredient(
                name="paneer",
                quantity=None,
                unit=None,
                confidence=0.9,
            ),
            [{"name": "Fresh Paneer 200 g"}],
        )

        self.assertEqual(product["name"], "Fresh Paneer 200 g")

    def test_qwen_json_parser_handles_thinking_prefix(self):
        recipe = _parse_recipe_json(
            "<think>Identify only real ingredients.</think>"
            '{"recipe_name":"Poha","servings":2,"ingredients":['
            '{"name":"poha","quantity":1,"unit":"cup","confidence":0.98}'
            "]}"
        )

        self.assertEqual(recipe.recipe_name, "Poha")
        self.assertEqual(recipe.ingredients[0].name, "poha")

    def test_transcript_proxy_config_status(self):
        self.assertEqual(
            get_transcript_config({}),
            {
                "youtube_proxy_configured": False,
                "youtube_proxy_type": None,
                "youtube_proxy_error": None,
            },
        )
        self.assertEqual(
            get_transcript_config(
                {
                    "WEBSHARE_PROXY_USERNAME": "user",
                    "WEBSHARE_PROXY_PASSWORD": "pass",
                }
            )["youtube_proxy_type"],
            "webshare",
        )
        self.assertEqual(
            get_transcript_config(
                {"YOUTUBE_TRANSCRIPT_PROXY_HTTP": "http://proxy.example.com:8080"}
            )["youtube_proxy_type"],
            "generic",
        )
        self.assertIn(
            "WEBSHARE_PROXY_PASSWORD",
            get_transcript_config({"WEBSHARE_PROXY_USERNAME": "user"})[
                "youtube_proxy_error"
            ],
        )

    def test_youtube_429_errors_are_classified_as_blocks(self):
        self.assertTrue(
            _is_youtube_block_error(
                RuntimeError("too many 429 error responses from YouTube")
            )
        )


if __name__ == "__main__":
    unittest.main()
