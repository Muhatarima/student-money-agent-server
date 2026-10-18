import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    MetaData,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Hash = String(64)  # sha256 hex


class ApprovalState(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    REJECTED = "rejected"


class Base(DeclarativeBase):
    # Predictable constraint names, so Alembic migrations stay stable
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True)}


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(IdMixin, Base):
    __tablename__ = "users"

    # Never the raw phone: an HMAC hash made by Messaging (A)
    phone_hash: Mapped[str] = mapped_column(Hash, unique=True)
    wa_id_encrypted: Mapped[str] = mapped_column(Text)  # Fernet, see crypto.py
    linked: Mapped[bool] = mapped_column(default=False)
    frozen: Mapped[bool] = mapped_column(default=False)


class Passkey(IdMixin, Base):
    __tablename__ = "passkeys"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    user: Mapped[User] = relationship()
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(default=0)
    transports: Mapped[str | None] = mapped_column(Text)
    device_label: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[datetime | None]


class LinkToken(IdMixin, Base):
    __tablename__ = "link_tokens"

    token_hash: Mapped[str] = mapped_column(Hash, unique=True)  # only the hash is stored
    phone_hash: Mapped[str] = mapped_column(Hash)
    wa_id_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(index=True)  # purge job
    used_at: Mapped[datetime | None]


class Approval(IdMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        # Freeze: expire all pending approvals of one user
        Index("ix_approvals_user_id_state", "user_id", "state"),
        # Purge job: pending approvals past their expiry
        Index(
            "ix_approvals_pending_expires_at",
            "expires_at",
            postgresql_where=text("state = 'pending'"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))  # "transfer" | "rule"
    subject_id: Mapped[uuid.UUID]  # transfer_id or mandate_id, owned by Money
    payload_hash: Mapped[str] = mapped_column(Hash)
    summary: Mapped[dict] = mapped_column(JSON)
    token_hash: Mapped[str] = mapped_column(Hash, unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    state: Mapped[ApprovalState] = mapped_column(
        Enum(
            ApprovalState,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ApprovalState.PENDING,
    )


class Signature(IdMixin, Base):
    """A stored passkey assertion: proof the user approved exactly payload_hash."""

    __tablename__ = "signatures"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id"))
    credential_id: Mapped[bytes] = mapped_column(LargeBinary)
    payload_hash: Mapped[str] = mapped_column(Hash)
    nonce: Mapped[str] = mapped_column(String(64))
    authenticator_data: Mapped[bytes] = mapped_column(LargeBinary)
    client_data_json: Mapped[bytes] = mapped_column(LargeBinary)
    signature: Mapped[bytes] = mapped_column(LargeBinary)


class UploadLink(IdMixin, Base):
    __tablename__ = "upload_links"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(Hash, unique=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)  # purge job
    used_at: Mapped[datetime | None]


class Document(IdMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Purge job: only documents not deleted yet
        Index(
            "ix_documents_delete_after",
            "delete_after",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(64))  # random, never the file name
    wrapped_key: Mapped[bytes | None] = mapped_column(LargeBinary)  # NULL = destroyed
    content_type: Mapped[str] = mapped_column(String(50))
    size_bytes: Mapped[int]
    delete_after: Mapped[datetime]
    deleted_at: Mapped[datetime | None]


class AuditEvent(Base):
    """Append-only (DB trigger, migration 0002) and hash-chained (see audit.py)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime]
    actor: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    target_hash: Mapped[str | None] = mapped_column(Hash)  # never the target itself
    details: Mapped[dict] = mapped_column(JSON)  # no PII
    prev_hash: Mapped[str] = mapped_column(Hash)
    row_hash: Mapped[str] = mapped_column(Hash)
