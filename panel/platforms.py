"""Curated platform settings for the operator console.

The panel already ships two generic editors (the YAML/JSON files and the whole
``.env``), but publishing credentials are spread over a dozen keys with
different shapes: bot tokens, chat ids, API base URLs, and on/off flags. This
module groups those keys into one card per platform so an operator can see
whether a channel is ready, replace a single secret, point a client at another
API base, and test the credential against the provider.

Secrets never travel to the browser: a card reports whether a value is stored,
not what it is. Saving goes through ``EnvStore`` so the ``.env`` file keeps its
mode, backups, and comments; the API answers with the container that has to be
recreated for the new values to be read.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from panel.editors import EditError, EnvStore

HTTP_TIMEOUT = 12.0
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
FLAG_VALUES = {"", "true", "false", "1", "0", "yes", "no", "on", "off"}


@dataclass(frozen=True)
class Field:
    """One editable key of a platform card."""

    key: str
    label: str
    kind: str = "text"  # text | secret | url | flag | id
    help: str = ""
    placeholder: str = ""
    default: str = ""
    required: bool = False


@dataclass(frozen=True)
class Platform:
    """One platform card: its keys, its mode, and its connection test."""

    key: str
    label: str
    mode: str  # auto | package | service | flag
    service: str
    summary: str
    docs: str = ""
    test: str = ""
    fields: tuple[Field, ...] = ()


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="telegram",
        label="Telegram",
        mode="auto",
        service="content-bot",
        summary="Operator bot: proposals, approvals, and the channel that receives posts.",
        docs="docs/CONTENT-PRODUCTION-GUIDE.md",
        test="telegram",
        fields=(
            Field(
                "CONTENT_BOT_TOKEN",
                "Bot token",
                "secret",
                help="From @BotFather. This is the bot you talk to in Telegram.",
                required=True,
            ),
            Field(
                "CONTENT_TELEGRAM_CHANNEL",
                "Channel id",
                "id",
                help="Numeric id (-100...) or @username of the channel that receives the posts.",
                required=True,
            ),
            Field(
                "CONTENT_TELEGRAM_USERS",
                "Operator user ids",
                "id",
                help="Comma-separated Telegram user ids allowed to approve and publish.",
            ),
            Field(
                "CONTENT_TELEGRAM_API_BASE",
                "API base URL",
                "url",
                default="https://api.telegram.org",
                help="Keep the default unless you front the Bot API with a proxy.",
            ),
        ),
    ),
    Platform(
        key="bale",
        label="Bale",
        mode="auto",
        service="content-bot",
        summary="Publishes to the Bale channel as soon as the platform is picked on a draft.",
        docs="docs/BALE-EITAA-SETUP.md",
        test="bale",
        fields=(
            Field(
                "CONTENT_BALE_TOKEN",
                "Bot token",
                "secret",
                help="Token from Bale's @botfather (123456789:abc...).",
                required=True,
            ),
            Field(
                "CONTENT_BALE_CHAT_ID",
                "Channel id",
                "id",
                help="The @username of the channel, or its numeric id.",
                required=True,
            ),
            Field(
                "CONTENT_BALE_API_BASE",
                "API base URL",
                "url",
                default="https://tapi.bale.ai",
                help="Bale speaks the Telegram Bot API shape.",
            ),
        ),
    ),
    Platform(
        key="eitaa",
        label="Eitaa",
        mode="auto",
        service="content-bot",
        summary="Publishes to the Eitaa channel through the EitaaYar gateway.",
        docs="docs/BALE-EITAA-SETUP.md",
        test="eitaa",
        fields=(
            Field(
                "CONTENT_EITAA_TOKEN",
                "Bot token",
                "secret",
                help="Token from the EitaaYar panel (bot522975:...).",
                required=True,
            ),
            Field(
                "CONTENT_EITAA_CHAT_ID",
                "Channel id",
                "id",
                help="The @username of the channel, or its numeric id.",
                required=True,
            ),
            Field(
                "CONTENT_EITAA_API_BASE",
                "API base URL",
                "url",
                default="https://eitaayar.ir/api",
                help="Change it when EitaaYar moves the gateway to another host.",
            ),
        ),
    ),
    Platform(
        key="instagram",
        label="Instagram",
        mode="package",
        service="content-bot",
        summary=(
            "Hands over a copy-ready package for the mobile app; the Graph API "
            "can publish automatically once Meta grants the permissions."
        ),
        docs="docs/INSTAGRAM-SETUP.md",
        test="instagram",
        fields=(
            Field(
                "INSTAGRAM_BUSINESS_ID",
                "Business account id",
                "id",
                help="Instagram Business account id linked to the Facebook page.",
                required=True,
            ),
            Field(
                "INSTAGRAM_ACCESS_TOKEN",
                "Access token",
                "secret",
                help="Long-lived token with instagram_basic and instagram_content_publish.",
                required=True,
            ),
            Field(
                "INSTAGRAM_APP_ID",
                "App id",
                "id",
                help="Only needed when you refresh the token yourself.",
            ),
            Field(
                "INSTAGRAM_APP_SECRET",
                "App secret",
                "secret",
                help="Used with the app id for manual token refreshes.",
            ),
            Field(
                "INSTAGRAM_API_BASE",
                "API base URL",
                "url",
                default="https://graph.facebook.com",
                help="Graph API host.",
            ),
            Field(
                "INSTAGRAM_API_VERSION",
                "API version",
                "text",
                default="v26.0",
                help="Pinned Graph API version; the panel keeps the current default.",
            ),
            Field(
                "INSTAGRAM_MEDIA_PUBLIC_BASE_URL",
                "Public media base URL",
                "url",
                help="HTTPS origin the Graph API downloads media from (for example "
                "https://media.example.com).",
            ),
            Field(
                "INSTAGRAM_AUTO_PUBLISH",
                "Automatic publishing",
                "flag",
                default="false",
                help="true tries the Graph API; false keeps the manual package flow.",
            ),
            Field(
                "INSTAGRAM_DISABLE_REFRESH",
                "Disable automatic refresh",
                "flag",
                default="true",
                help="true stops the bot from refreshing the long-lived token.",
            ),
        ),
    ),
    Platform(
        key="writer",
        label="AI writer",
        mode="service",
        service="content-bot",
        summary="OpenAI-compatible endpoint the bot calls to draft posts.",
        docs="docs/SMART-ROUTER-CLIENT-API.md",
        test="writer",
        fields=(
            Field(
                "CONTENT_WRITER_BASE_URL",
                "Base URL",
                "url",
                default="http://smart-router:8080/v1",
                help="Include the /v1 suffix for an OpenAI-compatible gateway.",
                required=True,
            ),
            Field(
                "CONTENT_WRITER_API_KEY",
                "API key",
                "secret",
                help="Sent as Bearer; leave empty when the gateway is open on the stack network.",
            ),
            Field(
                "CONTENT_WRITER_MODEL",
                "Model",
                "text",
                default="auto",
                help="auto follows the gateway default.",
            ),
            Field("CONTENT_WRITER_MAX_TOKENS", "Max tokens", "text", default="1600"),
            Field(
                "CONTENT_WRITER_REASONING_EFFORT",
                "Reasoning effort",
                "text",
                help="Optional; low/medium/high for gateways that accept it.",
            ),
        ),
    ),
    Platform(
        key="media",
        label="Media Studio",
        mode="service",
        service="content-bot",
        summary="Image and video generation service behind the media actions.",
        docs="docs/MEDIA-STUDIO.md",
        test="media",
        fields=(
            Field(
                "CONTENT_MEDIA_STUDIO_URL",
                "Base URL",
                "url",
                default="http://media-studio:8850",
                help="Service address on the stack network.",
                required=True,
            ),
            Field(
                "CONTENT_MEDIA_STUDIO_TOKEN",
                "API token",
                "secret",
                help="Bearer token the bot sends to Media Studio.",
            ),
            Field("CONTENT_MEDIA_IMAGE_DRIVER", "Image driver", "text", default="api-image"),
            Field("CONTENT_MEDIA_VIDEO_DRIVER", "Video driver", "text", default="flow-video"),
            Field(
                "CONTENT_MEDIA_VIDEO_EDIT_DRIVER",
                "Video edit driver",
                "text",
                default="video-edit",
            ),
        ),
    ),
    Platform(
        key="chooser",
        label="Platform chooser",
        mode="flag",
        service="content-bot",
        summary="Master switch for the More platforms button on every draft.",
        docs="docs/CONTENT-PRODUCTION-GUIDE.md",
        fields=(
            Field(
                "CONTENT_PLATFORMS_ENABLED",
                "Offer extra platforms",
                "flag",
                default="true",
                help="true shows Bale, Eitaa, Instagram, and the package platforms on drafts.",
            ),
        ),
    ),
    Platform(
        key="youtube",
        label="YouTube",
        mode="package",
        service="content-bot",
        summary="No credentials: the bot hands over a title, description, and the media files.",
        docs="docs/CONTENT-PRODUCTION-GUIDE.md",
    ),
    Platform(
        key="aparat",
        label="Aparat",
        mode="package",
        service="content-bot",
        summary="No credentials: package handover, same as YouTube.",
        docs="docs/CONTENT-PRODUCTION-GUIDE.md",
    ),
    Platform(
        key="linkedin",
        label="LinkedIn",
        mode="package",
        service="content-bot",
        summary="No credentials: package handover with its own title and description limits.",
        docs="docs/CONTENT-PRODUCTION-GUIDE.md",
    ),
)

PLATFORM_INDEX = {platform.key: platform for platform in PLATFORMS}


def platform_for(key: str) -> Platform:
    platform = PLATFORM_INDEX.get(str(key or ""))
    if platform is None:
        raise EditError(f"unknown platform: {key}")
    return platform


def _fetch(url: str, headers: dict | None = None) -> tuple[int, str, str]:
    """GET one URL; returns ``(status, body, error)`` with no exception."""
    request = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return int(response.status), response.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8", "replace"), ""
    except (urllib.error.URLError, socket.timeout, OSError) as error:
        host = urllib.parse.urlsplit(url).netloc
        return 0, "", f"{host} is unreachable: {error}"


def _json_body(body: str) -> dict:
    try:
        payload = json.loads(body)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bot_api(api_base, token, path):
    base = str(api_base or "").rstrip("/")
    return f"{base}/bot{token}/{path}"


def test_telegram(get) -> tuple[bool, str]:
    token = get("CONTENT_BOT_TOKEN", secret=True)
    base = get("CONTENT_TELEGRAM_API_BASE") or "https://api.telegram.org"
    if not token:
        return False, "Store the bot token first."
    status, body, error = _fetch(f"{base.rstrip('/')}/bot{token}/getMe")
    if error:
        return False, error
    payload = _json_body(body)
    if status < 300 and payload.get("ok"):
        username = (payload.get("result") or {}).get("username") or "bot"
        return True, f"Token is valid: @{username}."
    return False, f"HTTP {status}: {_detail(payload, body)}"


def test_bale(get) -> tuple[bool, str]:
    token = get("CONTENT_BALE_TOKEN", secret=True)
    base = get("CONTENT_BALE_API_BASE") or "https://tapi.bale.ai"
    if not token:
        return False, "Store the bot token first."
    status, body, error = _fetch(_bot_api(base, token, "getMe"))
    if error:
        return False, error
    payload = _json_body(body)
    if status < 300 and payload.get("ok"):
        username = (payload.get("result") or {}).get("username") or "bot"
        return True, f"Token is valid: @{username}."
    return False, f"HTTP {status}: {_detail(payload, body)}"


def test_eitaa(get) -> tuple[bool, str]:
    token = get("CONTENT_EITAA_TOKEN", secret=True)
    base = get("CONTENT_EITAA_API_BASE") or "https://eitaayar.ir/api"
    if not token:
        return False, "Store the bot token first."
    status, body, error = _fetch(f"{base.rstrip('/')}/{token}/getMe")
    if error:
        return False, error
    payload = _json_body(body)
    if status < 300 and payload.get("ok"):
        name = (payload.get("result") or {}).get("first_name") or "the account"
        return True, f"Token is valid for {name}."
    return False, f"HTTP {status}: {_detail(payload, body)}"


def test_instagram(get) -> tuple[bool, str]:
    token = get("INSTAGRAM_ACCESS_TOKEN", secret=True)
    business = get("INSTAGRAM_BUSINESS_ID")
    base = (get("INSTAGRAM_API_BASE") or "https://graph.facebook.com").rstrip("/")
    version = get("INSTAGRAM_API_VERSION") or "v26.0"
    if not token:
        return False, "Store the access token first."
    if not business:
        return False, "Store the business account id first."
    fields = urllib.parse.urlencode({"fields": "id,username", "access_token": token})
    url = f"{base}/{version}/{urllib.parse.quote(business)}?{fields}"
    status, body, error = _fetch(url)
    if error:
        return False, error
    payload = _json_body(body)
    if status < 300 and payload.get("id"):
        username = payload.get("username") or ""
        return True, f"Business account {payload['id']} answered" + (
            f" (@{username})." if username else "."
        )
    return False, f"HTTP {status}: {_detail(payload, body)}"


def test_writer(get) -> tuple[bool, str]:
    base = (get("CONTENT_WRITER_BASE_URL") or "").rstrip("/")
    key = get("CONTENT_WRITER_API_KEY", secret=True)
    if not base:
        return False, "Store the base URL first."
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if not URL_RE.match(base):
        return False, "The base URL must start with http:// or https://."
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    attempts = [(f"{base}/models", "model list"), (f"{root}/health", "health probe")]
    last = ""
    for url, label in attempts:
        status, body, error = _fetch(url, headers=headers)
        if error:
            last = error
            continue
        payload = _json_body(body)
        if status < 300:
            models = payload.get("data")
            count = len(models) if isinstance(models, list) else 0
            note = f"{count} models" if count else "reachable"
            return True, f"{label} answered ({note})."
        last = f"HTTP {status} on {label}: {_detail(payload, body)}"
    return False, last or "the gateway did not answer."


def test_media(get) -> tuple[bool, str]:
    base = (get("CONTENT_MEDIA_STUDIO_URL") or "").rstrip("/")
    if not base:
        return False, "Store the base URL first."
    if not URL_RE.match(base):
        return False, "The base URL must start with http:// or https://."
    status, body, error = _fetch(f"{base}/healthz")
    if error:
        return False, error
    payload = _json_body(body)
    if status < 300:
        jobs = payload.get("jobs")
        note = f"{jobs} jobs tracked" if isinstance(jobs, int) else "healthy"
        return True, f"Media Studio answered ({note})."
    return False, f"HTTP {status}: {_detail(payload, body)}"


def _detail(payload: dict, body: str) -> str:
    """One short, token-free description of a failed provider answer."""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type")
        if message:
            return str(message)[:160]
    for name in ("description", "message", "detail", "error"):
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return (body or "no response body").strip()[:160]


TESTERS = {
    "telegram": test_telegram,
    "bale": test_bale,
    "eitaa": test_eitaa,
    "instagram": test_instagram,
    "writer": test_writer,
    "media": test_media,
}


class PlatformStore:
    """Read, validate, save, and test the platform keys of the stack."""

    def __init__(self, root, env: EnvStore | None = None):
        self.root = root
        self.env = env or EnvStore(root)

    # ---------------------------------------------------------------- views

    def view(self) -> dict:
        cards = [self._card(platform) for platform in PLATFORMS]
        return {"platforms": cards}

    def _card(self, platform: Platform) -> dict:
        fields = []
        for field in platform.fields:
            stored = self.env.value(field.key, "")
            secret = field.kind == "secret" or self.env.is_secret(field.key)
            fields.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "kind": field.kind,
                    "help": field.help,
                    "placeholder": field.placeholder or field.default,
                    "default": field.default,
                    "required": bool(field.required),
                    "secret": secret,
                    "set": bool(stored),
                    "value": None if secret else stored,
                }
            )
        return {
            "key": platform.key,
            "label": platform.label,
            "mode": platform.mode,
            "service": platform.service,
            "summary": platform.summary,
            "docs": platform.docs,
            "test": bool(platform.test),
            "state": self._state(platform, fields),
            "fields": fields,
        }

    def _state(self, platform: Platform, fields: list[dict]) -> str:
        if not fields:
            return "package"
        if platform.mode == "flag":
            return "on" if _truthy(self.env.value(fields[0]["key"], "")) else "off"
        if platform.mode == "package" and not _truthy(
            self.env.value("INSTAGRAM_AUTO_PUBLISH", "")
        ):
            return "package"
        required = [row for row in fields if row["required"]]
        provided = [row for row in fields if row["set"]]
        if required and all(row["set"] for row in required):
            return "ready"
        if provided:
            return "partial"
        return "empty"

    # ---------------------------------------------------------------- writes

    def update(self, key: str, values) -> dict:
        platform = platform_for(key)
        if not isinstance(values, dict) or not values:
            raise EditError("send at least one field to update")
        known = {field.key: field for field in platform.fields}
        changed = []
        for name, raw in values.items():
            name = str(name or "")
            field = known.get(name)
            if field is None:
                raise EditError(f"{name} is not a field of {platform.label}")
            text = "" if raw is None else str(raw).strip()
            _validate(field, text)
            self.env.set(name, text)
            changed.append(name)
        return {
            "key": platform.key,
            "label": platform.label,
            "changed": changed,
            "platform": self._card(platform),
            "service": platform.service,
        }

    # ----------------------------------------------------------------- tests

    def test(self, key: str, values=None) -> dict:
        platform = platform_for(key)
        tester = TESTERS.get(platform.test)
        if tester is None:
            raise EditError(
                f"{platform.label} has nothing to test: it hands over a package instead."
            )
        overrides = {}
        for name, raw in (values or {}).items():
            name = str(name or "")
            if name not in {field.key for field in platform.fields}:
                raise EditError(f"{name} is not a field of {platform.label}")
            overrides[name] = "" if raw is None else str(raw).strip()

        def get(env_key: str, secret: bool = False) -> str:
            if env_key in overrides:
                typed = overrides[env_key]
                if typed or not secret:
                    return typed
            return self.env.value(env_key, "")

        ok, detail = tester(get)
        return {
            "key": platform.key,
            "label": platform.label,
            "ok": bool(ok),
            "detail": detail,
            "service": platform.service,
        }


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _validate(field: Field, text: str) -> None:
    if "\n" in text or "\r" in text:
        raise EditError("values must stay on one line")
    if field.kind == "url" and text and not URL_RE.match(text):
        raise EditError(f"{field.label} must start with http:// or https://")
    if field.kind == "flag" and text.lower() not in FLAG_VALUES:
        raise EditError(f"{field.label} must be true or false")
