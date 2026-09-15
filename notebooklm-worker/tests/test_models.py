"""Job model, state machine, and store tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from app import models


class JobModelTests(unittest.TestCase):
    def test_a_new_job_gets_an_id_and_a_timestamp(self):
        job = models.NotebookLMJob(topic="Topic")
        self.assertTrue(job.id)
        self.assertTrue(job.created_at)
        self.assertEqual(models.STATUS_CREATED, job.status)

    def test_sources_are_normalized(self):
        job = models.NotebookLMJob(
            topic="Topic",
            sources=["https://example.com", {"kind": "text", "value": "body"}, {"value": ""}],
        )
        self.assertEqual(
            [
                {"kind": "auto", "value": "https://example.com"},
                {"kind": "text", "value": "body"},
            ],
            job.sources,
        )

    def test_the_dictionary_form_keeps_every_spec_field(self):
        job = models.NotebookLMJob(topic="Topic").to_dict()
        for field in (
            "id",
            "content_id",
            "topic",
            "sources",
            "language",
            "duration",
            "voice_gender",
            "style",
            "tone",
            "audience",
            "status",
            "video_path",
            "created_at",
            "error",
        ):
            self.assertIn(field, job)

    def test_unknown_keys_are_ignored_when_loading(self):
        job = models.NotebookLMJob.from_dict({"id": "abc", "topic": "T", "extra": 1})
        self.assertEqual("abc", job.id)
        self.assertFalse(hasattr(job, "extra"))


class TransitionTests(unittest.TestCase):
    def test_the_workflow_moves_forward_only(self):
        sequence = [
            models.STATUS_UPLOADING,
            models.STATUS_PROCESSING,
            models.STATUS_GENERATING,
            models.STATUS_DOWNLOADING,
            models.STATUS_READY,
        ]
        current = models.STATUS_CREATED
        for target in sequence:
            self.assertTrue(models.can_transition(current, target))
            current = target
        self.assertEqual(models.STATUS_READY, current)

    def test_a_backwards_step_is_refused(self):
        self.assertFalse(models.can_transition(models.STATUS_GENERATING, models.STATUS_UPLOADING))

    def test_any_live_state_may_fail(self):
        for state in (
            models.STATUS_CREATED,
            models.STATUS_UPLOADING,
            models.STATUS_PROCESSING,
            models.STATUS_GENERATING,
            models.STATUS_DOWNLOADING,
        ):
            self.assertTrue(models.can_transition(state, models.STATUS_FAILED))

    def test_a_finished_job_never_moves_again(self):
        for state in (models.STATUS_READY, models.STATUS_FAILED, models.STATUS_CANCELED):
            self.assertFalse(models.can_transition(state, models.STATUS_GENERATING))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-store-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "jobs.json")
        self.store = models.JobStore(self.path)

    def test_a_job_survives_a_reload(self):
        created = self.store.create(models.NotebookLMJob(topic="Topic"))
        reopened = models.JobStore(self.path)
        loaded = reopened.get(created.id)
        self.assertIsNotNone(loaded)
        self.assertEqual("Topic", loaded.topic)
        self.assertTrue(loaded.log_path.endswith(f"{created.id}.log"))

    def test_transitions_are_persisted_and_illegal_ones_refused(self):
        job = self.store.create(models.NotebookLMJob(topic="Topic"))
        self.store.transition(job.id, models.STATUS_UPLOADING, stage="Preparing the sources")
        with self.assertRaises(models.TransitionError):
            self.store.transition(job.id, models.STATUS_CREATED)
        reloaded = models.JobStore(self.path).get(job.id)
        self.assertEqual(models.STATUS_UPLOADING, reloaded.status)
        self.assertEqual("Preparing the sources", reloaded.stage)

    def test_a_failure_stamps_the_finish_time(self):
        job = self.store.create(models.NotebookLMJob(topic="Topic"))
        failed = self.store.transition(job.id, models.STATUS_FAILED, error="boom")
        self.assertTrue(failed.finished_at)
        self.assertEqual("boom", failed.error)

    def test_the_oldest_created_job_is_claimed_first(self):
        first = self.store.create(models.NotebookLMJob(topic="First"))
        second = self.store.create(models.NotebookLMJob(topic="Second"))
        claimed = self.store.claim_next_created()
        self.assertEqual(first.id, claimed.id)
        self.assertTrue(claimed.started_at)
        self.assertEqual(second.id, self.store.claim_next_created().id)
        self.assertIsNone(self.store.claim_next_created())

    def test_only_a_created_job_can_be_canceled(self):
        job = self.store.create(models.NotebookLMJob(topic="Topic"))
        self.assertTrue(self.store.cancel_created(job.id))
        self.assertFalse(self.store.cancel_created(job.id))

    def test_counts_split_queued_from_running(self):
        queued = self.store.create(models.NotebookLMJob(topic="Queued"))
        running = self.store.create(models.NotebookLMJob(topic="Running"))
        self.store.transition(running.id, models.STATUS_UPLOADING)
        self.assertEqual({"queued": 1, "running": 1}, self.store.counts())
        self.assertEqual(1, len(self.store.list_jobs(limit=1)))
        self.assertEqual(queued.id, self.store.get(queued.id).id)

    def test_an_update_only_touches_given_keys(self):
        job = self.store.create(models.NotebookLMJob(topic="Topic"))
        self.store.update(job.id, stage="Waiting for the sources")
        reloaded = self.store.get(job.id)
        self.assertEqual("Waiting for the sources", reloaded.stage)
        self.assertEqual("Topic", reloaded.topic)


if __name__ == "__main__":
    unittest.main()
