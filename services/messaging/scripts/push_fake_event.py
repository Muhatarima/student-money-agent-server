"""Push a fake event so you can watch the notifier send it.
Run from services/messaging (Redis + mock identity on 8003 + notifier running):
    python -m scripts.push_fake_event transfer_settled
"""

import asyncio
import json
import sys
import uuid

from app.notifier import STREAM
from app.redis_client import redis

EXAMPLES = {
    "approval_needed": {"url": "http://localhost:8003/approve/demo"},
    "transfer_settled": {
        "transfer_id": "demo",
        "amount": "1000.00",
        "from_currency": "BDT",
        "to_currency": "IDR",
        "amount_out": "132300.00",
        "rate": "135.20",
        "fee": "20.00",
    },
    "transfer_failed": {"transfer_id": "demo", "reason": "insufficient_funds"},
    "rule_triggered": {"rule_id": "demo", "transfer_id": "demo", "state": "SETTLED"},
    "rule_skipped": {"rule_id": "demo", "reason": "over_rule_monthly_limit"},
    "rule_paused": {"rule_id": "demo"},
    "account_linked": {},
    "frozen": {},
}


async def main(event_type: str) -> None:
    event = {"type": event_type, "user_id": str(uuid.uuid4()), "data": EXAMPLES[event_type]}
    await redis.xadd(STREAM, {"data": json.dumps(event)})
    print(f"Pushed {event_type}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in EXAMPLES:
        sys.exit(f"Usage: python -m scripts.push_fake_event <{'|'.join(EXAMPLES)}>")
    asyncio.run(main(sys.argv[1]))
