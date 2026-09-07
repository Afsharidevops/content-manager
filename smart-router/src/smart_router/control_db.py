from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "v51_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(40), default="user", index=True)
    team: Mapped[str] = mapped_column(String(120), default="default", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class AccessGroup(Base):
    __tablename__ = "v55_access_groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    member_users_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class ApiKey(Base):
    __tablename__ = "v51_api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(40), default="user")
    team: Mapped[str] = mapped_column(String(120), default="default")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rpm: Mapped[int] = mapped_column(Integer, default=60)
    tpm: Mapped[int] = mapped_column(Integer, default=2000000)
    daily_requests: Mapped[int] = mapped_column(Integer, default=5000)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=0.0)
    allowed_tiers_json: Mapped[str] = mapped_column(Text, default='["fast","standard","strong"]')
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RouteProfile(Base):
    __tablename__ = "v51_route_profiles"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    model: Mapped[str] = mapped_column(String(240))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_tier: Mapped[str] = mapped_column(String(20), default="fast")
    max_output: Mapped[int] = mapped_column(Integer, default=4096)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Budget(Base):
    __tablename__ = "v51_budgets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(30), index=True)
    scope_value: Mapped[str] = mapped_column(String(160), index=True)
    monthly_usd: Mapped[float] = mapped_column(Float, default=0.0)
    warning_percent: Mapped[float] = mapped_column(Float, default=80.0)
    hard_stop_percent: Mapped[float] = mapped_column(Float, default=100.0)
    action: Mapped[str] = mapped_column(String(40), default="downgrade")
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "v51_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(160), default="anonymous", index=True)
    role: Mapped[str] = mapped_column(String(40), default="anonymous")
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource: Mapped[str] = mapped_column(String(240), default="")
    status: Mapped[str] = mapped_column(String(40), default="ok")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class RouteEvent(Base):
    __tablename__ = "v51_route_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(160), default="anonymous", index=True)
    team: Mapped[str] = mapped_column(String(120), default="default", index=True)
    tier: Mapped[str] = mapped_column(String(30), index=True)
    profile: Mapped[str] = mapped_column(String(30), index=True)
    model: Mapped[str] = mapped_column(String(240), index=True)
    policy: Mapped[str] = mapped_column(String(80), default="heuristic")
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    reason_json: Mapped[str] = mapped_column(Text, default="[]")
    request_id: Mapped[str] = mapped_column(String(80), default="", index=True)


class Policy(Base):
    __tablename__ = "v51_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    rule_json: Mapped[str] = mapped_column(Text, default="{}")
    action_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class KnowledgeBase(Base):
    __tablename__ = "v51_knowledge_bases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(160), default="admin")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "v51_knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(500), default="manual")
    title: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class KnowledgeEmbedding(Base):
    __tablename__ = "v56_knowledge_embeddings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    kb_id: Mapped[int] = mapped_column(Integer, index=True)
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    dimensions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RequestTrace(Base):
    __tablename__ = "v56_request_traces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(80), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[str] = mapped_column(String(40), default=utcnow, index=True)
    stage: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ok")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class GuardrailRule(Base):
    __tablename__ = "v56_guardrail_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(50), default="content")
    action: Mapped[str] = mapped_column(String(30), default="audit")
    pattern: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RouterPipeline(Base):
    __tablename__ = "v56_router_pipelines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class KnowledgePipeline(Base):
    __tablename__ = "v58_knowledge_pipelines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    graph_json: Mapped[str] = mapped_column(Text, default='{"nodes":[],"edges":[]}')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Workflow(Base):
    __tablename__ = "v56_workflows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    workflow_type: Mapped[str] = mapped_column(String(40), default="agent_team")
    graph_json: Mapped[str] = mapped_column(Text, default='{"nodes":[],"edges":[]}')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class PromptVersion(Base):
    __tablename__ = "v56_prompt_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class EvalDataset(Base):
    __tablename__ = "v56_eval_datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class EvalDatasetItem(Base):
    __tablename__ = "v56_eval_dataset_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(Integer, index=True)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    expected_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class EvalRun(Base):
    __tablename__ = "v56_eval_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(160), default="evaluation")
    variant_a: Mapped[str] = mapped_column(String(160), default="")
    variant_b: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class ModelCatalogEntry(Base):
    __tablename__ = "v56_model_catalog"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(120), default="upstream", index=True)
    model: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    context_limit: Mapped[int] = mapped_column(Integer, default=0)
    output_limit: Mapped[int] = mapped_column(Integer, default=0)
    input_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0)
    output_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    health: Mapped[str] = mapped_column(String(30), default="unknown")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Memory(Base):
    __tablename__ = "v51_memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(30), index=True)
    scope_value: Mapped[str] = mapped_column(String(160), index=True)
    key: Mapped[str] = mapped_column(String(180), index=True)
    value: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Agent(Base):
    __tablename__ = "v51_agents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    tier: Mapped[str] = mapped_column(String(30), default="auto")
    profile: Mapped[str] = mapped_column(String(30), default="auto")
    knowledge_json: Mapped[str] = mapped_column(Text, default="[]")
    plugins_json: Mapped[str] = mapped_column(Text, default="[]")
    permissions_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class AgentGraph(Base):
    __tablename__ = "v59_agent_graphs"
    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_json: Mapped[str] = mapped_column(Text, default='{"nodes":[],"edges":[]}')
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Team(Base):
    __tablename__ = "v51_agent_teams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    strategy: Mapped[str] = mapped_column(String(30), default="sequential")
    agent_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    synthesis_tier: Mapped[str] = mapped_column(String(30), default="strong")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Plugin(Base):
    __tablename__ = "v51_plugins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(40), default="mcp")
    description: Mapped[str] = mapped_column(Text, default="")
    endpoint: Mapped[str] = mapped_column(Text, default="")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    risk: Mapped[str] = mapped_column(String(30), default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Skill(Base):
    __tablename__ = "v55_skills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="general", index=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    commercial: Mapped[bool] = mapped_column(Boolean, default=False)
    license_note: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class AgentSkillLink(Base):
    __tablename__ = "v55_agent_skills"
    agent_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RateCounter(Base):
    __tablename__ = "v51_rate_counters"
    key: Mapped[str] = mapped_column(String(240), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    component: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), default="0.5.2")
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RuntimeSetting(Base):
    __tablename__ = "v55_runtime_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="null")
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class ExternalIdentity(Base):
    __tablename__ = "v52_external_identities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    subject: Mapped[str] = mapped_column(String(240), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    groups_json: Mapped[str] = mapped_column(Text, default="[]")
    last_login_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class RevokedSession(Base):
    __tablename__ = "v52_revoked_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[int] = mapped_column(Integer, default=0, index=True)


class ACLRule(Base):
    __tablename__ = "v52_acl_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_type: Mapped[str] = mapped_column(String(40), index=True)
    subject_value: Mapped[str] = mapped_column(String(180), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(180), index=True)
    permission: Mapped[str] = mapped_column(String(120), index=True)
    effect: Mapped[str] = mapped_column(String(16), default="allow")
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class ProviderHealthState(Base):
    __tablename__ = "v52_provider_health"
    model: Mapped[str] = mapped_column(String(240), primary_key=True)
    state: Mapped[str] = mapped_column(String(30), default="HEALTHY", index=True)
    total_requests: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latency_ema_ms: Mapped[float] = mapped_column(Float, default=0.0)
    circuit_open_until: Mapped[float] = mapped_column(Float, default=0.0)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failure_at: Mapped[str] = mapped_column(String(40), default="")
    last_success_at: Mapped[str] = mapped_column(String(40), default="")
    last_recovery_at: Mapped[str] = mapped_column(String(40), default="")


class OutcomeEvent(Base):
    __tablename__ = "v52_outcome_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40), default=utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    actor: Mapped[str] = mapped_column(String(160), default="anonymous", index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tool_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    execution_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fallback_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    manually_changed_tier: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class ControlDB:
    def __init__(self, url: str):
        self.url = url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
        Base.metadata.create_all(self.engine)
        if url.startswith("sqlite"):
            with self.engine.begin() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=5000"))
        with Session(self.engine) as session:
            row = session.get(SchemaVersion, "smart-router-control")
            if row is None:
                session.add(SchemaVersion(component="smart-router-control", version="0.5.9"))
            else:
                row.version = "0.5.9"
                row.updated_at = utcnow()
            session.commit()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            yield session

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def schema_version(self) -> str:
        with self.session() as session:
            row = session.get(SchemaVersion, "smart-router-control")
            return row.version if row else "unknown"

    def runtime_setting(self, key: str, default: Any = None) -> Any:
        with self.session() as session:
            row = session.get(RuntimeSetting, key)
            if row is None:
                return default
            try:
                return json.loads(row.value_json)
            except Exception:
                return default

    def set_runtime_setting(self, key: str, value: Any) -> None:
        with self.session() as session:
            row = session.get(RuntimeSetting, key)
            if row is None:
                row = RuntimeSetting(key=key)
                session.add(row)
            row.value_json = json.dumps(value, separators=(",", ":"))
            row.updated_at = utcnow()
            session.commit()

    def delete_runtime_settings(self, keys: list[str]) -> None:
        with self.session() as session:
            for key in keys:
                row = session.get(RuntimeSetting, key)
                if row is not None:
                    session.delete(row)
            session.commit()

    def bootstrap_profiles(self, defaults: dict[str, str]) -> None:
        with self.session() as session:
            changed = False
            for name, model in defaults.items():
                if session.get(RouteProfile, name) is None:
                    session.add(RouteProfile(name=name, model=model, min_tier=_profile_min_tier(name)))
                    changed = True
            if changed:
                session.commit()

    def audit(self, actor: str, role: str, action: str, resource: str = "", status: str = "ok", detail: dict[str, Any] | None = None) -> None:
        with self.session() as session:
            session.add(AuditEvent(actor=actor, role=role, action=action, resource=resource, status=status, detail_json=json.dumps(detail or {}, separators=(",", ":"))))
            session.commit()

    def new_request_id(self) -> str:
        return "rq_" + secrets.token_hex(10)



def _profile_min_tier(name: str) -> str:
    if name in {"vision", "coding"}:
        return "strong"
    if name in {"standard"}:
        return "standard"
    if name in {"strong"}:
        return "strong"
    return "fast"
