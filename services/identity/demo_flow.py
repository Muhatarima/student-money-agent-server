"""Interactive demo walkthrough script for Engineer C (Identity & WebAuthn).

Run while `uvicorn app.main:app --port 8003` is running.
Usage:
    python demo_flow.py [--base-url http://localhost:8003] [--key <INTERNAL_API_KEY>]
"""

import argparse
import hashlib
import sys
import uuid

import httpx
from contracts.hashing import payload_hash


def main():
    parser = argparse.ArgumentParser(description="Student Money Agent - Identity Demo Flow")
    parser.add_argument(
        "--base-url", default="http://localhost:8003", help="Base URL of Identity Service"
    )
    parser.add_argument("--key", default="test-internal-key", help="X-Internal-Key secret")
    parser.add_argument("--phone", default="+8801700000001", help="Student Phone Number")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"X-Internal-Key": args.key}
    phone_hash = hashlib.sha256(args.phone.encode()).hexdigest()
    wa_id = args.phone.replace("+", "")

    print("\n" + "=" * 65)
    print("  STUDENT MONEY AGENT - IDENTITY SERVICE DEMO WALKTHROUGH")
    print("=" * 65)

    # 1. Health check
    print("\n[Step 1] Checking service health...")
    try:
        r = httpx.get(f"{base_url}/health", timeout=5.0)
        print(f" -> GET /health: [{r.status_code}] {r.json()}")
    except Exception as exc:
        print(f" -> ERROR: Could not reach {base_url}/health: {exc}")
        print("Please ensure the service is running: uvicorn app.main:app --port 8003")
        sys.exit(1)

    # 2. Link Token Generation
    print(f"\n[Step 2] Generating biometric link token for phone {args.phone}...")
    r = httpx.post(
        f"{base_url}/internal/link-tokens",
        json={"phone_hash": phone_hash, "wa_id": wa_id},
        headers=headers,
    )
    if r.status_code != 200:
        print(f" -> FAILED [{r.status_code}]: {r.text}")
        sys.exit(1)
    link_data = r.json()
    print(f" -> Registration Link: {link_data['url']}")
    print(" -> (Open this link in your browser to register TouchID / Windows Hello)")

    # 3. Check User status
    print("\n[Step 3] Checking user status in Identity database...")
    r = httpx.get(f"{base_url}/internal/users/by-phone/{phone_hash}", headers=headers)
    if r.status_code == 200:
        user_info = r.json()
        user_id = user_info["user_id"]
        print(f" -> User ID: {user_id}")
        print(f" -> Linked: {user_info['linked']}, Frozen: {user_info['frozen']}")
    else:
        print(" -> User not yet registered in DB (will be created upon biometric registration).")
        user_id = str(uuid.uuid4())

    # 4. Create Transaction Approval
    print("\n[Step 4] Creating financial transaction approval request (e.g. BDT -> IDR)...")
    summary = {
        "amount": "1500.00",
        "from_currency": "BDT",
        "to_currency": "IDR",
        "rate": "131.25",
        "fee": "0.00",
        "recipient": "Bandung Institute Tuition",
    }
    tx_hash = payload_hash(summary)
    print(f" -> Transaction Payload Hash (SHA-256): {tx_hash}")

    r = httpx.post(
        f"{base_url}/internal/approvals",
        json={
            "user_id": user_id,
            "kind": "transfer",
            "subject_id": str(uuid.uuid4()),
            "payload_hash": tx_hash,
            "summary": summary,
        },
        headers=headers,
    )
    if r.status_code == 200:
        appr_data = r.json()
        print(f" -> Approval Created! ID: {appr_data['approval_id']}")
        print(f" -> Approval Page URL: {appr_data['url']}")
        print(" -> (Student sees exact amount, rate, fee countdown, and signs with passkey)")
    else:
        print(f" -> Approval creation returned [{r.status_code}]: {r.text}")

    # 5. Vault Upload Link
    print("\n[Step 5] Generating passkey-gated Document Vault upload link...")
    r = httpx.post(
        f"{base_url}/internal/upload-links",
        json={"user_id": user_id},
        headers=headers,
    )
    if r.status_code == 200:
        upload_data = r.json()
        print(f" -> Vault Upload URL: {upload_data['url']}")
        print(" -> (Student logs in with passkey, uploads passport, envelope-encrypted for 7 days)")
    else:
        print(f" -> Upload link returned [{r.status_code}]: {r.text}")

    print("\n" + "=" * 65)
    print("  DEMO WALKTHROUGH COMPLETE")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
