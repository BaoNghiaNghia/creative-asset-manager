from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.persistence_model import (
    InventoryKnowledgeEntryModel,
    inventory_utcnow,
)

KNOWLEDGE_KINDS = frozenset(
    {
        "RULE",
        "EXCEPTION",
        "COLUMN_MEANING",
        "ROW_TYPE",
        "FORMULA",
        "MATERIAL_MAPPING",
        "WAREHOUSE_MAPPING",
        "UNIT_CONVERSION",
        "NAMING_PATTERN",
        "DO_NOT_EDIT",
        "BUSINESS_NOTE",
    }
)
KNOWLEDGE_STATUSES = frozenset({"proposed", "draft", "active", "archived", "rejected"})
MAX_KNOWLEDGE_CONTENT_CHARS = 20_000
MAX_ACTIVE_KNOWLEDGE_ENTRIES = 100
MAX_ACTIVE_KNOWLEDGE_SNAPSHOT_CHARS = 64_000


def _knowledge_table_missing(error: SQLAlchemyError) -> bool:
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "42P01":
        return True
    message = str(error).lower()
    return "inventory_knowledge_entries" in message and (
        "no such table" in message or "does not exist" in message
    )


class InventoryKnowledgeError(RuntimeError):
    pass


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_entry(row: InventoryKnowledgeEntryModel) -> dict[str, Any]:
    return {
        "knowledge_key": row.knowledge_key,
        "version": row.version,
        "kind": row.kind,
        "scope_type": getattr(row, "scope_type", "workbook"),
        "scope_key": getattr(row, "scope_key", "*"),
        "title": getattr(row, "title", row.knowledge_key),
        "content": str(row.content),
        "structured_rule": dict(getattr(row, "structured_rule_json", {}) or {}),
    }


def _bounded_active_entries(rows: list[InventoryKnowledgeEntryModel]) -> list[dict[str, Any]]:
    if len(rows) > MAX_ACTIVE_KNOWLEDGE_ENTRIES:
        raise InventoryKnowledgeError("inventory_knowledge_snapshot_too_large")
    entries = [_snapshot_entry(row) for row in rows]
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload) > MAX_ACTIVE_KNOWLEDGE_SNAPSHOT_CHARS:
        raise InventoryKnowledgeError("inventory_knowledge_snapshot_too_large")
    return entries


def _view(row: InventoryKnowledgeEntryModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "knowledge_key": row.knowledge_key,
        "version": row.version,
        "kind": row.kind,
        "scope_type": row.scope_type,
        "scope_key": row.scope_key,
        "title": row.title,
        "content": row.content,
        "structured_rule": dict(row.structured_rule_json or {}),
        "status": row.status,
        "confidence": float(row.confidence) if row.confidence is not None else None,
        "source": row.source,
        "source_run_id": row.source_run_id,
        "source_content_hash": row.source_content_hash,
        "evidence": list(row.evidence_json or []),
        "supersedes_id": row.supersedes_id,
        "created_by": row.created_by,
        "activated_by": row.activated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
    }


class InventoryKnowledgeService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def _lock_scope(session: Session, tenant_id: str) -> None:
        tenant = session.scalar(
            select(TenantModel.id)
            .where(TenantModel.id == tenant_id)
            .with_for_update()
        )
        if tenant is None:
            raise LookupError("tenant_not_found")

    @staticmethod
    def _validate(kind: str, title: str, content: str, status: str) -> None:
        if kind not in KNOWLEDGE_KINDS:
            raise InventoryKnowledgeError("invalid_inventory_knowledge_kind")
        if status not in KNOWLEDGE_STATUSES:
            raise InventoryKnowledgeError("invalid_inventory_knowledge_status")
        if not title.strip():
            raise InventoryKnowledgeError("inventory_knowledge_title_required")
        if not content.strip():
            raise InventoryKnowledgeError("inventory_knowledge_content_required")
        if len(content) > MAX_KNOWLEDGE_CONTENT_CHARS:
            raise InventoryKnowledgeError("inventory_knowledge_content_too_large")

    def list(
        self,
        tenant_id: str,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            query = select(InventoryKnowledgeEntryModel).where(
                InventoryKnowledgeEntryModel.tenant_id == tenant_id
            )
            if statuses:
                invalid = set(statuses) - KNOWLEDGE_STATUSES
                if invalid:
                    raise InventoryKnowledgeError("invalid_inventory_knowledge_status")
                query = query.where(InventoryKnowledgeEntryModel.status.in_(statuses))
            rows = list(
                session.scalars(
                    query.order_by(
                        InventoryKnowledgeEntryModel.status,
                        InventoryKnowledgeEntryModel.kind,
                        InventoryKnowledgeEntryModel.scope_type,
                        InventoryKnowledgeEntryModel.scope_key,
                        InventoryKnowledgeEntryModel.knowledge_key,
                        InventoryKnowledgeEntryModel.version.desc(),
                    )
                )
            )
            return [_view(row) for row in rows]

    def create(
        self,
        tenant_id: str,
        *,
        kind: str,
        title: str,
        content: str,
        scope_type: str = "workbook",
        scope_key: str = "*",
        structured_rule: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
        actor_id: str | None = None,
        status: str = "draft",
        source: str = "manual",
        source_run_id: str | None = None,
        source_content_hash: str | None = None,
        knowledge_key: str | None = None,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate(kind, title, content, status)
        if confidence is not None and not 0 <= confidence <= 1:
            raise InventoryKnowledgeError("invalid_inventory_knowledge_confidence")
        key = knowledge_key or str(uuid4())
        with self.session_factory() as session:
            self._lock_scope(session, tenant_id)
            version = session.scalar(
                select(func.max(InventoryKnowledgeEntryModel.version)).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.knowledge_key == key,
                )
            )
            row = InventoryKnowledgeEntryModel(
                tenant_id=tenant_id,
                knowledge_key=key,
                version=int(version or 0) + 1,
                kind=kind,
                scope_type=scope_type.strip() or "workbook",
                scope_key=scope_key.strip() or "*",
                title=title.strip(),
                content=content.strip(),
                structured_rule_json=dict(structured_rule or {}),
                status=status,
                confidence=Decimal(str(confidence)) if confidence is not None else None,
                source=source,
                source_run_id=source_run_id,
                source_content_hash=source_content_hash,
                evidence_json=list(evidence or []),
                supersedes_id=supersedes_id,
                created_by=actor_id,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _view(row)

    def revise(
        self,
        tenant_id: str,
        entry_id: str,
        *,
        kind: str | None = None,
        title: str | None = None,
        content: str | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
        structured_rule: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        confidence: float | None = None,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            base = session.scalar(
                select(InventoryKnowledgeEntryModel).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.id == entry_id,
                )
            )
            if base is None:
                raise LookupError("inventory_knowledge_not_found")
            payload = {
                "kind": kind or base.kind,
                "title": title if title is not None else base.title,
                "content": content if content is not None else base.content,
                "scope_type": scope_type if scope_type is not None else base.scope_type,
                "scope_key": scope_key if scope_key is not None else base.scope_key,
                "structured_rule": structured_rule if structured_rule is not None else dict(base.structured_rule_json or {}),
                "evidence": evidence if evidence is not None else list(base.evidence_json or []),
                "confidence": confidence if confidence is not None else (float(base.confidence) if base.confidence is not None else None),
                "knowledge_key": base.knowledge_key,
                "supersedes_id": base.id,
            }
        return self.create(
            tenant_id,
            actor_id=actor_id,
            status="draft",
            source="manual_revision",
            **payload,
        )

    def activate(self, tenant_id: str, entry_id: str, actor_id: str | None) -> dict[str, Any]:
        try:
            with self.session_factory() as session:
                self._lock_scope(session, tenant_id)
                row = session.scalar(
                    select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.id == entry_id,
                    )
                )
                if row is None:
                    raise LookupError("inventory_knowledge_not_found")
                if row.status not in {"draft", "proposed", "active"}:
                    raise InventoryKnowledgeError("inventory_knowledge_not_activatable")
                rule = dict(row.structured_rule_json or {})
                if (
                    rule.get("learner") == "verified_daily_assessment_v2"
                    and rule.get("consistent_conclusion") is False
                ):
                    # Human review must produce a revised, evidence-backed rule;
                    # approving the raw conflicting observation is not safe.
                    raise InventoryKnowledgeError(
                        "inventory_knowledge_conflicting_observations_not_activatable"
                    )
                archived_prior = False
                for prior in session.scalars(
                    select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.knowledge_key == row.knowledge_key,
                        InventoryKnowledgeEntryModel.status == "active",
                        InventoryKnowledgeEntryModel.id != row.id,
                    )
                ):
                    prior.status = "archived"
                    archived_prior = True
                if archived_prior:
                    # Flush the old active row first so the partial unique index
                    # never observes two active versions in the same statement batch.
                    session.flush()
                row.status = "active"
                row.activated_by = actor_id
                row.activated_at = inventory_utcnow()
                row.updated_at = inventory_utcnow()
                session.flush()
                active_rows = list(
                    session.scalars(
                        select(InventoryKnowledgeEntryModel)
                        .where(
                            InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                            InventoryKnowledgeEntryModel.status == "active",
                        )
                        .order_by(
                            InventoryKnowledgeEntryModel.kind,
                            InventoryKnowledgeEntryModel.scope_type,
                            InventoryKnowledgeEntryModel.scope_key,
                            InventoryKnowledgeEntryModel.knowledge_key,
                            InventoryKnowledgeEntryModel.version,
                        )
                    )
                )
                _bounded_active_entries(active_rows)
                session.commit()
                session.refresh(row)
                return _view(row)
        except IntegrityError as exc:
            raise InventoryKnowledgeError("inventory_knowledge_activation_conflict") from exc

    def reject(self, tenant_id: str, entry_id: str, actor_id: str | None) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.scalar(
                select(InventoryKnowledgeEntryModel).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.id == entry_id,
                )
            )
            if row is None:
                raise LookupError("inventory_knowledge_not_found")
            if row.status not in {"proposed", "draft"}:
                raise InventoryKnowledgeError("inventory_knowledge_not_rejectable")
            row.status = "rejected"
            row.updated_at = inventory_utcnow()
            if actor_id and not row.created_by:
                row.created_by = actor_id
            session.commit()
            session.refresh(row)
            return _view(row)

    def active_snapshot(
        self,
        tenant_id: str,
        *,
        workbook_keys: tuple[str, ...] = (),
        sheet_keys: tuple[str, ...] = (),
        material_keys: tuple[str, ...] = (),
        warehouse_keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        # During a rolling deploy or narrow legacy fixture the new table may
        # genuinely not exist yet. Only that exact condition falls back to an
        # empty snapshot; real DB failures must stop the run rather than silently
        # dropping human-approved business rules.
        try:
            with self.session_factory() as session:
                query = select(InventoryKnowledgeEntryModel).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.status == "active",
                )
                if any((workbook_keys, sheet_keys, material_keys, warehouse_keys)):
                    scope_filters = [
                        InventoryKnowledgeEntryModel.scope_type == "global",
                        InventoryKnowledgeEntryModel.scope_key == "*",
                    ]
                    if workbook_keys:
                        scope_filters.append(
                            (
                                InventoryKnowledgeEntryModel.scope_type == "workbook"
                            )
                            & InventoryKnowledgeEntryModel.scope_key.in_(workbook_keys)
                        )
                    if sheet_keys:
                        scope_filters.append(
                            (InventoryKnowledgeEntryModel.scope_type == "sheet")
                            & InventoryKnowledgeEntryModel.scope_key.in_(sheet_keys)
                        )
                    if material_keys:
                        scope_filters.append(
                            (InventoryKnowledgeEntryModel.scope_type == "material")
                            & InventoryKnowledgeEntryModel.scope_key.in_(material_keys)
                        )
                    if warehouse_keys:
                        scope_filters.append(
                            (InventoryKnowledgeEntryModel.scope_type == "warehouse")
                            & InventoryKnowledgeEntryModel.scope_key.in_(warehouse_keys)
                        )
                    query = query.where(or_(*scope_filters))
                rows = list(
                    session.scalars(
                        query.order_by(
                            InventoryKnowledgeEntryModel.kind,
                            InventoryKnowledgeEntryModel.scope_type,
                            InventoryKnowledgeEntryModel.scope_key,
                            InventoryKnowledgeEntryModel.knowledge_key,
                            InventoryKnowledgeEntryModel.version,
                        ).limit(200)
                    )
                )
        except (OperationalError, ProgrammingError) as exc:
            if _knowledge_table_missing(exc):
                rows = []
            else:
                raise InventoryKnowledgeError("inventory_knowledge_unavailable") from exc
        except SQLAlchemyError as exc:
            raise InventoryKnowledgeError("inventory_knowledge_unavailable") from exc
        rows = [
            row for row in rows
            if hasattr(row, "knowledge_key")
            and hasattr(row, "version")
            and hasattr(row, "kind")
            and hasattr(row, "content")
        ]
        rows = [
            row for row in rows
            if not (
                bool((getattr(row, "structured_rule_json", {}) or {}).get("suspended"))
                or str((getattr(row, "structured_rule_json", {}) or {}).get("state") or "").lower() == "suspended"
                or str((getattr(row, "structured_rule_json", {}) or {}).get("mode") or "").lower() == "suspended"
            )
        ]
        entries = _bounded_active_entries(rows)
        digest = _canonical_hash(entries)
        return {
            "entries": entries,
            "count": len(entries),
            "hash": digest,
            "version": sum(int(item["version"]) for item in entries),
        }

    def gemini_learning_context(
        self, tenant_id: str, *, sheet_keys: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Bounded historical evidence for Gemini, never an executable rule set.

        Only verified observations enter this advisory memory. Disputed patterns
        are exposed as investigation topics, not as business conclusions.
        Active knowledge is provided separately by active_snapshot().
        """
        from app.modules.inventory.persistence_model import InventoryOperationAuditModel

        allowed = set(sheet_keys)
        with self.session_factory() as session:
            proposals = list(session.scalars(
                select(InventoryKnowledgeEntryModel).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.status == "proposed",
                    InventoryKnowledgeEntryModel.source == "verified_daily_observation",
                ).order_by(InventoryKnowledgeEntryModel.created_at.desc()).limit(80)
            ))
            audits = list(session.scalars(
                select(InventoryOperationAuditModel).where(
                    InventoryOperationAuditModel.tenant_id == tenant_id,
                    InventoryOperationAuditModel.stage == "evening_reconcile",
                    InventoryOperationAuditModel.status == "completed",
                ).order_by(
                    InventoryOperationAuditModel.business_date.desc(),
                    InventoryOperationAuditModel.created_at.desc(),
                ).limit(35)
            ))
        consistent = []
        conflicts = []
        for entry in proposals:
            if not isinstance(entry, InventoryKnowledgeEntryModel):
                continue
            if entry.scope_type != "sheet" or (allowed and entry.scope_key not in allowed):
                continue
            rule = dict(entry.structured_rule_json or {})
            if rule.get("learner") != "verified_daily_assessment_v2":
                continue
            if rule.get("consistent_conclusion") is True and len(consistent) < 12:
                consistent.append({
                    "sheet": entry.scope_key,
                    "title": entry.title[:160],
                    "observed_conclusion": entry.content[:320],
                    "support_days": int(rule.get("distinct_business_dates") or rule.get("support") or 0),
                    "verified_today": False,
                    "approved": False,
                })
            elif rule.get("consistent_conclusion") is False and len(conflicts) < 20:
                # Do not ship a contradictory conclusion into the agent's
                # executable instruction context.
                conflicts.append(entry.title[:160])
        observations = []
        included_days = set()
        for audit in audits:
            if not isinstance(audit, InventoryOperationAuditModel):
                continue
            summary = dict(audit.summary_json or {})
            if summary.get("verification_status") != "verified" or int(summary.get("read_cells") or 0) <= 0:
                continue
            assessment = dict(audit.assessment_json or {})
            if assessment.get("additional_reads_needed"):
                continue
            for finding in (assessment.get("observations") or [])[:24]:
                if not isinstance(finding, dict):
                    continue
                code = str(finding.get("code") or "").strip()
                if (not code or code.upper().startswith(("METADATA", "STRUCTURE", "WORKBOOK_REVIEWED"))
                    or float(finding.get("confidence") or 0) < 0.8):
                    continue
                for reference in (finding.get("evidence") or [])[:5]:
                    if not isinstance(reference, dict):
                        continue
                    sheet = str(reference.get("sheet") or "")
                    cell = str(reference.get("cell") or "")
                    fingerprint = str(reference.get("evidence_hash") or "")
                    if (not sheet or (allowed and sheet not in allowed)
                        or not cell or len(fingerprint) != 64):
                        continue
                    observations.append({
                        "business_date": audit.business_date.isoformat(),
                        "sheet": sheet,
                        "cell": cell,
                        "code": code[:128],
                        "observation": str(finding.get("conclusion") or "")[:320],
                        "evidence_hash": fingerprint,
                    })
                    included_days.add(audit.business_date.isoformat())
                    break
                if len(observations) >= 12:
                    break
            if len(observations) >= 12:
                break
        result = {
            "kind": "historical_advisory_not_authority",
            "approved_rules_only": False,
            "current_cells_revalidation_required": True,
            "verified_business_days_included": len(included_days),
            "consistent_patterns": consistent,
            "conflicting_topics": sorted(set(conflicts)),
            "verified_observations": observations,
        }
        result["hash"] = _canonical_hash(result)
        return result

    def learning_status(self, tenant_id: str, *, today: date | None = None) -> dict[str, Any]:
        """Read-only progress for continuous learning; never reads workbook data."""
        from app.modules.inventory.persistence_model import InventoryOperationAuditModel

        with self.session_factory() as session:
            audits = list(session.scalars(
                select(InventoryOperationAuditModel).where(
                    InventoryOperationAuditModel.tenant_id == tenant_id,
                    InventoryOperationAuditModel.stage == "evening_reconcile",
                ).order_by(
                    InventoryOperationAuditModel.business_date.desc(),
                    InventoryOperationAuditModel.created_at.desc(),
                ).limit(1000)
            ))
            statuses = dict(session.execute(
                select(InventoryKnowledgeEntryModel.status, func.count())
                .where(InventoryKnowledgeEntryModel.tenant_id == tenant_id)
                .group_by(InventoryKnowledgeEntryModel.status)
            ).all())
            sources = dict(session.execute(
                select(InventoryKnowledgeEntryModel.source, func.count())
                .where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.status == "proposed",
                )
                .group_by(InventoryKnowledgeEntryModel.source)
            ).all())
            observation_rules = list(session.scalars(
                select(InventoryKnowledgeEntryModel.structured_rule_json).where(
                    InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                    InventoryKnowledgeEntryModel.status == "proposed",
                    InventoryKnowledgeEntryModel.source == "verified_daily_observation",
                )
            ))
        dates: set[str] = set()
        total_read = 0
        last_learning = None
        for audit in audits:
            summary = dict(audit.summary_json or {})
            if last_learning is None and isinstance(summary.get("learning"), dict):
                last_learning = {
                    "business_date": audit.business_date.isoformat(),
                    **{
                        key: summary["learning"].get(key)
                        for key in ("status", "history_candidates", "assessment_candidates",
                                    "history_error", "assessment_error")
                        if key in summary["learning"]
                    },
                }
            if (audit.status == "completed"
                    and summary.get("verification_status") == "verified"):
                day = audit.business_date.isoformat()
                if day not in dates:
                    total_read += int(summary.get("read_cells") or 0)
                    dates.add(day)
        # Detect a run of purportedly successful zero-write days where Gemini
        # inspected only metadata. A truly validated no-change comparison or
        # verified edit resets the streak; legitimate no-edit days are not errors.
        unverified_noop_streak = 0
        seen_recent_days: set[date] = set()
        for audit in audits:
            if audit.business_date in seen_recent_days:
                continue
            seen_recent_days.add(audit.business_date)
            summary = dict(audit.summary_json or {})
            if audit.status != "completed" or summary.get("verification_status") != "verified":
                break
            if int(audit.writes or 0) != 0:
                break
            assessment = dict(audit.assessment_json or {})
            has_grounded_noop = False
            for observation in (assessment.get("observations") or []):
                if not isinstance(observation, dict) or str(observation.get("code") or "").upper() != "NO_CHANGES_VERIFIED":
                    continue
                row_columns: dict[tuple[str, int], set[str]] = {}
                for ref in (observation.get("evidence") or []):
                    if not isinstance(ref, dict):
                        continue
                    cell = str(ref.get("cell") or "")
                    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell)
                    if not match or int(match.group(2)) < 2 or len(str(ref.get("evidence_hash") or "")) != 64:
                        continue
                    row_columns.setdefault((str(ref.get("sheet") or ""), int(match.group(2))), set()).add(match.group(1))
                if any(len(columns) >= 2 for columns in row_columns.values()):
                    has_grounded_noop = True
                    break
            if has_grounded_noop:
                break
            unverified_noop_streak += 1

        conflicting = sum(
            1 for rule in observation_rules
            if (rule or {}).get("learner") == "verified_daily_assessment_v2"
            and (rule or {}).get("consistent_conclusion") is False
        )
        consistent = sum(
            1 for rule in observation_rules
            if (rule or {}).get("learner") == "verified_daily_assessment_v2"
            and (rule or {}).get("consistent_conclusion") is True
        )
        today_local = today or datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        latest_day = max(dates) if dates else None
        age = (today_local - date.fromisoformat(latest_day)).days if latest_day else None
        issues: list[str] = []
        if last_learning and last_learning.get("status") == "failed":
            issues.append("learning_failed")
        if age is None or age >= 2:
            issues.append("verified_data_stale")
        if conflicting:
            issues.append("conflicting_proposals")
        if unverified_noop_streak >= 3:
            issues.append("unverified_noop_streak")
        health_status = (
            "critical" if any(code in issues for code in ("learning_failed", "verified_data_stale"))
            else "warning" if issues
            else "watching" if last_learning is None
            else "healthy"
        )
        return {
            "health": {
                "status": health_status,
                "issue_codes": issues,
                "conflicting_proposals": conflicting,
                "consistent_proposals": consistent,
                "last_verified_age_days": age,
                "unverified_noop_streak": unverified_noop_streak,
            },
            "verified_days": len(dates),
            "read_cells_verified": total_read,
            "last_verified_day": max(dates) if dates else None,
            "last_learning": last_learning,
            "knowledge": {status: int(statuses.get(status, 0))
                          for status in ("active", "proposed", "draft", "archived", "rejected")},
            "proposals_by_source": sources,
        }

    def learn_from_verified_assessments(
        self, tenant_id: str, *, limit: int = 500,
        minimum_distinct_days: int = 2,
    ) -> list[dict[str, Any]]:
        """Produce review-only notes from repeated, grounded no-write assessments.

        This intentionally does not infer executable rules from arbitrary text.
        Only matching conclusions observed on independent verified business days
        qualify. All provenance is linked to stored audit IDs and cell evidence.
        """
        import re
        from app.modules.inventory.persistence_model import InventoryOperationAuditModel

        with self.session_factory() as session:
            audits = list(session.scalars(
                select(InventoryOperationAuditModel).where(
                    InventoryOperationAuditModel.tenant_id == tenant_id,
                    InventoryOperationAuditModel.stage == "evening_reconcile",
                    InventoryOperationAuditModel.status == "completed",
                ).order_by(
                    InventoryOperationAuditModel.business_date.desc(),
                    InventoryOperationAuditModel.created_at.desc(),
                ).limit(max(1, min(int(limit), 5000)))
            ))

        groups: dict[tuple[str, str], dict[Any, tuple[Any, dict, dict]]] = {}
        for audit in audits:
            summary = dict(audit.summary_json or {})
            if summary.get("verification_status") != "verified" or int(summary.get("read_cells") or 0) <= 0:
                continue
            assessment = dict(audit.assessment_json or {})
            if assessment.get("additional_reads_needed") is True:
                continue
            for observation in (assessment.get("observations") or [])[:40]:
                if not isinstance(observation, dict):
                    continue
                code = str(observation.get("code") or "").strip()
                conclusion = " ".join(str(observation.get("conclusion") or "").split())
                confidence = observation.get("confidence")
                evidence = observation.get("evidence") or []
                if (
                    not code or len(code) > 128 or not conclusion or len(conclusion) > 1024
                    or not isinstance(confidence, (int, float)) or not 0.8 <= confidence <= 1
                    or not isinstance(evidence, list) or not evidence
                ):
                    continue
                for ref in evidence[:12]:
                    if not isinstance(ref, dict):
                        continue
                    sheet = ref.get("sheet")
                    cell = ref.get("cell")
                    fingerprint = ref.get("evidence_hash")
                    if (
                        not isinstance(sheet, str) or not 0 < len(sheet) <= 255
                        or not isinstance(cell, str) or not re.fullmatch(r"[A-Z]+[1-9][0-9]*", cell)
                        or not isinstance(fingerprint, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                    ):
                        continue
                    key = (sheet, code)
                    # One observation from each business date; multiple references
                    # from one run are not independent learning support.
                    groups.setdefault(key, {}).setdefault(
                        audit.business_date, (audit, observation, ref),
                    )

        created: list[dict[str, Any]] = []
        for (sheet, code), day_observations in sorted(groups.items()):
            if len(day_observations) < max(2, minimum_distinct_days):
                continue
            dates = sorted(day_observations, reverse=True)[:20]
            sample = [day_observations[day] for day in dates]
            distinct_conclusions = {str(observation["conclusion"]).strip() for _, observation, _ in sample}
            consistent = len(distinct_conclusions) == 1
            evidence = [
                {
                    "business_date": audit.business_date.isoformat(),
                    "audit_id": audit.id,
                    "sheet": ref["sheet"],
                    "cell": ref["cell"],
                    "evidence_hash": ref["evidence_hash"],
                    "verification_status": "verified",
                    "conclusion": observation["conclusion"],
                }
                for audit, observation, ref in sample
            ]
            fingerprint = _canonical_hash({
                "sheet": sheet, "code": code,
            })
            knowledge_key = f"verified-observation:{fingerprint[:48]}"
            # Keep a single review item per observed conclusion. Rejecting a
            # candidate must prevent automated recreation on the next run.
            with self.session_factory() as session:
                existing = session.scalar(
                    select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.knowledge_key == knowledge_key,
                    ).order_by(InventoryKnowledgeEntryModel.version.desc()).limit(1)
                )
            if existing is not None:
                created.append(_view(existing))
                continue
            structured_rule = {
                "learner": "verified_daily_assessment_v2",
                "mode": "shadow",
                "executable": False,
                "eligible_for_activation": False,
                "requires_human_review": True,
                "observation_code": code,
                "support": len(day_observations),
                "distinct_business_dates": len(day_observations),
                "distinct_conclusions": len(distinct_conclusions),
                "consistent_conclusion": consistent,
            }
            try:
                created.append(self.create(
                    tenant_id,
                    kind="BUSINESS_NOTE",
                    title=f"Observed workbook pattern: {code}"[:255],
                    content=(next(iter(distinct_conclusions)) if consistent else
                             "Verified workbook observations disagree across days; review linked evidence before defining a business rule."),
                    scope_type="sheet", scope_key=sheet,
                    structured_rule=structured_rule, evidence=evidence,
                    confidence=min(
                        min(float(observation["confidence"]) for _, observation, _ in sample),
                        0.95 if consistent else 0.5,
                    ),
                    status="proposed", source="verified_daily_observation",
                    source_content_hash=fingerprint,
                    knowledge_key=knowledge_key,
                ))
            except IntegrityError:
                # Concurrent workers may mine the same confirmed pattern. The
                # tenant/key version uniqueness constraint is authoritative.
                with self.session_factory() as session:
                    existing = session.scalar(select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.knowledge_key == knowledge_key,
                    ))
                if existing is None:
                    raise
                created.append(_view(existing))
        return created

    def learn_from_verified_history(
        self,
        tenant_id: str,
        *,
        limit: int = 1000,
        minimum_support: int = 2,
    ) -> list[dict[str, Any]]:
        """Mine reusable proposals from verified historical mutations.

        Historical observations never become active knowledge automatically.
        A candidate needs repeated verified support and is persisted as a
        proposed RULE for explicit review/activation.
        """
        from app.modules.inventory.persistence_model import (
            InventoryOperationAuditModel,
            InventoryOperationChangeModel,
        )

        bounded_limit = max(1, min(int(limit), 5000))
        required_support = max(2, int(minimum_support))
        with self.session_factory() as session:
            rows = list(
                session.execute(
                    select(InventoryOperationChangeModel, InventoryOperationAuditModel)
                    .join(
                        InventoryOperationAuditModel,
                        InventoryOperationAuditModel.id == InventoryOperationChangeModel.audit_id,
                    )
                    .where(
                        InventoryOperationChangeModel.tenant_id == tenant_id,
                        InventoryOperationAuditModel.tenant_id == tenant_id,
                        InventoryOperationAuditModel.status == "completed",
                        InventoryOperationChangeModel.verification_status == "verified",
                    )
                    .order_by(
                        InventoryOperationAuditModel.business_date.desc(),
                        InventoryOperationChangeModel.id.desc(),
                    )
                    .limit(bounded_limit)
                )
            )

        groups: dict[tuple[str, str, str, str], list[tuple[Any, Any]]] = {}
        for change, audit in rows:
            reason = str(change.reason or "").strip()
            provenance = str(change.provenance or "").strip()
            operation_type = str(change.operation_type or "set_cell").strip()
            sheet = str(change.sheet or "").strip()
            if not reason or not sheet:
                continue
            key = (sheet, operation_type, provenance, reason)
            groups.setdefault(key, []).append((change, audit))

        proposals: list[dict[str, Any]] = []
        for (sheet, operation_type, provenance, reason), observations in sorted(groups.items()):
            if len(observations) < required_support:
                continue
            evidence = []
            for change, audit in observations[:20]:
                evidence.append({
                    "business_date": audit.business_date.isoformat(),
                    "audit_id": audit.id,
                    "change_id": change.id,
                    "sheet": sheet,
                    "cell": change.cell,
                    "source_sheet": change.source_sheet,
                    "source_cell": change.source_cell,
                    "material_id": change.material_id,
                    "warehouse_id": change.warehouse_id,
                    "before": (change.before_json or {}).get("value"),
                    "after": (change.after_json or {}).get("value"),
                    "verification_status": "verified",
                })
            support = len(observations)
            distinct_business_dates = len({audit.business_date for _change, audit in observations})
            # Historical candidates remain shadow-only until a human explicitly
            # activates them. Eligibility is advisory and can be revoked by a
            # later conflicting observation without ever granting write access.
            outcomes = {
                _canonical_hash({
                    "before": (change.before_json or {}).get("value"),
                    "after": (change.after_json or {}).get("value"),
                    "source_sheet": change.source_sheet,
                    "source_cell": change.source_cell,
                })
                for change, _audit in observations
            }
            conflicts = max(0, len(outcomes) - 1)
            confidence = min(0.99, max(0.0, 0.5 + (0.1 * support) - (0.2 * conflicts)))
            eligible_for_activation = (
                support >= max(required_support, 3)
                and distinct_business_dates >= 2
                and conflicts == 0
                and confidence >= 0.8
            )
            risk_level = "LOW" if operation_type in {"set_cell", "copy_cell"} and provenance else "MEDIUM"
            structured_rule = {
                "learner": "verified_operation_history_v1",
                "mode": "shadow",
                "sheet": sheet,
                "operation_type": operation_type,
                "provenance": provenance or None,
                "reason": reason,
                "support": support,
                "distinct_business_dates": distinct_business_dates,
                "conflicts": conflicts,
                "minimum_support": required_support,
                "confidence": confidence,
                "risk_level": risk_level,
                "executable": False,
                "execution_block_reason": "historical_rule_missing_complete_mechanical_contract",
                "eligible_for_activation": eligible_for_activation,
            }
            canonical = {
                "kind": "RULE",
                "scope_type": "sheet",
                "scope_key": sheet,
                "title": f"Observed verified pattern: {reason}"[:500],
                "content": (
                    f"Historical verified Inventory runs repeatedly used {operation_type} "
                    f"on sheet {sheet!r} for this reason: {reason}"
                ),
                "structured_rule": structured_rule,
                "evidence": evidence,
            }
            content_hash = _canonical_hash(canonical)
            knowledge_key = f"history:{_canonical_hash({'sheet': sheet, 'operation_type': operation_type, 'provenance': provenance, 'reason': reason})[:48]}"
            with self.session_factory() as session:
                existing = session.scalar(
                    select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.knowledge_key == knowledge_key,
                        InventoryKnowledgeEntryModel.source_content_hash == content_hash,
                        InventoryKnowledgeEntryModel.status.in_(("proposed", "active")),
                    )
                )
            if existing is not None:
                proposals.append(_view(existing))
                continue
            proposals.append(
                self.create(
                    tenant_id,
                    kind="RULE",
                    title=canonical["title"],
                    content=canonical["content"],
                    scope_type="sheet",
                    scope_key=sheet,
                    structured_rule=structured_rule,
                    evidence=evidence,
                    confidence=confidence,
                    status="proposed",
                    source="historical_verified_audit",
                    source_content_hash=content_hash,
                    knowledge_key=knowledge_key,
                )
            )
        return proposals

    def persist_proposals(
        self,
        tenant_id: str,
        *,
        run_id: str,
        proposals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for proposal in proposals:
            canonical = {
                "kind": proposal.get("kind"),
                "scope_type": proposal.get("scope_type") or "workbook",
                "scope_key": proposal.get("scope_key") or "*",
                "title": proposal.get("title"),
                "content": proposal.get("content"),
                "structured_rule": proposal.get("structured_rule") or {},
                "evidence": proposal.get("evidence") or [],
            }
            content_hash = _canonical_hash(canonical)
            with self.session_factory() as session:
                existing = session.scalar(
                    select(InventoryKnowledgeEntryModel).where(
                        InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                        InventoryKnowledgeEntryModel.source_run_id == run_id,
                        InventoryKnowledgeEntryModel.source_content_hash == content_hash,
                    )
                )
                if existing is not None:
                    created.append(_view(existing))
                    continue
            try:
                created.append(
                    self.create(
                        tenant_id,
                        kind=str(canonical["kind"]),
                        title=str(canonical["title"]),
                        content=str(canonical["content"]),
                        scope_type=str(canonical["scope_type"]),
                        scope_key=str(canonical["scope_key"]),
                        structured_rule=dict(canonical["structured_rule"]),
                        evidence=list(canonical["evidence"]),
                        confidence=float(proposal["confidence"]) if proposal.get("confidence") is not None else None,
                        status="proposed",
                        source="gemini_proposal",
                        source_run_id=run_id,
                        source_content_hash=content_hash,
                    )
                )
            except IntegrityError:
                with self.session_factory() as session:
                    existing = session.scalar(
                        select(InventoryKnowledgeEntryModel).where(
                            InventoryKnowledgeEntryModel.tenant_id == tenant_id,
                            InventoryKnowledgeEntryModel.source_run_id == run_id,
                            InventoryKnowledgeEntryModel.source_content_hash == content_hash,
                        )
                    )
                    if existing is None:
                        raise
                    created.append(_view(existing))
        return created
