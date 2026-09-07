import json

import pytest

from smart_router.observations import ObservationWriter
from smart_router.privacy import privacy_safe_json, session_identity


def test_session_id_is_hmac_not_raw(settings):
    digest, source = session_identity({"x-router-session": "secret-session"}, {}, settings.hmac_secret)
    assert source == "x-router-session"
    assert digest and digest != "secret-session"


def test_observation_rejects_raw_sensitive_shapes():
    with pytest.raises(ValueError):
        privacy_safe_json({"messages": [{"content": "secret"}]})
    with pytest.raises(ValueError):
        privacy_safe_json({"tool_arguments": {"password": "secret"}})


def test_observation_writer_keeps_only_derived_data(tmp_path):
    path = tmp_path / "obs.jsonl"
    ObservationWriter(str(path)).write({"policy": "learned", "confidence": 0.8, "probabilities": {"fast": 0.1, "standard": 0.8, "strong": 0.1}})
    row = json.loads(path.read_text())
    assert row["policy"] == "learned"
    assert "messages" not in row
