from __future__ import annotations


class GdeltAggregateError(ValueError):
    """GDELT 경계 실패를 provider payload 없이 stable reason code로 전달한다."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")
