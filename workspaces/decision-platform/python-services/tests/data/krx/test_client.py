from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.data.krx import _credential_transport
from app.data.krx.catalog import KOSDAQ_DAILY, KOSPI_DAILY
from app.data.krx.client import KrxOpenApiClient
from app.data.krx.settings import KrxOpenApiSettings


_AS_OF = date(2026, 7, 15)


@dataclass
class _RecordingQuota:
    reservations: list[str] = field(default_factory=list)

    def reserve(self, *, attempt_id: str) -> None:
        assert attempt_id
        self.reservations.append(attempt_id)


class _NonMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"non-mock transport must not run: {request.method}")


def _daily_row(
    *,
    symbol: str,
    name: str,
    market: str,
    market_cap: int,
    trading_value: int,
) -> dict[str, str]:
    return {
        "BAS_DD": "20260715",
        "ISU_CD": symbol,
        "ISU_NM": name,
        "MKT_NM": market,
        "SECT_TP_NM": "보통주",
        "TDD_CLSPRC": "10000",
        "CMPPREVDD_PRC": "100",
        "FLUC_RT": "1.00",
        "TDD_OPNPRC": "9900",
        "TDD_HGPRC": "10100",
        "TDD_LWPRC": "9800",
        "ACC_TRDVOL": "123456",
        "ACC_TRDVAL": str(trading_value),
        "MKTCAP": str(market_cap),
        "LIST_SHRS": "1000000",
    }


def _payload(*rows: dict[str, str]) -> dict[str, object]:
    return {"OutBlock_1": list(rows)}


def _client(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
    *,
    settings: KrxOpenApiSettings | None = None,
    quota: _RecordingQuota | None = None,
) -> tuple[KrxOpenApiClient, _RecordingQuota]:
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr("synthetic-krx-auth-key"),
    )
    selected_quota = quota or _RecordingQuota()
    client = KrxOpenApiClient._for_test(
        settings=settings or KrxOpenApiSettings(_env_file=None),
        transport=handler,
        quota=selected_quota,
    )
    return client, selected_quota


def test_test_factory_accepts_only_mock_transport() -> None:
    with pytest.raises(ValueError, match="MockTransport"):
        KrxOpenApiClient._for_test(
            settings=KrxOpenApiSettings(_env_file=None),
            transport=_NonMockTransport(),
            quota=_RecordingQuota(),
        )


def test_production_constructor_rejects_private_dependency_overrides() -> None:
    settings = KrxOpenApiSettings(_env_file=None)
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    quota = _RecordingQuota()

    with pytest.raises(ValueError, match="private|override"):
        KrxOpenApiClient(settings, transport=transport)
    with pytest.raises(ValueError, match="private|override"):
        KrxOpenApiClient(settings, quota=quota)


def test_fetch_universe_rows_calls_kospi_then_kosdaq_once_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert dict(request.url.params) == {"basDd": "20260715"}
        if request.url.path == KOSPI_DAILY.path:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_payload(
                    _daily_row(
                        symbol="005930",
                        name="삼성전자",
                        market="KOSPI",
                        market_cap=500_000,
                        trading_value=900_000,
                    ),
                    _daily_row(
                        symbol="000660",
                        name="SK하이닉스",
                        market="KOSPI",
                        market_cap=400_000,
                        trading_value=800_000,
                    ),
                ),
            )
        assert request.url.path == KOSDAQ_DAILY.path
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=_payload(
                _daily_row(
                    symbol="247540",
                    name="에코프로비엠",
                    market="KOSDAQ",
                    market_cap=300_000,
                    trading_value=700_000,
                )
            ),
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        rows = client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert paths == [KOSPI_DAILY.path, KOSDAQ_DAILY.path]
    assert [(row.market, row.symbol) for row in rows] == [
        ("KOSPI", "005930"),
        ("KOSPI", "000660"),
        ("KOSDAQ", "247540"),
    ]
    assert len(quota.reservations) == 2
    assert client.physical_attempt_count == 2


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_redirect_is_not_followed_or_retried(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(
            status_code,
            headers={"location": "https://attacker.invalid/credential-capture"},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="redirect|http_status"):
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert outbound == 1
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_retryable_status_still_has_zero_automatic_retries(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(
            status_code,
            headers={"content-type": "application/json"},
            json={"error": "synthetic provider error"},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="http_status"):
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert outbound == 1
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1


def test_second_endpoint_failure_makes_whole_fetch_fail_without_partial_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == KOSPI_DAILY.path:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_payload(
                    _daily_row(
                        symbol="005930",
                        name="삼성전자",
                        market="KOSPI",
                        market_cap=500_000,
                        trading_value=900_000,
                    )
                ),
            )
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": "synthetic provider error"},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="http_status"):
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert paths == [KOSPI_DAILY.path, KOSDAQ_DAILY.path]
    assert len(quota.reservations) == 2
    assert client.physical_attempt_count == 2


def test_parse_failure_is_not_retried_and_does_not_call_second_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"OutBlock_1": [{"BAS_DD": "wrong-shape"}]},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="parse|row|field|date"):
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert outbound == 1
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1


def test_fetch_rejects_non_session_date_before_any_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="trading|session"):
            client.fetch_universe_rows(date(2026, 7, 12))
    finally:
        client.close()

    assert outbound == 0
    assert quota.reservations == []
    assert client.physical_attempt_count == 0


def test_fetch_rejects_date_before_official_service_start_before_any_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="supported|range"):
            client.fetch_universe_rows(date(2009, 12, 30))
    finally:
        client.close()

    assert outbound == 0
    assert quota.reservations == []
    assert client.physical_attempt_count == 0


def test_fetch_sanitizes_calendar_failure_before_any_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-calendar-secret-/private/provider"
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    monkeypatch.setattr(
        "app.data.krx.client.is_xkrx_trading_day",
        lambda _: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="calendar_unavailable") as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert marker not in str(exc_info.value)
    assert outbound == 0
    assert quota.reservations == []
    assert client.physical_attempt_count == 0


def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(
        monkeypatch,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"OutBlock_1": []})),
    )

    client.close()
    client.close()
