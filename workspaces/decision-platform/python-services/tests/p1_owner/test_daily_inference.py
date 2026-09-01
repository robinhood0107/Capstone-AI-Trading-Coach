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
