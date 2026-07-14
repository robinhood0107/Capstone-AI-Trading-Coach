from __future__ import annotations

import hashlib
import math

import pytest

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256


def test_canonical_bytes_are_deterministic_utf8_and_newline_terminated() -> None:
    first = {"한글": "값", "alpha": 1, "nested": {"z": False, "a": None}}
    second = {"nested": {"a": None, "z": False}, "alpha": 1, "한글": "값"}

    expected = '{"alpha":1,"nested":{"a":null,"z":false},"한글":"값"}\n'.encode()

    assert canonical_json_bytes(first) == expected
    assert canonical_json_bytes(second) == expected
    assert b"\\u" not in expected


def test_hash_is_computed_from_exact_bytes_including_final_newline() -> None:
    payload = {"schemaVersion": 1, "source": "ecos"}
    encoded = canonical_json_bytes(payload)

    assert canonical_json_sha256(payload) == hashlib.sha256(encoded).hexdigest()
    assert canonical_json_sha256(payload) != hashlib.sha256(encoded.rstrip(b"\n")).hexdigest()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"value": value})


def test_negative_zero_is_normalized_to_zero() -> None:
    assert canonical_json_bytes({"float": -0.0}) == b'{"float":0}\n'


def test_non_string_object_keys_are_rejected_instead_of_coerced() -> None:
    with pytest.raises((TypeError, ValueError), match="key"):
        canonical_json_bytes({1: "unsafe"})


def test_input_is_not_mutated_during_normalization() -> None:
    payload = {"items": [{"value": -0.0}], "order": [2, 1]}

    canonical_json_bytes(payload)

    assert payload == {"items": [{"value": -0.0}], "order": [2, 1]}
