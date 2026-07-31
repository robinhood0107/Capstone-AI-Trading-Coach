from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
_PROVIDER_DIRECTORIES = (_FIXTURE_ROOT / "ecos",)
_FORBIDDEN_KEYS = {
    "authorization",
    "accesstoken",
    "apikey",
    "clientsecret",
    "credential",
    "providerauthheader",
    "providerrawbody",
    "providerrawheader",
    "rawbody",
    "rawheaders",
    "requesturl",
    "responseheaders",
    "xnaverclientid",
    "xnaverclientsecret",
    "xncpapigwapikey",
    "xncpapigwapikeyid",
}
_SYNTHETIC_ENV_CREDENTIALS = (b"ecos-local-secret-must-not-enter-fixtures-20260714",)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _walk(value: object) -> Iterator[tuple[str | None, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            assert isinstance(key, str)
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def _fixture_files() -> list[Path]:
    return sorted(path for directory in _PROVIDER_DIRECTORIES for path in directory.glob("*.json"))


def test_source_fixtures_are_json_with_synthetic_provenance_evidence() -> None:
    files = _fixture_files()
    assert files
    payload_by_provider = {
        directory.name: b"\n".join(path.read_bytes() for path in sorted(directory.glob("*.json")))
        for directory in _PROVIDER_DIRECTORIES
    }

    for path in files:
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    for combined in payload_by_provider.values():
        lowered = combined.lower()
        assert b"synthetic" in lowered or "합성".encode() in combined or b"fixture" in lowered


def test_source_fixtures_exclude_auth_raw_and_credential_fields() -> None:
    for path in _fixture_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in _walk(payload):
            if key is not None:
                assert _normalized_key(key) not in _FORBIDDEN_KEYS, f"forbidden key in {path.name}"
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                query_keys = {_normalized_key(name) for name, _ in parse_qsl(urlsplit(value).query)}
                assert query_keys.isdisjoint(_FORBIDDEN_KEYS), f"credential query in {path.name}"


@pytest.mark.parametrize("credential_bytes", _SYNTHETIC_ENV_CREDENTIALS)
def test_source_fixtures_do_not_embed_environment_credential_bytes(
    credential_bytes: bytes,
) -> None:
    combined = b"\n".join(path.read_bytes() for path in _fixture_files())
    assert credential_bytes not in combined
