"""S5 수집 결과 분류. 자율 운영이 스스로 결정하기 위한 최소 어휘다.

지금까지 실패는 이유를 들고 나오지 않았다. `DatasetUnavailable`이 `LightGbmContractError`의
하위 클래스여서 "그 단위에 provider 증거가 없다"와 "계약이 깨졌다"가 같은 타입이었고, 분류되지
않은 `ValueError`가 provider 경계를 넘기도 했다. 그러면 코드는 재시도할지 건너뛸지 멈출지 고를
근거가 없고, 사람이 매번 진단 스크립트를 써야 한다.

분류 지식은 예외를 정의한 곳이 스스로 선언한다(`outcome_class` 속성). 그래서 이 모듈은 HTTP
client를 import하지 않으며 provider 계층에 역의존하지 않는다. 선언이 없는 예외는 조용히
넘어가지 않고 `CONTRACT_VIOLATION`으로 fail-closed 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.lightgbm.errors import DatasetUnavailable


class OutcomeClass(StrEnum):
    """한 수집 단위의 종결 분류. 코드가 다음 행동을 이 값에서만 고른다."""

    # provider 측 일시 장애다. 승인 상한 안에서 다음 tick에 다시 시도한다.
    RETRYABLE_TRANSIENT = "RETRYABLE_TRANSIENT"
    # 그 단위에 provider 증거가 존재하지 않는다. 증거를 기록하고 그 단위만 제외한다.
    # 값을 만들어 채우는 일은 어떤 경우에도 하지 않는다.
    EVIDENCE_GAP = "EVIDENCE_GAP"
    # 계약이 깨졌다. 사람이 판단해야 하므로 멈춘다. 분류를 선언하지 않은 예외의 기본값이다.
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    # 승인 상한을 소진했다. 멈춘다.
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class CollectionUnit:
    """진단 원장이 지목할 수집 단위 신원이다.

    packet과 manifest에 이미 있는 공개 식별자만 담는다. provider 응답 조각은 담지 않는다.
    """

    provider: str
    operation_id: str
    query_sha256: str
    label: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "operationId": self.operation_id,
            "querySha256": self.query_sha256,
            "label": self.label,
        }


class BootstrapEvidenceGap(DatasetUnavailable):
    """그 단위에 provider 증거가 존재하지 않는다.

    상장폐지, 신규상장, 무거래 세션처럼 실제 시장에서 나오는 성질이다. 전체 실행을 죽이는 대신
    측정값과 함께 그 단위만 제외하는 것이 정확한 처리다. 제외 비율 상한은 호출자가 강제한다.
    """

    code = "EVIDENCE_GAP"
    outcome_class = OutcomeClass.EVIDENCE_GAP

    def __init__(
        self,
        message: str,
        *,
        unit: CollectionUnit,
        measured: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.unit = unit
        self.measured: Mapping[str, object] = dict(measured or {})


def classify(error: BaseException) -> OutcomeClass:
    """예외가 스스로 선언한 분류를 읽는다. 선언이 없으면 fail-closed 한다.

    기본값을 `CONTRACT_VIOLATION`으로 두는 것이 핵심이다. 모르는 실패를 재시도나 제외로 넘기면
    승인 호출을 태우거나 데이터를 조용히 축소한다.
    """

    declared = getattr(error, "outcome_class", None)
    if isinstance(declared, OutcomeClass):
        return declared
    if isinstance(declared, str):
        try:
            return OutcomeClass(declared)
        except ValueError:
            return OutcomeClass.CONTRACT_VIOLATION
    return OutcomeClass.CONTRACT_VIOLATION


def is_retryable(error: BaseException) -> bool:
    """다음 tick에 같은 단위를 다시 열어도 되는지 판정한다."""

    return classify(error) is OutcomeClass.RETRYABLE_TRANSIENT


def halts_run(error: BaseException) -> bool:
    """그 실행을 계속할 수 없는 분류인지 판정한다."""

    return classify(error) in {
        OutcomeClass.CONTRACT_VIOLATION,
        OutcomeClass.BUDGET_EXHAUSTED,
    }
