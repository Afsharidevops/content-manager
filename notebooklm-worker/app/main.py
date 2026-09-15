"""Entry point: ``python -m app`` serves the API, ``python -m app login``
opens NotebookLM once so the operator can sign in."""

from __future__ import annotations

import logging
import os
import sys

from app import browser
from app.config import Settings
from app.models import JobStore
from app.runner import JobRunner
from app.server import serve

LOGGER = logging.getLogger("notebooklm")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_server(settings: Settings) -> int:
    store = JobStore(os.path.join(settings.data_dir, "jobs.json"))
    runner = JobRunner(settings, store)
    runner.start()
    httpd = serve(settings, store, runner)
    LOGGER.info(
        "NotebookLM worker listening on %s:%s (session mode %s)",
        settings.bind_ip,
        settings.port,
        settings.session_mode,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping the NotebookLM worker")
    finally:
        httpd.server_close()
        runner.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    command = args[0] if args else "serve"
    if command in {"login", "notebooklm-login"}:
        wait = 600
        if len(args) > 1 and str(args[1]).isdigit():
            wait = int(args[1])
        LOGGER.info(
            "Opening NotebookLM for a manual sign-in (mode %s)", settings.session_mode
        )
        return browser.login(settings, wait_seconds=wait)
    if command in {"serve", "run"}:
        return run_server(settings)
    print("Usage: python -m app [serve|login [seconds]]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
