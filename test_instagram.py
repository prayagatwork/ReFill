import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import bot
import ig_client
import reel
import sessions
from app import app
from recipe_ai import Ingredient, Recipe


APP_SECRET = "test_app_secret"
VERIFY_TOKEN = "test_verify_token"
SENDER = "17841400000000001"

INGREDIENT_NAMES = [
    "paneer",
    "tomato",
    "onion",
    "ginger",
    "garlic",
    "green chilli",
    "cumin seeds",
    "turmeric",
    "red chilli powder",
    "garam masala",
    "coriander leaves",
    "fresh cream",
    "butter",
    "salt",
    "sunflower oil",
]


def sign(body: bytes) -> str:
    digest = hmac.new(APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_recipe(count: int = 15) -> Recipe:
    return Recipe(
        recipe_name="Paneer Butter Masala",
        servings=3,
        ingredients=[
            Ingredient(name=name, quantity=100, unit="g", confidence=0.9)
            for name in INGREDIENT_NAMES[:count]
        ],
    )


def reel_message(mid: str = "mid-reel-1", caption: str = "Paneer butter masala") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "time": 1767225600,
                "messaging": [
                    {
                        "sender": {"id": SENDER},
                        "recipient": {"id": "17841400000000000"},
                        "timestamp": 1767225600,
                        "message": {
                            "mid": mid,
                            "attachments": [
                                {
                                    "type": "ig_reel",
                                    "payload": {
                                        "url": "https://cdn.example/reel.mp4",
                                        "title": caption,
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def quick_reply_message(store: str, mid: str = "mid-qr-1") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "messaging": [
                    {
                        "sender": {"id": SENDER},
                        "recipient": {"id": "17841400000000000"},
                        "message": {
                            "mid": mid,
                            "text": store,
                            "quick_reply": {
                                "payload": f"{bot.STORE_PAYLOAD_PREFIX}{store}"
                            },
                        },
                    }
                ],
            }
        ],
    }


def text_message(text: str, mid: str = "mid-text-1") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "messaging": [
                    {
                        "sender": {"id": SENDER},
                        "recipient": {"id": "17841400000000000"},
                        "message": {"mid": mid, "text": text},
                    }
                ],
            }
        ],
    }


class InstagramFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sessions.configure(str(Path(tmp.name) / "refill-test.db"))
        self.addCleanup(sessions.configure, sessions.DEFAULT_DB_PATH)

        env = patch.dict(
            os.environ,
            {
                "IG_APP_SECRET": APP_SECRET,
                "IG_VERIFY_TOKEN": VERIFY_TOKEN,
                "IG_ACCESS_TOKEN": "test_access_token",
                "IG_ACCOUNT_ID": "17841400000000000",
                "TRANSCRIBE_API_KEY": "test_transcribe_key",
                "PUBLIC_BASE_URL": "https://refill.example",
            },
        )
        env.start()
        self.addCleanup(env.stop)

        self.texts: list[tuple[str, str]] = []
        self.quick_replies: list[tuple[str, str, list]] = []

        send_text = patch.object(
            ig_client,
            "send_text",
            side_effect=lambda to, text: self.texts.append((to, text)) or {},
        )
        send_quick = patch.object(
            ig_client,
            "send_quick_replies",
            side_effect=lambda to, text, options: self.quick_replies.append(
                (to, text, options)
            )
            or {},
        )
        send_text.start()
        send_quick.start()
        self.addCleanup(send_text.stop)
        self.addCleanup(send_quick.stop)

        self.client = TestClient(app)

    def post_webhook(self, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        return self.client.post(
            "/webhook/instagram",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": sign(body),
            },
        )

    def share_reel(self, mid: str = "mid-reel-1", count: int = 15):
        with patch.object(
            reel, "transcribe_media", return_value="Add paneer, tomato, and cream."
        ), patch.object(bot, "extract_recipe", return_value=build_recipe(count)):
            return self.post_webhook(reel_message(mid=mid))

    def test_verification_handshake_echoes_challenge(self):
        response = self.client.get(
            "/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "1158201444")

    def test_verification_rejects_wrong_token(self):
        response = self.client.get(
            "/webhook/instagram",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "1158201444",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "webhook_verification_failed")

    def test_unsigned_webhook_is_rejected(self):
        response = self.client.post(
            "/webhook/instagram",
            content=json.dumps(text_message("hi")).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "invalid_signature")

    def test_shared_reel_reports_product_count_and_offers_stores(self):
        response = self.share_reel()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "queued": 1})

        self.assertEqual(len(self.quick_replies), 1)
        _, prompt, options = self.quick_replies[0]

        self.assertIn("15 products found", prompt)
        self.assertIn("Paneer Butter Masala", prompt)
        self.assertEqual(
            [title for title, _ in options],
            ["JioMart", "Blinkit", "Zepto", "Instamart"],
        )
        self.assertEqual(
            [payload for _, payload in options],
            [
                "REFILL_STORE:jiomart",
                "REFILL_STORE:blinkit",
                "REFILL_STORE:zepto",
                "REFILL_STORE:instamart",
            ],
        )

        session = sessions.load_session(SENDER)
        self.assertEqual(session["state"], bot.STATE_AWAITING_STORE)
        self.assertEqual(len(session["payload"]["ingredients"]), 15)
        self.assertEqual(session["payload"]["reel_sources"], ["caption", "audio"])

    def test_duplicate_delivery_is_handled_once(self):
        self.share_reel(mid="mid-dupe")
        self.share_reel(mid="mid-dupe")

        self.assertEqual(len(self.quick_replies), 1)

    def test_store_choice_builds_basket_and_dms_the_link(self):
        self.share_reel()
        response = self.post_webhook(quick_reply_message("zepto"))

        self.assertEqual(response.status_code, 200)

        _, message = self.texts[-1]
        self.assertIn("Zepto basket is ready", message)
        self.assertIn("15 items", message)
        self.assertIn("https://refill.example/b/", message)
        # Zepto has no cart-fill extension, so the copy points at per-item search.
        self.assertIn("tap each item", message)

        token = message.split("https://refill.example/b/")[1].split()[0]
        basket = self.client.get(f"/api/basket/{token}").json()

        self.assertEqual(basket["store"], "zepto")
        self.assertEqual(basket["store_name"], "Zepto")
        self.assertEqual(basket["capability"]["best_handoff"], "deeplink")
        self.assertFalse(basket["capability"]["can_cart_fill"])
        self.assertEqual(basket["recipe_name"], "Paneer Butter Masala")
        self.assertEqual(len(basket["items"]), 15)
        self.assertEqual(basket["items"][0]["name"], "paneer")
        self.assertIn("zeptonow.com/search?query=paneer", basket["items"][0]["search_url"])

        # The session is consumed so a second tap cannot silently rebuild it.
        self.assertIsNone(sessions.load_session(SENDER))

    def test_blinkit_choice_offers_the_cart_fill_extension(self):
        self.share_reel()
        self.post_webhook(quick_reply_message("blinkit"))

        _, message = self.texts[-1]
        self.assertIn("Auto-fill Blinkit cart", message)

        token = message.split("https://refill.example/b/")[1].split()[0]
        basket = self.client.get(f"/api/basket/{token}").json()

        self.assertEqual(basket["capability"]["best_handoff"], "cart_fill")
        self.assertTrue(basket["capability"]["can_cart_fill"])


    def test_store_choice_without_a_reel_explains_itself(self):
        self.post_webhook(quick_reply_message("blinkit"))

        self.assertIn("expired", self.texts[-1][1])

    def test_expired_basket_link_returns_404(self):
        response = self.client.get("/api/basket/does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "basket_not_found")

    def test_basket_page_is_served_for_a_clean_link(self):
        response = self.client.get("/b/any-token")

        self.assertEqual(response.status_code, 200)
        self.assertIn("ReFill basket", response.text)

    def test_pasted_reel_link_asks_for_a_real_share(self):
        self.post_webhook(text_message("https://www.instagram.com/reel/CxYz123abc/"))

        self.assertIn("Tap the paper plane", self.texts[-1][1])

    def test_plain_text_gets_help(self):
        self.post_webhook(text_message("hello"))

        self.assertIn("Share any cooking reel", self.texts[-1][1])

    def test_echo_messages_are_ignored(self):
        payload = text_message("our own reply", mid="mid-echo")
        payload["entry"][0]["messaging"][0]["message"]["is_echo"] = True

        response = self.post_webhook(payload)

        self.assertEqual(response.json()["queued"], 0)
        self.assertEqual(self.texts, [])


class InstagramHelperTests(unittest.TestCase):
    def test_signature_verification(self):
        with patch.dict(os.environ, {"IG_APP_SECRET": APP_SECRET, "IG_ALLOW_UNSIGNED": ""}):
            body = b'{"object":"instagram"}'

            self.assertTrue(ig_client.verify_signature(body, sign(body)))
            self.assertFalse(ig_client.verify_signature(body, "sha256=deadbeef"))
            self.assertFalse(ig_client.verify_signature(body, None))

    def test_missing_secret_rejects_unless_explicitly_allowed(self):
        with patch.dict(os.environ, {"IG_APP_SECRET": "", "IG_ALLOW_UNSIGNED": ""}):
            self.assertFalse(ig_client.verify_signature(b"{}", None))

        with patch.dict(os.environ, {"IG_APP_SECRET": "", "IG_ALLOW_UNSIGNED": "true"}):
            self.assertTrue(ig_client.verify_signature(b"{}", None))

    def test_parse_events_reads_reels_quick_replies_and_postbacks(self):
        reel_only = ig_client.parse_events(reel_message())[0]
        self.assertTrue(reel_only.has_reel)
        self.assertEqual(reel_only.attachment_type, "ig_reel")
        self.assertEqual(reel_only.media_url, "https://cdn.example/reel.mp4")

        tapped = ig_client.parse_events(quick_reply_message("blinkit"))[0]
        self.assertEqual(tapped.quick_reply_payload, "REFILL_STORE:blinkit")

        postback = ig_client.parse_events(
            {
                "object": "instagram",
                "entry": [
                    {
                        "messaging": [
                            {
                                "sender": {"id": SENDER},
                                "postback": {"mid": "m1", "payload": "REFILL_STORE:zepto"},
                            }
                        ]
                    }
                ],
            }
        )[0]
        self.assertEqual(postback.quick_reply_payload, "REFILL_STORE:zepto")

    def test_parse_events_ignores_other_objects(self):
        self.assertEqual(ig_client.parse_events({"object": "page", "entry": []}), [])
        self.assertEqual(ig_client.parse_events(None), [])

    def test_store_payload_parsing(self):
        self.assertEqual(bot.store_from_payload("REFILL_STORE:blinkit"), "blinkit")
        self.assertIsNone(bot.store_from_payload("REFILL_STORE:bigbasket"))
        self.assertIsNone(bot.store_from_payload("hello"))
        self.assertIsNone(bot.store_from_payload(""))

    def test_reel_link_detection(self):
        self.assertTrue(reel.find_reel_link("look https://instagram.com/reel/Abc12/ here"))
        self.assertTrue(reel.find_reel_link("https://www.instagram.com/p/Abc12345/"))
        self.assertIsNone(reel.find_reel_link("just some text"))


class StoreCapabilityTests(unittest.TestCase):
    def test_blinkit_leads_with_cart_fill(self):
        from ecommerce import get_store_capability

        cap = get_store_capability("blinkit")
        self.assertEqual(cap["name"], "Blinkit")
        self.assertEqual(cap["best_handoff"], "cart_fill")
        self.assertTrue(cap["can_cart_fill"])
        self.assertIn("blinkit.com", cap["home_url"])

    def test_deeplink_only_stores_cannot_cart_fill(self):
        from ecommerce import get_store_capability

        for store in ("jiomart", "zepto", "instamart"):
            cap = get_store_capability(store)
            self.assertEqual(cap["best_handoff"], "deeplink", store)
            self.assertFalse(cap["can_cart_fill"], store)

    def test_unknown_store_falls_back_to_deeplink(self):
        from ecommerce import get_store_capability

        cap = get_store_capability("bigbasket")
        self.assertEqual(cap["best_handoff"], "deeplink")
        self.assertFalse(cap["can_cart_fill"])


class ReelTextTests(unittest.TestCase):
    def test_long_caption_skips_transcription(self):
        caption = (
            "Full ingredient list: "
            + ", ".join(INGREDIENT_NAMES)
            + ", a pinch of sugar, two tablespoons of cashew paste, "
            "and warm water as needed."
        )
        self.assertGreater(len(caption), reel.CAPTION_TRUST_CHARS)

        with patch.dict(os.environ, {"TRANSCRIBE_API_KEY": "key"}), patch.object(
            reel, "transcribe_media"
        ) as transcribe:
            source = reel.build_reel_text(caption=caption, media_url="https://cdn/x.mp4")

        transcribe.assert_not_called()
        self.assertEqual(source["sources"], ["caption"])
        self.assertIsNone(source["warning"])

    def test_thin_caption_falls_back_to_audio(self):
        with patch.dict(os.environ, {"TRANSCRIBE_API_KEY": "key"}), patch.object(
            reel, "transcribe_media", return_value="Take 200 grams of paneer."
        ):
            source = reel.build_reel_text(caption="yum", media_url="https://cdn/x.mp4")

        self.assertEqual(source["sources"], ["caption", "audio"])
        self.assertIn("200 grams of paneer", source["text"])

    def test_transcription_failure_is_reported_not_fatal(self):
        with patch.dict(os.environ, {"TRANSCRIBE_API_KEY": "key"}), patch.object(
            reel, "transcribe_media", side_effect=reel.ReelError("too large")
        ):
            source = reel.build_reel_text(caption="yum", media_url="https://cdn/x.mp4")

        self.assertEqual(source["sources"], ["caption"])
        self.assertEqual(source["warning"], "too large")
        self.assertIn("yum", source["text"])

    def test_unconfigured_transcription_warns(self):
        with patch.dict(
            os.environ,
            {"TRANSCRIBE_API_KEY": "", "OPENAI_API_KEY": "", "LLM_API_KEY": ""},
        ):
            source = reel.build_reel_text(caption="yum", media_url="https://cdn/x.mp4")

        self.assertIn("not configured", source["warning"])


if __name__ == "__main__":
    unittest.main()
