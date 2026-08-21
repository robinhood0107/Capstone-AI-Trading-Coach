from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from contracts.generate_s6_contracts import (
    CATALOG_DIR,
    EXAMPLE_DIR,
    INVALID_DIR,
    ROOT,
    SCHEMA_DIR,
    SCHEMA_IDS,
    ContractValidationError,
    build_outputs,
    validate_semantics,
)


FROZEN_AUTHORITY = {
    "contracts/catalogs/s2-2-system-rule-catalog.v1.json": "a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b",
    "contracts/catalogs/s2-2-system-rule-catalog.v2.json": "bd812439694cc55aa8eca61f7e8aebe371ef0a55040f2e2134f103449b18da70",
    "contracts/schemas/cross_market_policy_evaluation.v1.schema.json": "584bd33dbf6d16b90b8efe45f3a0131db3eb9b3ef5718670751fa9746d2afb25",
    "contracts/schemas/cross_market_risk_snapshot.v1.schema.json": "33606f3a2f882d5ad13498a964d663ffd254ac08d6b2cf76ab49bc18eb50bd16",
}


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise AssertionError(path)
    return value


class S6ContractTest(unittest.TestCase):
    def test_generator_is_deterministic_and_complete(self) -> None:
        outputs = build_outputs()
        self.assertEqual(32, len(outputs))
        for path, expected in outputs.items():
            self.assertTrue(path.exists(), path)
            self.assertEqual(expected, path.read_bytes(), path)

    def test_all_positive_fixtures_validate(self) -> None:
        for schema_id in SCHEMA_IDS:
            schema = _load(SCHEMA_DIR / f"{schema_id}.schema.json")
            payload = _load(EXAMPLE_DIR / f"{schema_id}.valid.json")
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self.assertEqual([], list(validator.iter_errors(payload)), schema_id)
            validate_semantics(schema_id, payload)

    def test_all_negative_fixtures_fail_closed(self) -> None:
        for schema_id in SCHEMA_IDS:
            schema = _load(SCHEMA_DIR / f"{schema_id}.schema.json")
            payload = _load(INVALID_DIR / f"{schema_id}.contract.invalid.json")
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
            semantic_error: ContractValidationError | None = None
            if not errors:
                try:
                    validate_semantics(schema_id, payload)
                except ContractValidationError as caught:
                    semantic_error = caught
            self.assertTrue(errors or semantic_error, schema_id)

    def test_catalog_v3_preserves_prior_rules_and_removes_threshold_fallback(self) -> None:
        v2 = _load(CATALOG_DIR / "s2-2-system-rule-catalog.v2.json")
        v3 = _load(CATALOG_DIR / "s2-2-system-rule-catalog.v3.json")
        self.assertEqual(v2["rules"][:14], v3["rules"][:14])
        rule = v3["rules"][14]
        self.assertIsNone(rule["defaultThreshold"])
        self.assertEqual([95, 97.5, 99], v3["crossMarketOverlay"]["allowedFrozenPercentiles"])
        self.assertEqual("ALLOW_TO_WARN_NEW_BUY_ONLY", v3["crossMarketOverlay"]["maximumAuthority"])
        self.assertFalse(v3["crossMarketOverlay"]["providerFanoutAllowed"])

    def test_hash_authority_mutation_and_explanation_invariance(self) -> None:
        fixture = _load(EXAMPLE_DIR / "cross_market_risk_snapshot.v2.valid.json")
        included = ["score", "thresholdPercentile", "thresholdArtifactHash", "configHash", "exposureCatalogHash"]

        def semantic_hash(payload: dict[str, object]) -> str:
            canonical = json.dumps({name: payload[name] for name in included}, sort_keys=True, separators=(",", ":")).encode()
            return hashlib.sha256(canonical).hexdigest()

        baseline = semantic_hash(fixture)
        for name in included:
            mutated = copy.deepcopy(fixture)
            mutated[name] = 96 if name in {"score", "thresholdPercentile"} else "f" * 64
            self.assertNotEqual(baseline, semantic_hash(mutated), name)
        for name in ("analystEvidence", "ragOutput", "llmOutput", "explanation"):
            mutated = copy.deepcopy(fixture)
            mutated[name] = {"untrusted": "changed"}
            self.assertEqual(baseline, semantic_hash(mutated), name)

    def test_prior_authority_bytes_remain_frozen(self) -> None:
        for relative, expected in FROZEN_AUTHORITY.items():
            self.assertEqual(expected, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), relative)


if __name__ == "__main__":
    unittest.main()
