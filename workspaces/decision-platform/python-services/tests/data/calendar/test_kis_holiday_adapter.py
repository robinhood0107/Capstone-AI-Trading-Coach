from __future__ import annotations

from datetime import date

import pytest

from app.data.calendar.adapters.kis_holiday import KISHolidayAdapter, parse_kis_holiday
from app.data.calendar.errors import AdapterValidationError


@pytest.mark.parametrize("opnd_yn, expected", [("Y", True), ("N", False)])
def test_kis_opnd_yn_is_authoritative(opnd_yn: str, expected: bool) -> None:
    observation = parse_kis_holiday(
        {
            "rt_cd": "0",
            "output": [
                {
                    "bass_dt": "20260603",
                    "opnd_yn": opnd_yn,
                    "bzdy_yn": "N" if expected else "Y",
                    "tr_day_yn": "N" if expected else "Y",
                    "sttl_day_yn": "Y",
                }
            ],
        },
        requested_day=date(2026, 6, 3),
    )

    assert observation.is_open is expected
    assert observation.source_id == "kis-holiday-ctca0903r"
    assert observation.tr_id == "CTCA0903R"


def test_kis_holiday_rejects_missing_opnd_yn_instead_of_falling_back() -> None:
    with pytest.raises(AdapterValidationError, match="opnd_yn"):
        parse_kis_holiday(
            {
                "rt_cd": "0",
                "output": [{"bass_dt": "20260603", "bzdy_yn": "Y", "tr_day_yn": "Y"}],
            },
            requested_day=date(2026, 6, 3),
        )


def test_kis_holiday_adapter_caches_same_day_observation_once() -> None:
    calls = 0

    def fetch(_: date) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"rt_cd": "0", "output": [{"bass_dt": "20260722", "opnd_yn": "Y"}]}

    adapter = KISHolidayAdapter(fetch)
    first = adapter.observe(date(2026, 7, 22))
    second = adapter.observe(date(2026, 7, 22))

    assert first == second
    assert calls == 1
