from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


KrxMarket = Literal["KOSPI", "KOSDAQ"]

# 인증정보가 전달되는 origin과 path는 runtime 설정으로 바꾸지 않고 코드 리뷰 대상에 둔다.
KRX_OPEN_API_ORIGIN: Final = "https://data-dbg.krx.co.kr"


@dataclass(frozen=True, slots=True)
class KrxEndpoint:
    """공식 KRX 일별매매정보 endpoint의 고정 요청·응답 계약이다."""

    name: str
    market: KrxMarket
    path: str
    request_parameter: str
    response_block: str


KOSPI_DAILY: Final = KrxEndpoint(
    name="kospi-daily-trading",
    market="KOSPI",
    path="/svc/apis/sto/stk_bydd_trd.json",
    request_parameter="basDd",
    response_block="OutBlock_1",
)
KOSDAQ_DAILY: Final = KrxEndpoint(
    name="kosdaq-daily-trading",
    market="KOSDAQ",
    path="/svc/apis/sto/ksq_bydd_trd.json",
    request_parameter="basDd",
    response_block="OutBlock_1",
)

# S1.3 universe 자동화는 국내 주식 두 시장만 사용하고 나머지 29개 서비스는 활성화하지 않는다.
ENABLED_UNIVERSE_ENDPOINTS: Final = (KOSPI_DAILY, KOSDAQ_DAILY)
