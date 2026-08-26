from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.brokerage.kis_mock_certification_gate import (
    CertificationWindowClosed,
    require_certification_window,
)

KST = ZoneInfo("Asia/Seoul")


def test_certification_window_accepts_open_xkrx_session() -> None:
    assert require_certification_window(datetime(2026, 8, 26, 9, 10, tzinfo=KST)) == "2026-08-26"
    assert require_certification_window(datetime(2026, 8, 26, 15, 0, tzinfo=KST)) == "2026-08-26"


@pytest.mark.parametrize(
    "observed",
    (
        datetime(2026, 8, 26, 9, 9, 59, tzinfo=KST),
        datetime(2026, 8, 26, 15, 0, 1, tzinfo=KST),
        datetime(2026, 8, 30, 10, 0, tzinfo=KST),
    ),
)
def test_certification_window_rejects_before_provider_client(observed: datetime) -> None:
    with pytest.raises(CertificationWindowClosed, match="MARKET_CLOSED"):
        require_certification_window(observed)
