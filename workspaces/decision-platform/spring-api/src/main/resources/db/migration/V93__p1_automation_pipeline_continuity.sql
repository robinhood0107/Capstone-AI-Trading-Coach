-- V91까지는 automation readiness가 public.orders를 직접 읽었다. automation 런타임은 KIS와
-- 직접 대사하고 automation_* 테이블만 갱신하므로, 봇이 한 번 체결하면 orders row가
-- SUBMITTED에 남아 다음 세션이 영구히 SKIPPED_DATA_UNAVAILABLE로 막혔다. 판정을 automation
-- 소유 상태로 옮기고, 사람이 낸 주문에 대한 보호만 orders에 남긴다.
--
-- 함께 닫는 것:
--   * 전이 whitelist가 엔진이 실제로 내는 전이(NEWS_CHECKING->HALTED 등)를 거부하던 문제
--   * 부분 청산에서 청산 평균가가 엔진과 다르게 덮어써지던 문제
--   * V92가 추가한 realized_pnl_krw에 아무도 값을 쓰지 않던 문제

-- 사람이 낸 미결 주문은 계속 봇을 막고, 봇 자신이 낸 주문은 automation 예약으로만 판정한다.
CREATE FUNCTION public.p1_automation_open_work_clear_v3(p_user_id text,p_account_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_open_work_clear_v3$
  SELECT NOT EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=p_account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
      AND NOT EXISTS (
        SELECT 1 FROM public.automation_order_reservations reservation
        WHERE reservation.order_id=item.order_id
      )
  ) AND NOT EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.user_id=p_user_id AND run.state='PENDING_RECONCILIATION'
  )
$p1_automation_open_work_clear_v3$;

ALTER FUNCTION public.p1_automation_open_work_clear_v3(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_open_work_clear_v3(text,text)
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,
  decision_automation_runtime;

-- 엔진 app/p1_owner/automation.py::_LEGAL_TRANSITIONS 와 한 글자도 어긋나면 안 된다.
-- contracts/tests/test_p1_v93_automation_pipeline.py 가 양쪽을 대조한다.
CREATE OR REPLACE FUNCTION public.p1_automation_transition_valid_v2(p_current text,p_next text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_transition_valid_v2$
  SELECT (p_current,p_next) IN (
    ('BUY_CANDIDATE_SELECTED','HALTED'),('BUY_CANDIDATE_SELECTED','NEWS_CHECKING'),
    ('EXIT_SELECTED','HALTED'),('EXIT_SELECTED','ORDER_SIZING'),
    ('NEWS_CHECKING','HALTED'),('NEWS_CHECKING','NEWS_VETOED'),
    ('NEWS_CHECKING','ORDER_SIZING'),('NEWS_CHECKING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SIZING','HALTED'),('ORDER_SIZING','RISK_CHECKING'),
    ('ORDER_SIZING','SKIPPED_DATA_UNAVAILABLE'),('ORDER_SIZING','SKIPPED_LATE_START'),
    ('ORDER_SIZING','SKIPPED_NO_ACTION'),('ORDER_SUBMITTED','CANCELLED_UNFILLED'),
    ('ORDER_SUBMITTED','COMPLETED'),('ORDER_SUBMITTED','HALTED'),
    ('ORDER_SUBMITTED','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','HALTED'),
    ('ORDER_SUBMITTING','ORDER_SUBMITTED'),('ORDER_SUBMITTING','ORDER_SUBMITTING'),
    ('ORDER_SUBMITTING','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SUBMITTING','SKIPPED_LATE_START'),('PENDING_RECONCILIATION','BUY_CANDIDATE_SELECTED'),
    ('PENDING_RECONCILIATION','CANCELLED_UNFILLED'),('PENDING_RECONCILIATION','COMPLETED'),
    ('PENDING_RECONCILIATION','EXIT_SELECTED'),('PENDING_RECONCILIATION','HALTED'),
    ('PENDING_RECONCILIATION','PENDING_RECONCILIATION'),('PENDING_RECONCILIATION','SKIPPED_DATA_UNAVAILABLE'),
    ('PENDING_RECONCILIATION','SKIPPED_NO_ACTION'),('PRECHECK','BUY_CANDIDATE_SELECTED'),
    ('PRECHECK','EXIT_SELECTED'),('PRECHECK','HALTED'),
    ('PRECHECK','RECONCILING_PREVIOUS'),('PRECHECK','SKIPPED_DATA_UNAVAILABLE'),
    ('PRECHECK','SKIPPED_NO_ACTION'),('RECONCILING_PREVIOUS','BUY_CANDIDATE_SELECTED'),
    ('RECONCILING_PREVIOUS','EXIT_SELECTED'),('RECONCILING_PREVIOUS','HALTED'),
    ('RECONCILING_PREVIOUS','PENDING_RECONCILIATION'),('RECONCILING_PREVIOUS','SKIPPED_DATA_UNAVAILABLE'),
    ('RECONCILING_PREVIOUS','SKIPPED_NO_ACTION'),('RISK_CHECKING','HALTED'),
    ('RISK_CHECKING','ORDER_SUBMITTING'),('RISK_CHECKING','SKIPPED_NO_ACTION'),
    ('SCHEDULED','HALTED'),('SCHEDULED','PRECHECK'),
    ('SCHEDULED','SCHEDULED'),('SCHEDULED','SKIPPED_DATA_UNAVAILABLE'),
    ('SCHEDULED','SKIPPED_LATE_START'),('SCHEDULED','SKIPPED_NO_ACTION')
  )
$p1_automation_transition_valid_v2$;

CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(
  p_user_id text,p_target_session date
)
RETURNS TABLE(
  control_configured boolean,certification_valid boolean,release_source_bound boolean,
  real_team_b_ready boolean,principle_current boolean,kill_switch_inactive boolean,
  account_baseline_matches boolean,unresolved_state_clear boolean,target_available boolean,
  current_control_version integer,all_ready boolean
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_runtime_readiness_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE observed_digest text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_target_session IS NULL THEN
    RAISE EXCEPTION 'automation readiness scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  control_configured:=control_row.user_id IS NOT NULL AND control_row.control_state='DISARMED'
    AND control_row.brokerage_mode='KIS_MOCK' AND control_row.baseline_account_digest~'^[0-9a-f]{64}$';
  certification_valid:=gate_row.user_id IS NOT NULL AND gate_row.certification_status='VALID'
    AND gate_row.certification_receipt_sha256 IS NOT NULL
    AND gate_row.strategy_eligible_from_session_date IS NOT NULL
    AND p_target_session>=gate_row.strategy_eligible_from_session_date;
  release_source_bound:=gate_row.user_id IS NOT NULL AND gate_row.clean_release_binding
    AND gate_row.release_binding_sha256 IS NOT NULL AND gate_row.source_binding_sha256 IS NOT NULL;
  real_team_b_ready:=gate_row.user_id IS NOT NULL AND gate_row.real_team_b_pointer_active
    AND gate_row.team_b_integrity_receipt_sha256 IS NOT NULL
    AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
    AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1;
  principle_current:=control_row.user_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.principles principle
    WHERE principle.user_id=p_user_id AND principle.principle_id=control_row.principle_id
      AND principle.status='ACTIVE'
  );
  kill_switch_inactive:=COALESCE((
    SELECT NOT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'
  ),false);
  IF control_configured THEN
    observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=control_row.baseline_account_digest;
  unresolved_state_clear:=control_row.user_id IS NOT NULL
    AND public.p1_automation_open_work_clear_v3(p_user_id,control_row.account_id);
  target_available:=NOT EXISTS (
    SELECT 1 FROM public.automation_runtime_schedule schedule
    WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
      AND schedule.schedule_state IN ('ARMED','CLAIMED')
  );
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND certification_valid AND release_source_bound
    AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$p1_automation_runtime_readiness_v1$;

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
    WHERE manifest.manifest_kind='DAILY' AND manifest.status='ACCEPTED'
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

CREATE OR REPLACE FUNCTION public.p1_advance_automation_checkpoint_v2(
  p_run_id text,p_claim_token_hash text,p_tick_identity_hash text,p_expected_version integer,
  p_next_state text,p_selected_symbol text,p_selected_side text,p_decision_id text,
  p_vertex_call_count integer,p_provider_call_count integer,p_logical_submit_count integer,
  p_reservation_id text,p_quantity bigint,p_limit_price_krw bigint,p_exact_intent_json text,
  p_exact_intent_sha256 text,p_quote_snapshot_json text,p_policy_id text,p_policy_version integer,
  p_position_expiry_session date,p_filled_quantity bigint,p_leaves_quantity bigint,
  p_unfilled_terminated_quantity bigint,p_average_fill_price_krw bigint,p_exit_reason text,
  p_expected_account_digest text,p_order_id text,p_provider_order_ref_hash text,p_result_hash text,
  p_event_type text,p_event_payload_hash text
)
RETURNS TABLE(checkpoint_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_checkpoint_v2$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE prior_tick public.automation_processed_ticks%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE decision_row public.decisions%ROWTYPE;
DECLARE artifact_row public.decision_artifacts%ROWTYPE;
DECLARE position_row public.automation_positions%ROWTYPE;
DECLARE next_version integer;
DECLARE sequence_value integer;
DECLARE event_seed text;
DECLARE terminal boolean;
DECLARE estimated_amount bigint;
DECLARE remaining_quantity bigint;
DECLARE exit_quantity_total bigint;
DECLARE exit_price_weighted bigint;
DECLARE realized_delta bigint;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_result_hash!~'^sha256:[0-9a-f]{64}$' OR p_event_payload_hash!~'^[0-9a-f]{64}$'
     OR p_expected_version<1 OR p_vertex_call_count NOT BETWEEN 0 AND 1
     OR p_provider_call_count NOT BETWEEN 0 AND 16 OR p_logical_submit_count NOT BETWEEN 0 AND 1
     OR p_filled_quantity<0 OR p_leaves_quantity<0 OR p_unfilled_terminated_quantity<0
     OR (p_average_fill_price_krw IS NULL)<>(p_filled_quantity=0)
     OR (p_exit_reason IS NOT NULL AND p_exit_reason NOT IN (
       'STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT'
     ))
     OR p_event_type NOT IN ('CONTROL_CHANGED','RUN_TRANSITIONED','BASELINE_CAPTURED','ACCOUNT_RECONCILED',
       'EXIT_SELECTED','BUY_SELECTED','NEWS_RESULT_RECORDED','RISK_RESULT_RECORDED','ORDER_RESERVED',
       'ORDER_OUTCOME_RECORDED','CANCEL_RECORDED','DRIFT_DETECTED','RUN_HALTED') THEN
    RAISE EXCEPTION 'automation v2 checkpoint input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO prior_tick FROM public.automation_processed_ticks
  WHERE run_id=p_run_id AND tick_identity_hash=p_tick_identity_hash;
  IF FOUND THEN
    IF prior_tick.result_hash<>p_result_hash THEN
      RAISE EXCEPTION 'automation tick identity conflict' USING ERRCODE='23505';
    END IF;
    checkpoint_version:=prior_tick.checkpoint_version;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id FOR UPDATE;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR UPDATE;
  IF checkpoint_row.run_id IS NULL OR checkpoint_row.checkpoint_version<>p_expected_version
     OR NOT public.p1_automation_transition_valid_v2(checkpoint_row.state,p_next_state)
     OR p_vertex_call_count<checkpoint_row.vertex_call_count
     OR p_provider_call_count<checkpoint_row.provider_call_count
     OR p_logical_submit_count<checkpoint_row.logical_submit_count
     OR control_row.policy_id IS NULL OR control_row.policy_id IS DISTINCT FROM p_policy_id
     OR control_row.policy_version IS DISTINCT FROM p_policy_version
     OR control_row.expected_account_digest_v2 IS DISTINCT FROM p_expected_account_digest THEN
    RAISE EXCEPTION 'automation v2 checkpoint CAS conflict' USING ERRCODE='40001';
  END IF;
  IF control_row.control_state='HALTED' OR (
    control_row.control_state='DISARMED' AND checkpoint_row.state NOT IN (
      'RECONCILING_PREVIOUS','ORDER_SUBMITTED','PENDING_RECONCILIATION'
    )
  ) THEN RAISE EXCEPTION 'automation control disallows new work' USING ERRCODE='40001'; END IF;
  IF p_quote_snapshot_json IS NOT NULL THEN
    IF octet_length(p_quote_snapshot_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_quote_snapshot_json::jsonb)<>'object'
       OR p_quote_snapshot_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol THEN
      RAISE EXCEPTION 'automation quote snapshot invalid' USING ERRCODE='22023';
    END IF;
    IF checkpoint_row.quote_snapshot_json IS NOT NULL
       AND checkpoint_row.quote_snapshot_json::jsonb<>p_quote_snapshot_json::jsonb THEN
      RAISE EXCEPTION 'automation quote snapshot drift' USING ERRCODE='40001';
    END IF;
  END IF;
  IF p_reservation_id IS NOT NULL THEN
    IF p_reservation_id!~'^auto_res_[0-9a-f]{32}$' OR p_quantity<=0 OR p_limit_price_krw<=0
       OR p_limit_price_krw>9223372036854775807/p_quantity
       OR p_exact_intent_json IS NULL OR octet_length(p_exact_intent_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_exact_intent_json::jsonb)<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(p_exact_intent_json::jsonb))<>8
       OR NOT p_exact_intent_json::jsonb ?& ARRAY[
         'estimatedAmount','estimatedPrice','orderType','quantity','side','strategyId','symbol','timeframe'
       ]
       OR p_exact_intent_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol
       OR p_exact_intent_json::jsonb->>'side' IS DISTINCT FROM p_selected_side
       OR p_exact_intent_json::jsonb->>'orderType'<>'LIMIT'
       OR (p_exact_intent_json::jsonb->>'quantity')::bigint<>p_quantity
       OR (p_exact_intent_json::jsonb->>'estimatedPrice')::bigint<>p_limit_price_krw
       OR p_exact_intent_json::jsonb->>'strategyId'<>control_row.strategy_id
       OR p_exact_intent_json::jsonb->>'timeframe'<>'1d'
       OR p_exact_intent_sha256!~'^[0-9a-f]{64}$'
       OR encode(public.digest(convert_to(p_exact_intent_json,'UTF8'),'sha256'),'hex')<>p_exact_intent_sha256
       OR p_quote_snapshot_json IS NULL THEN
      RAISE EXCEPTION 'automation exact order intent invalid' USING ERRCODE='22023';
    END IF;
    estimated_amount:=(p_exact_intent_json::jsonb->>'estimatedAmount')::bigint;
    IF estimated_amount<>p_quantity*p_limit_price_krw
       OR p_filled_quantity+p_leaves_quantity+p_unfilled_terminated_quantity<>p_quantity THEN
      RAISE EXCEPTION 'automation order quantity conservation failed' USING ERRCODE='40001';
    END IF;
    SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id FOR UPDATE;
    IF FOUND THEN
      IF reservation_row.reservation_id<>p_reservation_id OR reservation_row.symbol<>p_selected_symbol
         OR reservation_row.side<>p_selected_side OR reservation_row.quantity<>p_quantity
         OR reservation_row.limit_price_krw<>p_limit_price_krw
         OR reservation_row.estimated_amount_krw<>estimated_amount
         OR reservation_row.order_intent_sha256<>p_exact_intent_sha256
         OR reservation_row.exact_intent_json::jsonb<>p_exact_intent_json::jsonb
         OR p_filled_quantity<reservation_row.filled_quantity
         OR p_unfilled_terminated_quantity<reservation_row.unfilled_terminated_quantity THEN
        RAISE EXCEPTION 'automation reservation drift' USING ERRCODE='40001';
      END IF;
      UPDATE public.automation_order_reservations SET
        logical_submit_count=p_logical_submit_count,
        order_id=COALESCE(automation_order_reservations.order_id,p_order_id),
        provider_order_ref_hash=COALESCE(automation_order_reservations.provider_order_ref_hash,p_provider_order_ref_hash),
        filled_quantity=p_filled_quantity,leaves_quantity=p_leaves_quantity,
        unfilled_terminated_quantity=p_unfilled_terminated_quantity,
        average_fill_price_krw=p_average_fill_price_krw,
        reconciliation_status=CASE
          WHEN p_next_state IN ('COMPLETED','CANCELLED_UNFILLED') THEN 'MATCHED'
          ELSE automation_order_reservations.reconciliation_status END,
        exit_reason=COALESCE(automation_order_reservations.exit_reason,p_exit_reason),
        updated_at=statement_timestamp()
      WHERE run_id=p_run_id;
    ELSE
      IF checkpoint_row.state<>'ORDER_SIZING' OR p_next_state<>'RISK_CHECKING' THEN
        RAISE EXCEPTION 'automation reservation must precede risk' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_order_reservations(
        reservation_id,run_id,user_id,session_date,symbol,side,quantity,limit_price_krw,
        logical_submit_count,order_id,provider_order_ref_hash,created_at,updated_at,
        estimated_amount_krw,strategy_id,policy_id,policy_version,principle_version_id,
        exact_intent_json,quote_snapshot_json,order_intent_sha256,filled_quantity,leaves_quantity,
        unfilled_terminated_quantity,average_fill_price_krw,reconciliation_status,exit_reason
      ) VALUES (
        p_reservation_id,p_run_id,claim_row.user_id,claim_row.session_date,p_selected_symbol,
        p_selected_side,p_quantity,p_limit_price_krw,p_logical_submit_count,p_order_id,
        p_provider_order_ref_hash,statement_timestamp(),statement_timestamp(),estimated_amount,
        control_row.strategy_id,control_row.policy_id,control_row.policy_version,
        control_row.principle_version_id,p_exact_intent_json,p_quote_snapshot_json,
        p_exact_intent_sha256,p_filled_quantity,p_leaves_quantity,p_unfilled_terminated_quantity,
        p_average_fill_price_krw,'NOT_APPLICABLE',p_exit_reason
      );
    END IF;
    IF p_decision_id IS NOT NULL THEN
      SELECT * INTO decision_row FROM public.decisions
      WHERE decision_id=p_decision_id AND user_id=claim_row.user_id;
      SELECT * INTO artifact_row FROM public.decision_artifacts WHERE decision_id=p_decision_id;
      IF decision_row.decision_id IS NULL OR decision_row.outcome<>'ALLOW'
         OR NOT decision_row.can_submit_order OR decision_row.enforcement_action<>'NONE'
         OR decision_row.portfolio_source<>'KIS_MOCK'
         OR decision_row.principle_version_id<>control_row.principle_version_id
         OR decision_row.valid_until<=statement_timestamp() OR artifact_row.decision_id IS NULL
         OR EXISTS (
           SELECT 1 FROM jsonb_object_keys(p_exact_intent_json::jsonb) key
           WHERE artifact_row.snapshot_artifact_canonical_json::jsonb->'orderIntent'->>key
             IS DISTINCT FROM p_exact_intent_json::jsonb->>key
         ) THEN
        RAISE EXCEPTION 'automation exact Decision intent mismatch' USING ERRCODE='40001';
      END IF;
    END IF;
  ELSIF p_quantity IS NOT NULL OR p_limit_price_krw IS NOT NULL OR p_exact_intent_json IS NOT NULL
     OR p_exact_intent_sha256 IS NOT NULL OR p_order_id IS NOT NULL OR p_provider_order_ref_hash IS NOT NULL
     OR p_filled_quantity<>0 OR p_leaves_quantity<>0 OR p_unfilled_terminated_quantity<>0
     OR p_average_fill_price_krw IS NOT NULL THEN
    RAISE EXCEPTION 'automation order state lacks reservation' USING ERRCODE='22023';
  END IF;
  IF p_next_state='EXIT_SELECTED' AND p_selected_side='SELL' THEN
    UPDATE public.automation_positions SET status='EXIT_PENDING',exit_reason=p_exit_reason
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='OPEN';
    IF NOT FOUND THEN RAISE EXCEPTION 'automation SELL lot unavailable' USING ERRCODE='40001'; END IF;
  END IF;
  IF p_next_state='COMPLETED' AND p_reservation_id IS NOT NULL AND p_filled_quantity>0 THEN
    SELECT * INTO policy_row FROM public.automation_policy_versions
    WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
    IF p_selected_side='BUY' THEN
      IF p_position_expiry_session IS NULL OR p_position_expiry_session<=claim_row.session_date
         OR p_order_id IS NULL THEN
        RAISE EXCEPTION 'automation completed BUY evidence invalid' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_positions(
        position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
        bot_owned,short_allowed,created_at,closed_at,entry_order_id,entry_ordered_quantity,
        entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,
        policy_id,policy_version,stop_loss_bps,take_profit_bps,exit_filled_quantity,
        exit_average_fill_price_krw,exit_reason
      ) VALUES (
        'auto_pos_'||substr(encode(public.digest(convert_to(
          p_run_id||':'||p_selected_symbol||':'||claim_row.session_date::text,'UTF8'),'sha256'),'hex'),1,32),
        claim_row.user_id,control_row.account_id,p_selected_symbol,p_filled_quantity,
        claim_row.session_date,p_position_expiry_session,'OPEN',true,false,statement_timestamp(),NULL,
        p_order_id,p_quantity,p_filled_quantity,p_unfilled_terminated_quantity,p_average_fill_price_krw,
        control_row.policy_id,control_row.policy_version,policy_row.stop_loss_bps,
        policy_row.take_profit_bps,0,NULL,NULL
      );
    ELSE
      SELECT * INTO position_row FROM public.automation_positions
      WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
        AND symbol=p_selected_symbol AND status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
      IF NOT FOUND OR position_row.quantity<p_filled_quantity THEN
        RAISE EXCEPTION 'automation completed SELL lot unavailable' USING ERRCODE='40001';
      END IF;
      remaining_quantity:=position_row.quantity-p_filled_quantity;
      -- 부분 청산이 이어져도 엔진(app/p1_owner/automation.py::_apply_fill)과 같은 값이 되도록
      -- 수량가중 평균으로 누적하고, 왕복 비용 35bp를 올림 적용한 실현손익을 함께 적는다.
      exit_quantity_total:=position_row.exit_filled_quantity+p_filled_quantity;
      exit_price_weighted:=(
        position_row.exit_filled_quantity*COALESCE(position_row.exit_average_fill_price_krw,0)
        + p_filled_quantity*p_average_fill_price_krw
      )/exit_quantity_total;
      realized_delta:=NULL;
      IF position_row.entry_average_fill_price_krw IS NOT NULL THEN
        realized_delta:=
          (p_average_fill_price_krw-position_row.entry_average_fill_price_krw)*p_filled_quantity
          - (
            (p_average_fill_price_krw+position_row.entry_average_fill_price_krw)
              *p_filled_quantity*35+19999
          )/20000;
      END IF;
      UPDATE public.automation_positions SET
        quantity=remaining_quantity,exit_filled_quantity=exit_quantity_total,
        exit_average_fill_price_krw=exit_price_weighted,exit_reason=p_exit_reason,
        realized_pnl_krw=CASE
          WHEN realized_delta IS NULL THEN realized_pnl_krw
          ELSE COALESCE(realized_pnl_krw,0)+realized_delta
        END,
        status=CASE WHEN remaining_quantity=0 THEN 'CLOSED' ELSE 'OPEN' END,
        closed_at=CASE WHEN remaining_quantity=0 THEN statement_timestamp() ELSE NULL END
      WHERE position_id=position_row.position_id;
    END IF;
  ELSIF p_selected_side='SELL' AND p_next_state IN (
    'NEWS_VETOED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ) THEN
    UPDATE public.automation_positions SET status='OPEN'
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='EXIT_PENDING';
  END IF;
  next_version:=checkpoint_row.checkpoint_version+1;
  UPDATE public.automation_runtime_checkpoint SET
    checkpoint_version=next_version,state=p_next_state,selected_symbol=p_selected_symbol,
    selected_side=p_selected_side,decision_id=COALESCE(automation_runtime_checkpoint.decision_id,p_decision_id),
    vertex_call_count=p_vertex_call_count,provider_call_count=p_provider_call_count,
    logical_submit_count=p_logical_submit_count,
    quote_snapshot_json=COALESCE(automation_runtime_checkpoint.quote_snapshot_json,p_quote_snapshot_json),
    exit_reason=COALESCE(automation_runtime_checkpoint.exit_reason,p_exit_reason),updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND checkpoint_version=p_expected_version;
  UPDATE public.automation_runs SET
    state=p_next_state,selected_symbol=p_selected_symbol,selected_side=p_selected_side,
    physical_submit_count=p_logical_submit_count,vertex_call_count=p_vertex_call_count,
    provider_calls=p_provider_call_count,policy_id=control_row.policy_id,
    policy_version=control_row.policy_version,
    expected_account_digest_before=COALESCE(expected_account_digest_before,control_row.expected_account_digest_v2),
    updated_at=statement_timestamp() WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  event_seed:=p_run_id||':'||sequence_value::text||':'||p_event_type||':'||p_event_payload_hash;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,p_event_type,statement_timestamp(),p_event_payload_hash,
    p_provider_call_count,p_logical_submit_count,true
  );
  INSERT INTO public.automation_processed_ticks(
    run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at
  ) VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  terminal:=p_next_state IN ('NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED',
    'SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED');
  IF p_next_state='HALTED' AND control_row.control_state<>'HALTED' THEN
    UPDATE public.automation_control SET control_state='HALTED',version=version+1,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id;
  END IF;
  IF terminal THEN
    UPDATE public.automation_runtime_claim SET claim_state='RELEASED',released_at=statement_timestamp()
    WHERE run_id=p_run_id;
    UPDATE public.automation_runtime_schedule SET
      schedule_state=CASE WHEN p_next_state='HALTED' THEN 'HALTED' ELSE 'COMPLETED' END,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id AND session_date=claim_row.session_date;
  END IF;
  checkpoint_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_advance_automation_checkpoint_v2$;
