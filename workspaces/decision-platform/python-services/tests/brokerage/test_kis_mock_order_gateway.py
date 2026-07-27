from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    LiveOrderGateClosed,
    MOCK_BUY_TR_ID,
    MOCK_SELL_TR_ID,
    ORDER_CASH_PATH,
    MockOrderIntent,
    MockOrderRejected,
)


class FakeTransport:
    def __init__(
        self, response: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []
        self.response = response or {"rt_cd": "0", "output": {"ODNO": "000001"}}
        self.error = error

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        json_body: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append((method, path, tr_id, json_body))
        if self.error is not None:
            raise self.error
        return self.response


class FakeReferenceStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], object] = {}

    def put(self, order_id: str, account_id: str, reference: object) -> None:
        self.values[(order_id, account_id)] = reference

    def get(self, order_id: str, account_id: str) -> object | None:
        return self.values.get((order_id, account_id))


class CountingByteStream(httpx.SyncByteStream):
    def __init__(self, *, size_bytes: int, chunk_size: int = 64 * 1024) -> None:
        self.size_bytes = size_bytes
        self.chunk_size = chunk_size
        self.bytes_yielded = 0
        self.completed = False
        self.closed = False

    def __iter__(self):
        remaining = self.size_bytes
        while remaining:
            chunk_length = min(self.chunk_size, remaining)
            remaining -= chunk_length
            self.bytes_yielded += chunk_length
            yield b"x" * chunk_length
        self.completed = True

    def close(self) -> None:
        self.closed = True


class StreamingTransport(httpx.BaseTransport):
    def __init__(self, stream: CountingByteStream) -> None:
        self.stream = stream
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, stream=self.stream, request=request)


def test_mock_cash_order_maps_buy_sell_tr_ids_and_does_not_retry() -> None:
    transport = FakeTransport()
    gateway = KISMockOrderGateway(transport, mode="mock")

    buy = gateway.submit_cash_order(
        MockOrderIntent("005930", "BUY", "MARKET", quantity=2, estimated_price=70_000)
    )
    sell = gateway.submit_cash_order(
        MockOrderIntent("005930", "SELL", "LIMIT", quantity=1, estimated_price=70_000)
    )

    assert buy.tr_id == MOCK_BUY_TR_ID
    assert sell.tr_id == MOCK_SELL_TR_ID
    assert len(transport.calls) == 2
    assert transport.calls[0] == (
        "POST",
        ORDER_CASH_PATH,
        MOCK_BUY_TR_ID,
        {"PDNO": "005930", "ORD_DVSN": "01", "ORD_QTY": "2", "ORD_UNPR": "0"},
    )
    assert transport.calls[1][3]["ORD_UNPR"] == "70000"


def test_live_mode_fails_before_transport_call() -> None:
    transport = FakeTransport()
    gateway = KISMockOrderGateway(transport, mode="live")

    with pytest.raises(LiveOrderGateClosed):
        gateway.submit_cash_order(
            MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
        )

    assert transport.calls == []


def test_provider_error_is_not_retried_by_order_gateway() -> None:
    transport = FakeTransport(error=TimeoutError("synthetic timeout"))
    gateway = KISMockOrderGateway(transport, mode="mock")

    with pytest.raises(TimeoutError):
        gateway.submit_cash_order(
            MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
        )

    assert len(transport.calls) == 1


def test_rejected_or_malformed_receipt_is_fail_closed() -> None:
    for response in ({"rt_cd": "1", "msg1": "rejected"}, {"rt_cd": "0", "output": {}}):
        gateway = KISMockOrderGateway(FakeTransport(response=response), mode="mock")
        with pytest.raises(MockOrderRejected):
            gateway.submit_cash_order(
                MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
            )


def test_submit_reference_enables_exact_mock_cancel_without_exposing_live_tr_id() -> None:
    transport = FakeTransport(
        response={
            "rt_cd": "0",
            "output": {
                "ODNO": "synthetic-provider-order",
                "KRX_FWDG_ORD_ORGNO": "synthetic-provider-org",
            },
        }
    )
    reference_store = FakeReferenceStore()
    gateway = KISMockOrderGateway(
        transport,
        mode="mock",
        reference_store=reference_store,  # type: ignore[arg-type]
    )
    order_id = "ord_mock_" + "1" * 32
    account_id = "acct_" + "2" * 32

    gateway.submit_cash_order(
        MockOrderIntent("005930", "BUY", "LIMIT", quantity=1, estimated_price=70_000),
        order_id=order_id,
        account_id=account_id,
    )
    receipt = gateway.cancel_cash_order(order_id=order_id, account_id=account_id)

    assert receipt.status == "CANCELLED"
    assert len(transport.calls) == 2
    method, path, tr_id, body = transport.calls[1]
    assert (method, path, tr_id) == (
        "POST",
        "/uapi/domestic-stock/v1/trading/order-rvsecncl",
        "VTTC0013U",
    )
    assert body["RVSE_CNCL_DVSN_CD"] == "02"
    assert body["QTY_ALL_ORD_YN"] == "Y"
    assert "TTTC0013U" not in Path("app/brokerage/kis_mock_order_gateway.py").read_text(
        encoding="utf-8"
    )


def test_private_online_transport_is_mock_only_bounded_and_scrubs_account_echo(
    tmp_path: Path,
) -> None:
    online = importlib.import_module("app.brokerage.kis_mock_online_client")
    sends: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sends.append(request)
        body = request.read().decode("utf-8")
        assert '"CANO":"00000000"' in body
        assert '"ACNT_PRDT_CD":"01"' in body
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output": {
                    "ODNO": "synthetic-provider-order",
                    "KRX_FWDG_ORD_ORGNO": "synthetic-provider-org",
                    "CANO": "00000000",
                },
            },
        )

    client = online.KISMockBrokerageHttpClient(
        settings=online.KISSettings(
            kis_mode="mock",
            kis_offline=True,
            kis_data_dir=tmp_path,
            _env_file=None,
        ),
        account_number=SecretStr("00000000-01"),
        transport=httpx.MockTransport(handler),
        rate_limiter=online.TokenBucket(rate_per_second=1_000),
        budget=online.KISBrokerageCallBudget(token_p_cap=0, brokerage_cap=1),
    )
    try:
        response = client.request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            "VTTC0012U",
            json_body={
                "PDNO": "005930",
                "ORD_DVSN": "00",
                "ORD_QTY": "1",
                "ORD_UNPR": "70000",
            },
        )
        assert "CANO" not in repr(response)

        with pytest.raises(ValueError, match="allowlist"):
            client.request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-cash",
                "TTTC0012U",
                json_body={
                    "PDNO": "005930",
                    "ORD_DVSN": "00",
                    "ORD_QTY": "1",
                    "ORD_UNPR": "70000",
                },
            )
    finally:
        client.close()

    assert len(sends) == 1


def test_private_online_transport_enforces_mock_response_cap_before_full_stream(
    tmp_path: Path,
) -> None:
    online = importlib.import_module("app.brokerage.kis_mock_online_client")
    stream = CountingByteStream(size_bytes=online._MAX_RESPONSE_BYTES + 1)  # noqa: SLF001
    transport = StreamingTransport(stream)
    client = online.KISMockBrokerageHttpClient(
        settings=online.KISSettings(
            kis_mode="mock",
            kis_offline=True,
            kis_data_dir=tmp_path,
            _env_file=None,
        ),
        account_number=SecretStr("00000000-01"),
        transport=transport,
        rate_limiter=online.TokenBucket(rate_per_second=1_000),
        budget=online.KISBrokerageCallBudget(token_p_cap=0, brokerage_cap=1),
    )
    try:
        with pytest.raises(online.KISMockBrokerageError) as captured:
            client.request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-cash",
                "VTTC0012U",
                json_body={
                    "PDNO": "005930",
                    "ORD_DVSN": "00",
                    "ORD_QTY": "1",
                    "ORD_UNPR": "70000",
                },
            )
    finally:
        client.close()

    assert transport.calls == 1
    assert stream.closed is True
    assert stream.completed is False
    assert stream.bytes_yielded <= online._MAX_RESPONSE_BYTES + stream.chunk_size  # noqa: SLF001
    assert captured.value.reason_code == "BROKERAGE_RESPONSE_TOO_LARGE"


def test_private_online_transport_exposes_only_bounded_provider_rejection_code(
    tmp_path: Path,
) -> None:
    online = importlib.import_module("app.brokerage.kis_mock_online_client")
    provider_message = "raw account 00000000 must never escape"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "1",
                "msg_cd": "SAFE001",
                "msg1": provider_message,
            },
        )

    client = online.KISMockBrokerageHttpClient(
        settings=online.KISSettings(
            kis_mode="mock",
            kis_offline=True,
            kis_data_dir=tmp_path,
            _env_file=None,
        ),
        account_number=SecretStr("00000000-01"),
        transport=httpx.MockTransport(handler),
        rate_limiter=online.TokenBucket(rate_per_second=1_000),
        budget=online.KISBrokerageCallBudget(token_p_cap=0, brokerage_cap=1),
    )
    try:
        with pytest.raises(online.KISMockBrokerageError) as captured:
            client.request(
                "POST",
                "/uapi/domestic-stock/v1/trading/order-cash",
                "VTTC0012U",
                json_body={
                    "PDNO": "005930",
                    "ORD_DVSN": "00",
                    "ORD_QTY": "1",
                    "ORD_UNPR": "70000",
                },
            )
    finally:
        client.close()

    assert captured.value.reason_code == "BROKERAGE_PROVIDER_REJECTED"
    assert captured.value.provider_code == "SAFE001"
    assert captured.value.http_status is None
    assert provider_message not in str(captured.value)
    assert provider_message not in repr(captured.value)


def test_encrypted_reference_store_never_persists_provider_reference_plaintext() -> None:
    reference = importlib.import_module("app.brokerage.mock_order_reference_store")
    redis_client = fakeredis.FakeRedis()
    store = reference.EncryptedRedisOrderReferenceStore(
        redis_client,
        encryption_key=SecretStr("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
        ttl_seconds=900,
    )
    order_id = "ord_mock_" + "a" * 32
    account_id = "acct_" + "b" * 32
    provider_order_no = "synthetic-provider-order"
    provider_org_no = "synthetic-provider-org"

    store.put(
        order_id,
        account_id,
        reference.MockProviderOrderReference(
            provider_order_no=provider_order_no,
            provider_org_no=provider_org_no,
            order_division="00",
            quantity=1,
        ),
    )

    restored = store.get(order_id, account_id)
    assert restored is not None
    assert restored.provider_order_no == provider_order_no
    persisted = b" ".join(
        value if isinstance(value, bytes) else str(value).encode()
        for value in redis_client.scan_iter()
    ) + b" ".join(redis_client.mget(list(redis_client.scan_iter())))
    assert provider_order_no.encode() not in persisted
    assert provider_org_no.encode() not in persisted
