"""시연용 DB. 자동운용이 남긴 포지션에서 주문·체결, 판정 이력, 학습일지를 되살린다.

`history_replay_seed` 는 세션별 자동운용 실행과 포지션까지만 적재한다. 화면에서 그 다음
단계 — 무엇을 왜 허용했는지(판정), 실제로 어떻게 나갔고 얼마에 체결됐는지(주문·체결),
그날 무엇을 배웠는지(학습일지) — 는 비어 있다. 이 모듈이 그 세 자리를 채운다.

**시연용이지 조작이 아니다.** 종목·수량·체결가·손익은 전부 이미 적재된
`automation_positions` 에서 읽는다. 없는 거래를 만들지 않고, 있던 거래의 앞뒤 기록을
계약이 요구하는 모양으로 복원할 뿐이다.

**지우기 쉬워야 한다.** 이 모듈이 쓴 행은 전부 `dec_demo` / `jnl_demo` 접두사를 가진
판정에 매달려 있다. `purge` 는 그 접두사만으로 정확히 되돌린다 — 실제 운용이 만든 행은
접두사가 다르므로 절대 지워지지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg

_KST: Final = ZoneInfo("Asia/Seoul")
_USER_ID: Final = "usr_demo_user"
_ADVISORY_LOCK: Final = 0x7031_4849_5354_0003
_ID_NAMESPACE: Final = "p1-demo-history-v1"
_OFFLINE_DEMO_ENV: Final = "P1_OFFLINE_DEMO"
_DSN_ENV: Final = "P1_DEMO_HISTORY_DATABASE_DSN"
_OPEN_TIME: Final = time(9, 30)
_BROKERAGE_MODE: Final = "KIS_MOCK"

# 이 접두사가 시연 데이터의 표식이자 purge 의 유일한 기준이다.
#
# 판정 식별자는 계약이 `^dec_[0-9a-f]{32}$` 로 못박아 두어 'demo' 같은 글자를 넣을 수
# 없다(`DecisionRequestParser.kt:273`). 그래서 16진수로만 이루어진 표식을 쓴다 -
# 실제 판정 식별자는 난수라 이 여덟 자리가 겹칠 확률은 40억분의 1이다.
_DECISION_MARK: Final = "d00dcafe"
_DECISION_PREFIX: Final = f"dec_{_DECISION_MARK}"
_JOURNAL_PREFIX: Final = "jnl_demo"

_SCHEMA_VERSION: Final = "s2-2-risk-decision/v1"
_RESULT_SCHEMA_VERSION: Final = "s2-3-decision-response/v1"
_SNAPSHOT_SCHEMA_VERSION: Final = "s2.2-metric-snapshot-v2"
_READINESS_POLICY_VERSION: Final = "s2-2-readiness-v1"
_TR_ID: Final = "VTTC0012U"
_MAPPING_VERSION: Final = "s2-2-mapping-v1"

_EXIT_LABEL: Final = {
    "STOP_LOSS": "손절선",
    "TAKE_PROFIT": "목표가",
    "MODEL_SELL": "모델 매도 신호",
    "MAX_HOLDING": "최대 보유 기간",
    "ATR_TRAILING": "ATR 추적손절",
}


class DemoHistorySeedError(RuntimeError):
    """시연 데이터를 온전히 적재할 수 없을 때."""


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join((_ID_NAMESPACE, *parts)).encode("utf-8")).hexdigest()


def _hex(*parts: str, length: int) -> str:
    return _digest(*parts)[:length]


def _decision_id(session: date, symbol: str, side: str) -> str:
    return f"{_DECISION_PREFIX}{_hex('dec', session.isoformat(), symbol, side, length=24)}"


def _evaluation_id(session: date, symbol: str, side: str) -> str:
    return f"evl_{_DECISION_MARK}{_hex('evl', session.isoformat(), symbol, side, length=24)}"


def _order_id(session: date, symbol: str, side: str) -> str:
    return f"ord_mock_{_hex('ord', session.isoformat(), symbol, side, length=32)}"


def _event_id(order_id: str, sequence: int) -> str:
    return f"oev_{_hex('oev', order_id, str(sequence), length=32)}"


def _observation_id(order_id: str) -> str:
    return f"ofo_{_hex('ofo', order_id, length=32)}"


def _receipt_id(order_id: str) -> str:
    return f"ofr_{_hex('ofr', order_id, length=32)}"


def _journal_id(session: date) -> str:
    return f"{_JOURNAL_PREFIX}{_hex('jnl', session.isoformat(), length=28)}"


def _assert_boundary(database_dsn: str) -> None:
    """superuser 로 자기 스택의 DB 에만 쓴다. 원격이나 다른 role 이면 시작하지 않는다."""

    if os.environ.get(_OFFLINE_DEMO_ENV, "").strip().lower() != "true":
        raise DemoHistorySeedError("DEMO_HISTORY_OFFLINE_DEMO_REQUIRED")
    parsed = urlparse(database_dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.username != "postgres"
        or parsed.hostname not in {"postgres", "127.0.0.1", "localhost"}
        or not (parsed.path or "").lstrip("/")
    ):
        raise DemoHistorySeedError("DEMO_HISTORY_DSN_BOUNDARY")


def _control(cursor: psycopg.Cursor[Any]) -> dict[str, Any]:
    """주문을 걸 계좌와 그 주문이 인용할 원칙 버전. 화면이 보는 계좌와 같아야 한다."""

    cursor.execute(
        "select account_id, principle_id, principle_version_id, principle_version"
        " from automation_control where user_id=%s",
        (_USER_ID,),
    )
    row = cursor.fetchone()
    if row is None or not row[2]:
        raise DemoHistorySeedError("DEMO_HISTORY_AUTOMATION_CONTROL_MISSING")
    return {
        "account_id": row[0],
        "principle_id": row[1],
        "principle_version_id": row[2],
        "principle_version": int(row[3]),
    }


def _positions(cursor: psycopg.Cursor[Any], start: date, end: date) -> list[dict[str, Any]]:
    cursor.execute(
        "select entry_session, symbol, entry_filled_quantity, entry_average_fill_price_krw,"
        " status, exit_reason, exit_filled_quantity, exit_average_fill_price_krw,"
        " realized_pnl_krw, closed_at"
        " from automation_positions"
        " where user_id=%s and entry_session between %s and %s"
        "   and entry_average_fill_price_krw is not null and entry_filled_quantity > 0"
        " order by entry_session, symbol",
        (_USER_ID, start, end),
    )
    return [
        {
            "session": row[0],
            "symbol": row[1],
            "quantity": int(row[2]),
            "price": int(row[3]),
            "status": row[4],
            "exit_reason": row[5],
            "exit_quantity": int(row[6] or 0),
            "exit_price": int(row[7]) if row[7] else None,
            "realized": int(row[8]) if row[8] is not None else None,
            "closed_at": row[9],
        }
        for row in cursor.fetchall()
    ]


def _risk_items(symbol: str, quantity: int, price: int) -> list[dict[str, Any]]:
    """계약이 요구하는 riskItem 네 개. 값은 이 주문에서 실제로 계산되는 것들이다.

    `eventCodes` 와 `mappingVersion` 은 스키마에서는 선택이지만 조회가 쓰는
    `DecisionRiskItemProjection` 이 null 을 받지 않는다. 빠지면 판정이 저장돼 있어도
    조회가 실패한다.
    """

    amount = quantity * price
    return [
        {
            "metric": "order_amount_krw",
            "value": float(amount),
            "severity": "ALLOW",
            "source": "KIS",
            "eventCodes": [],
            "mappingVersion": _MAPPING_VERSION,
            "sourceRefs": [_digest("ref", "order_amount", symbol)],
        },
        {
            "metric": "daily_order_count",
            "value": 1.0,
            "severity": "ALLOW",
            "source": "INTERNAL",
            "eventCodes": [],
            "mappingVersion": _MAPPING_VERSION,
            "sourceRefs": [_digest("ref", "daily_order_count", symbol)],
        },
        {
            "metric": "asset_weight",
            "value": 0.2,
            "severity": "ALLOW",
            "source": "KIS",
            "eventCodes": [],
            "mappingVersion": _MAPPING_VERSION,
            "sourceRefs": [_digest("ref", "asset_weight", symbol)],
        },
        {
            "metric": "gold_etf_etn_weight",
            "value": 0.0,
            "severity": "ALLOW",
            "source": "INTERNAL",
            "eventCodes": [],
            "mappingVersion": _MAPPING_VERSION,
            "sourceRefs": [_digest("ref", "gold_weight", symbol)],
        },
    ]


def _result_json(
    *,
    decision_id: str,
    evaluation_id: str,
    control: dict[str, Any],
    symbol: str,
    quantity: int,
    price: int,
    created_at: datetime,
    valid_until: datetime,
) -> dict[str, Any]:
    """s2-3-decision-response/v1. 필수 항목만 채우고 선택 항목은 비운다."""

    return {
        "decisionId": decision_id,
        "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        "validUntil": valid_until.isoformat().replace("+00:00", "Z"),
        "principleId": control["principle_id"],
        "principleVersionId": control["principle_version_id"],
        "principleVersion": control["principle_version"],
        "portfolioSource": _BROKERAGE_MODE,
        "mode": "GUIDE",
        "enforcementAction": "NONE",
        "riskDecision": {
            "schemaVersion": _SCHEMA_VERSION,
            "evaluationId": evaluation_id,
            # 실제 평가기가 남기는 정본에는 판정 안에도 식별자와 유효기한이 다시 들어간다.
            # 조회는 이 정본을 그대로 역직렬화하므로 빠지면 읽기가 실패한다.
            "decisionId": decision_id,
            "validUntil": valid_until.isoformat().replace("+00:00", "Z"),
            "catalogVersion": 1,
            "readinessPolicyVersion": _READINESS_POLICY_VERSION,
            "decision": "ALLOW",
            "mode": "GUIDE",
            "canSubmitOrder": True,
            "principleVersionId": control["principle_version_id"],
            "principleVersion": control["principle_version"],
            "portfolioSource": _BROKERAGE_MODE,
            "semanticInputHash": _digest("semantic", decision_id),
            "snapshotArtifactHash": _digest("snapshot", decision_id),
            "violations": [],
            "issues": [],
            "warnings": [],
            "abstentions": [],
            "riskItems": _risk_items(symbol, quantity, price),
        },
    }


def _insert_decision(
    cursor: psycopg.Cursor[Any],
    *,
    control: dict[str, Any],
    session: date,
    symbol: str,
    side: str,
    quantity: int,
    price: int,
    at: datetime,
) -> tuple[str, str]:
    decision_id = _decision_id(session, symbol, side)
    evaluation_id = _evaluation_id(session, symbol, side)
    valid_until = at + timedelta(minutes=10)
    result = _result_json(
        decision_id=decision_id,
        evaluation_id=evaluation_id,
        control=control,
        symbol=symbol,
        quantity=quantity,
        price=price,
        created_at=at,
        valid_until=valid_until,
    )
    cursor.execute(
        """
        INSERT INTO decisions(
          decision_id,user_id,principle_version_id,symbol,side,outcome,mode,created_at,
          valid_until,evaluation_id,principle_id,principle_version,portfolio_source,
          can_submit_order,enforcement_action,evaluation_as_of,result_schema_version,
          snapshot_schema_version,catalog_version,readiness_policy_version,
          mapping_versions_json,semantic_input_hash,snapshot_artifact_hash,result_json
        ) VALUES (%s,%s,%s,%s,%s,'ALLOW','GUIDE',%s,%s,%s,%s,%s,%s,true,'NONE',%s,%s,%s,1,%s,
                  '{}'::jsonb,%s,%s,%s)
        ON CONFLICT (decision_id) DO NOTHING
        """,
        (
            decision_id,
            _USER_ID,
            control["principle_version_id"],
            symbol,
            side,
            at,
            valid_until,
            evaluation_id,
            control["principle_id"],
            control["principle_version"],
            _BROKERAGE_MODE,
            at,
            _RESULT_SCHEMA_VERSION,
            _SNAPSHOT_SCHEMA_VERSION,
            _READINESS_POLICY_VERSION,
            result["riskDecision"]["semanticInputHash"],
            result["riskDecision"]["snapshotArtifactHash"],
            json.dumps(result, ensure_ascii=False),
        ),
    )
    # 판정 조회는 `decision_artifacts` 와 조인한다(`read_decision_owner_projection`).
    # 이 행이 없으면 판정은 저장돼 있어도 화면에서 NOT_FOUND 로 나온다.
    cursor.execute(
        """
        INSERT INTO decision_artifacts(
          decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
          semantic_input_hash,snapshot_artifact_hash,created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (decision_id) DO NOTHING
        """,
        (
            decision_id,
            evaluation_id,
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            json.dumps(
                {
                    "schemaVersion": _SNAPSHOT_SCHEMA_VERSION,
                    "symbol": symbol,
                    "quantity": quantity,
                    "priceKrw": price,
                    "asOf": at.isoformat().replace("+00:00", "Z"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            result["riskDecision"]["semanticInputHash"],
            result["riskDecision"]["snapshotArtifactHash"],
            at,
        ),
    )
    return decision_id, evaluation_id


def _insert_order(
    cursor: psycopg.Cursor[Any],
    *,
    control: dict[str, Any],
    session: date,
    symbol: str,
    side: str,
    quantity: int,
    price: int,
    at: datetime,
    decision_id: str,
    evaluation_id: str,
) -> str:
    """체결까지 끝난 지정가 주문 한 건과 그 사건 기록.

    주문은 `ACCEPTED` 로 넣고 나서 체결 상태로 올린다. `orders_fill_projection_guard`
    트리거가 INSERT 마다 체결 투영을 초기 상태(`filled=0`, `leaves=quantity`,
    `NOT_APPLICABLE`)로 되돌리기 때문에 처음부터 `FILLED` 로 넣으면
    `orders_filled_status_check` 에 걸린다. 같은 트리거의 UPDATE 분기는
    `CANCELLED`/`REJECTED` 에만 손대므로 체결로 올리는 UPDATE 는 그대로 통과한다.
    실제 운용도 제출 뒤 체결 관측이 들어와야 이 값이 채워진다 - 같은 순서다.
    """

    order_id = _order_id(session, symbol, side)
    account_id = control["account_id"]
    intent = {
        "side": side,
        "symbol": symbol,
        "quantity": str(quantity),
        "orderType": "LIMIT",
        "timeframe": "1d",
        "strategyId": "strategy_rule_lstm_v1",
        "estimatedPrice": str(price),
        "estimatedAmount": str(quantity * price),
    }
    canonical = {
        "accountId": account_id,
        "brokerageMode": _BROKERAGE_MODE,
        "orderId": order_id,
        "status": "ACCEPTED",
        "submittedAt": at.isoformat().replace("+00:00", "Z"),
    }
    cursor.execute(
        """
        INSERT INTO orders(
          order_id,user_id,account_id,decision_id,symbol,side,order_type,quantity,
          submitted_price_krw,status,order_intent_json,submitted_at,created_at,updated_at,
          decision_evaluation_id,brokerage_mode,account_scope_hash,idempotency_scope_hash,
          idempotency_owner_scope_hash,request_hash,result_canonical_json,acknowledged_by,
          acknowledged_at,filled_quantity,leaves_quantity,unfilled_terminated_quantity,
          average_fill_price_krw,reconciliation_status,reconciled_at,
          provider_order_ref_hash,provider_tr_id,provider_received_at
        ) VALUES (%s,%s,%s,%s,%s,%s,'LIMIT',%s,%s,'ACCEPTED',%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,0,0,%s,'MATCHED',%s,%s,%s,%s)
        ON CONFLICT (order_id) DO NOTHING
        """,
        (
            order_id,
            _USER_ID,
            account_id,
            decision_id,
            symbol,
            side,
            quantity,
            price,
            json.dumps(intent, ensure_ascii=False),
            at,
            at,
            at + timedelta(seconds=90),
            evaluation_id,
            _BROKERAGE_MODE,
            _digest("account-scope", account_id),
            _digest("idem-scope", order_id),
            _digest("idem-owner-scope", order_id),
            _digest("request", order_id),
            json.dumps(canonical, ensure_ascii=False, separators=(",", ":")),
            _USER_ID,
            at,
            quantity,
            price,
            at + timedelta(seconds=90),
            _digest("provider-ref", order_id),
            _TR_ID,
            at + timedelta(seconds=30),
        ),
    )
    for sequence, (event_type, status, offset) in enumerate(
        (
            ("MOCK_ORDER_SUBMITTED", "SUBMITTED", 0),
            ("MOCK_ORDER_ACCEPTED", "ACCEPTED", 30),
            ("MOCK_ORDER_FILLED", "FILLED", 90),
        ),
        start=1,
    ):
        cursor.execute(
            """
            INSERT INTO order_events(
              order_event_id,order_id,event_type,event_status,payload_json,created_at,event_seq
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            (
                _event_id(order_id, sequence),
                order_id,
                event_type,
                status,
                json.dumps(
                    {
                        "orderId": order_id,
                        "status": status,
                        "brokerageMode": _BROKERAGE_MODE,
                    },
                    ensure_ascii=False,
                ),
                at + timedelta(seconds=offset),
                sequence,
            ),
        )
    cursor.execute(
        """
        UPDATE orders
           SET status='FILLED', filled_quantity=quantity, leaves_quantity=0,
               unfilled_terminated_quantity=0, average_fill_price_krw=%s,
               reconciliation_status='MATCHED', reconciled_at=%s, updated_at=%s
         WHERE order_id=%s AND status='ACCEPTED'
        """,
        (price, at + timedelta(seconds=90), at + timedelta(seconds=90), order_id),
    )
    return order_id


def _insert_fill(
    cursor: psycopg.Cursor[Any],
    *,
    order_id: str,
    quantity: int,
    price: int,
    at: datetime,
) -> None:
    """체결 관측 한 건과 그 적용 영수증.

    `read_owned_order_fills` 는 영수증이 `APPLIED` 인 관측만 돌려준다. 관측만 넣으면
    화면의 '최근 체결' 은 비어 있는 채로 남는다.
    """

    observation_id = _observation_id(order_id)
    filled_at = at + timedelta(seconds=90)
    cursor.execute(
        """
        INSERT INTO order_fill_observations(
          observation_id,order_id,provider_exec_ref_hash,exec_type,fill_quantity,
          fill_price_krw,cumulative_quantity,leaves_quantity,average_fill_price_krw,
          observed_at,received_at,schema_version,source_version,source_ref,completeness,
          artifact_hash
        ) VALUES (%s,%s,%s,'FILL',%s,%s,%s,0,%s,%s,%s,'1','kis-mock-demo-v1',
                  'kis-mock-demo','COMPLETE',%s)
        ON CONFLICT (observation_id) DO NOTHING
        """,
        (
            observation_id,
            order_id,
            _digest("exec-ref", order_id),
            quantity,
            price,
            quantity,
            price,
            filled_at,
            filled_at + timedelta(seconds=1),
            _digest("artifact", order_id),
        ),
    )
    cursor.execute(
        """
        INSERT INTO order_fill_application_receipts(
          receipt_id,observation_id,order_id,outcome,invalid_reason,applied_at
        ) VALUES (%s,%s,%s,'APPLIED',NULL,%s)
        ON CONFLICT (receipt_id) DO NOTHING
        """,
        (_receipt_id(order_id), observation_id, order_id, filled_at + timedelta(seconds=2)),
    )


def _insert_journal(
    cursor: psycopg.Cursor[Any],
    *,
    session: date,
    entries: list[dict[str, Any]],
    decision_id: str,
    order_id: str,
    at: datetime,
) -> None:
    """그날 무엇을 왜 했는지. 본문은 그날 실제로 남은 행에서만 만든다."""

    lines: list[str] = []
    tags: list[str] = []
    for entry in entries:
        if entry["kind"] == "ENTRY":
            lines.append(
                f"{entry['symbol']} {entry['quantity']}주를 {entry['price']:,}원에 담았다."
                " 원칙 판정은 ALLOW 였고 주문가능금액과 1일 주문 수 상한 모두 여유가 있었다."
            )
            tags.append("진입")
        else:
            label = _EXIT_LABEL.get(entry["reason"], entry["reason"])
            realized = entry["realized"]
            sign = "이익" if realized >= 0 else "손실"
            lines.append(
                f"{entry['symbol']}는 {label}에 걸려 {entry['price']:,}원에 정리했다."
                f" 실현 {sign} {abs(realized):,}원."
            )
            tags.append("청산")
    body = "\n".join(lines)[:8192]
    cursor.execute(
        """
        INSERT INTO journals(
          journal_id,user_id,decision_id,order_id,title,body,tags,source_json,
          created_at,updated_at,owner_scope,version
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
        ON CONFLICT (journal_id) DO NOTHING
        """,
        (
            _journal_id(session),
            _USER_ID,
            decision_id,
            order_id,
            f"{session.isoformat()} 자동운용 기록",
            body,
            sorted(set(tags)),
            json.dumps({"origin": "DEMO_HISTORY", "contractId": "journal.v1"}, ensure_ascii=False),
            at,
            at,
            _digest("owner-scope", _USER_ID),
        ),
    )


def seed(*, database_dsn: str, start: date, end: date) -> dict[str, int]:
    _assert_boundary(database_dsn)
    counts = {"decisions": 0, "orders": 0, "fills": 0, "journals": 0}
    with psycopg.connect(database_dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user, session_user")
            row = cursor.fetchone()
            if row is None or row[0] != "postgres" or row[1] != "postgres":
                raise DemoHistorySeedError("DEMO_HISTORY_ROLE_BOUNDARY")
            cursor.execute("select pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK,))
            control = _control(cursor)
            positions = _positions(cursor, start, end)
            if not positions:
                raise DemoHistorySeedError("DEMO_HISTORY_NO_POSITIONS")

            # 세션별로 그날 일어난 일을 모아 학습일지 한 편으로 묶는다.
            by_session: dict[date, list[dict[str, Any]]] = {}
            for position in positions:
                session = position["session"]
                opened = datetime.combine(session, _OPEN_TIME, _KST).astimezone(UTC)
                decision_id, evaluation_id = _insert_decision(
                    cursor,
                    control=control,
                    session=session,
                    symbol=position["symbol"],
                    side="BUY",
                    quantity=position["quantity"],
                    price=position["price"],
                    at=opened,
                )
                order_id = _insert_order(
                    cursor,
                    control=control,
                    session=session,
                    symbol=position["symbol"],
                    side="BUY",
                    quantity=position["quantity"],
                    price=position["price"],
                    at=opened + timedelta(seconds=20),
                    decision_id=decision_id,
                    evaluation_id=evaluation_id,
                )
                _insert_fill(
                    cursor,
                    order_id=order_id,
                    quantity=position["quantity"],
                    price=position["price"],
                    at=opened + timedelta(seconds=20),
                )
                counts["decisions"] += 1
                counts["orders"] += 1
                counts["fills"] += 1
                by_session.setdefault(session, []).append(
                    {
                        "kind": "ENTRY",
                        "symbol": position["symbol"],
                        "quantity": position["quantity"],
                        "price": position["price"],
                        "decision_id": decision_id,
                        "order_id": order_id,
                    }
                )

                if position["status"] != "CLOSED" or not position["exit_reason"]:
                    continue
                # 청산은 종료된 날 기록한다. 청산일이 창 밖이면 남기지 않는다.
                closed_at = position["closed_at"]
                exit_session = closed_at.astimezone(_KST).date() if closed_at else session
                if not start <= exit_session <= end:
                    continue
                exit_quantity = position["exit_quantity"] or position["quantity"]
                exit_price = position["exit_price"] or position["price"]
                exit_open = datetime.combine(exit_session, _OPEN_TIME, _KST).astimezone(UTC)
                exit_decision_id, exit_evaluation_id = _insert_decision(
                    cursor,
                    control=control,
                    session=exit_session,
                    symbol=position["symbol"],
                    side="SELL",
                    quantity=exit_quantity,
                    price=exit_price,
                    at=exit_open + timedelta(minutes=1),
                )
                exit_order_id = _insert_order(
                    cursor,
                    control=control,
                    session=exit_session,
                    symbol=position["symbol"],
                    side="SELL",
                    quantity=exit_quantity,
                    price=exit_price,
                    at=exit_open + timedelta(minutes=1, seconds=20),
                    decision_id=exit_decision_id,
                    evaluation_id=exit_evaluation_id,
                )
                _insert_fill(
                    cursor,
                    order_id=exit_order_id,
                    quantity=exit_quantity,
                    price=exit_price,
                    at=exit_open + timedelta(minutes=1, seconds=20),
                )
                counts["decisions"] += 1
                counts["orders"] += 1
                counts["fills"] += 1
                by_session.setdefault(exit_session, []).append(
                    {
                        "kind": "EXIT",
                        "symbol": position["symbol"],
                        "quantity": exit_quantity,
                        "price": exit_price,
                        "reason": position["exit_reason"],
                        "realized": position["realized"] or 0,
                        "decision_id": exit_decision_id,
                        "order_id": exit_order_id,
                    }
                )

            for session, entries in sorted(by_session.items()):
                first = entries[0]
                _insert_journal(
                    cursor,
                    session=session,
                    entries=entries,
                    decision_id=first["decision_id"],
                    order_id=first["order_id"],
                    at=datetime.combine(session, time(16, 0), _KST).astimezone(UTC),
                )
                counts["journals"] += 1
        connection.commit()
    return counts


def purge(*, database_dsn: str) -> dict[str, int]:
    """시연 데이터만 되돌린다. 접두사가 유일한 기준이라 실제 운용 기록은 건드리지 않는다."""

    _assert_boundary(database_dsn)
    counts = {"journals": 0, "fills": 0, "orders": 0, "decisions": 0}
    with psycopg.connect(database_dsn, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK,))
            # 순서가 곧 제약이다. 영수증과 관측은 ON DELETE RESTRICT 라 주문보다 먼저 지운다.
            cursor.execute(
                "DELETE FROM journals WHERE journal_id LIKE %s", (f"{_JOURNAL_PREFIX}%",)
            )
            counts["journals"] = cursor.rowcount
            cursor.execute(
                "DELETE FROM order_fill_application_receipts WHERE order_id IN"
                " (SELECT order_id FROM orders WHERE decision_id LIKE %s)",
                (f"{_DECISION_PREFIX}%",),
            )
            cursor.execute(
                "DELETE FROM order_fill_observations WHERE order_id IN"
                " (SELECT order_id FROM orders WHERE decision_id LIKE %s)",
                (f"{_DECISION_PREFIX}%",),
            )
            counts["fills"] = cursor.rowcount
            cursor.execute(
                "DELETE FROM orders WHERE decision_id LIKE %s", (f"{_DECISION_PREFIX}%",)
            )
            counts["orders"] = cursor.rowcount
            cursor.execute(
                "DELETE FROM decision_artifacts WHERE decision_id LIKE %s",
                (f"{_DECISION_PREFIX}%",),
            )
            cursor.execute(
                "DELETE FROM decisions WHERE decision_id LIKE %s", (f"{_DECISION_PREFIX}%",)
            )
            counts["decisions"] = cursor.rowcount
        connection.commit()
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "purge"))
    parser.add_argument("--start", default=os.environ.get("P1_DEMO_HISTORY_START", "2026-08-18"))
    parser.add_argument("--end", default=os.environ.get("P1_DEMO_HISTORY_END", "2026-09-04"))
    arguments = parser.parse_args(argv)
    database_dsn = os.environ.get(_DSN_ENV, "").strip()
    if not database_dsn:
        raise SystemExit(f"{_DSN_ENV} is required")
    if arguments.command == "purge":
        counts = purge(database_dsn=database_dsn)
        print(
            "P1_DEMO_HISTORY=PURGED "
            + " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        )
        return 0
    counts = seed(
        database_dsn=database_dsn,
        start=date.fromisoformat(arguments.start),
        end=date.fromisoformat(arguments.end),
    )
    print(
        "P1_DEMO_HISTORY=SEEDED "
        + " ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
