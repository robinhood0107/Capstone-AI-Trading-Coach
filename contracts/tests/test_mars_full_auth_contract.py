from __future__ import annotations

import json
import unittest
from pathlib import Path

from openapi_spec_validator import validate


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/openapi/mars-full-auth.v1.openapi.json"


class MarsFullAuthContractTest(unittest.TestCase):
    def test_full_auth_contract_covers_password_accounts_and_manual_provider_links(self) -> None:
        document = json.loads(CONTRACT.read_text(encoding="utf-8"))
        validate(document)
        self.assertEqual(
            set(document["paths"]),
            {
                "/api/v1/auth/login",
                "/api/v1/auth/options",
                "/api/v1/auth/signup",
                "/api/v1/auth/password",
                "/api/v1/auth/identities",
                "/api/v1/auth/identities/{provider}/link/start",
                "/api/v1/auth/identities/{provider}",
                "/api/v1/auth/oidc/start/google",
                "/api/v1/auth/oidc/callback/google",
                "/api/v1/auth/oidc/start/kakao",
                "/api/v1/auth/oidc/callback/kakao",
                "/api/v1/auth/oidc/exchange",
                "/api/v1/auth/logout",
            },
        )
        self.assertEqual(
            document["paths"]["/api/v1/auth/identities/{provider}/link/start"]["post"]["security"],
            [{"bearerAuth": []}],
        )
        self.assertEqual(
            document["paths"]["/api/v1/auth/identities/{provider}/link/start"]["post"]["parameters"][0]["schema"]["enum"],
            ["google", "kakao"],
        )
        self.assertEqual(
            document["paths"]["/api/v1/auth/oidc/exchange"]["post"]["operationId"],
            "exchangeSocialLoginSession",
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
