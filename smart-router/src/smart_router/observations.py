from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from .privacy import privacy_safe_json


class ObservationWriter:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, payload: dict[str, Any]) -> None:
        if not self.path:
            return
        event = {"timestamp": int(time.time()), **payload}
        line = privacy_safe_json(event) + "\n"
        with self._lock:
            os.umask(0o077)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
