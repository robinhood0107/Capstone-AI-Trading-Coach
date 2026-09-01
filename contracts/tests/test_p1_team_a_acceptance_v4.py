from __future__ import annotations

import json
import unittest

from contracts.generate_p1_team_a_acceptance_v4 import (
    CATALOG_PATH,
    CLIENT_PATH,
    EXPECTED_OPERATIONS_V4,
    main,
)


class P1TeamAAcceptanceV4ContractTest(unittest.TestCase):
    def test_generated_current_exact45_artifacts_are_checked_in(self) -> None:
        self.assertEqual(0, main(["--check"]))
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(45, catalog["acceptanceOperationCount"])
        self.assertEqual(76, catalog["rootOpenApi"]["operationCount"])
        self.assertEqual(45, len(EXPECTED_OPERATIONS_V4))

    def test_current_signal_client_uses_v3_without_confidence(self) -> None:
        client = CLIENT_PATH.read_text(encoding="utf-8")
        signal_line = next(
            line for line in client.splitlines() if '"SignalV3RuntimeResponse"' in line
        )
        self.assertNotIn("confidence", signal_line)
        self.assertIn('/api/v3/signals/{symbol}', client)
        self.assertIn("readSignalV3", client)
        self.assertEqual(45, client.count("expectedStatuses:"))


if __name__ == "__main__":
    unittest.main()
