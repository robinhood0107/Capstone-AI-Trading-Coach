"""Recompute historical RULE and LSTM signals through the production readers."""

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
    """Raised when a complete replay cannot be produced."""


def _pointer_and_universe(
    repository: DailySignalRepository, end: date
) -> tuple[dict[str, str], list[str]]:
    """Resolve the current model pointer and exact-31 universe through the runtime API."""

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
    """Compute one complete point-in-time signal panel for the exact-31 universe."""

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
            # Reject partial panels; production decisions require all 31 symbols.
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
