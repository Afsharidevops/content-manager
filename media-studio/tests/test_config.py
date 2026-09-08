import os
import unittest
from unittest.mock import patch

from media_studio.config import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env()
        self.assertIn("api-image", settings.drivers)
        self.assertTrue(settings.block_geo_redirect)

    def test_env_parsing(self):
        with patch.dict(
            os.environ,
            {
                "MEDIA_STUDIO_DATA_DIR": "/tmp/ms",
                "MEDIA_STUDIO_DRIVERS": "api-image, flow-video",
                "MEDIA_STUDIO_BLOCK_GEO_REDIRECT": "false",
                "MEDIA_STUDIO_FREEZE_ON_READY": "0",
                "MEDIA_STUDIO_PORT": "9900",
                "MEDIA_STUDIO_WRITER_BASE_URL": "https://api.example.test/v1",
                "MEDIA_STUDIO_WRITER_MODEL": "image-model",
            },
            clear=False,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.data_dir, "/tmp/ms")
        self.assertEqual(settings.drivers, ("api-image", "flow-video"))
        self.assertFalse(settings.block_geo_redirect)
        self.assertFalse(settings.freeze_on_ready)
        self.assertEqual(settings.port, 9900)
        self.assertEqual(settings.writer_base_url, "https://api.example.test/v1")
        self.assertEqual(settings.writer_model, "image-model")

    def test_writer_falls_back_to_content_env(self):
        with patch.dict(
            os.environ,
            {
                "CONTENT_WRITER_BASE_URL": "https://content.example.test/v1",
                "CONTENT_WRITER_API_KEY": "secret-key",
            },
            clear=False,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.writer_base_url, "https://content.example.test/v1")
        self.assertEqual(settings.writer_api_key, "secret-key")


if __name__ == "__main__":
    unittest.main()
