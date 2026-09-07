from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select

from .control_db import ApiKey, ControlDB, ExternalIdentity, RevokedSession, User


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {"*"},
    "admin": {"panel.read", "panel.write", "routing.use", "routing.manage", "users.manage", "keys.manage", "budgets.manage", "policies.manage", "knowledge.read", "knowledge.manage", "agents.run", "agents.manage", "plugins.manage", "audit.read", "acls.read", "acls.manage"},
    "operator": {"panel.read", "routing.use", "routing.manage", "knowledge.read", "agents.run", "audit.read", "acls.read"},
    "analyst": {"panel.read", "routing.use", "knowledge.read", "audit.read", "acls.read"},
    "approver": {"panel.read", "routing.use", "audit.read", "approvals.manage"},
    "agent": {"routing.use", "knowledge.read", "agents.run"},
    "user": {"routing.use", "knowledge.read", "agents.run"},
    "read_only": {"panel.read", "knowledge.read", "audit.read"},
}


@dataclass(frozen=True)
class Identity:
    actor: str
    role: str
    team: str = "default"
    user_id: int | None = None
    api_key_id: int | None = None
    rpm: int = 60
    tpm: int = 2000000
    daily_requests: int = 5000
    monthly_budget_usd: float = 0.0
    allowed_tiers: tuple[str, ...] = ("fast", "standard", "strong")

    def can(self, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in permissions or permission in permissions


class SecurityManager:
    def __init__(self, db: ControlDB, hmac_secret: str, admin_api_key: str | None = None, session_ttl: int = 8 * 3600):
        self.db = db
        self.secret = hmac_secret.encode()
        self.admin_api_key = (admin_api_key or "").strip()
        self.session_ttl = session_ttl

    @staticmethod
    def hash_password(password: str, salt: bytes | None = None) -> str:
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        salt = salt or os.urandom(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        try:
            scheme, salt_b64, digest_b64 = stored.split("$", 2)
            if scheme != "scrypt":
                return False
            salt = base64.urlsafe_b64decode(salt_b64)
            expected = base64.urlsafe_b64decode(digest_b64)
            actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    def bootstrap_admin(self, username: str, password: str | None) -> None:
        if not password:
            return
        with self.db.session() as session:
            existing = session.scalar(select(User).where(User.username == username))
            if existing is None:
                session.add(User(username=username, password_hash=self.hash_password(password), role="super_admin", team="admins"))
                session.commit()
                self.db.audit("system", "system", "bootstrap.admin", username)

    def login(self, username: str, password: str) -> str | None:
        with self.db.session() as session:
            user = session.scalar(select(User).where(User.username == username, User.active.is_(True)))
            if not user or not self.verify_password(password, user.password_hash):
                self.db.audit(username or "unknown", "anonymous", "auth.login", status="denied")
                return None
            token = self.issue_session(user)
            self.db.audit(user.username, user.role, "auth.login")
            return token

    def issue_session(self, user: User) -> str:
        payload = {
            "sub": user.username,
            "uid": user.id,
            "role": user.role,
            "team": user.team,
            "exp": int(time.time()) + self.session_ttl,
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        return f"v52.{body}.{sig}"

    def session_identity(self, token: str) -> Identity | None:
        try:
            prefix, body, sig = token.split(".", 2)
            if prefix not in {"v51", "v52"}:
                return None
            expected = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(_pad(body)))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            digest = hashlib.sha256(token.encode()).hexdigest()
            with self.db.session() as session:
                if session.get(RevokedSession, digest) is not None:
                    return None
                user = session.get(User, int(payload["uid"]))
                if not user or not user.active:
                    return None
                return Identity(actor=user.username, user_id=user.id, role=user.role, team=user.team)
        except Exception:
            return None

    def api_key_identity(self, token: str) -> Identity | None:
        if not token:
            return None
        if self.admin_api_key and hmac.compare_digest(token, self.admin_api_key):
            return Identity(actor="bootstrap-admin", role="super_admin", team="admins")
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.db.session() as session:
            row = session.scalar(select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.active.is_(True)))
            if not row:
                return None
            if row.expires_at and row.expires_at < now:
                return None
            try:
                allowed = tuple(json.loads(row.allowed_tiers_json))
            except Exception:
                allowed = ("fast", "standard", "strong")
            actor = row.name
            if row.user_id:
                user = session.get(User, row.user_id)
                if user and user.active:
                    actor = user.username
            return Identity(actor=actor, user_id=row.user_id, api_key_id=row.id, role=row.role, team=row.team, rpm=row.rpm, tpm=row.tpm, daily_requests=row.daily_requests, monthly_budget_usd=row.monthly_budget_usd, allowed_tiers=allowed)

    def revoke_session(self, token: str) -> None:
        if not token.startswith(("v51.", "v52.")):
            return
        digest = hashlib.sha256(token.encode()).hexdigest()
        try:
            payload = json.loads(base64.urlsafe_b64decode(_pad(token.split(".", 2)[1])))
            expires = int(payload.get("exp", int(time.time()) + self.session_ttl))
        except Exception:
            expires = int(time.time()) + self.session_ttl
        with self.db.session() as session:
            if session.get(RevokedSession, digest) is None:
                session.add(RevokedSession(token_hash=digest, expires_at=expires))
            # Opportunistic cleanup keeps the denylist bounded without a scheduler.
            for row in session.scalars(select(RevokedSession).where(RevokedSession.expires_at < int(time.time()))).all():
                session.delete(row)
            session.commit()

    def provision_external(self, provider: str, subject: str, username: str, groups: list[str], role: str, auto_provision: bool = True) -> tuple[User, str]:
        if role not in ROLE_PERMISSIONS:
            role = "user"
        safe = "".join(ch if ch.isalnum() or ch in "._-@" else "-" for ch in username).strip("-") or "oidc-user"
        with self.db.session() as session:
            link = session.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == provider, ExternalIdentity.subject == subject))
            if link is not None:
                user = session.get(User, link.user_id)
                if not user or not user.active:
                    raise PermissionError("external identity is disabled")
                user.role = role
                link.groups_json = json.dumps(groups, separators=(",", ":"))
                link.last_login_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(user)
            else:
                if not auto_provision:
                    raise PermissionError("external identity is not pre-provisioned")
                candidate = safe[:120]
                suffix = 1
                while session.scalar(select(User).where(User.username == candidate)) is not None:
                    suffix += 1
                    candidate = (safe[: max(1, 115-len(str(suffix)))] + f"-{suffix}")[:120]
                user = User(username=candidate, password_hash="external-only", role=role, team="default")
                session.add(user)
                session.flush()
                session.add(ExternalIdentity(provider=provider, subject=subject, user_id=user.id, groups_json=json.dumps(groups, separators=(",", ":"))))
                session.commit()
                session.refresh(user)
        token = self.issue_session(user)
        self.db.audit(user.username, user.role, "auth.oidc.login", provider, detail={"groups": groups})
        return user, token

    def create_api_key(self, name: str, role: str, team: str, user_id: int | None, rpm: int, tpm: int, daily_requests: int, monthly_budget_usd: float, allowed_tiers: Iterable[str], expires_at: str | None = None) -> tuple[ApiKey, str]:
        if role not in ROLE_PERMISSIONS:
            raise ValueError("unknown role")
        token = "srk_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.session() as session:
            row = ApiKey(name=name, key_hash=digest, prefix=token[:12], user_id=user_id, role=role, team=team, rpm=max(1, rpm), tpm=max(1000, tpm), daily_requests=max(1, daily_requests), monthly_budget_usd=max(0.0, monthly_budget_usd), allowed_tiers_json=json.dumps(sorted(set(allowed_tiers))), expires_at=expires_at)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row, token


def bearer(headers) -> str:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key", "").strip()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _pad(data: str) -> bytes:
    return (data + "=" * (-len(data) % 4)).encode()
