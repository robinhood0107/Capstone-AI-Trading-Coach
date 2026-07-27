from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.brokerage.brokerage_grpc_server import BrokerageGrpcServerSettings
from app.brokerage.kis_mock_online_client import (
    KISBrokerageCallBudget,
    KISBrokerageCallBudgetExceeded,
)
from app.brokerage.kis_mock_online_runtime import (
    KISMockExecutionReader,
    KISMockOnlineBalanceReader,
)
from app.brokerage.mock_order_reference_store import MockProviderOrderReference


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        assert json_body is None
        self.calls.append((method, path, tr_id, dict(params or {})))
        return self.payload


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


def test_online_balance_and_buyable_parser_returns_only_sanitized_projection() -> None:
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
                    "tot_evlu_amt": "1,140,000",
                }
            ],
        }
    )
    balance_reader = KISMockOnlineBalanceReader(balance_client)  # type: ignore[arg-type]

    balance = balance_reader.balance("acct_" + "a" * 32)

    assert balance is not None
    assert balance.cash_krw == 1_000_000
    assert balance.portfolio_equity_krw == 1_140_000
    assert [(position.symbol, position.quantity) for position in balance.positions] == [
        ("005930", 2)
    ]
    assert "provider-free-text" not in repr(balance)

    buyable_client = FakeClient(
        {
            "rt_cd": "0",
            "output": {
                "ord_psbl_cash": "1,000,000",
                "max_buy_qty": "14",
                "max_buy_amt": "980,000",
            },
        }
    )
    buyable_reader = KISMockOnlineBalanceReader(buyable_client)  # type: ignore[arg-type]

    buyable = buyable_reader.buyable("acct_" + "a" * 32, "005930", 70_000)

    assert buyable is not None
    assert (buyable.buyable_quantity, buyable.buyable_amount_krw) == (14, 980_000)


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
                    "cnc_cfrm_qty": "0",
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


def test_balance_and_execution_reject_incomplete_or_oversized_mock_pages() -> None:
    balance_reader = KISMockOnlineBalanceReader(
        FakeClient(
            {
                "rt_cd": "0",
                "ctx_area_fk100": "next",
                "ctx_area_nk100": "next",
                "output1": [],
                "output2": [{"dnca_tot_amt": "0", "tot_evlu_amt": "0"}],
            }
        )  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="another bounded page") as captured:
        balance_reader.balance("acct_" + "a" * 32)
    assert captured.value.reason_code == "BALANCE_PAGINATION_REQUIRED"  # type: ignore[attr-defined]

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
