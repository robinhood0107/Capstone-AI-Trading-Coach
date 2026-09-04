"""과거 영업일의 RULE·LSTM 신호를 실제 코드로 다시 계산한다 (재생 1단계).

## 왜 두 단계인가

대시보드의 실행 목록·보유·실현손익이 비어 있었다. 실질 거래 이력이 0건이고, 다른 팀원이
clone 해도 같은 상태에 도달할 방법이 없었다. 그래서 과거 영업일을 재생한다.

그런데 재생에 필요한 두 자원이 서로 다른 경계에 있다.

    추론 서버   127.0.0.1:50057 - decision-platform 컨테이너 안에서만 닿는다
    이력 표     RLS FORCE - superuser 만 쓸 수 있다

한 프로세스가 둘 다 가지면 decision-platform 에 superuser 자격증명이 생긴다. 그래서 나눈다.

    1단계 (이 파일)  decision-platform 안. 읽기 전용으로 신호를 계산해 JSON 으로 내보낸다.
    2단계            seed 컨테이너. superuser 로 그 JSON 을 읽어 이력 표에 쓴다.

## 값을 지어내지 않는다

세션마다 그 시점까지의 바만 쓰고(PIT) 프로덕션 코드를 그대로 호출한다.

    RULE 판정   daily_inference._features_and_rule      (그대로 import)
    LSTM 판정   기존 추론 gRPC + model_shape.classify_signal
    바 조회     p1_read_automation_atr_bars_v1          (런타임과 같은 함수)

즉 "무엇을 언제 샀는가" 는 전부 계산 결과다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, Final, cast


from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.daily_inference import (
    DailyInferenceError,
    DailySignalRepository,
    ReturnInferenceClient,
    _features_and_rule,
)
from app.p1_owner.model_shape import classify_signal

_CONTRACT_ID: Final = "p1-history-replay-signals.v1"


class HistoryReplaySignalError(RuntimeError):
    """재생할 수 없으면 조용히 건너뛰지 않고 사유를 남기고 멈춘다."""


def _pointer_and_universe(
    repository: DailySignalRepository, end: date
) -> tuple[dict[str, str], list[str]]:
    """모델 포인터와 exact-31 을 제품 함수에서 받는다.

    `decision_automation_runtime` 은 `current_p1_return_model_pointer` 나
    `p1_return_model_seed_signal` 을 직접 SELECT 할 권한이 없다 - 그것이 의도된 역할 분리다.
    `p1_read_daily_inference_context_v1` 이 같은 값을 definer 경계 안에서 돌려주므로 그것을 쓴다.
    포인터는 세션별이 아니라 현재 값이므로 아무 세션에서 받아도 같다. 배치가 이미 있는 세션은
    REPLAYED 를 돌려주니 MATERIALIZE 가 나올 때까지 하루씩 앞으로 물러난다.
    """

    probe = end
    for _ in range(400):
        context = repository.context(probe)
        if context is not None and context.get("outcome") == "MATERIALIZE":
            symbols = [str(item) for item in cast(list[Any], context["symbols"])]
            if len(symbols) != 31 or len(set(symbols)) != 31:
                raise HistoryReplaySignalError(
                    f"HISTORY_REPLAY_UNIVERSE_INVALID count={len(symbols)}"
                )
            return (
                {
                    "artifactId": str(context["artifactId"]),
                    "bundleSha256": str(context["bundleSha256"]),
                    "modelSha256": str(context["modelSha256"]),
                },
                sorted(symbols),
            )
        probe -= timedelta(days=1)
    raise HistoryReplaySignalError("HISTORY_REPLAY_MODEL_POINTER_MISSING")


def _session_signals(
    repository: DailySignalRepository,
    client: ReturnInferenceClient,
    pointer: dict[str, str],
    symbols: Sequence[str],
    session: date,
) -> tuple[str, dict[str, dict[str, Any]]] | None:
    """한 세션의 31종목 RULE·LSTM 신호. 이력이 모자라면 그 세션을 건너뛴다.

    소스 세션은 달력이 아니라 조회된 바가 정한다 - 런타임이 쓰는
    `p1_read_automation_atr_bars_v1` 이 대상 세션 이전의 바만 돌려주므로 그 마지막 바가 곧
    그 시점의 PIT 소스다. 종목마다 같아야 하고 다르면 `_features_and_rule` 이 거부한다.
    """

    probe = repository.history(symbols[0], session)
    if not probe:
        return None
    source_session = str(probe[-1]["sessionDate"])
    feature_rows: list[dict[str, Any]] = []
    rule: dict[str, str] = {}
    closes: dict[str, int] = {}
    for symbol in symbols:
        history = repository.history(symbol, session)
        try:
            features, rule_signal = _features_and_rule(history, source_session)
        except DailyInferenceError:
            # 이력이 짧거나 소스 세션이 어긋난 종목이 하나라도 있으면 그 세션은 재생하지
            # 않는다. 31종목이 아닌 부분 패널로 만든 이력은 운영과 다른 것이 된다.
            return None
        rule[symbol] = rule_signal
        closes[symbol] = int(features[-1][FEATURE_ORDER.index("raw_close")])
        feature_rows.append(
            {
                "currentClose": features[-1][FEATURE_ORDER.index("raw_close")],
                "features": features,
                "sessionDate": session.isoformat(),
                "symbol": symbol,
            }
        )
    request = {
        "artifactId": pointer["artifactId"],
        "bundleSha256": pointer["bundleSha256"],
        "contractId": "p1-return-inference-request.v1",
        "rows": feature_rows,
        "sessionDate": session.isoformat(),
    }
    response = json.loads(client.infer(canonical_json_bytes(request)))
    predictions = cast(list[dict[str, Any]], response["predictions"])
    out: dict[str, dict[str, Any]] = {}
    for item in predictions:
        symbol = str(item["symbol"])
        expected = float(item["expectedReturn"])
        out[symbol] = {
            "closeKrw": closes[symbol],
            "expectedReturn": expected,
            "lstm": classify_signal(expected),
            "rule": rule[symbol],
        }
    if set(out) != set(symbols):
        raise HistoryReplaySignalError("HISTORY_REPLAY_RESPONSE_SYMBOLS_INVALID")
    return source_session, out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", default="-", help="'-' 이면 표준출력")
    arguments = parser.parse_args(argv)
    if arguments.start > arguments.end:
        raise SystemExit("HISTORY_REPLAY_RANGE_INVALID")

    dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
    repository = DailySignalRepository(dsn)
    client = ReturnInferenceClient(
        os.environ.get("RETURN_INFERENCE_GRPC_TARGET", "127.0.0.1:50057").strip(),
        os.environ.get("RETURN_INFERENCE_GRPC_SHARED_SECRET", "").strip(),
    )
    try:
        pointer, symbols = _pointer_and_universe(repository, arguments.end)
        sessions: dict[str, Any] = {}
        # 달력 날짜를 훑고 거래 세션이 아닌 날은 바 조회가 비어 스스로 걸러진다.
        session = arguments.start
        while session <= arguments.end:
            result = _session_signals(repository, client, pointer, symbols, session)
            if result is None:
                print(f"skip {session}", file=sys.stderr)
            else:
                source_session, signals = result
                if source_session == session.isoformat():
                    # 대상 세션의 바가 이미 있으면 그 날의 종가를 본 것이 되어 PIT 이 깨진다.
                    print(f"skip {session} (not point-in-time)", file=sys.stderr)
                else:
                    sessions[session.isoformat()] = {
                        "signals": signals,
                        "sourceSession": source_session,
                    }
                    print(f"ok {session} source={source_session}", file=sys.stderr)
            session += timedelta(days=1)
    finally:
        client.close()

    document = {
        "bundleSha256": pointer["bundleSha256"],
        "contractId": _CONTRACT_ID,
        "modelSha256": pointer["modelSha256"],
        "sessions": sessions,
        "symbols": list(symbols),
    }
    payload = canonical_json_bytes(document).decode("utf-8")
    if arguments.out == "-":
        print(payload)
    else:
        with open(arguments.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    print(f"HISTORY_REPLAY_SIGNALS=sessions={len(sessions)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
