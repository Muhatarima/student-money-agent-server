"""initial tables

Revision ID: 0001
Revises:
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("wa_id_encrypted", sa.Text(), nullable=False),
        sa.Column("linked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("frozen", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_phone_hash"), "users", ["phone_hash"], unique=True)

    op.create_table(
        "passkeys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("transports", sa.Text(), nullable=True),
        sa.Column("device_label", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_passkeys_credential_id"), "passkeys", ["credential_id"], unique=True)
    op.create_index(op.f("ix_passkeys_user_id"), "passkeys", ["user_id"], unique=False)

    op.create_table(
        "link_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("wa_id_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_link_tokens_phone_hash"), "link_tokens", ["phone_hash"], unique=False)
    op.create_index(op.f("ix_link_tokens_token_hash"), "link_tokens", ["token_hash"], unique=True)

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_approvals_token_hash"), "approvals", ["token_hash"], unique=True)
    op.create_index(op.f("ix_approvals_user_id"), "approvals", ["user_id"], unique=False)

    op.create_table(
        "signatures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("authenticator_data", sa.LargeBinary(), nullable=False),
        sa.Column("client_data_json", sa.LargeBinary(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signatures_approval_id"), "signatures", ["approval_id"], unique=False)
    op.create_index(op.f("ix_signatures_user_id"), "signatures", ["user_id"], unique=False)

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=128), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_user_id"), "documents", ["user_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_hash", sa.String(length=128), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_documents_user_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_signatures_user_id"), table_name="signatures")
    op.drop_index(op.f("ix_signatures_approval_id"), table_name="signatures")
    op.drop_table("signatures")
    op.drop_index(op.f("ix_approvals_user_id"), table_name="approvals")
    op.drop_index(op.f("ix_approvals_token_hash"), table_name="approvals")
    op.drop_table("approvals")
    op.drop_index(op.f("ix_link_tokens_token_hash"), table_name="link_tokens")
    op.drop_index(op.f("ix_link_tokens_phone_hash"), table_name="link_tokens")
    op.drop_table("link_tokens")
    op.drop_index(op.f("ix_passkeys_user_id"), table_name="passkeys")
    op.drop_index(op.f("ix_passkeys_credential_id"), table_name="passkeys")
    op.drop_table("passkeys")
    op.drop_index(op.f("ix_users_phone_hash"), table_name="users")
    op.drop_table("users")
