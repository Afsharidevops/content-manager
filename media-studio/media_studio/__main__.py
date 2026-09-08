"""Container entrypoint: run the HTTP API and the single worker thread."""

from __future__ import annotations

import logging
import os
import signal

from media_studio.config import Settings
from media_studio.runner import JobQueue
from media_studio.server import serve
from media_studio.state import StateStore


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("MEDIA_STUDIO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    state = StateStore(os.path.join(settings.data_dir, "jobs.json"))
    queue = JobQueue(settings, state)
    queue.start()

    def _shutdown(_signum, _frame) -> None:
        logging.getLogger("media_studio").info("Shutting down worker.")
        queue.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        serve(settings, state, queue)
    finally:
        queue.stop()


if __name__ == "__main__":
    main()
