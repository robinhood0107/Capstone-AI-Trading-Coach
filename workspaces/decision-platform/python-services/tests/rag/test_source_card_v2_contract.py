from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.source_card_v2_contract import (
    RAG_SOURCE_CARD_V2_SCHEMA_PATH,
    RagSourceCardV2ContractError,
    parse_source_card_v2_front_matter,
    validate_source_card_v2_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
EXAMPLES_ROOT = REPO_ROOT / "contracts/examples"


def _load(relative_path: str) -> object:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "relative_path",
    [
        "contracts/examples/rag-source-card-v2.official-migration.valid.json",
        "contracts/examples/rag-source-card-v2.naver-official.valid.json",
        "contracts/examples/rag-source-card-v2.scholarly.valid.json",
    ],
)
def test_python_validator_accepts_all_v2_authority_variants(relative_path: str) -> None:
    payload = _load(relative_path)

    validated = validate_source_card_v2_payload(payload)

    assert validated["schemaVersion"] == "2"
    assert validated["cardVariant"] in {
        "OFFICIAL_UPSTREAM_CARD",
        "SCHOLARLY_PRIMARY_CARD",
    }


def test_python_validator_rejects_every_generated_negative_fixture() -> None:
    invalid_paths = sorted((EXAMPLES_ROOT / "invalid").glob("rag-source-card-v2.*.invalid.json"))
    assert len(invalid_paths) >= 24

    for invalid_path in invalid_paths:
        payload = json.loads(invalid_path.read_text(encoding="utf-8"))
        with pytest.raises(RagSourceCardV2ContractError, match="source card v2"):
            validate_source_card_v2_payload(payload)


def test_python_front_matter_parser_rejects_duplicate_yaml_keys() -> None:
    duplicate_fixture = (
        EXAMPLES_ROOT / "invalid" / "rag-source-card-v2.duplicate-key.invalid.yaml"
    ).read_bytes()

    with pytest.raises(RagSourceCardV2ContractError, match="duplicate"):
        parse_source_card_v2_front_matter(duplicate_fixture)


def test_python_validator_loads_only_the_canonical_v2_schema() -> None:
    assert RAG_SOURCE_CARD_V2_SCHEMA_PATH == (
        REPO_ROOT / "contracts/schemas/rag-source-card-v2.schema.json"
    )
    schema = json.loads(RAG_SOURCE_CARD_V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "contracts/schemas/rag-source-card-v2.schema.json"
    assert schema["additionalProperties"] is False
    assert len(schema["oneOf"]) == 2
