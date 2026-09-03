-- P1 자동운용: daily_ready 가 AUTOMATION_BOOTSTRAP 시장데이터도 인정한다.
--
-- V93:194-199 는 manifest_kind='DAILY' 만 인정했다. 그런데 DB 에는 AUTOMATION_BOOTSTRAP
-- 1건뿐이고(실측: ACCEPTED, session_date 2026-08-28) DAILY 는 0건이다. 그래서
-- dailyShardFreshComplete 가 영원히 false 이고 automation.py:1162 가
-- SKIPPED_DATA_UNAVAILABLE 로 전이한다 - arm 이 성공해도 주문이 나가지 않는다.
-- status API 의 blockers 에도 나타나지 않아 원인을 알 수 없었다.
--
-- bootstrap 도 같은 표의 같은 스키마이고 status='ACCEPTED' 로 이미 수용된 시장데이터다
-- (market_data_manifest_kind_check 가 세 종류를 모두 허용한다). 완화해도 최신성 경계는
-- 그대로 남는다 - as_of <= session_date + 09:20 KST 조건은 손대지 않았다.
--
-- 즉 "모양을 고정하고 바이트를 고정하지 않는다"에 맞춘 완화다. 데이터의 진위·수용 여부·
-- 최신성은 여전히 검사하고, manifest 종류 이름만 넓힌다.
--
-- 이 파일은 V93 의 함수를 그대로 복사해 manifest_kind 줄만 바꾼 것이다. CREATE OR REPLACE
-- 는 소유권과 GRANT 를 보존하므로 V93 의 ALTER/REVOKE/GRANT 는 반복하지 않는다.

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v1(
  p_run_id text,p_claim_token_hash text
)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_read_automation_runtime_state_v1$
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
  FROM public.automation_positions position
  WHERE position.user_id=claim_row.user_id;
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
    'killSwitchActive',COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true),
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
$p1_read_automation_runtime_state_v1$;
