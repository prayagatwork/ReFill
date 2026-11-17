"""Instagram Messaging API adapter: webhook verification, parsing, and sending.

Works with both auth paths Meta offers for Instagram DMs:

* Instagram API with Instagram Login -- default, ``https://graph.instagram.com``
  with ``IG_ACCOUNT_ID`` set to the Instagram professional account id (or ``me``).
* Instagram messaging via a linked Facebook Page -- set
  ``IG_GRAPH_BASE=https://graph.facebook.com`` and ``IG_ACCOUNT_ID`` to the Page id.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any

import requests


GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v23.0")
SEND_TIMEOUT_SECONDS = float(os.getenv("IG_SEND_TIMEOUT_SECONDS", "10"))
MAX_QUICK_REPLIES = 13
QUICK_REPLY_TITLE_LIMIT = 20

# Attachment types that can carry a cooking reel a user forwarded into the DM.
REEL_ATTACHMENT_TYPES = ("ig_reel", "share", "video", "story_mention")


class InstagramSendError(RuntimeError):
    pass


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def get_graph_base() -> str:
    return (os.getenv("IG_GRAPH_BASE") or "https://graph.instagram.com").rstrip("/")


def get_account_id() -> str:
    return _env("IG_ACCOUNT_ID") or "me"


def is_configured() -> bool:
    return bool(_env("IG_ACCESS_TOKEN"))


def get_config() -> dict[str, Any]:
    return {
        "configured": is_configured(),
        "graph_base": get_graph_base(),
        "graph_version": GRAPH_VERSION,
        "account_id_configured": bool(_env("IG_ACCOUNT_ID")),
        "verify_token_configured": bool(_env("IG_VERIFY_TOKEN")),
        "app_secret_configured": bool(_env("IG_APP_SECRET")),
        "unsigned_webhooks_allowed": allows_unsigned_webhooks(),
    }


def allows_unsigned_webhooks() -> bool:
    """Dev-only escape hatch so the webhook can be exercised with curl."""
    return _env("IG_ALLOW_UNSIGNED").lower() in {"1", "true", "yes"}


def verify_subscription(mode: str | None, token: str | None, challenge: str | None) -> str | None:
    """Answer Meta's ``GET`` webhook handshake. Returns the echo string or None."""
    expected = _env("IG_VERIFY_TOKEN")
    if not expected or mode != "subscribe" or not token:
        return None
    if not hmac.compare_digest(token, expected):
        return None
    return challenge or ""


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Validate the ``X-Hub-Signature-256`` header against the raw request body."""
    secret = _env("IG_APP_SECRET")
    if not secret:
        return allows_unsigned_webhooks()
    if not header or not header.startswith("sha256="):
        return False

    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1].strip())


@dataclass
class IncomingMessage:
    sender_id: str
    message_id: str = ""
    text: str = ""
    quick_reply_payload: str = ""
    attachment_type: str = ""
    media_url: str = ""
    media_title: str = ""
    is_echo: bool = False

    @property
    def has_reel(self) -> bool:
        return self.attachment_type in REEL_ATTACHMENT_TYPES and bool(self.media_url)


def parse_events(payload: Any) -> list[IncomingMessage]:
    """Flatten a webhook body into the messages ReFill cares about."""
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return []

    messages: list[IncomingMessage] = []

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue

        for event in entry.get("messaging") or []:
            parsed = _parse_event(event)
            if parsed is not None:
                messages.append(parsed)

    return messages


def _parse_event(event: Any) -> IncomingMessage | None:
    if not isinstance(event, dict):
        return None

    sender_id = str((event.get("sender") or {}).get("id") or "").strip()
    if not sender_id:
        return None

    postback = event.get("postback")
    if isinstance(postback, dict):
        return IncomingMessage(
            sender_id=sender_id,
            message_id=str(postback.get("mid") or ""),
            quick_reply_payload=str(postback.get("payload") or ""),
            text=str(postback.get("title") or ""),
        )

    message = event.get("message")
    if not isinstance(message, dict):
        # read receipts and reactions carry no message body
        return None

    attachment_type, media_url, media_title = _first_attachment(message.get("attachments"))

    return IncomingMessage(
        sender_id=sender_id,
        message_id=str(message.get("mid") or ""),
        text=str(message.get("text") or "").strip(),
        quick_reply_payload=str((message.get("quick_reply") or {}).get("payload") or ""),
        attachment_type=attachment_type,
        media_url=media_url,
        media_title=media_title,
        is_echo=bool(message.get("is_echo")),
    )


def _first_attachment(attachments: Any) -> tuple[str, str, str]:
    if not isinstance(attachments, list):
        return "", "", ""

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        payload = attachment.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        url = str(payload.get("url") or "").strip()
        title = str(payload.get("title") or payload.get("caption") or "").strip()
        kind = str(attachment.get("type") or "").strip()

        if kind:
            return kind, url, title

    return "", "", ""


def send_text(recipient_id: str, text: str) -> dict[str, Any]:
    return _send({"recipient": {"id": recipient_id}, "message": {"text": text}})


def send_quick_replies(
    recipient_id: str,
    text: str,
    options: list[tuple[str, str]],
) -> dict[str, Any]:
    quick_replies = [
        {
            "content_type": "text",
            "title": title[:QUICK_REPLY_TITLE_LIMIT],
            "payload": payload,
        }
        for title, payload in options[:MAX_QUICK_REPLIES]
    ]

    return _send(
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text, "quick_replies": quick_replies},
        }
    )


def _send(body: dict[str, Any]) -> dict[str, Any]:
    token = _env("IG_ACCESS_TOKEN")
    if not token:
        raise InstagramSendError("IG_ACCESS_TOKEN is not configured.")

    url = f"{get_graph_base()}/{GRAPH_VERSION}/{get_account_id()}/messages"

    try:
        response = requests.post(
            url,
            json=body,
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise InstagramSendError(f"Could not reach the Instagram API: {exc}") from exc

    if response.status_code >= 400:
        raise InstagramSendError(
            f"Instagram send failed with status {response.status_code}: "
            f"{_safe_error(response)}"
        )

    try:
        return response.json()
    except ValueError:
        return {}


def _safe_error(response: requests.Response) -> str:
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        return response.text[:200]

    message = error.get("message") or response.text[:200]
    return str(message)[:300]
