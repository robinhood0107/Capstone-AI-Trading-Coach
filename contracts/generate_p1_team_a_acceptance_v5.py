"""현재 owner 중지와 Ridge 표시용 exact-45 client. v4 원본은 보존한다."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from contracts.generate_p1_team_a_acceptance import canonical_json, generate_client, sha256
from contracts.generate_p1_team_a_acceptance_v4 import EXPECTED_OPERATIONS_V4, _remove_confidence_fields

EXPECTED_OPERATIONS_V5 = tuple(
 (category,method,'/api/v2/risk/kill-switch', 'readOwnerKillSwitch' if method=='GET' else 'changeOwnerKillSwitch',statuses)
 if path=='/api/v1/risk/kill-switch' else (category,method,path,operation,statuses)
 for category,method,path,operation,statuses in EXPECTED_OPERATIONS_V4
)


def artifacts():
    raw=(ROOT/'contracts/openapi/openapi.json').read_bytes()
    document=json.loads(raw)
    client_document=copy.deepcopy(document)
    _remove_confidence_fields(client_document)
    catalog={
      'contractId':'p1-team-a-acceptance.v5','acceptanceOperationCount':45,'sameOriginPrefix':'/api',
      'rootOpenApi':{'path':'contracts/openapi/openapi.json','operationCount':78,'sha256':sha256(raw)},
      'preservedV4':{'catalogSha256':sha256((ROOT/'contracts/catalogs/p1-team-a-acceptance.v4.json').read_bytes()),
                     'clientSha256':sha256((ROOT/'workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v4.ts').read_bytes())},
      'operations':[{'sequence':index,'category':c,'method':m,'path':p,'operationId':o,'expectedStatuses':list(s)}
                    for index,(c,m,p,o,s) in enumerate(EXPECTED_OPERATIONS_V5,1)]}
    return {
      ROOT/'contracts/catalogs/p1-team-a-acceptance.v5.json':canonical_json(catalog),
      ROOT/'workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v5.ts':generate_client(
          client_document,expected_operations=EXPECTED_OPERATIONS_V5,expected_root_count=78,
          generated_by='contracts/generate_p1_team_a_acceptance_v5.py')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for path,data in artifacts().items():
        if args.check:
            if not path.exists() or path.read_bytes()!=data:raise SystemExit('V5 artifact drift: '+str(path.relative_to(ROOT)))
        else:path.write_bytes(data)
    print('P1_TEAM_A_ACCEPTANCE_V5_GENERATION=PASS')
