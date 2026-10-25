"""Allow durable Multi-hat and UGC scene jobs in the existing Ecommerce job table.

Revision ID: 0183_allow_gatorhats_scene_stages
Revises: 0182_ecommerce_color_scoped_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0183_allow_gatorhats_scene_stages"
down_revision = "0182_ecommerce_color_scoped_jobs"
branch_labels = None
depends_on = None

OLD_STAGES = ("prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4")
SCENE_STAGES = (
    "prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3",
    "prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3",
)


def _check(stages):
    return "stage IN (" + ", ".join("'" + item + "'" for item in stages) + ")"


def upgrade():
    with op.batch_alter_table("rrugc_ecommerce_jobs") as batch:
        batch.drop_constraint("ck_rrugc_ecommerce_stage", type_="check")
        batch.create_check_constraint("ck_rrugc_ecommerce_stage", _check(OLD_STAGES + SCENE_STAGES))


def downgrade():
    connection = op.get_bind()
    count = connection.scalar(
        sa.text("SELECT count(*) FROM rrugc_ecommerce_jobs WHERE stage IN (" +
                ", ".join("'" + item + "'" for item in SCENE_STAGES) + ")")
    )
    if count:
        raise RuntimeError("scene_jobs_not_safe_to_downgrade")
    with op.batch_alter_table("rrugc_ecommerce_jobs") as batch:
        batch.drop_constraint("ck_rrugc_ecommerce_stage", type_="check")
        batch.create_check_constraint("ck_rrugc_ecommerce_stage", _check(OLD_STAGES))
