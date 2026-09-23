"""Private deployment ceiling shared by Python operator-funded provider transports."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.conninfo import conninfo_to_dict

_OWNER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{4,96}$")
_RATE = re.compile(r"[1-9][0-9]{0,5}")
_INPUT_OVERHEAD_TOKEN_CAP = 1_024
# Default global Gemini 3.5 Flash list rates on 2026-09-23 are $2.70/$16.20
# per million input/output tokens. Round up per token; a model change requires
# an operator review and possibly higher rates before public provider calls.
_MIN_VERTEX_INPUT_MICROUSD_PER_TOKEN = 3
_MIN_VERTEX_OUTPUT_MICROUSD_PER_TOKEN = 17


class OperatorAiBudgetConfigurationError(RuntimeError):
    """The public deployment ceiling is absent or malformed; provider calls stay closed."""


class OperatorAiBudgetReservationError(RuntimeError):
    """One provider attempt could not reserve its operator-funded gross exposure."""


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


def _microusd_per_token(name: str, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if _RATE.fullmatch(raw) is None:
        raise OperatorAiBudgetConfigurationError(f"{name} is required")
    value = int(raw)
    if value < minimum:
        raise OperatorAiBudgetConfigurationError(f"{name} is below the public rate floor")
    return value


@dataclass(frozen=True, slots=True)
class TradeAiGrossBudget:
    """Reserve before the Vertex socket using the automation role and the shared V201 lock."""

    database_dsn: str = field(repr=False)
    hard_cap_microusd: int
    input_microusd_per_token: int
    output_microusd_per_token: int

    @classmethod
    def from_environment(cls) -> TradeAiGrossBudget | None:
        hard_cap = deployment_hard_cap_microusd()
        if hard_cap is None:
            return None
        if os.environ.get("MARS_PUBLIC_SURFACE_MODE", "").strip() != "FULL":
            raise OperatorAiBudgetConfigurationError("TRADE_AI is unavailable outside FULL")
        database_dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
        try:
            parsed = conninfo_to_dict(database_dsn)
        except psycopg.Error:
            raise OperatorAiBudgetConfigurationError("TRADE_AI budget DSN is invalid") from None
        if (
            parsed.get("user") != "decision_automation_runtime"
            or parsed.get("host") not in {"postgres", "127.0.0.1", "localhost"}
            or not parsed.get("dbname")
        ):
            raise OperatorAiBudgetConfigurationError("TRADE_AI budget role is invalid")
        return cls(
            database_dsn=database_dsn,
            hard_cap_microusd=hard_cap,
            input_microusd_per_token=_microusd_per_token(
                "P1_VERTEX_INPUT_MICROUSD_PER_TOKEN", _MIN_VERTEX_INPUT_MICROUSD_PER_TOKEN
            ),
            output_microusd_per_token=_microusd_per_token(
                "P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN", _MIN_VERTEX_OUTPUT_MICROUSD_PER_TOKEN
            ),
        )

    def reserve(
        self,
        *,
        owner_user_id: str,
        run_id: str,
        payload_bytes: bytes,
        output_token_cap: int,
    ) -> None:
        if (
            _OWNER_ID.fullmatch(owner_user_id) is None
            or not run_id
            or len(run_id) > 128
            or not payload_bytes
            or output_token_cap <= 0
        ):
            raise OperatorAiBudgetReservationError("TRADE_AI budget identity is invalid")
        # The sent JSON is text-only. Escaped byte length plus a fixed protocol allowance
        # conservatively reserves input exposure; maxOutputTokens bounds the output side.
        max_gross_microusd = (
            len(payload_bytes) + _INPUT_OVERHEAD_TOKEN_CAP
        ) * self.input_microusd_per_token + output_token_cap * self.output_microusd_per_token
        if max_gross_microusd > self.hard_cap_microusd:
            raise OperatorAiBudgetReservationError("OPERATOR_AI_DAILY_GROSS_BUDGET_EXHAUSTED")
        reservation_id = (
            "aibr_"
            + hashlib.sha256(
                b"TRADE_AI\0"
                + run_id.encode("utf-8")
                + b"\0"
                + hashlib.sha256(payload_bytes).digest()
            ).hexdigest()[:32]
        )
        try:
            with psycopg.connect(
                self.database_dsn, autocommit=False, connect_timeout=2
            ) as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL statement_timeout = '5s'")
                    connection.execute("SET LOCAL lock_timeout = '500ms'")
                    if connection.execute("SELECT current_user, session_user").fetchone() != (
                        "decision_automation_runtime",
                        "decision_automation_runtime",
                    ):
                        raise OperatorAiBudgetReservationError("TRADE_AI budget role mismatch")
                    accepted = connection.execute(
                        """
                        SELECT public.reserve_operator_ai_gross_usage_v1(
                            %s, %s, 'TRADE_AI', 'VERTEX', %s, %s
                        )
                        """,
                        (reservation_id, owner_user_id, max_gross_microusd, self.hard_cap_microusd),
                    ).fetchone()
                    if accepted != (True,):
                        raise OperatorAiBudgetReservationError(
                            "OPERATOR_AI_DAILY_GROSS_BUDGET_EXHAUSTED"
                        )
        except psycopg.Error:
            raise OperatorAiBudgetReservationError("TRADE_AI budget database unavailable") from None
