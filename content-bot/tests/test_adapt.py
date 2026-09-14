"""Tests for the content adaptation layer and its tone profiles."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from content_bot import adapt

RECORD = {
    "id": "draft-1",
    "title": "Layer caching",
    "body": "Original body text.",
    "source_url": "https://example.com/layers",
    "category": "engineering",
}


class FakeWriter:
    def __init__(self, reply: str = "Adapted body text.", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def chat(self, messages, max_tokens=None):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        return self.reply


class ToneProfileTests(unittest.TestCase):
    def test_linkedin_targets_map_to_their_account_kind(self):
        personal = adapt.tone_for_target("linkedin_personal")
        self.assertEqual("linkedin_personal", personal.key)
        company = adapt.tone_for_target("linkedin_locallab", kind="organization")
        self.assertEqual("linkedin_company", company.key)
        person = adapt.tone_for_target("linkedin_founder", kind="person")
        self.assertEqual("linkedin_personal", person.key)

    def test_platform_and_unknown_targets_get_a_profile(self):
        self.assertEqual("telegram", adapt.tone_for_target("telegram").key)
        fallback = adapt.tone_for_target("mastodon")
        self.assertEqual("mastodon", fallback.key)
        self.assertEqual(("clear", "factual"), fallback.style)

    def test_policy_overrides_and_removes_profiles(self):
        tones = adapt.load_tones(
            {
                "tones": {
                    "linkedin_personal": {
                        "label": "Founder voice",
                        "style": ["first_person", "bold"],
                        "max_chars": 900,
                    },
                    "telegram": None,
                }
            }
        )
        self.assertEqual("Founder voice", tones["linkedin_personal"].label)
        self.assertEqual(("first_person", "bold"), tones["linkedin_personal"].style)
        self.assertEqual(900, tones["linkedin_personal"].max_chars)
        self.assertNotIn("telegram", tones)

    def test_prompt_templates_can_be_overridden_per_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "linkedin_personal.md").write_text("Custom prompt", encoding="utf-8")
            templates = adapt.load_templates(tmp)
        self.assertEqual("Custom prompt", templates["linkedin_personal"])
        self.assertIn("linkedin_company", templates)


class ContentAdapterTests(unittest.TestCase):
    def test_adaptation_sends_the_template_and_the_draft(self):
        writer = FakeWriter("Rewritten body.")
        adapter = adapt.ContentAdapter(writer=writer)
        text = adapter.text_for(RECORD, "linkedin_personal")
        self.assertEqual("Rewritten body.", text)
        system, user = writer.calls[0]["messages"]
        self.assertIn("first_person", system["content"])
        self.assertIn("personal profile", system["content"])
        self.assertEqual(RECORD["body"], json.loads(user["content"])["body"])

    def test_code_fences_are_stripped(self):
        writer = FakeWriter("```text\nClean post\n```")
        adapter = adapt.ContentAdapter(writer=writer)
        self.assertEqual("Clean post", adapter.text_for(RECORD, "telegram"))

    def test_long_replies_are_trimmed_to_the_tone_limit(self):
        writer = FakeWriter(" ".join(["word"] * 500))
        tones = {"linkedin_personal": adapt.ToneProfile(key="linkedin_personal", max_chars=60)}
        adapter = adapt.ContentAdapter(writer=writer, tones=tones, templates=adapt.DEFAULT_TEMPLATES)
        text = adapter.text_for(RECORD, "linkedin_personal")
        self.assertLessEqual(len(text), 60)
        self.assertFalse(text.endswith(" "))

    def test_disabled_adaptation_publishes_the_draft_body(self):
        writer = FakeWriter()
        adapter = adapt.ContentAdapter(writer=writer, enabled=False)
        self.assertEqual(RECORD["body"], adapter.text_for(RECORD, "linkedin_personal"))
        self.assertEqual([], writer.calls)

    def test_missing_writer_publishes_the_draft_body(self):
        adapter = adapt.ContentAdapter(writer=None)
        self.assertEqual(RECORD["body"], adapter.text_for(RECORD, "linkedin_personal"))

    def test_writer_failure_publishes_the_draft_body(self):
        writer = FakeWriter(error=RuntimeError("writer down"))
        adapter = adapt.ContentAdapter(writer=writer)
        self.assertEqual(RECORD["body"], adapter.text_for(RECORD, "linkedin_personal"))

    def test_empty_reply_publishes_the_draft_body(self):
        adapter = adapt.ContentAdapter(writer=FakeWriter("   "))
        self.assertEqual(RECORD["body"], adapter.text_for(RECORD, "linkedin_personal"))

    def test_variant_row_describes_the_target(self):
        adapter = adapt.ContentAdapter(writer=FakeWriter("Rewritten body."))
        row = adapter.variant(RECORD, "linkedin_locallab", kind="organization")
        self.assertEqual("linkedin", row["platform"])
        self.assertEqual("locallab", row["account"])
        self.assertEqual("linkedin_company", row["tone"])
        self.assertEqual("Rewritten body.", row["generated_text"])
        self.assertTrue(row["adapted"])

    def test_unchanged_reply_is_not_marked_as_adapted(self):
        adapter = adapt.ContentAdapter(writer=FakeWriter(RECORD["body"]))
        row = adapter.variant(RECORD, "linkedin_personal")
        self.assertFalse(row["adapted"])
        self.assertEqual("", row["tone"])
