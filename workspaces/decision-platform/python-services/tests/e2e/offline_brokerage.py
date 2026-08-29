"""KIS transport만 대체한 오프라인 brokerage gRPC 서버. 컨테이너 안에서만 돈다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

왜 필요한가. Spring의 `GrpcBrokerageAdapter`는 `app.brokerage.grpc.enabled=true`일 때만 존재하고,
없으면 KIS_MOCK 경로가 항상 `BrokerageUnavailableException`으로 닫힌다. 그런데 그 gRPC를 제공하는
production 서버(`app.brokerage.brokerage_grpc_server`)는 실제 KIS 모의투자 엔드포인트를 부른다.
거래시간 밖이고 provider 호출을 만들지 않기로 했으므로 그 서버를 켤 수 없다.

그래서 **같은 포트에 같은 계약으로 응답하는 오프라인 서버**를 테스트가 세운다. 대체하는 것은
`MockOrderTransport` 하나뿐이다. `BrokerageServicer`(인증·검증·계좌 결속), `KISMockOrderGateway`
(주문구분·거래소구분·TR ID 매핑과 응답 파싱)는 production 코드를 그대로 쓴다. 즉 이 서버가
가짜로 만드는 것은 "KIS가 무엇을 돌려주는가"뿐이고 그 위의 모든 경계는 실제다.

provider 물리 호출은 0이다. 네트워크를 열지 않는다.

실행(컨테이너 안):
  P1_FULL_PIPELINE_E2E=1 python -m e2e.offline_brokerage --cash 100000000
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from concurrent import futures
from datetime import UTC, datetime
from typing import Any, Final

import grpc

from app.brokerage.brokerage_rpc import BrokerageServicer
from app.brokerage.kis_mock_order_gateway import KISMockOrderGateway
from app.generated import brokerage_pb2, brokerage_pb2_grpc

_OPT_IN: Final = "P1_FULL_PIPELINE_E2E"
_ORDER_CASH_PATH: Final = "/uapi/domestic-stock/v1/trading/order-cash"
_PROVIDER_ORG_NO: Final = "00950"


class OfflineBrokerageError(RuntimeError):
    """오프라인 brokerage 서버가 계약을 만족하지 못했다."""


class OfflineKisTransport:
    """KIS 응답 모양만 흉내낸다. 네트워크를 열지 않으며 물리 호출은 0이다."""

    def __init__(self) -> None:
        self.calls = 0

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del params
        self.calls += 1
        if method != "POST" or path != _ORDER_CASH_PATH or json_body is None:
            raise OfflineBrokerageError(f"unexpected KIS request: {method} {path}")
        # provider 주문번호는 결정적이어야 재실행이 같은 결과를 낸다.
        ordinal = f"{self.calls:08d}"
        return {
            "rt_cd": "0",
            "msg_cd": "APBK0013",
            "msg1": "OFFLINE MOCK ACCEPTED",
            "output": {
                "ODNO": ordinal,
                "KRX_FWDG_ORD_ORGNO": _PROVIDER_ORG_NO,
                "ORD_TMD": datetime.now(UTC).strftime("%H%M%S"),
            },
        }


class CashOnlyBalanceReader:
    """KIS 잔고 경계. 매수 가능 수량은 현금으로만 판단한다.

    체결 원장의 권위는 `kis_fakes.AccountLedger`(드라이버 쪽)에 있다. 여기서는 bridge의 BUYABLE
    질의가 주문을 막지 않을 만큼의 현금만 보고한다. 두 곳이 같은 값을 유지할 필요가 없는 이유는
    계좌 계보(`permits_fill`)가 드라이버의 잔고만 보기 때문이다.

    `source_version`은 provider 표식이 아니라 **wire 계약 버전**이다. `GrpcBrokerageAdapter`가
    `kis-mock-balance-v1`/`kis-mock-buyable-v1`을 정확히 요구하므로 그대로 돌려준다. 증거 표식은
    이 값이 아니라 `portfolio_balance_observations.source_version`이고 그쪽은 이 테스트가 만들지
    않는다.
    """

    def __init__(self, *, account_id: str, cash_krw: int) -> None:
        self._account_id = account_id
        self._cash_krw = cash_krw

    def _observed_at(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse | None:
        if account_id != self._account_id:
            return None
        return brokerage_pb2.GetMockBalanceResponse(
            account_id=account_id,
            cash_krw=self._cash_krw,
            portfolio_equity_krw=self._cash_krw,
            margin_requirement_krw=0,
            positions=[],
            observed_at=self._observed_at(),
            source_version="kis-mock-balance-v1",
        )

    def buyable(
        self, account_id: str, symbol: str, estimated_price_krw: int
    ) -> brokerage_pb2.GetMockBuyableResponse | None:
        if account_id != self._account_id or estimated_price_krw <= 0:
            return None
        return brokerage_pb2.GetMockBuyableResponse(
            account_id=account_id,
            symbol=symbol,
            estimated_price_krw=estimated_price_krw,
            buyable_quantity=self._cash_krw // estimated_price_krw,
            buyable_amount_krw=self._cash_krw,
            cash_krw=self._cash_krw,
            observed_at=self._observed_at(),
            source_version="kis-mock-buyable-v1",
        )


def _require(name: str, pattern: str) -> str:
    value = os.environ.get(name, "").strip()
    if re.fullmatch(pattern, value) is None:
        raise OfflineBrokerageError(f"{name} is unavailable or invalid inside the container")
    return value


def serve(cash_krw: int, account_id: str) -> None:
    if os.environ.get(_OPT_IN) != "1":
        raise OfflineBrokerageError(f"{_OPT_IN}=1 must be set explicitly")
    bind_address = os.environ.get("PYTHON_BROKERAGE_GRPC_BIND_ADDRESS", "127.0.0.1:50052").strip()
    if not bind_address.startswith(("127.0.0.1:", "[::1]:")):
        raise OfflineBrokerageError("the offline brokerage must bind to numeric loopback")
    shared_secret = _require("BROKERAGE_GRPC_SHARED_SECRET", r"[A-Za-z0-9._~:-]{32,256}")
    # 결속 계좌는 테스트 인자다. 실제 KIS 설정(`KIS_MOCK_BOUND_ACCOUNT_ID`)과 automation control의
    # 계좌가 다를 수 있고, 이 서버가 대신하는 것은 그 계좌를 가진 KIS 쪽이기 때문이다.
    bound_account_id = account_id.strip()
    if re.fullmatch(r"acct_[0-9a-f]{32}", bound_account_id) is None:
        raise OfflineBrokerageError("the offline brokerage account id is invalid")

    transport = OfflineKisTransport()
    servicer = BrokerageServicer(
        KISMockOrderGateway(transport, mode="mock", reference_store=None),
        shared_secret,
        bound_account_id=bound_account_id,
        balance_reader=CashOnlyBalanceReader(account_id=bound_account_id, cash_krw=cash_krw),
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    brokerage_pb2_grpc.add_BrokerageServiceServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    server.add_insecure_port(bind_address)
    server.start()
    print(f"P1_E2E_OFFLINE_BROKERAGE listening on {bind_address}", flush=True)

    def stop(_signal: int, _frame: object) -> None:
        server.stop(grace=0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.wait_for_termination()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cash", type=int, default=100_000_000)
    parser.add_argument("--account", required=True)
    args = parser.parse_args(argv[1:])
    serve(args.cash, args.account)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
