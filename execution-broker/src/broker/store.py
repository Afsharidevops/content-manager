"""Persistent, independently approved capability nonces."""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

CAPABILITY_TTL_SECONDS = 300


class CapabilityError(ValueError):
    """A capability that must not be executed."""


class CapabilityStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS capabilities (
                    nonce      TEXT PRIMARY KEY,
                    feature    TEXT NOT NULL,
                    digest     TEXT NOT NULL,
                    request    TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    session    TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    approved   INTEGER NOT NULL DEFAULT 0,
                    consumed   INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {
                row[1] for row in self._connection.execute("PRAGMA table_info(capabilities)")
            }
            if "approved" not in columns:
                self._connection.execute(
                    "ALTER TABLE capabilities ADD COLUMN approved INTEGER NOT NULL DEFAULT 0"
                )
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS audit (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    at         REAL NOT NULL,
                    feature    TEXT NOT NULL,
                    action     TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    digest     TEXT NOT NULL,
                    returncode INTEGER,
                    duration   REAL,
                    out_len    INTEGER,
                    truncated  INTEGER
                )
            """)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def issue(self, *, feature: str, digest: str, request: str, user_id: str,
              session: str, generation: str) -> str:
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._connection.execute(
                "INSERT INTO capabilities (nonce, feature, digest, request, user_id, session,"
                " generation, expires_at, approved, consumed) VALUES (?,?,?,?,?,?,?,?,0,0)",
                (nonce, feature, digest, request, user_id, session, generation,
                 time.time() + CAPABILITY_TTL_SECONDS),
            )
            self._purge_expired()
        return nonce

    def approve(self, *, nonce: str, feature: str, digest: str, user_id: str,
                session: str, generation: str) -> None:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE capabilities SET approved = 1 WHERE nonce = ? AND feature = ?"
                " AND digest = ? AND user_id = ? AND session = ? AND generation = ?"
                " AND approved = 0 AND consumed = 0 AND expires_at > ?",
                (nonce, feature, digest, user_id, session, generation, time.time()),
            )
            if cursor.rowcount != 1:
                raise CapabilityError(
                    "The approval grant is unknown, expired, mismatched, or already resolved."
                )

    def cancel_bound(self, *, nonce: str, feature: str, digest: str, user_id: str,
                     session: str, generation: str) -> None:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE capabilities SET consumed = 1 WHERE nonce = ? AND feature = ?"
                " AND digest = ? AND user_id = ? AND session = ? AND generation = ?"
                " AND approved = 0 AND consumed = 0 AND expires_at > ?",
                (nonce, feature, digest, user_id, session, generation, time.time()),
            )
            if cursor.rowcount != 1:
                raise CapabilityError(
                    "The denial is unknown, expired, mismatched, or already resolved."
                )

    def consume(self, *, nonce: str, feature: str, digest: str, user_id: str,
                session: str, generation: str, wait_seconds: float = 0) -> dict[str, Any]:
        """Wait for and atomically claim one fully bound, independently approved capability."""
        deadline = time.monotonic() + max(0, wait_seconds)
        while True:
            with self._lock:
                now = time.time()
                cursor = self._connection.execute(
                    "UPDATE capabilities SET consumed = 1 WHERE nonce = ? AND feature = ?"
                    " AND digest = ? AND user_id = ? AND session = ? AND generation = ?"
                    " AND approved = 1 AND consumed = 0 AND expires_at > ?"
                    " RETURNING feature, digest, request, user_id, session, generation",
                    (nonce, feature, digest, user_id, session, generation, now),
                )
                row = cursor.fetchone()
                if row is not None:
                    stored_feature, stored_digest, request, _, _, _ = row
                    return {"feature": stored_feature, "digest": stored_digest,
                            "request": request}
                state = self._connection.execute(
                    "SELECT feature,digest,user_id,session,generation,expires_at,approved,consumed"
                    " FROM capabilities WHERE nonce = ?", (nonce,),
                ).fetchone()
            bound = state is not None and state[:5] == (
                feature, digest, user_id, session, generation
            )
            pending = bound and state[7] == 0 and state[6] == 0 and state[5] > now
            remaining = deadline - time.monotonic()
            if not pending or remaining <= 0:
                raise CapabilityError(
                    "This operation is not independently approved, expired, mismatched, already "
                    "executed, or was cancelled."
                )
            time.sleep(min(0.25, remaining, max(0, state[5] - now)))

    def cancel(self, nonce: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE capabilities SET consumed = 1 WHERE nonce = ?", (nonce,)
            )

    def cancel_generation(self, generation: str) -> None:
        """Revoke everything prepared under a superseded policy."""
        with self._lock:
            self._connection.execute(
                "UPDATE capabilities SET consumed = 1 WHERE generation != ?", (generation,)
            )

    def record(self, *, feature: str, action: str, user_id: str, digest: str,
               returncode: int | None, duration: float, out_len: int, truncated: bool) -> None:
        # Deliberately stores no command text, output, or secret value.
        with self._lock:
            self._connection.execute(
                "INSERT INTO audit (at, feature, action, user_id, digest, returncode, duration,"
                " out_len, truncated) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), feature, action, user_id, digest, returncode, duration,
                 out_len, int(truncated)),
            )

    def _purge_expired(self) -> None:
        self._connection.execute(
            "DELETE FROM capabilities WHERE expires_at < ?", (time.time() - 3_600,)
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
