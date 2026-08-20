"""S5.6 provider budgets, universe identity와 sensitivity gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Mapping, Sequence

import numpy as np

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.pit_calendar import RAW_SESSION_COUNT
from app.lightgbm.metrics import (
    multiclass_brier,
    natural_log_loss,
    tie_aware_argmax,
    top_label_ece,
)


# 승인된 packet window가 덮는 월 수다. 상한이 이 차원에서 나오도록 이름을 붙인다.
APPROVED_MONTHLY_SCHEDULE_COUNT = 51
# 51개월 universe의 실측 고유 종목 수다. 180은 겹침 가정이었고 실제 증거는 270이었다.
APPROVED_HORIZON_UNION_SIZE = 270
# 이전 승인 차원. 이미 소비한 packet을 read-only로 검증할 때만 쓰며 삭제하지 않는다.
SUPERSEDED_HORIZON_UNION_SIZES: tuple[int, ...] = (180,)
APPROVED_KIS_TOKEN_MAX = 1
APPROVED_ECOS_MAX_GET = 24
# 일별 4개 service × raw session + 월별 3개 service × 월 수. 유도식이 상한의 유일한 정의다.
APPROVED_KRX_MAX_GET = (
    RAW_SESSION_COUNT * 4 + APPROVED_MONTHLY_SCHEDULE_COUNT * 3
)
# union 종목마다 raw session을 100행 page로 덮는 수다. 리터럴로 두면 union 변경과 어긋난다.
APPROVED_KIS_MAX_GET = APPROVED_HORIZON_UNION_SIZE * math.ceil(RAW_SESSION_COUNT / 100)
APPROVED_TOTAL_MAX_PHYSICAL_CALLS = (
    APPROVED_KRX_MAX_GET
    + APPROVED_KIS_MAX_GET
    + APPROVED_KIS_TOKEN_MAX
    + APPROVED_ECOS_MAX_GET
)
MAX_KRX_SUPERSEDED_ALLOWANCE = 8
# KIS 소비도 같은 증거 결속 규칙을 따르지만 예산이 다르므로 provider별로 셋을 나눈다.
MAX_KIS_SUPERSEDED_ALLOWANCE = 8
MAX_KIS_TOKEN_SUPERSEDED_ALLOWANCE = 8


KRX_OPERATIONS = (
    "stk_bydd_trd",
    "ksq_bydd_trd",
    "kospi_dd_trd",
    "kosdaq_dd_trd",
    "stk_isu_base_info",
    "ksq_isu_base_info",
    "etf_bydd_trd",
)
KIS_OPERATION = "FHKST03010100"
ECOS_OPERATIONS = ("722Y001/0101000/D", "731Y001/0000001/D")


class SecurityClassification(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    PREFERRED = "PREFERRED"
    REIT = "REIT"
    ETF = "ETF"
    ETN = "ETN"
    SPAC = "SPAC"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class BootstrapBudget:
    """Provider handoff 전에 계산하는 one-shot cumulative physical budget."""

    krx_get: int
    kis_get: int
    kis_token: int
    ecos_get: int
    retry: int = 0
    cost: int = 0
    krx_superseded_allowance: int = 0
    kis_superseded_allowance: int = 0
    kis_token_superseded_allowance: int = 0

    def __post_init__(self) -> None:
        values = (
            self.krx_get,
            self.kis_get,
            self.kis_token,
            self.ecos_get,
            self.retry,
            self.cost,
            self.krx_superseded_allowance,
            self.kis_superseded_allowance,
            self.kis_token_superseded_allowance,
        )
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise LightGbmContractError("provider budget must use non-negative integers")
        # Allowance는 recovery receipt가 증명한 superseded consumed call 수만 좁게 복원한다.
        if (
            self.krx_superseded_allowance > MAX_KRX_SUPERSEDED_ALLOWANCE
            or self.kis_superseded_allowance > MAX_KIS_SUPERSEDED_ALLOWANCE
            or self.kis_token_superseded_allowance
            > MAX_KIS_TOKEN_SUPERSEDED_ALLOWANCE
        ):
            raise LightGbmContractError("S5.6 superseded allowance exceeds approved bound")
        if (
            self.krx_get > APPROVED_KRX_MAX_GET + self.krx_superseded_allowance
            or self.kis_get > APPROVED_KIS_MAX_GET + self.kis_superseded_allowance
            or self.kis_token
            > APPROVED_KIS_TOKEN_MAX + self.kis_token_superseded_allowance
            or self.ecos_get > APPROVED_ECOS_MAX_GET
            or self.total
            > APPROVED_TOTAL_MAX_PHYSICAL_CALLS + self.superseded_allowance_total
            or self.retry != 0
            or self.cost != 0
        ):
            raise LightGbmContractError("S5.6 approved provider budget exceeded")

    @property
    def superseded_allowance_total(self) -> int:
        return (
            self.krx_superseded_allowance
            + self.kis_superseded_allowance
            + self.kis_token_superseded_allowance
        )

    @property
    def total(self) -> int:
        return self.krx_get + self.kis_get + self.kis_token + self.ecos_get


def author_bootstrap_budget(
    *,
    monthly_schedule_count: int,
    union_size: int,
    raw_session_count: int = RAW_SESSION_COUNT,
) -> BootstrapBudget:
    """Exact plan dimensions에서 physical upper bound를 author한다.

    Fresh 경로는 allowance를 받지 않는다. 새 approved root가 과거 실패분을 근거 없이 더 쓰지
    못하도록 구조적으로 막는다.
    """

    return _author_bootstrap_budget(
        monthly_schedule_count=monthly_schedule_count,
        union_size=union_size,
        raw_session_count=raw_session_count,
        superseded_allowance=0,
        kis_superseded_allowance=0,
        kis_token_superseded_allowance=0,
    )


def author_recovery_bootstrap_budget(
    *,
    monthly_schedule_count: int,
    union_size: int,
    raw_session_count: int = RAW_SESSION_COUNT,
    superseded_allowance: int,
    kis_superseded_allowance: int = 0,
    kis_token_superseded_allowance: int = 0,
) -> BootstrapBudget:
    """Recovery만 증명된 superseded consumed call 수만큼 provider별 상한을 복원한다.

    Allowance는 recovery receipt가 재계산으로 증명한 값이어야 하며, packet bytes와 실행 권위에서
    다시 교차검증된다. 이 함수만으로는 provider 호출을 열지 않는다.
    """

    for value, bound in (
        (superseded_allowance, MAX_KRX_SUPERSEDED_ALLOWANCE),
        (kis_superseded_allowance, MAX_KIS_SUPERSEDED_ALLOWANCE),
        (kis_token_superseded_allowance, MAX_KIS_TOKEN_SUPERSEDED_ALLOWANCE),
    ):
        if isinstance(value, bool) or not 0 <= value <= bound:
            raise LightGbmContractError("S5.6 superseded allowance is invalid")
    return _author_bootstrap_budget(
        monthly_schedule_count=monthly_schedule_count,
        union_size=union_size,
        raw_session_count=raw_session_count,
        superseded_allowance=superseded_allowance,
        kis_superseded_allowance=kis_superseded_allowance,
        kis_token_superseded_allowance=kis_token_superseded_allowance,
    )


def _author_bootstrap_budget(
    *,
    monthly_schedule_count: int,
    union_size: int,
    raw_session_count: int,
    superseded_allowance: int,
    kis_superseded_allowance: int,
    kis_token_superseded_allowance: int,
) -> BootstrapBudget:
    if (
        not 1 <= union_size <= APPROVED_HORIZON_UNION_SIZE
        or monthly_schedule_count < 1
        or raw_session_count != RAW_SESSION_COUNT
    ):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: bootstrap dimensions are invalid")
    krx_get = raw_session_count * 4 + monthly_schedule_count * 2 + monthly_schedule_count
    kis_get = union_size * math.ceil(raw_session_count / 100)
    return BootstrapBudget(
        krx_get=krx_get + superseded_allowance,
        kis_get=kis_get + kis_superseded_allowance,
        kis_token=APPROVED_KIS_TOKEN_MAX + kis_token_superseded_allowance,
        ecos_get=APPROVED_ECOS_MAX_GET,
        krx_superseded_allowance=superseded_allowance,
        kis_superseded_allowance=kis_superseded_allowance,
        kis_token_superseded_allowance=kis_token_superseded_allowance,
    )


def is_spac_name(official_name: str) -> bool:
    """공식 이름에 승인된 두 marker만 적용하며 이름 추론 범위를 넓히지 않는다."""

    if not official_name or any(unicodedata.category(ch).startswith("C") for ch in official_name):
        raise LightGbmContractError("official security name is invalid")
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFC", official_name))
    return "스팩" in normalized or "기업인수목적" in normalized


def require_standard_stock_identity(standard_code: str) -> str:
    """주식 permanent identity는 12자리 표준 종목코드만 허용한다."""

    if re.fullmatch(r"[0-9A-Z]{12}", standard_code, flags=re.ASCII) is None:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: standard instrument identity is missing")
    return standard_code


def classify_krx_security(
    *, security_group: str, stock_kind: str, official_name: str, source_service: str
) -> SecurityClassification:
    """KRX exact category를 우선하고 SPAC만 승인된 공식-name fallback을 사용한다."""

    if source_service == "etf_bydd_trd":
        return SecurityClassification.ETF
    if source_service not in {"stk_isu_base_info", "ksq_isu_base_info"}:
        return SecurityClassification.UNRESOLVED
    if "부동산투자회사" in security_group or security_group == "REITs":
        return SecurityClassification.REIT
    if stock_kind == "우선주":
        return SecurityClassification.PREFERRED
    if is_spac_name(official_name):
        return SecurityClassification.SPAC
    if security_group == "주권" and stock_kind == "보통주":
        return SecurityClassification.COMMON_STOCK
    return SecurityClassification.UNRESOLVED


def corporate_action_sensitivity_pass(
    kis_close_returns: Sequence[float],
    krx_close_returns: Sequence[float],
    kis_label_returns: Sequence[float],
    krx_label_returns: Sequence[float],
) -> bool:
    """Event-free KIS/KRX return divergence의 exact 0.0005/0.1% gate."""

    return _difference_rate(kis_close_returns, krx_close_returns) <= 0.001 and _difference_rate(
        kis_label_returns, krx_label_returns
    ) <= 0.001


def macro_timing_sensitivity_metrics(
    *,
    primary_probabilities: np.ndarray,
    delayed_probabilities: np.ndarray,
    labels: Sequence[int],
    primary_row_count: int,
) -> dict[str, float]:
    """+1-session macro timing 판정에 쓰이는 지표를 그대로 돌려준다.

    판정만 남기면 네 하위 조건 중 어디서 걸렸는지 알 수 없어 모델 gate 실패와 계산 결함을
    구분할 수 없다.
    """

    if primary_row_count <= 0 or len(labels) < math.ceil(primary_row_count * 0.98):
        raise DatasetUnavailable("UNIDENTIFIABLE_OUTPUT: macro sensitivity coverage is below 98%")
    if primary_probabilities.shape != delayed_probabilities.shape or (
        primary_probabilities.shape != (len(labels), 3)
    ):
        raise LightGbmContractError("macro sensitivity probability shape is invalid")
    labels_array = np.asarray(labels, dtype=np.int64)
    primary_class = tie_aware_argmax(primary_probabilities)
    delayed_class = tie_aware_argmax(delayed_probabilities)
    return {
        "disagreement": float(np.mean(primary_class != delayed_class)),
        "primaryEce": top_label_ece(labels_array, primary_probabilities),
        "delayedEce": top_label_ece(labels_array, delayed_probabilities),
        "primaryBrier": multiclass_brier(labels_array, primary_probabilities),
        "delayedBrier": multiclass_brier(labels_array, delayed_probabilities),
        "primaryLogLoss": natural_log_loss(labels_array, primary_probabilities),
        "delayedLogLoss": natural_log_loss(labels_array, delayed_probabilities),
    }


def macro_timing_sensitivity_verdict(metrics: Mapping[str, float]) -> bool:
    """측정값에서 판정만 파생한다. 임계값은 계약이 고정한 그대로다."""

    return (
        metrics["disagreement"] <= 0.10
        and metrics["delayedEce"] - metrics["primaryEce"] <= 0.02
        and metrics["delayedLogLoss"] - metrics["primaryLogLoss"] <= 0.02
        and metrics["delayedBrier"] <= 1.10 * metrics["primaryBrier"]
    )


def macro_timing_sensitivity_pass(
    *,
    primary_probabilities: np.ndarray,
    delayed_probabilities: np.ndarray,
    labels: Sequence[int],
    primary_row_count: int,
) -> bool:
    """고정 model/calibrator의 +1-session macro timing sensitivity를 검증한다."""

    return macro_timing_sensitivity_verdict(
        macro_timing_sensitivity_metrics(
            primary_probabilities=primary_probabilities,
            delayed_probabilities=delayed_probabilities,
            labels=labels,
            primary_row_count=primary_row_count,
        )
    )


def align_macro_observations(
    *,
    sessions: Sequence[date],
    base_rate_observations: dict[date, Decimal],
    usdkrw_observations: dict[date, Decimal],
) -> tuple[tuple[float, float], ...]:
    """기준금리는 last-known level, USDKRW는 exact session만 허용한다."""

    if (
        not sessions
        or tuple(sessions) != tuple(sorted(sessions))
        or len(set(sessions)) != len(sessions)
    ):
        raise LightGbmContractError("macro session schedule is invalid")
    seed_dates = [day for day in base_rate_observations if day <= sessions[0]]
    if not seed_dates:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: base-rate seed is missing")
    ordered_rate = sorted(base_rate_observations.items())
    rate_index = 0
    current_rate: Decimal | None = None
    output: list[tuple[float, float]] = []
    for session in sessions:
        while rate_index < len(ordered_rate) and ordered_rate[rate_index][0] <= session:
            current_rate = ordered_rate[rate_index][1]
            rate_index += 1
        fx = usdkrw_observations.get(session)
        if current_rate is None or fx is None:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: exact macro observation is missing")
        pair = (float(current_rate), float(fx))
        if not all(math.isfinite(value) for value in pair) or pair[1] <= 0:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: macro observation is invalid")
        output.append(pair)
    return tuple(output)


def _difference_rate(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise DatasetUnavailable("UNIDENTIFIABLE_OUTPUT: sensitivity denominator is absent")
    left_values = np.asarray(left, dtype=np.float64)
    right_values = np.asarray(right, dtype=np.float64)
    if not np.isfinite(left_values).all() or not np.isfinite(right_values).all():
        raise DatasetUnavailable("DATASET_UNAVAILABLE: sensitivity evidence is non-finite")
    return float(np.mean(np.abs(left_values - right_values) > 0.0005))
