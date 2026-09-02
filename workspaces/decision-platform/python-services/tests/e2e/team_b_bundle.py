"""Team B가 완성됐다고 가정한 REAL_TEAM_B 번들을 만들어 실제 적재 함수에 넣는다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다. `tests/rehearsal/`·`tests/verification/`과 같은 경계다.

범위를 분명히 한다. 이 모듈은 **번들 파일 포맷 검증을 수행하지 않는다.** parquet·safetensors·
golden output 등 10개 파일의 형태는 Team B의 입력 계약이고 `app/p1_owner/importer.py`의
`validate_artifact_bundle`과 그 전용 테스트가 이미 담당한다. 여기서는 그 검증을 통과한 뒤의
산출물, 즉 **import packet**을 production 코드(`_build_import_packet`)로 만들어
`import_p1_return_bundle_v2`에 넣는다. 즉 DB 적재부터 아래로가 이 테스트의 범위다.

packet을 손으로 쓰지 않고 production 함수로 만드는 이유는 계약이 바뀌면 테스트가 같이 깨지게
하기 위해서다.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from app.p1_owner import importer
from app.data._shared.canonical_json import canonical_json_bytes

# exact-31. 실제로 거래할 두 종목만 KRX 실제 코드이고 나머지는 계약이 요구하는 자리수만 채운다.
# `132030`은 계약이 정확히 한 번 포함하도록 요구하는 금 ETF다.
TRADED_SYMBOL: Final = "005930"
SECONDARY_SYMBOL: Final = "000660"
GOLD_SYMBOL: Final = "132030"
UNIVERSE: Final[tuple[str, ...]] = (
    TRADED_SYMBOL,
    SECONDARY_SYMBOL,
    *(f"9{index:05d}" for index in range(28)),
    GOLD_SYMBOL,
)

_FILLER: Final = "b" * 64
_ARTIFACTS: Final[tuple[str, ...]] = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
_SCENARIO_METRICS: Final[dict[str, dict[str, float | int]]] = {
    "BASELINE": {"netReturn": 0.021, "mdd": -0.048, "sharpe": 0.44, "tradeCount": 18},
    "GUIDE": {"netReturn": 0.037, "mdd": -0.041, "sharpe": 0.61, "tradeCount": 22},
    "STRICT": {"netReturn": 0.012, "mdd": -0.033, "sharpe": 0.29, "tradeCount": 11},
}


class TeamBBundleError(RuntimeError):
    """번들 생성 또는 적재가 계약을 만족하지 못했다."""


@dataclass(frozen=True, slots=True)
class ImportedBundle:
    bundle_sha256: str
    artifact_id: str
    run_id: str
    outcome: str
    session_date: date


def _signal_rows(session_date: date, buy_symbols: frozenset[str]) -> list[dict[str, Any]]:
    """거래 대상만 BUY, 나머지는 HOLD. 신호는 결정적이어야 하므로 난수를 쓰지 않는다."""

    rows: list[dict[str, Any]] = []
    for index, symbol in enumerate(UNIVERSE):
        buy = symbol in buy_symbols
        rows.append(
            {
                "symbol": symbol,
                "signal": "BUY" if buy else "HOLD",
                "expectedReturn": 0.031 if buy else -0.004 + (index % 5) / 1000,
            }
        )
    return rows


def _sell_rows(session_date: date, sell_symbols: frozenset[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, symbol in enumerate(UNIVERSE):
        sell = symbol in sell_symbols
        rows.append(
            {
                "symbol": symbol,
                "signal": "SELL" if sell else "HOLD",
                "expectedReturn": -0.026 if sell else -0.004 + (index % 5) / 1000,
            }
        )
    return rows


def _equity_parquet(session_date: date) -> bytes:
    sessions = [session_date]
    table = pa.table(
        {
            "scenario": pa.array(
                [scenario for scenario in _SCENARIO_METRICS for _ in sessions], pa.string()
            ),
            "sessionDate": pa.array(
                [session for _ in _SCENARIO_METRICS for session in sessions], pa.date32()
            ),
            "equityKrw": pa.array(
                [1_000_000.0 for _ in _SCENARIO_METRICS for _ in sessions], pa.float64()
            ),
        }
    )
    buffer = io.BytesIO()
    pq.write_table(table, buffer)  # type: ignore[no-untyped-call]
    return buffer.getvalue()


def build_packet(
    *,
    session_date: date,
    buy_symbols: frozenset[str],
    sell_symbols: frozenset[str],
    ordinal: int,
) -> tuple[dict[str, Any], str]:
    """production `_build_import_packet`으로 REAL_TEAM_B packet과 그 sha256을 만든다."""

    if buy_symbols & sell_symbols:
        raise TeamBBundleError("a symbol cannot be both a BUY and a SELL candidate")
    unknown = (buy_symbols | sell_symbols) - set(UNIVERSE)
    if unknown:
        raise TeamBBundleError(
            f"signals reference symbols outside the exact-31 universe: {unknown}"
        )

    as_of = datetime(session_date.year, session_date.month, session_date.day, 0, 5, tzinfo=UTC)
    fresh_until = datetime(
        session_date.year, session_date.month, session_date.day, 23, 55, tzinfo=UTC
    )
    # 세션마다 다른 bundle_sha256이 필요하다. manifest sha는 packet identity를 결정하므로
    # ordinal을 섞어 append-only pointer가 최신을 택하도록 한다.
    manifest_sha256 = importer._digest(
        canonical_json_bytes(
            {"e2e": "p1-full-pipeline", "ordinal": ordinal, "session": str(session_date)}
        )
    )
    manifest = {
        "contractId": "p1-return-engine-artifact-manifest.v2",
        "evidenceMode": "REAL_TEAM_B",
        "inputPackSha256": _FILLER,
        "mockRuntimeEligible": True,
        "modelQuality": "PASS",
        "realTeamB": True,
        "runId": f"run_p1_e2e_{manifest_sha256[:24]}",
        "producer": {"trainingCodeSha256": _FILLER},
        "artifacts": [{"path": path, "sha256": _FILLER} for path in _ARTIFACTS],
    }
    packet = importer._build_import_packet(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        artifact_id=f"artifact_p1_{manifest_sha256[:24]}",
        session_date=session_date,
        as_of=as_of,
        fresh_until=fresh_until,
        lstm_rows=_signal_rows(session_date, buy_symbols)
        if not sell_symbols
        else _sell_rows(session_date, sell_symbols),
        baseline_rows=_signal_rows(session_date, buy_symbols)
        if not sell_symbols
        else _sell_rows(session_date, sell_symbols),
        scenarios=_SCENARIO_METRICS,
        equity_bytes=_equity_parquet(session_date),
    )
    packet_text = canonical_json_bytes(packet).decode("utf-8")
    return packet, importer._digest(packet_text.encode("utf-8"))


def import_packet(
    *, database_dsn: str, packet: dict[str, Any], packet_sha256: str
) -> ImportedBundle:
    """실제 적재 함수를 그대로 호출한다. 행을 손으로 쓰지 않는다."""

    packet_text = canonical_json_bytes(packet).decode("utf-8")
    try:
        with psycopg.connect(database_dsn, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT outcome,artifact_id,run_id FROM import_p1_return_bundle_v2(%s,%s)",
                    (packet_text, packet_sha256),
                )
                row = cursor.fetchone()
                if row is None:
                    raise TeamBBundleError("import returned no receipt")
                outcome, artifact_id, run_id = row
            connection.commit()
    except psycopg.Error as error:
        raise TeamBBundleError(f"import failed: {error}") from error
    return ImportedBundle(
        bundle_sha256=str(packet["bundleSha256"]),
        artifact_id=str(artifact_id),
        run_id=str(run_id),
        outcome=str(outcome),
        session_date=date.fromisoformat(str(packet["sessionDate"])),
    )
