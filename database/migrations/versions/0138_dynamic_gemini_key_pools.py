"""allow dynamic Gemini backup pools for Creative and Inventory

Revision ID: 0138_dynamic_gemini_key_pools
Revises: 0137_rrugc_keyword_favorites
"""
from __future__ import annotations

from alembic import op


revision = "0138_dynamic_gemini_key_pools"
down_revision = "0137_rrugc_keyword_favorites"
branch_labels = None
depends_on = None

_CREATIVE_DYNAMIC = (
    "provider IN ('gemini','gemini_video','gemini_image') "
    "OR provider LIKE 'gemini!_backup!_%' ESCAPE '!' "
    "OR provider LIKE 'gemini!_video!_backup!_%' ESCAPE '!'"
)
_CREATIVE_FIXED = (
    "provider IN ('gemini','gemini_video','gemini_image',"
    "'gemini_backup_1','gemini_backup_2','gemini_backup_3','gemini_backup_4',"
    "'gemini_backup_5','gemini_backup_6','gemini_backup_7','gemini_backup_8',"
    "'gemini_backup_9','gemini_backup_10',"
    "'gemini_video_backup_1','gemini_video_backup_2','gemini_video_backup_3',"
    "'gemini_video_backup_4','gemini_video_backup_5','gemini_video_backup_6',"
    "'gemini_video_backup_7','gemini_video_backup_8','gemini_video_backup_9',"
    "'gemini_video_backup_10')"
)
_INVENTORY_DYNAMIC = "provider = 'gemini' OR provider LIKE 'gemini!_backup!_%' ESCAPE '!'"
_INVENTORY_PRIMARY_ONLY = "provider = 'gemini'"


def upgrade() -> None:
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_creative_ai_credentials_provider", _CREATIVE_DYNAMIC
        )
    with op.batch_alter_table("inventory_ai_credentials") as batch:
        batch.drop_constraint("ck_inventory_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_inventory_ai_credentials_provider", _INVENTORY_DYNAMIC
        )


def _slot(provider: str, prefix: str) -> int | None:
    if not provider.startswith(prefix):
        return None
    suffix = provider[len(prefix):]
    return int(suffix) if suffix.isdigit() else None


def downgrade() -> None:
    bind = op.get_bind()
    creative = [
        str(row[0])
        for row in bind.exec_driver_sql(
            "SELECT provider FROM creative_ai_credentials"
        ).fetchall()
    ]
    if any(
        (slot := _slot(provider, "gemini_backup_")) is not None and slot > 10
        or (slot := _slot(provider, "gemini_video_backup_")) is not None and slot > 10
        for provider in creative
    ):
        raise RuntimeError(
            "Remove Creative Image/Video backup credentials above slot 10 before downgrade."
        )
    inventory_backups = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM inventory_ai_credentials "
        "WHERE provider LIKE 'gemini_backup_%'"
    ).scalar_one()
    if inventory_backups:
        raise RuntimeError(
            "Remove Inventory backup credentials before downgrading 0138."
        )
    with op.batch_alter_table("inventory_ai_credentials") as batch:
        batch.drop_constraint("ck_inventory_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_inventory_ai_credentials_provider", _INVENTORY_PRIMARY_ONLY
        )
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_creative_ai_credentials_provider", _CREATIVE_FIXED
        )
