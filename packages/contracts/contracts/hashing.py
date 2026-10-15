import hashlib, json

def canonical_json(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def payload_hash(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()
