-- 2026-09-09 무주문은 안전필터가 아니라 포지션 상한 포화였다. 상한과 거래당 위험을
-- 사용자가 고를 수 있게 원칙에 싣고, 어느 단계에서 후보가 사라졌는지를 종목별로 남긴다.
-- forward-only. 과거 migration bytes 와 기존 V112 심사표는 건드리지 않는다.
SET LOCAL row_security = on;

-- 1) 동시 보유 상한과 거래당 위험을 원칙 버전에 싣는다.
--    NULL 은 "이 버전이 저장되기 전" 을 뜻하고 effective view 가 이전 값으로 메운다.
ALTER TABLE public.automation_policy_versions
  ADD COLUMN max_open_positions integer
    CHECK (max_open_positions IS NULL OR max_open_positions BETWEEN 1 AND 20),
  ADD COLUMN risk_per_trade_bps integer
    CHECK (risk_per_trade_bps IS NULL OR risk_per_trade_bps BETWEEN 10 AND 300);

COMMENT ON COLUMN public.automation_policy_versions.max_open_positions IS
  '동시 보유 종목 상한. 종목당 상한금액 = capital_limit_krw / 이 값.';
COMMENT ON COLUMN public.automation_policy_versions.risk_per_trade_bps IS
  'ATR 변동성 기반 사이징의 거래당 위험(자본 대비 bps). 100 = 1%.';

-- 2) effective view 가 새 컬럼도 이전 버전에서 메우도록 다시 만든다.
--    positions_effective 가 이 view 에 의존하므로 함께 재생성한다.
DROP VIEW public.automation_positions_effective;
DROP VIEW public.automation_policy_versions_effective;

CREATE VIEW public.automation_policy_versions_effective WITH(security_invoker=true) AS
 SELECT p.policy_id,p.version,p.user_id,p.capital_limit_krw,p.stop_loss_bps,p.take_profit_bps,
        p.risk_profile,p.principle_id,p.principle_version_id,p.principle_version,p.created_at,
        COALESCE(p.max_holding_sessions,prior.max_holding_sessions) AS max_holding_sessions,
        COALESCE(p.atr_period,prior.atr_period) AS atr_period,
        COALESCE(p.atr_multiplier_milli,prior.atr_multiplier_milli) AS atr_multiplier_milli,
        COALESCE(p.model_sell_enabled,prior.model_sell_enabled) AS model_sell_enabled,
        -- 상한은 지금까지 코드 상수(5)였고 어느 버전에도 저장된 적이 없다. 저장된 값이
        -- 없으면 새 제품 기본값 10 이다. 5 는 6개가 열린 계정을 영구 포화시켜 매수를
        -- 막았고, 그 상태에서는 상한 초과 감지도 되지 않았다.
        COALESCE(p.max_open_positions,cap.max_open_positions,10) AS max_open_positions,
        COALESCE(p.risk_per_trade_bps,cap.risk_per_trade_bps,100) AS risk_per_trade_bps
 FROM public.automation_policy_versions p
 LEFT JOIN LATERAL (
   SELECT prior.* FROM public.automation_policy_versions prior
   WHERE prior.user_id=p.user_id AND prior.policy_id=p.policy_id AND prior.version<p.version
     AND prior.max_holding_sessions IS NOT NULL ORDER BY prior.version DESC LIMIT 1
 ) prior ON true
 LEFT JOIN LATERAL (
   SELECT cap.max_open_positions,cap.risk_per_trade_bps
   FROM public.automation_policy_versions cap
   WHERE cap.user_id=p.user_id AND cap.policy_id=p.policy_id AND cap.version<p.version
     AND cap.max_open_positions IS NOT NULL ORDER BY cap.version DESC LIMIT 1
 ) cap ON true;
ALTER VIEW public.automation_policy_versions_effective OWNER TO flyway;
GRANT SELECT ON public.automation_policy_versions_effective TO decision_app;

CREATE VIEW public.automation_positions_effective WITH(security_invoker=true) AS
 SELECT p.position_id,p.user_id,p.account_id,p.symbol,p.quantity,p.entry_session,
 CASE WHEN p.max_holding_sessions IS NULL AND policy.max_holding_sessions IS NOT NULL THEN
  CASE WHEN policy.max_holding_sessions=0 THEN NULL ELSE
   (SELECT session_date FROM public.trading_sessions WHERE exchange_mic='XKRX' AND is_open
    AND session_date>p.entry_session ORDER BY session_date OFFSET GREATEST(policy.max_holding_sessions-1,0) LIMIT 1)
  END ELSE p.expiry_session END AS expiry_session,p.status,p.bot_owned,p.short_allowed,p.created_at,p.closed_at,p.entry_order_id,p.entry_ordered_quantity,p.entry_filled_quantity,p.entry_unfilled_quantity,p.entry_average_fill_price_krw,p.policy_id,p.policy_version,p.stop_loss_bps,p.take_profit_bps,p.exit_filled_quantity,p.exit_average_fill_price_krw,p.exit_reason,p.realized_pnl_krw,COALESCE(p.max_holding_sessions,policy.max_holding_sessions) AS max_holding_sessions,COALESCE(p.atr_period,policy.atr_period) AS atr_period,COALESCE(p.atr_multiplier_milli,policy.atr_multiplier_milli) AS atr_multiplier_milli,COALESCE(p.model_sell_enabled,policy.model_sell_enabled) AS model_sell_enabled,COALESCE(p.peak_price_krw,CASE WHEN policy.max_holding_sessions IS NOT NULL THEN p.entry_average_fill_price_krw END) AS peak_price_krw,
 p.atr_as_of_session,p.trailing_stop_krw,
 CASE WHEN p.max_holding_sessions IS NULL AND policy.max_holding_sessions IS NOT NULL THEN 'UNAVAILABLE' ELSE p.atr_status END AS atr_status FROM public.automation_positions p LEFT JOIN public.automation_policy_versions_effective policy
 ON policy.user_id=p.user_id AND policy.policy_id=p.policy_id AND policy.version=p.policy_version;
ALTER VIEW public.automation_positions_effective OWNER TO flyway;
GRANT SELECT ON public.automation_positions_effective TO decision_app;

-- 3) 단계별 후보 탈락 사유. V112 심사표는 뉴스/AI 단계까지 간 후보만 담아서
--    "이 실행에는 남은 후보 심사 기록이 없습니다" 밖에 보여줄 수 없었다.
--    상위 단계(규칙/LSTM/안전필터/ATR)의 탈락도 종목별로 남겨 화면이 원인을 가리키게 한다.
CREATE TABLE public.automation_candidate_stage_outcomes (
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  stage text NOT NULL CHECK (stage IN (
    -- OBSERVATION 은 종목 단계가 아니라 세션 전체를 막는 선행 조건이다. 관측 적재가
    -- 실패하면 RiskEngine 이 전부 HOLD 하는데 지금까지 그 사유가 어디에도 남지 않았다.
    'OBSERVATION',
    'RULE_BUY','LSTM_VETO','QUOTE_SAFETY','ATR_HISTORY',
    'NEWS_DISCLOSURE','AI_JUDGE','RISK_ENGINE','ORDER'
  )),
  -- 세션 전체를 막는 단계는 종목이 없다. '000000' 을 그 자리표시로 쓴다.
  symbol text NOT NULL CHECK (symbol~'^[0-9]{6}$'),
  outcome text NOT NULL CHECK (outcome IN ('PASS','DROPPED')),
  -- 통과 행은 사유가 없다. 탈락 행은 반드시 기계가 읽는 사유 코드를 갖는다.
  reason_code text CHECK (reason_code IS NULL OR reason_code~'^[A-Z][A-Z0-9_]{2,63}$'),
  reason_detail text CHECK (reason_detail IS NULL OR octet_length(reason_detail) BETWEEN 1 AND 512),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (run_id,stage,symbol),
  CHECK ((outcome='DROPPED') = (reason_code IS NOT NULL))
);

CREATE INDEX automation_candidate_stage_outcomes_run_v166
  ON public.automation_candidate_stage_outcomes(run_id,stage,symbol);

ALTER TABLE public.automation_candidate_stage_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_candidate_stage_outcomes FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_candidate_stage_outcome_scope_v166
  ON public.automation_candidate_stage_outcomes TO PUBLIC
USING (
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
);

-- V112 심사표와 같은 최소권한. 런타임은 flyway 소유 SECURITY DEFINER 경로로 쓰므로
-- 별도 grant 를 주지 않는다.
GRANT SELECT,INSERT ON public.automation_candidate_stage_outcomes TO decision_app;

-- 4) 정책 저장 함수에 상한과 거래당 위험을 싣는다. V115 의 v2 함수는 maxOpenPositions 를
--    5 로 박아 넣어 화면이 늘 5 를 보여줬다. 기존 함수는 그대로 두고 새 시그니처를 만든다.
CREATE OR REPLACE FUNCTION public.p1_put_automation_policy_v3(
  p_user_id text,p_principle_id text,p_capital_limit_krw bigint,p_stop_loss_bps integer,
  p_take_profit_bps integer,p_max_holding_sessions integer,p_atr_period integer,
  p_atr_multiplier_milli integer,p_model_sell_enabled boolean,p_max_open_positions integer,
  p_risk_per_trade_bps integer,p_expected_version integer,p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_put_automation_policy_v3$
DECLARE base_result record;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE projection jsonb;
BEGIN
  IF p_max_open_positions NOT BETWEEN 1 AND 20
     OR p_risk_per_trade_bps NOT BETWEEN 10 AND 300 THEN
    RAISE EXCEPTION 'automation sizing policy input invalid' USING ERRCODE='22023';
  END IF;
  -- 나머지 검증과 CAS 는 검증된 v2 경로를 그대로 재사용한다.
  SELECT * INTO base_result FROM public.p1_put_automation_policy_v2(
    p_user_id,p_principle_id,p_capital_limit_krw,p_stop_loss_bps,p_take_profit_bps,
    p_max_holding_sessions,p_atr_period,p_atr_multiplier_milli,p_model_sell_enabled,
    p_expected_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  UPDATE public.automation_policy_versions SET
    max_open_positions=p_max_open_positions,risk_per_trade_bps=p_risk_per_trade_bps
  WHERE policy_id=base_result.result_json::jsonb->>'policyId'
    AND version=(base_result.result_json::jsonb->>'version')::integer
  RETURNING * INTO policy_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation sizing policy write unavailable' USING ERRCODE='40001';
  END IF;
  projection:=(base_result.result_json::jsonb)
    || jsonb_build_object(
         'maxOpenPositions',policy_row.max_open_positions,
         'riskPerTradeBps',policy_row.risk_per_trade_bps
       );
  UPDATE public.automation_policy_idempotency SET result_json=projection
  WHERE scope_hash=p_scope_hash AND user_id=p_user_id;
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_put_automation_policy_v3$;

ALTER FUNCTION public.p1_put_automation_policy_v3(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,integer,integer,text,text
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_put_automation_policy_v3(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,integer,integer,text,text
) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_put_automation_policy_v3(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,integer,integer,text,text
) TO decision_app;

-- 5) 런타임이 단계별 결과를 남기는 경로. 상태 전이 함수는 건드리지 않는다. 이 기록은
--    진단용이라 유실돼도 실행 상태의 정확성에는 영향이 없고, 대신 claim 을 검증해
--    남의 run 에 쓰지 못하게 한다.
CREATE OR REPLACE FUNCTION public.p1_record_automation_stage_outcomes_v1(
  p_run_id text,p_claim_token_hash text,p_outcomes jsonb
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_record_automation_stage_outcomes_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE inserted integer;
BEGIN
  IF jsonb_typeof(p_outcomes)<>'array' THEN
    RAISE EXCEPTION 'automation stage outcomes payload invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  INSERT INTO public.automation_candidate_stage_outcomes(
    run_id,user_id,stage,symbol,outcome,reason_code,reason_detail
  )
  SELECT p_run_id,claim_row.user_id,item->>'stage',item->>'symbol',item->>'outcome',
         NULLIF(item->>'reasonCode',''),NULLIF(item->>'reasonDetail','')
  FROM jsonb_array_elements(p_outcomes) item
  ON CONFLICT (run_id,stage,symbol) DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  RETURN inserted;
END
$p1_record_automation_stage_outcomes_v1$;

ALTER FUNCTION public.p1_record_automation_stage_outcomes_v1(text,text,jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_record_automation_stage_outcomes_v1(text,text,jsonb)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_record_automation_stage_outcomes_v1(text,text,jsonb)
  TO decision_automation_runtime;
