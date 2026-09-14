"""Tests for target resolution and the publication ledger rows."""

from __future__ import annotations

import unittest

from content_bot import publications


class NormalizeTargetTests(unittest.TestCase):
    def test_strings_and_mappings_produce_the_same_key(self):
        self.assertEqual("linkedin_personal", publications.normalize_target("LinkedIn_Personal"))
        self.assertEqual(
            "linkedin_personal",
            publications.normalize_target({"platform": "linkedin", "account": "personal"}),
        )
        self.assertEqual("telegram", publications.normalize_target({"platform": "telegram"}))
        self.assertEqual("", publications.normalize_target({"account": "personal"}))
        self.assertEqual("", publications.normalize_target(None))


class ResolveTargetTests(unittest.TestCase):
    def test_draft_targets_win_over_the_defaults(self):
        record = {"targets": [{"platform": "linkedin", "account": "locallab"}, "bale"]}
        self.assertEqual(
            ["linkedin_locallab", "bale"],
            publications.resolve_targets(record, ["telegram"]),
        )

    def test_defaults_apply_when_the_draft_has_no_targets(self):
        self.assertEqual(
            ["telegram", "linkedin_personal"],
            publications.resolve_targets({}, ["telegram", "linkedin_personal"]),
        )

    def test_duplicates_are_dropped_and_order_is_kept(self):
        record = {"targets": ["bale", "Bale", "linkedin_personal", "bale"]}
        self.assertEqual(["bale", "linkedin_personal"], publications.resolve_targets(record))

    def test_policy_defaults_are_read_from_the_publishing_section(self):
        policy = {"publishing": {"targets": [{"platform": "linkedin", "account": "personal"}]}}
        self.assertEqual(["linkedin_personal"], publications.publication_defaults(policy))
        self.assertEqual(["telegram"], publications.publication_defaults({"publishing": {"targets": "telegram"}}))
        self.assertEqual([], publications.publication_defaults({}))


class PublicationRowTests(unittest.TestCase):
    def test_target_is_split_into_platform_and_account(self):
        row = publications.build_publication(
            "draft-1", "linkedin_locallab", publications.STATUS_PUBLISHED, remote_id="urn:li:share:1"
        ).as_row()
        self.assertEqual("linkedin", row["platform"])
        self.assertEqual("locallab", row["account"])
        self.assertEqual("linkedin_locallab", row["target"])
        self.assertEqual("urn:li:share:1", row["remote_id"])
        self.assertEqual("draft-1", row["content_id"])
        self.assertTrue(row["id"].startswith("pub_"))
        self.assertTrue(row["published_at"])

    def test_failures_keep_the_error_message(self):
        row = publications.build_publication(
            "draft-1", "bale", publications.STATUS_FAILED, error="Bale: HTTP 500"
        ).as_row()
        self.assertEqual("Bale: HTTP 500", row["error"])
        self.assertEqual("bale", row["target"])

    def test_long_errors_are_capped(self):
        row = publications.build_publication(
            "draft-1", "bale", publications.STATUS_FAILED, error="x" * 900
        ).as_row()
        self.assertEqual(500, len(row["error"]))


class SummaryTests(unittest.TestCase):
    def test_every_status_gets_a_readable_line(self):
        rows = [
            publications.build_publication(
                "d", "linkedin_personal", publications.STATUS_PUBLISHED, remote_id="urn:li:share:5"
            ).as_row(),
            publications.build_publication(
                "d", "bale", publications.STATUS_FAILED, error="Bale: HTTP 500"
            ).as_row(),
            publications.build_publication(
                "d", "eitaa", publications.STATUS_SKIPPED, error="already published"
            ).as_row(),
        ]
        summary = publications.summarize_labels(
            rows, {"linkedin_personal": "LinkedIn (personal)"}
        )
        self.assertIn("LinkedIn (personal): published (urn:li:share:5)", summary)
        self.assertIn("bale: failed - Bale: HTTP 500", summary)
        self.assertIn("eitaa: skipped", summary)
