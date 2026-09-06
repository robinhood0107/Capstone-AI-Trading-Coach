"""새 API surface만 바뀌며 과거 OpenAPI는 원형 복원이 가능해야 한다."""
import copy
import json
import unittest
from contracts.generate_owner_ridge_contracts import PREVIOUS, ROOT, project_previous


class OwnerRidgeContractTest(unittest.TestCase):
    def test_previous_root_is_preserved_exactly(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        self.assertEqual(json.loads(PREVIOUS.read_text()), project_previous(current))

    def test_new_owner_surface_cannot_hide_behind_historical_projection(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        changed = copy.deepcopy(current)
        changed['paths']['/api/v1/risk/kill-switch']['post']['x-required-role'] = 'USER'
        with self.assertRaises(ValueError):
            project_previous(changed)
