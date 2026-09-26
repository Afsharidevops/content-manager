"""Instagram Reel Template system — template loader, plan builder, and preview."""

from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from content_bot import writer as writer_mod

log = logging.getLogger("content_bot.reel_templates")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates" / "instagram"

# ---------------------------------------------------------------------------
# Template field helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

_VISUAL_ANIMATION_MAP = {
    "scale": "zoom-in",
    "slide_up": "slide-up",
    "slide_left": "pan-left",
    "slide_right": "pan-right",
    "fade": "fade",
    "typewriter": "fade",
    "card_reveal": "scale",
    "floating": "float",
    "cursor_move": "pan-left",
    "zoom_in": "zoom-in",
}

_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")


def _english_or(default: str, value: str = "") -> str:
    """Return value only when it already contains Latin text."""
    text = " ".join(str(value or "").split()).strip()
    if text and _LATIN_RE.search(text) and not _ARABIC_SCRIPT_RE.search(text):
        return text
    return default


def _fill_placeholders(template: dict, values: dict[str, str]) -> dict:
    """Recursively replace {{key}} placeholders in strings."""
    def _replace(value: object) -> object:
        if isinstance(value, str):
            return _PLACEHOLDER_RE.sub(
                lambda m: values.get(m.group(1), m.group(0)), value
            )
        if isinstance(value, dict):
            return {k: _replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_replace(item) for item in value]
        return value
    return _replace(template)


def _derive_placeholder_values(
    title: str,
    body: str,
    source_url: str,
) -> dict[str, str]:
    """Build a placeholder map from draft content."""
    plain = writer_mod.Writer._strip_source_url(body, source_url) if source_url else body
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    first_line = lines[0] if lines else title
    words = re.split(r"\s+", plain)
    short = _english_or("A smarter way to explain this idea", " ".join(words[:20]))
    long_desc = _english_or(
        "A short, polished motion reel that explains the key idea with clean visuals",
        " ".join(words[:60]),
    )
    features = [
        w.strip().rstrip(".,")
        for w in words[1:40:13]
        if len(w.strip().rstrip(".,")) > 3
    ][:3]
    while len(features) < 3:
        features.append(f"Benefit {len(features) + 1}")

    topic = _english_or("This idea", title)
    hook = _english_or("This changes how you understand the topic", first_line)
    return {
        "hook_line": hook[:120],
        "problem_statement": short[:160],
        "solution_description": long_desc[:280],
        "benefits_list": " · ".join(features),
        "cta_text": "Try it now",
        "product_name": topic[:60],
        "value_proposition": "The smart way to " + short[:80] if short else title,
        "mockup_description": "See it in action",
        "interaction_description": "A quick walkthrough",
        "features_list": " · ".join(features),
        "before_problem": short[:160],
        "after_solution": topic[:120],
        "demo_description": "Watch how it works",
        "intro_line": short[:120],
        "step_1_text": long_desc[:160] if long_desc else "Step 1",
        "step_2_text": long_desc[160:320] if len(long_desc) > 160 else "Step 2",
        "step_3_text": long_desc[320:480] if len(long_desc) > 320 else "Step 3",
        "outro_line": "Follow for more!",
        "hook_claim": long_desc[:120] or title,
        "prompt_being_typed": long_desc[:80] if long_desc else "Create...",
        "generation_description": "AI is generating...",
        "result_description": long_desc[:200] or "Amazing result!",
        "topic": topic[:80],
    }


def _pick_duration(template: dict) -> int:
    """Return total duration from template or sum of scene durations."""
    total = template.get("total_duration") or 0
    if total:
        return int(total)
    scenes = template.get("scenes") or []
    return sum(int(s.get("duration", 4)) for s in scenes)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def available_templates() -> list[dict]:
    """List every template in the instagram templates directory.

    Returns metadata dicts with ``id``, ``label``, ``description``, and the
    raw template dict under ``template``.
    """
    results: list[dict] = []
    seen: set[str] = set()
    if not _TEMPLATES_DIR.is_dir():
        log.warning("reel templates directory not found: %s", _TEMPLATES_DIR)
        return results
    for path in sorted(_TEMPLATES_DIR.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("failed to load template %s: %s", path.name, exc)
            continue
        tid = str(data.get("id") or path.stem)
        if tid in seen:
            continue
        seen.add(tid)
        results.append(
            {
                "id": tid,
                "label": str(data.get("label") or path.stem.replace("_", " ").title()),
                "description": str(data.get("description") or ""),
                "total_duration": _pick_duration(data),
                "template": data,
            }
        )
    seen.clear()
    return results


def get_template(template_id: str) -> dict | None:
    """Return the full entry for one template id, or None."""
    for entry in available_templates():
        if entry["id"] == template_id:
            return entry
    return None


def template_to_plan(
    template_id: str,
    title: str = "",
    body: str = "",
    source_url: str = "",
) -> dict:
    """Convert one template + draft content into a storyboard/timeline plan.

    Returns the same shape as :meth:`ContentBot._agent_video_plan`:
    ``{"storyboard": …, "timeline": …}``.  The timeline returned here does
    **not** yet carry destination metadata; call
    :func:`apply_destination_meta` before rendering.
    """
    entry = get_template(template_id)
    if entry is None:
        raise ValueError(f"unknown template: {template_id}")

    template = entry["template"]
    placeholder_values = _derive_placeholder_values(title, body, source_url)
    filled = _fill_placeholders(template, placeholder_values)

    scenes_raw: list[dict] = filled.get("scenes") or []
    storyboard_scenes: list[dict] = []
    timeline_scenes: list[dict] = []

    for scene in scenes_raw:
        sid = int(scene.get("id", 0))
        duration = int(scene.get("duration", 4))
        narration = str(scene.get("narration") or "")
        visual = str(scene.get("visual") or "")
        emotion = str(scene.get("emotion") or "clear")
        animation_tag = str(scene.get("animation") or "fade")
        transition = str(scene.get("transition") or "fade")

        storyboard_scenes.append(
            {
                "duration": duration,
                "narration": narration,
                "visual": visual,
                "emotion": emotion,
            }
        )
        timeline_scenes.append(
            {
                "id": sid,
                "duration": duration,
                "narration": narration,
                "visual": visual,
                "transition": transition,
                "animation": _VISUAL_ANIMATION_MAP.get(animation_tag, "fade"),
            }
        )

    total = _pick_duration(filled)
    storyboard = {
        "title": title or template.get("label") or "AI video",
        "hook": storyboard_scenes[0].get("narration", "") if storyboard_scenes else title,
        "total_duration": total,
        "scenes": storyboard_scenes,
    }

    timeline: dict = {
        "version": 1,
        "meta": {"title": storyboard["title"], "subtitle": True},
        "scenes": timeline_scenes,
    }

    return {"storyboard": storyboard, "timeline": timeline}


def template_to_flow_prompt(
    template_id: str,
    title: str = "",
    body: str = "",
    source_url: str = "",
) -> str:
    """Convert one Instagram template into a Google Flow prompt."""
    entry = get_template(template_id)
    if entry is None:
        raise ValueError(f"unknown template: {template_id}")

    template = entry["template"]
    placeholder_values = _derive_placeholder_values(title, body, source_url)
    filled = _fill_placeholders(template, placeholder_values)
    scenes: list[dict] = filled.get("scenes") or []
    layout = filled.get("layout_rules") if isinstance(filled.get("layout_rules"), dict) else {}
    text_style = filled.get("text_style") if isinstance(filled.get("text_style"), dict) else {}
    animation_rules = filled.get("animation_rules") if isinstance(filled.get("animation_rules"), dict) else {}
    transition_rules = filled.get("transition_rules") if isinstance(filled.get("transition_rules"), dict) else {}
    asset_placement = filled.get("asset_placement") if isinstance(filled.get("asset_placement"), dict) else {}

    reel_title = _english_or(str(entry["label"]), title)[:90]
    total = _pick_duration(filled)
    lines: list[str] = [
        f"Create a {total}-second vertical Instagram motion reel in English only.",
        f"Template: {entry['label']}.",
        f"Topic: {reel_title}.",
        "Visual style: premium animated infographic reel, dark modern tech background, neon accent highlights, kinetic typography, clean icons, smooth camera motion, polished social media pacing.",
        "Format: 9:16 vertical video, 1080x1920, 30 fps, safe margins for Instagram UI.",
        "Do not use static title cards. Every scene must include motion, layered depth, animated text, and dynamic transitions.",
        "Use short English on-screen text only. Keep text readable and high contrast.",
    ]
    if body:
        source_body = writer_mod.Writer._strip_source_url(body, source_url) if source_url else body
        lines.append(
            "Content context: "
            + _english_or(
                "Explain the topic clearly with a premium product-demo feel.",
                source_body,
            )[:900]
        )
    lines.extend(
        [
            "",
            "Global rules:",
            f"- Layout: {json.dumps(layout, ensure_ascii=False)}",
            f"- Text style: {json.dumps(text_style, ensure_ascii=False)}",
            f"- Animation rules: {json.dumps(animation_rules, ensure_ascii=False)}",
            f"- Transition rules: {json.dumps(transition_rules, ensure_ascii=False)}",
            f"- Asset placement: {json.dumps(asset_placement, ensure_ascii=False)}",
            "- Use animated backgrounds, floating elements, parallax, card reveals, zoom highlights, and subtle glow effects.",
            "- Add browser, mobile, dashboard, icon, or product mockups when the scene calls for it.",
            "",
            "Scene timeline:",
        ]
    )
    for index, scene in enumerate(scenes, 1):
        label = str(scene.get("label") or f"Scene {index}").strip()
        duration = int(scene.get("duration") or 4)
        narration = _english_or("", str(scene.get("narration") or ""))
        visual = str(scene.get("visual") or "").strip()
        overlay = _english_or(narration, str(scene.get("text_overlay") or narration))
        animation = str(scene.get("animation") or animation_rules.get("default_in") or "fade")
        transition = str(scene.get("transition") or transition_rules.get("between_scenes") or "fade")
        asset = str(scene.get("asset") or "none")
        lines.extend(
            [
                f"{index}. {label} ({duration}s)",
                f"   On-screen text: {overlay[:140]}",
                f"   Voiceover idea: {narration[:220]}",
                f"   Visual direction: {visual}",
                f"   Animation: {animation}; transition: {transition}; asset: {asset}.",
            ]
        )
    lines.extend(
        [
            "",
            "Final quality target: match premium short-form animated explainer reels, with energetic pacing, modern typography, clean UI mockups, and no low-effort slideshow look.",
        ]
    )
    return "\n".join(lines).strip()


def apply_destination_meta(timeline: dict, destination: dict) -> dict:
    """Stamp destination aspect, resolution, and fps into a timeline copy.

    Mirrors :meth:`ContentBot._apply_video_destination` to avoid circular
    imports.
    """
    import json as _json

    cloned = _json.loads(_json.dumps(timeline or {}, ensure_ascii=False))
    meta: dict = cloned.get("meta") if isinstance(cloned.get("meta"), dict) else {}
    meta["aspect_ratio"] = str(destination.get("aspect_ratio") or "9:16")
    meta["resolution"] = str(destination.get("resolution") or "1080x1920")
    meta["fps"] = int(destination.get("fps") or meta.get("fps") or 30)
    cloned["meta"] = meta
    cloned["version"] = cloned.get("version") or 1
    return cloned


def template_preview(template_id: str, title: str = "", body: str = "") -> str:
    """Text preview for Telegram showing the template summary."""
    entry = get_template(template_id)
    if entry is None:
        return f"Template **{template_id}** not found."

    template = entry["template"]
    # Fill placeholders so the preview feels real
    placeholder_values = _derive_placeholder_values(title, body, "")
    filled = _fill_placeholders(template, placeholder_values)
    scenes = filled.get("scenes") or []

    lines: list[str] = [
        f"🎬 <b>{entry['label']}</b>",
        f"<i>{_html.escape(entry['description'])}</i>",
        f"Duration: ~{entry['total_duration']}s · {len(scenes)} scenes",
        "",
    ]
    for scene in scenes:
        sid = int(scene.get("id", 0))
        dur = int(scene.get("duration", 4))
        label = str(scene.get("label") or f"Scene {sid}")
        narration = str(scene.get("narration") or "")[:100]
        lines.append(f"  <b>{label}</b> ({dur}s)")
        if narration:
            lines.append(f"    {_html.escape(narration)}")
    return "\n".join(lines)
