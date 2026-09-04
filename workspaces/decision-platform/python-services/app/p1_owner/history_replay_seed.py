"""Persist deterministic INTERNAL_PAPER history from computed signal panels."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg

_KST: Final = ZoneInfo("Asia/Seoul")
_USER_ID: Final = "usr_demo_user"
_ADVISORY_LOCK: Final = 0x7031_4849_5354_0002
_ID_NAMESPACE: Final = "p1-history-replay-v1"
_OFFLINE_DEMO_ENV: Final = "P1_OFFLINE_DEMO"
_DSN_ENV: Final = "P1_HISTORY_REPLAY_DATABASE_DSN"
_OPEN_TIME: Final = time(9, 30)
_BROKERAGE_MODE: Final = "INTERNAL_PAPER"
_REPLAY_ACCOUNT_ID: Final = "acct_dddddddddddddddddddddddddddddddd"


class HistoryReplaySeedError(RuntimeError):
    """Raised when a complete replay cannot be persisted."""


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join((_ID_NAMESPACE, *parts)).encode("utf-8")).hexdigest()


def _run_id(session: date) -> str:
    return f"auto_run_replay{_digest('run', session.isoformat())[:23]}"


def _reservation_id(session: date, symbol: str) -> str:
    # Reservation IDs have a fixed prefix and 32-character hexadecimal body.
    return f"auto_res_{_digest('res', session.isoformat(), symbol)[:32]}"


def _position_id(session: date, symbol: str) -> str:
    return f"auto_pos_replay{_digest('pos', session.isoformat(), symbol)[:23]}"


def _entry_order_id(session: date, symbol: str) -> str:
    """Derive a stable entry identifier without creating a brokerage order."""

    return f"ord_mock_{_digest('ord', session.isoformat(), symbol)[:32]}"


def _event_id(run_id: str, sequence: int, kind: str) -> str:
    return f"auto_evt_replay{_digest('evt', run_id, str(sequence), kind)[:23]}"


def _assert_boundary(database_dsn: str) -> None:
    """superuser 로 자기 스택의 DB 에만 쓴다. 원격이나 다른 role 이면 시작하지 않는다."""

    if os.environ.get(_OFFLINE_DEMO_ENV, "").strip().lower() != "true":
        raise HistoryReplaySeedError("HISTORY_REPLAY_OFFLINE_DEMO_REQUIRED")
    parsed = urlparse(database_dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username != "postgres"
        or parsed.hostname not in {"postgres", "127.0.0.1", "localhost"}
        or not (parsed.path or "").lstrip("/")
    ):
        raise HistoryReplaySeedError("HISTORY_REPLAY_DSN_BOUNDARY")


def _policy(cursor: psycopg.Cursor[Any]) -> dict[str, Any]:
    cursor.execute(
        "select policy_id, version, capital_limit_krw, stop_loss_bps, take_profit_bps,"
        " principle_id, principle_version_id, principle_version"
        " from automation_policy_versions where user_id=%s order by version desc limit 1",
        (_USER_ID,),
    )
    row = cursor.fetchone()
    if row is None:
        raise HistoryReplaySeedError("HISTORY_REPLAY_POLICY_MISSING")
    return {
        "policy_id": row[0],
        "version": int(row[1]),
        "capital_limit_krw": int(row[2]),
        "stop_loss_bps": int(row[3]),
        "take_profit_bps": int(row[4]),
        "principle_id": row[5],
        "principle_version_id": row[6],
        "principle_version": int(row[7]),
    }


def _account_id(cursor: psycopg.Cursor[Any]) -> str:
    cursor.execute(
        """
        INSERT INTO paper_accounts(
          account_id,user_id,name,cash_balance,currency,status,owner_scope_hash,margin_requirement_krw
        ) VALUES (%s,%s,'Offline history replay',10000000,'KRW','ACTIVE',repeat('d',64),0)
        ON CONFLICT (account_id) DO NOTHING
        """,
        (_REPLAY_ACCOUNT_ID, _USER_ID),
    )
    return _REPLAY_ACCOUNT_ID


def _closes(cursor: psycopg.Cursor[Any], sessions: Sequence[date]) -> dict[date, dict[str, int]]:
    """세션별 종가. 재생의 체결가이자 청산 판정의 가격이다."""

    cursor.execute(
        "select session_date, symbol, close_price from market_data_bars"
        " where session_date = any(%s)",
        (list(sessions),),
    )
    out: dict[date, dict[str, int]] = {}
    for session_date, symbol, close in cursor.fetchall():
        out.setdefault(session_date, {})[str(symbol)] = int(close)
    return out


def _select_buy(
    signals: dict[str, dict[str, Any]], held: set[str], closes: dict[str, int]
) -> tuple[str, float] | None:
    """automation._buy_candidates 와 같은 판정과 정렬. 종목을 더하지 않는다."""

    candidates = [
        (symbol, float(value["expectedReturn"]))
        for symbol, value in signals.items()
        if value["rule"] == "BUY"
        and value["lstm"] != "SELL"
        and symbol not in held
        and symbol in closes
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return candidates[0]


def replay(document: dict[str, Any], *, database_dsn: str) -> dict[str, int]:
    """세션을 순서대로 진행시키고 결과를 적재한다. 같은 입력에 같은 행이 나온다."""

    _assert_boundary(database_dsn)
    ordered = sorted(document["sessions"])
    counts = {"runs": 0, "reservations": 0, "positions": 0, "closed": 0}
    with psycopg.connect(database_dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user, session_user")
            row = cursor.fetchone()
            if row is None or row[0] != "postgres" or row[1] != "postgres":
                raise HistoryReplaySeedError("HISTORY_REPLAY_ROLE_BOUNDARY")
            cursor.execute("select pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK,))
            policy = _policy(cursor)
            account_id = _account_id(cursor)
            sessions = [date.fromisoformat(item) for item in ordered]
            closes = _closes(cursor, sessions)
            slot_budget = policy["capital_limit_krw"] // 5
            # 보유 중인 재생 포지션. 키는 종목, 값은 (진입세션, 수량, 평균단가).
            open_positions: dict[str, tuple[date, int, int]] = {}

            for key in ordered:
                session = date.fromisoformat(key)
                session_closes = closes.get(session)
                if not session_closes:
                    # 그 날의 바가 없으면 거래일이 아니다. 달력을 따로 읽지 않는다.
                    continue
                signals = document["sessions"][key]["signals"]
                run_id = _run_id(session)
                started = datetime.combine(session, _OPEN_TIME, _KST).astimezone(UTC)
                sequence = 0
                events: list[tuple[str, int, str, datetime]] = []

                def _event(kind: str, offset: int) -> None:
                    nonlocal sequence
                    sequence += 1
                    events.append(
                        (
                            _event_id(run_id, sequence, kind),
                            sequence,
                            kind,
                            started + timedelta(seconds=offset),
                        )
                    )

                _event("BASELINE_CAPTURED", 0)

                # run 행을 먼저 만든다. 예약이 run_id 를 FK 로 참조하므로 순서가 강제된다.
                # 최종 상태는 아래에서 다시 쓴다.
                cursor.execute(
                    """
                    INSERT INTO automation_runs(
                      run_id,user_id,session_date,state,brokerage_mode,selected_symbol,
                      selected_side,physical_submit_count,vertex_call_count,provider_calls,
                      started_at,updated_at
                    ) VALUES (%s,%s,%s,'SCHEDULED',%s,NULL,NULL,0,0,0,%s,%s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (run_id, _USER_ID, session, _BROKERAGE_MODE, started, started),
                )

                # 1) 청산 먼저. 운영도 EXIT_SELECTED 가 진입보다 앞선다.
                exited: list[tuple[str, str, int]] = []
                for symbol, (entry_session, quantity, entry_price) in list(open_positions.items()):
                    price = session_closes.get(symbol)
                    if price is None or entry_session == session:
                        continue
                    move_bps = (price - entry_price) * 10_000 // entry_price
                    reason: str | None = None
                    if move_bps <= -policy["stop_loss_bps"]:
                        reason = "STOP_LOSS"
                    elif move_bps >= policy["take_profit_bps"]:
                        reason = "TAKE_PROFIT"
                    elif (
                        signals.get(symbol, {}).get("rule") == "SELL"
                        and signals.get(symbol, {}).get("lstm") == "SELL"
                    ):
                        reason = "MODEL_SELL"
                    if reason is None:
                        continue
                    realized = (price - entry_price) * quantity
                    cursor.execute(
                        """
                        UPDATE automation_positions
                           SET status='CLOSED', quantity=0, closed_at=%s, exit_reason=%s,
                               exit_filled_quantity=%s, exit_average_fill_price_krw=%s,
                               realized_pnl_krw=%s
                         WHERE position_id=%s
                        """,
                        (
                            started + timedelta(seconds=30),
                            reason,
                            quantity,
                            price,
                            realized,
                            _position_id(entry_session, symbol),
                        ),
                    )
                    exited.append((symbol, reason, realized))
                    open_positions.pop(symbol, None)
                    counts["closed"] += 1

                # 2) 진입. 세션당 신규 주문은 한 건이고 동시보유 상한은 5개다.
                chosen = None
                if len(open_positions) < 5:
                    chosen = _select_buy(signals, set(open_positions), session_closes)
                state = "SKIPPED_NO_ACTION"
                selected_symbol: str | None = None
                if chosen is not None:
                    symbol, _expected = chosen
                    price = session_closes[symbol]
                    quantity = slot_budget // price
                    if quantity >= 1:
                        selected_symbol = symbol
                        state = "COMPLETED"
                        _event("BUY_SELECTED", 10)
                        _event("ORDER_RESERVED", 20)
                        _event("RISK_RESULT_RECORDED", 25)
                        _event("ORDER_OUTCOME_RECORDED", 30)
                        cursor.execute(
                            """
                            INSERT INTO automation_order_reservations(
                              reservation_id,run_id,user_id,session_date,symbol,side,quantity,
                              limit_price_krw,logical_submit_count,created_at,updated_at,
                              estimated_amount_krw,policy_id,policy_version,principle_version_id,
                              strategy_id,order_intent_sha256,
                              filled_quantity,leaves_quantity,unfilled_terminated_quantity,
                              average_fill_price_krw,reconciliation_status
                            ) VALUES (%s,%s,%s,%s,%s,'BUY',%s,%s,1,%s,%s,%s,%s,%s,%s,
                                      'strategy_rule_lstm_v1',%s,%s,0,0,%s,'MATCHED')
                            ON CONFLICT (reservation_id) DO UPDATE SET
                              quantity=excluded.quantity,limit_price_krw=excluded.limit_price_krw,
                              filled_quantity=excluded.filled_quantity,
                              average_fill_price_krw=excluded.average_fill_price_krw,
                              updated_at=excluded.updated_at
                            """,
                            (
                                _reservation_id(session, symbol),
                                run_id,
                                _USER_ID,
                                session,
                                symbol,
                                quantity,
                                price,
                                started + timedelta(seconds=20),
                                started + timedelta(seconds=30),
                                quantity * price,
                                policy["policy_id"],
                                policy["version"],
                                policy["principle_version_id"],
                                _digest("intent", session.isoformat(), symbol)[:64],
                                quantity,
                                price,
                            ),
                        )
                        counts["reservations"] += 1
                        cursor.execute(
                            """
                            INSERT INTO automation_positions(
                              position_id,user_id,account_id,symbol,quantity,entry_session,
                              status,bot_owned,short_allowed,created_at,entry_order_id,
                              entry_ordered_quantity,entry_filled_quantity,
                              entry_unfilled_quantity,entry_average_fill_price_krw,
                              exit_filled_quantity,
                              policy_id,policy_version,stop_loss_bps,take_profit_bps
                            ) VALUES (%s,%s,%s,%s,%s,%s,'OPEN',true,false,%s,%s,%s,%s,0,%s,0,%s,%s,%s,%s)
                            ON CONFLICT (position_id) DO UPDATE SET account_id=excluded.account_id
                            """,
                            (
                                _position_id(session, symbol),
                                _USER_ID,
                                account_id,
                                symbol,
                                quantity,
                                session,
                                started + timedelta(seconds=30),
                                _entry_order_id(session, symbol),
                                quantity,
                                quantity,
                                price,
                                policy["policy_id"],
                                policy["version"],
                                policy["stop_loss_bps"],
                                policy["take_profit_bps"],
                            ),
                        )
                        counts["positions"] += 1
                        open_positions[symbol] = (session, quantity, price)
                if state == "SKIPPED_NO_ACTION" and exited:
                    # 청산만 있고 진입이 없던 세션도 실제로 일한 세션이다.
                    state = "COMPLETED"
                _event("RUN_TRANSITIONED", 40)

                cursor.execute(
                    """
                    INSERT INTO automation_runs(
                      run_id,user_id,session_date,state,brokerage_mode,selected_symbol,
                      selected_side,physical_submit_count,vertex_call_count,provider_calls,
                      started_at,updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,0,%s,%s)
                    ON CONFLICT (run_id) DO UPDATE SET
                      state=excluded.state,selected_symbol=excluded.selected_symbol,
                      selected_side=excluded.selected_side,updated_at=excluded.updated_at
                    """,
                    (
                        run_id,
                        _USER_ID,
                        session,
                        state,
                        _BROKERAGE_MODE,
                        selected_symbol,
                        "BUY" if selected_symbol else None,
                        started,
                        started + timedelta(seconds=40),
                    ),
                )
                counts["runs"] += 1
                for event_id, order, kind, occurred in events:
                    cursor.execute(
                        """
                        INSERT INTO automation_events(
                          event_id,run_id,user_id,sequence,event_type,occurred_at,
                          payload_hash,provider_calls,order_submits,sanitized
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,true)
                        -- event_id와 (run_id, sequence) 중복을 모두 멱등 처리한다.
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            event_id,
                            run_id,
                            _USER_ID,
                            order,
                            kind,
                            occurred,
                            _digest("payload", event_id),
                        ),
                    )
        connection.commit()
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("signals", type=Path)
    arguments = parser.parse_args(argv)
    database_dsn = os.environ.get(_DSN_ENV, "").strip()
    if not database_dsn:
        raise SystemExit(f"{_DSN_ENV} is required")
    document = json.loads(arguments.signals.read_text(encoding="utf-8"))
    if document.get("contractId") != "p1-history-replay-signals.v1":
        raise SystemExit("HISTORY_REPLAY_SIGNALS_CONTRACT_INVALID")
    counts = replay(document, database_dsn=database_dsn)
    print(
        "P1_HISTORY_REPLAY=SEEDED "
        + " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
