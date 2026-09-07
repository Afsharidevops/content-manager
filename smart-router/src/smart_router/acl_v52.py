from __future__ import annotations

import os
import json
from typing import Any

from sqlalchemy import delete, or_, select

from .control_db import ACLRule, AccessGroup, ControlDB
from .security_v51 import Identity


class ACLManager:
    def __init__(self, db: ControlDB):
        self.db = db
        self.default_deny = os.getenv("SMART_ROUTER_ACL_DEFAULT_DENY", "false").strip().lower() in {"1", "true", "yes", "on"}

    def allowed(self, identity: Identity, resource_type: str, resource_id: str | int, permission: str) -> bool:
        rid = str(resource_id)
        subjects = [
            ("user", identity.actor),
            ("role", identity.role),
            ("team", identity.team),
        ]
        if identity.api_key_id is not None:
            subjects.append(("virtual_key", str(identity.api_key_id)))
        with self.db.session() as session:
            groups = list(session.scalars(select(AccessGroup).where(AccessGroup.active.is_(True))))
            for group in groups:
                try:
                    members = json.loads(group.member_users_json or "[]")
                except Exception:
                    members = []
                if identity.actor in members:
                    subjects.append(("group", group.name))
            rows = list(session.scalars(select(ACLRule).where(
                ACLRule.resource_type == resource_type,
                or_(ACLRule.resource_id == rid, ACLRule.resource_id == "*"),
                or_(ACLRule.permission == permission, ACLRule.permission == "*"),
            )))
        matches = [r for r in rows if (r.subject_type, r.subject_value) in subjects]
        if any(r.effect == "deny" for r in matches):
            return False
        if any(r.effect == "allow" for r in matches):
            return True
        return not self.default_deny

    def filter_ids(self, identity: Identity, resource_type: str, ids: list[int], permission: str) -> list[int]:
        return [rid for rid in ids if self.allowed(identity, resource_type, rid, permission)]

    def create(self, *, subject_type: str, subject_value: str, resource_type: str, resource_id: str, permission: str, effect: str) -> ACLRule:
        if subject_type not in {"user", "role", "group", "team", "agent", "virtual_key"}:
            raise ValueError("invalid ACL subject type")
        if effect not in {"allow", "deny"}:
            raise ValueError("ACL effect must be allow or deny")
        if not all([subject_value.strip(), resource_type.strip(), resource_id.strip(), permission.strip()]):
            raise ValueError("ACL fields must not be empty")
        with self.db.session() as session:
            row = ACLRule(
                subject_type=subject_type,
                subject_value=subject_value[:180],
                resource_type=resource_type[:80],
                resource_id=resource_id[:180],
                permission=permission[:120],
                effect=effect,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def delete(self, rule_id: int) -> bool:
        with self.db.session() as session:
            row = session.get(ACLRule, rule_id)
            if not row:
                return False
            session.delete(row)
            session.commit()
            return True
