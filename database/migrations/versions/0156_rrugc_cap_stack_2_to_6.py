"""Reclassify existing Cap Stack Gemini results to the 2–6 cap acceptance range.

Revision ID: 0156_rrugc_cap_stack_2_to_6
Revises: 0155_rrugc_cap_stack_gemini
"""
from alembic import op

revision = "0156_rrugc_cap_stack_2_to_6"
down_revision = "0155_rrugc_cap_stack_gemini"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only reinterpret Gemini classification. Keep original measured counts,
    # manual cap counts and review labels untouched.
    op.execute("""
        UPDATE rrugc_cap_stack_images
        SET count_status = 'out_of_range'
        WHERE caps_detected > 6
          AND is_cap_stack = TRUE
          AND count_status IN ('ready', 'needs_review')
    """)
    op.execute("""
        UPDATE rrugc_cap_stack_images
        SET count_status = 'not_cap_stack'
        WHERE caps_detected < 2
          AND count_status IN ('ready', 'needs_review')
    """)


def downgrade() -> None:
    # We cannot reconstruct past confidence-based statuses; downgrade to
    # a review-required status instead of auto-approving out-of-range images.
    op.execute("""
        UPDATE rrugc_cap_stack_images
        SET count_status = 'needs_review'
        WHERE count_status = 'out_of_range'
    """)
