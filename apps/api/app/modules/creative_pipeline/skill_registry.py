from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.creative_pipeline.model import (
    CreativeSkillBindingModel,
    CreativeSkillExecutionModel,
    CreativeSkillModel,
    CreativeSkillVersionModel,
    ListingTaskModel,
    SourceGroupModel,
)

IDEA_SKILL_ID = "11111111-1111-4111-8111-111111111111"
IDEA_VERSION_ID = "11111111-1111-4111-8111-111111111112"
PROMPT_SKILL_ID = "22222222-2222-4222-8222-222222222222"
PROMPT_VERSION_ID = "22222222-2222-4222-8222-222222222223"

SKILL_NODE_TYPES = frozenset({"idea_story", "prompt"})
SKILL_SCOPE_TYPES = frozenset({"tenant", "source_group", "listing"})
SUPPORTED_KNOWLEDGE_REFS = frozenset({"idea_story", "seedance_2_5", "google_omni"})


class CreativeSkillRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BuiltinSkillSpec:
    skill_id: str
    version_id: str
    key: str
    name: str
    description: str
    node_type: str
    instructions: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    knowledge_refs: tuple[str, ...]


BUILTIN_SKILLS: tuple[BuiltinSkillSpec, ...] = (
    BuiltinSkillSpec(
        skill_id=IDEA_SKILL_ID,
        version_id=IDEA_VERSION_ID,
        key="creative-idea-story",
        name="Creative Idea / Story",
        description="GPT skill for structured creative concepts and story beats.",
        node_type="idea_story",
        instructions=(
            "Use the supplied listing context and frozen knowledge snapshot. "
            "Preserve product facts, follow the required output schema exactly, "
            "and do not invent unavailable product details."
        ),
        input_schema={"kind": "creative_pipeline_input_snapshot", "version": 1},
        output_schema={"kind": "idea_story", "version": 1},
        knowledge_refs=("idea_story",),
    ),
    BuiltinSkillSpec(
        skill_id=PROMPT_SKILL_ID,
        version_id=PROMPT_VERSION_ID,
        key="creative-video-prompt",
        name="Creative Video Prompt",
        description="GPT skill for provider-specific structured video prompts.",
        node_type="prompt",
        instructions=(
            "Convert the supplied approved idea into a provider-ready video prompt. "
            "Follow the frozen knowledge snapshot and target provider constraints exactly. "
            "Return only schema-valid structured output."
        ),
        input_schema={"kind": "idea_story", "version": 1},
        output_schema={"kind": "video_prompt_bundle", "version": 1},
        knowledge_refs=("seedance_2_5", "google_omni"),
    ),
)

_BUILTIN_BY_NODE = {item.node_type: item for item in BUILTIN_SKILLS}


@dataclass(frozen=True, slots=True)
class ResolvedCreativeSkill:
    skill_id: str
    skill_version_id: str
    skill_key: str
    name: str
    node_type: str
    version: int
    instructions: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    knowledge_refs: tuple[str, ...]
    provider: str
    preferred_model: str | None
    binding_scope: str
    binding_id: str | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def scope_key(scope_type: str, scope_id: str | None = None) -> str:
    if scope_type not in SKILL_SCOPE_TYPES:
        raise CreativeSkillRegistryError("invalid_skill_scope")
    if scope_type == "tenant":
        if scope_id:
            raise CreativeSkillRegistryError("tenant_scope_must_not_have_scope_id")
        return "tenant"
    if not scope_id:
        raise CreativeSkillRegistryError("skill_scope_id_required")
    return f"{scope_type}:{scope_id}"


class CreativeSkillRegistry:
    """Versioned GPT Skill registry with tenant/group/listing binding precedence."""

    def __init__(self, session: Session):
        self.session = session

    def ensure_builtins(self) -> None:
        now = utcnow()
        for spec in BUILTIN_SKILLS:
            skill = self.session.get(CreativeSkillModel, spec.skill_id)
            if skill is None:
                skill = CreativeSkillModel(
                    id=spec.skill_id,
                    owner_tenant_id=None,
                    skill_key=spec.key,
                    name=spec.name,
                    description=spec.description,
                    node_type=spec.node_type,
                    executor_type="gpt_skill",
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(skill)
                self.session.flush()
            version = self.session.get(CreativeSkillVersionModel, spec.version_id)
            if version is None:
                self.session.add(
                    CreativeSkillVersionModel(
                        id=spec.version_id,
                        skill_id=spec.skill_id,
                        owner_tenant_id=None,
                        version=1,
                        status="published",
                        instructions=spec.instructions,
                        input_schema_json=dict(spec.input_schema),
                        output_schema_json=dict(spec.output_schema),
                        knowledge_refs_json=list(spec.knowledge_refs),
                        provider="openai",
                        preferred_model=None,
                        metadata_json={"system_default": True},
                        created_by="system",
                        created_at=now,
                        published_at=now,
                    )
                )
                self.session.flush()

    @staticmethod
    def _skill_visible(skill: CreativeSkillModel, tenant_id: str) -> bool:
        return skill.owner_tenant_id in {None, tenant_id}

    @staticmethod
    def _version_visible(version: CreativeSkillVersionModel, tenant_id: str) -> bool:
        return version.owner_tenant_id in {None, tenant_id}

    def visible_skill(self, tenant_id: str, skill_id: str) -> CreativeSkillModel | None:
        self.ensure_builtins()
        skill = self.session.get(CreativeSkillModel, skill_id)
        if skill is None or not skill.active or not self._skill_visible(skill, tenant_id):
            return None
        return skill

    def visible_version(self, tenant_id: str, version_id: str) -> CreativeSkillVersionModel | None:
        self.ensure_builtins()
        version = self.session.get(CreativeSkillVersionModel, version_id)
        if version is None or not self._version_visible(version, tenant_id):
            return None
        skill = self.visible_skill(tenant_id, version.skill_id)
        return version if skill is not None else None

    def list_skills(
        self,
        tenant_id: str,
        *,
        include_content: bool = True,
    ) -> list[dict[str, Any]]:
        self.ensure_builtins()
        skills = list(
            self.session.scalars(
                select(CreativeSkillModel)
                .where(
                    CreativeSkillModel.active.is_(True),
                    or_(
                        CreativeSkillModel.owner_tenant_id.is_(None),
                        CreativeSkillModel.owner_tenant_id == tenant_id,
                    ),
                )
                .order_by(CreativeSkillModel.node_type, CreativeSkillModel.name)
            )
        )
        if not skills:
            return []

        versions = list(
            self.session.scalars(
                select(CreativeSkillVersionModel)
                .where(
                    CreativeSkillVersionModel.skill_id.in_(
                        [skill.id for skill in skills]
                    ),
                    or_(
                        CreativeSkillVersionModel.owner_tenant_id.is_(None),
                        CreativeSkillVersionModel.owner_tenant_id == tenant_id,
                    ),
                )
                .order_by(
                    CreativeSkillVersionModel.skill_id,
                    CreativeSkillVersionModel.version.desc(),
                )
            )
        )
        by_skill: dict[str, list[CreativeSkillVersionModel]] = {
            skill.id: [] for skill in skills
        }
        version_by_id = {}
        for version in versions:
            by_skill.setdefault(version.skill_id, []).append(version)
            version_by_id[version.id] = version

        tenant_bindings = list(
            self.session.scalars(
                select(CreativeSkillBindingModel).where(
                    CreativeSkillBindingModel.tenant_id == tenant_id,
                    CreativeSkillBindingModel.scope_key == "tenant",
                    CreativeSkillBindingModel.active.is_(True),
                )
            )
        )
        binding_by_node = {row.node_type: row for row in tenant_bindings}

        result: list[dict[str, Any]] = []
        for skill in skills:
            summary = self.skill_summary(
                skill,
                by_skill.get(skill.id, []),
                include_content=include_content,
            )
            binding = binding_by_node.get(skill.node_type)
            bound_version = (
                version_by_id.get(binding.skill_version_id)
                if binding is not None
                else None
            )
            if bound_version is not None and bound_version.skill_id == skill.id:
                summary["effective_tenant_version_id"] = bound_version.id
                summary["effective_tenant_scope"] = "tenant"
            else:
                spec = _BUILTIN_BY_NODE.get(skill.node_type)
                if (
                    binding is None
                    and spec is not None
                    and spec.skill_id == skill.id
                ):
                    summary["effective_tenant_version_id"] = spec.version_id
                    summary["effective_tenant_scope"] = "system_default"
                else:
                    summary["effective_tenant_version_id"] = None
                    summary["effective_tenant_scope"] = None
            result.append(summary)
        return result

    @staticmethod
    def version_summary(
        version: CreativeSkillVersionModel,
        *,
        include_content: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "knowledge_refs": list(version.knowledge_refs_json or []),
            "provider": version.provider,
            "preferred_model": version.preferred_model,
            "owner_tenant_id": version.owner_tenant_id,
            "created_by": version.created_by,
            "created_at": version.created_at,
            "published_at": version.published_at,
        }
        if include_content:
            payload.update(
                {
                    "instructions": version.instructions,
                    "input_schema": version.input_schema_json or {},
                    "output_schema": version.output_schema_json or {},
                }
            )
        return payload

    @classmethod
    def skill_summary(
        cls,
        skill: CreativeSkillModel,
        versions: list[CreativeSkillVersionModel],
        *,
        include_content: bool = True,
    ) -> dict[str, Any]:
        published = next((item for item in versions if item.status == "published"), None)
        return {
            "id": skill.id,
            "key": skill.skill_key,
            "name": skill.name,
            "description": skill.description,
            "node_type": skill.node_type,
            "executor_type": skill.executor_type,
            "owner_tenant_id": skill.owner_tenant_id,
            "active": skill.active,
            "latest_published_version": published.version if published else None,
            "versions": [
                cls.version_summary(
                    item,
                    include_content=include_content,
                )
                for item in versions
            ],
        }

    def create_version(
        self,
        *,
        tenant_id: str,
        skill_id: str,
        instructions: str,
        actor_id: str | None,
        preferred_model: str | None = None,
        knowledge_refs: list[str] | None = None,
        status: str = "published",
    ) -> CreativeSkillVersionModel:
        skill = self.visible_skill(tenant_id, skill_id)
        if skill is None:
            raise CreativeSkillRegistryError("skill_not_found")
        if status not in {"draft", "published"}:
            raise CreativeSkillRegistryError("invalid_skill_version_status")
        normalized_instructions = str(instructions).strip()
        if not normalized_instructions or len(normalized_instructions) > 50_000:
            raise CreativeSkillRegistryError("invalid_skill_instructions")
        normalized_model = (
            str(preferred_model).strip()[:128] if preferred_model else None
        ) or None
        required_ref_order = _BUILTIN_BY_NODE[skill.node_type].knowledge_refs
        refs = list(dict.fromkeys(knowledge_refs or required_ref_order))
        required_refs = set(required_ref_order)
        if (
            not refs
            or any(ref not in SUPPORTED_KNOWLEDGE_REFS for ref in refs)
            or set(refs) != required_refs
        ):
            raise CreativeSkillRegistryError("invalid_skill_knowledge_refs")
        refs = [ref for ref in required_ref_order if ref in required_refs]

        # Two operators can publish at nearly the same time. Allocate the
        # tenant-local version optimistically and retry a bounded number of
        # times if the unique version index wins in the other transaction.
        for allocation_attempt in range(3):
            visible_versions = list(
                self.session.scalars(
                    select(CreativeSkillVersionModel).where(
                        CreativeSkillVersionModel.skill_id == skill.id,
                        or_(
                            CreativeSkillVersionModel.owner_tenant_id.is_(None),
                            CreativeSkillVersionModel.owner_tenant_id == tenant_id,
                        ),
                    )
                )
            )
            next_version = (
                max((item.version for item in visible_versions), default=0) + 1
            )
            base = max(
                visible_versions,
                key=lambda item: item.version,
                default=None,
            )
            row = CreativeSkillVersionModel(
                skill_id=skill.id,
                owner_tenant_id=tenant_id,
                version=next_version,
                status=status,
                instructions=normalized_instructions,
                input_schema_json=dict(base.input_schema_json if base else {}),
                output_schema_json=dict(base.output_schema_json if base else {}),
                knowledge_refs_json=refs,
                provider=base.provider if base else "openai",
                preferred_model=normalized_model,
                metadata_json={
                    "customized_from_version": base.version if base else None
                },
                created_by=actor_id,
                published_at=utcnow() if status == "published" else None,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(row)
                    self.session.flush()
                return row
            except IntegrityError:
                if allocation_attempt == 2:
                    raise CreativeSkillRegistryError(
                        "skill_version_allocation_conflict"
                    )
        raise CreativeSkillRegistryError("skill_version_allocation_conflict")

    def _validate_scope(self, tenant_id: str, scope_type: str, scope_id: str | None) -> None:
        if scope_type == "tenant":
            if scope_id is not None:
                raise CreativeSkillRegistryError("tenant_scope_must_not_have_scope_id")
            return
        if not scope_id:
            raise CreativeSkillRegistryError("skill_scope_id_required")
        model = SourceGroupModel if scope_type == "source_group" else ListingTaskModel
        row = self.session.scalar(
            select(model.id).where(model.tenant_id == tenant_id, model.id == scope_id)
        )
        if row is None:
            raise CreativeSkillRegistryError("skill_scope_not_found")

    def set_binding(
        self,
        *,
        tenant_id: str,
        node_type: str,
        skill_version_id: str,
        scope_type: str,
        scope_id: str | None,
        actor_id: str | None,
    ) -> CreativeSkillBindingModel:
        if node_type not in SKILL_NODE_TYPES:
            raise CreativeSkillRegistryError("invalid_skill_node_type")
        if scope_type not in SKILL_SCOPE_TYPES:
            raise CreativeSkillRegistryError("invalid_skill_scope")
        self._validate_scope(tenant_id, scope_type, scope_id)
        version = self.visible_version(tenant_id, skill_version_id)
        if version is None or version.status != "published":
            raise CreativeSkillRegistryError("published_skill_version_required")
        skill = self.visible_skill(tenant_id, version.skill_id)
        if skill is None or skill.node_type != node_type:
            raise CreativeSkillRegistryError("skill_node_type_mismatch")
        key = scope_key(scope_type, scope_id)
        lookup = (
            select(CreativeSkillBindingModel)
            .where(
                CreativeSkillBindingModel.tenant_id == tenant_id,
                CreativeSkillBindingModel.node_type == node_type,
                CreativeSkillBindingModel.scope_key == key,
            )
            .with_for_update()
        )
        row = self.session.scalar(lookup)
        if row is None:
            candidate = CreativeSkillBindingModel(
                tenant_id=tenant_id,
                node_type=node_type,
                scope_type=scope_type,
                scope_id=scope_id,
                scope_key=key,
                skill_version_id=version.id,
                active=True,
                created_by=actor_id,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(candidate)
                    self.session.flush()
                return candidate
            except IntegrityError:
                # Another request inserted the same scope while this request
                # was validating it. Re-read and update the winning row.
                row = self.session.scalar(lookup)
                if row is None:
                    raise CreativeSkillRegistryError(
                        "skill_binding_conflict"
                    )
        row.skill_version_id = version.id
        row.active = True
        row.created_by = actor_id
        row.updated_at = utcnow()
        self.session.flush()
        return row

    def remove_binding(
        self,
        *,
        tenant_id: str,
        node_type: str,
        scope_type: str,
        scope_id: str | None,
    ) -> bool:
        key = scope_key(scope_type, scope_id)
        row = self.session.scalar(
            select(CreativeSkillBindingModel).where(
                CreativeSkillBindingModel.tenant_id == tenant_id,
                CreativeSkillBindingModel.node_type == node_type,
                CreativeSkillBindingModel.scope_key == key,
            )
        )
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def _resolved_from(
        self,
        *,
        skill: CreativeSkillModel,
        version: CreativeSkillVersionModel,
        binding_scope: str,
        binding_id: str | None,
    ) -> ResolvedCreativeSkill:
        return ResolvedCreativeSkill(
            skill_id=skill.id,
            skill_version_id=version.id,
            skill_key=skill.skill_key,
            name=skill.name,
            node_type=skill.node_type,
            version=version.version,
            instructions=version.instructions,
            input_schema=dict(version.input_schema_json or {}),
            output_schema=dict(version.output_schema_json or {}),
            knowledge_refs=tuple(str(item) for item in (version.knowledge_refs_json or [])),
            provider=version.provider,
            preferred_model=version.preferred_model,
            binding_scope=binding_scope,
            binding_id=binding_id,
        )

    def resolve_version(
        self,
        *,
        tenant_id: str,
        skill_version_id: str,
        node_type: str,
        binding_scope: str,
        binding_id: str | None = None,
    ) -> ResolvedCreativeSkill:
        if node_type not in SKILL_NODE_TYPES:
            raise CreativeSkillRegistryError("skill_not_supported_for_node")
        self.ensure_builtins()
        version = self.session.get(
            CreativeSkillVersionModel, skill_version_id
        )
        if version is None or not self._version_visible(version, tenant_id):
            raise CreativeSkillRegistryError("skill_version_not_found")
        skill = self.session.get(CreativeSkillModel, version.skill_id)
        if (
            skill is None
            or not self._skill_visible(skill, tenant_id)
            or skill.node_type != node_type
        ):
            raise CreativeSkillRegistryError("skill_node_type_mismatch")
        return self._resolved_from(
            skill=skill,
            version=version,
            binding_scope=binding_scope,
            binding_id=binding_id,
        )

    def resolve_for_node(
        self,
        *,
        tenant_id: str,
        listing: ListingTaskModel,
        node_type: str,
        node_run_id: str,
    ) -> ResolvedCreativeSkill:
        # Retries must be reproducible. Once a node has started with a skill
        # version, keep that immutable version even if an operator changes the
        # tenant/group/listing binding before the retry occurs. A regeneration
        # creates a new node/run and therefore picks up the newer binding.
        previous = self.session.scalar(
            select(CreativeSkillExecutionModel)
            .where(
                CreativeSkillExecutionModel.tenant_id == tenant_id,
                CreativeSkillExecutionModel.node_run_id == node_run_id,
            )
            .order_by(
                CreativeSkillExecutionModel.attempt_number.desc(),
                CreativeSkillExecutionModel.started_at.desc(),
                CreativeSkillExecutionModel.id.desc(),
            )
            .limit(1)
        )
        if previous is not None:
            return self.resolve_version(
                tenant_id=tenant_id,
                skill_version_id=previous.skill_version_id,
                node_type=node_type,
                binding_scope=previous.binding_scope,
            )
        return self.resolve(
            tenant_id=tenant_id,
            listing=listing,
            node_type=node_type,
        )

    def resolve(
        self,
        *,
        tenant_id: str,
        listing: ListingTaskModel,
        node_type: str,
    ) -> ResolvedCreativeSkill:
        if node_type not in SKILL_NODE_TYPES:
            raise CreativeSkillRegistryError("skill_not_supported_for_node")
        self.ensure_builtins()

        candidates = [
            ("listing", scope_key("listing", listing.id)),
            ("source_group", scope_key("source_group", listing.source_group_id)),
            ("tenant", scope_key("tenant")),
        ]
        rows = list(
            self.session.scalars(
                select(CreativeSkillBindingModel).where(
                    CreativeSkillBindingModel.tenant_id == tenant_id,
                    CreativeSkillBindingModel.node_type == node_type,
                    CreativeSkillBindingModel.active.is_(True),
                    CreativeSkillBindingModel.scope_key.in_([item[1] for item in candidates]),
                )
            )
        )
        by_key = {row.scope_key: row for row in rows}
        for scope_name, key in candidates:
            binding = by_key.get(key)
            if binding is None:
                continue
            version = self.visible_version(tenant_id, binding.skill_version_id)
            if version is None or version.status != "published":
                raise CreativeSkillRegistryError(
                    "skill_binding_version_unavailable"
                )
            skill = self.visible_skill(tenant_id, version.skill_id)
            if skill is None or skill.node_type != node_type:
                raise CreativeSkillRegistryError("skill_binding_invalid")
            return self._resolved_from(
                skill=skill,
                version=version,
                binding_scope=scope_name,
                binding_id=binding.id,
            )

        spec = _BUILTIN_BY_NODE[node_type]
        skill = self.session.get(CreativeSkillModel, spec.skill_id)
        version = self.session.get(CreativeSkillVersionModel, spec.version_id)
        if (
            skill is None
            or version is None
            or not skill.active
            or version.status != "published"
        ):
            raise CreativeSkillRegistryError("system_skill_unavailable")
        return self._resolved_from(
            skill=skill,
            version=version,
            binding_scope="system_default",
            binding_id=None,
        )

    def effective_for_listing(
        self,
        *,
        tenant_id: str,
        listing: ListingTaskModel,
    ) -> list[dict[str, Any]]:
        result = []
        for node_type in ("idea_story", "prompt"):
            resolved = self.resolve(
                tenant_id=tenant_id,
                listing=listing,
                node_type=node_type,
            )
            result.append(
                {
                    "node_type": node_type,
                    "executor_type": "gpt_skill",
                    "skill_id": resolved.skill_id,
                    "skill_version_id": resolved.skill_version_id,
                    "skill_key": resolved.skill_key,
                    "skill_name": resolved.name,
                    "skill_version": resolved.version,
                    "provider": resolved.provider,
                    "preferred_model": resolved.preferred_model,
                    "knowledge_refs": list(resolved.knowledge_refs),
                    "binding_scope": resolved.binding_scope,
                    "binding_id": resolved.binding_id,
                }
            )
        return result