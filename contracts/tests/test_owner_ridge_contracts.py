"""새 API surface만 바뀌며 과거 OpenAPI는 원형 복원이 가능해야 한다."""
import copy
import json
import unittest
from contracts.generate_owner_ridge_contracts import PREVIOUS, ROOT, project_previous
from contracts.historical_openapi_projection import project_historical_root


class OwnerRidgeContractTest(unittest.TestCase):
    def test_previous_root_is_preserved_exactly(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        # PREVIOUS 는 한 세대의 바이트다. 그 뒤에 내린 제품 결정(매수 마감 09:40->14:30
        # 등)을 되돌린 뒤 비교해야 비교가 성립한다.
        self.assertEqual(
            json.loads(PREVIOUS.read_text()),
            project_historical_root(project_previous(current)),
        )

    def test_new_owner_surface_cannot_hide_behind_historical_projection(self):
        current = json.loads((ROOT / 'contracts/openapi/openapi.json').read_text())
        changed = copy.deepcopy(current)
        changed['paths']['/api/v1/risk/kill-switch']['post']['x-required-role'] = 'USER'
        with self.assertRaises(ValueError):
            project_previous(changed)
