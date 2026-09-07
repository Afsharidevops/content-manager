from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from smart_router.config import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SMART_ROUTER_HMAC_SECRET", "test-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    monkeypatch.setenv("SMART_ROUTER_UPSTREAM_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("SMART_ROUTER_UPSTREAM_HEALTH_URL", "http://upstream.test/health")
    monkeypatch.setenv("SMART_ROUTER_DATABASE_PATH", str(tmp_path / "router.sqlite3"))
    monkeypatch.setenv("SMART_ROUTER_CALIBRATION_FILE", str(tmp_path / "missing-calibration.json"))
    monkeypatch.setenv("SMART_ROUTER_LEARNED_MODEL_FILE", str(tmp_path / "learned.joblib"))
    monkeypatch.setenv("SMART_ROUTER_LEARNED_METADATA_FILE", str(tmp_path / "learned.json"))
    monkeypatch.setenv("SMART_ROUTER_MODE", "route")
    return Settings.from_env()


@pytest.fixture
def learned_settings(settings: Settings) -> Settings:
    return replace(settings, policy="learned")
