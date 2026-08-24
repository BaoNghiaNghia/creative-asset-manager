"""Add tenant-scoped dynamic Inventory material registry.

Revision ID: 0051_inventory_material_registry
Revises: 0050_inventory_daily_sheets
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision="0051_inventory_material_registry"
down_revision="0050_inventory_daily_sheets"
branch_labels=None
depends_on=None
JSON_DOCUMENT=sa.JSON().with_variant(postgresql.JSONB(),"postgresql")

def upgrade()->None:
    op.add_column("inventory_items",sa.Column("canonical_dimension",sa.String(32)))
    op.add_column("inventory_items",sa.Column("preferred_unit",sa.String(64)))
    op.add_column("inventory_items",sa.Column("first_seen_at",sa.DateTime(timezone=True)))
    op.add_column("inventory_items",sa.Column("last_seen_at",sa.DateTime(timezone=True)))
    op.add_column("inventory_items",sa.Column("metadata_json",JSON_DOCUMENT,nullable=False,server_default=sa.text("'{}'")))
    op.create_table("inventory_material_external_identities",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(255),nullable=False),sa.Column("item_id",sa.String(36),nullable=False),
        sa.Column("source_type",sa.String(32),nullable=False),sa.Column("source_id",sa.String(2048),nullable=False),sa.Column("external_key",sa.String(255),nullable=False),
        sa.Column("last_seen_name",sa.String(512)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","item_id"],["inventory_items.tenant_id","inventory_items.id"],ondelete="CASCADE",name="fk_inventory_material_identity_item"),
        sa.UniqueConstraint("tenant_id","source_type","source_id","external_key",name="uq_inventory_material_identity_external"))
    op.create_index("ix_inventory_material_identity_item","inventory_material_external_identities",["tenant_id","item_id"])
    op.create_table("inventory_material_package_conversions",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(255),nullable=False),sa.Column("item_id",sa.String(36),nullable=False),
        sa.Column("package_name",sa.String(128),nullable=False),sa.Column("normalized_package",sa.String(128),nullable=False),sa.Column("canonical_value",sa.Numeric(24,8),nullable=False),
        sa.Column("canonical_unit",sa.String(64),nullable=False),sa.Column("approved_by",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","item_id"],["inventory_items.tenant_id","inventory_items.id"],ondelete="CASCADE",name="fk_inventory_material_conversion_item"),
        sa.UniqueConstraint("tenant_id","item_id","normalized_package",name="uq_inventory_material_conversion_package"),
        sa.CheckConstraint("canonical_value > 0",name="ck_inventory_material_conversion_positive"))
    op.create_table("inventory_material_candidates",
        sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(255),nullable=False),sa.Column("source_id",sa.String(2048),nullable=False),
        sa.Column("sheet",sa.String(255),nullable=False),sa.Column("source_row",sa.Integer(),nullable=False),sa.Column("external_key",sa.String(255),nullable=False),
        sa.Column("raw_name",sa.String(512),nullable=False),sa.Column("category",sa.String(255)),sa.Column("status",sa.String(32),nullable=False),
        sa.Column("suggested_item_id",sa.String(36)),sa.Column("suggested_canonical_name",sa.String(512)),sa.Column("confidence",sa.Numeric(7,6)),
        sa.Column("reasons_json",JSON_DOCUMENT,nullable=False,server_default=sa.text("'[]'")),sa.Column("context_json",JSON_DOCUMENT,nullable=False,server_default=sa.text("'{}'")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","suggested_item_id"],["inventory_items.tenant_id","inventory_items.id"],ondelete="RESTRICT",name="fk_inventory_material_candidate_item"),
        sa.UniqueConstraint("tenant_id","source_id","external_key","raw_name",name="uq_inventory_material_candidate_source"),
        sa.CheckConstraint("status IN ('new_material','possible_rename','ambiguous','ignored','approved','rejected')",name="ck_inventory_material_candidate_status"))
    op.create_index("ix_inventory_material_candidate_queue","inventory_material_candidates",["tenant_id","status","created_at"])
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("inventory_items", "metadata_json", server_default=None)

def downgrade()->None:
    op.drop_index("ix_inventory_material_candidate_queue",table_name="inventory_material_candidates");op.drop_table("inventory_material_candidates")
    op.drop_table("inventory_material_package_conversions")
    op.drop_index("ix_inventory_material_identity_item",table_name="inventory_material_external_identities");op.drop_table("inventory_material_external_identities")
    for name in ("metadata_json","last_seen_at","first_seen_at","preferred_unit","canonical_dimension"):op.drop_column("inventory_items",name)
