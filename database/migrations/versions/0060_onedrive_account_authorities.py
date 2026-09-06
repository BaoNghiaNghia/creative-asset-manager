"""allow account-specific OneDrive OAuth handoffs

Revision ID: 0060_onedrive_authorities
Revises: 0059_desktop_source_ctx
"""
from alembic import op

revision = "0060_onedrive_authorities"
down_revision = "0059_desktop_source_ctx"
branch_labels = None
depends_on = None

_EXPANDED = (
    "intent IN ('application_login','google_drive_connect','onedrive_connect',"
    "'onedrive_personal_connect','onedrive_work_connect')"
)
_LEGACY = "intent IN ('application_login','google_drive_connect','onedrive_connect')"


def _replace_constraint(expression: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("desktop_oauth_handoffs") as batch:
            batch.drop_constraint("ck_desktop_oauth_handoffs_intent", type_="check")
            batch.create_check_constraint("ck_desktop_oauth_handoffs_intent", expression)
        return
    op.drop_constraint("ck_desktop_oauth_handoffs_intent", "desktop_oauth_handoffs", type_="check")
    op.create_check_constraint("ck_desktop_oauth_handoffs_intent", "desktop_oauth_handoffs", expression)


def upgrade():
    _replace_constraint(_EXPANDED)



def downgrade():
    _replace_constraint(_LEGACY)
