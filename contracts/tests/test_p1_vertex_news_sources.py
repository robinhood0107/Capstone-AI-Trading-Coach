from __future__ import annotations

import unittest

from contracts.generate_p1_vertex_news_sources import (
    OFFICIAL_PRIMARY,
    REGISTERED_INDEPENDENT,
    build_catalog,
    build_outputs,
    generate,
    load_catalog,
    validate_catalog,
)
from contracts.generate_principle_contracts import ContractValidationError


class P1VertexNewsSourcesContractTest(unittest.TestCase):
    def test_generated_catalog_is_deterministic_and_checked_in(self) -> None:
        self.assertEqual(build_outputs(), build_outputs())
        self.assertEqual(0, generate(check=True))

    def test_catalog_separates_official_primary_from_registered_independent(self) -> None:
        catalog = load_catalog()
        sources = catalog["sources"]
        official = [item for item in sources if item["sourceType"] == "OFFICIAL_PRIMARY"]
        independent = [
            item for item in sources if item["sourceType"] == "REGISTERED_INDEPENDENT"
        ]

        self.assertEqual(len(OFFICIAL_PRIMARY), len(official))
        self.assertEqual(len(REGISTERED_INDEPENDENT), len(independent))
        self.assertEqual(len(sources), len(official) + len(independent))
        self.assertIs(True, catalog["unregisteredDomainsAreNotEvidence"])
        self.assertEqual(sorted(sources, key=lambda item: item["domain"]), sources)

    def test_official_primary_is_limited_to_regulators_and_the_exchange(self) -> None:
        # 발행사 IR이나 언론이 OFFICIAL_PRIMARY로 새어 들어오면 근거 두 종류 구분이 무의미해진다.
        for domain, _ in OFFICIAL_PRIMARY:
            with self.subTest(domain=domain):
                self.assertTrue(
                    domain.endswith((".go.kr", ".or.kr", ".krx.co.kr")) or domain == "krx.co.kr"
                )

    def test_catalog_validation_rejects_drifted_entries(self) -> None:
        for mutate in (
            lambda catalog: catalog["sources"].append(dict(catalog["sources"][0])),
            lambda catalog: catalog["sources"][0].update({"sourceType": "COMMUNITY"}),
            lambda catalog: catalog["sources"][0].update({"domain": "Reuters.com"}),
            lambda catalog: catalog["sources"][0].update({"sourceId": "src_unknown_x"}),
            lambda catalog: catalog["sources"][0].pop("domain"),
        ):
            with self.subTest(mutate=mutate):
                catalog = build_catalog()
                mutate(catalog)
                with self.assertRaises(ContractValidationError):
                    validate_catalog(catalog)


if __name__ == "__main__":
    unittest.main()
