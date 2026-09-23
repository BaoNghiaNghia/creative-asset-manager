"""Add versioned GPT Skill registry and execution audit for Creative Pipeline.

Revision ID: 0090_creative_pipeline_gpt_skills
Revises: 0089_r2_video_playback_derivative
"""
from alembic import op
from datetime import datetime, timezone
import sqlalchemy as sa

revision = "0090_creative_pipeline_gpt_skills"
down_revision = "0089_r2_video_playback_derivative"
branch_labels = None
depends_on = None

IDEA_SKILL_ID = "11111111-1111-4111-8111-111111111111"
IDEA_VERSION_ID = "11111111-1111-4111-8111-111111111112"
PROMPT_SKILL_ID = "22222222-2222-4222-8222-222222222222"
PROMPT_VERSION_ID = "22222222-2222-4222-8222-222222222223"


def upgrade():
    op.create_table(
        "creative_pipeline_skills",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_tenant_id", sa.String(255), nullable=True),
        sa.Column("skill_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("node_type", sa.String(48), nullable=False),
        sa.Column("executor_type", sa.String(32), nullable=False, server_default="gpt_skill"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("node_type IN ('idea_story','prompt')", name="ck_cp_skills_node_type"),
        sa.CheckConstraint("executor_type IN ('gpt_skill')", name="ck_cp_skills_executor_type"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cp_skills_node_active",
        "creative_pipeline_skills",
        ["node_type", "active"],
    )
    op.create_index(
        "uq_cp_skills_system_key",
        "creative_pipeline_skills",
        ["skill_key"],
        unique=True,
        postgresql_where=sa.text("owner_tenant_id IS NULL"),
        sqlite_where=sa.text("owner_tenant_id IS NULL"),
    )
    op.create_index(
        "uq_cp_skills_tenant_key",
        "creative_pipeline_skills",
        ["owner_tenant_id", "skill_key"],
        unique=True,
        postgresql_where=sa.text("owner_tenant_id IS NOT NULL"),
        sqlite_where=sa.text("owner_tenant_id IS NOT NULL"),
    )

    op.create_table(
        "creative_pipeline_skill_versions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("skill_id", sa.String(36), nullable=False),
        sa.Column("owner_tenant_id", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=False),
        sa.Column("knowledge_refs_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="openai"),
        sa.Column("preferred_model", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_cp_skill_versions_version"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_cp_skill_versions_status"),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["creative_pipeline_skills.id"],
            name="fk_cp_skill_versions_skill",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cp_skill_versions_skill_status",
        "creative_pipeline_skill_versions",
        ["skill_id", "status", "version"],
    )
    op.create_index(
        "uq_cp_skill_versions_system_number",
        "creative_pipeline_skill_versions",
        ["skill_id", "version"],
        unique=True,
        postgresql_where=sa.text("owner_tenant_id IS NULL"),
        sqlite_where=sa.text("owner_tenant_id IS NULL"),
    )
    op.create_index(
        "uq_cp_skill_versions_tenant_number",
        "creative_pipeline_skill_versions",
        ["skill_id", "owner_tenant_id", "version"],
        unique=True,
        postgresql_where=sa.text("owner_tenant_id IS NOT NULL"),
        sqlite_where=sa.text("owner_tenant_id IS NOT NULL"),
    )

    op.create_table(
        "creative_pipeline_skill_bindings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(48), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(255), nullable=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("skill_version_id", sa.String(36), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("node_type IN ('idea_story','prompt')", name="ck_cp_skill_bindings_node_type"),
        sa.CheckConstraint("scope_type IN ('tenant','source_group','listing')", name="ck_cp_skill_bindings_scope_type"),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["creative_pipeline_skill_versions.id"],
            name="fk_cp_skill_bindings_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "node_type", "scope_key", name="uq_cp_skill_bindings_scope"),
    )
    op.create_index(
        "ix_cp_skill_bindings_tenant_active",
        "creative_pipeline_skill_bindings",
        ["tenant_id", "active", "node_type"],
    )

    op.create_table(
        "creative_pipeline_skill_executions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("pipeline_run_id", sa.String(36), nullable=False),
        sa.Column("node_run_id", sa.String(36), nullable=False),
        sa.Column("listing_task_id", sa.String(36), nullable=False),
        sa.Column("skill_id", sa.String(36), nullable=False),
        sa.Column("skill_version_id", sa.String(36), nullable=False),
        sa.Column("skill_key", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("binding_scope", sa.String(320), nullable=False),
        sa.Column("variant_key", sa.String(64), nullable=False, server_default="default"),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("input_artifact_ids_json", sa.JSON(), nullable=False),
        sa.Column("output_artifact_ids_json", sa.JSON(), nullable=False),
        sa.Column("knowledge_snapshot_id", sa.String(255), nullable=True),
        sa.Column("prompt_sha256", sa.String(128), nullable=True),
        sa.Column("provider_request_id", sa.String(512), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_cp_skill_exec_attempt"),
        sa.CheckConstraint("skill_version > 0", name="ck_cp_skill_exec_version"),
        sa.CheckConstraint("status IN ('running','completed','failed','cancelled')", name="ck_cp_skill_exec_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pipeline_run_id"],
            ["creative_pipeline_runs.tenant_id", "creative_pipeline_runs.id"],
            name="fk_cp_skill_exec_tenant_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_run_id"],
            ["creative_pipeline_node_runs.tenant_id", "creative_pipeline_node_runs.id"],
            name="fk_cp_skill_exec_tenant_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "listing_task_id"],
            ["creative_pipeline_listing_tasks.tenant_id", "creative_pipeline_listing_tasks.id"],
            name="fk_cp_skill_exec_tenant_listing",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["creative_pipeline_skills.id"],
            name="fk_cp_skill_exec_skill",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["creative_pipeline_skill_versions.id"],
            name="fk_cp_skill_exec_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "node_run_id",
            "attempt_number",
            "variant_key",
            name="uq_cp_skill_exec_attempt_variant",
        ),
    )
    op.create_index(
        "ix_cp_skill_exec_tenant_run",
        "creative_pipeline_skill_executions",
        ["tenant_id", "pipeline_run_id", "started_at"],
    )
    op.create_index(
        "ix_cp_skill_exec_tenant_status",
        "creative_pipeline_skill_executions",
        ["tenant_id", "status", "started_at"],
    )

    skills = sa.table(
        "creative_pipeline_skills",
        sa.column("id", sa.String),
        sa.column("owner_tenant_id", sa.String),
        sa.column("skill_key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("node_type", sa.String),
        sa.column("executor_type", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    versions = sa.table(
        "creative_pipeline_skill_versions",
        sa.column("id", sa.String),
        sa.column("skill_id", sa.String),
        sa.column("owner_tenant_id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("status", sa.String),
        sa.column("instructions", sa.Text),
        sa.column("input_schema_json", sa.JSON),
        sa.column("output_schema_json", sa.JSON),
        sa.column("knowledge_refs_json", sa.JSON),
        sa.column("provider", sa.String),
        sa.column("preferred_model", sa.String),
        sa.column("metadata_json", sa.JSON),
        sa.column("created_by", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("published_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        skills,
        [
            {
                "id": IDEA_SKILL_ID,
                "owner_tenant_id": None,
                "skill_key": "creative-idea-story",
                "name": "Creative Idea / Story",
                "description": "GPT skill for structured creative concepts and story beats.",
                "node_type": "idea_story",
                "executor_type": "gpt_skill",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": PROMPT_SKILL_ID,
                "owner_tenant_id": None,
                "skill_key": "creative-video-prompt",
                "name": "Creative Video Prompt",
                "description": "GPT skill for provider-specific structured video prompts.",
                "node_type": "prompt",
                "executor_type": "gpt_skill",
                "active": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    op.bulk_insert(
        versions,
        [
            {
                "id": IDEA_VERSION_ID,
                "skill_id": IDEA_SKILL_ID,
                "owner_tenant_id": None,
                "version": 1,
                "status": "published",
                "instructions": "Use the supplied listing context and frozen knowledge snapshot. Preserve product facts, follow the required output schema exactly, and do not invent unavailable product details.",
                "input_schema_json": {"kind": "creative_pipeline_input_snapshot", "version": 1},
                "output_schema_json": {"kind": "idea_story", "version": 1},
                "knowledge_refs_json": ["idea_story"],
                "provider": "openai",
                "preferred_model": None,
                "metadata_json": {"system_default": True},
                "created_by": "system",
                "created_at": now,
                "published_at": now,
            },
            {
                "id": PROMPT_VERSION_ID,
                "skill_id": PROMPT_SKILL_ID,
                "owner_tenant_id": None,
                "version": 1,
                "status": "published",
                "instructions": "Convert the supplied approved idea into a provider-ready video prompt. Follow the frozen knowledge snapshot and target provider constraints exactly. Return only schema-valid structured output.",
                "input_schema_json": {"kind": "idea_story", "version": 1},
                "output_schema_json": {"kind": "video_prompt_bundle", "version": 1},
                "knowledge_refs_json": ["seedance_2_5", "google_omni"],
                "provider": "openai",
                "preferred_model": None,
                "metadata_json": {"system_default": True},
                "created_by": "system",
                "created_at": now,
                "published_at": now,
            },
        ],
    )


def downgrade():
    op.drop_index("ix_cp_skill_exec_tenant_status", table_name="creative_pipeline_skill_executions")
    op.drop_index("ix_cp_skill_exec_tenant_run", table_name="creative_pipeline_skill_executions")
    op.drop_table("creative_pipeline_skill_executions")
    op.drop_index("ix_cp_skill_bindings_tenant_active", table_name="creative_pipeline_skill_bindings")
    op.drop_table("creative_pipeline_skill_bindings")
    op.drop_index("uq_cp_skill_versions_tenant_number", table_name="creative_pipeline_skill_versions")
    op.drop_index("uq_cp_skill_versions_system_number", table_name="creative_pipeline_skill_versions")
    op.drop_index("ix_cp_skill_versions_skill_status", table_name="creative_pipeline_skill_versions")
    op.drop_table("creative_pipeline_skill_versions")
    op.drop_index("ix_cp_skills_node_active", table_name="creative_pipeline_skills")
    op.drop_table("creative_pipeline_skills")