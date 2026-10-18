"""Add tenant-scoped, reviewed document retrieval for Branding DNA.

Revision ID: 0164_branding_dna_shop_rag
Revises: 0163_branding_dna_scout_presence
No existing data touched; scanning and automatic profile updates remain disabled.
"""
import os
import sqlalchemy as sa
from alembic import op

revision = "0164_branding_dna_shop_rag"
down_revision = "0163_branding_dna_scout_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(150), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("source_note", sa.String(500), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("text_length", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("original_file", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("review_note", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["shop_id"], ["bc_shops.id"],
                                name="fk_bc_rag_document_shop", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "shop_id", "id", name="uq_bc_rag_document_scope"),
        sa.CheckConstraint("byte_count BETWEEN 1 AND 2097152", name="ck_bc_rag_document_bytes"),
        sa.CheckConstraint("status IN ('pending_review','approved','rejected','archived')",
                           name="ck_bc_rag_document_status"),
    )
    op.create_index("ix_bc_rag_document_shop_status", "bc_knowledge_documents",
                    ["tenant_id", "shop_id", "status", "created_at"])
    op.create_table(
        "bc_knowledge_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "document_id"],
            ["bc_knowledge_documents.tenant_id", "bc_knowledge_documents.shop_id",
             "bc_knowledge_documents.id"], name="fk_bc_rag_chunk_document_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_bc_rag_chunk_order"),
    )
    op.create_index("ix_bc_rag_chunks_shop_document", "bc_knowledge_chunks",
                    ["tenant_id", "shop_id", "document_id"])


def downgrade() -> None:
    # Never delete uploaded company documents during an automatic rollback.
    # CI disposable migration tests can explicitly opt in.
    bind = op.get_bind()
    url = bind.engine.url
    if os.environ.get("CAM_ALLOW_RAG_DATA_DROP_FOR_TEST") != "1" or not (
        url.database == "cam_integration" or
        (url.get_backend_name() == "sqlite" and
         (url.database or "").rsplit("/", 1)[-1] == "rag-test.db")
    ):
        raise RuntimeError("Shop knowledge data protected; downgrade denied")
    op.drop_index("ix_bc_rag_chunks_shop_document", table_name="bc_knowledge_chunks")
    op.drop_table("bc_knowledge_chunks")
    op.drop_index("ix_bc_rag_document_shop_status", table_name="bc_knowledge_documents")
    op.drop_table("bc_knowledge_documents")
