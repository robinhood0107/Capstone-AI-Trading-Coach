from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from threading import Event, Thread

import httpcore
import httpx
import pytest
from pydantic import SecretStr

from app.data.krx import _credential_transport, client as client_module
from app.data.krx._credential_transport import KrxCredentialError
from app.data.krx.catalog import KOSDAQ_DAILY, KOSPI_DAILY
from app.data.krx.client import KrxHttpError, KrxOpenApiClient
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


def test_two_endpoints_share_one_logical_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    observed_timeouts: list[dict[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_timeouts.append(dict(request.extensions["timeout"]))
        if request.url.path == KOSPI_DAILY.path:
            now[0] = 104.0
            return httpx.Response(
                200,
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
            200,
            json=_payload(
                _daily_row(
                    symbol="035720",
                    name="카카오",
                    market="KOSDAQ",
                    market_cap=300_000,
                    trading_value=700_000,
                )
            ),
        )

    monkeypatch.setattr(client_module.time, "monotonic", lambda: now[0])
    client, _ = _client(
        monkeypatch,
        httpx.MockTransport(handler),
        settings=KrxOpenApiSettings(
            _env_file=None,
            KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS=5.0,
        ),
    )

    rows = client.fetch_universe_rows(_AS_OF)

    assert len(rows) == 2
    assert observed_timeouts[0] == {
        "connect": 2.0,
        "read": 5.0,
        "write": 2.0,
        "pool": 1.0,
    }
    assert observed_timeouts[1] == {
        "connect": 1.0,
        "read": 1.0,
        "write": 1.0,
        "pool": 1.0,
    }


def test_default_timeout_profile_preserves_full_market_read_budget_for_both_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    observed_timeouts: list[dict[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_timeouts.append(dict(request.extensions["timeout"]))
        if request.url.path == KOSPI_DAILY.path:
            now[0] = 125.0
            return httpx.Response(
                200,
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
            200,
            json=_payload(
                _daily_row(
                    symbol="035720",
                    name="카카오",
                    market="KOSDAQ",
                    market_cap=300_000,
                    trading_value=700_000,
                )
            ),
        )

    monkeypatch.setattr(client_module.time, "monotonic", lambda: now[0])
    client, _ = _client(monkeypatch, httpx.MockTransport(handler))

    rows = client.fetch_universe_rows(_AS_OF)

    assert len(rows) == 2
    assert observed_timeouts == [
        {
            "connect": 2.0,
            "read": 30.0,
            "write": 2.0,
            "pool": 1.0,
        },
        {
            "connect": 2.0,
            "read": 30.0,
            "write": 2.0,
            "pool": 1.0,
        },
    ]


def test_production_constructor_rejects_private_dependency_overrides() -> None:
    settings = KrxOpenApiSettings(_env_file=None)
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    quota = _RecordingQuota()

    with pytest.raises(ValueError, match="private|override"):
        KrxOpenApiClient(settings, transport=transport)
    with pytest.raises(ValueError, match="private|override"):
        KrxOpenApiClient(settings, quota=quota)


def test_production_dependency_logs_cannot_bypass_transport_scrubbing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "synthetic-krx-auth-key"
    provider_reason = "Synthetic-Provider-Reason"
    provider_header_name = "X-Provider-Debug"
    provider_header_value = "synthetic-provider-header-value"
    first_body = json.dumps(
        _payload(
            _daily_row(
                symbol="005930",
                name="삼성전자",
                market="KOSPI",
                market_cap=500_000,
                trading_value=900_000,
            )
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    responses = [
        (
            f"HTTP/1.1 200 {provider_reason}\r\n"
            "Content-Type: application/json\r\n"
            f"{provider_header_name}: {provider_header_value}\r\n"
            f"Content-Length: {len(first_body)}\r\n"
            "\r\n"
        ).encode("ascii")
        + first_body,
        (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            f"AUTH_KEY: {marker}\r\n"
            "Content-Length: 2\r\n"
            "\r\n"
            "{}"
        ).encode("ascii"),
    ]
    inner = httpx.HTTPTransport(retries=0)
    inner._pool = httpcore.ConnectionPool(  # type: ignore[attr-defined]
        network_backend=httpcore.MockBackend(responses),
    )

    class _ClosableRedis:
        def close(self) -> None:
            pass

    quota = _RecordingQuota()
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr(marker),
    )
    monkeypatch.setattr(client_module, "_build_redis_client", _ClosableRedis)
    monkeypatch.setattr(client_module, "RedisQuotaReservation", lambda *_args, **_kwargs: quota)

    def build_http_transport(**kwargs: object) -> httpx.HTTPTransport:
        assert kwargs["proxy"] is None
        assert kwargs["http1"] is True
        assert kwargs["http2"] is False
        assert kwargs["retries"] == 0
        return inner

    monkeypatch.setattr(client_module.httpx, "HTTPTransport", build_http_transport)
    logger_names = ("httpx", "httpcore.connection", "httpcore.http11")
    loggers = tuple(logging.getLogger(name) for name in logger_names)
    records: list[logging.LogRecord] = []

    class _RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _RecordingHandler()
    for logger in loggers:
        caplog.set_level(logging.DEBUG, logger=logger.name)
        monkeypatch.setattr(logger, "propagate", False)
        logger.addHandler(handler)
    try:
        logging.getLogger("httpx").info("safe-httpx-before")
        logging.getLogger("httpcore.http11").debug("safe-httpcore-before")
        assert "safe-httpx-before" in (record.getMessage() for record in records)
        assert "safe-httpcore-before" in (record.getMessage() for record in records)
        records.clear()

        client = KrxOpenApiClient(KrxOpenApiSettings(_env_file=None))
        try:
            with pytest.raises(KrxCredentialError, match="response_unavailable"):
                client.fetch_universe_rows(_AS_OF)
        finally:
            client.close()

        assert client.physical_attempt_count == 2
        logging.getLogger("httpx").info("safe-httpx-after")
        logging.getLogger("httpcore.http11").debug("safe-httpcore-after")
        assert "safe-httpx-after" in (record.getMessage() for record in records)
        assert "safe-httpcore-after" in (record.getMessage() for record in records)
    finally:
        for logger in loggers:
            logger.removeHandler(handler)

    rendered = "\n".join(record.getMessage() for record in records)
    for forbidden in (
        marker,
        "AUTH_KEY",
        "basDd",
        _AS_OF.strftime("%Y%m%d"),
        provider_reason,
        provider_header_name,
        provider_header_value,
    ):
        assert forbidden not in rendered


def test_dependency_log_guard_does_not_hide_unrelated_thread_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("httpx")
    records: list[logging.LogRecord] = []
    worker_start = Event()
    worker_done = Event()

    class _RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    def log_from_unrelated_context() -> None:
        worker_start.wait()
        logger.info("safe-unrelated-http-log")
        worker_done.set()

    handler = _RecordingHandler()
    caplog.set_level(logging.INFO, logger=logger.name)
    monkeypatch.setattr(logger, "propagate", False)
    logger.addHandler(handler)
    worker = Thread(target=log_from_unrelated_context, daemon=True)
    try:
        worker.start()
        with _credential_transport._suppress_dependency_http_logs():
            logger.info("synthetic-guarded-provider-secret")
            worker_start.set()
            assert worker_done.wait(timeout=1)
        logger.info("safe-after-krx-context")
    finally:
        worker_start.set()
        worker.join(timeout=1)
        logger.removeHandler(handler)

    assert not worker.is_alive()
    rendered = "\n".join(record.getMessage() for record in records)
    assert "synthetic-guarded-provider-secret" not in rendered
    assert "safe-unrelated-http-log" in rendered
    assert "safe-after-krx-context" in rendered


def test_constructor_cleanup_failure_is_sanitized_and_still_closes_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_marker = "synthetic-initialize-secret-/private/provider"
    close_marker = "synthetic-transport-close-secret-/private/provider"

    class FailingTransport:
        closed = 0

        def close(self) -> None:
            self.closed += 1
            raise RuntimeError(close_marker)

    class RecordingRedis:
        closed = 0

        def close(self) -> None:
            self.closed += 1

    transport = FailingTransport()
    redis_client = RecordingRedis()
    monkeypatch.setattr(client_module, "_build_tls_context", lambda: object())
    monkeypatch.setattr(client_module, "_build_redis_client", lambda: redis_client)
    monkeypatch.setattr(client_module.httpx, "HTTPTransport", lambda **_: transport)
    monkeypatch.setattr(
        KrxOpenApiClient,
        "_initialize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(primary_marker)),
    )

    with pytest.raises(KrxCredentialError, match="initialization_unavailable") as exc_info:
        KrxOpenApiClient(KrxOpenApiSettings(_env_file=None))

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert primary_marker not in rendered
    assert close_marker not in rendered
    assert transport.closed == 1
    assert redis_client.closed == 1
    assert exc_info.value.__cause__ is None


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


def test_first_endpoint_read_timeout_is_not_retried_and_keeps_exact_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-provider-secret"
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        raise httpx.ReadTimeout(marker, request=request)

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(KrxCredentialError, match="read_timeout") as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    assert paths == [KOSPI_DAILY.path]
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1
    assert exc_info.value.__cause__ is None
    assert marker not in f"{exc_info.value!r} {exc_info.value}"


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


def test_second_endpoint_validation_failure_keeps_exact_request_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-provider-secret"
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
            200,
            headers={"content-type": "application/json"},
            json={"unknown": marker},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(KrxHttpError) as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    diagnostic = exc_info.value.validation_diagnostic
    assert diagnostic is not None
    assert diagnostic.leaf == "envelope_key_mismatch"
    assert diagnostic.request_ordinal == 2
    assert diagnostic.service == "ksq_bydd_trd"
    assert paths == [KOSPI_DAILY.path, KOSDAQ_DAILY.path]
    assert len(quota.reservations) == 2
    assert client.physical_attempt_count == 2
    assert marker not in str(exc_info.value)
    assert marker not in repr(diagnostic)


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


@pytest.mark.parametrize(
    (
        "headers",
        "content",
        "expected_stage",
        "expected_leaf",
        "expected_content_type_class",
        "expected_body_class",
        "expected_utf8_valid",
        "expected_bom",
    ),
    [
        (
            {},
            b'{"OutBlock_1":[]}',
            "media_type",
            "content_type_missing",
            "missing",
            "json_candidate",
            True,
            False,
        ),
        (
            [
                ("content-type", "application/json"),
                ("content-type", "text/html"),
            ],
            b'{"OutBlock_1":[]}',
            "media_type",
            "content_type_multiple",
            "multiple",
            "json_candidate",
            True,
            False,
        ),
        (
            {"content-type": "text/html; charset=utf-8"},
            b"<html><body>synthetic-provider-marker</body></html>",
            "media_type",
            "content_type_unsupported",
            "other",
            "html_like",
            True,
            False,
        ),
        (
            {"content-type": "application/json"},
            b"<html><body>synthetic-provider-marker</body></html>",
            "json_decode",
            "json_decode_failed",
            "application_json",
            "html_like",
            True,
            False,
        ),
        (
            {"content-type": "application/json"},
            b'{"OutBlock_1":',
            "json_decode",
            "json_decode_failed",
            "application_json",
            "json_candidate",
            True,
            False,
        ),
        (
            {"content-type": "application/json"},
            b'\xef\xbb\xbf{"OutBlock_1":[]}',
            "json_decode",
            "json_decode_failed",
            "application_json",
            "json_candidate",
            True,
            True,
        ),
        (
            {"content-type": "application/json"},
            b"\xff\xfe",
            "json_decode",
            "json_decode_failed",
            "application_json",
            "opaque",
            False,
            False,
        ),
        (
            {"content-type": "application/json"},
            b'{"OutBlock_1":[],"OutBlock_1":[]}',
            "json_limits",
            "json_limits_exceeded",
            "application_json",
            "json_candidate",
            True,
            False,
        ),
    ],
)
def test_http_200_validation_failure_preserves_only_allowlisted_response_leaf(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str] | list[tuple[str, str]],
    content: bytes,
    expected_stage: str,
    expected_leaf: str,
    expected_content_type_class: str,
    expected_body_class: str,
    expected_utf8_valid: bool,
    expected_bom: bool,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, headers=headers, content=content)

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(KrxHttpError) as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    diagnostic = exc_info.value.validation_diagnostic
    assert diagnostic is not None
    assert diagnostic.stage == expected_stage
    assert diagnostic.leaf == expected_leaf
    assert diagnostic.request_ordinal == 1
    assert diagnostic.service == "stk_bydd_trd"
    assert diagnostic.http_status == 200
    assert diagnostic.content_type_class == expected_content_type_class
    assert diagnostic.body_class == expected_body_class
    assert diagnostic.utf8_valid is expected_utf8_valid
    assert diagnostic.utf8_bom_present is expected_bom
    assert outbound == 1
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1
    assert "synthetic-provider-marker" not in str(exc_info.value)
    assert "synthetic-provider-marker" not in repr(diagnostic)


def test_http_200_unknown_object_records_shape_counts_without_provider_keys_or_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-provider-secret"
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"unknownCode": "synthetic", "unknownMessage": marker},
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(KrxHttpError) as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    diagnostic = exc_info.value.validation_diagnostic
    assert diagnostic is not None
    assert diagnostic.stage == "envelope_shape"
    assert diagnostic.leaf == "envelope_key_mismatch"
    assert diagnostic.request_ordinal == 1
    assert diagnostic.service == "stk_bydd_trd"
    assert diagnostic.top_level_type == "object"
    assert diagnostic.top_level_key_count == 2
    assert diagnostic.expected_block_present is False
    assert outbound == 1
    assert len(quota.reservations) == 1
    assert marker not in str(exc_info.value)
    assert marker not in repr(diagnostic)
    assert "unknownCode" not in repr(diagnostic)
    assert "unknownMessage" not in repr(diagnostic)


@pytest.mark.parametrize(
    ("content", "expected_top_level_type"),
    [
        (b"[]", "array"),
        (b'"synthetic-provider-secret"', "string"),
        (b"1", "number"),
        (b"true", "boolean"),
        (b"null", "null"),
    ],
)
def test_http_200_non_object_root_records_only_the_top_level_type(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes,
    expected_top_level_type: str,
) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=content,
        )

    client, quota = _client(monkeypatch, httpx.MockTransport(handler))
    try:
        with pytest.raises(KrxHttpError) as exc_info:
            client.fetch_universe_rows(_AS_OF)
    finally:
        client.close()

    diagnostic = exc_info.value.validation_diagnostic
    assert diagnostic is not None
    assert diagnostic.stage == "root_shape"
    assert diagnostic.leaf == "payload_not_object"
    assert diagnostic.top_level_type == expected_top_level_type
    assert diagnostic.request_ordinal == 1
    assert diagnostic.service == "stk_bydd_trd"
    assert outbound == 1
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1
    assert "synthetic-provider-secret" not in str(exc_info.value)
    assert "synthetic-provider-secret" not in repr(diagnostic)


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


def test_close_failure_is_stable_and_attempts_all_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-client-close-secret-/private/provider"

    class FailingHttp:
        closed = 0

        def close(self) -> None:
            self.closed += 1
            raise RuntimeError(marker)

    class RecordingRedis:
        closed = 0

        def close(self) -> None:
            self.closed += 1

    client, _ = _client(
        monkeypatch,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"OutBlock_1": []})),
    )
    http_client = FailingHttp()
    redis_client = RecordingRedis()
    client._http = http_client  # type: ignore[assignment]  # noqa: SLF001
    client._redis_client = redis_client  # noqa: SLF001

    with pytest.raises(KrxCredentialError, match="cleanup_unavailable") as exc_info:
        client.close()

    assert marker not in str(exc_info.value)
    assert http_client.closed == 1
    assert redis_client.closed == 1
    assert exc_info.value.__cause__ is None
