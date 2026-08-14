import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> dict:
    return json.loads(
        (ROOT / "contracts/catalogs/s4-9-mcp-strong-llm-contract.v1.json").read_text(
            encoding="utf-8"
        )
    )


class S49McpStrongLlmContractTest(unittest.TestCase):
    def test_s4_9_locks_full_top_five_and_five_provider_neutral_tools(self) -> None:
        catalog = _catalog()

        self.assertEqual(catalog["contractId"], "s4-9-mcp-strong-llm-v1")
        self.assertEqual(catalog["mcpProtocolVersion"], "2025-11-25")
        self.assertEqual(catalog["strongLlm"]["evidenceInput"], "FULL_TOP_5")
        self.assertEqual(catalog["strongLlm"]["maximumToolRounds"], 3)
        self.assertEqual(
            catalog["mcp"]["tools"],
            [
                "capstone_rag_search",
                "capstone_web_search",
                "capstone_web_read",
                "capstone_answer_validate",
                "capstone_answer_save",
            ],
        )
        self.assertFalse(catalog["mcp"]["ownerIdToolArgumentAllowed"])
        self.assertFalse(catalog["mcp"]["publicDynamicClientRegistration"])

    def test_s4_9_keeps_web_and_trading_authority_closed(self) -> None:
        catalog = _catalog()

        self.assertTrue(
            all(
                catalog["web"][field] is False
                for field in (
                    "googleSearch",
                    "naver",
                    "browserAutomation",
                    "crawler",
                    "deepResearch",
                )
            )
        )
        self.assertEqual(
            set(catalog["web"]["engines"]),
            {"duckduckgo", "brave", "mojeek", "qwant", "wikipedia"},
        )
        self.assertTrue(all(value is False for value in catalog["authority"].values()))

    def test_s4_9_tools_list_fixture_is_provider_neutral_and_has_no_owner_argument(self) -> None:
        fixture = json.loads(
            (ROOT / "contracts/catalogs/s4-9-mcp-tools-list.v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(fixture["jsonrpc"], "2.0")
        tools = fixture["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], _catalog()["mcp"]["tools"])
        for tool in tools:
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("ownerId", schema["properties"])
            self.assertNotIn("ownerUserId", schema["properties"])


if __name__ == "__main__":
    unittest.main()
