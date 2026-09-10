"""Instagram Graph API publisher (official Meta Graph API, no extra deps)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from content_bot import rtl as rtl_mod
from content_bot.http import request_bytes

GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v26.0"
CAPTION_MAX = 2200
CAROUSEL_MAX = 10
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_POLL_TIMEOUT_SECONDS = 600


class InstagramError(RuntimeError):
    """Raised for setup, network, and Graph API publish failures."""


def _strip_markup(text: str) -> str:
    """Remove lightweight markup that is meaningless on Instagram."""
    value = str(text or "")
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"`(.+?)`", r"\1", value)
    return value.replace("\r\n", "\n").replace("\r", "\n")


def build_caption(title: str, body: str, source_url: str = "") -> str:
    """Build one plain-text Instagram caption from a draft.

    The title is the first line, body paragraphs follow, and the source link
    appears once at the very end when it is not already part of the copy.
    """
    title = rtl_mod.mark_caption_head(_strip_markup(title).strip())
    body = _strip_markup(body).strip()
    source_url = str(source_url or "").strip()
    paragraphs = [
        rtl_mod.mark_lines(part.strip())
        for part in re.split(r"\n\s*\n", body)
        if part.strip()
    ]

    def assemble(parts: list[str]) -> str:
        caption = title
        if parts:
            caption = f"{caption}\n\n" + "\n\n".join(parts)
        if source_url and source_url not in caption:
            caption = f"{caption}\n\n{source_url}"
        return caption

    full = assemble(paragraphs)
    if len(full) <= CAPTION_MAX:
        return full
    kept: list[str] = []
    for paragraph in paragraphs:
        if len(assemble(kept + [paragraph])) <= CAPTION_MAX:
            kept.append(paragraph)
        else:
            break
    return assemble(kept)


class InstagramPublisher:
    """Publish photos, carousels, and videos through the Instagram Graph API.

    Media containers are created from publicly reachable URLs. The bot stores
    artifacts under its data directory; ``media_root`` and ``media_base_url``
    map a local file back to the public URL Meta can fetch.
    """

    def __init__(
        self,
        business_id: str,
        access_token: str,
        *,
        media_base_url: str = "",
        media_root: str = "",
        api_version: str = DEFAULT_API_VERSION,
        graph_base: str = GRAPH_BASE,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        poll_timeout_seconds: int = DEFAULT_POLL_TIMEOUT_SECONDS,
    ):
        self.business_id = str(business_id or "").strip()
        self.access_token = str(access_token or "").strip()
        self.media_base_url = str(media_base_url or "").strip().rstrip("/")
        self.media_root = str(media_root or "").strip()
        self.api_version = str(api_version or "").strip() or DEFAULT_API_VERSION
        self.graph_base = str(graph_base or "").strip().rstrip("/") or GRAPH_BASE
        self.poll_interval_seconds = max(1, int(poll_interval_seconds or 5))
        self.poll_timeout_seconds = max(10, int(poll_timeout_seconds or 600))

    # ------------------------------------------------------------- plumbing

    def _node_url(self, node_id: str) -> str:
        return f"{self.graph_base}/{self.api_version}/{node_id}"

    def _decode(self, status: int, raw: bytes) -> dict:
        if status >= 400:
            detail = ""
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                data = {}
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
                code = error.get("code")
                subcode = error.get("error_subcode")
                if code:
                    detail += f" (code {code}"
                    detail += f", subcode {subcode}" if subcode else ""
                    detail += ")"
            elif isinstance(data, dict):
                detail = str(data.get("error_message") or data.get("message") or "")
            raise InstagramError(
                f"Instagram API HTTP {status}"
                + (f": {detail}" if detail else "")
            )
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as error:
            raise InstagramError("Instagram returned invalid JSON") from error
        if not isinstance(data, dict):
            raise InstagramError("Instagram returned an unexpected response")
        return data

    def _request(self, method: str, url: str, fields: dict | None = None) -> dict:
        raw_body = None
        content_type = None
        if fields:
            raw_body = urlencode(
                {key: value for key, value in fields.items() if value is not None}
            ).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        try:
            status, raw = request_bytes(
                url,
                method=method,
                raw_body=raw_body,
                content_type=content_type,
                timeout=90,
            )
        except ConnectionError as error:
            raise InstagramError(f"Instagram network error: {error}") from error
        return self._decode(status, raw)

    def _get(self, node_id: str, fields: str) -> dict:
        query = urlencode({"fields": fields, "access_token": self.access_token})
        return self._request("GET", f"{self._node_url(node_id)}?{query}")

    def _post(self, path: str, fields: dict) -> dict:
        fields = dict(fields)
        fields.setdefault("access_token", self.access_token)
        return self._request("POST", f"{self._node_url(self.business_id)}/{path}", fields)

    # ------------------------------------------------------------ lifecycle

    def validate(self) -> dict:
        """Confirm the token and business id against the Graph API."""
        if not self.business_id or not self.access_token:
            raise InstagramError(
                "Instagram is not configured: set INSTAGRAM_BUSINESS_ID and "
                "INSTAGRAM_ACCESS_TOKEN."
            )
        data = self._get(self.business_id, "id,username")
        if "username" not in data:
            raise InstagramError(
                "The token cannot read the Instagram business account; check "
                "the token permissions and business id."
            )
        return {
            "id": str(data.get("id") or self.business_id),
            "username": str(data["username"]),
        }

    def public_url(self, local_path) -> str:
        """Map one local media file to the public URL Meta can fetch."""
        if not self.media_base_url:
            raise InstagramError(
                "INSTAGRAM_MEDIA_PUBLIC_BASE_URL is empty; the Instagram API "
                "needs a public URL for every photo or video."
            )
        path = Path(local_path)
        if not path.is_file():
            raise InstagramError(f"media file is missing from storage: {path.name}")
        if self.media_root:
            try:
                relative = path.resolve().relative_to(Path(self.media_root).resolve())
            except ValueError as error:
                raise InstagramError(
                    f"media file is outside the served media root: {path}"
                ) from error
        else:
            relative = Path(path.name)
        return f"{self.media_base_url}/{relative.as_posix()}"

    def _create_container(self, fields: dict) -> str:
        data = self._post("media", fields)
        creation_id = str(data.get("id") or "")
        if not creation_id:
            raise InstagramError("Instagram did not return a media container id")
        return creation_id

    def _wait_container(self, container_id: str) -> None:
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            data = self._get(container_id, "status_code,status,error_message")
            status_code = str(data.get("status_code") or "").upper()
            if status_code in {"FINISHED", "PUBLISHED"}:
                return
            if status_code in {"ERROR", "EXPIRED"}:
                detail = str(data.get("error_message") or data.get("status") or "")
                raise InstagramError(f"Instagram media processing failed: {detail}")
            if time.monotonic() >= deadline:
                raise InstagramError("Instagram media processing timed out")
            time.sleep(self.poll_interval_seconds)

    def _publish_container(self, container_id: str) -> str:
        data = self._post("media_publish", {"creation_id": container_id})
        media_id = str(data.get("id") or "")
        if not media_id:
            raise InstagramError("Instagram did not return a published media id")
        return media_id

    # ------------------------------------------------------------ publishing

    def publish_photo(self, caption: str, image_url: str) -> str:
        container = self._create_container({"image_url": image_url, "caption": caption})
        self._wait_container(container)
        return self._publish_container(container)

    def publish_carousel(self, caption: str, image_urls: list[str]) -> str:
        if not 2 <= len(image_urls) <= CAROUSEL_MAX:
            raise InstagramError(
                f"Instagram carousels need between 2 and {CAROUSEL_MAX} photos; "
                f"got {len(image_urls)}."
            )
        children = [
            self._create_container({"image_url": url, "is_carousel_item": "true"})
            for url in image_urls
        ]
        container = self._create_container(
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
            }
        )
        self._wait_container(container)
        return self._publish_container(container)

    def publish_video(self, caption: str, video_url: str, *, as_reels: bool = False) -> str:
        fields = {
            "media_type": "REELS" if as_reels else "VIDEO",
            "video_url": video_url,
            "caption": caption,
        }
        if as_reels:
            fields["share_to_feed"] = "true"
        container = self._create_container(fields)
        self._wait_container(container)
        return self._publish_container(container)

    def publish(self, caption: str, items: list[dict]) -> dict:
        """Publish one post built from local media items.

        ``items`` entries are ``{"kind": "image"|"video", "path": str}``. A
        single photo publishes as a photo, several photos as a carousel, and a
        single video as a feed video. Mixed and multi-video posts are refused
        with a clear message.
        """
        photos = [item for item in items if str(item.get("kind") or "") == "image"]
        videos = [item for item in items if str(item.get("kind") or "") == "video"]
        if not photos and not videos:
            raise InstagramError("Instagram publishing needs at least one photo or video")
        if photos and videos:
            raise InstagramError(
                "Instagram posts cannot mix photos and videos in one media set; "
                "publish the photo post and the video post separately."
            )
        if len(videos) > 1:
            raise InstagramError("Instagram posts accept one video per publish")
        urls = [self.public_url(item["path"]) for item in items]
        if videos:
            path = Path(str(videos[0]["path"]))
            if path.suffix.lower() not in {".mp4", ".mov"}:
                raise InstagramError(
                    "Instagram feed videos must be MP4 or MOV; convert the "
                    "uploaded video before publishing."
                )
            media_id = self.publish_video(caption, urls[0])
            return {"kind": "video", "media_id": media_id}
        if len(urls) == 1:
            media_id = self.publish_photo(caption, urls[0])
            return {"kind": "photo", "media_id": media_id}
        media_id = self.publish_carousel(caption, urls)
        return {"kind": "carousel", "media_id": media_id}


__all__ = [
    "CAPTION_MAX",
    "CAROUSEL_MAX",
    "DEFAULT_API_VERSION",
    "InstagramError",
    "InstagramPublisher",
    "build_caption",
]
