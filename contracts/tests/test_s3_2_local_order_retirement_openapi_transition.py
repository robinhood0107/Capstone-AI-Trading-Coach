from __future__ import annotations

import copy
import json
import unittest

from contracts.generate_principle_contracts import ContractValidationError
from contracts.verify_p1_return_signal_v3_openapi_transition import (
    HISTORICAL_ROOT_75_SHA256,
    _historical_digest,
    project_pre_signal_v3,
)
from contracts.verify_p1_v3_automation_openapi_transition import operations
from contracts.verify_s3_2_local_order_retirement_openapi_transition import (
    BASE_CONTRACT_SHA256,
    OPENAPI_PATH,
    project_pre_s3_2_local_order_retirement,
)


class S32LocalOrderRetirementOpenApiTransitionTest(unittest.TestCase):
    def test_projection_removes_only_the_v219_addition_before_frozen_signal_v3(self) -> None:
        current = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

        projected = project_pre_s3_2_local_order_retirement(current)

        self.assertEqual(len(operations(current)), len(operations(projected)))
        self.assertEqual(BASE_CONTRACT_SHA256, projected["x-s3-2-contract-sha256"])
        self.assertNotIn(
            "LOCAL_RETIRED",
            projected["components"]["schemas"]["S32OrderDetail"]["properties"]["status"]["enum"],
        )
        self.assertNotIn(
            "409",
            projected["paths"]["/api/v1/brokerage/orders/{orderId}/reconcile"]["post"]["responses"],
        )
        self.assertEqual(projected, project_pre_s3_2_local_order_retirement(projected))
        self.assertEqual(
            HISTORICAL_ROOT_75_SHA256,
            _historical_digest(project_pre_signal_v3(current)),
        )

    def test_projection_rejects_changes_outside_the_exact_v219_surface(self) -> None:
        current = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(current)
        mutated["paths"]["/api/v1/brokerage/orders/{orderId}/reconcile"]["post"]["responses"]["409"][
            "description"
        ] = "Different behavior"

        with self.assertRaisesRegex(ContractValidationError, "additive OpenAPI surface drifted"):
            project_pre_s3_2_local_order_retirement(mutated)


if __name__ == "__main__":
    unittest.main()
