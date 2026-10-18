"""Tests for packages/contracts.

Verifies that payload_hash is stable regardless of dict key insertion order
(canonical_json sorts keys alphabetically).
"""
import pytest
from contracts.hashing import canonical_json, payload_hash


def test_canonical_json_sorts_keys():
    a = canonical_json({"z": 1, "a": 2})
    b = canonical_json({"a": 2, "z": 1})
    assert a == b
    assert a == '{"a":2,"z":1}'


def test_payload_hash_stable_regardless_of_key_order():
    obj1 = {"amount": "10000", "rate": "135.20", "fee": "45"}
    obj2 = {"rate": "135.20", "amount": "10000", "fee": "45"}
    assert payload_hash(obj1) == payload_hash(obj2)


def test_payload_hash_changes_with_different_values():
    h1 = payload_hash({"amount": "10000"})
    h2 = payload_hash({"amount": "10001"})
    assert h1 != h2


def test_payload_hash_is_hex_string():
    h = payload_hash({"key": "value"})
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex = 64 chars
    int(h, 16)           # must be valid hex


def test_canonical_json_uses_compact_separators():
    result = canonical_json({"a": 1})
    assert " " not in result  # no spaces


def test_payload_hash_nested_dict():
    obj = {"outer": {"inner": "value"}, "top": 1}
    # Should not raise and should produce a consistent hash
    h1 = payload_hash(obj)
    h2 = payload_hash({"top": 1, "outer": {"inner": "value"}})
    assert h1 == h2
