from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlparse

import psycopg


_USER_ID = "usr_demo_user"
_PRINCIPLE_ID = "prc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_PRINCIPLE_VERSION_ID = "pvr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_PAPER_ACCOUNT_ID = "acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_KIS_ACCOUNT_ID = "acct_cccccccccccccccccccccccccccccccc"
_KIS_SCOPE = "c" * 64
_RAG_ANSWER_ID = "rag_team_a_fixture_0001"
_AUTOMATION_RUN_ID = "auto_run_team_a_news_veto_0001"


_RESET_STATEMENTS = (
    "DELETE FROM journal_idempotency WHERE user_id=%s",
    "DELETE FROM journals WHERE user_id=%s",
    "DELETE FROM automation_control_idempotency WHERE user_id=%s",
    "DELETE FROM automation_activation_gate WHERE user_id=%s",
    "DELETE FROM automation_control WHERE user_id=%s",
    "DELETE FROM order_fill_application_receipts WHERE order_id IN (SELECT order_id FROM orders WHERE user_id=%s)",
    "DELETE FROM order_fill_observations WHERE order_id IN (SELECT order_id FROM orders WHERE user_id=%s)",
    "DELETE FROM paper_order_events WHERE order_id IN (SELECT order_id FROM orders WHERE user_id=%s)",
    "DELETE FROM order_events WHERE order_id IN (SELECT order_id FROM orders WHERE user_id=%s)",
    "DELETE FROM orders WHERE user_id=%s",
    "DELETE FROM decision_artifacts WHERE decision_id IN (SELECT decision_id FROM decisions WHERE user_id=%s)",
    "DELETE FROM decision_invalidations WHERE owner_user_id=%s",
    "DELETE FROM decision_traces WHERE decision_id IN (SELECT decision_id FROM decisions WHERE user_id=%s)",
    "DELETE FROM decision_violations WHERE decision_id IN (SELECT decision_id FROM decisions WHERE user_id=%s)",
    "DELETE FROM decision_idempotency_results WHERE decision_id IN (SELECT decision_id FROM decisions WHERE user_id=%s)",
    "DELETE FROM decisions WHERE user_id=%s",
    "DELETE FROM principle_versions WHERE principle_id IN (SELECT principle_id FROM principles WHERE user_id=%s)",
    "DELETE FROM principles WHERE user_id=%s",
    "DELETE FROM rag_v2_answer_history WHERE answer_id=%s AND owner_user_id=%s",
    "DELETE FROM portfolio_position_observations WHERE balance_observation_id IN (SELECT observation_id FROM portfolio_balance_observations WHERE owner_user_id=%s AND source_version='team-a-acceptance-v1')",
    "DELETE FROM portfolio_balance_observations WHERE owner_user_id=%s AND source_version='team-a-acceptance-v1'",
    "DELETE FROM deterministic_risk_observations WHERE owner_user_id=%s AND source_version='team-a-acceptance-v1'",
    "DELETE FROM daily_order_count_observations WHERE owner_user_id=%s AND source_version='team-a-acceptance-v1'",
    "DELETE FROM market_quote_observations WHERE source_version='team-a-acceptance-v1'",
    "DELETE FROM instrument_catalog_observations WHERE source_version='team-a-acceptance-v1'",
    "DELETE FROM paper_accounts WHERE account_id=%s AND user_id=%s",
    "DELETE FROM user_sessions WHERE user_id=%s",
)


def _dsn(environment: Mapping[str, str]) -> str:
    if environment.get("P1_OFFLINE_DEMO") != "true":
        raise ValueError("offline_demo_required")
    value = environment.get("P1_TEAM_A_ACCEPTANCE_DATABASE_DSN", "")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "postgres"
        or parsed.username != "postgres"
        or parsed.path != "/capstone_p1"
        or not parsed.password
    ):
        raise ValueError("acceptance_dsn_boundary")
    return value


def _fixed_instant(environment: Mapping[str, str]) -> str:
    raw = environment.get("P1_TEAM_A_ACCEPTANCE_FIXED_CLOCK", "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("acceptance_fixed_clock") from error
    if (
        parsed.tzinfo is None
        or abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()) > 300
    ):
        raise ValueError("acceptance_fixed_clock_window")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reset(cursor: psycopg.Cursor[object]) -> None:
    for statement in _RESET_STATEMENTS:
        if "rag_v2_answer_history" in statement:
            cursor.execute(statement, (_RAG_ANSWER_ID, _USER_ID))
        elif "paper_accounts WHERE" in statement:
            cursor.execute(statement, (_PAPER_ACCOUNT_ID, _USER_ID))
        elif "%s" not in statement:
            cursor.execute(statement)
        else:
            cursor.execute(statement, (_USER_ID,))


def _seed(cursor: psycopg.Cursor[object]) -> None:
    cursor.execute(
        """
        INSERT INTO principles(principle_id,user_id,preset_id,title,mode,status,current_version)
        VALUES (%s,%s,'balanced','Team A acceptance principle','GUIDE','ACTIVE',1)
        """,
        (_PRINCIPLE_ID, _USER_ID),
    )
    cursor.execute(
        """
        INSERT INTO principle_versions(
          principle_version_id,principle_id,version,preset_id,title,mode,status,
          rules_json,changed_fields,created_by
        )
        SELECT %s,%s,1,preset_id,'Team A acceptance principle','GUIDE','ACTIVE',rules_json,
               ARRAY['presetId','title','mode','status','rules'],%s
        FROM principle_presets WHERE preset_id='balanced'
        """,
        (_PRINCIPLE_VERSION_ID, _PRINCIPLE_ID, _USER_ID),
    )
    cursor.execute(
        """
        INSERT INTO paper_accounts(
          account_id,user_id,name,cash_balance,currency,status,created_at,updated_at,
          owner_scope_hash,margin_requirement_krw
        ) VALUES (%s,%s,'Team A acceptance paper',10000000,'KRW','ACTIVE',
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,repeat('a',64),0)
        """,
        (_PAPER_ACCOUNT_ID, _USER_ID),
    )
    cursor.execute(
        """
        INSERT INTO market_quote_observations(
          observation_id,symbol,source,price_krw,bid_krw,ask_krw,completeness,observed_at,
          received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
        ) VALUES ('quote-team-a-acceptance','005930','KIS_MOCK',70000,69900,70000,'COMPLETE',
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,'market-quote-observation.v1',
          'team-a-acceptance-v1','{"symbol":"005930"}'::jsonb,repeat('1',64),repeat('2',64))
        """
    )
    cursor.execute(
        """
        INSERT INTO instrument_catalog_observations(
          observation_id,symbol,is_etf_etn,is_gold_etf_etn,product_risk_score,catalog_version,
          observed_at,received_at,completeness,schema_version,source_version,payload_json,
          source_ref,artifact_hash
        ) VALUES ('instrument-team-a-acceptance','005930',false,false,null,'team-a-catalog-v1',
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,'COMPLETE','instrument-catalog-observation.v1',
          'team-a-acceptance-v1','{"symbol":"005930"}'::jsonb,repeat('3',64),repeat('4',64))
        """
    )
    cursor.execute(
        """
        INSERT INTO portfolio_balance_observations(
          observation_id,owner_user_id,account_scope_hash,source,context_status,cash_krw,
          portfolio_equity_krw,margin_requirement_krw,completeness,position_count,observed_at,
          received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
        ) VALUES ('balance-team-a-acceptance',%s,%s,'KIS_MOCK','ACTIVE',10000000,10000000,0,
          'COMPLETE',0,current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          'portfolio-balance-observation.v1','team-a-acceptance-v1',
          '{"ownerScopeHash":"sanitized"}'::jsonb,repeat('5',64),repeat('6',64))
        """,
        (_USER_ID, _KIS_SCOPE),
    )
    cursor.execute(
        """
        INSERT INTO deterministic_risk_observations(
          observation_id,owner_user_id,owner_scope_hash,portfolio_source,daily_loss_rate,
          max_drawdown,annualized_volatility,completeness,observed_at,received_at,schema_version,
          source_version,payload_json,source_ref,artifact_hash
        ) VALUES ('risk-team-a-acceptance',%s,%s,'KIS_MOCK',-0.01,-0.05,0.20,'COMPLETE',
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,'deterministic-risk-observation.v1',
          'team-a-acceptance-v1','{"ownerScopeHash":"sanitized"}'::jsonb,repeat('7',64),repeat('8',64))
        """,
        (_USER_ID, _KIS_SCOPE),
    )
    cursor.execute(
        """
        INSERT INTO daily_order_count_observations(
          observation_id,owner_user_id,owner_scope_hash,portfolio_source,trading_date,order_count,
          covered_through,completeness,observed_at,received_at,schema_version,source_version,
          payload_json,source_ref,artifact_hash
        ) VALUES ('orders-team-a-acceptance',%s,%s,'KIS_MOCK',
          (current_setting('app.p1_team_a_fixed_instant')::timestamptz AT TIME ZONE 'Asia/Seoul')::date,0,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,'COMPLETE',
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          'daily-order-count-observation.v1',
          'team-a-acceptance-v1','{"ownerScopeHash":"sanitized"}'::jsonb,repeat('9',64),repeat('a',64))
        """,
        (_USER_ID, _KIS_SCOPE),
    )
    cursor.execute(
        """
        INSERT INTO automation_runs(
          run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
          physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at
        ) VALUES (%s,%s,'2026-08-18','NEWS_VETOED','INTERNAL_PAPER','005930','BUY',0,0,0,
          '2026-08-18T09:20:00+09:00','2026-08-18T09:21:00+09:00')
        ON CONFLICT (run_id) DO NOTHING
        """,
        (_AUTOMATION_RUN_ID, _USER_ID),
    )
    cursor.execute(
        """
        INSERT INTO automation_positions(
          position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
          bot_owned,short_allowed,created_at,closed_at
        ) VALUES ('auto_pos_team_a_closed_0001',%s,%s,'005930',1,'2026-08-18','2026-08-25',
          'CLOSED',true,false,'2026-08-18T09:20:00+09:00','2026-08-25T09:20:00+09:00')
        ON CONFLICT (position_id) DO NOTHING
        """,
        (_USER_ID, _PAPER_ACCOUNT_ID),
    )
    cursor.execute(
        """
        INSERT INTO automation_events(
          event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,
          provider_calls,order_submits,sanitized
        ) VALUES ('auto_evt_team_a_news_0001',%s,%s,1,'NEWS_RESULT_RECORDED',
          '2026-08-18T09:21:00+09:00',repeat('b',64),0,0,true)
        ON CONFLICT (event_id) DO NOTHING
        """,
        (_AUTOMATION_RUN_ID, _USER_ID),
    )
    cursor.execute(
        """
        SELECT
          EXISTS(SELECT 1 FROM automation_runs WHERE run_id=%s AND user_id=%s
            AND state='NEWS_VETOED' AND provider_calls=0 AND physical_submit_count=0),
          EXISTS(SELECT 1 FROM automation_positions WHERE position_id='auto_pos_team_a_closed_0001'
            AND user_id=%s AND quantity=1 AND bot_owned AND NOT short_allowed AND status='CLOSED'),
          EXISTS(SELECT 1 FROM automation_events WHERE event_id='auto_evt_team_a_news_0001'
            AND run_id=%s AND user_id=%s AND sanitized AND provider_calls=0 AND order_submits=0)
        """,
        (_AUTOMATION_RUN_ID, _USER_ID, _USER_ID, _AUTOMATION_RUN_ID, _USER_ID),
    )
    if cursor.fetchone() != (True, True, True):
        raise ValueError("automation_fixture_drift")
    cursor.execute(
        """
        INSERT INTO rag_v2_answer_history(
          answer_id,owner_user_id,request_id,answer_mode,generation_status,citation_coverage,
          retrieval_failure,guardrail_flags,public_corpus_version,private_overlay_state,kek_version,
          wrap_nonce,wrapped_dek,wrap_tag,question_nonce,question_ciphertext,question_tag,
          answer_nonce,answer_ciphertext,answer_tag,citation_count,created_at,expires_at
        ) VALUES (%s,%s,'req_team_a_rag_fixture_0001','CONCISE','ANSWERED',1.0,false,
          ARRAY[]::text[],'p1-public-seed-v1','ABSENT','kek-v1',decode(repeat('00',12),'hex'),
          decode(repeat('11',32),'hex'),decode(repeat('22',16),'hex'),decode(repeat('33',12),'hex'),
          decode('44','hex'),decode(repeat('55',16),'hex'),decode(repeat('66',12),'hex'),
          decode('77','hex'),decode(repeat('88',16),'hex'),1,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz,
          current_setting('app.p1_team_a_fixed_instant')::timestamptz+interval '30 days')
        """,
        (_RAG_ANSWER_ID, _USER_ID),
    )
    cursor.execute(
        """
        INSERT INTO rag_v2_answer_citations(
          answer_id,owner_user_id,ordinal,citation_kind,source_id,title,canonical_url,locator
        ) VALUES (%s,%s,1,'PUBLIC_WEB','src_team_a_fixture_001','Team A acceptance source',
          'https://example.org/team-a-acceptance','{"section":"acceptance"}'::jsonb)
        """,
        (_RAG_ANSWER_ID, _USER_ID),
    )
    cursor.execute(
        """
        SELECT count(*) FROM dashboard_artifact_views
        WHERE owner_user_id=%s AND run_id='demo_s8_fake_e2e_0001'
          AND fixture_class='SYNTHETIC_FAKE_E2E' AND evidence_mode='SYNTHETIC_DEMO'
          AND view_kind IN ('MODEL_EVALUATION','BACKTEST')
        """,
        (_USER_ID,),
    )
    if cursor.fetchone() != (2,):
        raise ValueError("synthetic_team_b_fixture_missing")


def _restore(cursor: psycopg.Cursor[object]) -> None:
    cursor.execute(
        """
        UPDATE automation_control
        SET control_state='DISARMED',version=version+1,updated_at=statement_timestamp()
        WHERE user_id=%s AND control_state='ARMED' AND version<2147483647
        """,
        (_USER_ID,),
    )


def execute(command: str, environment: Mapping[str, str]) -> None:
    dsn = _dsn(environment)
    with psycopg.connect(dsn, autocommit=False, connect_timeout=3) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout='3s'")
                cursor.execute("SET LOCAL statement_timeout='20s'")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('p1-team-a-acceptance',1))"
                )
                cursor.execute(
                    """
                    SELECT current_user,session_user,current_database(),
                      EXISTS(SELECT 1 FROM p1_offline_demo_authority WHERE authority_id='P1_OFFLINE_DEMO' AND active)
                    """
                )
                if cursor.fetchone() != ("postgres", "postgres", "capstone_p1", True):
                    raise ValueError("acceptance_database_authority")
                if command == "seed":
                    cursor.execute(
                        "SELECT set_config('app.p1_team_a_fixed_instant',%s,true)",
                        (_fixed_instant(environment),),
                    )
                    _reset(cursor)
                    _seed(cursor)
                elif command == "restore":
                    _restore(cursor)
                else:
                    raise ValueError("unknown_command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed", "restore"))
    args = parser.parse_args(argv)
    try:
        execute(args.command, os.environ)
    except (KeyError, OSError, ValueError, psycopg.Error):
        print("P1_TEAM_A_ACCEPTANCE_SEED=FAIL")
        return 1
    print(f"P1_TEAM_A_ACCEPTANCE_{args.command.upper()}=PASS")
    print("P1_TEAM_A_ACCEPTANCE_PROVIDER_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
