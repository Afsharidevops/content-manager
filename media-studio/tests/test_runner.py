import base64
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

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


class BrandingRunnerTests(unittest.TestCase):
    TINY_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-brand-run-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _image_driver(self):
        class ImageDriver(Driver):
            name = "fake-image"

            def run(self, ctx):
                path = os.path.join(ctx.work_dir, "pic.png")
                with open(path, "wb") as handle:
                    handle.write(BrandingRunnerTests.TINY_PNG)
                return [("pic.png", "image")]

        return ImageDriver()

    def _queue(self, brand_label: str):
        settings = Settings(
            data_dir=self.dir,
            drivers=("fake-image",),
            api_token="",
            brand_label=brand_label,
        )
        state = StateStore(os.path.join(self.dir, "jobs.json"))
        queue = JobQueue(settings, state, driver_factory=lambda name: self._image_driver())
        queue.start()
        self.addCleanup(queue.stop)
        return queue, state

    def _run_and_wait(self, queue, state, params=None):
        job = queue.submit("fake-image", "draw", params)
        deadline = time.time() + 8
        while time.time() < deadline:
            done = state.get(job.id)
            if done.status in (STATUS_DONE, STATUS_FAILED):
                return done
            time.sleep(0.05)
        self.fail("branding job did not finish")

    def test_raster_artifact_is_branded_when_label_configured(self):
        queue, state = self._queue(brand_label="Locallab")
        with mock.patch(
            "media_studio.branding.apply_brand_overlay",
            return_value=True,
        ) as apply:
            done = self._run_and_wait(queue, state)
        self.assertEqual(done.status, STATUS_DONE)
        self.assertEqual(len(apply.call_args_list), 1)
        path, label = apply.call_args.args[0], apply.call_args.args[1]
        self.assertEqual(label, "Locallab")
        self.assertTrue(path.endswith("pic.png"))
        self.assertTrue(os.path.isfile(path))

    def test_branding_skipped_when_label_blank(self):
        queue, state = self._queue(brand_label="")
        with mock.patch(
            "media_studio.branding.apply_brand_overlay",
            return_value=True,
        ) as apply:
            done = self._run_and_wait(queue, state)
        self.assertEqual(done.status, STATUS_DONE)
        apply.assert_not_called()

    def test_branding_skipped_when_job_opt_out(self):
        queue, state = self._queue(brand_label="Locallab")
        with mock.patch(
            "media_studio.branding.apply_brand_overlay",
            return_value=True,
        ) as apply:
            done = self._run_and_wait(queue, state, params={"brand": "false"})
        self.assertEqual(done.status, STATUS_DONE)
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
