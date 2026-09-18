"""Video Overview: submit the prompt, wait for the render, download it."""

from __future__ import annotations

import logging
import os
import subprocess
import time

from app.notebook import (
    NotebookLMError,
    visible_nodes,
    click_first,
    find_first,
)

LOGGER = logging.getLogger("notebooklm.video")


def _dismiss_blocking_notifications(page) -> int:
    """Close notification dialogs that block interaction (NOT source dialogs)."""
    import time as _time
    dismissed = 0
    known_notifications = [
        "اکنون انعطاف", "هم اکنون", "بیشتر استفاده کن", "قابلیت‌های جدید",
        "usage", "upgrade", "limit", "Gemini Notebook",
        "getting started", "what's new", "welcome", "tip",
        "اکنون می‌توانید", "تغییرات جدید",
    ]
    for _ in range(3):
        try:
            dialogs = page.locator("[role='dialog'], .cdk-overlay-pane, .notification-overlay")
            for i in range(min(dialogs.count(), 5)):
                try:
                    d = dialogs.nth(i)
                    if not d.is_visible():
                        continue
                    text = (d.text_content(timeout=300) or "").strip()
                    if not any(n in text for n in known_notifications):
                        continue
                    LOGGER.info("NOTIFICATION DIALOG: %s", text[:120])
                    close = d.locator("button[aria-label*='Close' i], button[aria-label*='بستن' i], [aria-label*='dismiss' i]")
                    if close.count() > 0 and close.first.is_visible():
                        close.first.click(timeout=1000)
                        dismissed += 1
                        _time.sleep(0.5)
                        continue
                    page.keyboard.press("Escape")
                    _time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass
    if dismissed:
        page.wait_for_timeout(800)
        LOGGER.info("Dismissed %d blocking notification(s)", dismissed)
    return dismissed


def _log_dialog_state(page, tag: str = "") -> None:
    """Log the current state of dialogs and overlays."""
    try:
        dialogs = page.locator("[role='dialog']")
        dlg_count = dialogs.count()
        backdrops = page.locator(".cdk-overlay-backdrop").count()
        dlg_text = ""
        if dlg_count > 0:
            try:
                dlg_text = (dialogs.first.text_content(timeout=300) or "")[:200]
            except Exception:
                pass
        LOGGER.info("DIALOG STATE [%s]: dialogs=%d backdrops=%d text=%r", 
                     tag, dlg_count, backdrops, dlg_text[:80] if dlg_text else "")
    except Exception:
        pass


def _log_all_dialog_buttons(page) -> list[dict]:
    """Log ALL buttons inside the active dialog. Returns list of button info dicts."""
    result = []
    try:
        btns = page.evaluate("""() => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return [];
            return Array.from(dialog.querySelectorAll('button, [role="button"], [role="menuitem"], [role="option"]')).map(b => ({
                text: (b.textContent || '').trim().substring(0, 60),
                aria: b.getAttribute('aria-label') || '',
                cls: (b.className || '').substring(0, 60),
                disabled: b.disabled || b.hasAttribute('disabled'),
                role: b.getAttribute('role') || '',
                tag: b.tagName,
            }));
        }""")
        LOGGER.info("DIALOG BUTTONS (%d):", len(btns))
        for i, b in enumerate(btns):
            LOGGER.info("  [%d] text=%s aria=%s role=%s disabled=%s", 
                        i, b.get('text','')[:40], b.get('aria','')[:30], 
                        b.get('role',''), b.get('disabled'))
            result.append(b)
    except Exception as e:
        LOGGER.info("Button dump failed: %s", e)
    return result


def _select_dropdown_option(page, trigger_selectors: list[str], option_selectors: list[str], 
                             step: str) -> bool:
    """Click a dropdown trigger, then select an option from the resulting menu."""
    import time as _time
    try:
        trigger = find_first(page, trigger_selectors, timeout=3)
        if trigger is None:
            LOGGER.info("%s: trigger not found", step)
            return False
        trigger_text = ""
        try:
            trigger_text = (trigger.text_content(timeout=200) or "").strip()[:30]
        except Exception:
            pass
        LOGGER.info("%s: trigger found text=%r", step, trigger_text)
        trigger.click()
        _time.sleep(0.5)
        # Look for option
        option = find_first(page, option_selectors, timeout=3)
        if option is not None:
            option.click()
            _time.sleep(0.3)
            LOGGER.info("%s: option selected", step)
            return True
        else:
            LOGGER.info("%s: option not found", step)
            return False
    except Exception as e:
        LOGGER.info("%s: failed: %s", step, e)
        return False


def _select_video_sources(page, selectors: dict) -> None:
    """Select all available sources in the Video Overview dialog."""
    import time as _time
    _log_dialog_state(page, "before-source-select")
    
    # Method 1: Try to find and click the source selector dropdown
    # In new NotebookLM UI, the dialog has a source dropdown showing "0 منبع" or similar
    try:
        # Look for mat-select or similar dropdown for source selection
        source_selectors = [
            "[role='dialog'] [class*='mat-mdc-select']:has-text('منبع')",
            "[role='dialog'] [class*='mat-mdc-select']",
            "[role='dialog'] [role='combobox']:has-text('منبع')",
            "[role='dialog'] [role='combobox']",
            "[role='dialog'] button:has-text('۰ منبع')",
            "[role='dialog'] button:has-text('0 منبع')",
            "[role='dialog'] button:has-text('منبع')",
            "[role='dialog'] [class*='source']",
        ]
        selector_found = False
        for sel in source_selectors:
            try:
                trigger = page.locator(sel).first
                if trigger.count() > 0 and trigger.is_visible():
                    txt = (trigger.text_content(timeout=200) or "").strip()[:40]
                    LOGGER.info("SOURCE SELECTOR found: sel=%s text=%r", sel, txt)
                    trigger.click()
                    _time.sleep(0.5)
                    selector_found = True
                    break
            except Exception:
                continue
        
        if not selector_found:
            LOGGER.info("No source selector dropdown found - using fallback")
        else:
            # Try to select all sources from the dropdown
            # Look for checkboxes or selectable items
            selectors_to_try = [
                "text='انتخاب همه'",
                "text='Select all'",
                "[role='menuitemcheckbox']",
                "[role='option']",
                "[role='menuitem']",
                "[class*='mat-mdc-option']",
                "[class*='checkbox']",
            ]
            selected_any = False
            for sel in selectors_to_try:
                try:
                    items = page.locator(sel)
                    for i in range(min(items.count(), 10)):
                        try:
                            item = items.nth(i)
                            if item.is_visible():
                                item.click()
                                selected_any = True
                                LOGGER.info("Selected source item %d via %s", i, sel)
                        except Exception:
                            pass
                    if selected_any:
                        break
                except Exception:
                    continue
            
            if selected_any:
                _time.sleep(0.5)
                # Close dropdown by clicking elsewhere or pressing Escape
                page.keyboard.press("Escape")
                _time.sleep(0.3)
            else:
                LOGGER.info("Could not select any source - sources may already be selected")
                page.keyboard.press("Escape")
                _time.sleep(0.3)
    except Exception as e:
        LOGGER.info("Source selection error: %s", e)
    
    _log_dialog_state(page, "after-source-select")
    
    # Method 2: If dialog has no source selector shown and we're still at "0 source",
    # the sources might be auto-selected. Just log the state.
    try:
        src_btn = page.locator("[role='dialog'] [class*='mat-mdc-select']").first
        if src_btn.count() > 0 and src_btn.is_visible():
            txt = (src_btn.text_content(timeout=200) or "").strip()
            LOGGER.info("Source selector current value: %s", txt)
    except Exception:
        pass


def _select_video_language(page, selectors: dict, lang: str = "persian") -> None:
    """Select language in Video Overview dialog."""
    if lang == "persian":
        _select_dropdown_option(
            page,
            selectors.get("video_lang", ["[role='dialog'] button:has-text('Language')"]),
            selectors.get("video_lang_persian", ["[role='menuitem']:has-text('Persian')"]),
            step="select language Persian"
        )
    else:
        LOGGER.info("Language %s selected (or not needed)", lang)


def _select_video_template(page, selectors: dict, template: str = "short") -> None:
    """Select template in Video Overview dialog."""
    if template == "short":
        _select_dropdown_option(
            page,
            selectors.get("video_template", ["[role='dialog'] button:has-text('Template')"]),
            selectors.get("video_template_short", ["[role='menuitem']:has-text('Short')"]),
            step="select template short"
        )
    elif template == "descriptive":
        _select_dropdown_option(
            page,
            selectors.get("video_template", ["[role='dialog'] button:has-text('Template')"]),
            selectors.get("video_template_descriptive", ["[role='menuitem']:has-text('Descriptive')"]),
            step="select template descriptive"
        )


def _select_video_style(page, selectors: dict, style: str = "auto") -> None:
    """Select visual style in Video Overview dialog."""
    if style == "auto":
        _select_dropdown_option(
            page,
            selectors.get("video_style", ["[role='dialog'] button:has-text('Style')"]),
            selectors.get("video_style_auto", ["[role='menuitem']:has-text('Automatic')"]),
            step="select style auto"
        )
    elif style == "classic":
        _select_dropdown_option(
            page,
            selectors.get("video_style", ["[role='dialog'] button:has-text('Style')"]),
            selectors.get("video_style_classic", ["[role='menuitem']:has-text('Classic')"]),
            step="select style classic"
        )


def start_video_overview(page, prompt: str, settings, selectors: dict) -> None:
    """Open the Video Overview composer and submit the rendered prompt."""
    # Start network monitoring
    import json as _json
    
    _video_requests = []
    _video_responses = []
    
    def _on_request(request):
        url = request.url
        if "batchexecute" in url or ("google.com" in url and url.startswith("https://notebook")):
            _video_requests.append({"url": url[:300], "method": request.method})
    
    def _on_response(response):
        url = response.url
        if "batchexecute" in url:
            try:
                body = response.text()[:2000]
                _video_responses.append({"url": url[:300], "status": response.status, "body": body})
            except Exception:
                pass
    
    try:
        page._video_requests = _video_requests
        page._video_responses = _video_responses
        page.on("request", _on_request)
        page.on("response", _on_response)
    except Exception:
        pass
    
    # Dismiss blocking notifications
    _dismiss_blocking_notifications(page)
    
    # Open Video Overview
    click_first(page, selectors["video_overview"], step="open video overview")
    page.wait_for_timeout(2000)
    _dismiss_blocking_notifications(page)
    
    # Wait for dialog to appear
    _found_dialog = False
    for _ in range(5):
        if page.locator("[role='dialog']").count() > 0:
            _found_dialog = True
            break
        page.wait_for_timeout(1000)
        click_first(page, selectors["video_overview"], step="retry video overview")
        page.wait_for_timeout(1000)
    
    if not _found_dialog:
        LOGGER.warning("Video compose dialog did NOT open after clicking Video Overview")
    
    _log_dialog_state(page, "video-dialog-opened")
    
    # Log all dialog buttons
    _log_all_dialog_buttons(page)
    
    # Select sources in the dialog
    _select_video_sources(page, selectors)
    
    # Customize: language, template, style
    _select_video_language(page, selectors, lang="persian")
    
    # Template: get from settings or default to short
    template = getattr(settings, "video_template", "short")
    _select_video_template(page, selectors, template=template)
    
    # Style: only applicable for descriptive template
    style = getattr(settings, "video_style", "auto")
    if template == "descriptive":
        _select_video_style(page, selectors, style=style)
    
    _log_dialog_state(page, "after-customization")
    
    # Dismiss notifications before prompt
    _dismiss_blocking_notifications(page)
    
    # Fill prompt field
    node = find_first(page, selectors["video_prompt"], timeout=15)
    if node is not None:
        try:
            ph = node.get_attribute("placeholder") or ""
            aria = node.get_attribute("aria-label") or ""
            LOGGER.info("VIDEO PROMPT FIELD: placeholder=%r aria=%r", ph, aria)
            
            node.scroll_into_view_if_needed()
            node.click()
            page.wait_for_timeout(300)
            node.fill("")
            page.wait_for_timeout(100)
            node.fill(prompt)
            page.wait_for_timeout(200)
            
            # Fire native events
            try:
                node.evaluate("(el) => { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }")
            except Exception:
                pass
            
            try:
                val = node.input_value(timeout=500)
                LOGGER.info("PROMPT VALUE SET: chars=%d preview=%r", len(val), val[:80])
            except Exception:
                pass
        except Exception as error:
            LOGGER.warning("Video prompt could not be typed: %s", error)
            try:
                node.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.type(prompt, delay=3)
            except Exception as e2:
                LOGGER.warning("Video prompt keyboard fallback also failed: %s", e2)
    else:
        LOGGER.warning("Video prompt field not found; generating with defaults")
    
    # Log dialog state before generating
    _log_dialog_state(page, "before-generate")
    
    # Find and click the Generate button
    _click_generate(page, selectors, settings)


def _click_generate(page, selectors: dict, settings) -> None:
    """Find the actual generate/submit button and click it with multiple strategies."""
    import time as _time
    
    # Log ALL buttons in dialog
    _log_all_dialog_buttons(page)
    
    # Find the generate button - prefer the primary "اکنون تولید کردن" button
    gen_node = find_first(page, selectors["video_generate"], timeout=10)
    
    if gen_node is not None:
        # Verify we're NOT about to click the dismiss button
        try:
            actual_text = (gen_node.text_content(timeout=200) or "").strip()
            if "بعداً" in actual_text or "skip" in actual_text.lower() or "later" in actual_text.lower():
                LOGGER.error("WRONG BUTTON (dismiss): '%s' - trying alternate selector", actual_text[:50])
                fallback_selectors = [s for s in selectors["video_generate"] if "بعداً" not in s]
                gen_node = find_first(page, fallback_selectors, timeout=5)
                if gen_node is not None:
                    actual_text = (gen_node.text_content(timeout=200) or "").strip()
        except Exception:
            pass
        
        # Log button state
        try:
            btn_disabled = gen_node.is_disabled()
            btn_text = gen_node.text_content(timeout=300) or ""
            btn_aria = gen_node.get_attribute("aria-label") or ""
            LOGGER.info("GENERATE BTN BEFORE: disabled=%s text=%r", btn_disabled, btn_text.strip()[:50])
        except Exception:
            pass
        
        # Multiple click strategies
        clicked = False
        for strategy_name, strategy_fn in [
            ("normal click", lambda: gen_node.click(timeout=3000)),
            ("force click", lambda: gen_node.click(force=True, timeout=3000, no_wait_after=True)),
            ("JS MouseEvent", lambda: page.evaluate("(el) => el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}))", gen_node)),
            ("keyboard Enter", lambda: [gen_node.focus(), page.keyboard.press("Enter")]),
        ]:
            try:
                strategy_fn()
                LOGGER.info("GENERATE: %s done", strategy_name)
                clicked = True
                break
            except Exception as exc:
                LOGGER.info("GENERATE: %s failed: %s", strategy_name, exc)
        
        if not clicked:
            LOGGER.warning("GENERATE: all click strategies failed")
        
        page.wait_for_timeout(2000)
    else:
        LOGGER.warning("GENERATE: button not found")
    
    # Capture state after click
    _log_dialog_state(page, "after-generate")
    
    # Log network requests
    _dump_network(page, tag="after-generate")
    
    # Take screenshot
    try:
        ss_path = os.path.join(settings.data_dir, "logs", f"video-after-gen-{int(time.time())}.png")
        page.screenshot(path=ss_path)
        LOGGER.info("SCREENSHOT after generate: %s", ss_path)
    except Exception:
        pass


def _dump_network(page, tag: str = "") -> None:
    """Dump captured network requests/responses."""
    import json as _json
    try:
        reqs = getattr(page, "_video_requests", []) or []
        resps = getattr(page, "_video_responses", []) or []
        rpc_names = set()
        for r in reqs:
            if 'rpcids=' in r.get('url',''):
                idx = r['url'].find('rpcids=')
                rid = r['url'][idx:idx+50]
                rpc_names.add(rid)
        LOGGER.info("NETWORK [%s]: requests=%d responses=%d rpc_ids=%s", 
                     tag, len(reqs), len(resps), list(rpc_names)[:5])
        for r in resps:
            LOGGER.info("RPC RESP [%s]: status=%s body=%s", 
                         tag, r.get("status"), (r.get("body","")[:300]))
    except Exception:
        pass


def _dump_page_state(page, tag: str = "") -> None:
    """Dump page state for debugging."""
    try:
        buttons = page.locator("button, [role='button'], mat-card, video")
        visible = []
        for i in range(min(buttons.count(), 20)):
            try:
                b = buttons.nth(i)
                if b.is_visible():
                    txt = (b.text_content(timeout=200) or "").strip()[:50]
                    aria = (b.get_attribute("aria-label") or "")[:30]
                    if txt or aria:
                        visible.append(f"  [{i}] text={txt!r} aria={aria!r}")
            except Exception:
                pass
        if visible:
            LOGGER.info("PAGE STATE [%s]:\n%s", tag, "\n".join(visible))
        else:
            LOGGER.info("PAGE STATE [%s]: no visible interactive elements", tag)
    except Exception:
        pass


def video_ready(page, selectors: dict) -> bool:
    """True when a rendered video or its download affordance is visible."""
    # Check for video element
    for selector in selectors["video_ready"]:
        for node in visible_nodes(page, selector):
            try:
                tag = node.evaluate("(n) => n.tagName")
                if tag == "VIDEO":
                    src = node.evaluate("(n) => n.currentSrc || n.src || ''")
                    if src:
                        LOGGER.info("VIDEO READY: src=%s", src[:120])
                        return True
                elif tag in ("BUTTON", "A"):
                    LOGGER.info("VIDEO ACTION found: tag=%s text=%s", tag, 
                                (node.text_content(timeout=200) or "")[:40].strip())
                    return True
                else:
                    LOGGER.info("VIDEO READY via %s: tag=%s", selector, tag)
                    return True
            except Exception:
                continue
    
    # Check for non-empty artifact library
    try:
        has_artifact = page.evaluate("""() => {
            const containers = document.querySelectorAll('[class*="artifact-library"]');
            for (const c of containers) {
                if (c.className.includes('empty')) continue;
                const text = c.textContent || '';
                if (text.includes('\\u062e\\u0648\\u0627\\u0647\\u062f \\u0634\\u062f') ||
                    text.includes('saved here')) continue;
                if (c.children.length > 0 && c.offsetParent !== null) {
                    const actionable = c.querySelector('button, video, [role="button"], a, img, [class*="card"]');
                    if (actionable) {
                        LOGGER.info("VIDEO ARTIFACT LIBRARY has content (non-empty)");
                        return true;
                    }
                }
            }
            return false;
        }""")
        if has_artifact:
            return True
    except Exception:
        pass
    
    # Check for video card specifically
    try:
        has_video_card = page.evaluate("""() => {
            // Look for video cards in the main content area
            const cards = document.querySelectorAll('[class*="card"], [class*="overview"], article, [class*="generated"]');
            for (const card of cards) {
                if (!card.offsetParent) continue;
                const text = card.textContent || '';
                if (text.includes('مرور ویدیویی') || text.includes('Video Overview') || text.includes('video')) {
                    const hasPlayBtn = card.querySelector('button[aria-label*="play" i], [class*="play-button"]');
                    if (hasPlayBtn || card.querySelector('video')) {
                        LOGGER.info("VIDEO CARD found: text=%s", text.substring(0, 80));
                        return true;
                    }
                }
            }
            return false;
        }""")
        if has_video_card:
            return True
    except Exception:
        pass
    
    return False


def wait_for_video(page, settings, selectors: dict) -> None:
    """Poll until the Video Overview finishes rendering."""
    start = time.time()
    deadline = start + max(120, settings.video_timeout_seconds)
    last_log = 0.0
    
    while time.time() < deadline:
        if video_ready(page, selectors):
            LOGGER.info("VIDEO GENERATION COMPLETE after %.0fs", time.time() - start)
            return
        
        elapsed = time.time() - start
        now = time.time()
        if now - last_log > 30.0:
            LOGGER.info("WAITING for video: %.0fs elapsed", elapsed)
            _dump_page_state(page, tag="waiting")
            last_log = now
        
        page.wait_for_timeout(max(3, settings.poll_seconds) * 1000)
    
    _dump_network(page, tag="timeout")
    try:
        ss_path = os.path.join(settings.data_dir, "logs", f"video-timeout-{int(time.time())}.png")
        page.screenshot(path=ss_path)
        LOGGER.info("SCREENSHOT at timeout: %s", ss_path)
    except Exception:
        pass
    
    raise NotebookLMError(
        "video generation",
        f"the overview was not ready after {settings.video_timeout_seconds}s",
    )


def download_video(page, target_path: str, settings, selectors: dict) -> str:
    """Download the finished overview to ``target_path`` and return the path."""
    import base64
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    
    page.wait_for_timeout(3000)
    
    _dump_page_state(page, tag="download-start")
    _dump_network(page, tag="download-start")
    
    # Try multiple strategies to get the video
    
    # Strategy 1: Find video element with src
    video_src = page.evaluate("""() => {
        const videos = document.querySelectorAll('video');
        for (const v of videos) {
            const src = v.currentSrc || v.src || '';
            if (src && src.startsWith('blob:')) {
                return {src: src, method: 'blob'};
            }
            if (src) {
                return {src: src, method: 'src'};
            }
        }
        return null;
    }""")
    
    if video_src:
        url = video_src.get('src', '')
        LOGGER.info("VIDEO SOURCE found: %s...", url[:100])
        if url.startswith('blob:'):
            b64data = page.evaluate("""async (url) => {
                try {
                    const r = await fetch(url);
                    if (!r.ok) return 'HTTP:' + r.status;
                    const blob = await r.blob();
                    return await new Promise((res, rej) => {
                        const reader = new FileReader();
                        reader.onload = () => res(reader.result);
                        reader.onerror = () => rej('FileReader error');
                        reader.readAsDataURL(blob);
                    });
                } catch(e) { return 'ERROR:' + e.message; }
            }""", url)
            if b64data and b64data.startswith("data:"):
                _, data = b64data.split(",", 1)
                raw = base64.b64decode(data)
                with open(target_path, "wb") as f:
                    f.write(raw)
                LOGGER.info("VIDEO DOWNLOADED via blob: %d bytes", len(raw))
                _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                return target_path
        else:
            # Direct URL
            b64data = page.evaluate("""async (url) => {
                try {
                    const r = await fetch(url);
                    if (!r.ok) return 'HTTP:' + r.status;
                    const blob = await r.blob();
                    return await new Promise((res, rej) => {
                        const reader = new FileReader();
                        reader.onload = () => res(reader.result);
                        reader.onerror = () => rej('FileReader error');
                        reader.readAsDataURL(blob);
                    });
                } catch(e) { return 'ERROR:' + e.message; }
            }""", url)
            if b64data and b64data.startswith("data:"):
                _, data = b64data.split(",", 1)
                raw = base64.b64decode(data)
                with open(target_path, "wb") as f:
                    f.write(raw)
                LOGGER.info("VIDEO DOWNLOADED via src: %d bytes", len(raw))
                _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                return target_path
    
    # Strategy 2: Click play button on video card to make video appear
    play_selectors = [
        "button[aria-label*='play' i]",
        "[aria-label*='play' i]",
        "[class*='play-button']",
        "[class*='play_arrow']",
        "button:has(svg)",
        "[class*='video-overview'] button",
        "[class*='overview-card'] button",
    ]
    for sel in play_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible():
                LOGGER.info("Play button found: %s", sel)
                btn.click()
                page.wait_for_timeout(3000)
                video_after = page.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) return v.currentSrc || v.src || '';
                    return '';
                }""")
                if video_after:
                    LOGGER.info("VIDEO SRC AFTER PLAY: %s", video_after[:150])
                    b64data = page.evaluate("""async (url) => {
                        try {
                            const r = await fetch(url);
                            if (!r.ok) return 'HTTP:' + r.status;
                            const blob = await r.blob();
                            return await new Promise((res, rej) => {
                                const reader = new FileReader();
                                reader.onload = () => res(reader.result);
                                reader.onerror = () => rej('FileReader error');
                                reader.readAsDataURL(blob);
                            });
                        } catch(e) { return 'ERROR:' + e.message; }
                    }""", video_after)
                    if b64data and b64data.startswith("data:"):
                        _, data = b64data.split(",", 1)
                        raw = base64.b64decode(data)
                        with open(target_path, "wb") as f:
                            f.write(raw)
                        LOGGER.info("VIDEO DOWNLOADED after play: %d bytes", len(raw))
                        _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                        return target_path
                break
        except Exception:
            continue
    
    # Strategy 3: Use the artifact menu for download
    try:
        # Find the artifact and its more menu
        artifact_menu = find_first(page, selectors.get("video_menu", []), timeout=3)
        if artifact_menu is not None:
            artifact_menu.click()
            page.wait_for_timeout(500)
            download_btn = find_first(page, selectors.get("video_download", []), timeout=2)
            if download_btn is not None:
                download_btn.click()
                page.wait_for_timeout(2000)
                # After clicking download, check for video element
                video_after = page.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (v) return v.currentSrc || v.src || '';
                    return '';
                }""")
                if video_after:
                    b64data = page.evaluate("""async (url) => {
                        try {
                            const r = await fetch(url);
                            if (!r.ok) return 'HTTP:' + r.status;
                            const blob = await r.blob();
                            return await new Promise((res, rej) => {
                                const reader = new FileReader();
                                reader.onload = () => res(reader.result);
                                reader.onerror = () => rej('FileReader error');
                                reader.readAsDataURL(blob);
                            });
                        } catch(e) { return 'ERROR:' + e.message; }
                    }""", video_after)
                    if b64data and b64data.startswith("data:"):
                        _, data = b64data.split(",", 1)
                        raw = base64.b64decode(data)
                        with open(target_path, "wb") as f:
                            f.write(raw)
                        LOGGER.info("VIDEO DOWNLOADED via menu: %d bytes", len(raw))
                        _try_trim(target_path, getattr(settings, "trim_last_seconds", 0))
                        return target_path
    except Exception as e:
        LOGGER.info("Artifact menu download failed: %s", e)
    
    # Strategy 4: Deep DOM scan for any video-related elements
    try:
        dom = page.evaluate("""() => {
            const results = [];
            const all = document.querySelectorAll('body *');
            for (const el of all) {
                if (!el.offsetParent) continue;
                const tag = el.tagName.toLowerCase();
                if (['script','style','meta','link','noscript'].includes(tag)) continue;
                const cls = (el.className || '').substring(0, 80);
                const txt = (el.textContent || '').trim().substring(0, 100);
                const aria = el.getAttribute('aria-label') || '';
                if (tag === 'video' || cls.includes('video') || cls.includes('artifact') || 
                    cls.includes('card') || aria.includes('play') || aria.includes('video') ||
                    aria.includes('download') || cls.includes('download')) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        tag: tag, cls: cls, txt: txt.substring(0, 60),
                        aria: aria.substring(0, 40),
                        w: Math.round(rect.width), h: Math.round(rect.height),
                        hasVideo: !!el.querySelector('video') || tag === 'video',
                    });
                }
            }
            return results;
        }""")
        if dom:
            LOGGER.info("DOWNLOAD SCAN: found %d video-related elements:", len(dom))
            for i, d in enumerate(dom[:15]):
                LOGGER.info("  [%d] <%s> cls=%s aria=%s hasV=%s", 
                            i, d.get('tag',''), d.get('cls',''), d.get('aria',''), d.get('hasVideo'))
    except Exception as e:
        LOGGER.info("DOM scan failed: %s", e)
    
    # Take final screenshot
    try:
        ss_path = os.path.join(settings.data_dir, "logs", f"video-dl-fail-{int(time.time())}.png")
        page.screenshot(path=ss_path)
        LOGGER.info("SCREENSHOT at download fail: %s", ss_path)
    except Exception:
        pass
    
    raise NotebookLMError("download video", "could not find or download the generated video")


def _try_trim(path: str, trim_last: int) -> None:
    """Trim last N seconds from video if ffmpeg/ffprobe are available."""
    if trim_last <= 0:
        return
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        total = float(probe.stdout.strip() or 0)
        if total > trim_last + 1:
            _trim_video(path, total - trim_last)
        else:
            LOGGER.info("Video too short (%.1fs) to trim %ds", total, trim_last)
    except Exception as exc:
        LOGGER.warning("Could not probe/trim video: %s", exc)


def _trim_video(path: str, keep: float) -> str | None:
    """Trim a video to ``keep`` seconds from the start using ffmpeg."""
    import subprocess as _sp
    import tempfile as _tf
    try:
        fd, tmp = _tf.mkstemp(suffix=os.path.splitext(path)[1] or ".mp4")
        os.close(fd)
        result = _sp.run(
            ["ffmpeg", "-y", "-i", path, "-t", str(keep),
             "-c", "copy", "-avoid_negative_ts", "make_zero", tmp],
            capture_output=True, timeout=120, text=True,
        )
        if result.returncode != 0:
            LOGGER.warning("trim failed (ffmpeg exit %d): %s", result.returncode, result.stderr[:200])
            os.unlink(tmp)
            return None
        os.replace(tmp, path)
        LOGGER.info("Trimmed %s to %.1fs", path, keep)
        return path
    except FileNotFoundError:
        LOGGER.warning("ffmpeg not available, skip trim")
        return None
    except Exception as exc:
        LOGGER.warning("trim error: %s", exc)
        return None
