-- baseline 복원 후 남은 row_security=off를 상속하지 않는다.
SET LOCAL row_security = on;
-- v1 writer가 잃어버린 청산 필드는 이전 저장 버전에서만 읽어 복원한다. 역사 row는 덮어쓰지 않는다.
CREATE VIEW public.automation_policy_versions_effective WITH(security_invoker=true) AS
 SELECT p.policy_id,p.version,p.user_id,p.capital_limit_krw,p.stop_loss_bps,p.take_profit_bps,p.risk_profile,p.principle_id,p.principle_version_id,p.principle_version,p.created_at,COALESCE(p.max_holding_sessions,prior.max_holding_sessions) AS max_holding_sessions,COALESCE(p.atr_period,prior.atr_period) AS atr_period,COALESCE(p.atr_multiplier_milli,prior.atr_multiplier_milli) AS atr_multiplier_milli,COALESCE(p.model_sell_enabled,prior.model_sell_enabled) AS model_sell_enabled FROM public.automation_policy_versions p LEFT JOIN LATERAL (
 SELECT prior.* FROM public.automation_policy_versions prior
 WHERE prior.user_id=p.user_id AND prior.policy_id=p.policy_id AND prior.version<p.version
  AND prior.max_holding_sessions IS NOT NULL ORDER BY prior.version DESC LIMIT 1
 ) prior ON true;
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

CREATE TABLE public.automation_policy_recovery_receipts AS
 SELECT p.user_id,p.policy_id,p.version,prior.version AS source_version,now() AS recovered_at
 FROM public.automation_policy_versions p JOIN LATERAL (
  SELECT version FROM public.automation_policy_versions v WHERE v.user_id=p.user_id AND v.policy_id=p.policy_id
   AND v.version<p.version AND v.max_holding_sessions IS NOT NULL ORDER BY version DESC LIMIT 1
 ) prior ON true WHERE p.max_holding_sessions IS NULL;
ALTER TABLE public.automation_policy_recovery_receipts OWNER TO flyway;
REVOKE ALL ON public.automation_policy_recovery_receipts FROM PUBLIC,decision_app;

CREATE FUNCTION public.p1_preserve_exit_policy_fields() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE prior public.automation_policy_versions%ROWTYPE;
BEGIN
 IF NEW.max_holding_sessions IS NULL AND NEW.atr_period IS NULL AND NEW.atr_multiplier_milli IS NULL AND NEW.model_sell_enabled IS NULL THEN
  SELECT * INTO prior FROM public.automation_policy_versions p
  WHERE p.user_id=NEW.user_id AND p.policy_id=NEW.policy_id AND p.version<NEW.version
   AND p.max_holding_sessions IS NOT NULL ORDER BY p.version DESC LIMIT 1;
  IF FOUND THEN
   NEW.max_holding_sessions:=prior.max_holding_sessions;NEW.atr_period:=prior.atr_period;
   NEW.atr_multiplier_milli:=prior.atr_multiplier_milli;NEW.model_sell_enabled:=prior.model_sell_enabled;
   NEW.risk_profile:=public.p1_automation_policy_profile_v2(NEW.stop_loss_bps,NEW.take_profit_bps,
    NEW.max_holding_sessions,NEW.atr_period,NEW.atr_multiplier_milli,NEW.model_sell_enabled);
  END IF;
 END IF;
 RETURN NEW;
END $f$;
ALTER FUNCTION public.p1_preserve_exit_policy_fields() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_preserve_exit_policy_fields() FROM PUBLIC;
CREATE TRIGGER p1_preserve_exit_policy BEFORE INSERT ON public.automation_policy_versions
 FOR EACH ROW EXECUTE FUNCTION public.p1_preserve_exit_policy_fields();

CREATE OR REPLACE FUNCTION public.p1_arm_automation_v3(p_user_id text, p_account_id text, p_policy_id text, p_expected_policy_version integer, p_expected_control_version integer, p_scope_hash text, p_request_hash text, p_provider_capability_ready boolean)
 RETURNS TABLE(result_json text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE settings_row public.strong_llm_owner_settings%ROWTYPE;
DECLARE base_result record;
DECLARE history_ready boolean;
DECLARE provider_ready boolean;
DECLARE settings_projection jsonb;
DECLARE settings_sha text;
BEGIN
  SELECT * INTO base_result FROM public.p1_arm_automation_v2(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,
    p_expected_control_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions_effective
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR policy_row.max_holding_sessions IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy unavailable' USING ERRCODE='40001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions_effective
    WHERE user_id=p_user_id AND account_id=p_account_id AND status IN ('OPEN','EXIT_PENDING')
      AND max_holding_sessions IS NULL
  ) THEN RAISE EXCEPTION 'LEGACY_POSITION_PRESENT' USING ERRCODE='P1L01'; END IF;
  SELECT EXISTS (SELECT 1 FROM public.market_data_manifests WHERE status='ACCEPTED')
    AND (SELECT count(*) FROM public.market_data_operational_universe)=31
    AND NOT EXISTS (
      SELECT 1 FROM public.market_data_operational_universe universe
      WHERE (SELECT count(*) FROM public.market_data_operational_bars bars
             WHERE bars.symbol=universe.symbol)<policy_row.atr_period+1
    ) INTO history_ready;
  IF NOT history_ready THEN
    RAISE EXCEPTION 'MARKET_DATA_CATCHUP_REQUIRED' USING ERRCODE='P1M01';
  END IF;
  SELECT * INTO settings_row FROM public.strong_llm_owner_settings
  WHERE owner_user_id=p_user_id;
  IF NOT FOUND THEN
    settings_row.provider:='vertex';settings_row.answer_language:='ko';
    settings_row.daily_generate_call_cap:=50;settings_row.ai_judgement_enabled:=false;
    settings_row.thinking_level:='low';
  END IF;
  provider_ready:=NOT settings_row.ai_judgement_enabled OR (
    COALESCE(p_provider_capability_ready,false)
    AND
    settings_row.daily_generate_call_cap>=3
    AND EXISTS (
      SELECT 1 FROM public.strong_llm_owner_credentials credential
      WHERE credential.owner_user_id=p_user_id AND credential.slot='PRIMARY'
    )
  );
  IF NOT provider_ready THEN
    RAISE EXCEPTION 'AI_PROVIDER_NOT_READY' USING ERRCODE='P1A01';
  END IF;
  settings_projection:=jsonb_build_object(
    'aiJudgementEnabled',settings_row.ai_judgement_enabled,
    'answerLanguage',settings_row.answer_language,
    'baseUrl',settings_row.base_url,
    'dailyGenerateCallCap',settings_row.daily_generate_call_cap,
    'fallbackBaseUrl',settings_row.fallback_base_url,
    'fallbackModelId',settings_row.fallback_model_id,
    'fallbackProvider',settings_row.fallback_provider,
    'modelId',settings_row.model_id,'provider',settings_row.provider,
    'thinkingLevel',settings_row.thinking_level
  );
  settings_sha:=encode(public.digest(convert_to(settings_projection::text,'UTF8'),'sha256'),'hex');
  UPDATE public.automation_control SET
    ai_settings_sha256=settings_sha,
    ai_settings_snapshot_json=settings_projection,
    ai_judgement_enabled_snapshot=settings_row.ai_judgement_enabled,
    ai_thinking_level_snapshot=settings_row.thinking_level
  WHERE user_id=p_user_id;
  result_json:=base_result.result_json;replayed:=false;RETURN NEXT;
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v1(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE reservation_json jsonb;
DECLARE positions_json jsonb;
DECLARE signals_json jsonb;
DECLARE manual_symbols_json jsonb;
DECLARE observed_digest text;
DECLARE baseline_projection jsonb;
DECLARE daily_ready boolean;
DECLARE no_open_order boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation state input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE';
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
  IF control_row.user_id IS NULL OR checkpoint_row.run_id IS NULL OR run_row.run_id IS NULL THEN
    RAISE EXCEPTION 'automation state unavailable' USING ERRCODE='P0002';
  END IF;
  observed_digest:=public.p1_automation_runtime_account_digest_v1(claim_row.user_id,control_row.account_id);
  SELECT jsonb_build_object(
    'accountId',control_row.account_id,
    'cashKrw',balance.cash_krw,
    'marginRequirementKrw',balance.margin_requirement_krw,
    'portfolioEquityKrw',balance.portfolio_equity_krw,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'marketValueKrw',position.market_value_krw,
        'quantity',position.quantity,
        'symbol',position.symbol
      ) ORDER BY position.symbol)
      FROM public.portfolio_position_observations position
      WHERE position.balance_observation_id=balance.observation_id
    ),'[]'::jsonb)
  ) INTO baseline_projection
  FROM public.portfolio_balance_observations balance
  WHERE balance.owner_user_id=claim_row.user_id AND balance.source='KIS_MOCK'
    AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
    AND balance.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
  ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id LIMIT 1;
  no_open_order:=public.p1_automation_open_work_clear_v3(claim_row.user_id,control_row.account_id);
  daily_ready:=EXISTS (
    SELECT 1 FROM public.market_data_manifests manifest
    WHERE manifest.manifest_kind IN ('DAILY','AUTOMATION_BOOTSTRAP')
      AND manifest.status='ACCEPTED'
      AND manifest.session_date<=claim_row.session_date
      AND manifest.as_of<=((claim_row.session_date+time '09:20') AT TIME ZONE 'Asia/Seoul')
  );
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'positionId',position.position_id,'accountId',position.account_id,'symbol',position.symbol,
    'entrySession',position.entry_session,'expirySession',position.expiry_session,
    'createdAt',position.created_at,'status',position.status,'closedAt',position.closed_at
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions_effective position
  WHERE position.user_id=claim_row.user_id AND position.account_id=control_row.account_id;
  SELECT to_jsonb(reservation) INTO reservation_json FROM (
    SELECT item.reservation_id AS "reservationId",item.symbol,item.side,item.quantity,
      item.limit_price_krw AS "limitPriceKrw",item.logical_submit_count AS "logicalSubmitCount",
      item.order_id AS "orderId",item.provider_order_ref_hash AS "providerOrderRefHash"
    FROM public.automation_order_reservations item WHERE item.run_id=p_run_id
  ) reservation;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'symbol',candidate.symbol,'lstmSignal',candidate.lstm_signal,
    'baselineSignal',candidate.baseline_signal,'expectedReturn',candidate.expected_return,
    'confidence',candidate.confidence
  ) ORDER BY candidate.symbol),'[]'::jsonb) INTO signals_json
  FROM (
    SELECT pointer.symbol,
      max(signal.signal) FILTER (WHERE signal.producer='LSTM') AS lstm_signal,
      max(signal.signal) FILTER (WHERE signal.producer='RULE_BASELINE') AS baseline_signal,
      max(signal.predicted_return) FILTER (WHERE signal.producer='LSTM') AS expected_return,
      max(signal.confidence) FILTER (WHERE signal.producer='LSTM') AS confidence
    FROM public.current_p1_return_signal_pointer pointer
    JOIN public.p1_return_signal_projection signal
      ON signal.bundle_sha256=pointer.bundle_sha256 AND signal.symbol=pointer.symbol
    WHERE pointer.session_date=claim_row.session_date
    GROUP BY pointer.symbol
    HAVING count(DISTINCT signal.producer)=2
  ) candidate;
  SELECT COALESCE(jsonb_agg(symbol ORDER BY symbol),'[]'::jsonb) INTO manual_symbols_json FROM (
    SELECT DISTINCT position.symbol
    FROM public.portfolio_position_observations position
    WHERE position.balance_observation_id=(
      SELECT balance.observation_id FROM public.portfolio_balance_observations balance
      WHERE balance.owner_user_id=claim_row.user_id AND balance.source='KIS_MOCK'
        AND balance.context_status='ACTIVE' AND balance.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
      ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id LIMIT 1
    )
  ) manual_position;
  RETURN jsonb_build_object(
    'accountComplete',observed_digest IS NOT NULL,
    'accountDigestMatches',observed_digest IS NOT NULL AND observed_digest=control_row.baseline_account_digest,
    'accountId',control_row.account_id,
    'baselineAccountDigest',control_row.baseline_account_digest,
    'baselineAccountProjection',baseline_projection,
    'brokerageMode',control_row.brokerage_mode,
    'checkpointVersion',checkpoint_row.checkpoint_version,
    'controlState',control_row.control_state,
    'controlVersion',control_row.version,
    'dailyShardFreshComplete',daily_ready,
    'decisionId',checkpoint_row.decision_id,
    'killSwitchActive',public.owner_stop_active(claim_row.user_id) OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true),
    'manualPositionSymbols',manual_symbols_json,
    'noOpenOrder',no_open_order,
    'positions',positions_json,
    'principleId',control_row.principle_id,
    'principleActiveCurrent',EXISTS (
      SELECT 1 FROM public.principles principle
      WHERE principle.user_id=claim_row.user_id AND principle.principle_id=control_row.principle_id
        AND principle.status='ACTIVE'
    ),
    'releaseActive',jsonb_array_length(signals_json)=31,
    'reservation',reservation_json,
    'runId',p_run_id,
    'runStartedAt',run_row.started_at,
    'selectedSide',checkpoint_row.selected_side,
    'selectedSymbol',checkpoint_row.selected_symbol,
    'sessionDate',claim_row.session_date,
    'signals',signals_json,
    'state',checkpoint_row.state,
    'strategyId',control_row.strategy_id,
    'unfinishedPreviousOrder',NOT no_open_order,
    'vertexCallCount',checkpoint_row.vertex_call_count,
    'providerCallCount',checkpoint_row.provider_call_count,
    'logicalSubmitCount',checkpoint_row.logical_submit_count
  )::text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v2(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE base jsonb;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE risk_projection jsonb;
DECLARE risk_digest text;
DECLARE positions_json jsonb;
DECLARE reservation_json jsonb;
DECLARE principle_rules jsonb;
DECLARE balance_row public.portfolio_balance_observations%ROWTYPE;
DECLARE open_position_value bigint;
DECLARE asset_weight_limit numeric;
DECLARE asset_remaining bigint;
DECLARE max_single_order bigint;
DECLARE catalog_symbols jsonb;
BEGIN
  base:=public.p1_read_automation_runtime_state_v1(p_run_id,p_claim_token_hash)::jsonb;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  IF control_row.policy_id IS NULL THEN
    RAISE EXCEPTION 'automation v2 policy unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions_effective
  WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id;
  risk_projection:=public.p1_automation_risk_balance_projection_v2(claim_row.user_id,control_row.account_id);
  IF risk_projection IS NOT NULL THEN
    risk_digest:=encode(public.digest(convert_to(risk_projection::text,'UTF8'),'sha256'),'hex');
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'accountId',position.account_id,'closedAt',position.closed_at,'createdAt',position.created_at,
    'entryAverageFillPriceKrw',position.entry_average_fill_price_krw,
    'entryNotionalKrw',position.entry_filled_quantity*position.entry_average_fill_price_krw,
    'entrySession',position.entry_session,'exitReason',position.exit_reason,
    'expirySession',position.expiry_session,'policyId',position.policy_id,
    'policyVersion',position.policy_version,'positionId',position.position_id,
    'quantity',position.quantity,'status',position.status,'stopLossBps',position.stop_loss_bps,
    'symbol',position.symbol,'takeProfitBps',position.take_profit_bps
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions_effective position
  WHERE position.user_id=claim_row.user_id AND position.policy_id IS NOT NULL;
  IF reservation_row.reservation_id IS NOT NULL THEN
    reservation_json:=jsonb_build_object(
      'exactIntent',reservation_row.exact_intent_json::jsonb,
      'limitPriceKrw',reservation_row.limit_price_krw,
      'logicalSubmitCount',reservation_row.logical_submit_count,
      'orderId',reservation_row.order_id,'providerOrderRefHash',reservation_row.provider_order_ref_hash,
      'quantity',reservation_row.quantity,'reservationId',reservation_row.reservation_id,
      'side',reservation_row.side,'symbol',reservation_row.symbol
    );
  END IF;
  -- V91까지 사이저 입력 세 개가 상수였다. 보유평가액 0과 원칙 한도 MAX_BIGINT 두 개 때문에
  -- _variable_buy_quantity의 min() 다섯 항 중 셋이 무력했고, 사용자가 정한 1회 최대주문 한도를
  -- 넘는 수량이 만들어져 RiskEngine이 BLOCK하면 그 세션의 단 한 번뿐인 주문이 낭비됐다.
  SELECT * INTO balance_row FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=claim_row.user_id AND item.source='KIS_MOCK'
    AND item.context_status='ACTIVE' AND item.completeness='COMPLETE'
    AND item.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  SELECT COALESCE(sum(position.market_value_krw),0) INTO open_position_value
  FROM public.portfolio_position_observations position
  WHERE position.balance_observation_id=balance_row.observation_id;

  SELECT version.rules_json INTO principle_rules FROM public.principle_versions version
  WHERE version.principle_version_id=control_row.principle_version_id;

  -- 규칙이 없거나 꺼져 있으면 제한이 없다는 뜻이므로 MAX_BIGINT를 유지한다.
  SELECT COALESCE(min((rule->>'threshold')::bigint),9223372036854775807)
  INTO max_single_order
  FROM jsonb_array_elements(COALESCE(principle_rules,'[]'::jsonb)) rule
  WHERE rule->>'ruleId'='max_single_order_amount' AND (rule->>'enabled')::boolean;

  SELECT min((rule->>'threshold')::numeric) INTO asset_weight_limit
  FROM jsonb_array_elements(COALESCE(principle_rules,'[]'::jsonb)) rule
  WHERE rule->>'ruleId'='max_position_per_asset' AND (rule->>'enabled')::boolean;

  IF asset_weight_limit IS NULL OR balance_row.portfolio_equity_krw IS NULL THEN
    asset_remaining:=9223372036854775807;
  ELSE
    -- 선택 종목이 이미 차지한 평가액을 뺀 나머지가 이번 주문에 허용된 한도다.
    asset_remaining:=GREATEST(0,
      floor(asset_weight_limit*balance_row.portfolio_equity_krw)::bigint
      - COALESCE((
        SELECT position.market_value_krw FROM public.portfolio_position_observations position
        WHERE position.balance_observation_id=balance_row.observation_id
          AND position.symbol=checkpoint_row.selected_symbol
      ),0));
  END IF;

  -- 분류가 확인된 종목만 risk-complete 판정에 쓸 수 있다. host가 보유 종목 전부를 덮는지
  -- 확인하도록 카탈로그가 아는 종목 집합을 그대로 넘긴다.
  SELECT COALESCE(jsonb_agg(catalog.symbol ORDER BY catalog.symbol),'[]'::jsonb)
  INTO catalog_symbols
  FROM public.latest_instrument_catalog_observations catalog
  WHERE catalog.completeness='COMPLETE';

  RETURN (base||jsonb_build_object(
    'accountComplete',risk_projection IS NOT NULL,
    'accountDigestMatches',risk_digest IS NOT NULL AND risk_digest=control_row.expected_account_digest_v2,
    'averageFillPriceKrw',reservation_row.average_fill_price_krw,
    'exitReason',COALESCE(reservation_row.exit_reason,checkpoint_row.exit_reason),
    'expectedAccountDigest',control_row.expected_account_digest_v2,
    'expectedAccountProjection',control_row.expected_account_projection_v2,
    'filledQuantity',COALESCE(reservation_row.filled_quantity,0),
    'leavesQuantity',COALESCE(reservation_row.leaves_quantity,0),
    'openPositionMarketValueKrw',open_position_value,'pendingBuyNotionalKrw',CASE
      WHEN reservation_row.side='BUY' AND reservation_row.reconciliation_status='NOT_APPLICABLE'
        THEN COALESCE(reservation_row.estimated_amount_krw,0) ELSE 0 END,
    'policy',jsonb_build_object(
      'capitalLimitKrw',policy_row.capital_limit_krw,'maxOpenPositions',5,
      'policyId',policy_row.policy_id,'preset',policy_row.risk_profile,
      'stopLossBps',policy_row.stop_loss_bps,'takeProfitBps',policy_row.take_profit_bps,
      'version',policy_row.version
    ),
    'positions',positions_json,'principleAssetRemainingKrw',asset_remaining,
    'principleMaxSingleOrderKrw',max_single_order,
    'instrumentCatalogSymbols',catalog_symbols,
    'providerExecRefHash',reservation_row.provider_order_ref_hash,
    'quoteSnapshot',CASE WHEN checkpoint_row.quote_snapshot_json IS NULL THEN NULL
      ELSE checkpoint_row.quote_snapshot_json::jsonb END,
    'reservation',reservation_json,
    'unfilledTerminatedQuantity',COALESCE(reservation_row.unfilled_terminated_quantity,0)
  ))::text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v3(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE base jsonb;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE positions_json jsonb;
BEGIN
  base:=public.p1_read_automation_runtime_state_v2(p_run_id,p_claim_token_hash)::jsonb;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  SELECT * INTO policy_row FROM public.automation_policy_versions_effective
  WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
  IF policy_row.max_holding_sessions IS NULL THEN RETURN base::text; END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'accountId',position.account_id,'atrAsOfSession',position.atr_as_of_session,
    'atrMultiplierMilli',position.atr_multiplier_milli,'atrPeriod',position.atr_period,
    'atrStatus',position.atr_status,'closedAt',position.closed_at,
    'createdAt',position.created_at,'entryAverageFillPriceKrw',position.entry_average_fill_price_krw,
    'entryNotionalKrw',position.quantity*position.entry_average_fill_price_krw,
    'entrySession',position.entry_session,'exitReason',position.exit_reason,
    'expirySession',position.expiry_session,'maxHoldingSessions',position.max_holding_sessions,
    'modelSellEnabled',position.model_sell_enabled,'peakPriceKrw',position.peak_price_krw,
    'policyId',position.policy_id,'policyVersion',position.policy_version,
    'positionId',position.position_id,'quantity',position.quantity,'status',position.status,
    'stopLossBps',position.stop_loss_bps,'symbol',position.symbol,
    'takeProfitBps',position.take_profit_bps,'trailingStopKrw',position.trailing_stop_krw
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions_effective position
  WHERE position.user_id=claim_row.user_id AND position.max_holding_sessions IS NOT NULL
    AND position.status IN ('OPEN','EXIT_PENDING');
  RETURN (base||jsonb_build_object(
    'policy',(base->'policy')||jsonb_build_object(
      'atrMultiplierMilli',policy_row.atr_multiplier_milli,'atrPeriod',policy_row.atr_period,
      'maxHoldingSessions',policy_row.max_holding_sessions,
      'modelSellEnabled',policy_row.model_sell_enabled
    ),
    'positions',positions_json
  ))::text;
END
$function$
;

CREATE FUNCTION public.p1_read_automation_schedule_time_v1(p_user_id text) RETURNS timestamptz
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN
 IF session_user<>'decision_app' OR current_setting('app.actor_user_id',true) IS DISTINCT FROM p_user_id THEN
  RAISE EXCEPTION 'schedule owner scope denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT min(run_at) FROM public.automation_runtime_schedule
   WHERE user_id=p_user_id AND schedule_state='ARMED' AND run_at>statement_timestamp());
END $f$;
ALTER FUNCTION public.p1_read_automation_schedule_time_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_schedule_time_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_schedule_time_v1(text) TO decision_app;

-- 새 포지션도 자신이 결속한 정책의 청산 기준을 잃지 않는다.
CREATE FUNCTION public.p1_copy_position_exit_policy() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE policy public.automation_policy_versions%ROWTYPE;
BEGIN
 SELECT * INTO policy FROM public.automation_policy_versions_effective
  WHERE user_id=NEW.user_id AND policy_id=NEW.policy_id AND version=NEW.policy_version;
 IF FOUND THEN
  NEW.max_holding_sessions:=COALESCE(NEW.max_holding_sessions,policy.max_holding_sessions);
  NEW.atr_period:=COALESCE(NEW.atr_period,policy.atr_period);
  NEW.atr_multiplier_milli:=COALESCE(NEW.atr_multiplier_milli,policy.atr_multiplier_milli);
  NEW.model_sell_enabled:=COALESCE(NEW.model_sell_enabled,policy.model_sell_enabled);
  IF NEW.max_holding_sessions IS NOT NULL THEN
   NEW.peak_price_krw:=COALESCE(NEW.peak_price_krw,NEW.entry_average_fill_price_krw);
   IF NEW.atr_status='LEGACY' THEN NEW.atr_status:='UNAVAILABLE'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $f$;
ALTER FUNCTION public.p1_copy_position_exit_policy() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_copy_position_exit_policy() FROM PUBLIC;
CREATE TRIGGER p1_position_exit_policy BEFORE INSERT ON public.automation_positions
 FOR EACH ROW EXECUTE FUNCTION public.p1_copy_position_exit_policy();
