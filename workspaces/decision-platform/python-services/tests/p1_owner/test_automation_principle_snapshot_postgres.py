"""격리 PostgreSQL에서 claim/편집/재개/다음 세션의 원칙 고정을 검증한다."""

from datetime import date
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import time
import json
import hashlib

import psycopg
import pytest

from tests.conftest import PostgresTestCluster


@pytest.mark.parametrize("current_policy", [False, True])
def test_claim_pins_version_replay_preserves_it_and_next_session_adopts_latest(
    isolated_postgres_cluster: PostgresTestCluster,
    current_policy: bool,
) -> None:
    cluster = isolated_postgres_cluster
    owner, principle = "usr_demo_user", "prc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with psycopg.connect(cluster["admin_dsn"]) as db:
        db.execute(
            """INSERT INTO principles(principle_id,user_id,preset_id,title,mode,status,current_version)
            VALUES (%s,%s,'balanced','snapshot','GUIDE','ACTIVE',1)""",
            (principle, owner),
        )
        for version in (1, 2, 3):
            db.execute(
                """INSERT INTO principle_versions(principle_version_id,principle_id,version,
                rules_json,created_by,preset_id,title,mode,status,changed_fields)
                SELECT %s,%s,%s,(SELECT jsonb_agg(CASE WHEN rule->>'ruleId'='max_single_order_amount'
                    THEN rule||jsonb_build_object('enabled',true,'threshold',%s::integer*50000)
                    ELSE rule END ORDER BY ordinal)
                  FROM jsonb_array_elements(rules_json) WITH ORDINALITY AS item(rule,ordinal)),
                  %s,'balanced','snapshot','GUIDE','ACTIVE',ARRAY['rules']
                FROM principle_presets WHERE preset_id='balanced'""",
                ("pvr_" + str(version) * 32, principle, version, version, owner),
            )
        db.execute(
            """INSERT INTO automation_control(user_id,control_state,version,brokerage_mode,
            account_id,principle_id,strategy_id,baseline_account_digest,certification_status,kill_switch_active)
            VALUES(%s,'ARMED',1,'KIS_MOCK','acct_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',%s,'strategy_rule_lstm_v1',
            repeat('a',64),'VALID',false)""",
            (owner, principle),
        )
        if current_policy:
            db.execute(
                """INSERT INTO automation_policy_versions(policy_id,version,user_id,
                capital_limit_krw,stop_loss_bps,take_profit_bps,risk_profile,principle_id,
                principle_version_id,principle_version,max_holding_sessions,atr_period,atr_multiplier_milli,model_sell_enabled)
                VALUES('auto_pol_'||repeat('d',32),1,%s,1000000,500,1000,
                public.p1_automation_policy_profile_v2(500,1000,5,14,2000,true),%s,
                'pvr_11111111111111111111111111111111',1,5,14,2000,true)""",
                (owner, principle),
            )
            db.execute(
                """UPDATE automation_control SET policy_id='auto_pol_'||repeat('d',32),
                policy_version=1,principle_version_id='pvr_11111111111111111111111111111111',principle_version=1,
                team_b_integrity_receipt_sha256_v2=repeat('e',64),initial_account_digest_v2=repeat('a',64),
                expected_account_digest_v2=repeat('a',64),expected_account_projection_v2=jsonb_build_object(
                  'accountId','acct_'||repeat('b',32),'cashKrw',1000000,'positions','[]'::jsonb,'schemaVersion','2')
                WHERE user_id=%s""",
                (owner,),
            )
        for index, session in enumerate((date(2026, 9, 8), date(2026, 9, 9))):
            db.execute(
                """INSERT INTO automation_runtime_schedule(schedule_id,user_id,session_date,
                control_version,schedule_state,run_at)
                VALUES(%s,%s,%s,1,'ARMED',%s::date+time '09:30')""",
                ("auto_sched_" + str(index + 1) * 32, owner, session, session),
            )

    def claim(session: date) -> tuple:
        with psycopg.connect(cluster["automation_runtime_dsn"]) as db:
            return db.execute(
                "SELECT * FROM p1_claim_automation_session_v1(%s,%s)",
                (session, "sha256:" + "c" * 64),
            ).fetchone()

    first_version = 2 if current_policy else 1
    next_version = first_version + 1
    if current_policy:
        # 편집이 먼저 잠그면 claim은 commit 뒤의 최신 버전을 사용한다.
        with (
            psycopg.connect(cluster["admin_dsn"]) as editor,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            editor.execute(
                "UPDATE principles SET current_version=2 WHERE principle_id=%s", (principle,)
            )
            future = pool.submit(claim, date(2026, 9, 8))
            try:
                deadline = time.monotonic() + 5
                with psycopg.connect(cluster["admin_dsn"], autocommit=True) as observer:
                    while time.monotonic() < deadline:
                        locked = observer.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename='decision_automation_runtime' AND wait_event_type='Lock')"
                        ).fetchone()[0]
                        if locked:
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail("claim did not wait for principle edit lock")
                assert not future.done()
            finally:
                editor.commit()
            first = future.result(timeout=5)
    else:
        first = claim(date(2026, 9, 8))
    assert first is not None and first[-1] is False
    with psycopg.connect(cluster["admin_dsn"]) as db:
        db.execute(
            "UPDATE principles SET current_version=%s WHERE principle_id=%s",
            (next_version, principle),
        )
        assert db.execute(
            "SELECT principle_version FROM automation_runs WHERE run_id=%s", (first[1],)
        ).fetchone() == (first_version,)
    replay = claim(date(2026, 9, 8))
    assert replay[1] == first[1] and replay[-1] is True
    if current_policy:
        from app.p1_owner.automation_runtime import inputs_from_state
        from app.p1_owner.automation import _variable_buy_quantity

        with psycopg.connect(cluster["automation_runtime_dsn"]) as db:
            state = json.loads(
                db.execute(
                    "SELECT p1_read_automation_runtime_state_v4(%s,%s)",
                    (first[1], "sha256:" + "c" * 64),
                ).fetchone()[0]
            )
        assert state["principleActiveCurrent"] is True
        assert state["principleMaxSingleOrderKrw"] == first_version * 50000
        sizing = inputs_from_state(
            state,
            risk_allow=True,
            buyable_quantity=100,
            buyable_amount_krw=1_000_000,
        )
        assert _variable_buy_quantity(sizing, 50000) == first_version
        with psycopg.connect(cluster["admin_dsn"]) as db:
            db.execute(
                """INSERT INTO automation_capital_policy_versions_v1(
                user_id,version,reinvest_realized_pnl,cash_buffer_bps,rebalance_deviation_bps,
                minimum_adjustment_krw,max_orders_per_session,effective_from_session,transition_started_at)
                VALUES(%s,1,true,100,200,10000,3,'2026-09-08',statement_timestamp())""",
                (owner,),
            )
        intent = {
            "symbol": "005930",
            "side": "BUY",
            "orderType": "LIMIT",
            "quantity": 1,
            "estimatedPrice": 50000,
            "estimatedAmount": 50000,
            "timeframe": "1d",
            "strategyId": "strategy_rule_lstm_v1",
        }
        intent_hash = hashlib.sha256(
            json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        snapshot = {
            "automationPolicyId": "auto_pol_" + "d" * 32,
            "automationPolicyVersion": 1,
            "capitalPolicyVersion": 1,
            "principleVersionId": "pvr_" + str(first_version) * 32,
            "principleVersion": first_version,
            "configuredCapitalKrw": 1_000_000,
            "realizedPnlSinceTransitionKrw": 0,
            "brokerBuyableCashKrw": 1_000_000,
            "botPositionMarketValueKrw": 0,
            "reservedBuyCashKrw": 0,
            "allocationCapKrw": 1_000_000,
            "investableCapKrw": 990_000,
            "targetPerPositionKrw": 198_000,
            "unusedCashReason": "BUFFER_OR_NO_MORE_ELIGIBLE_CANDIDATES",
            "snapshotSha256": "e" * 64,
        }
        orders = [
            {
                "ordinal": ordinal,
                "executionId": "auto_exec_" + str(ordinal) * 32,
                "phase": "ENTRY",
                "symbol": "005930" if ordinal == 1 else "000660",
                "side": "BUY",
                "quantity": 1,
                "limitPriceKrw": 50000,
                "currentQuantity": 0,
                "targetQuantity": 1,
                "exactIntent": intent if ordinal == 1 else (intent | {"symbol": "000660"}),
                "exactIntentSha256": intent_hash if ordinal == 1 else "f" * 64,
                "idempotencyKeyHash": "sha256:" + str(ordinal) * 64,
            }
            for ordinal in (1, 2)
        ]
        with psycopg.connect(cluster["automation_runtime_dsn"]) as db:
            assert db.execute(
                "SELECT p1_stage_automation_portfolio_plan_v1(%s,%s,%s::jsonb,%s::jsonb)",
                (first[1], "sha256:" + "c" * 64, json.dumps(snapshot), json.dumps(orders)),
            ).fetchone() == ("INSERTED",)
            assert db.execute(
                "SELECT p1_begin_automation_portfolio_execution_v1(%s,%s,1,%s)",
                (first[1], "sha256:" + "c" * 64, "sha256:" + "1" * 64),
            ).fetchone() == ("SUBMIT",)
            assert db.execute(
                "SELECT p1_finish_automation_portfolio_execution_v1(%s,%s,1,'PENDING_RECONCILIATION',NULL,NULL)",
                (first[1], "sha256:" + "c" * 64),
            ).fetchone() == ("UPDATED",)
            db.commit()
            with pytest.raises(psycopg.errors.SerializationFailure):
                db.execute(
                    "SELECT p1_begin_automation_portfolio_execution_v1(%s,%s,2,%s)",
                    (first[1], "sha256:" + "c" * 64, "sha256:" + "2" * 64),
                )
            db.rollback()
        with psycopg.connect(cluster["automation_runtime_dsn"]) as db:
            assert db.execute(
                "SELECT p1_finish_automation_portfolio_execution_v1(%s,%s,1,'FILLED',NULL,NULL)",
                (first[1], "sha256:" + "c" * 64),
            ).fetchone() == ("UPDATED",)
            assert db.execute(
                "SELECT p1_begin_automation_portfolio_execution_v1(%s,%s,2,%s)",
                (first[1], "sha256:" + "c" * 64, "sha256:" + "2" * 64),
            ).fetchone() == ("SUBMIT",)

    # 발급기 자체는 별도 인증 테스트의 범위다. 여기서는 발급 완료 capability를
    # seed하고 실제 consume/RLS/claim binding이 고정 버전을 읽는지 검증한다.
    def read_snapshot(claim_hash: str, requested_owner: str = owner) -> tuple | None:
        nonce = uuid4().hex
        token = "cap2_" + nonce * 2 + "." + "b" * 86
        with psycopg.connect(cluster["admin_dsn"]) as db:
            db.execute(
                """INSERT INTO actor_request_capability(token_hash,actor_user_id,actor_role,
                actor_security_version,expires_at,operation,target_kind,target_id,payload_hash,
                request_id,transaction_id,nonce,signature)
                SELECT 'sha256:'||encode(digest(%s,'sha256'),'hex'),user_id,role,security_version,
                statement_timestamp()+interval '25 seconds','READ_ACTIVE_PRINCIPLE','PRINCIPLE',%s,
                'sha256:'||encode(digest(%s,'sha256'),'hex'),%s,%s,%s,'ed25519:'||repeat('b',86)
                FROM users WHERE user_id=%s""",
                (token, principle, principle, "req_" + nonce, "txn_" + nonce, nonce, owner),
            )
        with psycopg.connect(cluster["app_dsn"]) as db:
            return db.execute(
                "SELECT * FROM read_automation_principle_snapshot_authorized(%s,%s,%s,%s,%s)",
                (token, requested_owner, principle, first[1], claim_hash),
            ).fetchone()

    assert read_snapshot("sha256:" + "c" * 64)[2] == first_version
    assert read_snapshot("sha256:" + "d" * 64) is None
    assert read_snapshot("sha256:" + "c" * 64, "usr_other_owner") is None
    with pytest.raises(psycopg.errors.SerializationFailure):
        claim(date(2026, 9, 9))
    with psycopg.connect(cluster["admin_dsn"]) as db:
        db.execute(
            "UPDATE automation_runtime_claim SET claim_state='RELEASED',released_at=now() WHERE run_id=%s",
            (first[1],),
        )
    second = claim(date(2026, 9, 9))
    with psycopg.connect(cluster["admin_dsn"]) as db:
        if current_policy:
            assert db.execute(
                "SELECT principle_version,policy_version FROM automation_control WHERE user_id=%s",
                (owner,),
            ).fetchone() == (next_version, 1)
        assert db.execute(
            "SELECT principle_version FROM automation_runs WHERE run_id=%s", (second[1],)
        ).fetchone() == (next_version,)
        assert db.execute(
            "SELECT principle_version FROM automation_runs WHERE run_id=%s", (first[1],)
        ).fetchone() == (first_version,)
    with psycopg.connect(cluster["admin_dsn"]) as db, pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE automation_runs SET principle_version=999 WHERE run_id=%s", (first[1],))
