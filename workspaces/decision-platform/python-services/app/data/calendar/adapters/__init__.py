"""S1.6 provider fixture를 bounded canonical 모델로 바꾸는 adapter package."""

from app.data.calendar.adapters.xkrx import build_xkrx_sessions

__all__ = ["build_xkrx_sessions"]
