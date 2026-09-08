import os
import shutil
import tempfile
import time
import unittest

from media_studio.config import Settings
from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.runner import JobQueue
from media_studio.state import StateStore, STATUS_DONE, STATUS_FAILED


class FakeDriver(Driver):
    name = "fake"

    def run(self, ctx: RunContext):
        with open(os.path.join(ctx.work_dir, "out.txt"), "w", encoding="utf-8") as handle:
            handle.write(ctx.prompt)
        return [("out.txt", "file")]


class FailingDriver(Driver):
    name = "fake-fail"

    def run(self, ctx: RunContext):
        raise DriverError("model refused", step="request", hint="try again later")


class BrokenDriver(Driver):
    name = "fake-broken"

    def run(self, ctx: RunContext):
        raise ValueError("boom")


def factory(name):
    return {"fake": FakeDriver(), "fake-fail": FailingDriver(), "fake-broken": BrokenDriver()}[name]


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-runner-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        settings = Settings(data_dir=self.dir, drivers=("fake", "fake-fail", "fake-broken"), api_token="")
        self.state = StateStore(os.path.join(self.dir, "jobs.json"))
        self.queue = JobQueue(settings, self.state, driver_factory=factory)
        self.queue.start()
        self.addCleanup(self.queue.stop)

    def _wait_status(self, job_id, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.state.get(job_id)
            if job.status in (STATUS_DONE, STATUS_FAILED):
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} did not finish")

    def test_successful_job_writes_artifact(self):
        job = self.queue.submit("fake", "hello media")
        done = self._wait_status(job.id)
        self.assertEqual(done.status, STATUS_DONE)
        self.assertEqual(len(done.artifacts), 1)
        self.assertEqual(done.artifacts[0].name, "out.txt")
        path = os.path.join(self.queue.artifact_root, job.id, "out.txt")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "hello media")

    def test_failing_driver_records_error(self):
        job = self.queue.submit("fake-fail", "nope")
        done = self._wait_status(job.id)
        self.assertEqual(done.status, STATUS_FAILED)
        self.assertIn("model refused", done.error)

    def test_unexpected_exception_is_captured(self):
        job = self.queue.submit("fake-broken", "nope")
        done = self._wait_status(job.id)
        self.assertEqual(done.status, STATUS_FAILED)
        self.assertIn("ValueError", done.error)

    def test_rejects_disabled_driver(self):
        with self.assertRaises(ValueError):
            self.queue.submit("flow-video", "not enabled")
        with self.assertRaises(ValueError):
            self.queue.submit("fake", "   ")

    def test_probe_requires_browser_driver(self):
        with self.assertRaises(ValueError):
            self.queue.submit("probe", "", {"driver": "api-image"})


if __name__ == "__main__":
    unittest.main()
