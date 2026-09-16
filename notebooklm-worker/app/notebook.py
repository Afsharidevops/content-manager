"""NotebookLM page automation: notebook, sources, and Studio.

Google ships the NotebookLM UI in the language of the signed-in account, and
the DOM changes without notice, so every step matches against a list of
selectors instead of a single one. The lists below are the calibration
baseline; ``NOTEBOOKLM_SELECTORS_FILE`` may point at a JSON document that
overrides any of them without a code change (see docs/NOTEBOOKLM-STUDIO.md).
"""

from __future__ import annotations

import json
import logging
import os
import time

from app import sources as sources_mod

LOGGER = logging.getLogger("notebooklm.notebook")

DEFAULT_SELECTORS: dict[str, list[str]] = {
    "new_notebook": [
        "button:has-text('Create new')",
        "button:has-text('New notebook')",
        "[role='button']:has-text('Create new')",
        "button[jsname]:has-text('add')",
        ".create-new-button",
        ".create-new-action-button",
        "[aria-label*='notebook' i]",
        "button:has-text('add')",
        "mat-card:has-text('add')",
    ],
    "notebook_title": [
        "input[aria-label*='notebook title' i]",
        "textarea[aria-label*='title' i]",
        "input[placeholder*='title' i]",
        "[contenteditable='true'][aria-label*='title' i]",
        "text=Untitled notebook",
    ],
    "add_source": [
        "button:has-text('Add source')",
        "button:has-text('Add sources')",
        "[role='button']:has-text('Add source')",
        "[aria-label*='Add source' i]",
    ],
    "source_file": [
        "button:has-text('Upload files')",
        "[role='menuitem']:has-text('Upload')",
        "[role='button']:has-text('Upload')",
    ],
    "source_website": [
        "button:has-text('Website')",
        "[role='menuitem']:has-text('Website')",
        "[role='button']:has-text('Website')",
    ],
    "source_youtube": [
        "button:has-text('YouTube')",
        "[role='menuitem']:has-text('YouTube')",
        "[role='button']:has-text('YouTube')",
    ],
    "source_text": [
        "button:has-text('Copied text')",
        "[role='menuitem']:has-text('Copied text')",
        "[role='button']:has-text('Copied text')",
    ],
    "file_input": ["input[type='file']"],
    "url_input": [
        "input[type='url']",
        "input[placeholder*='link' i]",
        "input[placeholder*='URL' i]",
        "input[aria-label*='URL' i]",
    ],
    "text_input": [
        "[role='dialog'] textarea",
        "[role='dialog'] [contenteditable='true']",
        "textarea",
    ],
    "source_confirm": [
        "[role='dialog'] button:has-text('Insert')",
        "[role='dialog'] button:has-text('Add')",
        "[role='dialog'] button:has-text('Upload')",
        "button:has-text('Insert')",
    ],
    "dialog_close": [
        "[role='dialog'] button[aria-label*='Close' i]",
        "[aria-label*='Close' i]",
    ],
    "studio_tab": [
        "button:has-text('Studio')",
        "[role='button']:has-text('Studio')",
        "[aria-label*='Studio' i]",
    ],
    "video_overview": [
        "button:has-text('Video Overview')",
        "[role='button']:has-text('Video Overview')",
        "[aria-label*='Video Overview' i]",
        "text=Video Overview",
    ],
    "video_customize": [
        "button:has-text('Customize')",
        "[role='button']:has-text('Customize')",
        "button:has-text('Edit')",
    ],
    "video_prompt": [
        "[role='dialog'] textarea",
        "[role='dialog'] [contenteditable='true']",
        "textarea[aria-label*='prompt' i]",
        "textarea",
    ],
    "video_generate": [
        "[role='dialog'] button:has-text('Generate')",
        "button:has-text('Generate')",
        "[role='button']:has-text('Generate')",
    ],
    "video_ready": [
        "[role='dialog'] video",
        "video",
        "button:has-text('Download')",
        "[aria-label*='Download' i]",
    ],
    "video_menu": [
        "button[aria-label*='More' i]",
        "button[aria-label*='menu' i]",
        "button:has-text('more_vert')",
    ],
    "video_download": [
        "[role='menuitem']:has-text('Download')",
        "button:has-text('Download')",
        "[role='menuitem']:has-text('download')",
    ],
}


class NotebookLMError(RuntimeError):
    """Raised when one automation step cannot be completed."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step


def load_selectors(path: str = "") -> dict[str, list[str]]:
    """Return the calibration baseline merged with the operator override."""
    selectors = {key: list(value) for key, value in DEFAULT_SELECTORS.items()}
    override_path = str(path or os.environ.get("NOTEBOOKLM_SELECTORS_FILE") or "").strip()
    if not override_path or not os.path.isfile(override_path):
        return selectors
    try:
        with open(override_path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        LOGGER.warning("Selector override %s is not valid JSON", override_path)
        return selectors
    if not isinstance(raw, dict):
        return selectors
    for key, value in raw.items():
        if key in selectors and isinstance(value, list) and value:
            selectors[key] = [str(item) for item in value if str(item).strip()]
    return selectors


def visible_nodes(page, selector: str):
    try:
        locator = page.locator(selector)
        count = min(locator.count(), 20)
    except Exception:  # noqa: BLE001 - a selector may be invalid for the DOM
        return []
    found = []
    for index in range(count):
        node = locator.nth(index)
        try:
            if node.is_visible():
                found.append(node)
        except Exception:  # noqa: BLE001 - detached nodes simply do not match
            continue
    return found


def find_first(page, selectors: list[str], *, timeout: float = 0.0, poll: float = 1.0):
    """Return the first visible node matching any selector."""
    deadline = time.time() + max(0.0, timeout)
    while True:
        for selector in selectors:
            found = visible_nodes(page, selector)
            if found:
                return found[0]
        if time.time() >= deadline:
            return None
        time.sleep(poll)


def click_first(page, selectors: list[str], *, step: str, timeout: float = 20.0):
    node = find_first(page, selectors, timeout=timeout)
    if node is None:
        raise NotebookLMError(step, f"no control matched {selectors}")
    node.click()
    return node


def fill_first(page, selectors: list[str], text: str, *, step: str, timeout: float = 20.0):
    node = find_first(page, selectors, timeout=timeout)
    if node is None:
        raise NotebookLMError(step, f"no field matched {selectors}")
    try:
        node.fill(text)
    except Exception:  # noqa: BLE001 - contenteditable needs a click first
        node.click()
        page.keyboard.type(text, delay=5)
    return node


class NotebookEditor:
    """Drive one NotebookLM notebook through a single job."""

    def __init__(self, page, settings, *, selectors: dict | None = None) -> None:
        self.page = page
        self.settings = settings
        self.selectors = selectors or load_selectors()

    # ---------------------------------------------------------- notebook

    def create_notebook(self, title: str) -> None:
        click_first(self.page, self.selectors["new_notebook"], step="create notebook")
        self.page.wait_for_timeout(2500)
        self.set_title(title)

    def set_title(self, title: str) -> None:
        if not str(title or "").strip():
            return
        node = find_first(self.page, self.selectors["notebook_title"], timeout=8)
        if node is None:
            LOGGER.info("Notebook title control not found; keeping the default title")
            return
        try:
            node.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.type(str(title)[:200], delay=10)
            self.page.keyboard.press("Enter")
        except Exception as error:  # noqa: BLE001 - a default title is acceptable
            LOGGER.warning("Notebook title could not be set: %s", error)

    # ------------------------------------------------------------ sources

    def add_material(self, material: sources_mod.Material) -> None:
        if material.kind == "file":
            self.add_file(material.path)
        elif material.kind == "url":
            self.add_link(material.value, kind="website")
        elif material.kind == "youtube":
            self.add_link(material.value, kind="youtube")
        else:
            self.add_text(material.value)

    def add_file(self, path: str) -> None:
        click_first(self.page, self.selectors["add_source"], step="open add source")
        click_first(self.page, self.selectors["source_file"], step="choose upload files")
        node = find_first(self.page, self.selectors["file_input"], timeout=20)
        if node is None:
            raise NotebookLMError("upload source", "no file input matched")
        node.set_input_files(path)
        self.page.wait_for_timeout(2000)
        self._close_dialog()

    def add_link(self, url: str, *, kind: str = "website") -> None:
        click_first(self.page, self.selectors["add_source"], step="open add source")
        key = "source_youtube" if kind == "youtube" else "source_website"
        click_first(self.page, self.selectors[key], step=f"choose {kind}")
        fill_first(self.page, self.selectors["url_input"], url, step=f"{kind} url")
        click_first(self.page, self.selectors["source_confirm"], step=f"add {kind}")
        self.page.wait_for_timeout(1500)
        self._close_dialog()

    def add_text(self, text: str) -> None:
        click_first(self.page, self.selectors["add_source"], step="open add source")
        click_first(self.page, self.selectors["source_text"], step="choose copied text")
        fill_first(self.page, self.selectors["text_input"], text, step="paste text")
        click_first(self.page, self.selectors["source_confirm"], step="insert text")
        self.page.wait_for_timeout(1500)
        self._close_dialog()

    def _close_dialog(self) -> None:
        node = find_first(self.page, self.selectors["dialog_close"], timeout=3)
        if node is not None:
            try:
                node.click()
            except Exception:  # noqa: BLE001 - the dialog may already be gone
                pass
        self.page.wait_for_timeout(500)

    def wait_for_sources(self, *, timeout: int = 0) -> None:
        """Wait until the source list stops showing progress indicators."""
        deadline = time.time() + (timeout or self.settings.source_timeout_seconds)
        quiet = 0
        while time.time() < deadline:
            busy = 0
            for selector in ("[role='progressbar']", "mat-progress-bar", "circle[role='progressbar']"):
                busy += len(visible_nodes(self.page, selector))
            quiet = quiet + 1 if busy == 0 else 0
            if quiet >= 3:
                return
            self.page.wait_for_timeout(3000)
        raise NotebookLMError(
            "source processing",
            "sources were still processing after "
            f"{timeout or self.settings.source_timeout_seconds}s",
        )

    # ------------------------------------------------------------- studio

    def open_studio(self) -> None:
        node = find_first(self.page, self.selectors["studio_tab"], timeout=8)
        if node is not None:
            try:
                node.click()
                self.page.wait_for_timeout(1500)
            except Exception as error:  # noqa: BLE001
                LOGGER.info("Studio control could not be clicked: %s", error)
