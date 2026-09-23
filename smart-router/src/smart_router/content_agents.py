"""Content production agents for the Hermes Smart Router.

The router already plans and reviews work; this module adds the production
roles that sit between research and rendering:

* **Storyboard Agent** turns a topic, script, or research notes into scenes
  with hooks, pacing, and visual direction.
* **Video Director Agent** turns a script or storyboard into a *timeline
  document* - the only input the Media Studio renderer accepts, so a raw
  script never reaches the renderer.
* **Media Planning Agent** decides which assets each scene needs and picks the
  cheapest source (open media first, generated assets only when required).
* **NotebookLM Recovery Agent** reads a recorded browser page state and
  returns one recovery decision for the worker (retry, dismiss an overlay,
  wait, switch flow, or abort) instead of blind selector retries.

Every capability call is a validated JSON round trip: the model gets a strict
schema, the answer is parsed, and deterministic repair clamps the values before
the document leaves the router. The agents are seeded into the control
database on startup so they appear in the panel and the orchestrator catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .orchestrator_v60 import (
    PROFILE_CHOICES,
    _response_failure,
    chat_failure,
    extract_text,
    parse_json_object,
)

# --------------------------------------------------------------------------
# Schemas shared with the prompts below.
# --------------------------------------------------------------------------

ASPECT_PRESETS = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080", "4:5": "1080x1350"}
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
ANIMATIONS = ("none", "zoom-in", "zoom-out", "pan-left", "pan-right")
ASSET_TYPES = ("auto", "text", "solid", "image", "video")
RECOVERY_ACTIONS = ("retry", "dismiss_overlay", "wait", "switch_flow", "abort")

MAX_SCENES = 40
MIN_SCENE_SECONDS = 1.0
MAX_SCENE_SECONDS = 90.0

TIMELINE_PROFILE_DEFAULT = "standard"


@dataclass(frozen=True)
class ContentAgentDefinition:
    name: str
    description: str
    system_prompt: str
    tier: str = "standard"
    profile: str = "standard"


SCRIPT_SYSTEM_PROMPT = (
    "You are the Hermes Script Agent. Turn a topic or research notes into a concise, accurate video script. "
    "Use a compelling hook, coherent narration, and a clear call to action appropriate to the requested platform. "
    "Do not invent facts absent from the input. Reply with one JSON object matching this schema:\n"
    "{\n"
    "  \"title\": \"video title\",\n"
    "  \"hook\": \"the opening line to grab attention\",\n"
    "  \"body\": \"the full script narration, formatted with paragraphs\",\n"
    "  \"call_to_action\": \"the closing action phrase\"\n"
    "}"
)

STORYBOARD_SYSTEM_PROMPT = (
    "You are the Hermes Storyboard Agent for short-form and long-form video. "
    "Split the given content into ordered scenes, give every scene one concrete visual direction, "
    "keep the pacing appropriate for the platform, put the strongest hook first, and end with a clear closing beat. "
    "Write narration in the requested language, keep each scene between 2 and 20 seconds, and never invent facts "
    "that are absent from the source material. Reply with one JSON object only."
)

SCENE_SYSTEM_PROMPT = (
    "You are the Hermes Storyboard Agent revising one scene of an existing storyboard. "
    "Rewrite only the requested scene, keep its role in the story and every other scene untouched, and follow the "
    "operator instruction for this revision. Write narration in the requested language between 2 and 20 seconds of "
    "spoken content and never invent facts that are absent from the source material. Reply with one JSON object only."
)

VIDEO_DIRECTOR_SYSTEM_PROMPT = (
    "You are the Hermes Video Director Agent. You convert a script or storyboard into a timeline document that a "
    "deterministic ffmpeg renderer executes. The renderer receives only your timeline, never the raw script, so every "
    "scene must carry its own narration and visual direction. Choose transitions and camera animations that fit the "
    "emotion of each scene, keep the first scene on a cut, and never exceed the requested total duration by more than "
    "one scene. Reply with one JSON object only."
)

MEDIA_PLANNING_SYSTEM_PROMPT = (
    "You are the Hermes Media Planning Agent. For each scene decide the cheapest asset that still serves the story: "
    "prefer plain text cards, then openly licensed media (Openverse, Wikimedia Commons, Internet Archive), then "
    "generated images, and only generated video when motion is essential. Name one concrete search query per asset "
    "and estimate the cost in USD (open and text assets cost zero). Reply with one JSON object only."
)

NOTEBOOKLM_RECOVERY_SYSTEM_PROMPT = (
    "You are the Hermes NotebookLM Recovery Agent. You read one recorded browser page state from a NotebookLM "
    "automation run and decide the single next action: retry the same step, dismiss a blocking overlay or dialog, "
    "wait longer, switch to a different flow, or abort the run. Base the decision only on the given page state, "
    "never guess that a click succeeded, and prefer the action that cannot lose an already attached source. "
    "Reply with one JSON object only."
)


def _storyboard_prompt(payload: dict[str, Any]) -> str:
    return (
        "Build a storyboard from this request.\n\n"
        f"Topic or brief: {payload.get('topic') or '(not given)'}\n"
        f"Script or research notes: {payload.get('script') or '(not given)'}\n"
        f"Platform: {payload.get('platform') or 'unspecified'}\n"
        f"Target total duration (seconds): {payload.get('duration') or 'let the content decide'}\n"
        f"Language: {payload.get('language') or 'match the source language'}\n"
        f"Style: {payload.get('style') or 'natural'}\n\n"
        "Reply with this JSON shape:\n"
        "{\n"
        '  "title": "short working title",\n'
        '  "hook": "the opening line that stops the scroll",\n'
        '  "scenes": [\n'
        '    {"index": 1, "duration": 4, "narration": "what the voice says", "visual": "what the viewer sees",\n'
        '     "emotion": "curious", "transition": "cut", "animation": "zoom-in", "asset_type": "text"}\n'
        "  ],\n"
        '  "total_duration": 45\n'
        "}\n"
        f"Use at most {MAX_SCENES} scenes, durations between {MIN_SCENE_SECONDS:.0f} and "
        f"{MAX_SCENE_SECONDS:.0f} seconds, transitions from {list(TRANSITIONS)}, "
        f"animations from {list(ANIMATIONS)}, and asset types from {list(ASSET_TYPES)}."
    )


def _script_prompt(payload: dict[str, Any]) -> str:
    return (
        "Write a video script from this request.\n\n"
        f"Topic: {payload.get('topic') or '(not given)'}\n"
        f"Research notes: {payload.get('research') or payload.get('notes') or '(not given)'}\n"
        f"Platform: {payload.get('platform') or 'short-form social video'}\n"
        f"Duration: {payload.get('duration') or 'unspecified'}\n"
        f"Style: {payload.get('style') or 'clear and engaging'}\n"
        f"Language: {payload.get('language') or 'English'}\n\n"
        "Reply with: {\"title\": \"\", \"hook\": \"\", \"body\": \"\", \"call_to_action\": \"\"}."
    )

def _scene_prompt(payload: dict[str, Any]) -> str:
    return (
        "Rewrite one scene of this storyboard.\n\n"
        f"Topic or brief: {payload.get('topic') or '(not given)'}\n"
        f"Script or research notes: {payload.get('script') or '(not given)'}\n"
        f"Storyboard JSON: {json.dumps(payload.get('storyboard'), ensure_ascii=False) if payload.get('storyboard') else '(not given)'}\n"
        f"Scene number to rewrite: {payload.get('index') or 1}\n"
        f"Operator instruction: {payload.get('instruction') or '(none - rewrite it for clarity and pacing)'}\n"
        f"Language: {payload.get('language') or 'match the source language'}\n\n"
        "Keep the scene's role in the story: the same beat, in the same order, with the same call to action when it "
        "carries one. Reply with this JSON shape:\n"
        "{\n"
        '  "index": 2,\n'
        '  "duration": 4,\n'
        '  "narration": "the spoken line for this scene",\n'
        '  "visual": "what the viewer sees",\n'
        '  "emotion": "curious",\n'
        '  "transition": "fade",\n'
        '  "animation": "zoom-in",\n'
        '  "asset_type": "text"\n'
        "}\n"
        f"Durations stay between {MIN_SCENE_SECONDS:.0f} and 20 seconds, transitions come from {list(TRANSITIONS)}, "
        f"animations from {list(ANIMATIONS)}, and asset types from {list(ASSET_TYPES)}."
    )


def _timeline_prompt(payload: dict[str, Any]) -> str:
    aspect = str(payload.get("aspect_ratio") or "9:16")
    resolution = ASPECT_PRESETS.get(aspect, ASPECT_PRESETS["9:16"])
    return (
        "Convert this material into a render timeline.\n\n"
        f"Topic: {payload.get('topic') or '(not given)'}\n"
        f"Script: {payload.get('script') or '(not given)'}\n"
        f"Storyboard JSON: {json.dumps(payload.get('storyboard'), ensure_ascii=False) if payload.get('storyboard') else '(not given)'}\n"
        f"Target aspect ratio: {aspect}\n"
        f"Language: {payload.get('language') or 'match the source language'}\n\n"
        "Reply with this JSON shape:\n"
        "{\n"
        '  "version": 1,\n'
        f'  "meta": {{"title": "short title", "aspect_ratio": "{aspect}", "resolution": "{resolution}",\n'
        '            "fps": 30, "subtitle": true, "brand": {"label": "", "position": "bottom-right", "style": "aurora"}},\n'
        '  "scenes": [\n'
        '    {"id": 1, "duration": 4, "narration": "the spoken line for this scene", "visual": "short heading",\n'
        '     "asset_type": "text", "transition": "cut", "animation": "zoom-in", "emotion": "calm"}\n'
        "  ]\n"
        "}\n"
        "Rules: the first scene uses transition \"cut\"; every later scene uses one of "
        f"{list(TRANSITIONS)}; asset_type is one of {list(ASSET_TYPES)} and stays \"text\" unless an asset is named; "
        f"durations stay between {MIN_SCENE_SECONDS:.0f} and {MAX_SCENE_SECONDS:.0f} seconds; at most {MAX_SCENES} scenes; "
        "narration is complete and speakable, never a fragment."
    )


def _media_plan_prompt(payload: dict[str, Any]) -> str:
    return (
        "Plan the assets for this video.\n\n"
        f"Scenes: {json.dumps(payload.get('scenes'), ensure_ascii=False)}\n"
        f"Budget hint: {payload.get('budget') or 'keep it free when possible'}\n"
        f"Available providers: {payload.get('providers') or 'openverse, wikimedia-commons, internet-archive, api-image, api-video'}\n\n"
        "Reply with this JSON shape:\n"
        "{\n"
        '  "assets": [\n'
        '    {"scene_id": 1, "asset_type": "image", "source": "openverse", "query": "search phrase",\n'
        '     "reason": "why this asset", "estimated_cost_usd": 0}\n'
        "  ],\n"
        '  "total_estimated_cost_usd": 0,\n'
        '  "notes": "one line on the trade-offs"\n'
        "}\n"
        "Every scene gets exactly one asset decision, cheapest option first."
    )


def _recovery_prompt(payload: dict[str, Any]) -> str:
    return (
        "A NotebookLM automation step needs a recovery decision.\n\n"
        f"Step: {payload.get('step') or '(unknown)'}\n"
        f"Attempt: {payload.get('attempt') or 1}\n"
        f"Error: {payload.get('error') or '(none)'}\n"
        f"Page URL: {payload.get('url') or '(unknown)'}\n"
        f"Recorded page state: {json.dumps(payload.get('page_state'), ensure_ascii=False)}\n\n"
        "Reply with this JSON shape:\n"
        "{\n"
        '  "diagnosis": "what the page state shows",\n'
        f'  "action": "retry" | "dismiss_overlay" | "wait" | "switch_flow" | "abort",\n'
        '  "wait_seconds": 3,\n'
        '  "selector_hint": "",\n'
        '  "reason": "why this action is safe"\n'
        "}\n"
        "Use dismiss_overlay only when the page state shows a dialog or overlay that is not the source picker; "
        "use abort when the recorded state cannot distinguish success from failure."
    )


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _pick(value: Any, choices: tuple[str, ...], default: str) -> str:
    chosen = str(value or "").strip().lower()
    return chosen if chosen in choices else default


def _text(value: Any, limit: int) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def normalize_storyboard(payload: Any, *, default_transition: str = "fade") -> dict[str, Any]:
    """Clamp one storyboard answer into the documented shape."""
    if not isinstance(payload, dict):
        raise ValueError("the storyboard answer is not a JSON object")
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("the storyboard answer carries no scenes")
    scenes: list[dict[str, Any]] = []
    for index, item in enumerate(raw_scenes[:MAX_SCENES], start=1):
        item = item if isinstance(item, dict) else {}
        transition = _pick(item.get("transition"), TRANSITIONS, default_transition)
        if index == 1:
            transition = "cut"
        scenes.append(
            {
                "index": index,
                "duration": round(_clamp_float(item.get("duration"), MIN_SCENE_SECONDS, 20.0, 4.0), 2),
                "narration": _text(item.get("narration"), 2000),
                "visual": _text(item.get("visual"), 600),
                "emotion": _text(item.get("emotion"), 60),
                "transition": transition,
                "animation": _pick(item.get("animation"), ANIMATIONS, "none"),
                "asset_type": _pick(item.get("asset_type"), ASSET_TYPES, "text"),
            }
        )
    return {
        "title": _text(payload.get("title"), 200),
        "hook": _text(payload.get("hook"), 400),
        "scenes": scenes,
        "total_duration": round(sum(scene["duration"] for scene in scenes), 2),
    }


def normalize_scene(payload: Any, *, index: int = 1, default_transition: str = "fade") -> dict[str, Any]:
    """Clamp one regenerated scene into the storyboard scene shape."""
    if not isinstance(payload, dict):
        raise ValueError("the scene answer is not a JSON object")
    narration = _text(payload.get("narration"), 2000)
    visual = _text(payload.get("visual"), 600)
    if not narration and not visual:
        raise ValueError("the scene answer carries neither narration nor a visual")
    transition = _pick(payload.get("transition"), TRANSITIONS, default_transition)
    if index <= 1:
        transition = "cut"
    return {
        "index": index,
        "duration": round(_clamp_float(payload.get("duration"), MIN_SCENE_SECONDS, 20.0, 4.0), 2),
        "narration": narration,
        "visual": visual,
        "emotion": _text(payload.get("emotion"), 60),
        "transition": transition,
        "animation": _pick(payload.get("animation"), ANIMATIONS, "none"),
        "asset_type": _pick(payload.get("asset_type"), ASSET_TYPES, "text"),
    }


def normalize_timeline_document(payload: Any) -> dict[str, Any]:
    """Clamp one timeline answer into a render-ready document.

    Media Studio validates the document again on submit; this pass keeps the
    router honest about bounds and enums before the job is created.
    """
    if not isinstance(payload, dict):
        raise ValueError("the timeline answer is not a JSON object")
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("the timeline answer carries no scenes")
    meta_in = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    aspect = str(meta_in.get("aspect_ratio") or "9:16").strip()
    if aspect not in ASPECT_PRESETS:
        aspect = "9:16"
    fps = 30
    try:
        candidate = int(meta_in.get("fps") or 30)
        fps = candidate if candidate in (24, 25, 30, 50, 60) else 30
    except (TypeError, ValueError):
        fps = 30
    brand_in = meta_in.get("brand") if isinstance(meta_in.get("brand"), dict) else {}
    scenes: list[dict[str, Any]] = []
    for index, item in enumerate(raw_scenes[:MAX_SCENES], start=1):
        item = item if isinstance(item, dict) else {}
        transition = _pick(item.get("transition"), TRANSITIONS, "fade")
        if index == 1:
            transition = "cut"
        narration = _text(item.get("narration"), 2000)
        visual = _text(item.get("visual"), 300)
        scenes.append(
            {
                "id": index,
                "duration": round(_clamp_float(item.get("duration"), MIN_SCENE_SECONDS, MAX_SCENE_SECONDS, 5.0), 2),
                "narration": narration,
                "visual": visual,
                "asset_type": _pick(item.get("asset_type"), ASSET_TYPES, "text"),
                "upload_id": "",
                "asset_path": "",
                "transition": transition,
                "animation": _pick(item.get("animation"), ANIMATIONS, "none"),
                "emotion": _text(item.get("emotion"), 60),
            }
        )
    return {
        "version": 1,
        "meta": {
            "title": _text(meta_in.get("title"), 200),
            "aspect_ratio": aspect,
            "resolution": ASPECT_PRESETS[aspect],
            "fps": fps,
            "subtitle": bool(meta_in.get("subtitle", True)),
            "brand": {
                # The deployment brand is a render-time setting, never a model
                # choice: Media Studio stamps its configured label instead.
                "label": "",
                "position": _pick(
                    brand_in.get("position"),
                    ("top-left", "top-right", "bottom-left", "bottom-right"),
                    "bottom-right",
                ),
                "style": _pick(brand_in.get("style"), ("aurora", "chip"), "aurora"),
            },
        },
        "scenes": scenes,
    }


def normalize_media_plan(payload: Any) -> dict[str, Any]:
    """Clamp one media-plan answer into the documented shape."""
    if not isinstance(payload, dict):
        raise ValueError("the media plan answer is not a JSON object")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("the media plan answer carries no assets list")
    assets: list[dict[str, Any]] = []
    for item in raw_assets[:MAX_SCENES]:
        item = item if isinstance(item, dict) else {}
        try:
            scene_id = int(item.get("scene_id") or len(assets) + 1)
        except (TypeError, ValueError):
            scene_id = len(assets) + 1
        assets.append(
            {
                "scene_id": scene_id,
                "asset_type": _pick(item.get("asset_type"), ASSET_TYPES, "text"),
                "source": _text(item.get("source"), 60) or "text",
                "query": _text(item.get("query"), 200),
                "reason": _text(item.get("reason"), 300),
                "estimated_cost_usd": round(_clamp_float(item.get("estimated_cost_usd"), 0.0, 1000.0, 0.0), 4),
            }
        )
    total = round(sum(asset["estimated_cost_usd"] for asset in assets), 4)
    return {
        "assets": assets,
        "total_estimated_cost_usd": total,
        "notes": _text(payload.get("notes"), 400),
    }


def normalize_recovery(payload: Any) -> dict[str, Any]:
    """Clamp one recovery answer into the documented shape."""
    if not isinstance(payload, dict):
        raise ValueError("the recovery answer is not a JSON object")
    return {
        "diagnosis": _text(payload.get("diagnosis"), 600),
        "action": _pick(payload.get("action"), RECOVERY_ACTIONS, "abort"),
        "wait_seconds": round(_clamp_float(payload.get("wait_seconds"), 0.0, 120.0, 2.0), 1),
        "selector_hint": _text(payload.get("selector_hint"), 300),
        "reason": _text(payload.get("reason"), 400),
    }


async def _json_call(
    cp: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    profile: str = "",
    agent_id: int | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    """Run one agent turn and return the parsed JSON object.

    A second attempt asks the model to repair its previous answer instead of
    repeating the question, which recovers most malformed-object failures.
    """
    chosen = profile if profile in PROFILE_CHOICES else TIMELINE_PROFILE_DEFAULT
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_error = "the agent returned no JSON object"
    for attempt in range(1, max(1, attempts) + 1):
        if agent_id:
            result = await cp._run_agent(int(agent_id), user_prompt, messages[:-1])
        else:
            result = await cp._local_chat({"model": "auto", "messages": messages}, profile=chosen)
        refusal = _response_failure(result) or chat_failure(result)
        if refusal:
            last_error = refusal
            break
        text = extract_text(result)
        parsed = parse_json_object(text)
        if parsed is not None:
            return parsed
        last_error = "the answer did not contain a JSON object"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": text[:6000]},
            {
                "role": "user",
                "content": "That answer could not be parsed. Reply with one JSON object only, no prose and no code fences.",
            },
        ]
    raise ContentAgentError(last_error)


class ContentAgentError(RuntimeError):
    """Raised when a content agent cannot produce a usable answer."""

    def __init__(self, message: str, code: str = "content_agent_failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


async def build_script(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, str]:
    """Return a structured script suitable for the Storyboard Agent."""
    answer = await _json_call(
        cp,
        system_prompt=SCRIPT_SYSTEM_PROMPT,
        user_prompt=_script_prompt(payload),
        profile=profile,
        agent_id=agent_id,
    )
    fields = {key: str(answer.get(key) or "").strip() for key in ("title", "hook", "body", "call_to_action")}
    if not fields["body"]:
        raise ContentAgentError("script body is required", "script_invalid")
    return fields

async def build_storyboard(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, Any]:
    """Return a validated storyboard for one request."""
    answer = await _json_call(
        cp,
        system_prompt=STORYBOARD_SYSTEM_PROMPT,
        user_prompt=_storyboard_prompt(payload),
        profile=profile,
        agent_id=agent_id,
    )
    try:
        return normalize_storyboard(answer)
    except ValueError as error:
        raise ContentAgentError(str(error), "storyboard_invalid") from error


async def build_scene(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, Any]:
    """Regenerate one storyboard scene and leave every other scene to the caller."""
    index = _clamp_int(payload.get("index"), 1, MAX_SCENES, 1)
    answer = await _json_call(
        cp,
        system_prompt=SCENE_SYSTEM_PROMPT,
        user_prompt=_scene_prompt({**payload, "index": index}),
        profile=profile,
        agent_id=agent_id,
    )
    try:
        return normalize_scene(answer, index=index)
    except ValueError as error:
        raise ContentAgentError(str(error), "scene_invalid") from error


async def build_timeline(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, Any]:
    """Return a validated, render-ready timeline document."""
    answer = await _json_call(
        cp,
        system_prompt=VIDEO_DIRECTOR_SYSTEM_PROMPT,
        user_prompt=_timeline_prompt(payload),
        profile=profile,
        agent_id=agent_id,
    )
    try:
        return normalize_timeline_document(answer)
    except ValueError as error:
        raise ContentAgentError(str(error), "timeline_invalid") from error


async def build_media_plan(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, Any]:
    """Return one asset decision per scene."""
    answer = await _json_call(
        cp,
        system_prompt=MEDIA_PLANNING_SYSTEM_PROMPT,
        user_prompt=_media_plan_prompt(payload),
        profile=profile,
        agent_id=agent_id,
    )
    try:
        return normalize_media_plan(answer)
    except ValueError as error:
        raise ContentAgentError(str(error), "media_plan_invalid") from error


async def plan_video(cp: Any, payload: dict[str, Any], *, profile: str = "") -> dict[str, Any]:
    """Run storyboard then timeline, so callers get one render-ready plan."""
    storyboard = await build_storyboard(cp, payload, profile=profile)
    timeline = await build_timeline(
        cp,
        {
            "topic": payload.get("topic") or storyboard.get("title"),
            "script": payload.get("script"),
            "storyboard": storyboard,
            "aspect_ratio": payload.get("aspect_ratio") or "9:16",
            "language": payload.get("language"),
        },
        profile=profile,
    )
    return {"storyboard": storyboard, "timeline": timeline}


async def recovery_decision(cp: Any, payload: dict[str, Any], *, agent_id: int | None = None, profile: str = "") -> dict[str, Any]:
    """Return one recovery decision for a stalled browser step."""
    answer = await _json_call(
        cp,
        system_prompt=NOTEBOOKLM_RECOVERY_SYSTEM_PROMPT,
        user_prompt=_recovery_prompt(payload),
        profile=profile,
        agent_id=agent_id,
    )
    try:
        return normalize_recovery(answer)
    except ValueError as error:
        raise ContentAgentError(str(error), "recovery_invalid") from error


CONTENT_AGENTS: tuple[ContentAgentDefinition, ...] = (
    ContentAgentDefinition(
        name="Script Agent",
        description="Converts a topic or research notes into a structured video script with a hook and call to action.",
        system_prompt=SCRIPT_SYSTEM_PROMPT,
    ),
    ContentAgentDefinition(
        name="Storyboard Agent",
        description=(
            "Turns a topic, script, or research notes into ordered scenes with hooks, pacing, "
            "visual direction, and transitions for video production."
        ),
        system_prompt=STORYBOARD_SYSTEM_PROMPT,
    ),
    ContentAgentDefinition(
        name="Video Director Agent",
        description=(
            "Converts a script or storyboard into a render timeline document. The Media Studio renderer "
            "accepts only this document, never a raw script."
        ),
        system_prompt=VIDEO_DIRECTOR_SYSTEM_PROMPT,
    ),
    ContentAgentDefinition(
        name="Media Planning Agent",
        description=(
            "Decides the asset for every scene and prefers free, openly licensed sources before "
            "generated media to keep production cost low."
        ),
        system_prompt=MEDIA_PLANNING_SYSTEM_PROMPT,
    ),
    ContentAgentDefinition(
        name="NotebookLM Recovery Agent",
        description=(
            "Reads a recorded NotebookLM page state and returns one recovery decision for the browser worker: "
            "retry, dismiss an overlay, wait, switch flow, or abort."
        ),
        system_prompt=NOTEBOOKLM_RECOVERY_SYSTEM_PROMPT,
        tier="strong",
        profile="strong",
    ),
)


def ensure_content_agents(cp: Any) -> dict[str, list[str]]:
    """Create the content agents that are missing from the control database.

    Existing agents keep their operator edits: the seeder only adds names that
    do not exist yet, so panel changes are never overwritten on restart.
    """
    from sqlalchemy import select

    from .control_db import Agent

    created: list[str] = []
    existing: list[str] = []
    with cp.db.session() as session:
        rows = {row.name for row in session.scalars(select(Agent))}
        for definition in CONTENT_AGENTS:
            if definition.name in rows:
                existing.append(definition.name)
                continue
            session.add(
                Agent(
                    name=definition.name,
                    description=definition.description,
                    system_prompt=definition.system_prompt,
                    tier=definition.tier,
                    profile=definition.profile,
                    knowledge_json="[]",
                    plugins_json="[]",
                    permissions_json="[]",
                    active=True,
                )
            )
            created.append(definition.name)
        session.commit()
    return {"created": created, "existing": existing}


def content_agent_ids(cp: Any) -> dict[str, int]:
    """Return the control-database id of every seeded content agent."""
    from sqlalchemy import select

    from .control_db import Agent

    names = [definition.name for definition in CONTENT_AGENTS]
    with cp.db.session() as session:
        rows = list(session.scalars(select(Agent).where(Agent.name.in_(names))))
    return {row.name: row.id for row in rows}


def agent_id_for(cp: Any, name: str) -> int | None:
    """Return one agent id by name, or ``None`` when it is not seeded yet."""
    return content_agent_ids(cp).get(name)
