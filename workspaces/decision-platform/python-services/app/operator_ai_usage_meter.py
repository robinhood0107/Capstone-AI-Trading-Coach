"""Best-effort recording of estimated operator-funded AI usage."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field

import psycopg
from psycopg.conninfo import conninfo_to_dict

_LOGGER = logging.getLogger(__name__)
_OWNER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{4,96}$")
_MIN_VERTEX_INPUT_MICROUSD_PER_TOKEN = 3
_MIN_VERTEX_OUTPUT_MICROUSD_PER_TOKEN = 17


@dataclass(frozen=True, slots=True)
class TradeAiUsageMeter:
    """Writes one estimate to the existing usage ledger without gating a Vertex call."""

    database_dsn: str = field(repr=False)
    input_microusd_per_token: int = _MIN_VERTEX_INPUT_MICROUSD_PER_TOKEN
    output_microusd_per_token: int = _MIN_VERTEX_OUTPUT_MICROUSD_PER_TOKEN

    @classmethod
    def from_environment(cls) -> TradeAiUsageMeter | None:
        if os.environ.get("MARS_PUBLIC_SURFACE_MODE", "LOCAL").strip() != "FULL":
            return None
        database_dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
        try:
            parsed = conninfo_to_dict(database_dsn)
        except psycopg.Error:
            _LOGGER.warning("trade_ai_usage_meter_unavailable reason=database_role")
            return None
        if (
            parsed.get("user") != "decision_automation_runtime"
            or parsed.get("host") not in {"postgres", "127.0.0.1", "localhost"}
            or not parsed.get("dbname")
        ):
            _LOGGER.warning("trade_ai_usage_meter_unavailable reason=database_role")
            return None
        return cls(
            database_dsn=database_dsn,
            input_microusd_per_token=_measurement_rate(
                "P1_VERTEX_INPUT_MICROUSD_PER_TOKEN", _MIN_VERTEX_INPUT_MICROUSD_PER_TOKEN
            ),
            output_microusd_per_token=_measurement_rate(
                "P1_VERTEX_OUTPUT_MICROUSD_PER_TOKEN", _MIN_VERTEX_OUTPUT_MICROUSD_PER_TOKEN
            ),
        )

    def record(
        self,
        *,
        owner_user_id: str,
        run_id: str,
        payload_bytes: bytes,
        output_token_cap: int,
    ) -> bool:
        if (
            _OWNER_ID.fullmatch(owner_user_id) is None
            or not run_id
            or len(run_id) > 128
            or not payload_bytes
            or output_token_cap <= 0
        ):
            _LOGGER.warning("trade_ai_usage_meter_skipped reason=measurement_identity")
            return False
        # The sent JSON is text-only; these conservative model rates estimate exposure, not a bill.
        max_gross_microusd = (
            len(payload_bytes) + 1_024
        ) * self.input_microusd_per_token + output_token_cap * self.output_microusd_per_token
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
                self.database_dsn, autocommit=False, connect_timeout=1
            ) as connection:
                with connection.transaction():
                    connection.execute("SET LOCAL statement_timeout = '1s'")
                    connection.execute("SET LOCAL lock_timeout = '250ms'")
                    if connection.execute("SELECT current_user, session_user").fetchone() != (
                        "decision_automation_runtime",
                        "decision_automation_runtime",
                    ):
                        _LOGGER.warning("trade_ai_usage_meter_unavailable reason=database_role")
                        return False
                    connection.execute(
                        """
                        SELECT public.record_operator_ai_gross_usage_v1(
                            %s, %s, 'TRADE_AI', 'VERTEX', %s
                        )
                        """,
                        (reservation_id, owner_user_id, max_gross_microusd),
                    )
            return True
        except Exception:
            _LOGGER.warning("trade_ai_usage_meter_unavailable reason=database_write")
            return False


def _measurement_rate(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if re.fullmatch(r"[1-9][0-9]{0,5}", raw) is None:
        return default
    value = int(raw)
    return value if value >= default else default
