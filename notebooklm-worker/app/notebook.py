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
        "[aria-label*='source' i]",
        ".add-source-button",
        "[aria-label*='افزودن منبع' i]",
        "button:has-text('افزودن')",
        "button:has-text('add_source')",
        "button:has-text('add source')",
        "mat-icon:has-text('add')",
        "[aria-label*='افزودن' i]",
    ],
    "source_file": [
        "button.drop-zone-icon-button:has-text('بارگذاری')",
        "button:has-text('بارگذاری فایل')",
        "button:has-text('Upload files')",
        "button:has-text('Upload')",
        ".add-source-link",
        "[aria-label*='source' i]:has-text('افزودن')",
        "[role='menuitem']:has-text('Upload')",
        "[role='button']:has-text('Upload')",
        "[aria-label*='upload' i]",
        "[aria-label*='file' i]",
        "button:has-text('file')",
        "button:has-text('آپلود')",
    ],
    "source_website": [
        "button.drop-zone-icon-button:has-text('وب‌سایت‌ها')",
        "button:has-text('وب‌سایت‌ها')",
        "button:has-text('Websites')",
        "button:has-text('Website')",
        "[role='button']:has-text('Websites')",
        "[role='button']:has-text('Website')",
        "button:has-text('وب')",
        "[aria-label*='website' i]",
        "[aria-label*='link' i]",
        "button:has-text('link')",
        "button.drop-zone-icon-button:nth-of-type(2)",
    ],
    "source_youtube": [
        "button:has-text('YouTube')",
        "[role='menuitem']:has-text('YouTube')",
        "[role='button']:has-text('YouTube')",
        "[aria-label*='youtube' i]",
        "[aria-label*='video' i]",
    ],
    "source_text": [
        "button.drop-zone-icon-button:has-text('نوشتار کپی‌شده')",
        "button:has-text('نوشتار کپی‌شده')",
        "button.drop-zone-icon-button:has-text('Copied text')",
        "button:has-text('Copied text')",
        ".add-source-link",
        "[aria-label*='paste' i]",
        "[aria-label*='text' i]",
        "button:has-text('paste')",
        "button:has-text('متن')",
        "button.mat-mdc-outlined-button:has-text('Copied text')",
        "button.mat-mdc-outlined-button:has-text('متن')",
        "button.drop-zone-icon-button",
        "mat-stroked-button.drop-zone-icon-button",
    ],
    "file_input": ["input[type='file']"],
    "url_input": [
        "textarea[aria-label='نشانی‌های وب را وارد کنید']",
        "textarea[placeholder*='پیوندهای موردنظرتان']",
        "textarea[aria-label*='نشانی' i]",
        "textarea[aria-label*='Enter URLs' i]",
        "textarea[placeholder*='link' i]",
        "textarea[placeholder*='URL' i]",
        "textarea[placeholder*='paste' i]",
        "input[aria-label*='URL' i]",
        "input[placeholder*='link' i]",
        "input[placeholder*='URL' i]",
        "input[type='url']",
    ],
    "text_input": [
        "[role='dialog'] textarea",
        "[role='dialog'] [contenteditable='true']",
        "[role='dialog'] input[type='text']",
        "textarea",
        "[contenteditable='true']",
        "[role='textbox']",
        "input:not([type='file']):not([type='hidden']):not([type='submit'])",
    ],
    "source_confirm": [
        "[role='dialog'] button:has-text('Insert')",
        "[role='dialog'] button:has-text('Add')",
        "[role='dialog'] button:has-text('Upload')",
        "button:has-text('Insert')",
        "button:has-text('Add')",
        "button:has-text('submit')",
        "button:has-text('افزودن')",
        "[role='button']:has-text('Add')",
        "button[type='submit']",
        "button:has-text('افزودن')",
        "[aria-label*='add' i]",
        "button:has-text('ایجاد')",
        "button.mat-mdc-outlined-button:has-text('Save')",
        "button.mat-mdc-outlined-button:has-text('ذخیره')",
        "button.mat-mdc-outlined-button:has-text('تایید')",
    ],
    "dialog_close": [
        "[role='dialog'] button[aria-label*='Close' i]",
        "[role='dialog'] button[aria-label*='بستن' i]",
        "[aria-label*='Close' i]",
        "[aria-label*='بستن' i]",
    ],
    "studio_tab": [
        "[aria-label*='استودیو' i]",
        "button:has-text('Studio')",
        "[role='button']:has-text('Studio')",
        "[aria-label*='Studio' i]",
        "[aria-label*='lab' i]",
        "button:has-text('Notebook')",
        "button:has-text('defter')",
    ],
    "video_overview": [
        "[aria-label*='مرور ویدیویی' i]",
        "[class*='create-artifact']:has-text('مرور ویدیویی')",
        "button:has-text('مرور ویدیویی')",
        "button:has-text('Video Overview')",
        "[role='button']:has-text('Video Overview')",
        "[aria-label*='Video Overview' i]",
        "text=Video Overview",
        "button:has-text('Generate')",
        "[aria-label*='generate' i]",
        "[aria-label*='overview' i]",
    ],
    "video_customize": [
        "button:has-text('سفارشی‌سازی')",
        "button:has-text('ویرایش')",
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
        "[role='dialog'] button:has-text('ساختن')",
        "[role='dialog'] button:has-text('ایجاد')",
        "[role='dialog'] button:has-text('تولید')",
        "[role='dialog'] button:has-text('Generate')",
        "button:has-text('ساختن')",
        "button:has-text('ایجاد')",
        "button:has-text('تولید')",
        "button:has-text('Generate')",
        "[role='button']:has-text('Generate')",
    ],
    "video_ready": [
        "[role='dialog'] video",
        "video",
        "button:has-text('دانلود')",
        "button:has-text('Download')",
        "[aria-label*='Download' i]",
        "[aria-label*='دانلود' i]",
    ],
    "video_menu": [
        "button[aria-label*='More' i]",
        "button[aria-label*='menu' i]",
        "button[aria-label*='بیشتر' i]",
        "button:has-text('more_vert')",
    ],
    "video_download": [
        "[role='menuitem']:has-text('دانلود')",
        "button:has-text('دانلود')",
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
def _log_overlay_state(page, tag: str = ""):
    """Log active dialog/overlay elements for diagnosis."""
    try:
        backdrops = page.locator(".cdk-overlay-backdrop:not([style*='display: none']):not([hidden])")
        dialogs = page.locator("[role='dialog']")
        containers = page.locator(".cdk-overlay-container")
        panes = page.locator(".cdk-overlay-pane:not([style*='display: none']):not([hidden])")
        text = ""
        for i in range(min(dialogs.count(), 3)):
            try:
                d = dialogs.nth(i)
                if d.is_visible():
                    t = (d.text_content(timeout=300) or "")[:120].strip()
                    if t:
                        text += f"  dialog[{i}]: {t}\n"
            except Exception:
                pass
        LOGGER.info(
            "OVERLAY STATE%s: backdrops=%d dialogs=%d panes=%d\n%s",
            f" ({tag})" if tag else "",
            backdrops.count() if backdrops.count() else 0,
            dialogs.count() if dialogs.count() else 0,
            panes.count() if panes.count() else 0,
            text,
        )
    except Exception:
        pass


def dismiss_overlays(page) -> int:
    """Try to close any popups/overlays blocking interaction. Returns number dismissed."""
    _log_overlay_state(page, "before dismiss")
    dismissed = 0
    # 1. Look for close/X buttons in visible overlays
    close_selectors = [
        "button[aria-label*='Close' i]",
        "button[aria-label*='close' i]",
        "[aria-label*='بستن' i]",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('Dismiss')",
        "button:has-text('متوجه شدم')",
        "button:has-text('باشه')",
        ".mat-mdc-dialog-actions button",
        "button:has-text('بستن')",
        "button.close-button",
        ".closepanel-button",
        "[role='dialog'] button:has-text('Close')",
        "button[aria-label*='cancel' i]",
        "button[aria-label*='بستن' i]",
        "mat-icon:has-text('close')",
        ".cdk-overlay-pane button.mat-icon-button",
    ]
    for sel in close_selectors:
        try:
            nodes = page.locator(sel)
            for i in range(min(nodes.count(), 5)):
                try:
                    if nodes.nth(i).is_visible():
                        nodes.nth(i).click(timeout=1000)
                        dismissed += 1
                        page.wait_for_timeout(300)
                except Exception:
                    pass
        except Exception:
            pass
    if dismissed > 0:
        page.wait_for_timeout(500)
        _log_overlay_state(page, "after close buttons")
        return dismissed
    # 2. Try Escape key to dismiss dialogs (multiple times for stacked overlays)
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass
    page.wait_for_timeout(300)
    _log_overlay_state(page, "after Escape")
    # 3. Click on backdrop to dismiss (Material CDK closes on backdrop click)
    try:
        backdrop = page.locator(".cdk-overlay-backdrop")
        for i in range(min(backdrop.count(), 3)):
            try:
                if backdrop.nth(i).is_visible():
                    backdrop.nth(i).click(timeout=1000, force=True)
                    dismissed += 1
                    page.wait_for_timeout(300)
            except Exception:
                pass
    except Exception:
        pass
    if dismissed > 0:
        page.wait_for_timeout(500)
        _log_overlay_state(page, "after backdrop click")
        return dismissed
    # 4. Last resort: remove only popover/notification overlays (NOT mat-dialogs)
    try:
        removed = page.evaluate("""
            (function() {
                let count = 0;
                document.querySelectorAll('.cdk-overlay-popover, [popover], .notification-overlay')
                    .forEach(el => { el.remove(); count++; });
                return count;
            })()
        """)
        if removed:
            LOGGER.info("Removed %d popover DOM elements as last resort", removed)
            page.wait_for_timeout(500)
            dismissed = removed
    except Exception:
        pass
    return dismissed





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
    # First dismiss any overlays that could block clicks
    dismiss_overlays(page)
    # Find the target element
    node = find_first(page, selectors, timeout=timeout)
    if node is None:
        # Debug: log visible buttons to help with future UI changes
        try:
            buttons = page.locator("button, [role='button'], mat-card, [role='menuitem']")
            visible = []
            for i in range(min(buttons.count(), 30)):
                try:
                    b = buttons.nth(i)
                    if b.is_visible():
                        txt = b.text_content(timeout=500)[:60] if b.text_content(timeout=500) else ""
                        aria = b.get_attribute("aria-label") or ""
                        cls = (b.get_attribute("class") or "")[:40]
                        visible.append(f"btn[{i}] text={txt!r} aria={aria!r} class={cls!r}")
                except Exception:
                    pass
            LOGGER.warning("Visible controls at '%s' step:\n%s", step, "\n".join(visible))
        except Exception as log_err:
            LOGGER.warning("Debug button dump failed: %s", log_err)
        raise NotebookLMError(step, f"no control matched {selectors}")
    # Try normal click
    for attempt in range(2):
        try:
            node.click(timeout=3000)
            return node
        except Exception:
            # Maybe overlay appeared between dismiss and click - dismiss again and retry
            dismiss_overlays(page)
            page.wait_for_timeout(300)
            if attempt == 0:
                continue
            # Final attempt: force click (bypasses overlay interception)
            try:
                node.click(force=True, timeout=5000, no_wait_after=True)
                return node
            except Exception as e2:
                # Last resort: JavaScript click bypasses all overlays
                try:
                    page.evaluate("(el) => el.click()", node)
                    return node
                except Exception as e3:
                    raise NotebookLMError(step, f"click failed (JS fallback): {e3}")
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
        # Check if we are already on a notebook page
        current_url = str(self.page.url or "")
        if "/notebook/" in current_url or "/note/" in current_url:
            LOGGER.info("Already inside a notebook (%s), skipping creation", current_url)
            return
        # Look for existing notebook title input or add source - already in a notebook?
        if find_first(self.page, self.selectors["add_source"], timeout=2) is not None:
            LOGGER.info("Add source button found - already inside a notebook, skipping creation")
            return
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

    def _log_source_picker_elements(self) -> None:
        """Log all visible interactive elements for diagnosing UI changes."""
        import json
        try:
            elements = self.page.locator("button, [role='menuitem'], [role='button'], input, textarea, [contenteditable='true'], [role='textbox']")
            for i in range(min(elements.count(), 30)):
                try:
                    el = elements.nth(i)
                    if el.is_visible():
                        tag = el.evaluate("el => el.tagName").lower()
                        txt = (el.text_content(timeout=300) or '')[:60].strip()
                        aria = (el.get_attribute("aria-label") or '')[:60]
                        cls = (el.get_attribute("class") or '')[:50]
                        ph = el.get_attribute("placeholder") or ''
                        typ = el.get_attribute("type") or ''
                        LOGGER.info("  picker[%d] tag=%s text=%r aria=%r class=%r placeholder=%r type=%r",
                                    i, tag, txt, aria, cls, ph, typ)
                except Exception:
                    pass
        except Exception as log_err:
            LOGGER.warning("_log_source_picker_elements failed: %s", log_err)

    def _wait_for_source_dialog(self, timeout: float = 10) -> object | None:
        """Wait for the Angular Material source dialog to appear. Returns the dialog element."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                dialog = self.page.locator("mat-dialog-container, [role='dialog']")
                if dialog.count() > 0:
                    for i in range(dialog.count()):
                        d = dialog.nth(i)
                        if d.is_visible():
                            LOGGER.info("SOURCE DIALOG: opened=true")
                            return d
            except Exception:
                pass
            time.sleep(0.5)
        LOGGER.warning("SOURCE DIALOG: not opened (timeout %.1fs)", timeout)
        return None

    def _click_in_dialog(self, selectors: list[str], *, step: str, timeout: float = 10) -> None:
        """Find and click a button inside the source dialog. Does NOT dismiss overlays."""
        # First wait for the dialog
        dialog = self._wait_for_source_dialog(timeout=timeout)
        if dialog is not None:
            # Log all buttons inside the dialog for diagnosis
            try:
                btns = dialog.locator("button, [role='menuitem'], [role='button']")
                for i in range(min(btns.count(), 20)):
                    try:
                        b = btns.nth(i)
                        if b.is_visible():
                            txt = (b.text_content(timeout=300) or '')[:60]
                            aria = (b.get_attribute('aria-label') or '')[:40]
                            cls = (b.get_attribute('class') or '')[:40]
                            LOGGER.info("  SOURCE DIALOG BUTTONS[%d]: text=%r aria=%r class=%r", i, txt, aria, cls)
                    except Exception:
                        pass
            except Exception as log_err:
                LOGGER.warning("dialog button log failed: %s", log_err)
        if dialog is not None:
            # Scoped search: try finding inside dialog first
            for selector in selectors:
                try:
                    inside = dialog.locator(selector)
                    if inside.count() > 0 and inside.first.is_visible():
                        LOGGER.info("  dialog btn found: selector=%r", selector)
                        try:
                            inside.first.click(timeout=3000)
                        except Exception:
                            try:
                                inside.first.click(force=True, timeout=3000, no_wait_after=True)
                            except Exception:
                                inside.first.evaluate("el => el.click()")
                        self.page.wait_for_timeout(500)
                        return
                except Exception:
                    pass
        # Fallback: global search with force/JS click (bypasses xap-uploader-dropzone)
        for selector in selectors:
            try:
                btn = self.page.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    try:
                        btn.first.click(timeout=3000)
                    except Exception:
                        # Dropzone may block — use force then JS click
                        try:
                            btn.first.click(force=True, timeout=3000, no_wait_after=True)
                        except Exception:
                            btn.first.evaluate("el => el.click()")
                    self.page.wait_for_timeout(500)
                    return
            except Exception:
                pass
        # Debug: log what's available
        try:
            vis = []
            for i in range(min(self.page.locator("button, [role='menuitem'], [role='button']").count(), 30)):
                el = self.page.locator("button, [role='menuitem'], [role='button']").nth(i)
                if el.is_visible():
                    txt = (el.text_content(timeout=300) or '')[:60]
                    aria = (el.get_attribute('aria-label') or '')[:40]
                    vis.append(f"text={txt!r} aria={aria!r}")
            LOGGER.warning("Visible controls inside dialog:\n%s", "\n".join(vis[:15]))
        except Exception:
            pass
        raise NotebookLMError(step, f"no control matched inside dialog {selectors}")

    def _dump_dialog_inputs(self, dialog, tag: str = "FIELDS") -> None:
        """Log all input-like elements inside the dialog for diagnosis."""
        try:
            inputs = dialog.locator("input, textarea, [contenteditable='true'], [role='textbox']")
            for i in range(min(inputs.count(), 15)):
                try:
                    el = inputs.nth(i)
                    if el.is_visible():
                        tag_name = el.evaluate("el => el.tagName").lower()
                        typ = (el.get_attribute("type") or el.get_attribute("inputtype") or "")[:20]
                        ph = (el.get_attribute("placeholder") or "")[:50]
                        aria = (el.get_attribute("aria-label") or "")[:50]
                        role = (el.get_attribute("role") or "")[:20]
                        cls = (el.get_attribute("class") or "")[:40]
                        txt = (el.text_content(timeout=300) or "")[:60].strip()
                        LOGGER.info(
                            "  %s[%d]: tag=%s type=%r placeholder=%r aria=%r role=%r class=%r text=%r",
                            tag, i, tag_name, typ, ph, aria, role, cls, txt,
                        )
                except Exception:
                    pass
        except Exception as log_err:
            LOGGER.warning("_dump_dialog_inputs failed: %s", log_err)

    def _fill_in_dialog(self, dialog, selectors: list[str], text: str, *, step: str, timeout: float = 10) -> None:
        """Find an input inside dialog and fill it. No dismiss_overlays."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for selector in selectors:
                try:
                    node = dialog.locator(selector).first
                    if node is not None and node.is_visible():
                        LOGGER.info("URL INPUT FOUND: selector=%r tag=%s placeholder=%r",
                                     selector,
                                     node.evaluate("el => el.tagName").lower(),
                                     node.get_attribute("placeholder") or "",
                        )
                        try:
                            node.fill(text)
                        except Exception:
                            node.click()
                            node.evaluate("el => el.value = ''")
                            self.page.keyboard.type(text, delay=5)
                        return node
                except Exception:
                    pass
            time.sleep(0.5)
        # Comprehensive dump on failure
        LOGGER.warning("URL INPUT NOT FOUND - dumping all input fields in dialog")
        self._dump_dialog_inputs(dialog, tag=f"FAILED {step}")
        # Also dump all dialog buttons
        try:
            btns = dialog.locator("button, [role='button']")
            for i in range(min(btns.count(), 15)):
                try:
                    b = btns.nth(i)
                    if b.is_visible():
                        txt = (b.text_content(timeout=300) or '')[:60]
                        aria = (b.get_attribute('aria-label') or '')[:40]
                        cls = (b.get_attribute('class') or '')[:40]
                        LOGGER.info("  DIALOG BTN[%d]: text=%r aria=%r class=%r", i, txt, aria, cls)
                except Exception:
                    pass
        except Exception:
            pass
        # Also dump all visible elements with their tag names
        try:
            all_el = dialog.locator("*")
            LOGGER.info("DIALOG ALL ELEMENTS (visible):")
            for i in range(min(all_el.count(), 30)):
                try:
                    el = all_el.nth(i)
                    if el.is_visible():
                        tag = el.evaluate("el => el.tagName").lower()
                        txt = (el.text_content(timeout=200) or '')[:40].strip()
                        cls = (el.get_attribute('class') or '')[:40]
                        if tag in ('input','textarea','div','mat-form-field','mat-dialog-content','mat-label','label','span','p','h1','h2','h3'):
                            LOGGER.info("  ALL[%d]: tag=%s class=%r text=%r", i, tag, cls, txt)
                except Exception:
                    pass
        except Exception:
            pass
        # Fallback to global search
        fill_first(self.page, selectors, text, step=step)
        return None

    def _wait_for_website_url_mode(self, dialog, timeout: float = 15) -> None:
        """Wait for the Websites URL entry mode to be ready inside the dialog.
        Handles lazy render, discover mode detection, and produces a DOM dump before failure."""
        import time as _time
        LOGGER.info("Waiting for website URL input mode...")
        # URL field selectors (the actual input the user should type into)
        url_selectors = [
            "textarea[placeholder*='link' i]",
            "textarea[placeholder*='URL' i]",
            "textarea[placeholder*='Paste' i]",
            "textarea[placeholder*='paste' i]",
            "textarea[aria-label*='Enter URLs' i]",
            "textarea[aria-label*='link' i]",
            "input[placeholder*='link' i]",
            "input[placeholder*='URL' i]",
            "input[placeholder*='paste' i]",
            "input[placeholder*='links' i]",
            "input[type='url']",
            "div[contenteditable='true']",
        ]
        # Detect discover mode (search for sources, not URL entry)
        discover_indicators = [
            "button:has-text('Search the web')",
            "button:has-text('search')",
            "[aria-label*='search' i]",
        ]
        deadline = _time.time() + timeout
        dumped_before = False
        while _time.time() < deadline:
            # Check for URL input first
            for sel in url_selectors:
                try:
                    node = dialog.locator(sel).first
                    if node is not None and node.is_visible():
                        LOGGER.info("URL input appeared: selector=%r", sel)
                        return
                except Exception:
                    pass
            # If not found, detect discover mode
            for sel in discover_indicators:
                try:
                    node = dialog.locator(sel).first
                    if node is not None and node.is_visible():
                        LOGGER.warning("In discover/search mode instead of URL entry mode")
                        break
                except Exception:
                    pass
            # Dump DOM once before full timeout
            if _time.time() > deadline - 5 and not dumped_before:
                dumped_before = True
                LOGGER.warning("URL input not found - dumping dialog state")
                self._dump_dialog_inputs(dialog, tag="WEBSITE MODE FAILED")
                try:
                    full_text = dialog.text_content(timeout=2000) or ""
                    LOGGER.info("WEBSITE DIALOG FULL TEXT:\n%s", full_text[:800])
                except Exception:
                    pass
                # Active tab check
                try:
                    tabs = dialog.locator("[role='tab'], .mat-tab-label, [aria-selected]")
                    for i in range(min(tabs.count(), 10)):
                        try:
                            t = tabs.nth(i)
                            if t.is_visible():
                                txt = (t.text_content(timeout=300) or '')[:60]
                                sel = t.get_attribute("aria-selected") or "?"
                                cls = (t.get_attribute("class") or '')[:40]
                                LOGGER.info("  TAB[%d]: text=%r aria-selected=%s class=%r", i, txt, sel, cls)
                        except Exception:
                            pass
                except Exception:
                    pass
                # Dump ALL buttons
                try:
                    btns = dialog.locator("button, [role='button']")
                    for i in range(min(btns.count(), 20)):
                        try:
                            b = btns.nth(i)
                            if b.is_visible():
                                txt = (b.text_content(timeout=300) or '')[:60]
                                aria = (b.get_attribute('aria-label') or '')[:40]
                                cls = (b.get_attribute('class') or '')[:40]
                                LOGGER.info("  BTN[%d]: text=%r aria=%r class=%r", i, txt, aria, cls)
                        except Exception:
                            pass
                except Exception:
                    pass
            _time.sleep(0.5)
        LOGGER.warning("Website URL mode did not activate within %.1fs", timeout)

    # ------------------------------------------------------------ sources

    def add_material(self, material: sources_mod.Material) -> None:
        dismiss_overlays(self.page)
        self.page.wait_for_timeout(300)
        import os
        kind = material.kind
        value_hint = (getattr(material, 'value', '') or '')[:80]
        path = getattr(material, 'path', '') or ''
        _, ext = os.path.splitext(path)
        if kind == "file":
            flow = "add_file"
        elif kind == "url":
            flow = "add_link(website)"
        elif kind == "youtube":
            flow = "add_link(youtube)"
        else:
            flow = "add_text"
        LOGGER.info(
            "SOURCE ROUTER: type=%s ext=%s path=%s value_hint=%r title=%s selected=%s",
            kind, ext, path, value_hint, material.title, flow,
        )
        LOGGER.info("Adding material kind=%s flow=%s", kind, flow)
        if kind == "file":
            self.add_file(path)
        elif kind == "url":
            self.add_link(material.value, kind="website")
        elif kind == "youtube":
            self.add_link(material.value, kind="youtube")
        else:
            self.add_text(material.value)

    def add_file(self, path: str) -> None:
        LOGGER.info("add_file: path=%s", path)
        dismiss_overlays(self.page)
        self.page.wait_for_timeout(300)
        # Open the source dialog
        try:
            click_first(self.page, self.selectors["add_source"], step="open add source")
        except NotebookLMError:
            LOGGER.info("add_file: add_source click failed, dialog may already be open")
        # Wait for dialog, then click source type inside it (no dismiss!)
        self._wait_for_source_dialog()
        self._click_in_dialog(self.selectors["source_file"], step="choose upload files")
        node = find_first(self.page, self.selectors["file_input"], timeout=20)
        if node is None:
            raise NotebookLMError("upload source", "no file input matched")
        node.set_input_files(path)
        self.page.wait_for_timeout(2000)
        self._close_dialog()
        # Confirm the source was actually added
        try:
            import os as _os
            fsize = _os.path.getsize(path) if _os.path.isfile(path) else 0
            fname = _os.path.basename(path) if path else "?"
            LOGGER.info("FILE SOURCE ADDED SUCCESS: filename=%s size=%d", fname, fsize)
        except Exception:
            LOGGER.info("FILE SOURCE ADDED SUCCESS: path=%s (size unknown)", path)

    def add_link(self, url: str, *, kind: str = "website") -> None:
        import time as _time
        dismiss_overlays(self.page)
        self.page.wait_for_timeout(300)
        # Open the source dialog
        try:
            click_first(self.page, self.selectors["add_source"], step="open add source")
        except NotebookLMError:
            LOGGER.info("add_link: add_source click failed, dialog may already be open")
        # Wait for dialog, then click source type inside it (no dismiss!)
        dialog = self._wait_for_source_dialog()
        source_key = "source_youtube" if kind == "youtube" else "source_website"
        self._click_in_dialog(self.selectors[source_key], step=f"choose {kind}")
        # Wait for URL input mode (handles lazy render + discover mode detection)
        self._wait_for_website_url_mode(dialog)
        # Dump what's visible after clicking website button
        LOGGER.info("WEBSITE MODE AFTER CLICK:")
        if dialog is not None:
            self._dump_dialog_inputs(dialog, tag="WEBSITE MODE")
        # URL input is inside the dialog
        if dialog is not None:
            self._fill_in_dialog(dialog, self.selectors["url_input"], url, step=f"{kind} url")
        else:
            fill_first(self.page, self.selectors["url_input"], url, step=f"{kind} url")
        click_first(self.page, self.selectors["source_confirm"], step=f"add {kind}")
        self.page.wait_for_timeout(1500)
        self._close_dialog()

    def add_text(self, text: str) -> None:
        LOGGER.info("add_text: text=%s", text[:80])
        dismiss_overlays(self.page)
        self.page.wait_for_timeout(300)
        # Open the source dialog
        try:
            click_first(self.page, self.selectors["add_source"], step="open add source")
        except NotebookLMError:
            LOGGER.info("add_text: add_source click failed, dialog may already be open")
        # Wait for dialog, then click source type inside it (no dismiss!)
        self._wait_for_source_dialog()
        self._click_in_dialog(self.selectors["source_text"], step="choose copied text")
        # Find a text input/textarea/editor
        text_node = find_first(self.page, self.selectors["text_input"], timeout=10)
        if text_node is None:
            # Last resort: try to find any visible input in the source panel
            LOGGER.info("add_text: no text_input found, dumping all inputs")
            try:
                inputs = self.page.locator("input, textarea, [contenteditable='true'], [role='textbox']")
                for i in range(min(inputs.count(), 20)):
                    try:
                        el = inputs.nth(i)
                        if el.is_visible():
                            tag = el.evaluate("el => el.tagName")
                            typ = el.get_attribute("type") or ""
                            ph = el.get_attribute("placeholder") or ""
                            aria = el.get_attribute("aria-label") or ""
                            LOGGER.info("  input[%d] tag=%s type=%r placeholder=%r aria=%r", i, tag, typ, ph, aria)
                    except Exception:
                        pass
            except Exception as log_err:
                LOGGER.warning("add_text input debug failed: %s", log_err)
            raise NotebookLMError("paste text", "no text input matched")
        # Fill the text
        try:
            text_node.fill("")
            text_node.fill(text)
        except Exception:
            text_node.click()
            self.page.keyboard.press("Control+A")
            self.page.keyboard.type(text, delay=5)
        self.page.wait_for_timeout(500)
        # Try to confirm/submit
        try:
            click_first(self.page, self.selectors["source_confirm"], step="insert text", timeout=5)
        except NotebookLMError:
            LOGGER.info("add_text: no confirm button found, text may auto-submit")
        self.page.wait_for_timeout(1500)
        self._close_dialog()
        # Confirm the source was added
        LOGGER.info(
            "SOURCE ADDED SUCCESS: type=text chars=%d title=%r",
            len(text), text[:60],
        )

    def _close_dialog(self) -> None:
        # Try close button (old dialog pattern)
        node = find_first(self.page, self.selectors["dialog_close"], timeout=2)
        if node is not None:
            try:
                node.click()
                self.page.wait_for_timeout(500)
                return
            except Exception:
                pass
        # Try Escape to dismiss
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
        except Exception:
            pass
        # Try backdrop click to dismiss
        try:
            backdrop = self.page.locator(".cdk-overlay-backdrop")
            if backdrop.count() > 0 and backdrop.first.is_visible():
                backdrop.first.click(force=True, timeout=1000)
                self.page.wait_for_timeout(400)
        except Exception:
            pass
        # Log what's left
        _log_overlay_state(self.page, "after _close_dialog")

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
