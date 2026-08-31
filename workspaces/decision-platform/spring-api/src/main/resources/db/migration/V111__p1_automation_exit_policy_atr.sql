-- Automation V3 user exit policy and deterministic ATR state.  This migration
-- adds no provider, account, or order authority and preserves V1/V2 functions.

CREATE FUNCTION public.p1_automation_policy_profile_v2(
  p_stop_loss_bps integer,
  p_take_profit_bps integer,
  p_max_holding_sessions integer,
  p_atr_period integer,
  p_atr_multiplier_milli integer,
  p_model_sell_enabled boolean
) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_policy_profile_v2$
  SELECT CASE (
    p_stop_loss_bps,p_take_profit_bps,p_max_holding_sessions,p_atr_period,
    p_atr_multiplier_milli,p_model_sell_enabled
  )
    WHEN (300,500,20,22,2500,true) THEN 'CONSERVATIVE'
    WHEN (500,1000,60,22,3000,true) THEN 'BALANCED'
    WHEN (800,1500,0,22,3500,true) THEN 'AGGRESSIVE'
    ELSE 'CUSTOM'
  END
$p1_automation_policy_profile_v2$;

ALTER TABLE public.automation_policy_versions
  DROP CONSTRAINT automation_policy_versions_check,
  ADD COLUMN max_holding_sessions integer,
  ADD COLUMN atr_period integer,
  ADD COLUMN atr_multiplier_milli integer,
  ADD COLUMN model_sell_enabled boolean,
  ADD CONSTRAINT automation_policy_versions_v3_shape_check CHECK (
    (
      max_holding_sessions IS NULL AND atr_period IS NULL
      AND atr_multiplier_milli IS NULL AND model_sell_enabled IS NULL
      AND risk_profile=public.p1_automation_policy_profile_v1(stop_loss_bps,take_profit_bps)
    ) OR (
      max_holding_sessions BETWEEN 0 AND 1260
      AND atr_period BETWEEN 5 AND 100
      AND atr_multiplier_milli BETWEEN 1000 AND 10000
      AND atr_multiplier_milli%100=0
      AND model_sell_enabled IS NOT NULL
      AND risk_profile=public.p1_automation_policy_profile_v2(
        stop_loss_bps,take_profit_bps,max_holding_sessions,atr_period,
        atr_multiplier_milli,model_sell_enabled
      )
    )
  );

ALTER TABLE public.automation_runtime_checkpoint
  DROP CONSTRAINT automation_checkpoint_exit_reason_v2_check,
  ADD CONSTRAINT automation_checkpoint_exit_reason_v3_check CHECK (
    exit_reason IS NULL OR exit_reason IN (
      'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
    )
  );

ALTER TABLE public.automation_order_reservations
  DROP CONSTRAINT automation_reservation_fill_v2_check,
  ADD CONSTRAINT automation_reservation_fill_v3_check CHECK (
    filled_quantity>=0 AND unfilled_terminated_quantity>=0
      AND (leaves_quantity IS NULL OR leaves_quantity>=0)
      AND (average_fill_price_krw IS NULL OR average_fill_price_krw>0)
      AND reconciliation_status IN ('NOT_APPLICABLE','MATCHED','MISMATCH')
      AND (exit_reason IS NULL OR exit_reason IN (
        'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
      ))
  );

ALTER TABLE public.automation_positions
  NO FORCE ROW LEVEL SECURITY;

ALTER TABLE public.automation_positions
  DROP CONSTRAINT automation_positions_check,
  DROP CONSTRAINT automation_position_v2_shape_check,
  ALTER COLUMN expiry_session DROP NOT NULL,
  ADD COLUMN max_holding_sessions integer,
  ADD COLUMN atr_period integer,
  ADD COLUMN atr_multiplier_milli integer,
  ADD COLUMN model_sell_enabled boolean,
  ADD COLUMN peak_price_krw bigint,
  ADD COLUMN atr_as_of_session date,
  ADD COLUMN trailing_stop_krw bigint,
  ADD COLUMN atr_status text NOT NULL DEFAULT 'LEGACY',
  ADD CONSTRAINT automation_positions_expiry_v3_check CHECK (
    expiry_session IS NULL OR expiry_session>entry_session
  ),
  ADD CONSTRAINT automation_position_v2_shape_check CHECK (
    (policy_id IS NULL AND policy_version IS NULL AND entry_order_id IS NULL
      AND entry_ordered_quantity IS NULL AND entry_filled_quantity IS NULL
      AND entry_unfilled_quantity IS NULL AND entry_average_fill_price_krw IS NULL
      AND stop_loss_bps IS NULL AND take_profit_bps IS NULL)
    OR
    (policy_id~'^auto_pol_[0-9a-f]{32}$' AND policy_version>=1
      AND entry_order_id~'^ord_mock_[0-9a-f]{32}$'
      AND entry_ordered_quantity>0 AND entry_filled_quantity>0 AND entry_unfilled_quantity>=0
      AND entry_ordered_quantity=entry_filled_quantity+entry_unfilled_quantity
      AND entry_average_fill_price_krw>0
      AND stop_loss_bps BETWEEN 100 AND 1500 AND take_profit_bps BETWEEN 200 AND 3000
      AND take_profit_bps>stop_loss_bps
      AND ((status='CLOSED')=(quantity=0))
      AND exit_filled_quantity>=0 AND quantity+exit_filled_quantity=entry_filled_quantity
      AND (exit_average_fill_price_krw IS NULL OR exit_average_fill_price_krw>0)
      AND (exit_reason IS NULL OR exit_reason IN (
        'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
      )))
  ),
  ADD CONSTRAINT automation_position_v3_snapshot_check CHECK (
    (
      max_holding_sessions IS NULL AND atr_period IS NULL
      AND atr_multiplier_milli IS NULL AND model_sell_enabled IS NULL
      AND peak_price_krw IS NULL AND atr_as_of_session IS NULL
      AND trailing_stop_krw IS NULL AND atr_status='LEGACY'
    ) OR (
      max_holding_sessions BETWEEN 0 AND 1260
      AND atr_period BETWEEN 5 AND 100
      AND atr_multiplier_milli BETWEEN 1000 AND 10000
      AND atr_multiplier_milli%100=0 AND model_sell_enabled IS NOT NULL
      AND peak_price_krw>0
      AND ((max_holding_sessions=0 AND expiry_session IS NULL)
        OR (max_holding_sessions>0 AND expiry_session IS NOT NULL))
      AND atr_status IN ('AVAILABLE','UNAVAILABLE')
      AND (
        (atr_status='AVAILABLE' AND atr_as_of_session IS NOT NULL AND trailing_stop_krw>0)
        OR (atr_status='UNAVAILABLE')
      )
    )
  );

ALTER TABLE public.automation_positions
  FORCE ROW LEVEL SECURITY;

CREATE FUNCTION public.p1_put_automation_policy_v2(
  p_user_id text,p_principle_id text,p_capital_limit_krw bigint,p_stop_loss_bps integer,
  p_take_profit_bps integer,p_max_holding_sessions integer,p_atr_period integer,
  p_atr_multiplier_milli integer,p_model_sell_enabled boolean,p_expected_version integer,
  p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_put_automation_policy_v2$
DECLARE base_result record;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE projection jsonb;
DECLARE profile text;
BEGIN
  IF p_max_holding_sessions NOT BETWEEN 0 AND 1260 OR p_atr_period NOT BETWEEN 5 AND 100
     OR p_atr_multiplier_milli NOT BETWEEN 1000 AND 10000
     OR p_atr_multiplier_milli%100<>0 OR p_model_sell_enabled IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy input invalid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO base_result FROM public.p1_put_automation_policy_v1(
    p_user_id,p_principle_id,p_capital_limit_krw,p_stop_loss_bps,p_take_profit_bps,
    p_expected_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  profile:=public.p1_automation_policy_profile_v2(
    p_stop_loss_bps,p_take_profit_bps,p_max_holding_sessions,p_atr_period,
    p_atr_multiplier_milli,p_model_sell_enabled
  );
  UPDATE public.automation_policy_versions SET
    max_holding_sessions=p_max_holding_sessions,atr_period=p_atr_period,
    atr_multiplier_milli=p_atr_multiplier_milli,model_sell_enabled=p_model_sell_enabled,
    risk_profile=profile
  WHERE policy_id=base_result.result_json::jsonb->>'policyId'
    AND version=(base_result.result_json::jsonb->>'version')::integer
  RETURNING * INTO policy_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation v3 policy write unavailable' USING ERRCODE='40001';
  END IF;
  projection:=jsonb_build_object(
    'atrMultiplierMilli',policy_row.atr_multiplier_milli,'atrPeriod',policy_row.atr_period,
    'buyCutoffTimeKst','09:40','cancelTimeKst','15:20',
    'capitalLimitKrw',policy_row.capital_limit_krw,'contractId','automation-policy.v2',
    'createdAt',policy_row.created_at,'evaluationTimeKst','09:30',
    'maxHoldingSessions',policy_row.max_holding_sessions,'maxNewOrdersPerSession',1,
    'maxOpenPositions',5,'modelSellEnabled',policy_row.model_sell_enabled,
    'policyId',policy_row.policy_id,'presetId',lower(policy_row.risk_profile),
    'stopLossBps',policy_row.stop_loss_bps,'takeProfitBps',policy_row.take_profit_bps,
    'updatedAt',policy_row.created_at,'version',policy_row.version
  );
  UPDATE public.automation_policy_idempotency SET result_json=projection
  WHERE scope_hash=p_scope_hash AND user_id=p_user_id;
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_put_automation_policy_v2$;

CREATE FUNCTION public.p1_arm_automation_v3(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text,
  p_provider_capability_ready boolean
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_v3$
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE base_result record;
DECLARE history_ready boolean;
BEGIN
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR policy_row.max_holding_sessions IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy unavailable' USING ERRCODE='40001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions
    WHERE user_id=p_user_id AND status IN ('OPEN','EXIT_PENDING')
      AND max_holding_sessions IS NULL
  ) THEN
    RAISE EXCEPTION 'LEGACY_POSITION_PRESENT' USING ERRCODE='P1L01';
  END IF;
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
  SELECT * INTO base_result FROM public.p1_arm_automation_v2(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,
    p_expected_control_version,p_scope_hash,p_request_hash
  );
  result_json:=base_result.result_json;replayed:=base_result.replayed;RETURN NEXT;
END
$p1_arm_automation_v3$;

CREATE FUNCTION public.p1_read_automation_market_history_status_owner_v1(p_user_id text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_market_history_status_owner_v1$
DECLARE result text;
DECLARE universe_count bigint;
DECLARE insufficient_count bigint;
DECLARE required_period integer;
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation market history owner scope denied' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO universe_count FROM public.market_data_operational_universe;
  SELECT COALESCE(atr_period,22) INTO required_period
  FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND max_holding_sessions IS NOT NULL
  ORDER BY version DESC LIMIT 1;
  required_period:=COALESCE(required_period,22);
  SELECT count(*) INTO insufficient_count
  FROM public.market_data_operational_universe universe
  WHERE (SELECT count(*) FROM public.market_data_operational_bars history
         WHERE history.symbol=universe.symbol)<required_period+1;
  result:=CASE
    WHEN NOT EXISTS (SELECT 1 FROM public.market_data_manifests)
      OR NOT EXISTS (SELECT 1 FROM public.market_data_bars) OR universe_count=0 THEN 'EMPTY'
    WHEN universe_count<>31 OR insufficient_count>0 THEN 'PARTIAL'
    ELSE 'READY'
  END;
  RETURN result;
END
$p1_read_automation_market_history_status_owner_v1$;

CREATE FUNCTION public.p1_read_automation_runtime_state_v3(
  p_run_id text,p_claim_token_hash text
) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_runtime_state_v3$
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
  SELECT * INTO policy_row FROM public.automation_policy_versions
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
  FROM public.automation_positions position
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
$p1_read_automation_runtime_state_v3$;

CREATE FUNCTION public.p1_advance_automation_checkpoint_v3(
  p_run_id text,p_claim_token_hash text,p_tick_identity_hash text,p_expected_version integer,
  p_next_state text,p_selected_symbol text,p_selected_side text,p_decision_id text,
  p_vertex_call_count integer,p_provider_call_count integer,p_logical_submit_count integer,
  p_reservation_id text,p_quantity bigint,p_limit_price_krw bigint,p_exact_intent_json text,
  p_exact_intent_sha256 text,p_quote_snapshot_json text,p_policy_id text,p_policy_version integer,
  p_position_expiry_session date,p_filled_quantity bigint,p_leaves_quantity bigint,
  p_unfilled_terminated_quantity bigint,p_average_fill_price_krw bigint,p_exit_reason text,
  p_expected_account_digest text,p_order_id text,p_provider_order_ref_hash text,p_result_hash text,
  p_event_type text,p_event_payload_hash text,p_position_state_json text
) RETURNS TABLE(checkpoint_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_checkpoint_v3$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE base_result record;
DECLARE mapped_exit_reason text;
DECLARE mapped_expiry date;
DECLARE state_count integer;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_position_state_json IS NULL OR octet_length(p_position_state_json) NOT BETWEEN 2 AND 16384
     OR jsonb_typeof(p_position_state_json::jsonb)<>'array'
     OR jsonb_array_length(p_position_state_json::jsonb)>5
     OR (p_exit_reason IS NOT NULL AND p_exit_reason NOT IN (
       'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
     )) THEN
    RAISE EXCEPTION 'automation v3 checkpoint input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE policy_id=p_policy_id AND version=p_policy_version AND user_id=claim_row.user_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation policy unavailable' USING ERRCODE='40001'; END IF;
  mapped_exit_reason:=CASE WHEN p_exit_reason='ATR_TRAILING' THEN 'STOP_LOSS' ELSE p_exit_reason END;
  mapped_expiry:=CASE
    WHEN policy_row.max_holding_sessions=0 AND p_next_state='COMPLETED'
      AND p_selected_side='BUY' THEN claim_row.session_date+1
    ELSE p_position_expiry_session
  END;
  SELECT * INTO base_result FROM public.p1_advance_automation_checkpoint_v2(
    p_run_id,p_claim_token_hash,p_tick_identity_hash,p_expected_version,p_next_state,
    p_selected_symbol,p_selected_side,p_decision_id,p_vertex_call_count,LEAST(p_provider_call_count,16),
    p_logical_submit_count,p_reservation_id,p_quantity,p_limit_price_krw,p_exact_intent_json,
    p_exact_intent_sha256,p_quote_snapshot_json,p_policy_id,p_policy_version,mapped_expiry,
    p_filled_quantity,p_leaves_quantity,p_unfilled_terminated_quantity,p_average_fill_price_krw,
    mapped_exit_reason,p_expected_account_digest,p_order_id,p_provider_order_ref_hash,p_result_hash,
    p_event_type,p_event_payload_hash
  );
  checkpoint_version:=base_result.checkpoint_version;replayed:=base_result.replayed;
  IF replayed OR policy_row.max_holding_sessions IS NULL THEN RETURN NEXT;RETURN; END IF;
  INSERT INTO public.automation_v3_usage(
    run_id,user_id,provider_call_count,screening_provider_call_count,
    grounding_query_count,candidate_set_sha256,evidence_set_sha256,updated_at
  ) VALUES (
    p_run_id,claim_row.user_id,p_provider_call_count,0,0,NULL,NULL,statement_timestamp()
  ) ON CONFLICT (run_id) DO UPDATE SET
    provider_call_count=excluded.provider_call_count,updated_at=excluded.updated_at;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_position_state_json::jsonb) item
    WHERE jsonb_typeof(item)<>'object'
      OR NOT item ?& ARRAY[
        'atrAsOfSession','atrMultiplierMilli','atrPeriod','atrStatus','exitReason',
        'expirySession','maxHoldingSessions','modelSellEnabled','peakPriceKrw',
        'positionId','trailingStopKrw'
      ]
      OR item->>'positionId'!~'^auto_pos_[A-Za-z0-9_-]{8,96}$'
      OR item->>'atrStatus' NOT IN ('AVAILABLE','UNAVAILABLE')
      OR (item->>'peakPriceKrw')!~'^[1-9][0-9]*$'
      OR (item->>'maxHoldingSessions')!~'^(0|[1-9][0-9]{0,3})$'
      OR (item->>'atrPeriod')!~'^[1-9][0-9]{0,2}$'
      OR (item->>'atrMultiplierMilli')!~'^[1-9][0-9]{3,4}$'
      OR jsonb_typeof(item->'modelSellEnabled')<>'boolean'
      OR (item->>'trailingStopKrw' IS NOT NULL AND item->>'trailingStopKrw'!~'^[1-9][0-9]*$')
      OR (item->>'atrAsOfSession' IS NOT NULL AND item->>'atrAsOfSession'!~'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
      OR (item->>'expirySession' IS NOT NULL AND item->>'expirySession'!~'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
      OR (item->>'exitReason' IS NOT NULL AND item->>'exitReason' NOT IN (
        'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
      ))
  ) THEN RAISE EXCEPTION 'automation v3 position state invalid' USING ERRCODE='22023'; END IF;
  SELECT count(*) INTO state_count FROM jsonb_array_elements(p_position_state_json::jsonb);
  IF state_count<>(
    SELECT count(DISTINCT item->>'positionId')
    FROM jsonb_array_elements(p_position_state_json::jsonb) item
  ) OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_position_state_json::jsonb) item
    WHERE NOT EXISTS (
      SELECT 1 FROM public.automation_positions position
      WHERE position.position_id=item->>'positionId' AND position.user_id=claim_row.user_id
    )
  ) OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_position_state_json::jsonb) item
    JOIN public.automation_positions position ON position.position_id=item->>'positionId'
    WHERE position.user_id=claim_row.user_id AND position.max_holding_sessions IS NOT NULL
      AND (
        position.max_holding_sessions<>(item->>'maxHoldingSessions')::integer
        OR position.atr_period<>(item->>'atrPeriod')::integer
        OR position.atr_multiplier_milli<>(item->>'atrMultiplierMilli')::integer
        OR position.model_sell_enabled<>(item->>'modelSellEnabled')::boolean
        OR position.expiry_session IS DISTINCT FROM (item->>'expirySession')::date
      )
  ) THEN RAISE EXCEPTION 'automation v3 position ownership invalid' USING ERRCODE='42501'; END IF;
  UPDATE public.automation_positions position SET
    max_holding_sessions=COALESCE(position.max_holding_sessions,(item.value->>'maxHoldingSessions')::integer),
    atr_period=COALESCE(position.atr_period,(item.value->>'atrPeriod')::integer),
    atr_multiplier_milli=COALESCE(position.atr_multiplier_milli,(item.value->>'atrMultiplierMilli')::integer),
    model_sell_enabled=COALESCE(position.model_sell_enabled,(item.value->>'modelSellEnabled')::boolean),
    peak_price_krw=(item.value->>'peakPriceKrw')::bigint,
    atr_as_of_session=(item.value->>'atrAsOfSession')::date,
    trailing_stop_krw=(item.value->>'trailingStopKrw')::bigint,
    atr_status=item.value->>'atrStatus',
    expiry_session=CASE WHEN position.max_holding_sessions IS NULL
      THEN (item.value->>'expirySession')::date ELSE position.expiry_session END,
    exit_reason=COALESCE((item.value->>'exitReason'),position.exit_reason)
  FROM jsonb_array_elements(p_position_state_json::jsonb) item
  WHERE position.position_id=item.value->>'positionId' AND position.user_id=claim_row.user_id;
  IF p_exit_reason='ATR_TRAILING' THEN
    UPDATE public.automation_runtime_checkpoint SET exit_reason='ATR_TRAILING' WHERE run_id=p_run_id;
    UPDATE public.automation_order_reservations SET exit_reason='ATR_TRAILING' WHERE run_id=p_run_id;
    UPDATE public.automation_positions SET exit_reason='ATR_TRAILING'
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol
      AND status IN ('OPEN','EXIT_PENDING','CLOSED');
  END IF;
  RETURN NEXT;
END
$p1_advance_automation_checkpoint_v3$;

ALTER FUNCTION public.p1_automation_policy_profile_v2(integer,integer,integer,integer,integer,boolean) OWNER TO flyway;
ALTER FUNCTION public.p1_put_automation_policy_v2(text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_market_history_status_owner_v1(text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_runtime_state_v3(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_advance_automation_checkpoint_v3(text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text,text) OWNER TO flyway;

REVOKE ALL ON FUNCTION public.p1_put_automation_policy_v2(text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text) FROM PUBLIC,decision_app,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean) FROM PUBLIC,decision_app,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_read_automation_market_history_status_owner_v1(text) FROM PUBLIC,decision_app,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_read_automation_runtime_state_v3(text,text) FROM PUBLIC,decision_app,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_advance_automation_checkpoint_v3(text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text,text) FROM PUBLIC,decision_app,decision_automation_runtime;

GRANT EXECUTE ON FUNCTION public.p1_put_automation_policy_v2(text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean) TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_market_history_status_owner_v1(text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_runtime_state_v3(text,text) TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_advance_automation_checkpoint_v3(text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text,text) TO decision_automation_runtime;
