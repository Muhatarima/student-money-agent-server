import hashlib
import hmac
import json
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()
SECRET = os.environ["WA_APP_SECRET"].encode()
URL = "http://127.0.0.1:8000/webhook"

msg_id = f"wamid.TEST-{uuid.uuid4().hex[:8]}"  # fresh ID each run

payload = {
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {"messages": [{
        "id": msg_id,
        "from": "8801700001234",
        "type": "text",
        "text": {"body": "convert 10000 BDT to IDR"},
    }]}}]}],
}
body = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
headers = {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}

for attempt in (1, 2, 3):  # Meta "retrying" the same message
    r = httpx.post(URL, content=body, headers=headers)
    print(f"Attempt {attempt}:", r.status_code, r.json())