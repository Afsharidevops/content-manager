from pathlib import Path

from smart_router.control_db import ControlDB
from smart_router.knowledge_v51 import KnowledgeManager
from smart_router.security_v51 import Identity


def test_identity_default_tpm_supports_long_tool_sessions():
    assert Identity("client", "operator").tpm >= 2_000_000


def test_knowledge_can_use_separate_sqlite_database(tmp_path: Path):
    control = ControlDB(f"sqlite:///{tmp_path / 'control.sqlite3'}")
    knowledge_url = f"sqlite:///{tmp_path / 'knowledge.sqlite3'}"
    km = KnowledgeManager(control, knowledge_url)
    assert km.storage_mode == "external"
    assert km.ping()
    kb = km.create_base("docs", "test", "admin")
    assert km.add_document(kb.id, "runbook", "Runbook", "nginx service restart procedure") == 1
    hits = km.search([kb.id], "nginx restart")
    assert hits and hits[0]["source"] == "runbook"


def test_knowledge_defaults_to_control_database(tmp_path: Path):
    control = ControlDB(f"sqlite:///{tmp_path / 'control.sqlite3'}")
    km = KnowledgeManager(control)
    assert km.storage_mode == "control"
    assert km.database_url == control.url
