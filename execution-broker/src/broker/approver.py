"""Independent Telegram approval boundary for sealed execution capabilities."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import approval


class ApproverError(ValueError):
    """An invalid, unauthorized, or already resolved approval request."""


class ApprovalRequestStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), isolation_level=None,
                                           check_same_thread=False)
        self._lock = threading.Lock()
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                capability TEXT PRIMARY KEY,
                callback_token TEXT UNIQUE NOT NULL,
                target TEXT NOT NULL,
                feature TEXT NOT NULL,
                digest TEXT NOT NULL,
                request_json TEXT NOT NULL,
                summary TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session TEXT NOT NULL,
                generation TEXT NOT NULL,
                expires_at REAL NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending',
                delivered INTEGER NOT NULL DEFAULT 0,
                granted INTEGER NOT NULL DEFAULT 0
            )
        """)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def create(self, payload: dict[str, Any], *, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._connection.execute(
                "INSERT INTO approval_requests (capability,callback_token,target,feature,digest,"
                "request_json,summary,user_id,session,generation,expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (payload["capability"], token, payload["target"], payload["feature"],
                 payload["digest"], "{}",
                 payload["summary"], payload["user_id"], payload["session"],
                 payload["generation"], time.time() + ttl_seconds),
            )
        return token

    def mark_delivered(self, capability: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE approval_requests SET delivered=1 WHERE capability=?", (capability,)
            )

    def discard(self, capability: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM approval_requests WHERE capability=? AND decision='pending'",
                (capability,),
            )

    def resolve(self, token: str, decision: str, user_id: str) -> dict[str, Any]:
        if decision not in ("approved", "denied"):
            raise ApproverError("Invalid approval decision.")
        with self._lock:
            row = self._connection.execute(
                "UPDATE approval_requests SET decision=? WHERE callback_token=? AND user_id=?"
                " AND decision='pending' AND delivered=1 AND expires_at>?"
                " RETURNING capability,target,feature,digest,user_id,session,generation,summary",
                (decision, token, user_id, time.time()),
            ).fetchone()
        if row is None:
            raise ApproverError("This approval is unknown, expired, mismatched, or already resolved.")
        keys = ("capability", "target", "feature", "digest", "user_id", "session",
                "generation", "summary")
        result = dict(zip(keys, row))
        result["decision"] = decision
        return result

    def unresolved_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT capability,target,feature,digest,user_id,session,generation,decision"
                " FROM approval_requests WHERE decision!='pending' AND granted=0"
                " AND expires_at>? ORDER BY expires_at LIMIT 100", (time.time(),)
            ).fetchall()
        keys = ("capability", "target", "feature", "digest", "user_id", "session",
                "generation", "decision")
        return [dict(zip(keys, row)) for row in rows]

    def mark_granted(self, capability: str, decision: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE approval_requests SET granted=1 WHERE capability=? AND decision=?",
                (capability, decision),
            )


class TelegramApprover:
    def __init__(self, *, token_file: Path, users_file: Path,
                 store: ApprovalRequestStore,
                 decision_sender: Callable[[dict[str, Any]], bool]):
        self._token_file = token_file
        self._users_file = users_file
        self._store = store
        self._decision_sender = decision_sender
        self._offset = 0

    def _token(self) -> str:
        try:
            return self._token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def users(self) -> frozenset[str]:
        try:
            value = self._users_file.read_text(encoding="utf-8").strip()
        except OSError:
            return frozenset()
        users = value.split(",") if value else []
        if any(not user.isdigit() for user in users):
            return frozenset()
        return frozenset(users)

    def configured(self) -> bool:
        token = self._token()
        return bool(token and ":" in token and self.users())

    def _api(self, method: str, payload: dict[str, Any], timeout: float = 35) -> Any:
        token = self._token()
        if not token:
            raise ApproverError("The dedicated execution approval bot is not configured.")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=urllib.parse.urlencode({
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (dict, list)) else str(value)
                for key, value in payload.items()
            }).encode("utf-8"), method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ApproverError(
                f"Telegram approval bot request failed: {type(exc).__name__}."
            ) from exc
        if not result.get("ok"):
            raise ApproverError("Telegram rejected the approval bot request.")
        return result.get("result")

    def submit(self, payload: dict[str, Any], *, ttl_seconds: int) -> None:
        user_id = payload["user_id"]
        if user_id not in self.users():
            raise ApproverError("The requested Telegram user is not an execution approver.")
        token = self._store.create(payload, ttl_seconds=ttl_seconds)
        keyboard = {"inline_keyboard": [[
            {"text": "Approve once", "callback_data": f"exec:{token}:approve"},
            {"text": "Deny", "callback_data": f"exec:{token}:deny"},
        ]]}
        text = ("Execution approval requested\n\n" + payload["summary"] + "\n\n"
                + f"Request digest: {payload['digest']}\n"
                + "This button can resolve this exact operation once.")
        try:
            self._api("sendMessage", {"chat_id": user_id, "text": text,
                      "reply_markup": keyboard, "disable_web_page_preview": "true"})
        except Exception:
            self._store.discard(payload["capability"])
            raise
        self._store.mark_delivered(payload["capability"])

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        sender = str((callback.get("from") or {}).get("id", ""))
        chat = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
        parts = str(callback.get("data", "")).split(":")
        if len(parts) != 3 or parts[0] != "exec" or parts[2] not in ("approve", "deny"):
            return
        try:
            if not sender.isdigit() or sender != chat or sender not in self.users():
                raise ApproverError("This approval belongs to another authorized private chat.")
            decision = "approved" if parts[2] == "approve" else "denied"
            grant = self._store.resolve(parts[1], decision, sender)
            delivered = self._decision_sender(grant)
            if delivered:
                self._store.mark_granted(grant["capability"], decision)
            self._api("answerCallbackQuery", {"callback_query_id": callback_id,
                      "text": "Approved once." if decision == "approved" else "Denied.",
                      "show_alert": "true"}, timeout=10)
            message = callback.get("message") or {}
            if message.get("message_id") is not None:
                self._api("editMessageReplyMarkup", {"chat_id": sender,
                          "message_id": message["message_id"],
                          "reply_markup": {"inline_keyboard": []}}, timeout=10)
        except ApproverError as exc:
            if callback_id:
                try:
                    self._api("answerCallbackQuery", {"callback_query_id": callback_id,
                              "text": str(exc)[:180], "show_alert": "true"}, timeout=10)
                except ApproverError:
                    pass

    def run(self) -> None:
        while True:
            try:
                # Telegram disables getUpdates while a webhook exists. This bot is a
                # dedicated polling approver, so remove stale webhook configuration
                # without discarding a callback that may already be queued.
                self._api("deleteWebhook", {"drop_pending_updates": "false"}, timeout=10)
                break
            except ApproverError:
                time.sleep(2)
        while True:
            try:
                updates = self._api("getUpdates", {"offset": self._offset, "timeout": 25,
                                    "allowed_updates": ["callback_query"]}, timeout=35) or []
                for update in updates:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    callback = update.get("callback_query")
                    if isinstance(callback, dict):
                        self._handle_callback(callback)
                for grant in self._store.unresolved_decisions():
                    if self._decision_sender(grant):
                        self._store.mark_granted(grant["capability"], grant["decision"])
            except (ApproverError, OSError, ValueError):
                time.sleep(2)


def validate_submission(payload: dict[str, Any], *, generation: str) -> dict[str, Any]:
    allowed = {"target", "feature", "capability", "digest", "request", "summary",
               "user_id", "session", "generation"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ApproverError("The approval request has unsupported or missing fields.")
    target, feature = payload["target"], payload["feature"]
    if target not in ("docker", "ssh") or feature not in ("local", "docker", "ssh"):
        raise ApproverError("The approval request target or feature is invalid.")
    if (target == "docker") != (feature in ("local", "docker")):
        raise ApproverError("The approval request targets the wrong execution broker.")
    if payload["generation"] != generation:
        raise ApproverError("The approval request uses a superseded policy generation.")
    if not isinstance(payload["request"], dict):
        raise ApproverError("The sealed operation is missing.")
    if not isinstance(payload["capability"], str) or len(payload["capability"]) < 32:
        raise ApproverError("The capability is invalid.")
    if not isinstance(payload["digest"], str) or len(payload["digest"]) != 64:
        raise ApproverError("The operation digest is invalid.")
    if not isinstance(payload["user_id"], str) or not payload["user_id"].isdigit():
        raise ApproverError("A numeric Telegram execution user is required.")
    if not isinstance(payload["session"], str) or not payload["session"]:
        raise ApproverError("The execution session is missing.")
    approval.check_floor(feature, payload["request"])
    expected_digest = approval.canonical_digest(feature, payload["request"])
    expected_summary = approval.render_summary(feature, payload["request"])
    if payload["digest"] != expected_digest or payload["summary"] != expected_summary:
        raise ApproverError("The exact operation digest or summary does not match.")
    return payload
