import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class S49LangGraphGroundingContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(
            (ROOT / "contracts/catalogs/s4-9-mcp-strong-llm-contract.v2.json").read_text(
                encoding="utf-8"
            )
        )

    def test_google_grounding_budget_and_fallback_are_explicit(self) -> None:
        self.assertEqual(self.catalog["contractId"], "s4-9-mcp-strong-llm-v2")
        self.assertEqual(self.catalog["strongLlm"]["runtime"], "PYTHON_LANGGRAPH_BOUNDED_STATE_GRAPH")
        self.assertTrue(self.catalog["strongLlm"]["providerPermitRequired"])
        self.assertEqual(self.catalog["strongLlm"]["hiddenRetryCount"], 0)
        google = self.catalog["search"]["googleGrounding"]
        self.assertEqual(google["monthlySoftCap"], 4000)
        self.assertEqual(google["billingPeriodZone"], "America/Los_Angeles")
        self.assertFalse(google["overageAllowed"])
        self.assertFalse(google["automatedRedirectRead"])

    def test_provenance_and_downstream_authority_remain_closed(self) -> None:
        self.assertEqual(
            set(self.catalog["provenance"]["sourceTypes"]),
            {"GOOGLE_GROUNDING", "SEARXNG_RESULT", "USER_ROOT", "DISCOVERED_LINK"},
        )
        self.assertTrue(self.catalog["provenance"]["readByResultId"])
        self.assertFalse(self.catalog["provenance"]["unregisteredModelUrlAllowed"])
        self.assertTrue(all(value is False for value in self.catalog["storage"].values()))
        self.assertTrue(all(value is False for value in self.catalog["authority"].values()))

    def test_searxng_fallback_keeps_only_duckduckgo(self) -> None:
        settings = (ROOT / "infra/searxng/settings.yml").read_text(encoding="utf-8")

        self.assertIn("keep_only:\n      - duckduckgo", settings)
        for forbidden in ("brave", "mojeek", "qwant", "wikipedia", "flaresolverr"):
            self.assertNotIn(f"      - {forbidden}", settings.lower())


if __name__ == "__main__":
    unittest.main()
