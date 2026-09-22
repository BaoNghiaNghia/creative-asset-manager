from __future__ import annotations

import hashlib
import json
from decimal import Decimal
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
        entries = _bounded_active_entries(rows)
        digest = _canonical_hash(entries)
        return {
            "entries": entries,
            "count": len(entries),
            "hash": digest,
            "version": sum(int(item["version"]) for item in entries),
        }

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
