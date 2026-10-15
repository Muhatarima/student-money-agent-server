import hashlib
import hmac
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
SECRET = os.environ["WA_APP_SECRET"].encode()
URL = "http://127.0.0.1:8001/webhook"

body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
good_sig = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()

# Test A: correctly signed -> should be 200
r = httpx.post(URL, content=body, headers={"X-Hub-Signature-256": good_sig,
                                           "Content-Type": "application/json"})
print("Genuine message:", r.status_code, r.json())

# Test B: attacker with a fake signature -> should be 401
r = httpx.post(URL, content=body, headers={"X-Hub-Signature-256": "sha256=fake",
                                           "Content-Type": "application/json"})
print("Fake signature: ", r.status_code, r.json())

# Test C: message changed after signing -> should be 401
tampered = body.replace(b"entry", b"hack!")
r = httpx.post(URL, content=tampered, headers={"X-Hub-Signature-256": good_sig,
                                               "Content-Type": "application/json"})
print("Tampered body:  ", r.status_code, r.json())