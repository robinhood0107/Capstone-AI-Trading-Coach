from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import grpc
import pytest

from app.brokerage.brokerage_rpc import BrokerageServicer, _connection_failure_leaf
from app.brokerage.kis_mock_online_client import KISMockBrokerageError, KISMockFailureReason
from app.brokerage.kis_mock_online_runtime import KISMockBalanceSourceProbe
from app.brokerage.kis_mock_order_gateway import KISMockOrderGateway
from app.data.kis._credential_transport import KISCredentialError
from app.generated import brokerage_pb2
from tests.brokerage.test_brokerage_rpc import FakeContext, FakeTransport, RpcAborted

_ACCOUNT = "acct_" + "3" * 32


class _Reader:
    def __init__(self, outcome: KISMockBalanceSourceProbe | Exception) -> None:
        self._outcome = outcome

    def balance(self, account_id: str) -> None:
        pytest.fail("connection check must not use the enriched balance path")

    def verify_connection(self, account_id: str) -> KISMockBalanceSourceProbe:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    def buyable(self, *_args: object) -> None:
        pytest.fail("connection check must not query buyable")


def _servicer(reader: _Reader) -> BrokerageServicer:
    class OwnerFactory:
        @contextmanager
        def open(
            self, *_args: object, **_kwargs: object
        ) -> Iterator[tuple[KISMockOrderGateway, _Reader]]:
            yield KISMockOrderGateway(FakeTransport()), reader

        def certify(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("connection check must not start certification")

    return BrokerageServicer(None, "s" * 32, owner_factory=OwnerFactory())  # type: ignore[arg-type]


def _verify(servicer: BrokerageServicer) -> brokerage_pb2.VerifyMockConnectionResponse:
    return servicer.VerifyMockConnection(
        brokerage_pb2.VerifyMockConnectionRequest(
            request_id="req-connection-proof",
            account_id=_ACCOUNT,
            credential=brokerage_pb2.BoundMockCredentialEnvelope(
                owner_user_id="usr_" + "b" * 32, account_id=_ACCOUNT, credential_state="STORED"
            ),
        ),
        FakeContext(),  # type: ignore[arg-type]
    )


def test_connection_check_returns_the_balance_it_actually_read() -> None:
    probe = KISMockBalanceSourceProbe(
        account_id=_ACCOUNT,
        cash_krw=94_533_738,
        portfolio_equity_krw=97_233_738,
        positions=(("055550", 45, 2_700_000),),
        positions_complete=True,
    )
    response = _verify(_servicer(_Reader(probe)))
    assert response.connected and response.account_id == _ACCOUNT
    assert response.cash_krw == 94_533_738
    assert [(p.symbol, p.quantity) for p in response.positions] == [("055550", 45)]
    assert response.positions_complete is True


@pytest.mark.parametrize(
    ("error", "leaf"),
    [
        (
            KISMockBrokerageError(KISMockFailureReason.CREDENTIAL_UNAVAILABLE),
            "MOCK_CONNECTION_APP_KEY_REJECTED",
        ),
        (KISCredentialError("KIS token issue failed"), "MOCK_CONNECTION_APP_KEY_REJECTED"),
        (
            KISMockBrokerageError(KISMockFailureReason.PROVIDER_REJECTED, provider_code="OPSQ2000"),
            "MOCK_CONNECTION_ACCOUNT_REJECTED",
        ),
        (
            KISMockBrokerageError(KISMockFailureReason.RATE_LIMIT_UNAVAILABLE),
            "MOCK_CONNECTION_RATE_LIMITED",
        ),
        (
            KISMockBrokerageError(KISMockFailureReason.TRANSPORT_UNAVAILABLE),
            "MOCK_CONNECTION_KIS_UNAVAILABLE",
        ),
    ],
)
def test_connection_failures_fold_into_fixed_user_facing_leaves(
    error: Exception, leaf: str
) -> None:
    assert _connection_failure_leaf(error) == leaf
    with pytest.raises(RpcAborted) as aborted:
        _verify(_servicer(_Reader(error)))
    assert aborted.value.code == grpc.StatusCode.FAILED_PRECONDITION
    assert aborted.value.detail == leaf
    # KIS 원문 코드는 host 로 나가지 않는다.
    assert "OPSQ" not in aborted.value.detail


def test_unknown_failures_stay_a_generic_outage() -> None:
    assert _connection_failure_leaf(RuntimeError("boom")) is None
    with pytest.raises(RpcAborted) as aborted:
        _verify(_servicer(_Reader(RuntimeError("boom"))))
    assert aborted.value.code == grpc.StatusCode.UNAVAILABLE
