"""A fake fingerprint sensor + fake py_webauthn checks. No real crypto, but the SAME rules:
the signed challenge must be the expected one, the origin must match, and an assertion
only verifies with the public key of the passkey that made it.
"""

import json
from types import SimpleNamespace

from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

ORIGIN = "http://localhost:8003"


def make_credential(challenge: bytes, credential_id: bytes = b"cred-1", origin: str = ORIGIN):
    """What the browser sends back after the user touches the sensor."""
    client_data = json.dumps({"challenge": bytes_to_base64url(challenge), "origin": origin})
    cid = bytes_to_base64url(credential_id)
    return {
        "id": cid,
        "rawId": cid,
        "type": "public-key",
        "response": {
            "clientDataJSON": bytes_to_base64url(client_data.encode()),
            "authenticatorData": bytes_to_base64url(b"authenticator-data"),
            # "signed" with this passkey's private key
            "signature": bytes_to_base64url(b"signed-by-" + credential_id),
            "transports": ["internal"],
        },
    }


def challenge_of(options: dict) -> bytes:
    return base64url_to_bytes(options["challenge"])


def _check(credential, expected_challenge, expected_origin):
    client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    if base64url_to_bytes(client_data["challenge"]) != expected_challenge:
        raise ValueError("challenge mismatch")
    if client_data["origin"] != expected_origin:
        raise ValueError("origin mismatch")


def verify_registration_response(*, credential, expected_challenge, expected_origin, **_):
    _check(credential, expected_challenge, expected_origin)
    credential_id = base64url_to_bytes(credential["rawId"])
    return SimpleNamespace(
        credential_id=credential_id, credential_public_key=b"pk-" + credential_id, sign_count=0
    )


def verify_authentication_response(
    *,
    credential,
    expected_challenge,
    expected_origin,
    credential_public_key,
    credential_current_sign_count,
    **_,
):
    _check(credential, expected_challenge, expected_origin)
    signature = base64url_to_bytes(credential["response"]["signature"])
    # The signature only checks out with the public key of the passkey that signed it
    if b"pk-" + signature.removeprefix(b"signed-by-") != credential_public_key:
        raise ValueError("bad signature")
    return SimpleNamespace(new_sign_count=credential_current_sign_count + 1)
