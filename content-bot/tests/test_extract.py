"""Tests for offline HTML and RSS/Atom parsing."""

from __future__ import annotations

import unittest

from content_bot.extract import extract_article, parse_rss


class ExtractArticleTest(unittest.TestCase):
    def test_article_text_and_metadata(self):
        html_text = """<html><head>
          <title>Fallback title</title>
          <meta property="og:title" content="Open Graph title">
          <meta property="og:description" content="A short description">
          <meta property="og:image" content="/cover.png">
          <meta property="article:published_time" content="2026-09-07T08:00:00Z">
        </head><body><nav>Skip navigation noise</nav>
          <article><h1>Headline</h1><p>First useful paragraph.</p>
          <p>Second useful paragraph with more context about containers.</p></article>
          <footer>Skip footer noise</footer></body></html>"""
        article = extract_article(html_text, "https://example.com/post/1")
        self.assertEqual(article["title"], "Open Graph title")
        self.assertEqual(article["description"], "A short description")
        self.assertEqual(article["image"], "https://example.com/cover.png")
        self.assertEqual(article["published_at"], "2026-09-07T08:00:00Z")
        self.assertIn("First useful paragraph", article["text"])
        self.assertIn("Second useful paragraph", article["text"])
        self.assertNotIn("Skip navigation noise", article["text"])
        self.assertNotIn("Skip footer noise", article["text"])

    def test_title_fallback_without_og_meta(self):
        html_text = "<html><head><title>Plain title</title></head><body><p>Text only.</p></body></html>"
        article = extract_article(html_text)
        self.assertEqual(article["title"], "Plain title")


class ParseRssTest(unittest.TestCase):
    def test_rss2_items(self):
        xml_text = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Feed</title>
          <item><title>First post</title><link>https://example.com/1</link>
            <pubDate>Mon, 07 Sep 2026 08:00:00 +0000</pubDate>
            <description>Summary one</description><category>devops</category></item>
          <item><title>No link post</title><description>Missing link</description></item>
        </channel></rss>"""
        items = parse_rss("Sample", "https://example.com/feed", xml_text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "First post")
        self.assertEqual(items[0]["url"], "https://example.com/1")
        self.assertEqual(items[0]["tags"], ["devops"])

    def test_atom_items(self):
        xml_text = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
          <entry><title>Atom post</title>
            <link rel="alternate" href="https://example.com/a/1"/>
            <updated>2026-09-07T08:00:00Z</updated></entry>
        </feed>"""
        items = parse_rss("Atom", "https://example.com/atom", xml_text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Atom post")
        self.assertEqual(items[0]["url"], "https://example.com/a/1")


if __name__ == "__main__":
    unittest.main()
