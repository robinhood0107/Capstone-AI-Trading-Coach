from __future__ import annotations

from types import MappingProxyType
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Mapping


KrxMarket = Literal["KOSPI", "KOSDAQ"]
KrxServiceCategory = Literal["지수", "주식", "증권상품", "채권", "파생상품", "일반상품", "ESG"]
KrxServiceStatus = Literal["NOW", "NEXT", "LATER", "EXCLUDE"]

# 인증정보가 전달되는 origin과 path는 runtime 설정으로 바꾸지 않고 코드 리뷰 대상에 둔다.
KRX_OPEN_API_ORIGIN: Final = "https://data-dbg.krx.co.kr"
KRX_OPEN_API_FIRST_AVAILABLE_DATE: Final = date(2010, 1, 4)


@dataclass(frozen=True, slots=True)
class KrxEndpoint:
    """공식 KRX 일별매매정보 endpoint의 고정 요청·응답 계약이다."""

    name: str
    market: KrxMarket
    path: str
    request_parameter: str
    response_block: str


@dataclass(frozen=True, slots=True)
class KrxServicePlan:
    """공식 서비스 ID의 현재 Decision Platform 사용 경계를 실행 endpoint와 분리해 보존한다."""

    category: KrxServiceCategory
    api_id: str
    status: KrxServiceStatus


@dataclass(frozen=True, slots=True)
class KrxProductionEndpoint:
    """S5.6 one-shot에서만 활성인 exact KRX service/path 계약이다."""

    service: str
    path: str
    request_parameter: str
    response_block: str


KRX_SERVICE_PLAN: Final[tuple[KrxServicePlan, ...]] = (
    KrxServicePlan("지수", "krx_dd_trd", "LATER"),
    KrxServicePlan("지수", "kospi_dd_trd", "NEXT"),
    KrxServicePlan("지수", "kosdaq_dd_trd", "NEXT"),
    KrxServicePlan("지수", "bon_dd_trd", "LATER"),
    KrxServicePlan("지수", "drvprod_dd_trd", "LATER"),
    KrxServicePlan("주식", "stk_bydd_trd", "NOW"),
    KrxServicePlan("주식", "ksq_bydd_trd", "NOW"),
    KrxServicePlan("주식", "knx_bydd_trd", "EXCLUDE"),
    KrxServicePlan("주식", "sw_bydd_trd", "EXCLUDE"),
    KrxServicePlan("주식", "sr_bydd_trd", "EXCLUDE"),
    KrxServicePlan("주식", "stk_isu_base_info", "NEXT"),
    KrxServicePlan("주식", "ksq_isu_base_info", "NEXT"),
    KrxServicePlan("주식", "knx_isu_base_info", "EXCLUDE"),
    KrxServicePlan("증권상품", "etf_bydd_trd", "NEXT"),
    KrxServicePlan("증권상품", "etn_bydd_trd", "NEXT"),
    KrxServicePlan("증권상품", "elw_bydd_trd", "LATER"),
    KrxServicePlan("채권", "kts_bydd_trd", "LATER"),
    KrxServicePlan("채권", "bnd_bydd_trd", "LATER"),
    KrxServicePlan("채권", "smb_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "fut_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "eqsfu_stk_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "eqkfu_ksq_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "opt_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "eqsop_bydd_trd", "LATER"),
    KrxServicePlan("파생상품", "eqkop_bydd_trd", "LATER"),
    KrxServicePlan("일반상품", "oil_bydd_trd", "EXCLUDE"),
    KrxServicePlan("일반상품", "gold_bydd_trd", "NEXT"),
    KrxServicePlan("일반상품", "ets_bydd_trd", "EXCLUDE"),
    KrxServicePlan("ESG", "esg_etp_info", "EXCLUDE"),
    KrxServicePlan("ESG", "sri_bond_info", "EXCLUDE"),
    KrxServicePlan("ESG", "esg_index_info", "EXCLUDE"),
)


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
ENABLED_UNIVERSE_ENDPOINTS_BY_SERVICE: Final[Mapping[str, KrxEndpoint]] = MappingProxyType(
    {
        "stk_bydd_trd": KOSPI_DAILY,
        "ksq_bydd_trd": KOSDAQ_DAILY,
    }
)


# 기존 S1.3 두 endpoint 집합은 byte/behavior stable하게 두고 S5.6 전용 allowlist를 분리한다.
S5_PRODUCTION_ENDPOINTS: Final[Mapping[str, KrxProductionEndpoint]] = MappingProxyType(
    {
        "stk_bydd_trd": KrxProductionEndpoint(
            "stk_bydd_trd", "/svc/apis/sto/stk_bydd_trd.json", "basDd", "OutBlock_1"
        ),
        "ksq_bydd_trd": KrxProductionEndpoint(
            "ksq_bydd_trd", "/svc/apis/sto/ksq_bydd_trd.json", "basDd", "OutBlock_1"
        ),
        "kospi_dd_trd": KrxProductionEndpoint(
            "kospi_dd_trd", "/svc/apis/idx/kospi_dd_trd.json", "basDd", "OutBlock_1"
        ),
        "kosdaq_dd_trd": KrxProductionEndpoint(
            "kosdaq_dd_trd", "/svc/apis/idx/kosdaq_dd_trd.json", "basDd", "OutBlock_1"
        ),
        "stk_isu_base_info": KrxProductionEndpoint(
            "stk_isu_base_info", "/svc/apis/sto/stk_isu_base_info.json", "basDd", "OutBlock_1"
        ),
        "ksq_isu_base_info": KrxProductionEndpoint(
            "ksq_isu_base_info", "/svc/apis/sto/ksq_isu_base_info.json", "basDd", "OutBlock_1"
        ),
        "etf_bydd_trd": KrxProductionEndpoint(
            "etf_bydd_trd", "/svc/apis/etp/etf_bydd_trd.json", "basDd", "OutBlock_1"
        ),
    }
)
