"""Storyboard drafts for the Video Studio scene editor.

The Storyboard and Video Director agents answer with JSON, and the operator
edits that answer scene by scene before anything is rendered. A draft is one
JSON file under ``data/panel/storyboards``: the brief, the storyboard the
agents returned, the render timeline, the approval trail and the last render
job. Files keep the panel stateless across restarts without a database.

Only this module touches the files. The HTTP layer goes through the panel app
methods, so every write is one atomic replace and every id is validated
before it becomes a path.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Draft lifecycle. ``rendering``/``rendered``/``failed`` follow the Media
# Studio job; ``approved``/``rejected`` are the operator verdict.
STATUSES = ("draft", "approved", "rejected", "rendering", "rendered", "failed")
EDITABLE_STATUSES = ("draft", "approved", "rejected")

# The renderer contract this editor writes against; Media Studio validates the
# document again on submit, so these only keep the stored draft coherent.
ASSET_TYPES = ("auto", "text", "solid", "image", "video")
ANIMATIONS = ("none", "zoom-in", "zoom-out", "pan-left", "pan-right")
TRANSITIONS = (
    "cut",
    "fade",
    "dissolve",
    "slideleft",
    "slideright",
    "wipeleft",
    "wipeup",
    "circleopen",
)

MAX_SCENES = 40
MIN_SCENE_SECONDS = 0.5
MAX_SCENE_SECONDS = 120.0
NARRATION_LIMIT = 2000
VISUAL_LIMIT = 600
SHORT_LIMIT = 60
TITLE_LIMIT = 200
NOTE_LIMIT = 400
DRAFT_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
DRAFT_LIMIT = 200

# Media Studio job status -> draft status.
JOB_STATUS_MAP = {
    "queued": "rendering",
    "running": "rendering",
    "done": "rendered",
    "failed": "failed",
}


class StoryboardError(ValueError):
    """Raised when a draft cannot be read or the patch is not valid."""


class StoryboardMissing(StoryboardError):
    """Raised when a draft id does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: object, limit: int) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def _pick(value: object, choices: tuple[str, ...], default: str) -> str:
    chosen = str(value or "").strip().lower()
    return chosen if chosen in choices else default


def _duration(value: object, default: float = 4.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(MIN_SCENE_SECONDS, min(MAX_SCENE_SECONDS, number)), 2)


def normalize_scene(item: object, index: int) -> dict:
    """Clamp one timeline scene the way the renderer would read it."""
    item = item if isinstance(item, dict) else {}
    transition = _pick(item.get("transition"), TRANSITIONS, "fade")
    if index <= 1:
        transition = "cut"
    return {
        "id": index,
        "duration": _duration(item.get("duration")),
        "narration": _text(item.get("narration"), NARRATION_LIMIT),
        "visual": _text(item.get("visual"), VISUAL_LIMIT),
        "asset_type": _pick(item.get("asset_type"), ASSET_TYPES, "text"),
        "upload_id": _text(item.get("upload_id"), TITLE_LIMIT),
        "asset_path": _text(item.get("asset_path"), 400),
        "transition": transition,
        "animation": _pick(item.get("animation"), ANIMATIONS, "none"),
        "emotion": _text(item.get("emotion"), SHORT_LIMIT),
    }


def normalize_scenes(value: object) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise StoryboardError("a draft needs at least one scene.")
    if len(value) > MAX_SCENES:
        raise StoryboardError(f"a draft holds at most {MAX_SCENES} scenes.")
    return [normalize_scene(item, index) for index, item in enumerate(value, start=1)]


def _storyboard_scenes(timeline_scenes: list[dict], incoming: object = None) -> list[dict]:
    """Mirror the timeline scenes back into the storyboard shape.

    The storyboard keeps the agent's numbering and its wider duration range so
    the two views of the same draft never disagree after an edit.
    """
    keep: list[dict] = []
    if isinstance(incoming, list):
        keep = [item for item in incoming if isinstance(item, dict)]
    scenes: list[dict] = []
    for index, scene in enumerate(timeline_scenes, start=1):
        source = keep[index - 1] if index <= len(keep) else {}
        scenes.append(
            {
                "index": index,
                "duration": scene["duration"],
                "narration": scene["narration"],
                "visual": scene["visual"],
                "emotion": scene["emotion"],
                "transition": scene["transition"],
                "animation": scene["animation"],
                "asset_type": scene["asset_type"],
                "role": _text(source.get("role"), SHORT_LIMIT),
            }
        )
    return scenes


def _meta(payload: object, fallback: dict) -> dict:
    meta = dict(fallback) if isinstance(fallback, dict) else {}
    incoming = payload if isinstance(payload, dict) else {}
    for key in ("title", "aspect_ratio", "resolution", "fps", "subtitle"):
        if key in incoming:
            meta[key] = incoming[key]
    meta["title"] = _text(meta.get("title"), TITLE_LIMIT)
    brand = meta.get("brand") if isinstance(meta.get("brand"), dict) else {}
    in_brand = incoming.get("brand") if isinstance(incoming.get("brand"), dict) else {}
    merged_brand = dict(brand)
    merged_brand.update({key: value for key, value in in_brand.items() if key in ("label", "position", "style")})
    meta["brand"] = {
        "label": _text(merged_brand.get("label"), 80),
        "position": _pick(merged_brand.get("position"), ("top-left", "top-right", "bottom-left", "bottom-right"), "bottom-right"),
        "style": _pick(merged_brand.get("style"), ("aurora", "chip"), "aurora"),
    }
    aspect = str(meta.get("aspect_ratio") or "9:16").strip()
    meta["aspect_ratio"] = aspect if aspect in ("9:16", "16:9", "1:1", "4:5") else "9:16"
    try:
        fps = int(meta.get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30
    meta["fps"] = fps if fps in (24, 25, 30, 50, 60) else 30
    meta["subtitle"] = bool(meta.get("subtitle", True))
    return meta


class StoryboardStore:
    """Read and write storyboard drafts under ``data/panel/storyboards``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.dir = self.root / "data" / "panel" / "storyboards"

    # ----------------------------------------------------------- internals

    def _path(self, draft_id: str) -> Path:
        if not DRAFT_ID_RE.fullmatch(str(draft_id or "")):
            raise StoryboardMissing(f"unknown storyboard draft {draft_id}.")
        return self.dir / f"{draft_id}.json"

    def _read(self, draft_id: str) -> dict:
        path = self._path(draft_id)
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise StoryboardMissing(f"unknown storyboard draft {draft_id}.") from error
        except (OSError, ValueError) as error:
            raise StoryboardError(f"storyboard draft {draft_id} is not readable: {error}") from error
        if not isinstance(draft, dict):
            raise StoryboardError(f"storyboard draft {draft_id} is not a JSON object.")
        return draft

    def _write(self, draft: dict) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        draft["updated_at"] = _now()
        handle, tmp_name = tempfile.mkstemp(dir=str(self.dir), prefix=".draft-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(draft, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.dir / f"{draft['id']}.json")
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return draft

    @staticmethod
    def _summary(draft: dict) -> dict:
        timeline = draft.get("timeline") if isinstance(draft.get("timeline"), dict) else {}
        scenes = timeline.get("scenes") if isinstance(timeline.get("scenes"), list) else []
        meta = timeline.get("meta") if isinstance(timeline.get("meta"), dict) else {}
        job = draft.get("job") if isinstance(draft.get("job"), dict) else {}
        return {
            "id": str(draft.get("id") or ""),
            "title": str(draft.get("title") or ""),
            "status": str(draft.get("status") or "draft"),
            "created_at": str(draft.get("created_at") or ""),
            "updated_at": str(draft.get("updated_at") or ""),
            "scenes": len(scenes),
            "duration": round(sum(float(scene.get("duration") or 0) for scene in scenes if isinstance(scene, dict)), 2),
            "aspect_ratio": str(meta.get("aspect_ratio") or ""),
            "job_id": str(job.get("id") or ""),
            "job_status": str(job.get("status") or ""),
        }

    # ------------------------------------------------------------------ api

    def list(self) -> list[dict]:
        """Summaries, newest first. A broken file never hides the others."""
        if not self.dir.is_dir():
            return []
        summaries: list[dict] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(draft, dict) and draft.get("id"):
                summaries.append(self._summary(draft))
        summaries.sort(key=lambda row: row["updated_at"], reverse=True)
        return summaries[:DRAFT_LIMIT]

    def read(self, draft_id: str) -> dict:
        return self._read(draft_id)

    def create(self, *, brief: dict, storyboard: dict, timeline: dict) -> dict:
        """Store one planned draft and return it."""
        scenes = normalize_scenes((timeline or {}).get("scenes"))
        meta = _meta((timeline or {}).get("meta"), {})
        storyboard = storyboard if isinstance(storyboard, dict) else {}
        title = _text(storyboard.get("title") or meta.get("title"), TITLE_LIMIT)
        try:
            version = int((timeline or {}).get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        draft = {
            "id": f"{stamp}-{secrets.token_hex(3)}",
            "title": title,
            "status": "draft",
            "created_at": _now(),
            "updated_at": _now(),
            "brief": {
                "topic": _text((brief or {}).get("topic"), 400),
                "script": _text((brief or {}).get("script"), 4000),
                "language": _text((brief or {}).get("language"), 20),
                "style": _text((brief or {}).get("style"), 60),
                "duration": (brief or {}).get("duration") or 0,
            },
            "storyboard": {
                "title": title,
                "hook": _text(storyboard.get("hook"), 400),
                "scenes": _storyboard_scenes(scenes, storyboard.get("scenes")),
                "total_duration": round(sum(scene["duration"] for scene in scenes), 2),
            },
            "timeline": {"version": version, "meta": meta, "scenes": scenes},
            "job": {"id": "", "status": "", "driver": "", "artifact": ""},
            "trail": [{"at": _now(), "action": "created", "note": ""}],
        }
        return self._write(draft)

    def update(self, draft_id: str, patch: dict) -> dict:
        """Apply the editor's patch: title, hook, meta and the scene list."""
        draft = self._read(draft_id)
        patch = patch if isinstance(patch, dict) else {}
        timeline = draft.get("timeline") if isinstance(draft.get("timeline"), dict) else {}
        storyboard = draft.get("storyboard") if isinstance(draft.get("storyboard"), dict) else {}
        if "scenes" in patch:
            scenes = normalize_scenes(patch.get("scenes"))
        else:
            scenes = normalize_scenes(timeline.get("scenes"))
        meta = _meta(patch.get("meta"), timeline.get("meta"))
        if "title" in patch:
            meta["title"] = _text(patch.get("title"), TITLE_LIMIT)
        draft["title"] = _text(meta.get("title") or draft.get("title"), TITLE_LIMIT)
        draft["timeline"] = {
            "version": timeline.get("version") or 1,
            "meta": meta,
            "scenes": scenes,
        }
        draft["storyboard"] = {
            "title": draft["title"],
            "hook": _text(patch.get("hook", storyboard.get("hook")), 400),
            "scenes": _storyboard_scenes(scenes, storyboard.get("scenes")),
            "total_duration": round(sum(scene["duration"] for scene in scenes), 2),
        }
        return self._write(draft)

    def replace_scene(self, draft_id: str, index: int, scene: dict) -> dict:
        """Replace one scene, keeping the rest of the draft untouched."""
        draft = self._read(draft_id)
        timeline = draft.get("timeline") if isinstance(draft.get("timeline"), dict) else {}
        scenes = timeline.get("scenes") if isinstance(timeline.get("scenes"), list) else []
        if not scenes:
            raise StoryboardError("this draft has no scenes to replace.")
        if index < 1 or index > len(scenes):
            raise StoryboardError(f"scene {index} is outside this draft (1-{len(scenes)}).")
        replaced = normalize_scene(scene, index)
        if index == 1:
            replaced["transition"] = "cut"
        updated = [dict(item) for item in scenes]
        updated[index - 1] = replaced
        return self.update(draft_id, {"scenes": updated})

    def set_status(self, draft_id: str, status: str, note: str = "") -> dict:
        if status not in EDITABLE_STATUSES:
            raise StoryboardError(f"{status} is not an operator verdict.")
        draft = self._read(draft_id)
        draft["status"] = status
        trail = draft.get("trail") if isinstance(draft.get("trail"), list) else []
        trail.append({"at": _now(), "action": status, "note": _text(note, NOTE_LIMIT)})
        draft["trail"] = trail[-40:]
        return self._write(draft)

    def record_job(self, draft_id: str, job: dict) -> dict:
        """Attach one Media Studio job and follow its status."""
        draft = self._read(draft_id)
        job = job if isinstance(job, dict) else {}
        status = str(job.get("status") or "")
        draft["job"] = {
            "id": str(job.get("id") or ""),
            "status": status,
            "driver": str(job.get("driver") or ""),
            "artifact": _text(job.get("error") or "", NOTE_LIMIT),
        }
        mapped = JOB_STATUS_MAP.get(status)
        if mapped:
            draft["status"] = mapped
        trail = draft.get("trail") if isinstance(draft.get("trail"), list) else []
        trail.append({"at": _now(), "action": f"render {status or 'submitted'}".strip(), "note": str(job.get("id") or "")})
        draft["trail"] = trail[-40:]
        return self._write(draft)

    def delete(self, draft_id: str) -> None:
        path = self._path(draft_id)
        try:
            path.unlink()
        except FileNotFoundError as error:
            raise StoryboardMissing(f"unknown storyboard draft {draft_id}.") from error
        except OSError as error:
            raise StoryboardError(f"storyboard draft {draft_id} could not be deleted: {error}") from error
