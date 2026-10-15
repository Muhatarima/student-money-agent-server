from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field


class Currency(str, Enum):
    BDT = "BDT"
    IDR = "IDR"
    USD = "USD"


class TransferProposal(BaseModel):
    user_id: UUID
    from_currency: Currency
    to_currency: Currency
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)  # in from_currency
    recipient_id: UUID | None = None   # None = user's own wallet
    idempotency_key: str = Field(min_length=8, max_length=100)


class RuleProposal(BaseModel):
    user_id: UUID
    from_currency: Currency
    to_currency: Currency
    target_rate: Decimal = Field(gt=0)
    amount_per_run: Decimal = Field(gt=0)
    max_per_month: Decimal = Field(gt=0)
    valid_until: datetime
    idempotency_key: str = Field(min_length=8, max_length=100)


class ProposalResult(BaseModel):
    status: str                      # "pending_approval" | "rejected"
    reason: str | None = None        # why rejected (safe to show user)
    approval_url: str | None = None
    subject_id: UUID | None = None   # transfer_id or mandate_id


class RateOut(BaseModel):
    pair: str                        # "BDT_IDR"
    rate: Decimal
    fetched_at: datetime


class BalanceOut(BaseModel):
    user_id: UUID
    balances: dict[str, Decimal]     # {"BDT": ..., "IDR": ...}


class ApprovalCreate(BaseModel):
    user_id: UUID
    kind: str                        # "transfer" | "rule"
    subject_id: UUID                 # transfer_id or mandate_id (owned by B)
    payload_hash: str                # sha256 hex of canonical JSON of what is approved
    summary: dict                    # human-readable fields to show on the page
    expires_at: datetime


class ApprovalCreated(BaseModel):
    approval_id: UUID
    url: str


class ApprovalConfirmed(BaseModel):
    approval_id: UUID
    subject_id: UUID
    payload_hash: str
    signature_id: UUID               # stored passkey assertion (owned by C)


class VerifySignatureIn(BaseModel):
    user_id: UUID
    signature_id: UUID
    payload_hash: str


class VerifySignatureOut(BaseModel):
    valid: bool


class UserOut(BaseModel):
    user_id: UUID
    linked: bool
    frozen: bool


class LinkTokenOut(BaseModel):
    url: str
    expires_at: datetime
