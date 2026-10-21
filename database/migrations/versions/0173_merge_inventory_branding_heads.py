"""Unify Catinat inventory and Branding DNA browser-observation migration heads.

Revision ID: 0173_catinat_branding_merge
Revises: 0172_catinat_transfers, 0170_branding_dna_browser_observation_reviews

Both branches descend from 0168. This is a no-op graph merge: the respective
audited migration branches remain separate and are all applied by Alembic.
"""
revision = "0173_catinat_branding_merge"
down_revision = (
    "0172_catinat_transfers",
    "0170_branding_dna_browser_observation_reviews",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
