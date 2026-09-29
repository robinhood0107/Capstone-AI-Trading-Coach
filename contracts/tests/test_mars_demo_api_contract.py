from __future__ import annotations

import json
from pathlib import Path
import unittest

from openapi_spec_validator import validate


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/openapi/mars-demo-v2.openapi.json"


class MarsDemoApiContractTest(unittest.TestCase):
    def test_demo_v2_uses_its_own_cookie_routes_and_no_full_or_legacy_demo_api(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        self.assertEqual(document["info"]["version"], "2.0.0")
        paths = set(document["paths"])
        self.assertEqual(paths, {
            "/healthz",
            "/api/demo/session",
            "/api/demo/logout",
            "/api/demo/state",
            "/api/demo/overlay",
            "/api/demo/agent",
        })
        self.assertFalse(any(path.startswith("/api/v1/") or "brokerage" in path for path in paths))
        session = document["paths"]["/api/demo/session"]["post"]
        self.assertEqual(session["security"], [])
        self.assertEqual(session["responses"]["429"]["description"], "Session issuance rate limit reached")
        cookie = document["components"]["securitySchemes"]["demoVisitorCookie"]
        self.assertEqual((cookie["type"], cookie["in"], cookie["name"]), ("apiKey", "cookie", "mars_demo_visitor"))
        question = document["components"]["schemas"]["AgentQuestion"]["properties"]["question"]
        self.assertEqual(question["maxLength"], 1500)
        self.assertNotIn("password", str(document).lower())
        self.assertNotIn("kis", str(document).lower())

    def test_legacy_full_spring_demo_agent_entrypoint_is_absent(self) -> None:
        spring = ROOT / "workspaces/decision-platform/spring-api/src/main/kotlin"
        source = "\n".join(path.read_text(encoding="utf-8") for path in spring.rglob("*.kt"))
        self.assertNotIn("class DemoAgentController", source)
        self.assertNotIn("DEMO_INTERNAL_OWNER_USER_ID", source)
        self.assertNotIn("PublicSurfaceMode.DEMO", source)
        self.assertNotIn("/api/v1/demo/agent/ask", source)


if __name__ == "__main__":
    unittest.main()
