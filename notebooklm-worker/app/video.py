"""Scoped NotebookLM Video Overview automation."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256

from app import ui
from app.notebook import NotebookLMError, find_first

LOGGER = logging.getLogger("notebooklm.video")


@dataclass(frozen=True)
class VideoStyle:
    """One visual style discovered from the current NotebookLM UI."""

    label: str
    key: str
    slug: str


@dataclass
class GenerationTracker:
    """Identify the card created or changed by one generation request."""

    before: dict[str, str]
    style_label: str
    started_at: float
    card_identity: str = ""


def _regex(values: tuple[str, ...] | list[str]):
    return re.compile("|".join(re.escape(value) for value in values), re.IGNORECASE)


def _text(locator) -> str:
    try:
        return (locator.text_content(timeout=300) or "").strip()
    except Exception:
        return ""


def _attribute(locator, name: str) -> str:
    try:
        return (locator.get_attribute(name) or "").strip()
    except Exception:
        return ""


def _is_visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.is_visible()
    except Exception:
        return False


def _first_visible(locator):
    try:
        for index in range(min(locator.count(), 50)):
            node = locator.nth(index)
            if node.is_visible():
                return node
    except Exception:
        pass
    return None


def _click_ready(locator, *, step: str, timeout_ms: int = 5000) -> None:
    """Click a visible, enabled control with one bounded retry."""
    last_error: Exception | None = None
    for _ in range(2):
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            try:
                if locator.is_disabled():
                    raise NotebookLMError(step, "control is disabled")
            except AttributeError:
                pass
            locator.scroll_into_view_if_needed(timeout=timeout_ms)
            locator.click(timeout=timeout_ms)
            return
        except NotebookLMError:
            raise
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(0.2)
    raise NotebookLMError(step, f"click failed: {last_error}")


def _save_debug(page, settings, prefix: str, *, card=None) -> list[str]:
    """Save a bounded screenshot and JSON state for a failed async step."""
    logs_dir = os.path.join(settings.data_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    stamp = int(time.time())
    image_path = os.path.join(logs_dir, f"{prefix}-{stamp}.png")
    json_path = os.path.join(logs_dir, f"{prefix}-{stamp}.json")
    written: list[str] = []
    try:
        page.screenshot(path=image_path, full_page=False)
        written.append(image_path)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("Could not save %s screenshot: %s", prefix, error)
    data = {"url": str(getattr(page, "url", "") or ""), "step": prefix}
    try:
        data["title"] = page.title()
    except Exception:
        data["title"] = ""
    if card is not None:
        data["video_card_text"] = _text(card)[:2000]
    try:
        controls = page.locator("button:visible, [role='button']:visible, [role='menuitem']:visible")
        data["visible_controls"] = [
            {
                "text": _text(controls.nth(index))[:120],
                "aria_label": _attribute(controls.nth(index), "aria-label")[:120],
            }
            for index in range(min(controls.count(), 30))
        ]
    except Exception:
        data["visible_controls"] = []
    try:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        written.append(json_path)
    except OSError as error:
        LOGGER.warning("Could not save %s debug JSON: %s", prefix, error)
    if written:
        LOGGER.info("Debug artifacts for %s: %s", prefix, written)
    return written


def _dialog(page, timeout_seconds: int):
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        dialog = _first_visible(page.get_by_role("dialog"))
        if dialog is not None:
            return dialog
        time.sleep(0.2)
    return None


def _studio_root(page):
    for name in ("Studio", "استودیو"):
        root = _first_visible(page.get_by_role("region", name=re.compile(re.escape(name), re.I)))
        if root is not None:
            return root
    for selector in ("[aria-label*='Studio' i]", "[aria-label*='استودیو']", "[class*='studio']"):
        nodes = page.locator(selector)
        for index in range(min(nodes.count(), 50)):
            root = nodes.nth(index)
            if not _is_visible(root):
                continue
            tag = _attribute(root, "tagName").casefold()
            role = _attribute(root, "role").casefold()
            if role == "button":
                continue
            try:
                tag = root.evaluate("node => node.tagName.toLowerCase()")
            except Exception:
                tag = ""
            if tag == "button":
                continue
            return root
    return page


def _labeled_container(root, names: tuple[str, ...], css_selectors: tuple[str, ...]):
    """Find a scoped section by role/name, accessible label, text, then CSS."""
    pattern = _regex(names)
    for role in ("group", "region"):
        node = _first_visible(root.get_by_role(role, name=pattern))
        if node is not None:
            return node
    for name in names:
        for selector in (f"[aria-label*='{name}' i]", f"[data-testid*='{ui.safe_style_slug(name)}' i]"):
            node = _first_visible(root.locator(selector))
            if node is not None:
                return node
    for name in names:
        label = _first_visible(root.get_by_text(name, exact=True))
        if label is None:
            continue
        candidate = label
        for _ in range(4):
            try:
                candidate = candidate.locator("xpath=..")
                if candidate.locator("button, [role='button'], [role='radio'], [role='combobox']").count() > 0:
                    return candidate
            except Exception:
                break
    for selector in css_selectors:
        node = _first_visible(root.locator(selector))
        if node is not None:
            return node
    return None


def _open_video_customization(page, settings, selectors: dict):
    LOGGER.info("Opening Video Overview")
    studio = _studio_root(page)
    overview = _first_visible(studio.get_by_role("button", name=_regex(ui.labels("video_overview"))))
    if overview is None:
        overview = _first_visible(studio.get_by_text(_regex(ui.labels("video_overview"))))
    if overview is None:
        overview = find_first(studio, selectors.get("video_overview", []), timeout=5)
    if overview is None:
        # Fallback: search the whole page (Video Overview may be outside studio root)
        overview = _first_visible(page.get_by_role("button", name=_regex(ui.labels("video_overview"))))
    if overview is None:
        overview = find_first(page, selectors.get("video_overview", []), timeout=3)
    if overview is None:
        raise NotebookLMError("open video overview", "Video Overview control was not found in Studio")
    _click_ready(overview, step="open video overview")

    timeout = int(getattr(settings, "step_timeout_seconds", 45))
    dialog = _dialog(page, timeout)
    if dialog is None:
        customize = _first_visible(studio.get_by_role("button", name=_regex(ui.labels("customize"))))
        if customize is not None:
            _click_ready(customize, step="open video customization")
            dialog = _dialog(page, timeout)
    if dialog is None:
        _save_debug(page, settings, "video-customize-timeout")
        raise NotebookLMError("video customization", "customization dialog did not open")

    customize = _first_visible(dialog.get_by_role("button", name=_regex(ui.labels("customize"))))
    if customize is not None:
        _click_ready(customize, step="open video customization")
        dialog = _dialog(page, timeout) or dialog
    LOGGER.info("Video customization opened")
    return dialog


def _parse_count(text: str) -> int:
    translated = str(text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    match = re.search(r"(\d+)\s*(?:sources?|منبع)", translated, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _selected_source_count(container) -> int:
    count = _parse_count(_text(container))
    if count > 0:
        return count
    for selector in ("[aria-selected='true']", "[aria-checked='true']", "input:checked"):
        try:
            selected = container.locator(selector).count()
            if selected > 0:
                return selected
        except Exception:
            continue
    return 0


def _select_video_sources(page, dialog) -> int:
    """Keep preselected sources intact; only open the scoped source control at zero."""
    container = _labeled_container(
        dialog,
        ui.labels("sources"),
        ("[data-testid*='source-select']", "[class*='source-select']", "[class*='sources']"),
    )
    if container is None:
        raise NotebookLMError("video sources", "Sources container was not found in customization")
    selected = _selected_source_count(container)
    if selected > 0:
        LOGGER.info("Video sources already selected: %d", selected)
        return selected

    trigger = _first_visible(container.get_by_role("combobox"))
    if trigger is None:
        trigger = _first_visible(container.locator("button[aria-haspopup='listbox'], button[aria-haspopup='menu']"))
    if trigger is None:
        raise NotebookLMError("video sources", "no scoped source selector was found")
    _click_ready(trigger, step="open source selector")

    select_all = None
    for role in ("option", "menuitem", "menuitemcheckbox"):
        select_all = _first_visible(page.get_by_role(role, name=_regex(ui.labels("select_all"))))
        if select_all is not None:
            break
    if select_all is not None:
        _click_ready(select_all, step="select all sources")
    else:
        overlay = _first_visible(page.locator("[role='listbox']:visible, [role='menu']:visible"))
        if overlay is None:
            raise NotebookLMError("video sources", "source selector opened without options")
        choices = overlay.locator("[role='option'], [role='menuitemcheckbox']")
        clicked = 0
        for index in range(min(choices.count(), 50)):
            choice = choices.nth(index)
            if not choice.is_visible() or _attribute(choice, "aria-selected") == "true":
                continue
            _click_ready(choice, step="select video source")
            clicked += 1
        if clicked == 0:
            raise NotebookLMError("video sources", "no available source could be selected")
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    selected = _selected_source_count(container)
    if selected <= 0:
        raise NotebookLMError("video sources", "source selector still reports zero selected sources")
    LOGGER.info("Video sources selected: %d", selected)
    return selected


def _select_video_language(page, dialog, language: str = "persian") -> None:
    if ui.normalize_label(language) not in {"persian", ui.normalize_label("فارسی")}:
        raise NotebookLMError("video language", f"unsupported language: {language}")
    container = _labeled_container(
        dialog,
        ui.labels("language"),
        ("[data-testid*='language']", "[class*='language']"),
    )
    if container is None:
        dialog_text = ui.normalize_label(_text(dialog))
        if any(ui.normalize_label(label) in dialog_text for label in ui.labels("language")) and any(
            ui.normalize_label(label) in dialog_text for label in ui.labels("persian")
        ):
            LOGGER.info("Language: Persian (already selected)")
            return
        dialog_comboboxes = dialog.get_by_role("combobox")
        for index in range(min(dialog_comboboxes.count(), 20)):
            candidate = dialog_comboboxes.nth(index)
            if not _is_visible(candidate):
                continue
            candidate_text = ui.normalize_label(_text(candidate))
            if any(ui.normalize_label(label) in candidate_text for label in ui.labels("persian")):
                LOGGER.info("Language: Persian (already selected)")
                return
        raise NotebookLMError("video language", "Language container was not found")
    if any(ui.normalize_label(label) in ui.normalize_label(_text(container)) for label in ui.labels("persian")):
        LOGGER.info("Language: Persian (already selected)")
        return
    trigger = _first_visible(container.get_by_role("combobox"))
    if trigger is None:
        trigger = _first_visible(container.locator("button[aria-haspopup='listbox'], button[aria-haspopup='menu']"))
    if trigger is None:
        raise NotebookLMError("video language", "scoped language combobox was not found")
    _click_ready(trigger, step="open language selector")
    option = None
    for role in ("option", "menuitem"):
        option = _first_visible(page.get_by_role(role, name=_regex(ui.labels("persian"))))
        if option is not None:
            break
    if option is None:
        option = _first_visible(page.get_by_text(_regex(ui.labels("persian"))))
    if option is None:
        raise NotebookLMError("video language", "Persian option was not found")
    _click_ready(option, step="select Persian language")
    refreshed = _labeled_container(dialog, ui.labels("language"), ("[data-testid*='language']", "[class*='language']"))
    if refreshed is not None and not any(
        ui.normalize_label(label) in ui.normalize_label(_text(refreshed)) for label in ui.labels("persian")
    ):
        raise NotebookLMError("video language", "Persian did not become the selected language")
    LOGGER.info("Language: Persian")


def _option_cards(container):
    return container.locator(
        "[role='radio'], [data-option], [data-value], "
        "[class*='option-card'], [class*='format-card'], [class*='style-card'], "
        "mat-card, mat-radio-button, .tile-radio-button, .carousel-radio-button"
    )


def _card_label(card) -> str:
    aria = _attribute(card, "aria-label")
    if aria:
        return aria
    lines = [line.strip() for line in _text(card).splitlines() if line.strip()]
    label = lines[0] if lines else ""
    normalized = ui.normalize_label(label)
    if normalized.startswith("check "):
        return label.split(maxsplit=1)[1] if len(label.split(maxsplit=1)) > 1 else ""
    if normalized.startswith("check"):
        remainder = label[len("check"):].strip()
        if remainder:
            return remainder
    return label


def _card_selected(card) -> bool | None:
    for attribute in ("aria-selected", "aria-pressed", "aria-checked"):
        value = _attribute(card, attribute).lower()
        if value in {"true", "false"}:
            return value == "true"
    classes = _attribute(card, "class").casefold()
    if any(token in classes for token in ("selected", "active", "checked")):
        return True
    return None


def _find_option_card(container, labels: tuple[str, ...]):
    cards = _option_cards(container)
    normalized = [ui.normalize_label(label) for label in labels]
    compact = [label.replace(" ", "") for label in normalized]
    for index in range(min(cards.count(), 100)):
        card = cards.nth(index)
        label = ui.normalize_label(_card_label(card))
        text = ui.normalize_label(_text(card))
        compact_label = label.replace(" ", "")
        compact_text = text.replace(" ", "")
        if label in normalized or any(
            text == target
            or text.startswith(f"{target} ")
            or text.startswith(target)
            for target in normalized
        ) or compact_label in compact or any(compact_text.startswith(target) for target in compact):
            return card
    return None


def _is_style_card(card) -> bool:
    label = ui.normalize_label(_card_label(card))
    text = ui.normalize_label(_text(card))
    control_labels = (
        ui.labels("customize")
        + ui.labels("generate_now")
        + ui.labels("more")
        + ("next", "previous", "قبلی", "بعدی", "expand", "collapse")
    )
    if not label or any(ui.normalize_label(item) in label for item in control_labels):
        return False
    if any(ui.normalize_label(item) in text for item in ui.labels("format") + ui.labels("language")):
        return False
    role = _attribute(card, "role")
    classes = _attribute(card, "class").casefold()
    data_value = _attribute(card, "data-value") or _attribute(card, "data-option")
    if role == "radio" or data_value:
        return True
    return any(token in classes for token in ("style-card", "option-card", "visual-style", "carousel-radio-button"))


def _select_video_template(page, dialog, template: str = "explainer") -> str:
    """Select the card-based Format option."""
    del page
    normalized = ui.normalize_label(template)
    target = ui.labels("explainer") if normalized in {"explainer", "descriptive"} else ("Brief", "کوتاه", "قالب کوتاه")
    container = _labeled_container(
        dialog,
        ui.labels("format"),
        ("[data-testid*='format']", "[class*='format']", "[class*='template']"),
    )
    if container is None:
        if _first_visible(dialog.locator(".tile-radio-button, mat-radio-button")) is not None:
            container = dialog
    if container is None:
        raise NotebookLMError("video format", "Format card container was not found")
    card = _find_option_card(container, target)
    if card is None and container is not dialog:
        card = _find_option_card(dialog, target)
    if card is None:
        raise NotebookLMError("video format", f"format card was not found: {template}")
    if _card_selected(card) is not True:
        _click_ready(card, step=f"select video format {template}")
    selected = _card_selected(card)
    if selected is False:
        raise NotebookLMError("video format", f"format card did not become selected: {template}")
    label = _card_label(card) or template
    LOGGER.info("Format: %s", label)
    return label


def _scroll_style_carousel(container, direction: int) -> bool:
    try:
        result = container.evaluate(
            """(root, direction) => {
                const nodes = [root, ...root.querySelectorAll('*')];
                const scroller = nodes.find(node => node.scrollWidth > node.clientWidth + 4);
                if (!scroller) return false;
                const before = scroller.scrollLeft;
                const step = Math.max(120, Math.floor(scroller.clientWidth * 0.75));
                scroller.scrollLeft = before + direction * step;
                return scroller.scrollLeft !== before;
            }""",
            direction,
        )
        return bool(result)
    except Exception:
        return False


def _style_container(dialog):
    return _labeled_container(
        dialog,
        ui.labels("visual_style"),
        (
            "[data-testid*='visual-style']",
            "[data-testid*='style']",
            "[class*='visual-style']",
            "[class*='style-picker']",
            ".carousel-group",
        ),
    )


def discover_styles_from_dialog(dialog) -> list[VideoStyle]:
    """Discover every runtime style card, including cards in a carousel."""
    container = _style_container(dialog)
    if container is None:
        raise NotebookLMError("video style discovery", "Visual Style container was not found")
    discovered: dict[str, VideoStyle] = {}
    directions = [1] * 8 + [-1] * 8
    stagnant = 0
    for direction in directions:
        before = len(discovered)
        cards = _option_cards(container)
        for index in range(min(cards.count(), 100)):
            card = cards.nth(index)
            if not _is_style_card(card):
                continue
            label = _card_label(card)
            key = ui.normalize_label(label)
            if not key:
                continue
            try:
                card.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            discovered.setdefault(key, VideoStyle(label=label, key=key, slug=ui.safe_style_slug(label)))
        stagnant = stagnant + 1 if len(discovered) == before else 0
        moved = _scroll_style_carousel(container, direction)
        if stagnant >= 2 and not moved:
            break
        time.sleep(0.15)
    styles = list(discovered.values())
    LOGGER.info("Discovered video styles: %s", [style.label for style in styles])
    return styles


def _select_video_style(page, dialog, requested: str | None) -> VideoStyle | None:
    del page
    normalized = ui.normalize_label(requested or "")
    if normalized in {"", "auto", "default", "none", "null"}:
        LOGGER.info("Style: NotebookLM default")
        return None
    if normalized == "all":
        raise NotebookLMError("video style", "'all' must be orchestrated outside one customization dialog")
    container = _style_container(dialog)
    styles = discover_styles_from_dialog(dialog)
    available = [style.label for style in styles]
    matched = next(
        (style for style in styles if ui.style_matches(requested or "", style.label, style.key)),
        None,
    )
    if matched is None:
        raise NotebookLMError(
            "video style",
            f"Requested style {requested!r} was not found. Available styles: {available}",
        )
    card = _find_option_card(container, tuple(ui.style_aliases(requested or "")))
    if card is None:
        card = _find_option_card(container, (matched.label,))
    if card is None:
        raise NotebookLMError("video style", f"style card disappeared: {matched.label}")
    card.scroll_into_view_if_needed(timeout=3000)
    if _card_selected(card) is not True:
        _click_ready(card, step=f"select video style {matched.label}")
    selected = _card_selected(card)
    if selected is False:
        raise NotebookLMError("video style", f"style card did not become selected: {matched.label}")
    LOGGER.info("Style: %s", matched.label)
    return matched


def _fill_prompt(dialog, prompt: str, selectors: dict) -> None:
    if not str(prompt or "").strip():
        return
    prompt_field = _first_visible(dialog.get_by_role("textbox"))
    if prompt_field is None:
        prompt_field = find_first(dialog, selectors.get("video_prompt", []), timeout=3)
    if prompt_field is None:
        LOGGER.info("Video prompt field is not available; continuing with configured options")
        return
    try:
        prompt_field.fill(prompt)
    except Exception as error:
        raise NotebookLMError("video prompt", f"could not fill prompt: {error}") from error


def _video_cards(page) -> list:
    """Locate Video Overview artifact cards from Studio."""
    studio = _studio_root(page)
    overview_labels = {ui.normalize_label(label) for label in ui.labels("video_overview")}

    artifact_cards = studio.locator("[class~='artifact-item-button']")
    matched = []
    for index in range(min(artifact_cards.count(), 100)):
        card = artifact_cards.nth(index)
        if not _is_visible(card):
            continue
        content = _video_card_content(card)
        if any(label in content for label in overview_labels):
            matched.append(card)
    if matched:
        return matched

    fallback_selectors = (
        "[data-artifact-id]",
        "[data-testid*='video-overview']",
        "[class*='video-overview-card']",
        "[class*='artifact-card']",
        "article",
        "mat-card",
    )
    for selector in fallback_selectors:
        cards = studio.locator(selector)
        legacy = []
        for index in range(min(cards.count(), 100)):
            card = cards.nth(index)
            if not _is_visible(card):
                continue
            content = ui.normalize_label(f"{_text(card)} {_attribute(card, 'aria-label')}")
            if any(label in content for label in overview_labels):
                legacy.append(card)
        if legacy:
            return legacy
    return []


def _video_card_content(card) -> str:
    """Return text plus the nested semantic label of one artifact card."""
    described = _first_visible(card.locator("[aria-description]"))
    content_parts = [
        _text(card),
        _attribute(card, "aria-description"),
        _attribute(card, "aria-label"),
    ]
    if described is not None:
        content_parts.append(_attribute(described, "aria-description"))
        content_parts.append(_attribute(described, "aria-label"))
    return ui.normalize_label(" ".join(content_parts))


def _card_identity(card, index: int) -> str:
    for attribute in ("data-artifact-id", "data-id", "data-testid", "id"):
        value = _attribute(card, attribute)
        if value:
            return f"{attribute}:{value}"
    first_line = ""
    text = _text(card)
    if text:
        first_line = text.splitlines()[0]
    title = ui.normalize_label(_attribute(card, "aria-label") or first_line)
    digest = sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"fallback:{digest}:{index}"


def _card_signature(card) -> str:
    busy = 0
    for selector in ("[role='progressbar']", "progress", "[aria-busy='true']", "mat-progress-bar"):
        try:
            busy += card.locator(selector).count()
        except Exception:
            pass
    raw = f"{ui.normalize_label(_text(card))}|{_attribute(card, 'class')}|busy={busy}"
    return sha256(raw.encode("utf-8")).hexdigest()


def snapshot_video_cards(page) -> dict[str, str]:
    return {
        _card_identity(card, index): _card_signature(card)
        for index, card in enumerate(_video_cards(page))
    }


def _locate_generation_card(page, tracker: GenerationTracker):
    candidates = []
    changed = []
    for index, card in enumerate(_video_cards(page)):
        identity = _card_identity(card, index)
        signature = _card_signature(card)
        if tracker.card_identity and identity == tracker.card_identity:
            return card
        if identity not in tracker.before:
            candidates.append((identity, card))
        elif tracker.before[identity] != signature:
            changed.append((identity, card))
    pool = candidates or changed
    if len(pool) == 1:
        tracker.card_identity = pool[0][0]
        return pool[0][1]
    return None


def _card_completed(card) -> bool:
    for selector in ("[role='progressbar']", "progress", "[aria-busy='true']", "mat-progress-bar"):
        try:
            if _first_visible(card.locator(selector)) is not None:
                return False
        except Exception:
            pass
    text = ui.normalize_label(_text(card))
    busy_terms = ("generating", "processing", "creating", "در حال تولید", "درحال تولید", "در حال ساخت")
    if any(ui.normalize_label(term) in text for term in busy_terms):
        return False
    menu = _first_visible(card.get_by_role("button", name=_regex(ui.labels("more"))))
    if menu is None:
        menu = _first_visible(card.locator("button[aria-haspopup='menu'], button[title*='More' i]"))
    if menu is not None:
        return True
    for selector in ("video", "button[aria-label*='play' i]", "[data-status='complete']", "[aria-label*='Download' i]"):
        if _first_visible(card.locator(selector)) is not None:
            return True
    return any(term in text for term in ("ready", "complete", "آماده", "تکمیل"))


def _generate_button(dialog, selectors: dict):
    button = _first_visible(dialog.get_by_role("button", name=_regex(ui.labels("generate_now"))))
    if button is not None:
        return button
    return find_first(dialog, selectors.get("video_generate", []), timeout=3)


def _click_generate(page, dialog, selectors: dict, style_label: str) -> GenerationTracker:
    button = _generate_button(dialog, selectors)
    if button is None:
        raise NotebookLMError("video generation", "Generate now button was not found")
    if not _is_visible(button):
        raise NotebookLMError("video generation", "Generate now button is not visible")
    try:
        if button.is_disabled():
            raise NotebookLMError("video generation", "Generate now button is disabled")
    except AttributeError:
        pass
    before = snapshot_video_cards(page)
    LOGGER.info("Starting video generation")
    _click_ready(button, step="generate video overview")
    return GenerationTracker(before=before, style_label=style_label, started_at=time.time())


def discover_video_styles(page, settings, selectors: dict) -> list[VideoStyle]:
    """Open Customize once and return every Visual Style currently offered."""
    dialog = _open_video_customization(page, settings, selectors)
    _select_video_sources(page, dialog)
    styles = discover_styles_from_dialog(dialog)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    if not styles:
        raise NotebookLMError("video style discovery", "NotebookLM returned no visual style cards")
    return styles


def start_video_overview(
    page,
    prompt: str,
    settings,
    selectors: dict,
    *,
    style_name: str | None = None,
    video_template: str | None = None,
) -> GenerationTracker:
    """Configure and start one Video Overview generation."""
    dialog = _open_video_customization(page, settings, selectors)
    _select_video_sources(page, dialog)
    _select_video_language(page, dialog, language="persian")
    video_format = str(video_template or getattr(settings, "video_template", "explainer") or "explainer")
    _select_video_template(page, dialog, template=video_format)
    requested_style = style_name if style_name is not None else getattr(settings, "video_style", "auto")
    selected_style = _select_video_style(page, dialog, requested_style)
    _fill_prompt(dialog, prompt, selectors)
    return _click_generate(page, dialog, selectors, selected_style.label if selected_style else "default")


def video_ready(page, selectors: dict, card=None) -> bool:
    """Return readiness for one specific Video Overview card only."""
    del page, selectors
    return card is not None and _card_completed(card)


def wait_for_video(page, settings, selectors: dict, tracker: GenerationTracker | None = None):
    """Wait for the card belonging to this generation, never a page-global match."""
    tracker = tracker or GenerationTracker(snapshot_video_cards(page), "default", time.time())
    timeout = max(1, int(getattr(settings, "video_timeout_seconds", 1500)))
    poll = max(1, int(getattr(settings, "poll_seconds", 15)))
    deadline = time.time() + timeout
    LOGGER.info("Waiting for video generation")
    LOGGER.info("Video cards before generation: %d", len(tracker.before))
    while time.time() < deadline:
        card = _locate_generation_card(page, tracker)
        if card is not None and _card_completed(card):
            LOGGER.info("Video ready for style: %s", tracker.style_label)
            return card
        page.wait_for_timeout(min(poll, 5) * 1000)
    card = _locate_generation_card(page, tracker)
    total_cards = len(_video_cards(page))
    LOGGER.warning(
        "Video generation timeout: total_cards=%d tracked_card=%s",
        total_cards,
        "found" if card is not None else "not found",
    )
    _save_debug(page, settings, "video-generation-timeout", card=card)
    raise NotebookLMError(
        "video generation",
        f"Video Overview for style {tracker.style_label!r} was not ready after {timeout}s",
    )


def _open_card_menu(page, card):
    if not _card_completed(card):
        raise NotebookLMError("download video", "Video Overview card is not completed")
    menu_button = _first_visible(card.get_by_role("button", name=_regex(ui.labels("more"))))
    if menu_button is None:
        for selector in (
            "button[aria-haspopup='menu']",
            "button[aria-label*='More' i]",
            "button[aria-label*='بیشتر']",
            "button[title*='More' i]",
            "button[data-testid*='menu']",
        ):
            menu_button = _first_visible(card.locator(selector))
            if menu_button is not None:
                break
    if menu_button is None:
        raise NotebookLMError("download video", "three-dot menu was not found in the completed Video card")
    LOGGER.info("Opening video menu")
    try:
        _click_ready(menu_button, step="open video menu")
    except NotebookLMError as error:
        if "cdk-overlay-backdrop" not in str(error):
            raise
        existing_menu = _first_visible(page.get_by_role("menu"))
        if existing_menu is not None:
            return existing_menu
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass
        _click_ready(menu_button, step="open video menu")
    deadline = time.time() + 5
    while time.time() < deadline:
        menu = _first_visible(page.get_by_role("menu"))
        if menu is not None:
            return menu
        time.sleep(0.1)
    raise NotebookLMError("download video", "video menu did not open")


def _download_item(menu):
    item = _first_visible(menu.get_by_role("menuitem", name=_regex(ui.labels("download"))))
    if item is not None:
        return item
    for label in ui.labels("download"):
        item = _first_visible(menu.get_by_text(label, exact=True))
        if item is not None:
            return item
    return None


def _direct_video_fallback(page, card, target_path: str) -> bool:
    """Fetch a card-scoped video source only after the download event failed."""
    video = _first_visible(card.locator("video"))
    if video is None:
        return False
    try:
        source = video.evaluate("node => node.currentSrc || node.src || ''")
    except Exception:
        return False
    if not source:
        return False
    try:
        encoded = page.evaluate(
            """async url => {
                const response = await fetch(url);
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const buffer = await response.arrayBuffer();
                let binary = '';
                const bytes = new Uint8Array(buffer);
                for (let offset = 0; offset < bytes.length; offset += 32768) {
                    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
                }
                return btoa(binary);
            }""",
            source,
        )
        with open(target_path, "wb") as handle:
            handle.write(base64.b64decode(encoded))
        return os.path.getsize(target_path) > 0
    except Exception as error:  # noqa: BLE001
        LOGGER.info("Direct video fallback failed: %s", error)
        return False


def download_video(page, target_path: str, settings, selectors: dict, *, video_card=None) -> str:
    """Download one completed card via Playwright's download event."""
    del selectors
    if video_card is None:
        cards = [card for card in _video_cards(page) if _card_completed(card)]
        if len(cards) != 1:
            raise NotebookLMError("download video", "a specific completed Video card is required")
        video_card = cards[0]
    if not _card_completed(video_card):
        raise NotebookLMError("download video", "Video Overview card is not completed")
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    menu = _open_card_menu(page, video_card)
    item = _download_item(menu)
    if item is None:
        raise NotebookLMError("download video", "Download/دانلود/بارگیری item was not found in the open menu")
    timeout_ms = max(1, int(getattr(settings, "download_timeout_seconds", 300))) * 1000
    LOGGER.info("Starting download")
    event_error: Exception | None = None
    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            _click_ready(item, step="download video", timeout_ms=min(timeout_ms, 10000))
        download = download_info.value
        download.save_as(target_path)
    except Exception as error:  # noqa: BLE001
        event_error = error
        LOGGER.warning("Playwright download event failed: %s", error)
        if not _direct_video_fallback(page, video_card, target_path):
            _save_debug(page, settings, "video-download-failed", card=video_card)
            raise NotebookLMError("download video", f"download event failed: {error}") from error
    if not os.path.isfile(target_path) or os.path.getsize(target_path) <= 0:
        _save_debug(page, settings, "video-download-empty", card=video_card)
        raise NotebookLMError("download video", "downloaded file is missing or empty")
    if event_error is None:
        LOGGER.info("Download saved to: %s", target_path)
    else:
        LOGGER.info("Download fallback saved to: %s", target_path)
    _try_trim(target_path, int(getattr(settings, "trim_last_seconds", 0) or 0))
    return target_path


def _try_trim(path: str, trim_last: int) -> None:
    """Trim last N seconds from video if ffmpeg/ffprobe are available."""
    if trim_last <= 0:
        return
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        total = float(probe.stdout.strip() or 0)
        if total > trim_last + 1:
            _trim_video(path, total - trim_last)
        else:
            LOGGER.info("Video too short (%.1fs) to trim %ds", total, trim_last)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("Could not probe/trim video: %s", error)


def _trim_video(path: str, keep: float) -> str | None:
    """Trim a video to ``keep`` seconds from the start using ffmpeg."""
    import tempfile

    try:
        descriptor, temporary_path = tempfile.mkstemp(suffix=os.path.splitext(path)[1] or ".mp4")
        os.close(descriptor)
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-t",
                str(keep),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                temporary_path,
            ],
            capture_output=True,
            timeout=120,
            text=True,
        )
        if result.returncode != 0:
            LOGGER.warning("trim failed (ffmpeg exit %d): %s", result.returncode, result.stderr[:200])
            os.unlink(temporary_path)
            return None
        os.replace(temporary_path, path)
        LOGGER.info("Trimmed %s to %.1fs", path, keep)
        return path
    except FileNotFoundError:
        LOGGER.warning("ffmpeg not available, skip trim")
        return None
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("trim error: %s", error)
        return None
