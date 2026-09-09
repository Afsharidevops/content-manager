"""Offline tests for the topic search provider."""

from __future__ import annotations

import unittest

from content_bot.search import SearchError, parse_results, search_topic

PAGE = """
<html><body>
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone&rut=abc">Persian title one</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone&rut=abc">
    First useful snippet about the topic.
  </a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.org/two">Title two</a>
  <a class="result__snippet" href="https://example.org/two">Second snippet here.</a>
</div>
<a class="result__a" href="mailto:bad@example.com">Ignored</a>
</body></html>
"""


class SearchTests(unittest.TestCase):
    def test_parse_results_decodes_redirects(self):
        results = parse_results(PAGE)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://example.com/one")
        self.assertEqual(results[0]["title"], "Persian title one")
        self.assertEqual(results[1]["url"], "https://example.org/two")
        self.assertTrue(results[1]["snippet"].startswith("Second snippet"))

    def test_search_topic_uses_fetched_html_and_limit(self):
        results = search_topic("some topic", limit=1, fetch_html=lambda q: PAGE)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Persian title one")

    def test_search_topic_rejects_short_query(self):
        with self.assertRaises(SearchError):
            search_topic("ab", limit=2, fetch_html=lambda q: PAGE)

    def test_search_topic_propagates_fetch_failures(self):
        def broken(query):
            raise SearchError("network down")

        with self.assertRaises(SearchError):
            search_topic("some topic", fetch_html=broken)


if __name__ == "__main__":
    unittest.main()
