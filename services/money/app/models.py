import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Rates need more decimals than money (e.g. BDT->USD = 0.00820000)
Rate = Numeric(20, 8)


class OwnerType(enum.StrEnum):
    USER = "user"
    HOUSE = "house"


class TransferState(enum.StrEnum):
    CREATED = "CREATED"
    QUOTED = "QUOTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class MandateState(enum.StrEnum):
    PENDING_SIGNATURE = "pending_signature"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"


def str_enum(cls: type[enum.StrEnum]) -> Enum:
    # VARCHAR + CHECK instead of a Postgres ENUM type: adding a state later is an easy migration
    return Enum(
        cls,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


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
    # Every Decimal is money, every datetime is timezone-aware. Never float.
    type_annotation_map = {
        Decimal: Numeric(20, 4),
        datetime: DateTime(timezone=True),
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Account(IdMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        # NULLS NOT DISTINCT: house accounts have owner_id NULL and must still be unique
        UniqueConstraint(
            "owner_type", "owner_id", "name", "currency", postgresql_nulls_not_distinct=True
        ),
    )

    owner_type: Mapped[OwnerType] = mapped_column(str_enum(OwnerType))
    owner_id: Mapped[uuid.UUID | None]
    name: Mapped[str] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(3))


class LedgerEntry(IdMixin, Base):
    # Insert-only. A DB trigger blocks UPDATE and DELETE (see migration 0002).
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="nonzero_amount"),
        # Balance = SUM(amount) per account; INCLUDE lets Postgres answer from the index alone
        Index("ix_ledger_entries_account_id", "account_id", postgresql_include=["amount"]),
    )

    transfer_id: Mapped[uuid.UUID] = mapped_column(index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3))


class Quote(IdMixin, Base):
    __tablename__ = "quotes"

    pair: Mapped[str] = mapped_column(String(7))
    rate: Mapped[Decimal] = mapped_column(Rate)
    fee: Mapped[Decimal]
    amount_in: Mapped[Decimal]
    amount_out: Mapped[Decimal]
    expires_at: Mapped[datetime]
    provider: Mapped[str] = mapped_column(String(30))


class Mandate(IdMixin, Base):
    __tablename__ = "mandates"

    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    from_currency: Mapped[str] = mapped_column(String(3))
    to_currency: Mapped[str] = mapped_column(String(3))
    target_rate: Mapped[Decimal] = mapped_column(Rate)
    amount_per_run: Mapped[Decimal]
    max_per_month: Mapped[Decimal]
    valid_until: Mapped[datetime]
    state: Mapped[MandateState] = mapped_column(
        str_enum(MandateState), default=MandateState.PENDING_SIGNATURE
    )
    signature_id: Mapped[str | None] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    last_run_at: Mapped[datetime | None]


class Transfer(IdMixin, Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="positive_amount"),
        # Daily limit: SUM(amount) WHERE user_id = ? AND created_at >= today
        Index("ix_transfers_user_id_created_at", "user_id", "created_at"),
        # Monthly limit per mandate (most transfers have no mandate, so skip those rows)
        Index(
            "ix_transfers_mandate_id_created_at",
            "mandate_id",
            "created_at",
            postgresql_where=text("mandate_id IS NOT NULL"),
        ),
        # Reconciliation only looks at the few rows stuck in SUBMITTED
        Index(
            "ix_transfers_submitted_updated_at",
            "updated_at",
            postgresql_where=text("state = 'SUBMITTED'"),
        ),
    )

    user_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(20), default="conversion")
    from_currency: Mapped[str] = mapped_column(String(3))
    to_currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal]
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id"))
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandates.id"))
    state: Mapped[TransferState] = mapped_column(
        str_enum(TransferState), default=TransferState.CREATED
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    approval_id: Mapped[str | None] = mapped_column(String(64))
    provider_ref: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Recipient(IdMixin, Base):
    __tablename__ = "recipients"

    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    provider_token: Mapped[str] = mapped_column(String(255))
    masked_label: Mapped[str] = mapped_column(String(50))
    cooling_until: Mapped[datetime]


class Freeze(Base):
    __tablename__ = "freezes"

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    frozen_at: Mapped[datetime] = mapped_column(server_default=func.now())
