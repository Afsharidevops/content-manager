import unittest

from media_studio.browser import is_geo_redirect_url, should_abort


class GeoPolicyTests(unittest.TestCase):
    def test_flow_unsupported_country_matches(self):
        urls = [
            "https://flow.google.com/unsupported-country",
            "https://flow.google.com/unsupported-country?hl=en",
            "https://flow.google-ir.com/unsupported-country",
            "http://flow.google.com/unsupported-country",
        ]
        for url in urls:
            self.assertTrue(is_geo_redirect_url(url), url)

    def test_unrelated_urls_do_not_match(self):
        urls = [
            "https://flow.google.com/",
            "https://flow.google.com/storyboard/xyz",
            "https://gemini.google.com/app",
            "https://flow.example.com/unsupported-country",
            "https://labs.google/fx/tools/flow",
        ]
        for url in urls:
            self.assertFalse(is_geo_redirect_url(url), url)

    def test_should_abort_respects_flag(self):
        url = "https://flow.google.com/unsupported-country"
        self.assertTrue(should_abort(url, True))
        self.assertFalse(should_abort(url, False))
        self.assertFalse(should_abort("https://flow.google.com/", True))


if __name__ == "__main__":
    unittest.main()

from unittest import mock

from media_studio.drivers.base import DriverError
from media_studio.drivers.flow_video import FlowVideoDriver

class _Ctx:
    def __init__(self):
        self.messages = []

    def log(self, message):
        self.messages.append(message)

class _Page:
    def __init__(self, url):
        self.url = url

class FlowVideoGeoRecoveryTests(unittest.TestCase):
    def test_unsupported_country_consent_recovery_continues_when_url_changes(self):
        driver = FlowVideoDriver()
        ctx = _Ctx()
        page = _Page("https://flow.google.com/unsupported-country")

        def dismiss(_page, _ctx):
            page.url = "https://flow.google.com/"
            return True

        with mock.patch("media_studio.drivers.flow_video.body_contains", return_value=None), \
             mock.patch.object(driver, "_dismiss_consent_page", side_effect=dismiss), \
             mock.patch("media_studio.drivers.flow_video.time.sleep", return_value=None):
            driver._check_geo_block(page, ctx)

        self.assertIn("recovered from unsupported-country", "\n".join(ctx.messages))

    def test_unsupported_country_still_fails_when_consent_cannot_recover(self):
        driver = FlowVideoDriver()
        page = _Page("https://flow.google.com/unsupported-country")

        with mock.patch("media_studio.drivers.flow_video.body_contains", return_value=None), \
             mock.patch.object(driver, "_dismiss_consent_page", return_value=False):
            with self.assertRaises(DriverError) as caught:
                driver._check_geo_block(page, _Ctx())

        self.assertEqual("geo", caught.exception.step)
        self.assertIn("unsupported region", str(caught.exception))
