"""Config wiring tests."""

from __future__ import annotations

import os
import unittest

from app.config import Settings


class VideoStyleConfigTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("NOTEBOOKLM_VIDEO_STYLE", None)
        os.environ.pop("NOTEBOOKLM_VIDEO_TEMPLATE", None)

    def test_video_style_env_parsing(self):
        os.environ["NOTEBOOKLM_VIDEO_STYLE"] = "all"
        settings = Settings.from_env()
        self.assertEqual("all", settings.video_style)

    def test_video_template_default_is_explainer(self):
        settings = Settings.from_env()
        self.assertEqual("explainer", settings.video_template)
