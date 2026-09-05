-- Automatic recovery for a completed KIS Mock submit whose mutable reservation was
-- removed while append-only run/audit/outbox evidence and the bot position survived.
-- Recovery never recreates the deleted intent. It records a new account baseline only
-- after the encrypted provider reference, KIS execution, current balance, and local
-- position agree exactly.

ALTER TABLE public.automation_account_lineage
  DROP CONSTRAINT automation_account_lineage_reason_check;
ALTER TABLE public.automation_account_lineage
  ADD CONSTRAINT automation_account_lineage_reason_check CHECK (
    reason IN ('ARM_BASELINE','BUY_FILL','SELL_FILL','RECOVERY_BASELINE')
  );

CREATE FUNCTION public.p1_read_automation_recovery_candidate_v1(p_user_id text)
RETURNS TABLE(
  run_id text,account_id text,session_date date,position_id text,order_id text,
  symbol text,filled_quantity bigint,average_fill_price_krw bigint
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_recovery_candidate_v1$
DECLARE candidate_count integer;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$' THEN
    RAISE EXCEPTION 'automation recovery scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT count(*) INTO candidate_count
  FROM public.automation_runs run
  JOIN public.automation_control control ON control.user_id=run.user_id
  JOIN public.automation_positions position
    ON position.user_id=run.user_id AND position.account_id=control.account_id
   AND position.entry_session=run.session_date AND position.symbol=run.selected_symbol
   AND position.created_at=run.updated_at
  WHERE run.user_id=p_user_id AND run.state='COMPLETED' AND run.brokerage_mode='KIS_MOCK'
    AND run.selected_side='BUY' AND run.physical_submit_count=1
    AND control.control_state='DISARMED'
    AND position.entry_order_id IS NOT NULL
    AND position.entry_filled_quantity=position.entry_ordered_quantity
    AND position.entry_average_fill_price_krw>0
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_order_reservations reservation
      WHERE reservation.run_id=run.run_id
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_account_lineage lineage
      WHERE lineage.run_id=run.run_id AND lineage.reason='RECOVERY_BASELINE'
    )
    AND EXISTS (
      SELECT 1 FROM public.audit_logs audit
      WHERE audit.user_id=run.user_id AND audit.action='MOCK_ORDER_SUBMITTED'
        AND audit.target_type='ORDER' AND audit.target_id=position.entry_order_id
        AND audit.payload_json->>'orderId'=position.entry_order_id
        AND audit.payload_json->>'brokerageMode'='KIS_MOCK'
        AND audit.payload_json->>'status'='SUBMITTED'
        AND audit.created_at BETWEEN run.started_at AND run.updated_at
    )
    AND EXISTS (
      SELECT 1 FROM public.event_outbox event
      JOIN public.async_event_registry registry
        ON registry.event_type=event.event_type
       AND registry.outbox_schema_version=event.schema_version AND registry.enabled
      WHERE event.aggregate_id=position.entry_order_id
        AND event.event_type='brokerage.mock-order-submitted.v1'
        AND event.payload_json->>'orderId'=position.entry_order_id
        AND event.payload_json->>'brokerageMode'='KIS_MOCK'
        AND event.payload_json->>'status'='SUBMITTED'
    );
  IF candidate_count>1 THEN
    RAISE EXCEPTION 'automation recovery is ambiguous' USING ERRCODE='55000';
  END IF;
  RETURN QUERY
  SELECT run.run_id,control.account_id,run.session_date,position.position_id,
    position.entry_order_id,position.symbol,position.entry_filled_quantity,
    position.entry_average_fill_price_krw
  FROM public.automation_runs run
  JOIN public.automation_control control ON control.user_id=run.user_id
  JOIN public.automation_positions position
    ON position.user_id=run.user_id AND position.account_id=control.account_id
   AND position.entry_session=run.session_date AND position.symbol=run.selected_symbol
   AND position.created_at=run.updated_at
  WHERE run.user_id=p_user_id AND run.state='COMPLETED' AND run.brokerage_mode='KIS_MOCK'
    AND run.selected_side='BUY' AND run.physical_submit_count=1
    AND control.control_state='DISARMED'
    AND position.entry_order_id IS NOT NULL
    AND position.entry_filled_quantity=position.entry_ordered_quantity
    AND position.entry_average_fill_price_krw>0
    AND NOT EXISTS (SELECT 1 FROM public.automation_order_reservations reservation WHERE reservation.run_id=run.run_id)
    AND NOT EXISTS (SELECT 1 FROM public.automation_account_lineage lineage WHERE lineage.run_id=run.run_id AND lineage.reason='RECOVERY_BASELINE')
    AND EXISTS (
      SELECT 1 FROM public.audit_logs audit
      WHERE audit.user_id=run.user_id AND audit.action='MOCK_ORDER_SUBMITTED'
        AND audit.target_type='ORDER' AND audit.target_id=position.entry_order_id
        AND audit.payload_json->>'orderId'=position.entry_order_id
        AND audit.payload_json->>'brokerageMode'='KIS_MOCK'
        AND audit.payload_json->>'status'='SUBMITTED'
        AND audit.created_at BETWEEN run.started_at AND run.updated_at
    )
    AND EXISTS (
      SELECT 1 FROM public.event_outbox event
      JOIN public.async_event_registry registry
        ON registry.event_type=event.event_type
       AND registry.outbox_schema_version=event.schema_version AND registry.enabled
      WHERE event.aggregate_id=position.entry_order_id
        AND event.event_type='brokerage.mock-order-submitted.v1'
        AND event.payload_json->>'orderId'=position.entry_order_id
        AND event.payload_json->>'brokerageMode'='KIS_MOCK'
        AND event.payload_json->>'status'='SUBMITTED'
    )
  LIMIT 1;
END
$p1_read_automation_recovery_candidate_v1$;

CREATE FUNCTION public.p1_complete_automation_recovery_v1(
  p_user_id text,p_run_id text,p_position_id text,p_order_id text,
  p_filled_quantity bigint,p_average_fill_price_krw bigint,p_account_projection jsonb
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_complete_automation_recovery_v1$
DECLARE candidate record;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE full_projection jsonb;
DECLARE risk_projection jsonb;
DECLARE full_digest text;
DECLARE risk_digest text;
DECLARE lineage_digest text;
DECLARE prior_digest_value text;
DECLARE sequence_value integer;
DECLARE account_scope_hash_value text;
DECLARE observation_id_value text;
DECLARE recovery_timestamp timestamptz;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_position_id!~'^auto_pos_[A-Za-z0-9_-]{8,96}$'
     OR p_order_id!~'^ord_mock_[0-9a-f]{32}$'
     OR p_filled_quantity<=0 OR p_average_fill_price_krw<=0
     OR jsonb_typeof(p_account_projection)<>'object'
     OR jsonb_typeof(p_account_projection->'positions')<>'array'
     OR p_account_projection->>'positionsComplete'<>'true'
     OR p_account_projection-ARRAY[
       'accountId','cashKrw','marginRequirementKrw','portfolioEquityKrw',
       'positions','positionsComplete','schemaVersion'
     ]<>'{}'::jsonb THEN
    RAISE EXCEPTION 'automation recovery input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT * INTO candidate FROM public.p1_read_automation_recovery_candidate_v1(p_user_id);
  IF NOT FOUND OR candidate.run_id<>p_run_id OR candidate.position_id<>p_position_id
     OR candidate.order_id<>p_order_id OR candidate.filled_quantity<>p_filled_quantity
     OR candidate.average_fill_price_krw<>p_average_fill_price_krw THEN
    RAISE EXCEPTION 'automation recovery evidence changed' USING ERRCODE='40001';
  END IF;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND OR control_row.control_state<>'DISARMED'
     OR p_account_projection->>'accountId'<>control_row.account_id
     OR NOT EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_account_projection->'positions') item
       WHERE item->>'symbol'=candidate.symbol
         AND (item->>'quantity')::bigint=(SELECT quantity FROM public.automation_positions WHERE position_id=p_position_id)
     ) THEN
    RAISE EXCEPTION 'automation recovery balance mismatch' USING ERRCODE='40001';
  END IF;
  SELECT jsonb_build_object(
    'accountId',p_account_projection->>'accountId',
    'cashKrw',(p_account_projection->>'cashKrw')::bigint,
    'marginRequirementKrw',(p_account_projection->>'marginRequirementKrw')::bigint,
    'portfolioEquityKrw',(p_account_projection->>'portfolioEquityKrw')::bigint,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'marketValueKrw',(item->>'marketValueKrw')::bigint,
        'quantity',(item->>'quantity')::bigint,'symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_account_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO full_projection;
  SELECT jsonb_build_object(
    'accountId',p_account_projection->>'accountId','cashKrw',p_account_projection->>'cashKrw',
    'schemaVersion',2,'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'quantity',item->>'quantity','symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_account_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO risk_projection;
  IF NOT public.p1_automation_structural_projection_valid_v2(risk_projection) THEN
    RAISE EXCEPTION 'automation recovery risk projection invalid' USING ERRCODE='22023';
  END IF;
  SELECT min(account_scope_hash) INTO account_scope_hash_value
  FROM public.portfolio_balance_observations
  WHERE owner_user_id=p_user_id AND source='KIS_MOCK' AND context_status='ACTIVE'
    AND account_scope_hash LIKE substr(control_row.account_id,6)||'%';
  IF account_scope_hash_value IS NULL OR EXISTS (
    SELECT 1 FROM public.portfolio_balance_observations item
    WHERE item.owner_user_id=p_user_id AND item.source='KIS_MOCK' AND item.context_status='ACTIVE'
      AND item.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
      AND item.account_scope_hash<>account_scope_hash_value
  ) OR EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_account_projection->'positions') item
    WHERE NOT EXISTS (
      SELECT 1 FROM public.instrument_catalog_observations catalog
      WHERE catalog.symbol=item->>'symbol' AND catalog.completeness='COMPLETE'
    )
  ) THEN
    RAISE EXCEPTION 'automation recovery account evidence incomplete' USING ERRCODE='40001';
  END IF;
  SELECT jsonb_build_object(
    'accountId',p_account_projection->>'accountId',
    'cashKrw',(p_account_projection->>'cashKrw')::bigint,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'quantity',(item->>'quantity')::bigint,'symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_account_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO p_account_projection;
  full_digest:=encode(public.digest(convert_to(full_projection::text,'UTF8'),'sha256'),'hex');
  risk_digest:=encode(public.digest(convert_to(risk_projection::text,'UTF8'),'sha256'),'hex');
  lineage_digest:=encode(public.digest(convert_to(p_account_projection::text,'UTF8'),'sha256'),'hex');
  recovery_timestamp:=statement_timestamp();
  observation_id_value:='pbo_'||encode(public.digest(convert_to(
    p_user_id||':'||p_run_id||':'||p_order_id||':'||full_digest,'UTF8'),'sha256'),'hex');
  INSERT INTO public.portfolio_balance_observations(
    observation_id,owner_user_id,account_scope_hash,source,context_status,cash_krw,
    portfolio_equity_krw,margin_requirement_krw,completeness,position_count,observed_at,
    received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
  ) VALUES (
    observation_id_value,p_user_id,account_scope_hash_value,'KIS_MOCK','ACTIVE',
    (full_projection->>'cashKrw')::bigint,(full_projection->>'portfolioEquityKrw')::bigint,
    (full_projection->>'marginRequirementKrw')::bigint,'COMPLETE',
    jsonb_array_length(full_projection->'positions'),recovery_timestamp,recovery_timestamp,
    '2','kis-mock-online-complete-v2',full_projection,
    encode(public.digest(convert_to('automation-recovery:'||full_digest,'UTF8'),'sha256'),'hex'),
    full_digest
  );
  INSERT INTO public.portfolio_position_observations(
    balance_observation_id,symbol,quantity,market_value_krw,is_gold_etf_etn
  )
  SELECT observation_id_value,item->>'symbol',(item->>'quantity')::bigint,
    (item->>'marketValueKrw')::bigint,(
      SELECT catalog.is_gold_etf_etn
      FROM public.instrument_catalog_observations catalog
      WHERE catalog.symbol=item->>'symbol' AND catalog.completeness='COMPLETE'
      ORDER BY catalog.observed_at DESC,catalog.received_at DESC,catalog.observation_id DESC LIMIT 1
    )
  FROM jsonb_array_elements(full_projection->'positions') item;
  SELECT COALESCE(
    (SELECT next_digest FROM public.automation_account_lineage WHERE user_id=p_user_id ORDER BY sequence DESC LIMIT 1),
    control_row.initial_account_digest_v2,control_row.expected_account_digest_v2,
    control_row.baseline_account_digest
  ) INTO prior_digest_value;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value
  FROM public.automation_account_lineage WHERE user_id=p_user_id;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(
      p_user_id||':'||sequence_value::text||':RECOVERY_BASELINE','UTF8'),'sha256'),'hex'),1,32),
    p_user_id,p_run_id,sequence_value,'RECOVERY_BASELINE',prior_digest_value,lineage_digest,
    p_order_id,p_filled_quantity,p_average_fill_price_krw,statement_timestamp()
  );
  UPDATE public.automation_control SET
    baseline_account_digest=full_digest,
    expected_account_digest_v2=risk_digest,
    expected_account_projection_v2=risk_projection,
    updated_at=statement_timestamp()
  WHERE user_id=p_user_id AND control_state='DISARMED';
  RETURN sequence_value;
END
$p1_complete_automation_recovery_v1$;

CREATE OR REPLACE FUNCTION public.p1_automation_open_work_clear_v3(p_user_id text,p_account_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_open_work_clear_v3$
  SELECT NOT EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=p_account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
      AND NOT EXISTS (SELECT 1 FROM public.automation_order_reservations reservation WHERE reservation.order_id=item.order_id)
  ) AND NOT EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.user_id=p_user_id AND run.state='PENDING_RECONCILIATION'
  ) AND NOT EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.user_id=p_user_id AND run.physical_submit_count=1
      AND NOT EXISTS (SELECT 1 FROM public.automation_order_reservations reservation WHERE reservation.run_id=run.run_id)
      AND NOT EXISTS (SELECT 1 FROM public.automation_account_lineage lineage WHERE lineage.run_id=run.run_id AND lineage.reason='RECOVERY_BASELINE')
  )
$p1_automation_open_work_clear_v3$;

CREATE OR REPLACE FUNCTION public.p1_automation_status_facts_v2(p_user_id text,p_account_id text)
RETURNS TABLE(principle_configured boolean,unresolved_reconciliation boolean,active_principle_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_status_facts_v2$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1()
     OR (p_account_id IS NOT NULL AND p_account_id!~'^acct_[0-9a-f]{32}$') THEN
    RAISE EXCEPTION 'automation status facts scope denied' USING ERRCODE='42501';
  END IF;
  SELECT principle.principle_id INTO active_principle_id FROM public.principles principle
  WHERE principle.user_id=p_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id DESC LIMIT 1;
  principle_configured:=active_principle_id IS NOT NULL;
  unresolved_reconciliation:=p_account_id IS NOT NULL AND (
    EXISTS (
      SELECT 1 FROM public.orders item WHERE item.user_id=p_user_id AND item.account_id=p_account_id
        AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
          OR item.reconciliation_status='MISMATCH')
    )
    OR EXISTS (
      SELECT 1 FROM public.automation_runs run
      WHERE run.user_id=p_user_id AND run.physical_submit_count=1
        AND NOT EXISTS (SELECT 1 FROM public.automation_order_reservations reservation WHERE reservation.run_id=run.run_id)
        AND NOT EXISTS (SELECT 1 FROM public.automation_account_lineage lineage WHERE lineage.run_id=run.run_id AND lineage.reason='RECOVERY_BASELINE')
    )
  );
  RETURN NEXT;
END
$p1_automation_status_facts_v2$;

ALTER FUNCTION public.p1_read_automation_recovery_candidate_v1(text) OWNER TO flyway;
ALTER FUNCTION public.p1_complete_automation_recovery_v1(text,text,text,text,bigint,bigint,jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_recovery_candidate_v1(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p1_complete_automation_recovery_v1(text,text,text,text,bigint,bigint,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_recovery_candidate_v1(text) TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_complete_automation_recovery_v1(text,text,text,text,bigint,bigint,jsonb) TO decision_automation_runtime;
