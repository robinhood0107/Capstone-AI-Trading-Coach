"""S5 LightGBM fail-closed 상태와 계약 오류."""


class LightGbmContractError(ValueError):
    """S5 입력·artifact·학습 계약이 위반됐을 때 원문 경로 없이 보고한다."""


class DatasetUnavailable(LightGbmContractError):
    """필수 point-in-time evidence가 없어 실제 dataset을 만들 수 없음을 나타낸다."""

    code = "DATASET_UNAVAILABLE"
