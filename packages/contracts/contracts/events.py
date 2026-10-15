from pydantic import BaseModel
from uuid import UUID

class NotifyEvent(BaseModel):
    type: str          # "approval_needed" | "transfer_settled" | "transfer_failed"
                       # | "rule_triggered" | "account_linked" | "frozen"
    user_id: UUID
    data: dict         # e.g. {"url": ...} or {"amount": "10000", "rate": "135.2", "fee": "45"}
