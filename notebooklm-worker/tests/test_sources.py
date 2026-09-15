"""Source classification, uploads, and material building."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest

from app import sources
from app.models import NotebookLMJob


class ClassifyTests(unittest.TestCase):
    def test_youtube_links_are_detected(self):
        for value in (
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://m.youtube.com/watch?v=abc",
        ):
            self.assertEqual("youtube", sources.classify(value))

    def test_plain_links_are_websites(self):
        self.assertEqual("url", sources.classify("https://lwn.net/Articles/1"))

    def test_anything_else_is_text(self):
        self.assertEqual("text", sources.classify("Kubernetes hardening notes"))


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-uploads-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_an_upload_round_trips_through_its_id(self):
        upload_id = sources.store_upload(self.dir, "brief.pdf", b"%PDF-1.4 body")
        path = sources.resolve_upload(self.dir, upload_id)
        self.assertTrue(path.endswith(".pdf"))
        with open(path, "rb") as handle:
            self.assertEqual(b"%PDF-1.4 body", handle.read())

    def test_an_unknown_extension_becomes_text(self):
        upload_id = sources.store_upload(self.dir, "notes.bin", b"raw")
        self.assertTrue(sources.resolve_upload(self.dir, upload_id).endswith(".txt"))

    def test_a_path_traversal_id_is_refused(self):
        self.assertEqual("", sources.resolve_upload(self.dir, "../secret"))
        self.assertEqual("", sources.resolve_upload(self.dir, "a/b"))

    def test_stale_uploads_are_pruned(self):
        fresh = sources.store_upload(self.dir, "new.txt", b"new")
        old = sources.store_upload(self.dir, "old.txt", b"old")
        old_path = sources.resolve_upload(self.dir, old)
        os.utime(old_path, (time.time() - 7200, time.time() - 7200))
        removed = sources.prune_uploads(self.dir, ttl_seconds=3600)
        self.assertEqual(1, removed)
        self.assertTrue(sources.resolve_upload(self.dir, fresh))
        self.assertFalse(sources.resolve_upload(self.dir, old))

    def test_pruning_is_off_when_the_ttl_is_zero(self):
        sources.store_upload(self.dir, "keep.txt", b"keep")
        self.assertEqual(0, sources.prune_uploads(self.dir, ttl_seconds=0))


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-materials-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def build(self, raw_sources):
        job = NotebookLMJob(topic="Topic", sources=raw_sources)
        return sources.build_materials(job, self.dir)

    def test_auto_kind_is_resolved_by_value(self):
        materials = self.build(
            [
                "https://example.com/post",
                "https://youtu.be/abc",
                "Just some notes",
            ]
        )
        self.assertEqual(["url", "youtube", "text"], [m.kind for m in materials])

    def test_a_file_source_resolves_to_its_stored_path(self):
        upload_id = sources.store_upload(self.dir, "brief.md", b"# Brief")
        materials = self.build([{"kind": "file", "value": upload_id, "title": "Brief"}])
        self.assertEqual(1, len(materials))
        self.assertEqual("file", materials[0].kind)
        self.assertTrue(materials[0].path.endswith(".md"))

    def test_an_unknown_upload_is_skipped(self):
        self.assertEqual([], self.build([{"kind": "file", "value": "nope"}]))

    def test_empty_values_are_skipped(self):
        materials = self.build([{"kind": "text", "value": "   "}, "", {"value": "kept"}])
        self.assertEqual(["kept"], [m.value for m in materials])

    def test_the_source_note_lists_titles_and_links(self):
        upload_id = sources.store_upload(self.dir, "brief.txt", b"body")
        materials = self.build(
            [
                {"kind": "text", "value": "Long pasted text", "title": "Brief"},
                {"kind": "url", "value": "https://example.com"},
                {"kind": "file", "value": upload_id, "title": "Reference brief"},
            ]
        )
        note = sources.sources_note(materials)
        self.assertIn("- Brief", note)
        self.assertIn("- https://example.com", note)
        self.assertIn("- Reference brief", note)


if __name__ == "__main__":
    unittest.main()
