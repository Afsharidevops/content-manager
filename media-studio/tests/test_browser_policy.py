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
