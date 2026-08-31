"""기록된 KIS 잔고를 risk-balance 관측으로 재생하는 SQL을 만든다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 pytest 수집
대상도 아니다.

왜 필요한가. `p1_automation_risk_balance_projection_v2`(V91)는 arm 판정에서
`source_version='kis-mock-online-complete-v2'` 관측을 요구한다. 그 관측을 만드는 실시간 경로는
거래시간(09:10~15:00 KST) KIS 잔고조회뿐인데, 배포가 장 마감 이후라 실행할 수 없다.

그래서 같은 날 실제로 받아 기록해 둔 잔고를 그대로 재생한다. 값을 지어내지 않고 관측 시각도
현재로 바꾸지 않는다. 출처는 fixture 옆의 provenance 문서에 적혀 있다.

payload와 해시는 production writer(`app/brokerage/kis_mock_portfolio_writer.py`)와 같은 방식으로
계산해 행의 모양이 실제 관측과 다르지 않게 한다. 재생본이라는 사실은 데이터가 아니라 문서와
아티팩트가 증명한다.

실행:
  python tests/rehearsal/replay_risk_balance_observation.py \\
    artifacts/decision-platform/live-rehearsal/replay-kis-mock-portfolio.v1.json
  # 출력 SQL을 검토한 뒤 psql로 흘려 넣는다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_ROOT_FIELDS = {
    "schemaVersion",
    "sourceVersion",
    "ownerUserId",
    "ownerScopeHash",
    "observedAt",
    "receivedAt",
    "completeness",
    "cashKrw",
    "portfolioEquityKrw",
    "marginRequirementKrw",
    "positions",
}


class ReplayInputError(ValueError):
    """재생 입력이 계약을 만족하지 못한다."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_statements(path: Path) -> list[str]:
    artifact_bytes = path.read_bytes()
    if len(artifact_bytes) > 64 * 1024:
        raise ReplayInputError("replay fixture exceeds the bound")
    root = json.loads(artifact_bytes)
    if not isinstance(root, dict) or set(root) != _ROOT_FIELDS:
        raise ReplayInputError("replay fixture root shape is invalid")
    if root["sourceVersion"] != "kis-mock-online-complete-v2":
        raise ReplayInputError("replay fixture is not a risk-balance replay")
    if root["completeness"] != "COMPLETE":
        raise ReplayInputError("an incomplete balance cannot satisfy the arm gate")

    positions = sorted(root["positions"], key=lambda item: str(item["symbol"]))
    payload = {
        "cashKrw": int(root["cashKrw"]),
        "completeness": root["completeness"],
        "marginRequirementKrw": int(root["marginRequirementKrw"]),
        "ownerScopeHash": root["ownerScopeHash"],
        "portfolioEquityKrw": int(root["portfolioEquityKrw"]),
        "positions": [
            {
                "isGoldEtfEtn": bool(item["isGoldEtfEtn"]),
                "marketValueKrw": int(item["marketValueKrw"]),
                "quantity": int(item["quantity"]),
                "symbol": str(item["symbol"]),
            }
            for item in positions
        ],
    }
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    identity = _canonical(
        {
            "artifactHash": artifact_hash,
            "observedAt": root["observedAt"],
            "ownerScopeHash": root["ownerScopeHash"],
            "payload": payload,
            "schemaVersion": root["schemaVersion"],
            "sourceVersion": root["sourceVersion"],
        }
    )
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    observation_id = f"pbo_{identity_hash}"
    source_ref = hashlib.sha256(f"s3-kis-mock-portfolio:{identity_hash}".encode()).hexdigest()

    statements = [
        "INSERT INTO portfolio_balance_observations ("
        "observation_id, owner_user_id, account_scope_hash, source, context_status, cash_krw,"
        " portfolio_equity_krw, margin_requirement_krw, completeness, position_count,"
        " observed_at, received_at, schema_version, source_version, payload_json, source_ref,"
        " artifact_hash) VALUES ("
        f"{_quote(observation_id)}, {_quote(str(root['ownerUserId']))},"
        f" {_quote(str(root['ownerScopeHash']))}, 'KIS_MOCK', 'ACTIVE', {payload['cashKrw']},"
        f" {payload['portfolioEquityKrw']}, {payload['marginRequirementKrw']},"
        f" {_quote(payload['completeness'])}, {len(positions)},"
        f" {_quote(str(root['observedAt']))}::timestamptz,"
        f" {_quote(str(root['receivedAt']))}::timestamptz,"
        f" {_quote(str(root['schemaVersion']))}, {_quote(str(root['sourceVersion']))},"
        f" {_quote(_canonical(payload))}::jsonb, {_quote(source_ref)}, {_quote(artifact_hash)})"
        " ON CONFLICT DO NOTHING;"
    ]
    for item in payload["positions"]:
        statements.append(
            "INSERT INTO portfolio_position_observations ("
            "balance_observation_id, symbol, quantity, market_value_krw, is_gold_etf_etn"
            f") VALUES ({_quote(observation_id)}, {_quote(item['symbol'])}, {item['quantity']},"
            f" {item['marketValueKrw']}, {str(item['isGoldEtfEtn']).lower()})"
            " ON CONFLICT DO NOTHING;"
        )
    return statements


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        for statement in build_statements(Path(argv[1])):
            print(statement)
    except (ReplayInputError, OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"REPLAY_RISK_BALANCE=FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
