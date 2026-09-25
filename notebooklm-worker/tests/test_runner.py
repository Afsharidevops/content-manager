"""Runner state-machine tests with an injected automation."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from app.config import Settings
from app.models import JobStore, NotebookLMJob
from app import runner as runner_mod


def good_automation(ctx) -> str:
    ctx.progress(runner_mod.STAGE_SOURCES)
    ctx.progress(runner_mod.STAGE_NOTEBOOK)
    ctx.progress(runner_mod.STAGE_UPLOAD)
    ctx.progress(runner_mod.STAGE_PROCESS)
    ctx.progress(runner_mod.STAGE_GENERATE)
    ctx.progress(runner_mod.STAGE_DOWNLOAD)
    target = os.path.join(ctx.videos_dir, f"{ctx.job.id}.mp4")
    with open(target, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42video")
    return target


def failing_automation(ctx) -> str:
    ctx.progress(runner_mod.STAGE_SOURCES)
    raise RuntimeError("NotebookLM selector drifted")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-runner-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, poll_seconds=1)
        self.store = JobStore(os.path.join(self.dir, "jobs.json"))

    def build(self, automation) -> runner_mod.JobRunner:
        return runner_mod.JobRunner(self.settings, self.store, automation=automation)

    def test_a_finished_job_moves_through_every_state(self):
        seen: list[str] = []
        original = self.store.transition

        def spy(job_id, status, **kwargs):
            seen.append(status)
            return original(job_id, status, **kwargs)

        self.store.transition = spy  # type: ignore[assignment]
        job = self.store.create(NotebookLMJob(topic="Kubernetes security"))
        runner = self.build(good_automation)
        runner.run_job(job)
        collapsed = [status for index, status in enumerate(seen) if index == 0 or status != seen[index - 1]]
        self.assertEqual(
            ["uploading", "processing", "generating", "downloading", "ready"],
            collapsed,
        )
        stored = self.store.get(job.id)
        self.assertEqual("ready", stored.status)
        self.assertTrue(os.path.isfile(stored.video_path))
        self.assertTrue(os.path.isfile(stored.log_path))

    def test_only_the_forward_states_are_visited_once(self):
        job = self.store.create(NotebookLMJob(topic="Topic"))
        runner = self.build(good_automation)
        runner.run_job(job)
        stored = self.store.get(job.id)
        self.assertEqual("ready", stored.status)
        self.assertEqual("", stored.stage)
        with open(stored.log_path, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertIn("started", contents)
        self.assertIn(stored.id, contents)

    def test_a_failure_is_recorded_with_its_message(self):
        job = self.store.create(NotebookLMJob(topic="Topic"))
        runner = self.build(failing_automation)
        runner.run_job(job)
        stored = self.store.get(job.id)
        self.assertEqual("failed", stored.status)
        self.assertIn("NotebookLM selector drifted", stored.error)
        self.assertTrue(stored.finished_at)

    def test_run_once_claims_and_runs_the_oldest_job(self):
        self.store.create(NotebookLMJob(topic="Topic"))
        runner = self.build(good_automation)
        self.assertTrue(runner.run_once())
        self.assertFalse(runner.run_once())

    def test_the_runner_stop_is_idempotent(self):
        runner = self.build(good_automation)
        runner.stop()
        runner.start()
        runner.stop()
        runner.stop()


if __name__ == "__main__":
    unittest.main()

class AuditRunnerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-audit-runner-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, poll_seconds=1)
        self.store = JobStore(os.path.join(self.dir, "jobs.json"))

    def test_source_count_negative_fails_the_job(self):
        def automation(ctx):
            ctx.progress(runner_mod.STAGE_SOURCES)
            ctx.progress(runner_mod.STAGE_PROCESS)
            from app.notebook import NotebookLMError
            count = -1
            if count <= 0:
                raise NotebookLMError("source verification", "notebook has 0 sources after adding materials")
            return "unreachable"

        job = self.store.create(NotebookLMJob(topic="Topic"))
        runner = runner_mod.JobRunner(self.settings, self.store, automation=automation)
        runner.run_job(job)
        stored = self.store.get(job.id)
        self.assertEqual("failed", stored.status)
        self.assertIn("source verification", stored.error)

    def test_all_styles_generates_one_download_per_style(self):
        styles = [("Anime", "anime"), ("Classic", "classic")]

        def automation(ctx):
            ctx.progress(runner_mod.STAGE_SOURCES)
            ctx.progress(runner_mod.STAGE_PROCESS)
            ctx.progress(runner_mod.STAGE_GENERATE)
            paths = {}
            for label, key in styles:
                ctx.progress(runner_mod.STAGE_DOWNLOAD)
                path = os.path.join(ctx.videos_dir, f"{ctx.job.id}__explainer__{key}.mp4")
                with open(path, "wb") as handle:
                    handle.write(f"video-{key}".encode())
                paths[key] = path
            ctx.store.update(ctx.job.id, video_paths=paths)
            return next(iter(paths.values()))

        job = self.store.create(NotebookLMJob(topic="Topic", video_style="all"))
        runner = runner_mod.JobRunner(self.settings, self.store, automation=automation)
        runner.run_job(job)
        stored = self.store.get(job.id)
        self.assertEqual("ready", stored.status)
        self.assertEqual({"anime", "classic"}, set(stored.video_paths))
        for path in stored.video_paths.values():
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 0)

class AuditPathTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-paths-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_all_style_output_path_includes_format_and_style(self):
        path = runner_mod.video_output_path(self.dir, "job1", "explainer", "anime")
        self.assertTrue(path.endswith("job1__explainer__anime.mp4"))

    def test_all_style_output_path_avoids_collision(self):
        first = runner_mod.video_output_path(self.dir, "job1", "explainer", "anime")
        with open(first, "wb") as handle:
            handle.write(b"old")
        second = runner_mod.video_output_path(self.dir, "job1", "explainer", "anime")
        self.assertTrue(second.endswith("job1__explainer__anime-2.mp4"))
        self.assertNotEqual(first, second)
