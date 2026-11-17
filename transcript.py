import os
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi

try:
    from youtube_transcript_api import IpBlocked, RequestBlocked
except ImportError:  # pragma: no cover - older library versions
    IpBlocked = None
    RequestBlocked = None


PREFERRED_LANGUAGES = ("en", "en-US", "en-GB", "hi", "mr")
YOUTUBE_BLOCKED_MESSAGE = (
    "YouTube blocked transcript retrieval from this server IP. "
    "Paste the transcript manually or configure a rotating residential proxy."
)
YOUTUBE_BLOCK_MARKERS = (
    "requestblocked",
    "ipblocked",
    "youtube is blocking",
    "blocked by youtube",
    "ip has been blocked",
    "cloud provider",
    "too many requests",
    "too many 429",
    "429 error responses",
)


class UnsupportedSourceError(ValueError):
    pass


class TranscriptError(RuntimeError):
    pass


class TranscriptBlockedError(TranscriptError):
    pass


def detect_source(url: str | None) -> str:
    if not url:
        return "unknown"

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return "youtube"

    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower()

    if "youtube.com" in netloc or "youtu.be" in netloc:
        return "youtube"

    if "instagram.com" in netloc:
        return "instagram"

    return "unknown"


def get_youtube_video_id(url: str):
    if not url:
        return None

    # Accept raw video IDs (11 chars, letters, digits, - and _)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url

    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower()

    if "youtube.com" in netloc:
        # Try query param first (standard watch URLs)
        vid = parse_qs(parsed.query).get("v", [None])[0]
        if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid

        # Handle path-based URLs like /shorts/<id>, /embed/<id>, or /live/<id>
        path_parts = (parsed.path or "").strip("/").split("/")
        for part in path_parts:
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", part):
                return part

    if "youtu.be" in netloc:
        candidate = (parsed.path or "").strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate

    # If someone passed a path-like value that contains just the id
    path_id = (parsed.path or "").strip("/")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", path_id):
        return path_id

    return None


def get_transcript(url: str) -> str:
    source = detect_source(url)

    video_id = get_youtube_video_id(url)

    if source == "instagram":
        raise UnsupportedSourceError(
            "Instagram Reels do not expose reliable server-side transcripts here yet. Pass transcript text for now."
        )

    if not video_id:
        raise ValueError("Invalid or unsupported video URL.")

    api = YouTubeTranscriptApi(proxy_config=_build_proxy_config())

    try:
        transcript = api.fetch(video_id, languages=PREFERRED_LANGUAGES)
    except Exception as exc:
        if _is_youtube_block_error(exc):
            raise TranscriptBlockedError(YOUTUBE_BLOCKED_MESSAGE) from exc

        try:
            transcript_list = api.list(video_id)
            transcript = None
            for t in transcript_list:
                try:
                    transcript = t.fetch()
                    break
                except Exception:
                    continue

            if transcript is None:
                raise exc
        except Exception as exc2:
            if _is_youtube_block_error(exc2):
                raise TranscriptBlockedError(YOUTUBE_BLOCKED_MESSAGE) from exc2

            raise TranscriptError(
                f"Could not retrieve transcript: {_compact_error_message(exc2)}"
            ) from exc2

    parts = []
    for item in transcript:
        if isinstance(item, dict):
            parts.append(item.get("text", ""))
        else:
            text_attr = getattr(item, "text", None)
            if text_attr is not None:
                parts.append(text_attr)
            else:
                parts.append(str(item))

    text = _clean_transcript(" ".join(p for p in parts if p))
    if not text:
        raise TranscriptError("Transcript was retrieved but contained no text.")

    return text


def _clean_transcript(text: str) -> str:
    text = re.sub(r"\[(music|applause|laughter|noise)\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_transcript_config(environ: Mapping[str, str] | None = None) -> dict:
    try:
        proxy_type = _get_proxy_type(environ)
    except TranscriptError as exc:
        return {
            "youtube_proxy_configured": False,
            "youtube_proxy_type": None,
            "youtube_proxy_error": str(exc),
        }

    return {
        "youtube_proxy_configured": proxy_type is not None,
        "youtube_proxy_type": proxy_type,
        "youtube_proxy_error": None,
    }


def _build_proxy_config(environ: Mapping[str, str] | None = None):
    env = os.environ if environ is None else environ
    proxy_type = _get_proxy_type(env)

    if proxy_type == "webshare":
        from youtube_transcript_api.proxies import WebshareProxyConfig

        countries = _csv_env(env, "WEBSHARE_PROXY_COUNTRIES")
        return WebshareProxyConfig(
            proxy_username=_env_value(env, "WEBSHARE_PROXY_USERNAME"),
            proxy_password=_env_value(env, "WEBSHARE_PROXY_PASSWORD"),
            filter_ip_locations=countries or None,
        )

    if proxy_type == "generic":
        from youtube_transcript_api.proxies import GenericProxyConfig

        http_url = _env_value(env, "YOUTUBE_TRANSCRIPT_PROXY_HTTP")
        https_url = _env_value(env, "YOUTUBE_TRANSCRIPT_PROXY_HTTPS")
        return GenericProxyConfig(
            http_url=http_url or None,
            https_url=https_url or None,
        )

    return None


def _get_proxy_type(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ

    webshare_username = _env_value(env, "WEBSHARE_PROXY_USERNAME")
    webshare_password = _env_value(env, "WEBSHARE_PROXY_PASSWORD")
    generic_http = _env_value(env, "YOUTUBE_TRANSCRIPT_PROXY_HTTP")
    generic_https = _env_value(env, "YOUTUBE_TRANSCRIPT_PROXY_HTTPS")

    if webshare_username or webshare_password:
        if not webshare_username or not webshare_password:
            raise TranscriptError(
                "Set both WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD."
            )
        return "webshare"

    if generic_http or generic_https:
        return "generic"

    return None


def _env_value(environ: Mapping[str, str], key: str) -> str:
    return str(environ.get(key, "")).strip()


def _csv_env(environ: Mapping[str, str], key: str) -> list[str]:
    return [part.strip() for part in _env_value(environ, key).split(",") if part.strip()]


def _is_youtube_block_error(exc: Exception) -> bool:
    blocked_classes = tuple(
        cls for cls in (RequestBlocked, IpBlocked) if isinstance(cls, type)
    )
    if blocked_classes and isinstance(exc, blocked_classes):
        return True

    message = str(exc).lower()
    return any(marker in message for marker in YOUTUBE_BLOCK_MARKERS)


def _compact_error_message(exc: Exception) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    if len(message) <= 360:
        return message

    return f"{message[:357].rstrip()}..."
