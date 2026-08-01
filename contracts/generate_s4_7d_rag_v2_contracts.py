from __future__ import annotations

import argparse
import copy
import hashlib
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)


REPO_ROOT = _SCRIPT_REPO_ROOT
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s4-rag-v2-contract.v1.json"
CATALOG_HASH_PATH = (
    REPO_ROOT / "contracts/catalogs/s4-rag-v2-contract.v1.sha256.json"
)
HASH_PATTERN: Final[str] = "^[0-9a-f]{64}$"
OPAQUE_ID_PATTERN: Final[str] = "^[a-z][a-z0-9_-]{2,95}$"
SOURCE_ID_PATTERN: Final[str] = "^src_[a-z0-9][a-z0-9_-]{2,95}$"
REVISION_ID_PATTERN: Final[str] = "^srv_[a-z0-9][a-z0-9_-]{2,95}$"

OA_TRACK_IDS: Final[tuple[str, ...]] = (
    "MICRO_GAME_INFO_MARKET_DESIGN",
    "MACRO_MONETARY_INTERNATIONAL",
    "PROBABILITY_STATISTICS_OPTIMIZATION",
    "ECONOMETRICS_CAUSAL_EVENT_STUDY",
    "TIME_SERIES_REGIME_VOLATILITY",
    "ACCOUNTING_CORPORATE_FINANCE_VALUATION",
    "ASSET_PRICING_FACTOR_PORTFOLIO",
    "FIXED_INCOME_RATES_CREDIT",
    "DERIVATIVES_STOCHASTIC_NUMERICS",
    "MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY",
    "RISK_STRESS_BACKTEST_MODEL_RISK",
    "BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING",
    "FINANCIAL_ML_PIT_DATA_PROVENANCE",
    "CROSS_MARKET_COMMODITIES_POLICY_KOREA",
)
SUPPORTED_MIME_TYPES: Final[tuple[str, ...]] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/html",
    "text/markdown",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/tiff",
)
OCR_CANDIDATES: Final[tuple[str, ...]] = (
    "PADDLE_STRUCTURED",
    "PADDLE_VL",
    "UNLIMITED_GGUF",
)

FROZEN_V1_HASHES: Final[dict[str, str]] = {
    "contracts/openapi/openapi.json": (
        "94414736f6a1c17b95eafffd53a07a5d33d7a66705890c53dcc971eb5ded3f89"
    ),
    "contracts/proto/rag.proto": (
        "d9e4182d5479f27f479187e912d0db02814474dd00306e78b7ef03fb53afc13c"
    ),
    "contracts/proto/rag.descriptor.pb": (
        "633a1214b48221eeaf3d96734353f86dfaf1a20f9e74852511c10b372bf399f4"
    ),
    "contracts/proto/rag.descriptor.sha256": (
        "a519c34e9e8843c8049eb39bf75fe4d94fcfba04f32a029e54a0f9165722332d"
    ),
    "contracts/schemas/rag-source-card-v1.schema.json": (
        "89f25e66d8165ceb813045e17c689e1000bb86f710f8d8c0acb22ccc6d0c846c"
    ),
    "contracts/schemas/rag-source-card-v2.schema.json": (
        "84d3524f69cce5271e757f7f984114fa3f411f31a4d3316be380422418c10ce5"
    ),
    "contracts/schemas/s4-rag-ask-request.schema.json": (
        "b0af6447c0234de050f0277ca52a61ce430ef80c78ffa4c1d58a6bcffc2a0096"
    ),
    "contracts/schemas/s4-rag-answer.schema.json": (
        "e77d12079c84da9a5d9fb5aaf58b07dc01c6cf828f2d62acde3461a03a4c22a1"
    ),
    "contracts/schemas/s4-rag-history-page.schema.json": (
        "3862656a31198654848a92d464efb7b96f82fcb1fd5dbd0460837478de112cdb"
    ),
    "contracts/schemas/s4-rag-history-detail.schema.json": (
        "78b495d57d27d181adbde51c102a0d87a99678af6db52e8ed6e02a7a6c72b879"
    ),
    "capstone-rag/eval/s4-5-evaluation-60.v1.json": (
        "3903d076ae0977ffa5e85b796f727f845220dc98f8b012de2e22bda4ffdf29d8"
    ),
    "capstone-rag/reports/s4-5-fixture-evaluation.v1.json": (
        "b083d755cb408d3d9449d6ecceeb243071fbf514e34c38fecc06d5ac8c43576a"
    ),
    "capstone-rag/manifests/s4-7b-project-source-cards-30.v1.json": (
        "d772ab9a54c5477afeccfd41cd41e496645967dee59dd50c0bcc304ae3c95558"
    ),
    "capstone-rag/manifests/s4-7c-project-source-cards-30-external.v1.json": (
        "d368ea5fee624e923aa3f80322815d0ceaf84ccca9a0ef271b670faade6cfc98"
    ),
}

SCHEMA_IDS: Final[tuple[str, ...]] = (
    "rag-source-card-v3",
    "rag-document-ir-v1",
    "rag-oa-manifest-v1",
    "s4-rag-v2-ask-request",
    "s4-rag-v2-answer",
    "s4-rag-v2-corpus-status",
    "s4-rag-v2-history-page",
    "s4-rag-v2-history-detail",
    "s4-rag-v2-error",
)
SCHEMA_PATHS: Final[dict[str, Path]] = {
    schema_id: REPO_ROOT / f"contracts/schemas/{schema_id}.schema.json"
    for schema_id in SCHEMA_IDS
}

VALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/rag-source-card-v3.project.valid.json",
        "contracts/examples/rag-source-card-v3.oa.valid.json",
        "contracts/examples/rag-source-card-v3.owner.valid.json",
        "contracts/examples/rag-document-ir-v1.native.valid.json",
        "contracts/examples/rag-document-ir-v1.ocr.valid.json",
        "contracts/examples/rag-oa-manifest-v1.draft.valid.json",
        "contracts/examples/s4-rag-v2-ask-request.valid.json",
        "contracts/examples/s4-rag-v2-answer.public-web.valid.json",
        "contracts/examples/s4-rag-v2-answer.local-document.valid.json",
        "contracts/examples/s4-rag-v2-corpus-status.building.valid.json",
        "contracts/examples/s4-rag-v2-history-page.valid.json",
        "contracts/examples/s4-rag-v2-history-detail.local-document.valid.json",
        "contracts/examples/s4-rag-v2-error.corpus-not-ready.valid.json",
    }
)
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/invalid/rag-document-ir-v1.absolute-path.invalid.json",
        "contracts/examples/invalid/rag-document-ir-v1.duplicate-reading-order.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-corpus-selector.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-profile-selector.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-top-k-selector.invalid.json",
        "contracts/examples/invalid/rag-source-card-v3.external-llm-without-local-processing.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-answer.local-citation-url.invalid.json",
        "contracts/examples/invalid/rag-source-card-v3.oa-machine-fetch-disabled.invalid.json",
        "contracts/examples/invalid/rag-source-card-v3.owner-machine-fetch.invalid.json",
        "contracts/examples/invalid/rag-source-card-v3.owner-redistribution.invalid.json",
        "contracts/examples/invalid/rag-oa-manifest-v1.released-underfilled.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-corpus-status.status-leaks-hash.invalid.json",
        "contracts/examples/invalid/rag-source-card-v3.unknown-field.invalid.json",
    }
)
OPENAPI_PATH: Final[str] = "contracts/openapi/rag-v2.openapi.json"
CATALOG_HASH_RELATIVE_PATH: Final[str] = (
    "contracts/catalogs/s4-rag-v2-contract.v1.sha256.json"
)
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        *(path.relative_to(REPO_ROOT).as_posix() for path in SCHEMA_PATHS.values()),
        *VALID_FIXTURE_PATHS,
        *INVALID_FIXTURE_PATHS,
        OPENAPI_PATH,
        CATALOG_HASH_RELATIVE_PATH,
    }
)

_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:/home/|/users/|/mnt/[a-z]/|(?<![a-z])[a-z]:[\\/]|\\\\wsl(?:\.localhost)?\\|file:)"
)


def load_json(path: Path) -> Any:
    """중복 key를 거부하는 canonical loader로 contract JSON만 읽는다."""

    return load_json_bytes_strict(
        path.read_bytes(), source=path.relative_to(REPO_ROOT).as_posix()
    )


def canonical_tree_digest(root: Path) -> str:
    """정규 파일의 상대경로와 개별 digest를 결합해 exact corpus tree를 잠근다."""

    if not root.is_absolute():
        root = (REPO_ROOT / root).resolve(strict=True)
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _validate_frozen_v1() -> None:
    for relative_path, expected_hash in FROZEN_V1_HASHES.items():
        path = REPO_ROOT / relative_path
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ContractValidationError(
                f"frozen v1 input is unavailable: {relative_path}"
            ) from error
        if actual_hash != expected_hash:
            raise ContractValidationError(f"frozen v1 input drifted: {relative_path}")
    if canonical_tree_digest(REPO_ROOT / "capstone-rag/source-cards/s4-7b") != (
        "0336148dd05841861fbd3f054ed5eaa72ea511e341bb0ae205223a7da1de95a2"
    ):
        raise ContractValidationError("exact-30 S4.7B tree drifted")
    if canonical_tree_digest(
        REPO_ROOT / "capstone-rag/source-cards/s4-7c-external"
    ) != "84b70aa3bfc1e24bbff939bd1b6c25d6fb9ec50e03eb36929fa9ede232051874":
        raise ContractValidationError("exact-30 S4.7C tree drifted")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """S4.7D static 선택 집합을 읽고 기존 v1 bytes까지 함께 검증한다."""

    catalog = load_json(path)
    if not isinstance(catalog, dict):
        raise ContractValidationError("S4.7D RAG v2 catalog must be an object")
    validate_catalog(catalog)
    _validate_frozen_v1()
    return catalog


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    expected = {
        "sourceKinds": [
            "PROJECT_SOURCE_CARD",
            "OPEN_ACCESS_DOCUMENT",
            "OWNER_LOCAL_DOCUMENT",
        ],
        "curriculumTracks": list(OA_TRACK_IDS),
        "supportedMimeTypes": list(SUPPORTED_MIME_TYPES),
        "ocrResearchCandidates": list(OCR_CANDIDATES),
        "activeProcessingModes": ["LOCAL_EPHEMERAL_PARSE"],
    }
    if catalog.get("contractId") != "s4-rag-v2-contract.v1":
        raise ContractValidationError("S4.7D catalog identity drifted")
    if catalog.get("schemaVersion") != 1:
        raise ContractValidationError("S4.7D catalog schema version drifted")
    for key, value in expected.items():
        if catalog.get(key) != value:
            raise ContractValidationError(f"S4.7D catalog {key} drifted")
    if catalog.get("productionOcrBackend") != "PADDLE_VL":
        raise ContractValidationError("production OCR backend selection drifted")
    retrieval = catalog.get("retrieval")
    if not isinstance(retrieval, dict) or retrieval.get("rrfK") != 60:
        raise ContractValidationError("application RRF k=60 drifted")
    selection = catalog.get("clientSelection")
    if not isinstance(selection, dict) or any(selection.values()):
        raise ContractValidationError("client retrieval selection must remain disabled")
    if catalog.get("decisionAuthority") != "NONE":
        raise ContractValidationError("RAG v2 cannot gain decision authority")
    bundle = catalog.get("serverSelectedBundle")
    if not isinstance(bundle, dict) or bundle.get("components") != [
        "exact30",
        "oa",
        "ownerPrivate",
    ]:
        raise ContractValidationError("server-selected corpus bundle drifted")
    oa = catalog.get("oaCorpus")
    if not isinstance(oa, dict) or (
        oa.get("minimumSources"),
        oa.get("maximumSources"),
        oa.get("minimumSourcesPerTrack"),
        oa.get("maximumSourcesPerTrack"),
        oa.get("minimumQualityScore"),
    ) != (112, 140, 8, 10, 80):
        raise ContractValidationError("OA112-140 bounds drifted")


def _closed(*, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }


def _schema_document(schema_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(dict(body))
    schema["$id"] = f"contracts/schemas/{schema_id}.schema.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = schema_id
    return schema


def _text(*, minimum: int = 1, maximum: int = 2048) -> dict[str, Any]:
    return {"maxLength": maximum, "minLength": minimum, "type": "string"}


def _hash(*, nullable: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"pattern": HASH_PATTERN, "type": "string"}
    if nullable:
        return {"oneOf": [value, {"type": "null"}]}
    return value


def _https_uri(*, nullable: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "format": "uri",
        "maxLength": 2048,
        "pattern": "^https://",
        "type": "string",
    }
    if nullable:
        return {"oneOf": [value, {"type": "null"}]}
    return value


def _parser_evidence_schema() -> dict[str, Any]:
    ocr = _closed(
        required=["backend", "backendVersion", "modelSha256"],
        properties={
            "backend": {"enum": ["NOT_USED", *OCR_CANDIDATES]},
            "backendVersion": {"oneOf": [_text(maximum=128), {"type": "null"}]},
            "modelSha256": _hash(nullable=True),
        },
    )
    ocr["allOf"] = [
        {
            "if": {"properties": {"backend": {"const": "NOT_USED"}}},
            "then": {
                "properties": {
                    "backendVersion": {"type": "null"},
                    "modelSha256": {"type": "null"},
                }
            },
        },
        {
            "if": {"properties": {"backend": {"enum": list(OCR_CANDIDATES)}}},
            "then": {
                "properties": {
                    "backendVersion": {"type": "string"},
                    "modelSha256": {"type": "string", "pattern": HASH_PATTERN},
                }
            },
        },
    ]
    return _closed(
        required=["parserBackend", "parserVersion", "parserArtifactSha256", "ocr"],
        properties={
            "parserArtifactSha256": _hash(),
            "parserBackend": _text(maximum=128),
            "parserVersion": _text(maximum=128),
            "ocr": ocr,
        },
    )


def _license_evidence_schema() -> dict[str, Any]:
    return _closed(
        required=["accessBasis", "checkedAt", "evidenceUrl", "licenseId", "note"],
        properties={
            "accessBasis": {
                "enum": ["PROJECT_AUTHORED", "OPEN_ACCESS", "OWNER_POSSESSION"]
            },
            "checkedAt": {"format": "date-time", "type": "string"},
            "evidenceUrl": _https_uri(nullable=True),
            "licenseId": _text(maximum=128),
            "note": _text(maximum=1000),
        },
    )


def _source_card_v3_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "contractId": {"const": "rag-source-card-v3"},
        "schemaVersion": {"const": 3},
        "sourceKind": {
            "enum": [
                "PROJECT_SOURCE_CARD",
                "OPEN_ACCESS_DOCUMENT",
                "OWNER_LOCAL_DOCUMENT",
            ]
        },
        "sourceId": {"pattern": SOURCE_ID_PATTERN, "type": "string"},
        "sourceRevisionId": {"pattern": REVISION_ID_PATTERN, "type": "string"},
        "title": _text(maximum=500),
        "authors": {
            "items": _text(maximum=300),
            "maxItems": 50,
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
        },
        "publishedOn": {"format": "date", "type": "string"},
        "curriculumTrack": {
            "enum": ["PROJECT_CORE", "OWNER_PRIVATE_UNCLASSIFIED", *OA_TRACK_IDS]
        },
        "canonicalUrl": _https_uri(),
        "downloadUrl": _https_uri(),
        "opaqueLocalDocumentId": {
            "pattern": "^doc_[a-z0-9][a-z0-9_-]{10,95}$",
            "type": "string",
        },
        "sanitizedDisplayName": {
            "maxLength": 160,
            "minLength": 1,
            "pattern": "^[^/\\\\:]+$",
            "type": "string",
        },
        "mimeType": {"enum": list(SUPPORTED_MIME_TYPES)},
        "rawContentSha256": _hash(),
        "normalizedContentSha256": _hash(),
        "licenseEvidence": _license_evidence_schema(),
        "machineFetchAllowed": {"type": "boolean"},
        "localProcessingAllowed": {"type": "boolean"},
        "redistributionAllowed": {"type": "boolean"},
        "externalLlmAllowed": {"type": "boolean"},
        "parserEvidence": _parser_evidence_schema(),
    }
    schema = _schema_document(
        "rag-source-card-v3",
        _closed(
            required=[
                "contractId",
                "schemaVersion",
                "sourceKind",
                "sourceId",
                "sourceRevisionId",
                "title",
                "authors",
                "publishedOn",
                "curriculumTrack",
                "mimeType",
                "rawContentSha256",
                "normalizedContentSha256",
                "licenseEvidence",
                "machineFetchAllowed",
                "localProcessingAllowed",
                "redistributionAllowed",
                "externalLlmAllowed",
                "parserEvidence",
            ],
            properties=properties,
        ),
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"externalLlmAllowed": {"const": True}}},
            "then": {"properties": {"localProcessingAllowed": {"const": True}}},
        },
        {
            "if": {"properties": {"sourceKind": {"const": "PROJECT_SOURCE_CARD"}}},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["downloadUrl"]},
                        {"required": ["opaqueLocalDocumentId"]},
                        {"required": ["sanitizedDisplayName"]},
                    ]
                },
                "properties": {
                    "curriculumTrack": {"const": "PROJECT_CORE"},
                    "licenseEvidence": {
                        "properties": {"accessBasis": {"const": "PROJECT_AUTHORED"}}
                    },
                    "machineFetchAllowed": {"const": False},
                },
                "required": ["canonicalUrl"],
            },
        },
        {
            "if": {"properties": {"sourceKind": {"const": "OPEN_ACCESS_DOCUMENT"}}},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["opaqueLocalDocumentId"]},
                        {"required": ["sanitizedDisplayName"]},
                    ]
                },
                "properties": {
                    "curriculumTrack": {"enum": list(OA_TRACK_IDS)},
                    "licenseEvidence": {
                        "properties": {"accessBasis": {"const": "OPEN_ACCESS"}}
                    },
                    "localProcessingAllowed": {"const": True},
                    "machineFetchAllowed": {"const": True},
                },
                "required": ["canonicalUrl", "downloadUrl"],
            },
        },
        {
            "if": {"properties": {"sourceKind": {"const": "OWNER_LOCAL_DOCUMENT"}}},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["canonicalUrl"]},
                        {"required": ["downloadUrl"]},
                    ]
                },
                "properties": {
                    "curriculumTrack": {
                        "enum": ["OWNER_PRIVATE_UNCLASSIFIED", *OA_TRACK_IDS]
                    },
                    "licenseEvidence": {
                        "properties": {"accessBasis": {"const": "OWNER_POSSESSION"}}
                    },
                    "machineFetchAllowed": {"const": False},
                    "redistributionAllowed": {"const": False},
                },
                "required": ["opaqueLocalDocumentId", "sanitizedDisplayName"],
            },
        },
    ]
    return schema


def _locator_schema() -> dict[str, Any]:
    locator = _closed(
        required=[],
        properties={
            "page": {"minimum": 1, "type": "integer"},
            "slide": {"minimum": 1, "type": "integer"},
            "sheet": _text(maximum=128),
            "section": _text(maximum=300),
        },
    )
    locator["anyOf"] = [
        {"required": ["page"]},
        {"required": ["slide"]},
        {"required": ["sheet"]},
        {"required": ["section"]},
    ]
    return locator


def _block_common(block_type: str, extra: dict[str, Any]) -> dict[str, Any]:
    properties = {
        "blockType": {"const": block_type},
        "locator": _locator_schema(),
        "readingOrder": {"minimum": 0, "type": "integer"},
        "ocrConfidence": {
            "oneOf": [
                {"maximum": 1, "minimum": 0, "type": "number"},
                {"type": "null"},
            ]
        },
        **extra,
    }
    return _closed(required=list(properties), properties=properties)


def _document_ir_schema() -> dict[str, Any]:
    text = _text(maximum=65_536)
    cell = _closed(
        required=["column", "columnSpan", "row", "rowSpan", "text"],
        properties={
            "column": {"minimum": 0, "type": "integer"},
            "columnSpan": {"maximum": 256, "minimum": 1, "type": "integer"},
            "row": {"minimum": 0, "type": "integer"},
            "rowSpan": {"maximum": 50_000, "minimum": 1, "type": "integer"},
            "text": text,
        },
    )
    blocks = [
        _block_common(
            "HEADING",
            {"level": {"maximum": 6, "minimum": 1, "type": "integer"}, "text": text},
        ),
        _block_common("PARAGRAPH", {"text": text}),
        _block_common(
            "LIST",
            {
                "items": {
                    "items": text,
                    "maxItems": 1000,
                    "minItems": 1,
                    "type": "array",
                },
                "ordered": {"type": "boolean"},
            },
        ),
        _block_common(
            "TABLE",
            {
                "cells": {
                    "items": cell,
                    "maxItems": 50_000,
                    "minItems": 1,
                    "type": "array",
                },
                "columnCount": {"maximum": 256, "minimum": 1, "type": "integer"},
                "rowCount": {"maximum": 50_000, "minimum": 1, "type": "integer"},
            },
        ),
        _block_common(
            "FORMULA",
            {"normalizedFormula": text, "sourceText": text},
        ),
        _block_common(
            "CAPTION",
            {"targetReadingOrder": {"minimum": 0, "type": "integer"}, "text": text},
        ),
    ]
    safety = _closed(
        required=["externalLlmEligible", "piiDetected", "promptInjectionDetected", "secretDetected"],
        properties={
            "externalLlmEligible": {"type": "boolean"},
            "piiDetected": {"type": "boolean"},
            "promptInjectionDetected": {"type": "boolean"},
            "secretDetected": {"type": "boolean"},
        },
    )
    return _schema_document(
        "rag-document-ir-v1",
        _closed(
            required=[
                "contractId",
                "documentIrVersion",
                "sourceId",
                "sourceRevisionId",
                "mimeType",
                "rawContentSha256",
                "normalizedContentSha256",
                "languageTags",
                "extractionMode",
                "parserEvidence",
                "blocks",
                "safetyClassification",
            ],
            properties={
                "blocks": {
                    "items": {"oneOf": blocks},
                    "maxItems": 50_000,
                    "minItems": 1,
                    "type": "array",
                },
                "contractId": {"const": "rag-document-ir-v1"},
                "documentIrVersion": {"const": 1},
                "extractionMode": {"enum": ["NATIVE", "MIXED", "OCR"]},
                "languageTags": {
                    "items": {"pattern": "^[a-z]{2,3}(?:-[A-Z]{2})?$", "type": "string"},
                    "maxItems": 10,
                    "minItems": 1,
                    "type": "array",
                    "uniqueItems": True,
                },
                "mimeType": {"enum": list(SUPPORTED_MIME_TYPES)},
                "normalizedContentSha256": _hash(),
                "parserEvidence": _parser_evidence_schema(),
                "rawContentSha256": _hash(),
                "safetyClassification": safety,
                "sourceId": {"pattern": SOURCE_ID_PATTERN, "type": "string"},
                "sourceRevisionId": {"pattern": REVISION_ID_PATTERN, "type": "string"},
            },
        ),
    )


def _oa_manifest_schema() -> dict[str, Any]:
    source = _closed(
        required=[
            "sourceId",
            "sourceRevisionId",
            "trackId",
            "qualityScore",
            "curriculumRoles",
            "canonicalUrl",
            "downloadUrl",
            "rawContentSha256",
            "machineFetchAllowed",
            "localProcessingAllowed",
            "fallbackAllowed",
        ],
        properties={
            "canonicalUrl": _https_uri(),
            "curriculumRoles": {
                "items": {
                    "enum": [
                        "PUBLIC_TEACHING_MATERIAL",
                        "ORIGINAL_RESEARCH",
                        "MODERN_REVIEW_REPLICATION_CORRECTION",
                    ]
                },
                "maxItems": 3,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "downloadUrl": _https_uri(),
            "fallbackAllowed": {"const": False},
            "localProcessingAllowed": {"const": True},
            "machineFetchAllowed": {"const": True},
            "qualityScore": {"maximum": 100, "minimum": 80, "type": "integer"},
            "rawContentSha256": _hash(),
            "sourceId": {"pattern": SOURCE_ID_PATTERN, "type": "string"},
            "sourceRevisionId": {"pattern": REVISION_ID_PATTERN, "type": "string"},
            "trackId": {"enum": list(OA_TRACK_IDS)},
        },
    )
    track = _closed(
        required=["trackId", "minimumSources", "maximumSources", "sourceCount"],
        properties={
            "maximumSources": {"const": 10},
            "minimumSources": {"const": 8},
            "sourceCount": {"maximum": 10, "minimum": 0, "type": "integer"},
            "trackId": {"enum": list(OA_TRACK_IDS)},
        },
    )
    schema = _schema_document(
        "rag-oa-manifest-v1",
        _closed(
            required=[
                "contractId",
                "manifestId",
                "releaseStatus",
                "releaseDigest",
                "signedManifest",
                "sourceCount",
                "tracks",
                "sources",
                "rawRedistributed",
                "extractedTextRedistributed",
                "embeddingsRedistributed",
            ],
            properties={
                "contractId": {"const": "rag-oa-manifest-v1"},
                "embeddingsRedistributed": {"const": False},
                "extractedTextRedistributed": {"const": False},
                "manifestId": {"pattern": "^oa140_[a-z0-9][a-z0-9_-]{2,95}$", "type": "string"},
                "rawRedistributed": {"const": False},
                "releaseDigest": _hash(nullable=True),
                "releaseStatus": {"enum": ["DRAFT", "RELEASED", "WITHDRAWN"]},
                "signedManifest": {"type": "boolean"},
                "sourceCount": {"maximum": 140, "minimum": 0, "type": "integer"},
                "sources": {
                    "items": source,
                    "maxItems": 140,
                    "type": "array",
                },
                "tracks": {
                    "items": track,
                    "maxItems": 14,
                    "minItems": 14,
                    "type": "array",
                },
            },
        ),
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"releaseStatus": {"const": "RELEASED"}}},
            "then": {
                "properties": {
                    "releaseDigest": {"pattern": HASH_PATTERN, "type": "string"},
                    "signedManifest": {"const": True},
                    "sourceCount": {"maximum": 140, "minimum": 112, "type": "integer"},
                    "sources": {"maxItems": 140, "minItems": 112, "type": "array"},
                }
            },
        }
    ]
    return schema


def _v2_ask_schema() -> dict[str, Any]:
    v1 = load_json(REPO_ROOT / "contracts/schemas/s4-rag-ask-request.schema.json")
    if not isinstance(v1, dict):
        raise ContractValidationError("v1 RAG ask schema is unavailable")
    schema = copy.deepcopy(v1)
    schema["$id"] = "contracts/schemas/s4-rag-v2-ask-request.schema.json"
    schema["title"] = "S4 RAG public ask request v2"
    return schema


def _citation_schema() -> dict[str, Any]:
    locator = _locator_schema()
    public = _closed(
        required=["citationKind", "sourceId", "title", "canonicalUrl", "locator"],
        properties={
            "canonicalUrl": _https_uri(),
            "citationKind": {"const": "PUBLIC_WEB"},
            "locator": locator,
            "sourceId": {"pattern": SOURCE_ID_PATTERN, "type": "string"},
            "title": _text(maximum=500),
        },
    )
    local = _closed(
        required=["citationKind", "documentId", "displayName", "locator"],
        properties={
            "citationKind": {"const": "LOCAL_DOCUMENT"},
            "displayName": {
                "maxLength": 160,
                "minLength": 1,
                "pattern": "^[^/\\\\:]+$",
                "type": "string",
            },
            "documentId": {"pattern": "^doc_[a-z0-9][a-z0-9_-]{10,95}$", "type": "string"},
            "locator": locator,
        },
    )
    return {"oneOf": [public, local]}


def _v2_answer_schema() -> dict[str, Any]:
    return _schema_document(
        "s4-rag-v2-answer",
        _closed(
            required=[
                "requestId",
                "answerId",
                "generationStatus",
                "answer",
                "citationCoverage",
                "citations",
                "retrievalFailure",
                "guardrailFlags",
            ],
            properties={
                "answer": {"oneOf": [_text(maximum=8192), {"type": "null"}]},
                "answerId": {
                    "oneOf": [
                        {"pattern": "^rag_[A-Za-z0-9_-]{12,96}$", "type": "string"},
                        {"type": "null"},
                    ]
                },
                "citationCoverage": {"maximum": 1, "minimum": 0, "type": "number"},
                "citations": {"items": _citation_schema(), "maxItems": 5, "type": "array"},
                "generationStatus": {
                    "enum": [
                        "ANSWERED",
                        "RETRIEVAL_ONLY",
                        "RETRIEVAL_FAILURE",
                        "BLOCKED_SENSITIVE",
                        "BLOCKED_ADVICE",
                        "GENERATION_UNAVAILABLE",
                    ]
                },
                "guardrailFlags": {
                    "items": _text(maximum=128),
                    "maxItems": 20,
                    "type": "array",
                    "uniqueItems": True,
                },
                "requestId": {"pattern": "^req_[A-Za-z0-9_-]{12,96}$", "type": "string"},
                "retrievalFailure": {"type": "boolean"},
            },
        ),
    )


def _corpus_status_schema() -> dict[str, Any]:
    return _schema_document(
        "s4-rag-v2-corpus-status",
        _closed(
            required=[
                "state",
                "publicCorpusVersion",
                "privateOverlayState",
                "progressPercent",
                "failureCode",
            ],
            properties={
                "failureCode": {
                    "oneOf": [
                        {
                            "enum": [
                                "DOWNLOAD_UNAVAILABLE",
                                "SOURCE_DRIFT",
                                "PARSER_FAILED",
                                "OCR_QUALITY_FAILED",
                                "EMBEDDING_FAILED",
                                "DISK_FULL",
                                "ACTIVATION_CONFLICT",
                            ]
                        },
                        {"type": "null"},
                    ]
                },
                "privateOverlayState": {"enum": ["ABSENT", "BUILDING", "READY", "FAILED"]},
                "progressPercent": {"maximum": 100, "minimum": 0, "type": "integer"},
                "publicCorpusVersion": _text(maximum=128),
                "state": {"enum": ["CORE_READY", "BUILDING", "FULL_READY", "FAILED"]},
            },
        ),
    )


def _history_page_schema() -> dict[str, Any]:
    item = _closed(
        required=["answerId", "createdAt", "expiresAt", "generationStatus"],
        properties={
            "answerId": {"pattern": "^rag_[A-Za-z0-9_-]{12,96}$", "type": "string"},
            "createdAt": {"format": "date-time", "type": "string"},
            "expiresAt": {"format": "date-time", "type": "string"},
            "generationStatus": {
                "enum": ["ANSWERED", "RETRIEVAL_ONLY", "RETRIEVAL_FAILURE"]
            },
        },
    )
    return _schema_document(
        "s4-rag-v2-history-page",
        _closed(
            required=["items", "nextCursor"],
            properties={
                "items": {"items": item, "maxItems": 50, "type": "array"},
                "nextCursor": {
                    "oneOf": [_text(maximum=512), {"type": "null"}]
                },
            },
        ),
    )


def _history_detail_schema() -> dict[str, Any]:
    return _schema_document(
        "s4-rag-v2-history-detail",
        _closed(
            required=[
                "answerId",
                "question",
                "answer",
                "generationStatus",
                "citations",
                "createdAt",
                "expiresAt",
            ],
            properties={
                "answer": _text(maximum=8192),
                "answerId": {"pattern": "^rag_[A-Za-z0-9_-]{12,96}$", "type": "string"},
                "citations": {"items": _citation_schema(), "maxItems": 5, "type": "array"},
                "createdAt": {"format": "date-time", "type": "string"},
                "expiresAt": {"format": "date-time", "type": "string"},
                "generationStatus": {"enum": ["ANSWERED", "RETRIEVAL_ONLY"]},
                "question": _text(maximum=8192),
            },
        ),
    )


def _error_schema() -> dict[str, Any]:
    return _schema_document(
        "s4-rag-v2-error",
        _closed(
            required=["code", "message", "requestId"],
            properties={
                "code": {
                    "enum": [
                        "CORPUS_NOT_READY",
                        "RAG_VALIDATION_FAILED",
                        "RAG_HISTORY_NOT_FOUND",
                        "RAG_UNAVAILABLE",
                    ]
                },
                "message": _text(maximum=300),
                "requestId": {"pattern": "^req_[A-Za-z0-9_-]{12,96}$", "type": "string"},
            },
        ),
    )


def _source_fixture(source_kind: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "authors": ["S4.7D Contract Team"],
        "contractId": "rag-source-card-v3",
        "externalLlmAllowed": False,
        "licenseEvidence": {
            "accessBasis": "PROJECT_AUTHORED",
            "checkedAt": "2026-08-02T00:00:00Z",
            "evidenceUrl": "https://github.com/robinhood0107/Capstone-AI-Trading-Coach",
            "licenseId": "PROJECT_AUTHORED",
            "note": "프로젝트가 직접 작성한 bounded source card다.",
        },
        "localProcessingAllowed": True,
        "machineFetchAllowed": False,
        "mimeType": "text/markdown",
        "normalizedContentSha256": "2" * 64,
        "parserEvidence": {
            "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
            "parserArtifactSha256": "3" * 64,
            "parserBackend": "capstone-markdown-native",
            "parserVersion": "1.0.0",
        },
        "publishedOn": "2026-08-01",
        "rawContentSha256": "1" * 64,
        "redistributionAllowed": True,
        "schemaVersion": 3,
        "sourceId": "src_project_exact30_001",
        "sourceKind": source_kind,
        "sourceRevisionId": "srv_project_exact30_001",
        "title": "프로젝트 exact-30 source card",
        "curriculumTrack": "PROJECT_CORE",
        "canonicalUrl": "https://github.com/robinhood0107/Capstone-AI-Trading-Coach",
    }
    if source_kind == "OPEN_ACCESS_DOCUMENT":
        common.update(
            {
                "authors": ["Open-access research authors"],
                "curriculumTrack": "DERIVATIVES_STOCHASTIC_NUMERICS",
                "downloadUrl": "https://arxiv.org/pdf/2403.00746",
                "licenseEvidence": {
                    "accessBasis": "OPEN_ACCESS",
                    "checkedAt": "2026-08-02T00:00:00Z",
                    "evidenceUrl": "https://arxiv.org/abs/2403.00746",
                    "licenseId": "ARXIV_AUTHOR_DISTRIBUTION",
                    "note": "공식 arXiv 원천에서 local build만 허용한다.",
                },
                "machineFetchAllowed": True,
                "mimeType": "application/pdf",
                "redistributionAllowed": False,
                "sourceId": "src_arxiv_option_pricing_001",
                "sourceRevisionId": "srv_arxiv_option_pricing_001",
                "title": "Open-access option-pricing benchmark paper",
                "canonicalUrl": "https://arxiv.org/abs/2403.00746",
            }
        )
    elif source_kind == "OWNER_LOCAL_DOCUMENT":
        common.pop("canonicalUrl")
        common.update(
            {
                "authors": ["Owner-provided author metadata"],
                "curriculumTrack": "OWNER_PRIVATE_UNCLASSIFIED",
                "licenseEvidence": {
                    "accessBasis": "OWNER_POSSESSION",
                    "checkedAt": "2026-08-02T00:00:00Z",
                    "evidenceUrl": None,
                    "licenseId": "OWNER_ATTESTED_LOCAL_USE",
                    "note": "원본은 owner 보유 위치에서 read-only로 처리한다.",
                },
                "machineFetchAllowed": False,
                "opaqueLocalDocumentId": "doc_01ownerprivateexample",
                "redistributionAllowed": False,
                "sanitizedDisplayName": "개인 금융공학 노트.pdf",
                "sourceId": "src_owner_document_001",
                "sourceRevisionId": "srv_owner_document_001",
                "title": "개인 금융공학 노트",
            }
        )
    return common


def _native_ir_fixture() -> dict[str, Any]:
    common = {
        "locator": {"page": 1, "section": "모형과 표"},
        "ocrConfidence": None,
    }
    return {
        "blocks": [
            {**common, "blockType": "HEADING", "level": 1, "readingOrder": 0, "text": "옵션가격 모형"},
            {**common, "blockType": "PARAGRAPH", "readingOrder": 1, "text": "가격식의 가정과 한계를 설명한다."},
            {
                **common,
                "blockType": "TABLE",
                "cells": [
                    {"column": 0, "columnSpan": 1, "row": 0, "rowSpan": 1, "text": "기호"},
                    {"column": 1, "columnSpan": 1, "row": 0, "rowSpan": 1, "text": "의미"},
                ],
                "columnCount": 2,
                "readingOrder": 2,
                "rowCount": 1,
            },
            {
                **common,
                "blockType": "FORMULA",
                "normalizedFormula": "C=S*N(d1)-K*exp(-r*T)*N(d2)",
                "readingOrder": 3,
                "sourceText": "C = S N(d1) - K e^{-rT} N(d2)",
            },
            {**common, "blockType": "CAPTION", "readingOrder": 4, "targetReadingOrder": 2, "text": "표 1. 기호 정의"},
        ],
        "contractId": "rag-document-ir-v1",
        "documentIrVersion": 1,
        "extractionMode": "NATIVE",
        "languageTags": ["ko", "en"],
        "mimeType": "application/pdf",
        "normalizedContentSha256": "5" * 64,
        "parserEvidence": {
            "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
            "parserArtifactSha256": "6" * 64,
            "parserBackend": "capstone-pdf-native",
            "parserVersion": "1.0.0",
        },
        "rawContentSha256": "4" * 64,
        "safetyClassification": {
            "externalLlmEligible": True,
            "piiDetected": False,
            "promptInjectionDetected": False,
            "secretDetected": False,
        },
        "sourceId": "src_arxiv_option_pricing_001",
        "sourceRevisionId": "srv_arxiv_option_pricing_001",
    }


def _ocr_ir_fixture() -> dict[str, Any]:
    value = _native_ir_fixture()
    value["blocks"] = [
        {
            "blockType": "PARAGRAPH",
            "locator": {"page": 2},
            "ocrConfidence": 0.997,
            "readingOrder": 0,
            "text": "금융안정 관련 핵심 숫자와 단위를 보존한다.",
        }
    ]
    value["extractionMode"] = "OCR"
    value["parserEvidence"]["ocr"] = {
        "backend": "PADDLE_STRUCTURED",
        "backendVersion": "benchmark-pinned",
        "modelSha256": "7" * 64,
    }
    return value


def _draft_oa_manifest() -> dict[str, Any]:
    return {
        "contractId": "rag-oa-manifest-v1",
        "embeddingsRedistributed": False,
        "extractedTextRedistributed": False,
        "manifestId": "oa140_contract_draft_v1",
        "rawRedistributed": False,
        "releaseDigest": None,
        "releaseStatus": "DRAFT",
        "signedManifest": False,
        "sourceCount": 0,
        "sources": [],
        "tracks": [
            {
                "maximumSources": 10,
                "minimumSources": 8,
                "sourceCount": 0,
                "trackId": track_id,
            }
            for track_id in OA_TRACK_IDS
        ],
    }


def _ask_fixture() -> dict[str, Any]:
    return {
        "answerMode": "DETAILED",
        "question": "옵션가격 모형의 가정과 한계를 근거와 함께 설명해 주세요.",
        "relatedSymbols": ["005930"],
        "topics": ["FINANCIAL_ENGINEERING"],
    }


def _public_citation() -> dict[str, Any]:
    return {
        "canonicalUrl": "https://arxiv.org/abs/2403.00746",
        "citationKind": "PUBLIC_WEB",
        "locator": {"page": 3, "section": "Method"},
        "sourceId": "src_arxiv_option_pricing_001",
        "title": "Open-access option-pricing benchmark paper",
    }


def _local_citation() -> dict[str, Any]:
    return {
        "citationKind": "LOCAL_DOCUMENT",
        "displayName": "개인 금융공학 노트.pdf",
        "documentId": "doc_01ownerprivateexample",
        "locator": {"page": 4, "section": "위험요인"},
    }


def _answer_fixture(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": "검색된 근거의 가정과 한계를 설명합니다.",
        "answerId": "rag_01EXAMPLEANSWERID",
        "citationCoverage": 1.0,
        "citations": [citation],
        "generationStatus": "RETRIEVAL_ONLY",
        "guardrailFlags": [],
        "requestId": "req_01EXAMPLEREQUESTID",
        "retrievalFailure": False,
    }


def _status_fixture() -> dict[str, Any]:
    return {
        "failureCode": None,
        "privateOverlayState": "BUILDING",
        "progressPercent": 42,
        "publicCorpusVersion": "exact30-v1+oa140-draft-v1",
        "state": "BUILDING",
    }


def _history_page_fixture() -> dict[str, Any]:
    return {
        "items": [
            {
                "answerId": "rag_01EXAMPLEANSWERID",
                "createdAt": "2026-08-02T00:00:00Z",
                "expiresAt": "2026-09-01T00:00:00Z",
                "generationStatus": "RETRIEVAL_ONLY",
            }
        ],
        "nextCursor": None,
    }


def _history_detail_fixture() -> dict[str, Any]:
    return {
        "answer": "개인 문서에서 확인된 금융공학 개념을 설명합니다.",
        "answerId": "rag_01EXAMPLEANSWERID",
        "citations": [_local_citation()],
        "createdAt": "2026-08-02T00:00:00Z",
        "expiresAt": "2026-09-01T00:00:00Z",
        "generationStatus": "RETRIEVAL_ONLY",
        "question": "이 문서의 위험요인을 설명해 주세요.",
    }


def _error_fixture() -> dict[str, Any]:
    return {
        "code": "CORPUS_NOT_READY",
        "message": "필수 OA corpus가 아직 준비되지 않았습니다.",
        "requestId": "req_01EXAMPLEREQUESTID",
    }


def _openapi_document(schemas: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    parameter = {
        "in": "path",
        "name": "answerId",
        "required": True,
        "schema": {"pattern": "^rag_[A-Za-z0-9_-]{12,96}$", "type": "string"},
    }
    error_response = {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/RagV2Error"}
            }
        },
        "description": "Stable RAG v2 failure",
    }
    document = {
        "components": {
            "schemas": {
                "RagV2Answer": schemas["s4-rag-v2-answer"],
                "RagV2AskRequest": schemas["s4-rag-v2-ask-request"],
                "RagV2CorpusStatus": schemas["s4-rag-v2-corpus-status"],
                "RagV2Error": schemas["s4-rag-v2-error"],
                "RagV2HistoryDetail": schemas["s4-rag-v2-history-detail"],
                "RagV2HistoryPage": schemas["s4-rag-v2-history-page"],
            },
            "securitySchemes": {
                "bearerAuth": {"bearerFormat": "JWT", "scheme": "bearer", "type": "http"}
            },
        },
        "info": {
            "title": "Capstone RAG v2 API",
            "version": "2.0.0-contract",
        },
        "openapi": "3.1.1",
        "paths": {
            "/api/v2/rag/ask": {
                "post": {
                    "operationId": "askRagV2",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RagV2AskRequest"}
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2Answer"}
                                }
                            },
                            "description": "Evidence-bound answer",
                        },
                        "409": error_response,
                        "503": error_response,
                    },
                    "security": [{"bearerAuth": []}],
                    "summary": "Ask the server-selected exact-30, OA, and owner overlay bundle",
                }
            },
            "/api/v2/rag/corpus-status": {
                "get": {
                    "operationId": "getRagV2CorpusStatus",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2CorpusStatus"}
                                }
                            },
                            "description": "Sanitized corpus readiness",
                        }
                    },
                    "security": [{"bearerAuth": []}],
                }
            },
            "/api/v2/rag/history": {
                "get": {
                    "operationId": "listRagV2History",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2HistoryPage"}
                                }
                            },
                            "description": "Metadata-only owner history page",
                        }
                    },
                    "security": [{"bearerAuth": []}],
                }
            },
            "/api/v2/rag/history/{answerId}": {
                "delete": {
                    "operationId": "deleteRagV2History",
                    "parameters": [parameter],
                    "responses": {"204": {"description": "Idempotently deleted"}},
                    "security": [{"bearerAuth": []}],
                },
                "get": {
                    "operationId": "getRagV2HistoryDetail",
                    "parameters": [parameter],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2HistoryDetail"}
                                }
                            },
                            "description": "Owner-scoped decrypted detail",
                        },
                        "404": error_response,
                    },
                    "security": [{"bearerAuth": []}],
                },
            },
        },
        "servers": [{"url": "/"}],
    }
    return document


def _schema_id_for_fixture(relative_path: str) -> str:
    return Path(relative_path).name.split(".", maxsplit=1)[0]


def _reject_private_path(value: object) -> None:
    if isinstance(value, str) and _PRIVATE_PATH_PATTERN.search(value):
        raise ContractValidationError("contract payload must not expose a local path")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_private_path(key)
            _reject_private_path(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_private_path(nested)


def validate_semantics(schema_id: str, payload: object) -> None:
    """JSON Schema가 표현하기 어려운 source 수량과 path 불변식을 fail-closed한다."""

    _reject_private_path(payload)
    if not isinstance(payload, dict):
        raise ContractValidationError(f"{schema_id} must be an object")
    if schema_id == "rag-source-card-v3":
        kind = payload.get("sourceKind")
        if payload.get("externalLlmAllowed") and not payload.get("localProcessingAllowed"):
            raise ContractValidationError("external LLM requires local processing permission")
        if kind == "OPEN_ACCESS_DOCUMENT":
            if not payload.get("machineFetchAllowed") or not payload.get("localProcessingAllowed"):
                raise ContractValidationError("OA source must be machine-fetchable and locally processable")
        elif kind == "OWNER_LOCAL_DOCUMENT":
            if payload.get("machineFetchAllowed"):
                raise ContractValidationError("owner-local source cannot enable machine fetch")
            if payload.get("redistributionAllowed"):
                raise ContractValidationError("owner-local source cannot enable redistribution")
        return
    if schema_id == "rag-document-ir-v1":
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            raise ContractValidationError("Document IR blocks must be an array")
        orders = [block.get("readingOrder") for block in blocks if isinstance(block, dict)]
        if orders != list(range(len(blocks))):
            raise ContractValidationError("Document IR reading order must be unique and contiguous")
        mode = payload.get("extractionMode")
        ocr_used = any(
            isinstance(block, dict) and block.get("ocrConfidence") is not None
            for block in blocks
        )
        if mode == "NATIVE" and ocr_used:
            raise ContractValidationError("native extraction cannot claim OCR confidence")
        if mode == "OCR" and not ocr_used:
            raise ContractValidationError("OCR extraction requires OCR confidence evidence")
        return
    if schema_id == "rag-oa-manifest-v1":
        tracks = payload.get("tracks")
        sources = payload.get("sources")
        if not isinstance(tracks, list) or not isinstance(sources, list):
            raise ContractValidationError("OA manifest tracks and sources must be arrays")
        track_ids = [item.get("trackId") for item in tracks if isinstance(item, dict)]
        if track_ids != list(OA_TRACK_IDS):
            raise ContractValidationError("OA manifest must preserve the exact 14-track order")
        if payload.get("sourceCount") != len(sources):
            raise ContractValidationError("OA manifest sourceCount drifted")
        source_ids = [item.get("sourceId") for item in sources if isinstance(item, dict)]
        if len(source_ids) != len(set(source_ids)):
            raise ContractValidationError("OA manifest sourceId must be unique")
        if payload.get("releaseStatus") == "RELEASED":
            if not 112 <= len(sources) <= 140:
                raise ContractValidationError("released OA manifest requires 112..140 sources")
            by_track = {track_id: [] for track_id in OA_TRACK_IDS}
            for source in sources:
                if isinstance(source, dict) and source.get("trackId") in by_track:
                    by_track[source["trackId"]].append(source)
            required_roles = {
                "PUBLIC_TEACHING_MATERIAL",
                "ORIGINAL_RESEARCH",
                "MODERN_REVIEW_REPLICATION_CORRECTION",
            }
            for track_id, entries in by_track.items():
                if not 8 <= len(entries) <= 10:
                    raise ContractValidationError(
                        f"released OA track {track_id} requires 8..10 sources"
                    )
                roles = {
                    role
                    for entry in entries
                    for role in entry.get("curriculumRoles", [])
                }
                if not required_roles.issubset(roles):
                    raise ContractValidationError(
                        f"released OA track {track_id} lacks required curriculum roles"
                    )
        return
    if schema_id == "s4-rag-v2-ask-request":
        question = payload.get("question")
        if not isinstance(question, str) or unicodedata.normalize("NFC", question) != question:
            raise ContractValidationError("RAG v2 question must be NFC text")
        if len(question.encode("utf-8")) > 8192:
            raise ContractValidationError("RAG v2 question exceeds UTF-8 byte limit")
        return
    if schema_id in {
        "s4-rag-v2-answer",
        "s4-rag-v2-history-detail",
    }:
        for citation in payload.get("citations", []):
            if not isinstance(citation, dict):
                raise ContractValidationError("citation must be an object")
            if citation.get("citationKind") == "LOCAL_DOCUMENT" and "canonicalUrl" in citation:
                raise ContractValidationError("local citation cannot expose a URL")
        return
    if schema_id == "s4-rag-v2-corpus-status":
        forbidden = {"filename", "path", "credential", "sha256", "contentHash"}
        if forbidden.intersection(payload):
            raise ContractValidationError("corpus status exposes an internal field")


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        "rag-source-card-v3": _source_card_v3_schema(),
        "rag-document-ir-v1": _document_ir_schema(),
        "rag-oa-manifest-v1": _oa_manifest_schema(),
        "s4-rag-v2-ask-request": _v2_ask_schema(),
        "s4-rag-v2-answer": _v2_answer_schema(),
        "s4-rag-v2-corpus-status": _corpus_status_schema(),
        "s4-rag-v2-history-page": _history_page_schema(),
        "s4-rag-v2-history-detail": _history_detail_schema(),
        "s4-rag-v2-error": _error_schema(),
    }


def _valid_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "contracts/examples/rag-source-card-v3.project.valid.json": _source_fixture("PROJECT_SOURCE_CARD"),
        "contracts/examples/rag-source-card-v3.oa.valid.json": _source_fixture("OPEN_ACCESS_DOCUMENT"),
        "contracts/examples/rag-source-card-v3.owner.valid.json": _source_fixture("OWNER_LOCAL_DOCUMENT"),
        "contracts/examples/rag-document-ir-v1.native.valid.json": _native_ir_fixture(),
        "contracts/examples/rag-document-ir-v1.ocr.valid.json": _ocr_ir_fixture(),
        "contracts/examples/rag-oa-manifest-v1.draft.valid.json": _draft_oa_manifest(),
        "contracts/examples/s4-rag-v2-ask-request.valid.json": _ask_fixture(),
        "contracts/examples/s4-rag-v2-answer.public-web.valid.json": _answer_fixture(_public_citation()),
        "contracts/examples/s4-rag-v2-answer.local-document.valid.json": _answer_fixture(_local_citation()),
        "contracts/examples/s4-rag-v2-corpus-status.building.valid.json": _status_fixture(),
        "contracts/examples/s4-rag-v2-history-page.valid.json": _history_page_fixture(),
        "contracts/examples/s4-rag-v2-history-detail.local-document.valid.json": _history_detail_fixture(),
        "contracts/examples/s4-rag-v2-error.corpus-not-ready.valid.json": _error_fixture(),
    }


def _invalid_fixtures(valid: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    absolute_path = copy.deepcopy(valid["contracts/examples/rag-document-ir-v1.native.valid.json"])
    absolute_path["sourcePath"] = "/home/example/private.pdf"

    duplicate_order = copy.deepcopy(valid["contracts/examples/rag-document-ir-v1.native.valid.json"])
    duplicate_order["blocks"][1]["readingOrder"] = 0

    ask = valid["contracts/examples/s4-rag-v2-ask-request.valid.json"]
    corpus_selector = {**ask, "corpus": "owner-only"}
    profile_selector = {**ask, "profile": "bge_m3_local_1024_v1"}
    top_k_selector = {**ask, "topK": 5}

    external_without_local = copy.deepcopy(
        valid["contracts/examples/rag-source-card-v3.project.valid.json"]
    )
    external_without_local["externalLlmAllowed"] = True
    external_without_local["localProcessingAllowed"] = False

    local_url = copy.deepcopy(
        valid["contracts/examples/s4-rag-v2-answer.local-document.valid.json"]
    )
    local_url["citations"][0]["canonicalUrl"] = "https://example.org/private"

    oa_fetch_disabled = copy.deepcopy(
        valid["contracts/examples/rag-source-card-v3.oa.valid.json"]
    )
    oa_fetch_disabled["machineFetchAllowed"] = False

    owner_fetch = copy.deepcopy(
        valid["contracts/examples/rag-source-card-v3.owner.valid.json"]
    )
    owner_fetch["machineFetchAllowed"] = True

    owner_redistribution = copy.deepcopy(
        valid["contracts/examples/rag-source-card-v3.owner.valid.json"]
    )
    owner_redistribution["redistributionAllowed"] = True

    released_underfilled = copy.deepcopy(
        valid["contracts/examples/rag-oa-manifest-v1.draft.valid.json"]
    )
    released_underfilled["releaseStatus"] = "RELEASED"
    released_underfilled["releaseDigest"] = "8" * 64
    released_underfilled["signedManifest"] = True

    status_hash = copy.deepcopy(
        valid["contracts/examples/s4-rag-v2-corpus-status.building.valid.json"]
    )
    status_hash["sha256"] = "9" * 64

    unknown = copy.deepcopy(
        valid["contracts/examples/rag-source-card-v3.project.valid.json"]
    )
    unknown["runtimePath"] = "opaque-but-forbidden"

    return {
        "contracts/examples/invalid/rag-document-ir-v1.absolute-path.invalid.json": absolute_path,
        "contracts/examples/invalid/rag-document-ir-v1.duplicate-reading-order.invalid.json": duplicate_order,
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-corpus-selector.invalid.json": corpus_selector,
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-profile-selector.invalid.json": profile_selector,
        "contracts/examples/invalid/s4-rag-v2-ask-request.client-top-k-selector.invalid.json": top_k_selector,
        "contracts/examples/invalid/rag-source-card-v3.external-llm-without-local-processing.invalid.json": external_without_local,
        "contracts/examples/invalid/s4-rag-v2-answer.local-citation-url.invalid.json": local_url,
        "contracts/examples/invalid/rag-source-card-v3.oa-machine-fetch-disabled.invalid.json": oa_fetch_disabled,
        "contracts/examples/invalid/rag-source-card-v3.owner-machine-fetch.invalid.json": owner_fetch,
        "contracts/examples/invalid/rag-source-card-v3.owner-redistribution.invalid.json": owner_redistribution,
        "contracts/examples/invalid/rag-oa-manifest-v1.released-underfilled.invalid.json": released_underfilled,
        "contracts/examples/invalid/s4-rag-v2-corpus-status.status-leaks-hash.invalid.json": status_hash,
        "contracts/examples/invalid/rag-source-card-v3.unknown-field.invalid.json": unknown,
    }


def _validate_generated(
    schemas: Mapping[str, dict[str, Any]],
    valid: Mapping[str, dict[str, Any]],
    invalid: Mapping[str, dict[str, Any]],
) -> None:
    validators: dict[str, Draft202012Validator] = {}
    for schema_id, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        validators[schema_id] = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
    for relative_path, payload in valid.items():
        schema_id = _schema_id_for_fixture(relative_path)
        errors = list(validators[schema_id].iter_errors(payload))
        if errors:
            raise ContractValidationError(
                f"generated valid fixture failed {relative_path}: {errors[0].message}"
            )
        validate_semantics(schema_id, payload)
    for relative_path, payload in invalid.items():
        schema_id = _schema_id_for_fixture(relative_path)
        errors = list(validators[schema_id].iter_errors(payload))
        semantic_error: ContractValidationError | None = None
        if not errors:
            try:
                validate_semantics(schema_id, payload)
            except ContractValidationError as caught:
                semantic_error = caught
        if not errors and semantic_error is None:
            raise ContractValidationError(
                f"generated invalid fixture was accepted: {relative_path}"
            )


def generate_outputs(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    """S4.7D v3/v2 계약을 같은 입력에서 결정적으로 생성한다."""

    validate_catalog(catalog)
    _validate_frozen_v1()
    schemas = _schemas()
    valid = _valid_fixtures()
    invalid = _invalid_fixtures(valid)
    _validate_generated(schemas, valid, invalid)
    catalog_digest = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
    outputs: dict[str, bytes] = {
        path.relative_to(REPO_ROOT).as_posix(): canonical_json_bytes(schemas[schema_id])
        for schema_id, path in SCHEMA_PATHS.items()
    }
    outputs.update(
        {relative_path: canonical_json_bytes(payload) for relative_path, payload in valid.items()}
    )
    outputs.update(
        {relative_path: canonical_json_bytes(payload) for relative_path, payload in invalid.items()}
    )
    outputs[CATALOG_HASH_RELATIVE_PATH] = canonical_json_bytes(
        {
            "catalogPath": CATALOG_PATH.relative_to(REPO_ROOT).as_posix(),
            "schemaVersion": 1,
            "sha256": catalog_digest,
        }
    )
    outputs[OPENAPI_PATH] = canonical_json_bytes(_openapi_document(schemas))
    if frozenset(outputs) != OUTPUTS:
        raise ContractValidationError("S4.7D generated output set drifted")
    return outputs


def _write(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in sorted(outputs.items()):
        write_generated_artifact(REPO_ROOT, relative_path, payload)
        print(f"WROTE {relative_path}")


def _check(outputs: Mapping[str, bytes]) -> int:
    failures = 0
    for relative_path, expected in sorted(outputs.items()):
        try:
            actual = (REPO_ROOT / relative_path).read_bytes()
        except OSError:
            failures += 1
            print(f"FAIL missing generated S4.7D artifact {relative_path}")
            continue
        if actual != expected:
            failures += 1
            print(f"FAIL generated S4.7D artifact drift {relative_path}")
    if failures == 0:
        print("S4_7D_RAG_V2_CONTRACT_LOCK_VERIFIED")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and verify S4.7D source v3, Document IR, OA, and RAG v2 contracts."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        catalog = load_catalog()
        outputs = generate_outputs(catalog)
        if arguments.write:
            _write(outputs)
            return 0
        return 1 if _check(outputs) else 0
    except (ContractValidationError, OSError, SchemaError) as error:
        print(f"S4.7D RAG v2 contract generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
