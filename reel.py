"""Turn a shared Instagram reel into text a recipe extractor can read.

Two signals, in order of reliability:

1. The caption / title Meta ships in the webhook attachment payload.
2. The reel's spoken audio, transcribed with a speech-to-text model. Attachment
   URLs from Meta are short-lived signed CDN links, so this has to happen while
   handling the webhook.
"""

from __future__ import annotations

import io
import os
import re
from typing import Any

import requests
from openai import OpenAIError

from recipe_ai import build_openai_client


# OpenAI's audio endpoints reject uploads above 25 MB. Reels are normally well
# under this, and a cap also keeps a hostile URL from exhausting memory.
MAX_MEDIA_BYTES = int(os.getenv("REEL_MAX_MEDIA_BYTES", str(25 * 1024 * 1024)))
DOWNLOAD_TIMEOUT_SECONDS = float(os.getenv("REEL_DOWNLOAD_TIMEOUT_SECONDS", "30"))
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")
CAPTION_TRUST_CHARS = int(os.getenv("REEL_CAPTION_TRUST_CHARS", "220"))

REEL_URL_PATTERN = re.compile(
    r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})",
    re.IGNORECASE,
)


class ReelError(RuntimeError):
    pass


def is_transcription_configured() -> bool:
    return bool(_transcribe_api_key())


def get_reel_config() -> dict[str, Any]:
    return {
        "transcription_configured": is_transcription_configured(),
        "transcribe_model": TRANSCRIBE_MODEL,
        "max_media_mb": round(MAX_MEDIA_BYTES / (1024 * 1024), 1),
    }


def find_reel_link(text: str) -> str | None:
    """Detect a pasted Instagram reel URL, which we cannot fetch media for."""
    if not text:
        return None
    match = REEL_URL_PATTERN.search(text)
    return match.group(0) if match else None


def build_reel_text(caption: str = "", media_url: str = "", extra_text: str = "") -> dict[str, Any]:
    """Assemble the best available text for one reel.

    Returns the combined text plus which sources contributed, so the bot can tell
    the user why extraction was thin instead of failing silently.
    """
    caption = (caption or "").strip()
    extra_text = (extra_text or "").strip()
    sources: list[str] = []
    parts: list[str] = []
    warning: str | None = None

    if caption:
        sources.append("caption")
        parts.append(f"Reel caption:\n{caption}")

    if extra_text:
        sources.append("user_note")
        parts.append(f"Note from the user:\n{extra_text}")

    needs_audio = media_url and len(caption) < CAPTION_TRUST_CHARS

    if needs_audio and is_transcription_configured():
        try:
            spoken = transcribe_media(media_url)
        except ReelError as exc:
            warning = str(exc)
        else:
            if spoken:
                sources.append("audio")
                parts.append(f"Spoken narration:\n{spoken}")
    elif needs_audio:
        warning = "Audio transcription is not configured, so only the caption was read."

    return {
        "text": "\n\n".join(parts).strip(),
        "sources": sources,
        "warning": warning,
    }


def transcribe_media(media_url: str) -> str:
    data = download_media(media_url)
    return transcribe_bytes(data)


def download_media(media_url: str) -> bytes:
    if not media_url:
        raise ReelError("The reel attachment did not include a media URL.")

    try:
        with requests.get(media_url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            response.raise_for_status()

            buffer = io.BytesIO()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                buffer.write(chunk)
                if buffer.tell() > MAX_MEDIA_BYTES:
                    raise ReelError("The reel is too large to transcribe.")
    except ReelError:
        raise
    except requests.RequestException as exc:
        raise ReelError("Could not download the reel media.") from exc

    data = buffer.getvalue()
    if not data:
        raise ReelError("The reel media download was empty.")

    return data


def transcribe_bytes(data: bytes, filename: str = "reel.mp4") -> str:
    if not is_transcription_configured():
        raise ReelError("Audio transcription is not configured.")

    client = build_openai_client(
        api_key=_transcribe_api_key(),
        base_url=os.getenv("TRANSCRIBE_BASE_URL") or None,
    )

    upload = io.BytesIO(data)
    upload.name = filename

    try:
        result = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=upload)
    except OpenAIError as exc:
        raise ReelError(f"Reel transcription failed: {exc}") from exc

    text = getattr(result, "text", None) or ""
    return str(text).strip()


def _transcribe_api_key() -> str:
    for name in ("TRANSCRIBE_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""
