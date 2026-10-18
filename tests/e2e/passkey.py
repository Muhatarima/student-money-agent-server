"""A software passkey: does what the phone's fingerprint sensor does, with a key in memory.

It makes real WebAuthn data (attestation "none", ES256 signatures), so Identity checks it
with its normal py_webauthn code. Nothing in the services is switched off for the test.
create() = navigator.credentials.create(), get() = navigator.credentials.get().
"""

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

UP, UV, AT = 0x01, 0x04, 0x40  # flags: user present, user verified (fingerprint), new key


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftPasskey:
    def __init__(self, origin: str, rp_id: str):
        self.origin = origin
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0

    def create(self, options: dict) -> dict:
        assert options["rp"]["id"] == self.rp_id
        client_data = self._client_data("webauthn.create", options["challenge"])
        numbers = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = bytes(16) + len(self.credential_id).to_bytes(2, "big") + self.credential_id
        auth_data = self._auth_data(UP | UV | AT) + attested + cose_key
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return self._credential(
            {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            }
        )

    def get(self, options: dict) -> dict:
        # Like a real phone: only answers if the site asks for one of OUR keys
        allowed = [c["id"] for c in options["allowCredentials"]]
        assert b64url(self.credential_id) in allowed, "this passkey wasn't asked for"
        client_data = self._client_data("webauthn.get", options["challenge"])
        self.sign_count += 1
        auth_data = self._auth_data(UP | UV)
        signed = auth_data + hashlib.sha256(client_data).digest()
        signature = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        return self._credential(
            {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
            }
        )

    def _client_data(self, type_: str, challenge: str) -> bytes:
        data = {"type": type_, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        return json.dumps(data).encode()

    def _auth_data(self, flags: int) -> bytes:
        rp_id_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_id_hash + bytes([flags]) + self.sign_count.to_bytes(4, "big")

    def _credential(self, response: dict) -> dict:
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
