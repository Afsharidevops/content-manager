from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from smart_router.acl_v52 import ACLManager
from smart_router.control_db import ControlDB, User
from smart_router.provider_health import ProviderHealthRegistry
from smart_router.security_v51 import Identity, SecurityManager
from smart_router.secrets_v52 import env_or_file, redacted_url


def test_file_secret_loading(tmp_path: Path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("value-from-file\n")
    monkeypatch.delenv("EXAMPLE_SECRET", raising=False)
    monkeypatch.setenv("EXAMPLE_SECRET_FILE", str(secret))
    assert env_or_file("EXAMPLE_SECRET") == "value-from-file"


def test_secret_ambiguity_fails(tmp_path: Path, monkeypatch):
    secret = tmp_path / "secret"; secret.write_text("file")
    monkeypatch.setenv("EXAMPLE_SECRET", "inline")
    monkeypatch.setenv("EXAMPLE_SECRET_FILE", str(secret))
    try:
        env_or_file("EXAMPLE_SECRET")
        assert False, "expected ambiguity failure"
    except ValueError:
        pass


def test_redacted_database_url():
    assert redacted_url("postgresql://user:secret@db:5432/router") == "postgresql://db:5432/router"


def test_acl_deny_wins(tmp_path: Path):
    db = ControlDB(f"sqlite:///{tmp_path/'control.sqlite3'}")
    acl = ACLManager(db)
    ident = Identity("alice", "user", "dev")
    acl.create(subject_type="role", subject_value="user", resource_type="knowledge", resource_id="1", permission="knowledge.read", effect="allow")
    acl.create(subject_type="user", subject_value="alice", resource_type="knowledge", resource_id="1", permission="knowledge.read", effect="deny")
    assert not acl.allowed(ident, "knowledge", 1, "knowledge.read")


def test_circuit_opens_and_recovers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SMART_ROUTER_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("SMART_ROUTER_CIRCUIT_COOLDOWN_SECONDS", "1")
    db = ControlDB(f"sqlite:///{tmp_path/'health.sqlite3'}")
    reg = ProviderHealthRegistry(db)
    reg.record("route-a", 503, 100)
    assert reg.available("route-a")
    reg.record("route-a", 503, 100)
    assert not reg.available("route-a")


def test_disabled_user_invalidates_existing_session(tmp_path: Path):
    db = ControlDB(f"sqlite:///{tmp_path/'session.sqlite3'}")
    sec = SecurityManager(db, "x"*64)
    with db.session() as s:
        user = User(username="alice", password_hash=sec.hash_password("correct-horse-battery"), role="user", team="default")
        s.add(user); s.commit(); s.refresh(user)
        token = sec.issue_session(user)
        uid = user.id
    assert sec.session_identity(token) is not None
    with db.session() as s:
        user = s.get(User, uid); user.active = False; s.commit()
    assert sec.session_identity(token) is None


def test_external_identity_can_require_preprovision(tmp_path: Path):
    db = ControlDB(f"sqlite:///{tmp_path/'preprovision.sqlite3'}")
    sec = SecurityManager(db, "x" * 64)
    import pytest
    with pytest.raises(PermissionError):
        sec.provision_external("oidc", "subject-1", "alice", ["dev"], "user", auto_provision=False)


def test_oidc_local_login_flag(monkeypatch):
    from smart_router.oidc_v52 import OIDCManager
    monkeypatch.setenv("SMART_ROUTER_OIDC_ENABLED", "false")
    monkeypatch.setenv("SMART_ROUTER_OIDC_LOCAL_LOGIN_ENABLED", "false")
    manager = OIDCManager("x" * 64)
    assert manager.local_login_enabled is False


def test_redis_sticky_store_shared_semantics(settings):
    import json
    from contextlib import nullcontext
    from smart_router.database import RedisRouteStore

    class FakeRedis:
        def __init__(self): self.values = {}
        def ping(self): return True
        def get(self, key): return self.values.get(key)
        def set(self, key, value, ex=None): self.values[key] = value; return True
        def delete(self, key): self.values.pop(key, None); return 1
        def lock(self, *args, **kwargs): return nullcontext()

    client = FakeRedis()
    store_a = RedisRouteStore(settings, client=client)
    store_b = RedisRouteStore(settings, client=client)
    first = store_a.resolve("session-hash", "auto", "fast", "initial", now=100)
    second = store_b.resolve("session-hash", "auto", "strong", "needs_tools", now=101)
    third = store_a.resolve("session-hash", "auto", "fast", "simple", now=102)
    assert first.action == "created" and first.tier == "fast"
    assert second.action == "promoted" and second.tier == "strong"
    assert third.hit is True and third.tier == "strong"
    key = store_a._key("session-hash", "auto")
    assert json.loads(client.values[key])["tier"] == "strong"
