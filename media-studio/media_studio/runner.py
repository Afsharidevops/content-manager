"""Job queue: one worker thread executes jobs serially against one browser session."""

from __future__ import annotations

import importlib
import logging
import os
import threading
import time
import traceback

from media_studio.browser import open_page, snapshot_page
from media_studio.drivers import DRIVERS, PROBE, DriverMeta, meta_for
from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.state import ArtifactInfo, Job, StateStore

LOGGER = logging.getLogger("media_studio.runner")

_DRIVER_MODULES = {
    "api-image": ("media_studio.drivers.api_image", "ApiImageDriver"),
    "flow-video": ("media_studio.drivers.flow_video", "FlowVideoDriver"),
    "gemini-image": ("media_studio.drivers.gemini_image", "GeminiImageDriver"),
}


def _load_driver(name: str) -> Driver:
    entry = _DRIVER_MODULES.get(name)
    if not entry:
        raise KeyError(name)
    module_name, class_name = entry
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


class JobQueue:
    def __init__(self, settings, state: StateStore, driver_factory=_load_driver) -> None:
        self._settings = settings
        self._state = state
        self._factory = driver_factory
        self._artifact_root = os.path.join(settings.data_dir, "artifacts")
        self._log_root = os.path.join(settings.data_dir, "logs")
        os.makedirs(self._artifact_root, exist_ok=True)
        os.makedirs(self._log_root, exist_ok=True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def artifact_root(self) -> str:
        return self._artifact_root

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="media-studio-worker", daemon=True)
        self._thread.start()
        LOGGER.info("Worker thread started.")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def submit(self, driver: str, prompt: str, params: dict | None = None) -> Job:
        if driver != PROBE and driver not in self._settings.drivers:
            raise ValueError(f"Driver '{driver}' is not enabled. Enabled: {', '.join(self._settings.drivers)}.")
        if driver == PROBE:
            target = meta_for(str((params or {}).get("driver", "")))
            if not target or not target.needs_browser:
                raise ValueError("A probe requires a browser driver target (flow-video or gemini-image).")
        elif not prompt.strip():
            raise ValueError("Prompt must not be empty.")
        return self._state.create_job(driver, prompt, params or {})

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._state.claim_next_queued()
            if job is None:
                self._stop.wait(0.6)
                continue
            try:
                self._execute(job)
            except Exception:  # noqa: BLE001 - worker must survive driver bugs
                LOGGER.exception("Unhandled failure for job %s", job.id)
                self._state.finish(job.id, error="Internal worker failure; see container logs.")

    def _execute(self, job: Job) -> None:
        work_dir = os.path.join(self._artifact_root, job.id)
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(job.log_path or ""), exist_ok=True)
        log_file = open(job.log_path or os.devnull, "a", encoding="utf-8")

        def log(message: str) -> None:
            line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
            try:
                log_file.write(line + "\n")
                log_file.flush()
            except OSError:
                pass
            LOGGER.info("job %s: %s", job.id, message)

        ctx = RunContext(
            prompt=job.prompt,
            params=job.params,
            work_dir=work_dir,
            settings=self._settings,
            log=log,
        )
        artifacts: list[tuple[str, str]] = []
        page = None
        try:
            if job.driver == PROBE:
                target = meta_for(str(job.params.get("driver", "")))
                artifacts = self._probe(ctx, target)
            else:
                driver = self._factory(job.driver)
                meta = meta_for(job.driver) or DriverMeta(job.driver, job.driver, "api")
                if meta.needs_browser:
                    with open_page(self._settings) as opened:
                        ctx.page = opened
                        page = opened
                        artifacts = driver.run(ctx)
                else:
                    artifacts = driver.run(ctx)
            registered = self._register_artifacts(job.id, artifacts)
            self._state.finish(job.id, artifacts=registered)
            log(f"finished with {len(registered)} artifact(s).")
        except DriverError as exc:
            log(f"driver error: {exc.describe()}")
            self._capture_failure(job.id, ctx, page, log)
            self._state.finish(job.id, artifacts=self._snapshot_artifacts(job.id), error=exc.describe())
        except Exception as exc:  # noqa: BLE001
            log(f"unexpected error: {exc}")
            log(traceback.format_exc(limit=6))
            self._capture_failure(job.id, ctx, page, log)
            self._state.finish(job.id, artifacts=self._snapshot_artifacts(job.id), error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                log_file.close()
            except OSError:
                pass

    def _probe(self, ctx: RunContext, target: DriverMeta) -> list[tuple[str, str]]:
        with open_page(self._settings) as page:
            ctx.page = page
            wait_s = max(3.0, float(ctx.params.get("wait_seconds", 25)))
            page.goto(target.target_url, wait_until="domcontentloaded", timeout=60000)
            ctx.log(f"probe: opened {target.target_url}; waiting {wait_s:.0f}s for the operator to sign in if needed.")
            deadline = time.time() + wait_s
            while time.time() < deadline:
                page.wait_for_timeout(1000)
            files = snapshot_page(page, ctx.work_dir, prefix="probe")
            ctx.log(f"probe: snapshot captured: {', '.join(files)}")
        return [(name, "file") for name in files]

    def _register_artifacts(self, job_id: str, artifacts: list[tuple[str, str]]) -> list[ArtifactInfo]:
        registered: list[ArtifactInfo] = []
        work_dir = os.path.join(self._artifact_root, job_id)
        for name, kind in artifacts:
            path = os.path.join(work_dir, os.path.basename(name))
            if not os.path.isfile(path):
                continue
            registered.append(ArtifactInfo(name=os.path.basename(name), kind=kind, size=os.path.getsize(path)))
        return registered

    def _snapshot_artifacts(self, job_id: str) -> list[ArtifactInfo]:
        work_dir = os.path.join(self._artifact_root, job_id)
        result = []
        if os.path.isdir(work_dir):
            for name in sorted(os.listdir(work_dir)):
                path = os.path.join(work_dir, name)
                if os.path.isfile(path):
                    result.append(ArtifactInfo(name=name, kind="file", size=os.path.getsize(path)))
        return result

    def _capture_failure(self, job_id: str, ctx: RunContext, page, log) -> None:
        if page is None:
            return
        try:
            files = snapshot_page(page, ctx.work_dir, prefix="failure")
            log(f"failure snapshot: {', '.join(files)}")
        except Exception:  # noqa: BLE001
            pass
