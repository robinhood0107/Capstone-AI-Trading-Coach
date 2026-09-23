"""Private deployment ceiling shared by Python operator-funded provider transports."""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation


class OperatorAiBudgetConfigurationError(RuntimeError):
    """The public deployment ceiling is absent or malformed; provider calls stay closed."""


def deployment_hard_cap_microusd() -> int | None:
    mode = os.environ.get("MARS_PUBLIC_SURFACE_MODE", "LOCAL").strip()
    if mode == "LOCAL":
        return None
    if mode not in {"FULL", "DEMO"}:
        raise OperatorAiBudgetConfigurationError("MARS_PUBLIC_SURFACE_MODE is invalid")
    raw = os.environ.get("MARS_AI_DAILY_HARD_CAP_USD", "").strip()
    if re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,2})?", raw) is None:
        raise OperatorAiBudgetConfigurationError("MARS_AI_DAILY_HARD_CAP_USD is required")
    try:
        microusd = int(Decimal(raw) * 1_000_000)
    except (InvalidOperation, ValueError, OverflowError):
        raise OperatorAiBudgetConfigurationError("MARS_AI_DAILY_HARD_CAP_USD is invalid") from None
    if microusd <= 0:
        raise OperatorAiBudgetConfigurationError("MARS_AI_DAILY_HARD_CAP_USD is invalid")
    return microusd
