"""Serial job runner: one NotebookLM video at a time.

The browser flow is injected so the state machine can be tested without a
real Google session; ``run_browser_flow`` is the production automation.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass

from app import browser as browser_mod
from app import prompts as prompts_mod
from app import sources as sources_mod
from app import video as video_mod
from app.models import (
    JobStore,
    NotebookLMJob,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_GENERATING,
    STATUS_PROCESSING,
    STATUS_READY,
    STATUS_UPLOADING,
)
from app.notebook import NotebookEditor, load_selectors
from app.recovery import RecoveryClient

LOGGER = logging.getLogger("notebooklm.runner")

STAGE_SOURCES = "Preparing the sources"
STAGE_NOTEBOOK = "Creating the notebook"
STAGE_UPLOAD = "Uploading the sources"
STAGE_PROCESS = "Waiting for the sources"
STAGE_GENERATE = "Generating the video overview"
STAGE_DOWNLOAD = "Downloading the video"

#: Which workflow state each progress stage belongs to. The runner only ever
#: moves forward, so repeated stages are ignored by the transition helper.
STAGE_STATES = {
    STAGE_SOURCES: STATUS_UPLOADING,
    STAGE_NOTEBOOK: STATUS_UPLOADING,
    STAGE_UPLOAD: STATUS_UPLOADING,
    STAGE_PROCESS: STATUS_PROCESSING,
    STAGE_GENERATE: STATUS_GENERATING,
    STAGE_DOWNLOAD: STATUS_DOWNLOADING,
}


@dataclass
class RunContext:
    job: NotebookLMJob
    settings: object
    selectors: dict
    uploads_dir: str
    logs_dir: str
    videos_dir: str
    progress: object  # callable(stage: str) -> None
    recovery: RecoveryClient


def run_browser_flow(ctx: RunContext) -> str:
    """Drive NotebookLM end to end and return the downloaded video path."""
    settings = ctx.settings
    job = ctx.job
    with browser_mod.open_page(settings) as page:
        browser_mod.configure_page(page, settings)
        try:
            browser_mod.open_notebooklm(page, settings)
            editor = NotebookEditor(page, settings, selectors=ctx.selectors)
            materials = sources_mod.build_materials(job, ctx.uploads_dir)
            title = (job.topic or "Content Manager video").strip()[:120]
            ctx.progress(STAGE_NOTEBOOK)
            topic_submitted_via_modal = editor.create_notebook(title)
            ctx.progress(STAGE_UPLOAD)
            
            # Only render prompt and add as text source if topic was NOT already
            # submitted via the "create with topic" modal (new NotebookLM UI)
            if not topic_submitted_via_modal:
                topic_note = prompts_mod.render_prompt(
                    job.topic,
                    prompts_mod.get_profile(job.profile, default=settings.default_profile),
                    duration=job.duration,
                    sources_note=sources_mod.sources_note(materials),
                )
                editor.add_material(
                    sources_mod.Material(kind="text", value=topic_note, title="Video brief")
                )
            else:
                LOGGER.info("Topic was submitted via create modal, skipping add_material(text)")
                # Still render prompt for use in video overview
                topic_note = prompts_mod.render_prompt(
                    job.topic,
                    prompts_mod.get_profile(job.profile, default=settings.default_profile),
                    duration=job.duration,
                    sources_note=sources_mod.sources_note(materials),
                )
            
            for material in materials:
                editor.add_material(material)
            ctx.progress(STAGE_PROCESS)
            
            # Wait for sources - if topic was submitted via modal, we need to wait
            # for NotebookLM to finish processing the topic source
            editor.wait_for_sources()
            # Verify sources were actually added
            src_count = editor.source_count()
            LOGGER.info("Source count after wait: %d", src_count)
            if src_count == 0:
                # If topic was submitted via modal but no source yet, wait more
                if topic_submitted_via_modal:
                    LOGGER.info("Topic submitted via modal but source count is 0, waiting more...")
                    editor.wait_for_sources(timeout=120)
                    src_count = editor.source_count()
                    LOGGER.info("Source count after extended wait: %d", src_count)
                if src_count == 0:
                    raise NotebookLMError("source verification", "notebook has 0 sources after adding materials")
            ctx.progress(STAGE_GENERATE)
            editor.open_studio()
            video_mod.start_video_overview(page, topic_note, settings, ctx.selectors)
            video_mod.wait_for_video(page, settings, ctx.selectors)
            ctx.progress(STAGE_DOWNLOAD)
            target = os.path.join(ctx.videos_dir, f"{job.id}.mp4")
            return video_mod.download_video(page, target, settings, ctx.selectors)
        except Exception as error:
            if settings.keep_screenshots:
                try:
                    browser_mod.snapshot_page(page, ctx.logs_dir, prefix=job.id)
                except Exception as error:  # noqa: BLE001 - a report must not mask the failure
                    LOGGER.warning("Snapshot for job %s failed: %s", job.id, error)
            decision = getattr(ctx, "recovery", None)
            if decision is not None:
                recovery = decision.decide(
                    step=ctx.job.stage or "browser_flow",
                    attempt=1,
                    error=error,
                    page=page,
                )
                if recovery:
                    action = decision.apply(page, recovery)
                    LOGGER.warning("Recovery decision for job %s: %s", ctx.job.id, action)
            raise


class JobRunner:
    """Pull created jobs off the store and run them one after another."""

    def __init__(
        self,
        settings,
        store: JobStore,
        *,
        automation=None,
        poll_seconds: float = 2.0,
    ) -> None:
        self.settings = settings
        self.store = store
        self.automation = automation or run_browser_flow
        self.poll_seconds = poll_seconds
        self.uploads_dir = os.path.join(settings.data_dir, "uploads")
        self.logs_dir = os.path.join(settings.data_dir, "logs")
        self.videos_dir = os.path.join(settings.data_dir, "videos")
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for directory in (self.uploads_dir, self.logs_dir, self.videos_dir):
            os.makedirs(directory, exist_ok=True)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ensure_dirs()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notebooklm-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.poll_seconds)
            except Exception:  # noqa: BLE001 - the loop must survive one bad job
                LOGGER.exception("NotebookLM job loop error")
                self._stop.wait(self.poll_seconds)

    # ---------------------------------------------------------------- jobs

    def run_once(self) -> bool:
        job = self.store.claim_next_created()
        if job is None:
            return False
        self.run_job(job)
        return True

    def run_job(self, job: NotebookLMJob) -> NotebookLMJob | None:
        handler = self._attach_log(job)
        LOGGER.info(
            "Job %s started: profile=%s topic=%r sources=%d",
            job.id,
            job.profile,
            job.topic,
            len(job.sources or []),
        )
        try:
            sources_mod.prune_uploads(
                self.uploads_dir, int(self.settings.upload_ttl_seconds)
            )
            context = RunContext(
                job=job,
                settings=self.settings,
                selectors=load_selectors(),
                uploads_dir=self.uploads_dir,
                logs_dir=self.logs_dir,
                videos_dir=self.videos_dir,
                progress=lambda stage: self._progress(job.id, stage),
                recovery=RecoveryClient(self.settings),
            )
            path = self.automation(context)
            self.store.update(job.id, video_path=str(path or ""), stage="")
            LOGGER.info("Job %s produced %s", job.id, path)
            return self.store.transition(job.id, STATUS_READY)
        except Exception as error:  # noqa: BLE001 - every failure becomes job state
            message = f"{type(error).__name__}: {error}"
            LOGGER.warning("Job %s failed: %s", job.id, message)
            try:
                return self.store.transition(job.id, STATUS_FAILED, error=message)
            except Exception:  # noqa: BLE001 - never let reporting break the loop
                return None
        finally:
            self._detach_log(handler)

    def _progress(self, job_id: str, stage: str) -> None:
        """Record one automation step, advancing the workflow state with it."""
        target = STAGE_STATES.get(str(stage))
        if not target:
            self.store.update(job_id, stage=str(stage))
            return
        self.store.transition(job_id, target, stage=str(stage))

    # -------------------------------------------------------------- logging

    def _attach_log(self, job: NotebookLMJob):
        path = job.log_path or os.path.join(self.logs_dir, f"{job.id}.log")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            logger = logging.getLogger("notebooklm")
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
            logger.addHandler(handler)
        except OSError:
            return None
        self.store.update(job.id, log_path=path)
        return handler

    def _detach_log(self, handler) -> None:
        if handler is None:
            return
        logging.getLogger("notebooklm").removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass


def cleanup_work_dirs(settings) -> None:
    """Remove the transient upload scratch space (used by tests and tools)."""
    shutil.rmtree(os.path.join(settings.data_dir, "uploads"), ignore_errors=True)
    time.sleep(0)
