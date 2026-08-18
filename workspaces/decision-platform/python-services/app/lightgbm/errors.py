"""S5 LightGBM fail-closed 상태와 계약 오류."""


class LightGbmContractError(ValueError):
    """S5 입력·artifact·학습 계약이 위반됐을 때 원문 경로 없이 보고한다."""


class DatasetUnavailable(LightGbmContractError):
    """필수 point-in-time evidence가 없어 실제 dataset을 만들 수 없음을 나타낸다."""

    code = "DATASET_UNAVAILABLE"
class CalendarDivergenceSuspected(DatasetUnavailable):
    """달력이 거래일로 본 session에 provider 증거가 없음을 별도 유형으로 보고한다.

    이 유형은 provider 장애가 아니라 달력 권위 결손의 징후다. 일반 실패와 섞이면 남은 승인
    호출을 계속 태우게 되므로 후보 session을 들고 즉시 fail-closed 한다.
    """

    code = "CALENDAR_DIVERGENCE_SUSPECTED"

    def __init__(self, message: str, *, operation_id: str, session_date: str) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.session_date = session_date
