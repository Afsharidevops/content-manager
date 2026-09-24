#!/usr/bin/env python3
"""Capture panel screenshots using Playwright + mock API server.

Usage:
    python3 screenshot-demo/capture.py
"""

import asyncio, json, os, pathlib, socket, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "docs-site" / "assets"
OUT.mkdir(parents=True, exist_ok=True)
SCREENSHOT_DIR = OUT / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = "demo-token"
CAPTURE_ONLY = os.environ.get("CAPTURE_ONLY", "").strip()

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}}

CAPTURES = [
    {
        "name": "content-console-overview-v0.6.0",
        "view": "overview",
        "wait": 1200,
        "description": "Operations Center overview showing containers, disk, profiles, endpoints, and media jobs.",
    },
    {
        "name": "content-console-pipeline-state-v0.6.0",
        "view": "state",
        "wait": 1200,
        "description": "Content pipeline with draft queue, console action results, and scheduled routines.",
    },
    {
        "name": "content-console-orchestration-v0.6.0",
        "view": "orchestration",
        "wait": 1500,
        "description": "Multi-agent orchestration runs: completed, awaiting approval, running, and failed.",
    },
    {
        "name": "content-console-video-v0.6.0",
        "view": "storyboard",
        "wait": 1500,
        "description": "Video Studio with storyboard planning, timeline editor, job queue and Media Studio driver status.",
    },
    {
        "name": "content-console-knowledge-v0.6.0",
        "view": "knowledge",
        "wait": 1500,
        "description": "Knowledge center with bases, indexed chunks, document ingestion and retrieval test.",
    },
    {
        "name": "smart-router-console-v0.6.0",
        "view": "hermes",
        "wait": 1500,
        "description": "Smart Router view with agents, routing profiles, models, and content agent bindings.",
    },
]

CLEAN_NAMES = {
    "content-console-overview-v0.6.0": "operations-center",
    "content-console-pipeline-state-v0.6.0": "content-pipeline",
    "content-console-video-v0.6.0": "video-studio",
    "content-console-orchestration-v0.6.0": "orchestration",
    "content-console-knowledge-v0.6.0": "knowledge-studio",
    "smart-router-console-v0.6.0": "smart-router",
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def capture(mock_url: str):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        launch_options = {"headless": True}
        chrome = "/usr/bin/google-chrome"
        if pathlib.Path(chrome).exists():
            launch_options["executable_path"] = chrome
        browser = await p.chromium.launch(**launch_options)
        context = await browser.new_context(
            viewport=VIEWPORTS["desktop"],
            device_scale_factor=1,
            locale="en-US",
        )
        page = await context.new_page()

        # Navigate to mock server
        await page.goto(mock_url, wait_until="networkidle")
        # The mock session endpoint authenticates immediately. If the login
        # form ever appears, sign in with the demo token as a fallback.
        if await page.locator("#login:not(.hidden)").count():
            await page.fill("#token", TOKEN)
            await page.click("button.btn.primary[type=submit]")
            await page.wait_for_timeout(800)
        # Wait for shell to show
        await page.wait_for_selector("#shell:not(.hidden)", timeout=5000)
        await page.wait_for_timeout(500)

        captures = [
            cap for cap in CAPTURES
            if not CAPTURE_ONLY or cap["view"] == CAPTURE_ONLY or cap["name"] == CAPTURE_ONLY
        ]
        for cap in captures:
            print(f"📸 {cap['name']} ({cap['view']})")
            # Navigate via the sidebar button
            view_button = page.locator(f"#nav >> button:has-text('{cap['view'].title()}')")
            if cap["view"] == "overview":
                view_button = page.locator("#nav >> button:has-text('Overview')")
            elif cap["view"] == "state":
                view_button = page.locator("#nav >> button:has-text('Pipeline state')")
            elif cap["view"] == "platforms":
                view_button = page.locator("#nav >> button:has-text('Platforms')")
            elif cap["view"] == "storage":
                view_button = page.locator("#nav >> button:has-text('Storage')")
            elif cap["view"] == "backups":
                view_button = page.locator("#nav >> button:has-text('Backups')")
            elif cap["view"] == "orchestration":
                view_button = page.locator("#nav >> button:has-text('Orchestration')")
            elif cap["view"] == "video":
                view_button = page.locator("#nav >> button:has-text('Video Studio')")
            elif cap["view"] == "storyboard":
                view_button = page.locator("#nav >> button:has-text('Storyboard')")
            elif cap["view"] == "knowledge":
                view_button = page.locator("#nav >> button:has-text('Knowledge')")
            elif cap["view"] == "hermes":
                view_button = page.locator("#nav >> button:has-text('Hermes')")

            await view_button.click()
            await page.wait_for_timeout(cap["wait"])

            if cap["view"] == "video":
                await page.fill("input[placeholder^='Topic']", "Smart Router failover for AI content operations")
                await page.fill("input[placeholder='Brand label on the video']", "locallab")
                await page.fill("input[placeholder='e.g. fa']", "en")
                await page.click("button:has-text('Plan')")
                await page.wait_for_timeout(700)
                open_timeline = page.locator("details[open] >> summary:has-text('Timeline JSON')")
                if await open_timeline.count():
                    await open_timeline.click()
                    await page.wait_for_timeout(200)

            if cap["view"] == "storyboard":
                await page.evaluate("""
                  (() => {
                    document.body.style.zoom = '0.72';
                    const page = document.getElementById('page');
                    if (!page) return;
                    const cards = Array.from(page.querySelectorAll('.card'));
                    const planCard = cards.find((card) => card.querySelector('button')?.textContent?.includes('Plan draft'));
                    if (planCard) {
                      planCard.style.maxHeight = '96px';
                      planCard.style.overflow = 'hidden';
                      planCard.style.opacity = '0.72';
                    }
                    const selectedDraft = cards.find((card) => card.textContent?.includes('Selected draft'));
                    if (selectedDraft) {
                      selectedDraft.style.maxHeight = 'none';
                      selectedDraft.style.padding = '12px';
                    }
                    page.querySelectorAll('[data-scene]').forEach((scene) => {
                      scene.style.display = 'grid';
                      scene.style.gridTemplateColumns = 'minmax(0, 1fr) minmax(0, 1fr)';
                      scene.style.gap = '6px 10px';
                      scene.style.padding = '9px';
                      scene.children[0].style.gridColumn = '1 / -1';
                      scene.children[1].style.gridTemplateColumns = 'repeat(2, minmax(0, 1fr))';
                      scene.children[2].style.gridColumn = '2';
                      scene.children[3].style.gridColumn = '1';
                      scene.children[4].style.gridColumn = '2';
                      scene.children[4].style.gridTemplateColumns = 'repeat(3, minmax(0, 1fr))';
                      scene.querySelectorAll('label').forEach((label) => {
                        label.style.gap = '2px';
                        label.style.lineHeight = '1.1';
                        label.style.minWidth = '0';
                      });
                      scene.querySelectorAll('label > span').forEach((label) => {
                        label.style.fontSize = '9px';
                      });
                      scene.querySelectorAll('input, select').forEach((field) => {
                        field.style.minHeight = '27px';
                        field.style.height = '27px';
                        field.style.padding = '3px 6px';
                        field.style.fontSize = '10px';
                      });
                    });
                    page.querySelectorAll('[data-scene] textarea').forEach((textarea) => {
                      textarea.style.minHeight = '34px';
                      textarea.style.height = '34px';
                      textarea.style.padding = '4px 6px';
                      textarea.style.fontSize = '10px';
                    });
                    page.querySelectorAll('[data-scene] .row button').forEach((button) => {
                      button.style.padding = '3px 7px';
                      button.style.fontSize = '10px';
                    });
                  })();
                """)
            else:
                await page.evaluate("document.body.style.zoom = '1'")

            if cap["view"] == "knowledge":
                await page.fill("input[placeholder='Ask the knowledge base a question']", "How do agents use the video studio timeline?")
                await page.click("button:has-text('Search')")
                await page.wait_for_timeout(700)

            # Scroll to top of page content
            await page.evaluate("""
                window.scrollTo(0, 0);
                document.documentElement.scrollTop = 0;
                document.body.scrollTop = 0;
                const page = document.getElementById('page');
                if (page) page.scrollTop = 0;
            """)
            await page.wait_for_timeout(300)

            asset_path = OUT / f"{cap['name']}.png"
            await page.screenshot(path=str(asset_path), full_page=False)
            clean_name = CLEAN_NAMES.get(cap["name"])
            if clean_name:
                clean_path = SCREENSHOT_DIR / f"{clean_name}.png"
                clean_path.write_bytes(asset_path.read_bytes())
                print(f"  ✅ Saved {asset_path} and {clean_path}")
            else:
                print(f"  ✅ Saved {asset_path}")

        await browser.close()
        print("🎉 All screenshots captured.")


def main():
    mock_port = free_port()
    mock_url = f"http://127.0.0.1:{mock_port}"
    # Start mock server
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "mock_server.py"), "--port", str(mock_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)
        asyncio.run(capture(mock_url))
    finally:
        proc.terminate()
        proc.wait(timeout=3)


if __name__ == "__main__":
    main()
