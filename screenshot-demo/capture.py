#!/usr/bin/env python3
"""Capture panel screenshots using Playwright + mock API server.

Usage:
    python3 screenshot-demo/capture.py
"""

import asyncio, json, os, pathlib, socket, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "docs-site" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

TOKEN = "demo-token"

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
        "name": "content-console-platforms-v0.6.0",
        "view": "platforms",
        "wait": 1200,
        "description": "Publishing platform configuration cards for Telegram, Instagram, Bale, Eitaa, and more.",
    },
    {
        "name": "content-console-storage-v0.6.0",
        "view": "storage",
        "wait": 1200,
        "description": "Object storage view with RustFS console, S3 configuration, and service storage matrix.",
    },
    {
        "name": "content-console-backups-v0.6.0",
        "view": "backups",
        "wait": 1200,
        "description": "Backup archives listing with stack version, size, sections, and creation times.",
    },
    {
        "name": "content-console-orchestration-v0.6.0",
        "view": "orchestration",
        "wait": 1500,
        "description": "Multi-agent orchestration runs: completed, awaiting approval, running, and failed.",
    },
]


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

        for cap in CAPTURES:
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

            await view_button.click()
            await page.wait_for_timeout(cap["wait"])
            # Scroll to top of page content
            await page.evaluate("document.getElementById('page').scrollTop = 0")
            await page.wait_for_timeout(300)

            await page.screenshot(path=str(OUT / f"{cap['name']}.png"), full_page=False)
            print(f"  ✅ Saved {OUT / cap['name']}.png")

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
