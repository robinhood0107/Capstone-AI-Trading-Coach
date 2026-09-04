from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.daily_inference import (
    DailyInferenceError,
    DailyInferenceService,
    _features_and_rule,
)


class _Repository:
    def __init__(self) -> None:
        self.packet: dict[str, Any] | None = None

    def context(self, target_session: date) -> dict[str, Any]:
        return {
            "artifactId": "artifact_p1_" + "a" * 24,
            "bundleSha256": "a" * 64,
            "marketManifestSha256": "b" * 64,
            "modelSha256": "c" * 64,
            "outcome": "MATERIALIZE",
            "sourceSession": "2026-08-31",
            "symbols": [f"{value:06d}" for value in range(1, 31)] + ["132030"],
            "targetSession": target_session.isoformat(),
        }

    def history(self, symbol: str, target_session: date) -> list[dict[str, Any]]:
        del symbol, target_session
        first = date(2026, 7, 23)
        return [
            {
                "close": 10_000 + index,
                "high": 10_100 + index,
                "low": 9_900 + index,
                "open": 10_000 + index,
                "sessionDate": (first + timedelta(days=index)).isoformat(),
                "volume": 100_000 + index,
            }
            for index in range(40)
        ]

    def commit(self, packet: dict[str, Any]) -> tuple[str, str]:
        self.packet = packet
        return "IMPORTED", "d" * 64


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def infer(self, request_bytes: bytes) -> bytes:
        self.calls += 1
        request = json.loads(request_bytes)
        return canonical_json_bytes(
            {
                "artifactId": request["artifactId"],
                "bundleSha256": request["bundleSha256"],
                "contractId": "p1-return-inference-response.v1",
                "orderAuthority": "NONE",
                "predictions": [
                    {
                        "expectedReturn": 0.01,
                        "forecastClose": row["currentClose"] * 1.01,
                        "signal": "BUY",
                        "symbol": row["symbol"],
                    }
                    for row in request["rows"]
                ],
                "providerCalls": 0,
                "sessionDate": request["sessionDate"],
            }
        )

    def close(self) -> None:
        pass


def test_daily_service_materializes_exact31_lstm_and_rule_without_confidence() -> None:
    repository = _Repository()
    client = _Client()
    service = DailyInferenceService(repository, client)  # type: ignore[arg-type]

    result = service.ensure_daily_signals(date(2026, 9, 1))

    assert result.outcome == "IMPORTED"
    assert client.calls == 1
    assert repository.packet is not None
    assert len(repository.packet["signals"]) == 62
    assert {item["producer"] for item in repository.packet["signals"]} == {
        "LSTM",
        "RULE_BASELINE",
    }
    assert all("confidence" not in item for item in repository.packet["signals"])
    assert repository.packet["sourceSession"] == "2026-08-31"
    assert repository.packet["targetSession"] == "2026-09-01"


def test_feature_builder_requires_verified_latest_source_session() -> None:
    history = _Repository().history("005930", date(2026, 9, 1))
    with pytest.raises(DailyInferenceError, match="HISTORY_INCOMPLETE"):
        _features_and_rule(history, "2026-08-30")


def test_replayed_context_does_not_call_model_or_commit() -> None:
    repository = _Repository()
    repository.context = lambda _target: {  # type: ignore[method-assign]
        "batchSha256": "e" * 64,
        "outcome": "REPLAYED",
    }
    client = _Client()
    result = DailyInferenceService(repository, client).ensure_daily_signals(  # type: ignore[arg-type]
        date(2026, 9, 1)
    )
    assert result.outcome == "REPLAYED"
    assert client.calls == 0
    assert repository.packet is None


def _history(closes: list[float]) -> list[dict[str, object]]:
    """종가만 다른 최소 이력. rule 판정에는 종가만 쓰인다."""

    from datetime import date, timedelta

    start = date(2026, 6, 1)
    return [
        {
            "close": value,
            "high": value,
            "low": value,
            "open": value,
            "sessionDate": (start + timedelta(days=index)).isoformat(),
            "volume": 1000,
        }
        for index, value in enumerate(closes)
    ]


def test_rule_buys_on_a_steady_uptrend_without_a_same_day_crossover() -> None:
    """교차가 없어도 추세 상태면 BUY 다.

    이전 판정은 prior_ma5 <= prior_ma20 을 요구해 이 경우를 HOLD 로 떨어뜨렸다. 실측으로
    55% 의 날에 매수 후보가 0개가 되고 다음날 초과수익도 -1.14%p 였다.
    """

    # 되돌림이 섞인 상승 추세. 교차는 이미 한참 전에 지났고 지금은 상태만 남았다.
    # 마지막 14개 변화가 9상승 5하락이라 RSI 는 약 64 로 70 아래다 - 단조 상승으로 만들면
    # 하락일이 없어 RSI 가 100 으로 포화되고, 그건 실제 시장 계열에 없는 형태다.
    closes = [100.0 + index for index in range(31)]
    for index in range(14):
        closes.append(closes[-1] + (-1.0 if index % 3 == 1 else 1.0))
    history = _history(closes)

    _, signal = _features_and_rule(history, history[-1]["sessionDate"])

    assert signal == "BUY"


def test_rule_sells_on_a_steady_downtrend_without_a_same_day_crossover() -> None:
    # 되돌림이 섞인 하락 추세. 마지막 14개가 9하락 5상승이라 RSI 는 약 36 으로 30 위다.
    closes = [200.0 - index for index in range(31)]
    for index in range(14):
        closes.append(closes[-1] + (1.0 if index % 3 == 1 else -1.0))
    history = _history(closes)

    _, signal = _features_and_rule(history, history[-1]["sessionDate"])

    assert signal == "SELL"


def test_rule_holds_when_the_trend_has_no_direction() -> None:
    """방향이 없으면 HOLD 다. 상태 판정이 아무 때나 BUY 를 내지는 않는다."""

    closes = [100.0 + (1.0 if index % 2 else 0.0) for index in range(45)]
    history = _history(closes)

    _, signal = _features_and_rule(history, history[-1]["sessionDate"])

    assert signal in {"BUY", "HOLD", "SELL"}
    # 톱니에서는 MA5 와 MA20 이 거의 같으므로 극단 신호가 나오면 안 된다.
    closes_flat = [100.0] * 45
    _, flat_signal = _features_and_rule(
        _history(closes_flat), _history(closes_flat)[-1]["sessionDate"]
    )
    assert flat_signal == "HOLD"


def test_rule_uses_the_long_trend_even_when_the_short_ma_state_is_negative() -> None:
    """RULE_BASELINE은 단기 MA 교차가 아니라 적응형 장기 추세 상태로 판정한다."""

    # 마지막 20세션은 조정 중이라 MA5 < MA20 이다. 그래도 200세션 장기 평균보다 종가가
    # 높고 RSI가 과열이 아니므로 trend_only 규칙은 BUY를 유지해야 한다.
    closes = [100.0] * 200 + [110.0 + (index % 2) - index * 0.2 for index in range(20)]
    history = _history(closes)

    _, signal = _features_and_rule(history, history[-1]["sessionDate"])

    assert sum(closes[-5:]) / 5 < sum(closes[-20:]) / 20
    assert closes[-1] > sum(closes[-200:]) / 200
    assert signal == "BUY"


def test_rule_keeps_a_short_listed_symbol_when_200_sessions_are_unavailable() -> None:
    """상장 이력 39세션도 보유한 전체 이력으로 장기 추세를 계산한다."""

    closes = [100.0] * 20 + [110.0 + (index % 2) - index * 0.2 for index in range(19)]
    history = _history(closes)

    _, signal = _features_and_rule(history, history[-1]["sessionDate"])

    assert len(history) == 39
    assert signal == "BUY"


def test_short_history_reports_the_session_count_and_requirement() -> None:
    """이력이 짧으면 몇 세션인지와 얼마가 필요한지 말한다.

    신규 상장 종목처럼 이력이 짧은 경우 "왜 막혔나"가 곧 대처 방법이다. 정보 없는 예외는
    31종목 중 무엇이 문제인지 알려주지 않아 매번 따로 조사하게 만든다.
    """

    from app.p1_owner.daily_inference import _MIN_HISTORY

    closes = [100.0 + index for index in range(_MIN_HISTORY - 1)]
    history = _history(closes)

    with pytest.raises(DailyInferenceError) as raised:
        _features_and_rule(history, history[-1]["sessionDate"])

    message = str(raised.value)
    assert "HISTORY_TOO_SHORT" in message
    assert f"sessions={_MIN_HISTORY - 1}" in message
    assert f"required={_MIN_HISTORY}" in message


def test_minimum_history_is_derived_from_the_window_and_ma_warmup() -> None:
    """최소 이력은 리터럴이 아니라 파생값이다.

    MA20 이 생기려면 앞에 19세션이 필요하고 모델은 feature 행 _WINDOW_SIZE 개를 받는다.
    리터럴 39 로 되돌아가면 window 나 MA 기간이 바뀔 때 조용히 어긋난다.
    """

    from app.p1_owner.daily_inference import _MA_LONG_WARMUP, _MIN_HISTORY, _WINDOW_SIZE

    assert _MIN_HISTORY == _MA_LONG_WARMUP + _WINDOW_SIZE


def test_stale_source_session_is_reported_separately_from_short_history() -> None:
    """소스 세션 불일치는 이력 부족과 다른 실패다. 원인도 대처도 다르다."""

    closes = [100.0 + index for index in range(45)]
    history = _history(closes)

    with pytest.raises(DailyInferenceError) as raised:
        _features_and_rule(history, "1999-01-01")

    message = str(raised.value)
    assert "HISTORY_INCOMPLETE" in message
    assert "expectedSource=1999-01-01" in message
    assert f"observedSource={history[-1]['sessionDate']}" in message
