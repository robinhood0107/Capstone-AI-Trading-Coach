-- run 이 terminal 이 되면 claim 은 RELEASED 로 닫히고, 그 뒤 11:00·14:00 포트폴리오
-- continuation 이 같은 claim 으로 돈다(portfolio 함수들은 당일 15:20 까지 RELEASED 를 받는다).
-- 그 사이 api 가 재기동되면 claim_for_owner 는 None 을 돌려주고, 상태 읽기 함수는 ACTIVE 만
-- 받아서 그날 남은 결정 시점이 통째로 사라졌다. 상태·AI 판정 읽기를 portfolio 함수와 같은
-- 창(당일 15:20 이전 RELEASED)으로 맞추고, 그 창에서 continuation 을 다시 잡는 함수를 둔다.
-- AI 판정 읽기는 PERFORM 뒤에 FOUND 를 읽어 claim 없음 검사가 발동하지 않았다(V224 와 같은 결함).

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
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'));
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
$function$;

CREATE OR REPLACE FUNCTION public.p1_read_automation_ai_judgement_v1(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE judgement_row public.automation_ai_judgements%ROWTYPE;
DECLARE claim_found boolean;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation ai judgement read invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'));
  claim_found:=FOUND;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT claim_found THEN
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO judgement_row FROM public.automation_ai_judgements
  WHERE run_id=p_run_id ORDER BY checkpoint_version DESC LIMIT 1;
  IF NOT FOUND OR judgement_row.participation<>'APPLIED' THEN
    RETURN NULL;
  END IF;
  -- 모델이 쓴 글(verdicts_json)은 여기로 돌려주지 않는다. 그것은 사후 감사용 기록이고,
  -- 다시 읽어 결정에 쓰면 프롬프트 출력이 두 번째 통로로 매매에 닿는다.
  RETURN jsonb_build_object(
    'baselineSymbol',judgement_row.baseline_symbol,
    'checkpointVersion',judgement_row.checkpoint_version,
    'confidenceBps',judgement_row.confidence_bps,
    'participation',judgement_row.participation,
    'selectedSymbol',judgement_row.selected_symbol
  )::text;
END;
$function$;

CREATE OR REPLACE FUNCTION public.p1_resume_automation_continuation_v1(
  p_owner_user_id text, p_session_date date, p_claim_token_hash text
) RETURNS TABLE(user_id text, run_id text, control_version integer, account_id text, principle_id text,
                strategy_id text, baseline_account_digest text, replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE claim_found boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_session_date IS NULL OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation continuation input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','1',true);
  SELECT claim.* INTO claim_row FROM public.automation_runtime_claim claim
  WHERE claim.user_id=p_owner_user_id AND claim.session_date=p_session_date
    AND claim.claim_token_hash=p_claim_token_hash AND claim.claim_state='RELEASED'
    AND claim.session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
    AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20';
  claim_found:=FOUND;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);
  IF NOT claim_found THEN RETURN; END IF;
  PERFORM pg_catalog.set_config('app.automation_owner_user_id',claim_row.user_id,true);
  PERFORM 1 FROM public.automation_runtime_checkpoint checkpoint
  WHERE checkpoint.run_id=claim_row.run_id
    AND checkpoint.state IN ('COMPLETED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION');
  IF NOT FOUND THEN RETURN; END IF;
  SELECT control.* INTO control_row FROM public.automation_control control
  WHERE control.user_id=claim_row.user_id;
  IF NOT FOUND OR control_row.control_state<>'ARMED' OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RETURN;
  END IF;
  user_id:=claim_row.user_id;run_id:=claim_row.run_id;control_version:=control_row.version;
  account_id:=control_row.account_id;principle_id:=control_row.principle_id;
  strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
  replayed:=true;RETURN NEXT;
END
$function$;
REVOKE ALL ON FUNCTION public.p1_resume_automation_continuation_v1(text,date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_resume_automation_continuation_v1(text,date,text) TO decision_automation_runtime;
