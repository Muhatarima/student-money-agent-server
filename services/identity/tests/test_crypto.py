import hashlib

from app.crypto import decrypt_str, encrypt_str, hash_token, new_token


def test_encrypt_decrypt_roundtrip():
    secret = "+8801700000000"
    encrypted = encrypt_str(secret)
    assert encrypted != secret
    decrypted = decrypt_str(encrypted)
    assert decrypted == secret


def test_new_token():
    t1 = new_token()
    t2 = new_token()
    assert len(t1) >= 40
    assert t1 != t2


def test_hash_token():
    token = "sample-secret-token"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert hash_token(token) == expected
