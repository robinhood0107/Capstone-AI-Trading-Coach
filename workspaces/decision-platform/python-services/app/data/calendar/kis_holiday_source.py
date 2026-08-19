"""S1.6 calendar authority가 실제 CTCA0903R 응답을 소비하는 유일한 연결부다.

기존 adapter(`KISHolidayAdapter`)와 기존 KIS client는 각각 구현돼 있었지만 둘을 잇는 production
경로가 없어, S5 달력은 정적 correction set만 신뢰할 수밖에 없었다. 이 모듈은 그 공백만 메우며
provider 호출 상한과 mode 경계를 넓히지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from app.data.calendar.adapters.kis_holiday import KISHolidayAdapter
from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.models import KISHolidayObservation

# 후보 session만 확정하는 용도이므로 승인 상한을 작게 고정한다. 전체 window 전수 조회가 아니다.
MAX_KIS_HOLIDAY_CALLS = 32


class HolidayResponseClient(Protocol):
    """`KISMarketClient.holiday_response`만 요구해 client 전체 표면에 의존하지 않는다."""

    def holiday_response(self, base_date: date) -> dict[str, Any] | None: ...


class KISHolidayAuthority:
    """승인 상한 안에서만 CTCA0903R를 열고 날짜별 관측을 한 번씩 확정한다."""

    def __init__(
        self,
        client: HolidayResponseClient,
        *,
        max_calls: int = MAX_KIS_HOLIDAY_CALLS,
    ) -> None:
        if isinstance(max_calls, bool) or not 1 <= max_calls <= MAX_KIS_HOLIDAY_CALLS:
            raise AdapterValidationError("KIS holiday call cap is outside the approved bound")
        self._client = client
        self._max_calls = max_calls
        self._calls = 0
        self._adapter = KISHolidayAdapter(self._fetch)

    @property
    def calls(self) -> int:
        """실제로 발생한 physical holiday 호출 수다. 캐시 재사용은 세지 않는다."""

        return self._calls

    @property
    def max_calls(self) -> int:
        return self._max_calls

    def observe(self, day: date) -> KISHolidayObservation:
        """엄격 파서를 통과한 opnd_yn 권위만 반환한다."""

        return self._adapter.observe(day)

    def _fetch(self, day: date) -> dict[str, Any]:
        if self._calls >= self._max_calls:
            raise AdapterValidationError("KIS holiday call cap is exhausted")
        self._calls += 1
        response = self._client.holiday_response(day)
        if response is None:
            # mock/offline 경계에서는 권위를 주장하지 않고 명시적으로 미확정 처리한다.
            raise AdapterValidationError("KIS holiday authority is unavailable in this mode")
        return response
