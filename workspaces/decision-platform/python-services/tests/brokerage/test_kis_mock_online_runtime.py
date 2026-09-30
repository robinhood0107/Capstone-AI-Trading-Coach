from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.brokerage.brokerage_grpc_server import BrokerageGrpcServerSettings
from app.brokerage.kis_mock_online_client import (
    KISBrokerageCallBudget,
    KISBrokerageCallBudgetExceeded,
)
from app.brokerage.kis_mock_online_runtime import (
    KISMockExecutionReader,
    KISMockOnlineBalanceReader,
    KISMockProjectionError,
)
from app.brokerage.mock_order_reference_store import MockProviderOrderReference


class FakeClient:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.call_count = 0
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []
        self.continuations: list[str | None] = []

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
        continuation: str | None = None,
    ) -> dict[str, Any]:
        assert json_body is None
        self.calls.append((method, path, tr_id, dict(params or {})))
        self.continuations.append(continuation)
        payload = self.payloads[min(self.call_count, len(self.payloads) - 1)]
        self.call_count += 1
        return payload


def test_online_server_defaults_closed_before_any_runtime_client_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", raising=False)
    monkeypatch.setenv("KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP", "0")
    monkeypatch.setenv("KIS_BROKERAGE_PHYSICAL_CAP", "1")
    monkeypatch.setenv("BROKERAGE_GRPC_SHARED_SECRET", "s" * 32)
    monkeypatch.setenv(
        "KIS_MOCK_ORDER_REFERENCE_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    with pytest.raises(ValueError, match="gate is closed"):
        BrokerageGrpcServerSettings.from_env()


def test_online_server_requires_one_valid_bound_opaque_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "true")
    monkeypatch.setenv("KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP", "0")
    monkeypatch.setenv("KIS_BROKERAGE_PHYSICAL_CAP", "1")
    monkeypatch.setenv("BROKERAGE_GRPC_SHARED_SECRET", "s" * 32)
    monkeypatch.setenv(
        "KIS_MOCK_ORDER_REFERENCE_KEY",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )

    monkeypatch.delenv("KIS_MOCK_BOUND_ACCOUNT_ID", raising=False)
    with pytest.raises(ValueError, match="BOUND_ACCOUNT_ID"):
        BrokerageGrpcServerSettings.from_env()

    monkeypatch.setenv("KIS_MOCK_BOUND_ACCOUNT_ID", "acct_invalid")
    with pytest.raises(ValueError, match="BOUND_ACCOUNT_ID"):
        BrokerageGrpcServerSettings.from_env()

    account_id = "acct_" + "a" * 32
    monkeypatch.setenv("KIS_MOCK_BOUND_ACCOUNT_ID", account_id)
    assert BrokerageGrpcServerSettings.from_env().bound_account_id == account_id


def test_full_server_rejects_global_account_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    monkeypatch.setenv("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "true")
    monkeypatch.setenv("KIS_BROKERAGE_TOKEN_P_PHYSICAL_CAP", "1")
    monkeypatch.setenv("KIS_BROKERAGE_PHYSICAL_CAP", "1")
    monkeypatch.setenv("BROKERAGE_GRPC_SHARED_SECRET", "s" * 32)
    monkeypatch.setenv(
        "KIS_MOCK_ORDER_REFERENCE_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    )
    monkeypatch.setenv("KIS_MOCK_BOUND_ACCOUNT_ID", "acct_" + "a" * 32)
    with pytest.raises(ValueError, match="cannot use KIS_MOCK_BOUND_ACCOUNT_ID"):
        BrokerageGrpcServerSettings.from_env()
    monkeypatch.delenv("KIS_MOCK_BOUND_ACCOUNT_ID")
    assert BrokerageGrpcServerSettings.from_env().product_mode == "FULL"


def test_brokerage_physical_budget_fails_before_exceeding_exact_packet_cap() -> None:
    budget = KISBrokerageCallBudget(token_p_cap=1, brokerage_cap=2)

    budget.reserve_token_p()
    budget.reserve_brokerage()
    budget.reserve_brokerage()

    with pytest.raises(KISBrokerageCallBudgetExceeded, match="tokenP"):
        budget.reserve_token_p()
    with pytest.raises(KISBrokerageCallBudgetExceeded, match="brokerage"):
        budget.reserve_brokerage()
    assert budget.counts == {"tokenP": 1, "brokerage": 2}


def test_online_balance_probe_parses_source_without_fabricating_risk_fields() -> None:
    balance_client = FakeClient(
        {
            "rt_cd": "0",
            "output1": [
                {
                    "pdno": "005930",
                    "hldg_qty": "2",
                    "evlu_amt": "140,000",
                    "prdt_name": "provider-free-text",
                }
            ],
            "output2": [
                {
                    "dnca_tot_amt": "1,000,000",
                    "prvs_rcdl_excc_amt": "1,000,000",
                    "tot_evlu_amt": "1,140,000",
                }
            ],
        }
    )
    balance_reader = KISMockOnlineBalanceReader(balance_client)  # type: ignore[arg-type]
    account_id = "acct_" + "a" * 32

    with pytest.raises(KISMockProjectionError) as captured:
        balance_reader.balance(account_id)

    assert captured.value.reason_code == "BALANCE_RISK_FIELDS_UNAVAILABLE"
    assert balance_client.calls == []

    source = balance_reader.probe_balance_source(account_id)
    assert source.account_id == account_id
    assert source.cash_krw == 1_000_000
    assert source.portfolio_equity_krw == 1_140_000
    assert source.positions == (("005930", 2, 140_000),)
    assert source.positions_complete is True
    assert "provider-free-text" not in repr(source)
    balance_reader.verify_connection(account_id)
    assert len(balance_client.calls) == 2

    market_value_only = replace(
        source,
        portfolio_equity_krw=source.portfolio_equity_krw + 10_000,
        positions=(("005930", 2, 150_000),),
    )
    cash_changed = replace(source, cash_krw=source.cash_krw - 1)
    quantity_changed = replace(source, positions=(("005930", 1, 70_000),))
    assert market_value_only.reconciliation_digest() == source.reconciliation_digest()
    assert cash_changed.reconciliation_digest() != source.reconciliation_digest()
    assert quantity_changed.reconciliation_digest() != source.reconciliation_digest()


def test_balance_probe_follows_continuation_pages_before_calling_holdings_complete() -> None:
    pages = [
        {
            "rt_cd": "0",
            "_tr_cont": "M",
            "ctx_area_fk100": "fk-2",
            "ctx_area_nk100": "nk-2",
            "output1": [{"pdno": "005930", "hldg_qty": "2", "evlu_amt": "140,000"}],
            "output2": [{"prvs_rcdl_excc_amt": "1,000,000", "tot_evlu_amt": "1,280,000"}],
        },
        {
            "rt_cd": "0",
            "_tr_cont": "E",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [{"pdno": "000660", "hldg_qty": "1", "evlu_amt": "140,000"}],
            "output2": [],
        },
    ]
    client = FakeClient(pages)
    source = KISMockOnlineBalanceReader(client).probe_balance_source("acct_" + "a" * 32)  # type: ignore[arg-type]

    assert source.positions_complete is True
    assert source.positions == (("000660", 1, 140_000), ("005930", 2, 140_000))
    assert len(client.calls) == 2
    assert client.continuations == [None, "N"]
    assert client.calls[1][3]["CTX_AREA_FK100"] == "fk-2"


def test_online_buyable_parser_returns_only_sanitized_projection() -> None:
    buyable_client = FakeClient(
        {
            "rt_cd": "0",
            "output": {
                "ord_psbl_cash": "1,000,000",
                "nrcvb_buy_qty": "14",
                "nrcvb_buy_amt": "980,000",
                # 최대매수 필드는 미수를 포함할 수 있으므로 projection 근거로 쓰지 않는다.
                "max_buy_qty": "99",
                "max_buy_amt": "9,900,000",
            },
        }
    )
    buyable_reader = KISMockOnlineBalanceReader(buyable_client)  # type: ignore[arg-type]

    buyable = buyable_reader.buyable(
        "acct_" + "a" * 32,
        "005930",
        70_000,
        order_division="07",
    )

    assert buyable is not None
    assert (buyable.buyable_quantity, buyable.buyable_amount_krw) == (14, 980_000)
    assert buyable_client.calls[0][3]["ORD_DVSN"] == "07"


def test_execution_reader_enforces_quantity_invariant_and_hashes_raw_reference() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [
                {
                    "odno": raw_order_no,
                    "pdno": "005930",
                    "tot_ccld_qty": "1",
                    "rmn_qty": "1",
                    "avg_prvs": "70,000",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                    "ord_dt": "20260727",
                    "ord_tmd": "090001",
                }
            ],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]

    snapshot = reader.read(
        reference=MockProviderOrderReference(
            provider_order_no=raw_order_no,
            provider_org_no="synthetic-provider-org",
            order_division="00",
            quantity=2,
        ),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert snapshot.cumulative_quantity == 1
    assert snapshot.leaves_quantity == 1
    assert len(snapshot.provider_exec_ref_hash) == 64
    assert raw_order_no not in repr(snapshot)
    assert client.calls == [
        (
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "VTTC0081R",
            {
                "INQR_STRT_DT": "20260727",
                "INQR_END_DT": "20260727",
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "synthetic-provider-org",
                "ODNO": raw_order_no,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": "KRX",
            },
        )
    ]


def test_execution_optional_reader_returns_partial_snapshot_with_one_provider_page() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [
                {
                    "odno": raw_order_no,
                    "pdno": "005930",
                    "tot_ccld_qty": "2",
                    "rmn_qty": "3",
                    "avg_prvs": "70,100",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                    "ord_dt": "20260727",
                    "ord_tmd": "090001",
                }
            ],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    snapshot = reader.read_optional(
        reference=MockProviderOrderReference(
            provider_order_no=raw_order_no,
            provider_org_no="synthetic-provider-org",
            order_division="00",
            quantity=5,
        ),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert snapshot is not None
    assert (snapshot.cumulative_quantity, snapshot.leaves_quantity) == (2, 3)
    assert snapshot.average_fill_price_krw == 70_100
    assert len(client.calls) == 1


def test_execution_recovery_requires_one_exact_filled_buy() -> None:
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [
                {
                    "odno": "recovered-provider-order",
                    "pdno": "005930",
                    "tot_ccld_qty": "2",
                    "rmn_qty": "0",
                    "avg_prvs": "70,100",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                    "ord_dt": "20260727",
                    "ord_tmd": "090001",
                }
            ],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]

    snapshot = reader.recover_unique_filled_buy(
        symbol="005930",
        quantity=2,
        average_fill_price_krw=70_100,
        session_date=date(2026, 7, 27),
    )

    assert snapshot is not None
    assert (snapshot.symbol, snapshot.cumulative_quantity, snapshot.leaves_quantity) == (
        "005930",
        2,
        0,
    )
    assert client.calls[0][3]["SLL_BUY_DVSN_CD"] == "02"
    assert client.calls[0][3]["PDNO"] == "005930"
    assert client.calls[0][3]["ODNO"] == ""
    assert snapshot.average_fill_price_krw == 70_100


def test_execution_reader_uses_reference_exchange_division() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [{"odno": raw_order_no}],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]

    source = reader.probe_execution_source(
        reference=MockProviderOrderReference(
            provider_order_no=raw_order_no,
            provider_org_no="synthetic-provider-org",
            order_division="00",
            exchange_division="NXT",
            quantity=1,
        ),
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert source.matched is True
    assert client.calls[0][3]["EXCG_ID_DVSN_CD"] == "NXT"


def test_execution_probe_allows_empty_cancelled_order_page_without_publish_snapshot() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    reference = MockProviderOrderReference(
        provider_order_no=raw_order_no,
        provider_org_no="synthetic-provider-org",
        order_division="00",
        quantity=1,
    )

    source = reader.probe_execution_source(
        reference=reference,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert source.rows_seen == 0
    assert source.matched is False
    assert source.provider_exec_ref_hash is None
    with pytest.raises(ValueError, match="incomplete"):
        reader.read(
            reference=reference,
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )


def test_final_open_order_reconciliation_uses_exact_order_and_unfilled_filter() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    reference = MockProviderOrderReference(
        provider_order_no=raw_order_no,
        provider_org_no="synthetic-provider-org",
        order_division="00",
        quantity=1,
    )

    receipt = reader.require_no_open_order(
        reference=reference,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert receipt.rows_seen == 0
    assert len(receipt.provider_exec_ref_hash) == 64
    assert raw_order_no not in repr(receipt)
    assert client.calls[0][3]["CCLD_DVSN"] == "02"
    assert client.calls[0][3]["ODNO"] == raw_order_no


@pytest.mark.parametrize(
    "payload",
    [
        {
            "rt_cd": "0",
            "ctx_area_fk100": "next",
            "ctx_area_nk100": "",
            "output1": [],
        },
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [{"odno": "synthetic-provider-order"}],
        },
    ],
)
def test_final_open_order_reconciliation_rejects_continuation_or_residual_order(
    payload: dict[str, Any],
) -> None:
    reader = KISMockExecutionReader(FakeClient(payload))  # type: ignore[arg-type]

    with pytest.raises(KISMockProjectionError) as captured:
        reader.require_no_open_order(
            reference=MockProviderOrderReference(
                provider_order_no="synthetic-provider-order",
                provider_org_no="synthetic-provider-org",
                order_division="00",
                quantity=1,
            ),
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )

    assert captured.value.reason_code == "OPEN_ORDER_RECONCILIATION_FAILED"


def test_execution_probe_allows_empty_provider_sentinel_without_publish_snapshot() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": "",
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    reference = MockProviderOrderReference(
        provider_order_no=raw_order_no,
        provider_org_no="synthetic-provider-org",
        order_division="00",
        quantity=1,
    )

    source = reader.probe_execution_source(
        reference=reference,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert source.rows_seen == 0
    assert source.matched is False
    assert source.provider_exec_ref_hash is None
    with pytest.raises(ValueError, match="incomplete"):
        reader.read(
            reference=reference,
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )


def test_execution_probe_allows_sparse_matched_row_without_publish_snapshot() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [{"odno": raw_order_no}],
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    reference = MockProviderOrderReference(
        provider_order_no=raw_order_no,
        provider_org_no="synthetic-provider-org",
        order_division="00",
        quantity=1,
    )

    source = reader.probe_execution_source(
        reference=reference,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert source.rows_seen == 1
    assert source.matched is True
    assert len(source.provider_exec_ref_hash or "") == 64
    assert raw_order_no not in repr(source)
    with pytest.raises(ValueError, match="symbol"):
        reader.read(
            reference=reference,
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )


def test_execution_probe_allows_blank_cursor_and_singleton_row_shape() -> None:
    raw_order_no = "synthetic-provider-order"
    client = FakeClient(
        {
            "rt_cd": "0",
            "ctx_area_fk100": "   ",
            "ctx_area_nk100": "\t",
            "output1": {"ODNO": raw_order_no},
        }
    )
    reader = KISMockExecutionReader(client)  # type: ignore[arg-type]
    reference = MockProviderOrderReference(
        provider_order_no=raw_order_no,
        provider_org_no="synthetic-provider-org",
        order_division="00",
        quantity=1,
    )

    source = reader.probe_execution_source(
        reference=reference,
        start=date(2026, 7, 27),
        end=date(2026, 7, 27),
        recent=True,
    )

    assert source.rows_seen == 1
    assert source.matched is True
    assert len(source.provider_exec_ref_hash or "") == 64
    assert raw_order_no not in repr(source)
    with pytest.raises(ValueError, match="incomplete"):
        reader.read(
            reference=reference,
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )


def test_balance_probe_marks_page_limit_exhaustion_incomplete() -> None:
    pages = [
        {
            "rt_cd": "0",
            "_tr_cont": "M",
            "ctx_area_fk100": f"fk-{index + 1}",
            "ctx_area_nk100": f"nk-{index + 1}",
            "output1": [{"pdno": f"{index + 1:06d}", "hldg_qty": "1", "evlu_amt": "70,000"}],
            "output2": [{"prvs_rcdl_excc_amt": "0", "tot_evlu_amt": "0"}],
        }
        for index in range(4)
    ]
    balance_reader = KISMockOnlineBalanceReader(
        FakeClient(pages)  # type: ignore[arg-type]
    )
    source = balance_reader.probe_balance_source("acct_" + "a" * 32)
    assert len(source.positions) == 4
    assert source.positions_complete is False


def test_account_order_scan_paginates_and_detects_pending_external_order() -> None:
    pages = [
        {
            "rt_cd": "0",
            "ctx_area_fk100": "orders-2",
            "ctx_area_nk100": "orders-2",
            "_tr_cont": "M",
            "output1": [
                {
                    "odno": "raw-order-1",
                    "pdno": "086790",
                    "sll_buy_dvsn_cd": "02",
                    "ord_qty": "1",
                    "tot_ccld_qty": "0",
                    "rmn_qty": "1",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                }
            ],
        },
        {
            "rt_cd": "0",
            "_tr_cont": "E",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [
                {
                    "odno": "raw-order-2",
                    "pdno": "005930",
                    "sll_buy_dvsn_cd": "01",
                    "ord_qty": "1",
                    "tot_ccld_qty": "1",
                    "rmn_qty": "0",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                }
            ],
        },
    ]
    client = FakeClient(pages)
    orders = KISMockExecutionReader(client).read_session_orders(session_date=date(2026, 9, 29))  # type: ignore[arg-type]

    assert len(orders) == 2
    assert sum(order.unresolved for order in orders) == 1
    assert all("raw-order" not in repr(order) for order in orders)
    assert client.continuations == [None, "N"]


def test_lost_submit_response_matches_only_a_new_exact_kis_order() -> None:
    row = {
        "odno": "new-kis-order",
        "ord_gno_brno": "branch",
        "ord_dt": "20260930",
        "ord_tmd": "093055",
        "pdno": "105560",
        "sll_buy_dvsn_cd": "02",
        "ord_dvsn_cd": "00",
        "ord_qty": "30",
        "ord_unpr": "172000",
    }
    reader = KISMockExecutionReader(FakeClient({"rt_cd": "0", "output1": [row]}))  # type: ignore[arg-type]
    request = dict(
        session_date=date(2026, 9, 30),
        submitted_at=datetime(2026, 9, 30, 9, 30, 54, tzinfo=ZoneInfo("Asia/Seoul")),
        symbol="105560",
        side="BUY",
        quantity=30,
        order_division="00",
        limit_price_krw=172_000,
        exchange_division="KRX",
    )

    recovered = reader.recover_new_order_reference(
        before_order_ref_hashes=frozenset(),
        **request,  # type: ignore[arg-type]
    )
    assert recovered is not None
    assert recovered.provider_order_no == "new-kis-order"
    assert recovered.provider_org_no == "branch"
    assert (
        reader.recover_new_order_reference(  # type: ignore[arg-type]
            before_order_ref_hashes=frozenset(
                {hashlib.sha256(b"kis-mock-order-receipt/v1\0new-kis-order").hexdigest()}
            ),
            **request,
        )
        is None
    )


def test_account_order_scan_rejects_unaccounted_quantity_and_duplicate_pages() -> None:
    base_row = {
        "odno": "raw-order-1",
        "pdno": "086790",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "2",
        "tot_ccld_qty": "1",
        "rmn_qty": "0",
        "cncl_cfrm_qty": "0",
        "rjct_qty": "0",
        "cncl_yn": "N",
    }
    with pytest.raises(ValueError, match="quantities are invalid"):
        KISMockExecutionReader(
            FakeClient({"rt_cd": "0", "output1": [base_row]})
        ).read_session_orders(  # type: ignore[arg-type]
            session_date=date(2026, 9, 29)
        )

    first = {
        "rt_cd": "0",
        "_tr_cont": "M",
        "ctx_area_fk100": "next",
        "ctx_area_nk100": "next",
        "output1": [{**base_row, "tot_ccld_qty": "0", "rmn_qty": "2"}],
    }
    duplicate = {
        "rt_cd": "0",
        "_tr_cont": "E",
        "ctx_area_fk100": "",
        "ctx_area_nk100": "",
        "output1": [{**base_row, "tot_ccld_qty": "0", "rmn_qty": "2"}],
    }
    with pytest.raises(ValueError, match="duplicate"):
        KISMockExecutionReader(FakeClient([first, duplicate])).read_session_orders(  # type: ignore[arg-type]
            session_date=date(2026, 9, 29)
        )


def test_account_order_scan_fails_closed_when_page_limit_still_has_more() -> None:
    pages = [
        {
            "rt_cd": "0",
            "_tr_cont": "M",
            "ctx_area_fk100": f"fk-{index + 1}",
            "ctx_area_nk100": f"nk-{index + 1}",
            "output1": [
                {
                    "odno": f"order-{index + 1}",
                    "pdno": "086790",
                    "sll_buy_dvsn_cd": "02",
                    "ord_qty": "1",
                    "tot_ccld_qty": "1",
                    "rmn_qty": "0",
                    "cncl_cfrm_qty": "0",
                    "rjct_qty": "0",
                    "cncl_yn": "N",
                }
            ],
        }
        for index in range(4)
    ]
    client = FakeClient(pages)

    with pytest.raises(ValueError, match="page limit"):
        KISMockExecutionReader(client).read_session_orders(session_date=date(2026, 9, 29))  # type: ignore[arg-type]

    assert len(client.calls) == 4


def test_balance_reader_rejects_a_more_pages_header_without_cursors() -> None:
    reader = KISMockOnlineBalanceReader(
        FakeClient(
            {
                "rt_cd": "0",
                "_tr_cont": "M",
                "ctx_area_fk100": "",
                "ctx_area_nk100": "",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "evlu_amt": "70,000"}],
                "output2": [{"prvs_rcdl_excc_amt": "0", "tot_evlu_amt": "70,000"}],
            }
        )  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="no cursor"):
        reader.probe_balance_source("acct_" + "a" * 32)


def test_balance_probe_rejects_duplicate_symbols_across_pages() -> None:
    pages = [
        {
            "rt_cd": "0",
            "_tr_cont": "M",
            "ctx_area_fk100": "next",
            "ctx_area_nk100": "next",
            "output1": [{"pdno": "086790", "hldg_qty": "1", "evlu_amt": "17,460"}],
            "output2": [{"prvs_rcdl_excc_amt": "982,540", "tot_evlu_amt": "1,000,000"}],
        },
        {
            "rt_cd": "0",
            "_tr_cont": "E",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
            "output1": [{"pdno": "086790", "hldg_qty": "2", "evlu_amt": "34,920"}],
            "output2": [],
        },
    ]

    with pytest.raises(KISMockProjectionError, match="duplicate positions"):
        KISMockOnlineBalanceReader(FakeClient(pages)).probe_balance_source(  # type: ignore[arg-type]
            "acct_" + "a" * 32
        )


def test_execution_rejects_incomplete_or_oversized_mock_pages() -> None:
    raw_order_no = "synthetic-provider-order"
    execution_reader = KISMockExecutionReader(
        FakeClient(
            {
                "rt_cd": "0",
                "ctx_area_fk100": "",
                "ctx_area_nk100": "",
                "output1": [{"odno": raw_order_no}] * 16,
            }
        )  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="incomplete"):
        execution_reader.read(
            reference=MockProviderOrderReference(
                provider_order_no=raw_order_no,
                provider_org_no="synthetic-provider-org",
                order_division="00",
                quantity=1,
            ),
            start=date(2026, 7, 27),
            end=date(2026, 7, 27),
            recent=True,
        )
