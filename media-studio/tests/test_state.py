import os
import shutil
import tempfile
import unittest

from media_studio.state import StateStore, STATUS_CANCELED, STATUS_DONE, STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-state-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.store = StateStore(os.path.join(self.dir, "jobs.json"))

    def test_lifecycle(self):
        job = self.store.create_job("api-image", "a red cube")
        self.assertEqual(job.status, STATUS_QUEUED)
        claimed = self.store.claim_next_queued()
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, STATUS_RUNNING)
        finished = self.store.finish(job.id, error="")
        self.assertEqual(finished.status, STATUS_DONE)
        self.assertIsNotNone(finished.finished_at)

    def test_fifo_and_cancel(self):
        first = self.store.create_job("api-image", "first")
        second = self.store.create_job("api-image", "second")
        self.assertTrue(self.store.cancel_queued(first.id))
        claimed = self.store.claim_next_queued()
        self.assertEqual(claimed.id, second.id)
        self.assertIsNone(self.store.claim_next_queued())
        self.assertEqual(self.store.get(first.id).status, STATUS_CANCELED)

    def test_failure_records_error(self):
        job = self.store.create_job("api-image", "bad")
        self.store.claim_next_queued()
        failed = self.store.finish(job.id, error="boom")
        self.assertEqual(failed.status, STATUS_FAILED)
        self.assertIn("boom", failed.error)

    def test_persistence_reload(self):
        job = self.store.create_job("flow-video", "persist me")
        reloaded = StateStore(os.path.join(self.dir, "jobs.json"))
        self.assertEqual(reloaded.get(job.id).prompt, "persist me")


if __name__ == "__main__":
    unittest.main()
