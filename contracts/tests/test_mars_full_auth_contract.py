from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/openapi/mars-full-auth.v1.openapi.json"


class MarsFullAuthContractTest(unittest.TestCase):
    def test_full_auth_contract_is_valid_and_has_no_password_route(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        self.assertEqual(
            set(document["paths"]),
            {"/api/v1/auth/oidc/exchange", "/api/v1/auth/logout"},
        )
        self.assertEqual(
            document["paths"]["/api/v1/auth/oidc/exchange"]["post"]["parameters"][0]
            ["schema"]["const"],
            "https://mars.royaljellynas.org",
        )
        self.assertEqual(
            set(document["paths"]["/api/v1/auth/logout"]["post"]["responses"]),
            {"204", "401"},
        )
