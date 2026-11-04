"""Tenant-scoped eight-slot G3 human pilot shortlist.

Revision ID: 0194_typography_g3_pilot_shortlist
Revises: 0193_rrugc_three_quarter_placement
"""
from alembic import op
import sqlalchemy as sa

revision = "0194_typography_g3_pilot_shortlist"
down_revision = "0193_rrugc_three_quarter_placement"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rrugc_typography_pilot_shortlist",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("selected_by_user_id", sa.String(255), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reference_id"],
            ["rrugc_typography_references.tenant_id", "rrugc_typography_references.id"],
            name="fk_typography_pilot_reference_tenant", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "reference_id", name="uq_typography_pilot_reference"),
        sa.UniqueConstraint("tenant_id", "slot", name="uq_typography_pilot_tenant_slot"),
        sa.CheckConstraint("slot >= 1 AND slot <= 8", name="ck_typography_pilot_slot"),
    )


def downgrade():
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM rrugc_typography_pilot_shortlist"))
    if count:
        raise RuntimeError("cannot_drop_nonempty_g3_pilot_shortlist")
    op.drop_table("rrugc_typography_pilot_shortlist")
