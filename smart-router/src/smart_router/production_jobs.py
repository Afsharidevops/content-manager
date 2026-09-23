from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .control_db import ProductionJob


def _job_dict(row: ProductionJob) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for column in row.__table__.columns:
        d[column.name] = getattr(row, column.name)
    d["storyboard"] = _load_json(d.pop("storyboard_json", "{}"))
    d["timeline"] = _load_json(d.pop("timeline_json", "{}"))
    d["media_plan"] = _load_json(d.pop("media_plan_json", "{}"))
    return d


def _load_json(value: str, fallback: Any = None) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return fallback if fallback is not None else {}


def create_job(
    db: Any,
    *,
    title: str = "",
    topic: str = "",
    script: str = "",
    platform: str = "",
    aspect_ratio: str = "9:16",
    language: str = "en",
    style: str = "",
    actor: str = "",
) -> ProductionJob:
    now = datetime.now(timezone.utc).isoformat()
    with db.session() as session:
        row = ProductionJob(
            title=title,
            topic=topic,
            script=script,
            platform=platform,
            aspect_ratio=aspect_ratio,
            language=language,
            style=style,
            actor=actor,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def get_job(db: Any, job_id: int) -> ProductionJob | None:
    from sqlalchemy import select

    with db.session() as session:
        return session.scalar(select(ProductionJob).where(ProductionJob.id == job_id))


def list_jobs(db: Any, *, limit: int = 100, status: str = "") -> list[ProductionJob]:
    from sqlalchemy import select

    with db.session() as session:
        query = select(ProductionJob).order_by(ProductionJob.id.desc()).limit(max(1, min(500, limit)))
        if status:
            query = query.where(ProductionJob.status == status)
        return list(session.scalars(query))


def transition(
    db: Any,
    job_id: int,
    *,
    status: str = "",
    storyboard: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    media_plan: dict[str, Any] | None = None,
    ms_job_id: str = "",
    ms_artifact_name: str = "",
    error: str | None = None,
) -> ProductionJob:
    now = datetime.now(timezone.utc).isoformat()
    with db.session() as session:
        row = session.get(ProductionJob, job_id)
        if row is None:
            raise ValueError(f"production job {job_id} not found")
        if status:
            row.status = status
        if storyboard is not None:
            row.storyboard_json = json.dumps(storyboard, separators=(",", ":"))
        if timeline is not None:
            row.timeline_json = json.dumps(timeline, separators=(",", ":"))
        if media_plan is not None:
            row.media_plan_json = json.dumps(media_plan, separators=(",", ":"))
        if ms_job_id:
            row.ms_job_id = ms_job_id
        if ms_artifact_name:
            row.ms_artifact_name = ms_artifact_name
        if error is not None:
            row.error = error
        row.updated_at = now
        session.commit()
        session.refresh(row)
        return row