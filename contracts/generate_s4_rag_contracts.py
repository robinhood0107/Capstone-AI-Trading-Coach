from __future__ import annotations

import argparse
import copy
import hashlib
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)

REPO_ROOT = _SCRIPT_REPO_ROOT
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s4-rag-contract.v1.json"
EXPECTED_CATALOG_SHA256: Final[str] = (
    "9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a"
)
PROFILE_IDS: Final[tuple[str, str]] = (
    "bge_m3_local_1024_v1",
    "voyage_context_4_1024_v1",
)
POLICY_IDS: Final[tuple[str, str, str]] = (
    "bge_only_v1",
    "voyage_only_v1",
    "bge_then_voyage_on_sla_v1",
)
FORBIDDEN_PROFILE_IDS: Final[frozenset[str]] = frozenset(
    {"voyage_context_3_1024_v1"}
)
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/s4-rag-contract.schema.json",
        "contracts/schemas/s4-rag-ask-request.schema.json",
        "contracts/schemas/s4-rag-admin-policy-selection.schema.json",
        "contracts/examples/s4-rag-contract.valid.json",
        "contracts/examples/s4-rag-ask-request.valid.json",
        "contracts/examples/s4-rag-admin-policy-selection.valid.json",
        "contracts/examples/invalid/s4-rag-contract.voyage-context-3.invalid.json",
        "contracts/examples/invalid/s4-rag-contract.profile-policy-confusion.invalid.json",
        "contracts/examples/invalid/s4-rag-ask-request.profile-selection.invalid.json",
        "contracts/examples/invalid/s4-rag-ask-request.non-nfc.invalid.json",
        "contracts/examples/invalid/s4-rag-ask-request.symbol-count.invalid.json",
        "contracts/examples/invalid/s4-rag-ask-request.symbol-shape.invalid.json",
        "contracts/examples/invalid/s4-rag-ask-request.top-k.invalid.json",
        "contracts/examples/invalid/s4-rag-admin-policy-selection.profile-as-policy.invalid.json",
    }
)


def load_catalog(path: Path = CATALOG_PATH) -> Mapping[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_CATALOG_SHA256:
        raise ContractValidationError(
            f"S4 RAG catalog hash mismatch: expected {EXPECTED_CATALOG_SHA256}, got {digest}"
        )
    catalog = load_json_bytes_strict(raw, source=path.relative_to(REPO_ROOT).as_posix())
    if not isinstance(catalog, dict):
        raise ContractValidationError("S4 RAG catalog must be an object.")
    validate_catalog_semantics(catalog)
    return catalog


def _closed_catalog_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "additionalProperties": False,
            "properties": {
                key: _closed_catalog_shape(item) for key, item in value.items()
            },
            "required": list(value),
            "type": "object",
        }
    if isinstance(value, list):
        return {"const": value}
    return {"const": value}


def _catalog_schema(catalog: Mapping[str, Any]) -> dict[str, Any]:
    schema = _closed_catalog_shape(dict(catalog))
    schema["$id"] = "contracts/schemas/s4-rag-contract.schema.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "S4 RAG static contract catalog v1"
    return schema


def _ask_request_schema(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "$id": "contracts/schemas/s4-rag-ask-request.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "answerMode": {"enum": catalog["answerModes"]},
            "question": {
                "maxLength": catalog["askRequest"][
                    "maximumQuestionUnicodeScalars"
                ],
                "minLength": catalog["askRequest"][
                    "minimumQuestionUnicodeScalars"
                ],
                "type": "string",
            },
            "relatedSymbols": {
                "items": {
                    "pattern": catalog["askRequest"]["relatedSymbolsPattern"],
                    "type": "string",
                },
                "maxItems": catalog["askRequest"]["relatedSymbolsMaximumItems"],
                "type": "array",
                "uniqueItems": True,
            },
            "topics": {
                "items": {
                    "enum": catalog["topicAllowlist"],
                },
                "maxItems": catalog["askRequest"]["topicsMaximumItems"],
                "type": "array",
                "uniqueItems": True,
            },
        },
        "required": ["question", "answerMode"],
        "title": "S4 RAG public ask request v1",
        "type": "object",
    }


def _admin_policy_selection_schema(catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "$id": "contracts/schemas/s4-rag-admin-policy-selection.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "approvedAt": {"format": "date-time", "type": "string"},
            "policyId": {"enum": catalog["policyIds"]},
            "reason": {
                "maxLength": 300,
                "minLength": 12,
                "type": "string",
            },
        },
        "required": ["policyId", "reason", "approvedAt"],
        "title": "S4 RAG admin policy pointer selection v1",
        "type": "object",
    }


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def validate_catalog_semantics(catalog: Mapping[str, Any]) -> None:
    expected_root_keys = {
        "answerModes",
        "askRequest",
        "canonicalChunking",
        "contractId",
        "dimension",
        "embeddingInputStrategies",
        "embeddingOperations",
        "forbiddenProfileIds",
        "generationStatuses",
        "policies",
        "policyIds",
        "profiles",
        "profileIds",
        "schemaVersion",
        "sourceMetadata",
        "topicAllowlist",
    }
    if set(catalog) != expected_root_keys:
        raise ContractValidationError("S4 RAG catalog root key set drifted.")
    if catalog["contractId"] != "s4-rag-contract/v1":
        raise ContractValidationError("S4 RAG contractId drifted.")
    if catalog["schemaVersion"] != 1 or catalog["dimension"] != 1024:
        raise ContractValidationError("S4 RAG schemaVersion/dimension must remain v1/1024.")
    if tuple(catalog["profileIds"]) != PROFILE_IDS:
        raise ContractValidationError("S4 RAG profileIds must be the exact two approved profiles.")
    if tuple(catalog["policyIds"]) != POLICY_IDS:
        raise ContractValidationError("S4 RAG policyIds must be the exact three approved policies.")
    if set(catalog["forbiddenProfileIds"]) != FORBIDDEN_PROFILE_IDS:
        raise ContractValidationError("S4 RAG forbidden profile set drifted.")
    forbidden_seen = FORBIDDEN_PROFILE_IDS.intersection(_walk_strings(catalog))
    if forbidden_seen - set(catalog["forbiddenProfileIds"]):
        raise ContractValidationError(
            f"S4 RAG forbidden profile leaked into active catalog: {sorted(forbidden_seen)}"
        )

    profiles = catalog["profiles"]
    if not isinstance(profiles, list) or len(profiles) != 2:
        raise ContractValidationError("S4 RAG profiles must contain exactly two entries.")
    profiles_by_id = {profile.get("profileId"): profile for profile in profiles}
    if tuple(profiles_by_id) != PROFILE_IDS:
        raise ContractValidationError("S4 RAG profiles must preserve approved ID order.")
    for profile_id, profile in profiles_by_id.items():
        if profile["dimension"] != 1024:
            raise ContractValidationError(f"{profile_id} must be 1024-dimensional.")
        if profile["vectorSpace"] != profile_id:
            raise ContractValidationError(f"{profile_id} vectorSpace must equal profileId.")
        if profile["operationAllowlist"] != catalog["embeddingOperations"]:
            raise ContractValidationError(f"{profile_id} operation allowlist drifted.")
        if profile["trustRemoteCode"] is not False:
            raise ContractValidationError(f"{profile_id} must keep trustRemoteCode=false.")
        if profile["canonicalChunkOverlapPercent"] != 0:
            raise ContractValidationError(
                f"{profile_id} canonical chunks must keep overlap at zero."
            )

    bge = profiles_by_id["bge_m3_local_1024_v1"]
    if (
        bge["provider"] != "LOCAL"
        or bge["artifactFormat"] != "ONNX_DATA_ONLY"
        or bge["externalProvider"] is not False
        or bge["freeTokenEligible"] is not False
        or bge["providerOrigin"] is not None
        or bge["providerEndpoint"] is not None
        or bge["embeddingInputStrategy"]
        != "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15"
        or bge["transientAdjacentContextMaxPercent"] != 15
    ):
        raise ContractValidationError(
            "BGE profile must remain local ONNX with transient context capped at 15%."
        )

    voyage = profiles_by_id["voyage_context_4_1024_v1"]
    if (
        voyage["provider"] != "VOYAGE"
        or voyage["model"] != "voyage-context-4"
        or voyage["externalProvider"] is not True
        or voyage["freeTokenEligible"] is not True
        or voyage["providerOrigin"] != "https://api.voyageai.com"
        or voyage["providerEndpoint"] != "POST /v1/contextualizedembeddings"
        or voyage["embeddingInputStrategy"]
        != "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0"
        or voyage["transientAdjacentContextMaxPercent"] != 0
    ):
        raise ContractValidationError("Voyage profile must remain context-4 only.")

    if catalog["answerModes"] != ["CONCISE", "DETAILED"]:
        raise ContractValidationError("S4 RAG answer modes must remain CONCISE/DETAILED.")
    if catalog["canonicalChunking"] != {
        "boundaryStrategy": "MARKDOWN_HEADING_PARAGRAPH",
        "maximumTargetTokens": 600,
        "minimumTargetTokens": 400,
        "overlapPercent": 0,
        "oversizedTablePolicy": "REJECT",
        "tableSplitAllowed": False,
    }:
        raise ContractValidationError("S4 RAG canonical chunking contract drifted.")
    if catalog["embeddingInputStrategies"] != [
        "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15",
        "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0",
    ]:
        raise ContractValidationError("S4 RAG embedding input strategy set drifted.")
    if catalog["sourceMetadata"] != {
        "maximumItems": 30,
        "publicSourceType": "PROJECT_SOURCE_CARD",
        "queryParametersAllowed": False,
        "rawChunkIncluded": False,
        "rawUpstreamBodyIncluded": False,
    }:
        raise ContractValidationError("S4 RAG public source metadata boundary drifted.")

    policies = catalog["policies"]
    if not isinstance(policies, list) or len(policies) != 3:
        raise ContractValidationError("S4 RAG policies must contain exactly three entries.")
    policies_by_id = {policy.get("policyId"): policy for policy in policies}
    if tuple(policies_by_id) != POLICY_IDS:
        raise ContractValidationError("S4 RAG policy order/id set drifted.")
    if set(policies_by_id).intersection(PROFILE_IDS):
        raise ContractValidationError("Profile IDs cannot appear in the policy namespace.")

    for policy_id, policy in policies_by_id.items():
        if policy["perRequestFallback"] is not False:
            raise ContractValidationError(f"{policy_id} must not be a per-request fallback.")
        if policy["providerOutageFallback"] is not False:
            raise ContractValidationError(f"{policy_id} must not fallback on provider outage.")
        if policy["documentProfileId"] != policy["queryProfileId"]:
            raise ContractValidationError(f"{policy_id} cannot mix document/query vector spaces.")
        if policy["defaultProfileId"] != policy["queryProfileId"]:
            raise ContractValidationError(f"{policy_id} default/query profile drifted.")
        if policy["defaultProfileId"] not in PROFILE_IDS:
            raise ContractValidationError(f"{policy_id} points to an unknown profile.")

    if policies_by_id["bge_only_v1"]["outboundProviderCalls"] is not False:
        raise ContractValidationError("bge_only_v1 must keep provider outbound calls at zero.")
    if policies_by_id["voyage_only_v1"]["outboundProviderCalls"] is not True:
        raise ContractValidationError("voyage_only_v1 must explicitly allow provider outbound calls.")
    transition = policies_by_id["bge_then_voyage_on_sla_v1"]["transition"]
    if transition != {
        "adminApprovalRequired": True,
        "allowed": True,
        "targetProfileId": "voyage_context_4_1024_v1",
        "trigger": "BGE_WARM_P95_SLA_FAILED_AND_VOYAGE_EVAL_PASSED",
    }:
        raise ContractValidationError("bge_then_voyage_on_sla_v1 transition contract drifted.")

    ask = catalog["askRequest"]
    if ask["route"] != "POST /api/v1/rag/ask":
        raise ContractValidationError("S4 RAG ask route drifted.")
    if ask["idempotencyHeader"] != "X-Idempotency-Key":
        raise ContractValidationError("S4 RAG idempotency header drifted.")
    if {
        "maximumQuestionUnicodeScalars": ask["maximumQuestionUnicodeScalars"],
        "maximumQuestionUtf8Bytes": ask["maximumQuestionUtf8Bytes"],
        "minimumQuestionUnicodeScalars": ask["minimumQuestionUnicodeScalars"],
        "normalization": ask["normalization"],
        "relatedSymbolsMaximumItems": ask["relatedSymbolsMaximumItems"],
        "relatedSymbolsPattern": ask["relatedSymbolsPattern"],
        "topicsMaximumItems": ask["topicsMaximumItems"],
    } != {
        "maximumQuestionUnicodeScalars": 1000,
        "maximumQuestionUtf8Bytes": 8192,
        "minimumQuestionUnicodeScalars": 1,
        "normalization": "NFC",
        "relatedSymbolsMaximumItems": 5,
        "relatedSymbolsPattern": "^[0-9]{6}$",
        "topicsMaximumItems": 5,
    }:
        raise ContractValidationError("S4 RAG public ask input bounds drifted.")
    forbidden_fields = set(ask["forbiddenBodyFields"])
    if not {
        "embeddingProfileId",
        "embeddingPolicyId",
        "profileId",
        "policyId",
        "topK",
        "sourceTier",
        "provider",
        "model",
    }.issubset(forbidden_fields):
        raise ContractValidationError("S4 RAG public ask body must forbid profile/policy/provider controls.")


def validate_rag_ask_request_semantics(request: object, catalog: Mapping[str, Any]) -> None:
    if not isinstance(request, dict):
        raise ContractValidationError("S4 RAG ask request must be an object.")
    forbidden_fields = set(catalog["askRequest"]["forbiddenBodyFields"])
    leaked = sorted(forbidden_fields.intersection(request))
    if leaked:
        raise ContractValidationError(
            f"S4 RAG public ask request cannot carry server-owned controls: {leaked}"
        )
    question = request.get("question")
    if not isinstance(question, str):
        raise ContractValidationError("S4 RAG question must be a string.")
    if unicodedata.normalize("NFC", question) != question:
        raise ContractValidationError("S4 RAG question must already be NFC-normalized.")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in question):
        raise ContractValidationError("S4 RAG question must contain Unicode scalar values only.")
    ask = catalog["askRequest"]
    if not (
        ask["minimumQuestionUnicodeScalars"]
        <= len(question)
        <= ask["maximumQuestionUnicodeScalars"]
    ):
        raise ContractValidationError("S4 RAG question Unicode scalar count is out of range.")
    try:
        encoded_question = question.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ContractValidationError("S4 RAG question must be valid UTF-8.") from error
    if len(encoded_question) > ask["maximumQuestionUtf8Bytes"]:
        raise ContractValidationError("S4 RAG question exceeds the UTF-8 byte limit.")
    symbols = request.get("relatedSymbols", [])
    if not isinstance(symbols, list) or any(
        not isinstance(symbol, str)
        or len(symbol) != 6
        or not symbol.isascii()
        or not symbol.isdigit()
        for symbol in symbols
    ):
        raise ContractValidationError(
            "S4 RAG relatedSymbols must contain exact six-digit symbols."
        )


def validate_admin_policy_selection_semantics(
    request: object, catalog: Mapping[str, Any]
) -> None:
    if not isinstance(request, dict):
        raise ContractValidationError("S4 RAG admin policy selection must be an object.")
    policy_id = request.get("policyId")
    if policy_id in catalog["profileIds"]:
        raise ContractValidationError("Profile ID cannot be submitted as a policy ID.")


def _fixtures(catalog: Mapping[str, Any]) -> dict[str, Any]:
    valid_ask = {
        "answerMode": "CONCISE",
        "question": "KIS 일봉 조정주가 사용 시 어떤 한계를 인용해야 하나요?",
        "relatedSymbols": ["005930"],
        "topics": ["API", "PRODUCT_RISK"],
    }
    valid_policy = {
        "approvedAt": "2026-07-29T00:00:00Z",
        "policyId": "bge_then_voyage_on_sla_v1",
        "reason": "BGE warm p95 failed and Voyage evaluation passed with admin approval.",
    }
    fixtures: dict[str, Any] = {
        "contracts/examples/s4-rag-contract.valid.json": dict(catalog),
        "contracts/examples/s4-rag-ask-request.valid.json": valid_ask,
        "contracts/examples/s4-rag-admin-policy-selection.valid.json": valid_policy,
    }
    voyage3 = copy.deepcopy(dict(catalog))
    voyage3["profileIds"].append("voyage_context_3_1024_v1")
    voyage3["profiles"].append(
        {
            "artifactFormat": "PROVIDER_API_RESPONSE_DATA_ONLY",
            "canonicalChunkOverlapPercent": 0,
            "dimension": 1024,
            "embeddingInputStrategy": "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0",
            "externalProvider": True,
            "freeTokenEligible": False,
            "model": "voyage-context-3",
            "operationAllowlist": ["DOCUMENT_EMBED", "QUERY_EMBED"],
            "profileId": "voyage_context_3_1024_v1",
            "provider": "VOYAGE",
            "providerEndpoint": "POST /v1/contextualizedembeddings",
            "providerOrigin": "https://api.voyageai.com",
            "transientAdjacentContextMaxPercent": 0,
            "trustRemoteCode": False,
            "vectorSpace": "voyage_context_3_1024_v1",
        }
    )
    fixtures[
        "contracts/examples/invalid/s4-rag-contract.voyage-context-3.invalid.json"
    ] = voyage3
    confused_catalog = copy.deepcopy(dict(catalog))
    confused_catalog["policies"][0]["policyId"] = "bge_m3_local_1024_v1"
    fixtures[
        "contracts/examples/invalid/s4-rag-contract.profile-policy-confusion.invalid.json"
    ] = confused_catalog
    ask_with_profile = copy.deepcopy(valid_ask)
    ask_with_profile["embeddingProfileId"] = "bge_m3_local_1024_v1"
    fixtures[
        "contracts/examples/invalid/s4-rag-ask-request.profile-selection.invalid.json"
    ] = ask_with_profile
    ask_with_non_nfc = copy.deepcopy(valid_ask)
    ask_with_non_nfc["question"] = "Cafe\u0301 리스크를 설명해 주세요."
    fixtures[
        "contracts/examples/invalid/s4-rag-ask-request.non-nfc.invalid.json"
    ] = ask_with_non_nfc
    ask_with_too_many_symbols = copy.deepcopy(valid_ask)
    ask_with_too_many_symbols["relatedSymbols"] = [
        "005930",
        "000660",
        "035420",
        "051910",
        "068270",
        "207940",
    ]
    fixtures[
        "contracts/examples/invalid/s4-rag-ask-request.symbol-count.invalid.json"
    ] = ask_with_too_many_symbols
    ask_with_bad_symbol = copy.deepcopy(valid_ask)
    ask_with_bad_symbol["relatedSymbols"] = ["NVDA"]
    fixtures[
        "contracts/examples/invalid/s4-rag-ask-request.symbol-shape.invalid.json"
    ] = ask_with_bad_symbol
    ask_with_top_k = copy.deepcopy(valid_ask)
    ask_with_top_k["topK"] = 5
    fixtures["contracts/examples/invalid/s4-rag-ask-request.top-k.invalid.json"] = (
        ask_with_top_k
    )
    profile_as_policy = copy.deepcopy(valid_policy)
    profile_as_policy["policyId"] = "voyage_context_4_1024_v1"
    fixtures[
        "contracts/examples/invalid/s4-rag-admin-policy-selection.profile-as-policy.invalid.json"
    ] = profile_as_policy
    return fixtures


def generate_outputs(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    validate_catalog_semantics(catalog)
    catalog_schema = _catalog_schema(catalog)
    ask_schema = _ask_request_schema(catalog)
    policy_schema = _admin_policy_selection_schema(catalog)
    Draft202012Validator.check_schema(catalog_schema)
    Draft202012Validator.check_schema(ask_schema)
    Draft202012Validator.check_schema(policy_schema)
    outputs: dict[str, bytes] = {
        "contracts/schemas/s4-rag-contract.schema.json": canonical_json_bytes(catalog_schema),
        "contracts/schemas/s4-rag-ask-request.schema.json": canonical_json_bytes(ask_schema),
        "contracts/schemas/s4-rag-admin-policy-selection.schema.json": canonical_json_bytes(policy_schema),
    }
    outputs.update(
        {path: canonical_json_bytes(value) for path, value in _fixtures(catalog).items()}
    )
    if frozenset(outputs) != OUTPUTS:
        raise ContractValidationError("S4 RAG generated output manifest drifted.")

    validators = {
        "s4-rag-contract": Draft202012Validator(catalog_schema),
        "s4-rag-ask-request": Draft202012Validator(ask_schema),
        "s4-rag-admin-policy-selection": Draft202012Validator(policy_schema),
    }
    for path, payload in _fixtures(catalog).items():
        schema_name = (
            path.removeprefix("contracts/examples/")
            .removeprefix("invalid/")
            .removesuffix(".valid.json")
            .removesuffix(".invalid.json")
            .split(".", maxsplit=1)[0]
        )
        errors = list(validators[schema_name].iter_errors(payload))
        semantic_error: ContractValidationError | None = None
        if not errors:
            try:
                if schema_name == "s4-rag-contract":
                    validate_catalog_semantics(payload)
                elif schema_name == "s4-rag-ask-request":
                    validate_rag_ask_request_semantics(payload, catalog)
                elif schema_name == "s4-rag-admin-policy-selection":
                    validate_admin_policy_selection_semantics(payload, catalog)
            except ContractValidationError as caught:
                semantic_error = caught
        if path.endswith(".valid.json") and (errors or semantic_error):
            detail = errors[0].message if errors else str(semantic_error)
            raise ContractValidationError(f"{path}: generated positive fixture invalid: {detail}")
        if path.endswith(".invalid.json") and not errors and semantic_error is None:
            raise ContractValidationError(f"{path}: generated negative fixture passed.")
    return dict(sorted(outputs.items()))


def _check_outputs(outputs: Mapping[str, bytes]) -> int:
    failures = 0
    for relative_path, expected in outputs.items():
        path = REPO_ROOT / relative_path
        try:
            actual = path.read_bytes()
        except OSError:
            failures += 1
            print(f"FAIL missing generated artifact {relative_path}", file=sys.stderr)
            continue
        if actual != expected:
            failures += 1
            print(f"FAIL generated artifact drift {relative_path}", file=sys.stderr)
        else:
            print(f"PASS generated artifact {relative_path}")
    return failures


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in outputs.items():
        path = REPO_ROOT / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"WROTE {relative_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and verify the canonical S4 RAG profile/policy contracts."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        catalog = load_catalog()
        outputs = generate_outputs(catalog)
        if arguments.write:
            _write_outputs(outputs)
            print(f"S4_RAG_CONTRACT_LOCK_VERIFIED {EXPECTED_CATALOG_SHA256}")
            return 0
        failures = _check_outputs(outputs)
    except (OSError, ContractValidationError, SchemaError, KeyError, TypeError) as error:
        print(f"S4 RAG contract generation failed: {error}", file=sys.stderr)
        return 1
    if failures:
        print(f"S4 RAG contract generation failed: {failures} drift(s)", file=sys.stderr)
        return 1
    print(f"S4_RAG_CONTRACT_LOCK_VERIFIED {EXPECTED_CATALOG_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
