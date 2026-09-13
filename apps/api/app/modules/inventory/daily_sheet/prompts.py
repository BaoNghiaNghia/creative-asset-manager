"""Tenant versioned business instructions for Inventory Gemini workflows."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.persistence_model import InventoryPromptVersionModel, InventorySettingsModel

PROMPT_TYPES = frozenset({"daily_gemini_processing", "carry_forward_0900"})
BUILTIN_PROMPTS = {
    "daily_gemini_processing": """Understand the workbook from metadata, labels, values and formulas. Process only the authorized daily Gemini working copy. Identify material and warehouse relationships from workbook evidence and use the tenant catalog for identities. Treat missing data as unknown, preserve structure and formulas, and report ambiguity without inventing values. Process independently safe materials even when another requires review.""",
    "carry_forward_0900": """Inspect the previous verified Gemini workbook and current shared workbook. Identify previous Closing values and matching current Opening cells, including fourth-sheet warehouse allocation when evidence confirms it. Resolve exact tenant material and warehouse identities. Return one evidence-backed mapping per material and warehouse, preserve each quantity exactly, and report ambiguous or missing mappings instead of guessing.""",
}

@dataclass(frozen=True)
class ResolvedInventoryPrompt:
    prompt_type: str
    content: str
    source: str
    version: str
    content_hash: str

class InventoryPromptStorageUnavailable(RuntimeError):
    code = "inventory_prompt_storage_unavailable"

class InventoryPromptConflict(RuntimeError):
    code = "inventory_prompt_conflict"

def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def _validate_type(prompt_type: str) -> str:
    if prompt_type not in PROMPT_TYPES: raise ValueError("invalid_inventory_prompt_type")
    return prompt_type

def _content(value: str) -> str:
    value = value.strip()
    if not value: raise ValueError("inventory_prompt_content_required")
    if len(value) > 20_000: raise ValueError("inventory_prompt_content_too_long")
    return value

class InventoryPromptResolver:
    def __init__(self, sessions: sessionmaker[Session]): self.sessions = sessions
    def resolve(self, tenant_id: str, prompt_type: str, *, legacy_goals: list[str] | None = None) -> ResolvedInventoryPrompt:
        _validate_type(prompt_type)
        try:
            with self.sessions() as session:
                row = session.scalar(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type, InventoryPromptVersionModel.status == "active").order_by(InventoryPromptVersionModel.version.desc()))
        except OperationalError as exc:
            raise InventoryPromptStorageUnavailable("inventory_prompt_storage_unavailable") from exc
        if row is not None and all(hasattr(row, field) for field in ("content", "version", "content_hash", "prompt_type")):
            return ResolvedInventoryPrompt(prompt_type, row.content, "custom", f"custom-v{row.version}", row.content_hash)
        if prompt_type == "daily_gemini_processing" and legacy_goals:
            content = "\n".join(f"- {item}" for item in legacy_goals if str(item).strip())
            if content: return ResolvedInventoryPrompt(prompt_type, content, "legacy_config", "legacy-config", _hash(content))
        content = BUILTIN_PROMPTS[prompt_type]
        return ResolvedInventoryPrompt(prompt_type, content, "builtin", "builtin-v1", _hash(content))

    def freeze(self, row, tenant_id: str, prompt_type: str, *, prefix: str, legacy_goals: list[str] | None = None) -> ResolvedInventoryPrompt:
        content = getattr(row, f"{prefix}_content", None)
        stored_hash = getattr(row, f"{prefix}_hash", None)
        if content is not None:
            if not stored_hash or _hash(content) != stored_hash:
                raise RuntimeError("inventory_prompt_snapshot_corrupt")
            return ResolvedInventoryPrompt(prompt_type, content, getattr(row, f"{prefix}_source") or "builtin", getattr(row, f"{prefix}_version") or "builtin-v1", stored_hash)
        resolved = self.resolve(tenant_id, prompt_type, legacy_goals=legacy_goals)
        setattr(row, f"{prefix}_source", resolved.source)
        setattr(row, f"{prefix}_version", resolved.version)
        setattr(row, f"{prefix}_content", resolved.content)
        setattr(row, f"{prefix}_hash", resolved.content_hash)
        return resolved

    def state(self, tenant_id: str, prompt_type: str, legacy_goals: list[str] | None = None) -> dict:
        resolved = self.resolve(tenant_id, prompt_type, legacy_goals=legacy_goals)
        with self.sessions() as session:
            rows = list(session.scalars(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type).order_by(InventoryPromptVersionModel.version.desc())))
        active = next((row for row in rows if row.status == "active"), None); draft = next((row for row in rows if row.status == "draft"), None)
        return {"prompt_type": prompt_type, "source": resolved.source, "version": resolved.version, "content_hash": resolved.content_hash, "active_version_id": active.id if active else None, "active_content": active.content if active else None, "draft": self._view(draft) if draft else None, "builtin_content": BUILTIN_PROMPTS[prompt_type], "safety_summary": "Business instructions only. Backend safety and write scope remain locked."}

    @staticmethod
    def _view(row): return {"id": row.id, "version": row.version, "content": row.content, "content_hash": row.content_hash, "status": row.status, "created_at": row.created_at, "activated_at": row.activated_at}
    def versions(self, tenant_id: str, prompt_type: str):
        _validate_type(prompt_type)
        with self.sessions() as session: return [self._view(row) for row in session.scalars(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type).order_by(InventoryPromptVersionModel.version.desc()))]
    @staticmethod
    def _lock_scope(session: Session, tenant_id: str) -> None:
        # A stable tenant settings row serializes version allocation and active
        # switching on PostgreSQL; unique indexes remain the final invariant.
        row = session.scalar(select(InventorySettingsModel.id).where(InventorySettingsModel.tenant_id == tenant_id).with_for_update())
        if row is None: raise RuntimeError("inventory_settings_required")
    def draft(self, tenant_id: str, prompt_type: str, content: str, actor: str | None):
        content = _content(content); _validate_type(prompt_type)
        with self.sessions() as session:
            self._lock_scope(session, tenant_id)
            version = int(session.scalar(select(func.max(InventoryPromptVersionModel.version)).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type)) or 0) + 1
            row = InventoryPromptVersionModel(tenant_id=tenant_id, prompt_type=prompt_type, version=version, content=content, content_hash=_hash(content), status="draft", created_by=actor)
            try: session.add(row); session.commit()
            except IntegrityError as exc: session.rollback(); raise InventoryPromptConflict("inventory_prompt_conflict") from exc
            session.refresh(row); return self._view(row)
    def activate(self, tenant_id: str, prompt_type: str, prompt_id: str, actor: str | None):
        _validate_type(prompt_type); now = datetime.now(timezone.utc)
        with self.sessions() as session:
            self._lock_scope(session, tenant_id)
            row = session.scalar(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.id == prompt_id, InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type).with_for_update())
            if row is None: raise LookupError("inventory_prompt_not_found")
            for active in session.scalars(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type, InventoryPromptVersionModel.status == "active").with_for_update()): active.status="archived"; active.archived_at=now
            # PostgreSQL checks the partial one-active index per statement.  Flush
            # the archival update before promoting the requested draft so ORM
            # update ordering cannot briefly create two active rows.
            session.flush()
            row.status="active"; row.activated_by=actor; row.activated_at=now
            try: session.commit()
            except IntegrityError as exc: session.rollback(); raise InventoryPromptConflict("inventory_prompt_conflict") from exc
            session.refresh(row); return self._view(row)
    def restore(self, tenant_id: str, prompt_type: str, prompt_id: str, actor: str | None):
        _validate_type(prompt_type)
        with self.sessions() as session:
            self._lock_scope(session, tenant_id)
            source = session.scalar(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.id == prompt_id, InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type))
            if source is None: raise LookupError("inventory_prompt_not_found")
            content = source.content
        draft = self.draft(tenant_id, prompt_type, content, actor)
        return self.activate(tenant_id, prompt_type, draft["id"], actor)
    def reset(self, tenant_id: str, prompt_type: str, actor: str | None):
        _validate_type(prompt_type); now=datetime.now(timezone.utc)
        with self.sessions() as session:
            self._lock_scope(session, tenant_id)
            for row in session.scalars(select(InventoryPromptVersionModel).where(InventoryPromptVersionModel.tenant_id == tenant_id, InventoryPromptVersionModel.prompt_type == prompt_type, InventoryPromptVersionModel.status == "active").with_for_update()): row.status="archived"; row.archived_at=now
            session.commit()
