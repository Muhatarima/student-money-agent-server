"""The handbook demo script (section 10), end to end, on the real services in Docker.

Fakes only at the edges: WhatsApp (WA_FAKE_SEND), the LLM (fake_llm.py), the money
provider (MockProvider) and the fingerprint sensor (passkey.py). Everything in between is
the real code: webhook, queue, worker, agent, notifier, Money, Identity, Postgres, Redis.
"""

import time
import uuid
from decimal import Decimal

from demo import IDENTITY, Student, identity, link_in, money, run_in, text_of

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def link_account(student: Student) -> None:
    """Demo 1: "hi" -> link -> register fingerprint -> "account secured"."""
    student.say("hi")
    welcome = student.wait_for("secure your account")
    assert link_in(welcome).startswith(f"{IDENTITY}/link?t=")
    student.register_passkey(link_in(welcome))
    student.wait_for("Account secured with your passkey")


def balances(student: Student) -> dict[str, Decimal]:
    response = money.get(f"/internal/balance/{student.user_id}")
    return {currency: Decimal(amount) for currency, amount in response.json()["balances"].items()}


def test_demo_script():
    amina = Student("8801700001111")

    # 1. Link the WhatsApp number with a passkey, then give the demo user test money
    link_account(amina)
    assert money.post(f"/dev/seed/{amina.user_id}").status_code == 200  # 10000 BDT

    # 2. Rate question (agent -> get_rate tool -> Money)
    amina.say("what's the BDT to IDR rate?")
    amina.wait_for("1 BDT = 135.20 IDR")

    # 3. One-off conversion: proposal -> approval page -> fingerprint -> receipt
    amina.say("Convert 1000 taka to rupiah")
    page = amina.approve(link_in(amina.wait_for("Please review and approve")))
    assert "1000" in page and "135.2" in page  # the page shows what is being approved
    receipt = text_of(amina.wait_for("✅ Converted"))
    assert "BDT" in receipt and "132496" in receipt  # (1000 - 20 fee) * 135.20
    assert balances(amina) == {"BDT": Decimal("9000"), "IDR": Decimal("132496")}

    # 4. Auto-rule, signed with the fingerprint
    amina.say("Auto-convert 500 taka every time the rate is above 136")
    page = amina.approve(link_in(amina.wait_for("Please review and approve")))
    assert "This rule will run automatically" in page
    [rule] = money.get(f"/internal/rules/{amina.user_id}").json()
    assert rule["status"] == "active"

    # 5. Nudge the mock rate above the target: the rule runs by itself (rates refresh every 60s)
    run_in("redis", "redis-cli", "SET", "rates:mock:BDT_IDR", "136.50")
    assert "65520" in text_of(amina.wait_for("✅ Converted", timeout=150))  # (500-20)*136.50
    amina.wait_for("Your auto-rule ran")
    assert balances(amina) == {"BDT": Decimal("8500"), "IDR": Decimal("198016")}

    # 6. FREEZE: exactly ONE confirmation, even though Identity and Money both publish "frozen"
    amina.say("FREEZE")
    amina.wait_for("Everything is frozen")
    time.sleep(5)  # the notifier has had time to handle both "frozen" events
    assert amina.new_messages() == []

    # ...and everything is blocked: in WhatsApp, in Money and in Identity
    amina.say("Convert 1000 taka to rupiah")
    amina.wait_for("Your account is frozen")
    amina.say("UPLOAD")
    amina.wait_for("Your account is frozen")
    proposal = {
        "user_id": amina.user_id,
        "from_currency": "BDT",
        "to_currency": "IDR",
        "amount": "100",
        "idempotency_key": f"e2e:{uuid.uuid4().hex}",
    }
    result = money.post("/internal/proposals/transfer", json=proposal).json()
    assert result["status"] == "rejected" and "account_frozen" in result["reason"]
    assert [r["status"] for r in money.get(f"/internal/rules/{amina.user_id}").json()] == ["paused"]
    upload_link = identity.post("/internal/upload-links", json={"user_id": amina.user_id})
    assert upload_link.status_code == 409

    # 7. Document upload (a second student: a frozen account can't upload) -> auto-delete date
    budi = Student("6281200002222")
    link_account(budi)
    budi.say("UPLOAD")
    result_page = budi.upload(link_in(budi.wait_for("upload documents only through")), PDF)
    assert "auto-deletes on" in result_page
    assert len(run_in("identity-api", "ls", "-A", "vault_local").split()) == 1

    # 8. Purge, as if 8 days had passed (documents are kept 7 days): the file is gone
    run_in(
        "identity-jobs",
        "python",
        "-c",
        "import asyncio; from datetime import UTC, datetime, timedelta; "
        "from app.jobs import purge_once; "
        "asyncio.run(purge_once(datetime.now(UTC) + timedelta(days=8)))",
    )
    assert run_in("identity-api", "ls", "-A", "vault_local").split() == []
