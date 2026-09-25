"""Browser helper tests."""

from __future__ import annotations

import logging
import unittest

from app import browser
from app.config import Settings


class FakePage:
    def __init__(self, urls: list[str]):
        self.urls = list(urls)
        self.goto_calls: list[tuple[str, str]] = []
        self.waits: list[int] = []
        self.url = ""

    def goto(self, url: str, wait_until: str):
        self.goto_calls.append((url, wait_until))
        if self.urls:
            self.url = self.urls.pop(0)
        else:
            self.url = url

    def wait_for_timeout(self, timeout_ms: int):
        self.waits.append(timeout_ms)


class BrowserTests(unittest.TestCase):
    def test_open_notebooklm_logs_and_retries_unsupported_redirect(self):
        settings = Settings(home_url="https://notebooklm.google.com/")
        page = FakePage(
            [
                "https://notebook.google/?location=unsupported",
                "https://notebooklm.google.com/",
            ]
        )

        with self.assertLogs("notebooklm.browser", level=logging.INFO) as logs:
            browser.open_notebooklm(page, settings)

        self.assertEqual(
            [
                ("https://notebooklm.google.com/", "domcontentloaded"),
                ("https://notebooklm.google.com/", "domcontentloaded"),
            ],
            page.goto_calls,
        )
        self.assertIn("reason=unsupported_or_wrong_host", "\n".join(logs.output))
        self.assertIn("action=retry_home_navigation", "\n".join(logs.output))
        self.assertIn("NotebookLM home retry completed", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
