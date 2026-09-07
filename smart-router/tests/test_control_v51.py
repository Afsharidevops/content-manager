from types import SimpleNamespace

from smart_router.control_db import ControlDB
from smart_router.knowledge_v51 import KnowledgeManager
from smart_router.policy_v51 import PolicyEngine
from smart_router.security_v51 import Identity, SecurityManager


def test_security_and_key(tmp_path):
    db = ControlDB(f"sqlite:///{tmp_path/'c.db'}")
    sec = SecurityManager(db, "x" * 64)
    sec.bootstrap_admin("admin", "a-very-strong-password")
    token = sec.login("admin", "a-very-strong-password")
    assert token
    identity = sec.session_identity(token)
    assert identity and identity.role == "super_admin"
    _, key = sec.create_api_key("ci", "agent", "dev", None, 10, 10000, 100, 5, ["fast", "standard"])
    got = sec.api_key_identity(key)
    assert got and got.team == "dev" and "strong" not in got.allowed_tiers


def test_knowledge_search(tmp_path):
    db = ControlDB(f"sqlite:///{tmp_path/'k.db'}")
    km = KnowledgeManager(db)
    kb = km.create_base("runbooks", "", "admin")
    km.add_document(kb.id, "runbook.md", "Nginx", "Restart nginx only after checking configuration with nginx -t. Kubernetes uses rollout restart.")
    hits = km.search([kb.id], "nginx configuration restart", 3)
    assert hits and "nginx" in hits[0]["content"].lower()

from types import SimpleNamespace
from starlette.requests import Request
from smart_router.control_plane import ControlPlane


def _settings(tmp_path, client_api_key="legacy-secret"):
    tier = lambda model: SimpleNamespace(model=model)
    return SimpleNamespace(
        hmac_secret="h" * 48,
        client_api_key=client_api_key,
        fast=tier("fast-model"), standard=tier("standard-model"), strong=tier("strong-model"),
        upstream_base_url="http://gateway.invalid/v1", upstream_health_url="http://gateway.invalid/health",
        upstream_api_key="", mode="route", policy="heuristic", allow_tier_overrides=False,
        read_timeout_seconds=30,
    )


def _request(headers=None):
    headers = headers or []
    scope = {"type":"http","method":"POST","path":"/v1/chat/completions","headers":[(k.lower().encode(), v.encode()) for k,v in headers]}
    return Request(scope)


def test_control_plane_accepts_legacy_client_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{tmp_path/'control.sqlite3'}")
    cp = ControlPlane(_settings(tmp_path))
    req = _request([("authorization", "Bearer legacy-secret")])
    ident = cp.authenticate_api_request(req)
    assert ident is not None
    assert ident.actor == "legacy-client"
    assert ident.tpm == 2_000_000
    assert ident.can("routing.use")


def test_control_plane_profile_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{tmp_path/'control.sqlite3'}")
    cp = ControlPlane(_settings(tmp_path, client_api_key=""))
    req = _request()
    req.state.v51_identity = __import__("smart_router.security_v51", fromlist=["Identity"]).Identity("admin", "super_admin")
    body = {"model":"auto", "messages":[{"role":"user","content":"Write a Python Kubernetes controller"}]}
    result = cp.finalize_routing(req, body, "standard", "standard-model", None)
    assert result.error is None
    assert result.profile == "coding"
    assert result.model == "strong-model"
