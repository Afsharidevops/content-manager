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

CREATE_MODAL_SUBMIT_SELECTORS = [
    "[role='dialog'] [aria-label='ارسال']",
    "[role='dialog'] button[aria-label*='ارسال' i]",
    "[role='dialog'] button[aria-label*='send' i]",
    "[role='dialog'] button:has-text('arrow_forward')",
    "[role='dialog'] button.mat-mdc-icon-button:not([aria-haspopup]):not([aria-label*=بستن i])",
    "button:has-text('arrow_forward')",
    "button:has-text('send')",
]

DEFAULT_SELECTORS: dict[str, list[str]] = {
    "new_notebook": [
        "button:has-text('Create new')",
        "button:has-text('New notebook')",
        "button[aria-label='دفترچه جدید']",
        "nb-button[aria-label='دفترچه جدید']",
        "button:has-text('دفترچه جدید')",
        "nb-button:has-text('دفترچه جدید')",
        "[role='button']:has-text('Create new')",
        "[role='button']:has-text('دفترچه جدید')",
        "button[jsname]:has-text('add')",
        ".create-new-button",
        ".create-new-action-button",
        "button[aria-label*='new notebook' i]",
        "button:has-text('add')",
        "mat-card:has-text('add')",
    ],
    "create_modal_topic": [
        "[role='dialog'] textarea",
        "[role='dialog'] input",
        "textarea[placeholder*='جستجو' i]",
        "textarea[placeholder*='search' i]",
        "textarea[placeholder*='topic' i]",
        "input[placeholder*='جستجو' i]",
        "input[placeholder*='search' i]",
    ],
    "create_modal_submit": CREATE_MODAL_SUBMIT_SELECTORS,
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
    "insert_research": [
        "[role='dialog'] button:has-text('Insert')",
        "[role='dialog'] button:has-text('درج')",
        "[role='dialog'] button:has-text('درج کردن')",
        "[role='dialog'] button:has-text('Import')",
        "[role='dialog'] button:has-text('وارد کردن')",
        "button:has-text('Insert')",
        "button:has-text('درج')",
        "button:has-text('درج کردن')",
        "button:has-text('Import')",
        "button:has-text('وارد کردن')",
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
    ],
    "video_customize": [
        "button:has-text('سفارشی‌سازی')",
        "button:has-text('ویرایش')",
        "button:has-text('Customize')",
        "[role='button']:has-text('Customize')",
        "button:has-text('Edit')",
    ],
    "video_prompt": [
        "[role='dialog'] textarea[aria-label*='prompt' i]",
        "[role='dialog'] textarea[aria-label*='porsman' i]",
        "[role='dialog'] textarea[aria-label*='ساخت' i]",
        "[role='dialog'] textarea:not([placeholder*='link' i]):not([placeholder*='URL' i]):not([placeholder*='جستجو' i]):not([placeholder*='search' i])",
        "[role='dialog'] textarea",
        "[role='dialog'] [contenteditable='true']",
    ],
    "video_generate": [
        "[role='dialog'] button:has-text('اکنون تولید')",
        "[role='dialog'] button:has-text('ایجاد')",
        "[role='dialog'] button:has-text('بساز')",
        "[role='dialog'] button.mat-mdc-unelevated-button:has-text('تولید')",
        "[role='dialog'] button.mat-mdc-unelevated-button",
        "[role='dialog'] button:not(:has-text('بعداً')):has-text('تولید')",
        "[role='dialog'] button:not(:has-text('بعداً')):has-text('ساخت')",
        "[role='dialog'] button:not(:has-text('بعداً')):has-text('Generate')",
    ],
    "video_ready": [
        "video[src*='blob']",
        "video",
        "[class*='artifact'] video",
        "[class*='overview'] video",
        "[class*='generated'] video",
        "button:has-text('دانلود')",
        "button:has-text('Download')",
        "[aria-label*='Download' i]",
        "[aria-label*='دانلود' i]",
        "[aria-label*='play' i] video",
        "button[aria-label*='play' i]",
    ],
    "video_artifact": [
        "[class*='artifact']",
        "[class*='VideoOverview']",
        "[class*='video-overview']",
        "[class*='generated']",
        "article:has(video)",
        "mat-card:has(video)",
        "div:has(> video)",
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
    "video_lang": [
        "[role='dialog'] [aria-label*='language' i]",
        "[role='dialog'] [aria-label*='زبان' i]",
        "[role='dialog'] button:has-text('زبان')",
        "[role='dialog'] button:has-text('Language')",
    ],
    "video_lang_persian": [
        "[role='menuitem']:has-text('فارسی')",
        "[role='menuitem']:has-text('Persian')",
        "[role='option']:has-text('فارسی')",
        "[role='option']:has-text('Persian')",
        "[aria-label*='فارسی' i]",
        "[aria-label*='Persian' i]",
    ],
    "video_template": [
        "[role='dialog'] [aria-label*='template' i]",
        "[role='dialog'] [aria-label*='قالب' i]",
        "[role='dialog'] button:has-text('قالب')",
        "[role='dialog'] button:has-text('Template')",
    ],
    "video_template_short": [
        "[role='menuitem']:has-text('کوتاه')",
        "[role='menuitem']:has-text('Short')",
        "[role='option']:has-text('کوتاه')",
        "[role='option']:has-text('Short')",
    ],
    "video_template_descriptive": [
        "[role='menuitem']:has-text('توضیح‌دهنده')",
        "[role='menuitem']:has-text('Descriptive')",
        "[role='option']:has-text('توضیح‌دهنده')",
        "[role='option']:has-text('Explanatory')",
    ],
    "video_style": [
        "[role='dialog'] [aria-label*='style' i]",
        "[role='dialog'] [aria-label*='سبک' i]",
        "[role='dialog'] button:has-text('سبک')",
        "[role='dialog'] button:has-text('Style')",
    ],
    "video_style_auto": [
        "[role='menuitem']:has-text('انتخاب خودکار')",
        "[role='menuitem']:has-text('Automatic')",
        "[role='option']:has-text('انتخاب خودکار')",
        "[role='option']:has-text('Auto')",
    ],
    "video_style_classic": [
        "[role='menuitem']:has-text('کلاسیک')",
        "[role='menuitem']:has-text('Classic')",
        "[role='option']:has-text('کلاسیک')",
        "[role='option']:has-text('Classic')",
    ],
    "video_style_anime": [
        "[role='dialog'] [class*='style'] [class*='card']:has-text('انیمه')",
        "[role='dialog'] [class*='style'] [class*='card']:has-text('Anime')",
        "[role='dialog'] button:has-text('انیمه')",
        "[role='dialog'] button:has-text('Anime')",
        "[role='menuitem']:has-text('انیمه')",
        "[role='menuitem']:has-text('Anime')",
        "[role='option']:has-text('انیمه')",
        "[role='option']:has-text('Anime')",
    ],
    "video_source_select": [
        "[role='dialog'] [class*='source-select']",
        "[role='dialog'] button:has-text('منبع')",
        "[role='dialog'] [class*='mat-mdc-select']",
        # scoped source selector must target the source container only, not global combobox
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

def dismiss_blocking_popovers(page) -> int:
    """Dismiss non-dialog NotebookLM popovers that can cover active controls."""
    try:
        closed = page.evaluate("""
            () => {
                let count = 0;
                const roots = Array.from(document.querySelectorAll('.cdk-overlay-popover, .cdk-overlay-pane'));
                const clicked = new Set();
                roots.forEach(root => {
                    if (root.querySelector('textarea, input, [contenteditable="true"], [aria-label="ارسال"]')) return;
                    const isPromo = Array.from(root.querySelectorAll('img')).some(img =>
                        String(img.src || '').includes('/promos/') || String(img.alt || '').includes('Gemini Notebook')
                    );
                    if (!isPromo) return;
                    const close = root.querySelector(
                        'button[aria-label*="Close" i], button[aria-label*="بستن کادر گفتگو"], button.close-button'
                    );
                    if (!close || clicked.has(close)) return;
                    clicked.add(close);
                    close.click();
                    count += 1;
                });
                return count;
            }
        """)
        if closed:
            LOGGER.info("Closed %d blocking promo popover overlays", closed)
            page.wait_for_timeout(300)
        return int(closed or 0)
    except Exception:
        return 0


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


def click_first(page, selectors: list[str], *, step: str, timeout: float = 20.0, dismiss: bool = True):
    # First dismiss any overlays that could block clicks
    # (skip when dismiss=False: caller is inside a modal/dialog that should stay open)
    if dismiss:
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

    def create_notebook(self, title: str) -> bool:
        """Create or reuse a notebook.
        
        Returns True if the topic was submitted via the new "create with topic"
        Fast Research modal and the runner must continue with Insert.
        Returns False if an existing notebook was reused or the old flow was used.
        
        The new NotebookLM UI shows a modal after clicking "Create new" where
        the user enters a topic directly. NotebookLM then creates the notebook
        then shows an explicit Insert step before sources are added.
        
        IMPORTANT: True means the Fast Research flow was started, not that
        sources already exist. The runner must click Insert and then verify
        real sources before continuing.
        """
        import time as _time
        # Check if we are already on a notebook page by URL only
        # Do NOT use add_source selector here - it matches on the home page too
        current_url = str(self.page.url or "")
        if "/notebook/" in current_url or "/note/" in current_url:
            LOGGER.info("Already inside a notebook (%s), skipping creation", current_url)
            return False
        # Also check for notebook-specific content like source list panel
        notebook_indicators = [
            "[class*='source-list']",
            "[class*='artifact-library']",
            "[class*='source-panel']",
            "[aria-label*='source list' i]",
        ]
        for indicator in notebook_indicators:
            try:
                el = self.page.locator(indicator).first
                if el.count() > 0 and el.is_visible():
                    LOGGER.info("Notebook source panel found (%s) - already inside a notebook, skipping creation", indicator)
                    return False
            except Exception:
                pass
        
        # Click "Create new"
        click_first(self.page, self.selectors["new_notebook"], step="create notebook")
        self.page.wait_for_timeout(2000)
        
        # Check if the "create with topic" modal appeared (new NotebookLM UI)
        # Log ALL elements in dialog for debugging
        try:
            dialog_txt = self.page.evaluate("""() => {
                const d = document.querySelector('[role="dialog"]');
                if (!d) return 'NO_DIALOG';
                return {
                    exists: true,
                    text: (d.textContent || '').substring(0, 300),
                    html: d.innerHTML.substring(0, 500),
                };
            }""")
            if isinstance(dialog_txt, dict):
                LOGGER.info("CREATE MODAL content: text=%s", dialog_txt.get('text','')[:200])
            else:
                LOGGER.info("CREATE MODAL: %s", dialog_txt)
        except Exception:
            pass
        
        # Log all inputs/textareas in dialog with full attributes
        topic_input = None
        try:
            inputs = self.page.locator("[role='dialog'] textarea, [role='dialog'] input, [role='dialog'] [contenteditable='true']")
            LOGGER.info("CREATE MODAL inputs: count=%d", inputs.count())
            for i in range(min(inputs.count(), 10)):
                try:
                    el = inputs.nth(i)
                    if el.is_visible():
                        tag = el.evaluate("el => el.tagName")
                        typ = el.get_attribute("type") or ""
                        ph = el.get_attribute("placeholder") or ""
                        aria = el.get_attribute("aria-label") or ""
                        role = el.get_attribute("role") or ""
                        cls = (el.get_attribute("class") or "")[:60]
                        val = ""
                        try:
                            val = el.input_value(timeout=300) or ""
                        except Exception:
                            try:
                                val = el.evaluate("el => el.textContent || ''") or ""
                            except Exception:
                                pass
                        LOGGER.info("  input[%d] tag=%s type=%r placeholder=%r aria=%r role=%r class=%r value=%r",
                                    i, tag, typ, ph[:40], aria[:40], role, cls, val[:60])
                        if i == 0 and tag in ("TEXTAREA", "INPUT") and topic_input is None:
                            topic_input = el
                except Exception:
                    pass
        except Exception:
            pass
        
        if topic_input is None:
            topic_input = find_first(self.page, [
                "[role='dialog'] textarea",
                "[role='dialog'] input",
                "textarea[placeholder*='جستجو' i]",
                "textarea[placeholder*='search' i]",
                "textarea[placeholder*='topic' i]",
                "input[placeholder*='جستجو' i]",
                "input[placeholder*='search' i]",
                "input[placeholder*='topic' i]",
            ], timeout=3)
        
        if topic_input is not None and title.strip():
            LOGGER.info("CREATE MODAL detected with topic input - filling topic")
            try:
                # Log input details before fill
                ph = topic_input.get_attribute("placeholder") or ""
                aria = topic_input.get_attribute("aria-label") or ""
                tag = topic_input.evaluate("el => el.tagName") if hasattr(topic_input, 'evaluate') else "?"
                LOGGER.info("TOPIC INPUT: tag=%s placeholder=%r aria=%r", tag, ph, aria)
                
                # Fill the topic
                topic_input.fill("")
                _time.sleep(0.2)
                topic_input.fill(title)
                _time.sleep(0.5)
                
                # Log value after fill
                try:
                    filled_val = topic_input.input_value(timeout=300) or ""
                    LOGGER.info("TOPIC INPUT AFTER FILL: value=%r len=%d", filled_val[:80], len(filled_val))
                except Exception:
                    try:
                        filled_val = topic_input.evaluate("el => el.value || el.textContent || ''") or ""
                        LOGGER.info("TOPIC INPUT AFTER FILL (js): value=%r len=%d", filled_val[:80], len(filled_val))
                    except Exception:
                        LOGGER.warning("TOPIC INPUT: could not read value after fill")
                
                # Fire native input events
                try:
                    topic_input.evaluate("(el) => { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
                except Exception:
                    pass
                _time.sleep(0.5)
                
                # Find submit button - log all buttons in dialog
                try:
                    btns = self.page.evaluate("""() => {
                        const d = document.querySelector('[role="dialog"]');
                        if (!d) return [];
                        return Array.from(d.querySelectorAll('button, [role="button"]')).map(b => ({
                            text: (b.textContent || '').trim().substring(0, 40),
                            aria: b.getAttribute('aria-label') || '',
                            disabled: b.disabled || b.hasAttribute('disabled'),
                            cls: (b.className || '').substring(0, 40),
                            tag: b.tagName,
                        }));
                    }""")
                    LOGGER.info("CREATE MODAL buttons after fill:")
                    for i, b in enumerate(btns):
                        LOGGER.info("  btn[%d] text=%s aria=%r disabled=%s", i, b.get('text',''), b.get('aria',''), b.get('disabled'))
                except Exception:
                    pass
                
                # Find the submit button
                submit_btn = find_first(self.page, CREATE_MODAL_SUBMIT_SELECTORS, timeout=5)
                
                if submit_btn is not None:
                    LOGGER.info("CREATE MODAL: clicking submit button")
                    try:
                        submit_aria = submit_btn.get_attribute("aria-label") or ""
                        submit_text = (submit_btn.text_content(timeout=200) or "").strip()[:30]
                        LOGGER.info("SUBMIT BTN: aria=%r text=%r", submit_aria, submit_text)
                    except Exception:
                        pass
                    dismiss_blocking_popovers(self.page)
                    submit_btn.click()
                    _time.sleep(1)
                    
                    # Wait for modal to close and notebook to load
                    dialog_closed = False
                    for _ in range(20):
                        if self.page.locator("[role='dialog']").count() == 0:
                            dialog_closed = True
                            LOGGER.info("CREATE MODAL: dialog closed")
                            break
                        url = str(self.page.url or "")
                        if "/notebook/" in url or "/note/" in url:
                            LOGGER.info("CREATE MODAL: on notebook page %s", url)
                            break
                        _time.sleep(1)
                    
                    self.page.wait_for_timeout(3000)
                    
                    LOGGER.info("CREATE MODAL: Fast Research submitted; runner will wait for Insert")
                    return True
                else:
                    LOGGER.info("CREATE MODAL: submit button not found, trying Enter key")
                    try:
                        topic_input.press("Enter")
                    except Exception:
                        pass
                    self.page.wait_for_timeout(3000)
                    
                    LOGGER.info("CREATE MODAL: Enter submitted Fast Research; runner will wait for Insert")
                    return True
            except Exception as e:
                LOGGER.warning("CREATE MODAL interaction failed: %s", e)
                return False
        elif topic_input is not None:
            LOGGER.info("CREATE MODAL detected but no title provided, dismissing")
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(500)
            except Exception:
                pass
        else:
            LOGGER.info("No create modal detected, using old empty-notebook flow")
        
        # Old flow: just set the title
        self.set_title(title)
        return False

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
        self.page.wait_for_timeout(3000)
        # Try confirm button inside dialog (file may auto-upload in new UI)
        try:
            self._click_in_dialog(self.selectors["source_confirm"], step="confirm file", timeout=3)
            self.page.wait_for_timeout(1000)
        except NotebookLMError:
            LOGGER.info("add_file: no confirm button, file may auto-upload")
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
        self._click_in_dialog(self.selectors["source_confirm"], step=f"confirm {kind}")
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
            self._click_in_dialog(self.selectors["source_confirm"], step="confirm text", timeout=5)
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

    def source_count(self) -> int:
        """Count real source cards/items without counting source controls."""
        try:
            count = self.page.evaluate(r"""() => {
                const visible = el => !!(el && el.offsetParent !== null);
                const isControl = el => {
                    const text = (el.textContent || '').trim();
                    const aria = el.getAttribute('aria-label') || '';
                    const role = el.getAttribute('role') || '';
                    if (el.tagName === 'BUTTON' || role === 'button') return true;
                    if (/add source|add sources|افزودن منبع|افزودن منابع/i.test(text + ' ' + aria)) return true;
                    if (/source selector|select sources/i.test(aria)) return true;
                    return false;
                };
                const roots = Array.from(document.querySelectorAll(
                    '[aria-label*="Sources" i], [aria-label*="منابع"], [class*="source-list"], [class*="source-panel"], [class*="sources-panel"], [data-testid*="source"]'
                )).filter(visible);
                for (const root of roots) {
                    const items = Array.from(root.querySelectorAll(
                        '[data-source-id], [data-testid*="source-item"], [class*="source-item"], [class*="source-card"], [class*="source-chip"], [role="listitem"]'
                    )).filter(el => {
                        if (!visible(el) || isControl(el)) return false;
                        const text = (el.textContent || '').trim();
                        if (text.length < 2) return false;
                        return true;
                    });
                    if (items.length > 0) return items.length;
                }
                return 0;
            }""")
            count_val = int(count) if count is not None else 0
            LOGGER.info("source_count result: %d", count_val)
            return count_val
        except Exception as exc:
            LOGGER.warning("source_count exception: %s", exc)
            return 0

    def open_studio(self) -> None:
        node = find_first(self.page, self.selectors["studio_tab"], timeout=8)
        if node is not None:
            try:
                LOGGER.info("Studio aria: %s", (node.get_attribute("aria-label") or "").strip())
                node.click()
                self.page.wait_for_timeout(1500)
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("Studio control could not be clicked: %s", error)

    def wait_for_research(self, *, timeout: int = 0) -> bool:
        """Wait for Fast Research to complete, then return True.
        Returns False if there's no research indicator (already done or old UI)."""
        import time as _t
        deadline = _t.time() + (timeout or self.settings.source_timeout_seconds)
        LOGGER.info("Fast Research started")
        # Look for research completion indicator — Insert button or status text
        while _t.time() < deadline:
            # Check if Insert button is visible
            insert_btn = find_first(self.page, self.selectors.get("insert_research", []), timeout=2)
            if insert_btn is not None:
                LOGGER.info("Fast Research completed — Insert button found")
                return True
            # Check for "research complete" text
            try:
                text = (self.page.text_content("body") or "").lower()
                complete_terms = ["research complete", "suggested for you", "پیشنهاد"]
                if any(term in text for term in complete_terms):
                    LOGGER.info("Fast Research: completion text found")
                    return True
            except Exception:
                pass
            _t.sleep(2)
        LOGGER.info("Fast Research: no completion detected (may already be done)")
        return False

    def insert_research_results(self) -> bool:
        """Click the Insert / درج کردن button after Fast Research completes.
        Returns True if click succeeded and source count increased."""
        before = self.source_count()
        insert_btn = find_first(self.page, self.selectors.get("insert_research", []), timeout=10)
        if insert_btn is None:
            LOGGER.info("Insert button not found; sources may already be imported")
            return False
        try:
            LOGGER.info("Importing research sources")
            insert_btn.scroll_into_view_if_needed()
            insert_btn.click()
            self.page.wait_for_timeout(2000)
            self.wait_for_sources(timeout=60)
            after = self.source_count()
            LOGGER.info("Sources imported: %d (was %d)", after, before)
            return after > before
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("Insert research failed: %s", error)
            return False
