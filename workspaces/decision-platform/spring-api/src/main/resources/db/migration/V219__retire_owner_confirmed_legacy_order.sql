-- Retire the one owner-confirmed legacy FULL ledger row without claiming that KIS cancelled it.
-- Keep the original order/events and the V218 quarantine evidence for audit; only close the
-- unresolved local projection so it no longer blocks this owner's account-history gate.
SET LOCAL row_security = on;
ALTER TABLE public.orders NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_schedule NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_account_aliases_v218 NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.order_events NO FORCE ROW LEVEL SECURITY;

CREATE TABLE public.full_owner_order_resolution_events_v219 (
  order_id text PRIMARY KEY REFERENCES public.orders(order_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  prior_status text NOT NULL CHECK (prior_status='SUBMITTED'),
  resolved_status text NOT NULL CHECK (resolved_status='CANCELLED'),
  quantity bigint NOT NULL CHECK (quantity=1),
  filled_quantity bigint NOT NULL CHECK (filled_quantity=0),
  prior_leaves_quantity bigint NOT NULL CHECK (prior_leaves_quantity=1),
  unfilled_terminated_quantity bigint NOT NULL CHECK (unfilled_terminated_quantity=1),
  broker_cancel_confirmed boolean NOT NULL DEFAULT false CHECK (NOT broker_cancel_confirmed),
  resolution_code text NOT NULL CHECK (
    resolution_code='OWNER_CONFIRMED_LOCAL_UNRECONCILED_ORDER_RETIREMENT'
  ),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.full_owner_order_resolution_events_v219 OWNER TO flyway;
REVOKE ALL ON public.full_owner_order_resolution_events_v219
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT SELECT,INSERT ON public.full_owner_order_resolution_events_v219 TO flyway;
CREATE TRIGGER full_owner_order_resolution_events_append_only_v219
  BEFORE UPDATE OR DELETE ON public.full_owner_order_resolution_events_v219
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();
COMMENT ON TABLE public.full_owner_order_resolution_events_v219 IS
  'Owner-confirmed local retirement of a legacy order row; broker-side cancellation is not asserted.';

-- The generic order reader normally prefers the latest provider lifecycle event over its row
-- projection. For this explicit local retirement, return the closed row state instead of the old
-- CANCEL_REQUESTED event so API clients do not keep showing it as pending.
CREATE OR REPLACE FUNCTION public.read_mock_order_owner_projection(
  requested_actor_user_id text,
  requested_order_id text,
  requested_capability_token text
)
RETURNS TABLE (
  order_id text,
  account_id text,
  brokerage_mode text,
  status text,
  submitted_at timestamptz,
  decision_id text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $read_mock_order_owner_projection_v219$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  RETURN QUERY
  SELECT
    stored.order_id,
    stored.account_id,
    stored.brokerage_mode,
    CASE WHEN EXISTS (
      SELECT 1 FROM public.full_owner_order_resolution_events_v219 resolution
      WHERE resolution.order_id=stored.order_id
        AND resolution.owner_user_id=stored.user_id
        AND resolution.broker_cancel_confirmed=false
    ) AND stored.status='CANCELLED' THEN 'LOCAL_RETIRED'
      ELSE COALESCE(latest_event.event_status,stored.status) END,
    stored.submitted_at,
    stored.decision_id
  FROM public.orders stored
  LEFT JOIN LATERAL (
    SELECT event.event_status
    FROM public.order_events event
    WHERE event.order_id=stored.order_id AND event.event_status IS NOT NULL
    ORDER BY event.event_seq DESC
    LIMIT 1
  ) latest_event ON true
  WHERE stored.user_id=requested_actor_user_id
    AND stored.order_id=requested_order_id
    AND EXISTS (
      SELECT 1 FROM public.users actor
      WHERE actor.user_id=requested_actor_user_id AND actor.status='ACTIVE'
    )
  LIMIT 1;
END
$read_mock_order_owner_projection_v219$;
ALTER FUNCTION public.read_mock_order_owner_projection(text,text,text) OWNER TO flyway;

-- Local retirement has no KIS reference/evidence. Do not pass its stale cancel-request event and
-- terminal quantities into the fill reconciler; report a stable, non-retryable conflict instead.
CREATE OR REPLACE FUNCTION public.read_order_reconciliation_state_authorized_v2(
  p_capability text,p_payload_text text
)
RETURNS TABLE(operation_outcome text,state_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $read_order_reconciliation_state_authorized_v2_local_retirement$
DECLARE payload jsonb:=p_payload_text::jsonb;
DECLARE scope_token text;
DECLARE read_result record;
BEGIN
  scope_token:=public.open_brokerage_internal_scope_v1(
    p_capability,payload->>'actorUserId','READ_ORDER_FILL_STATE','ORDER',payload->>'orderId',
    public.actor_capability_payload_hash(p_payload_text)
  );
  SELECT * INTO read_result
  FROM public.read_order_reconciliation_state(payload,scope_token);
  IF NOT FOUND THEN RETURN; END IF;
  IF read_result.operation_outcome='READY'
     AND EXISTS (
       SELECT 1 FROM public.full_owner_order_resolution_events_v219 resolution
       WHERE resolution.order_id=payload->>'orderId'
         AND resolution.broker_cancel_confirmed=false
     ) THEN
    RETURN QUERY SELECT 'LOCAL_RETIREMENT_NOT_RECONCILABLE'::text,NULL::text;
    RETURN;
  END IF;
  RETURN QUERY SELECT read_result.operation_outcome,read_result.state_json;
END
$read_order_reconciliation_state_authorized_v2_local_retirement$;
ALTER FUNCTION public.read_order_reconciliation_state_authorized_v2(text,text) OWNER TO flyway;

-- Keep the final DB write closed too, even if a future caller skips the read-state guard.
CREATE OR REPLACE FUNCTION public.apply_stored_order_fills_authorized_v2(
  p_capability text,p_payload_text text
)
RETURNS TABLE(
  operation_outcome text,order_id text,brokerage_mode text,status text,
  filled_quantity bigint,leaves_quantity bigint,unfilled_terminated_quantity bigint,
  average_fill_price_krw bigint,reconciliation_status text,reconciled_at timestamptz,
  applied_event_count integer,has_more boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $apply_stored_order_fills_authorized_v2_local_retirement$
DECLARE payload jsonb:=p_payload_text::jsonb;
DECLARE scope_token text;
BEGIN
  scope_token:=public.open_brokerage_internal_scope_v1(
    p_capability,payload->>'actorUserId','APPLY_ORDER_FILLS','ORDER',payload->>'orderId',
    public.actor_capability_payload_hash(p_payload_text)
  );
  IF EXISTS (
    SELECT 1 FROM public.full_owner_order_resolution_events_v219 resolution
    WHERE resolution.order_id=payload->>'orderId'
      AND resolution.broker_cancel_confirmed=false
  ) THEN
    -- The wrapper opened this one-use scope before the local-retirement guard. Consume it even
    -- though the guarded apply function is intentionally not called.
    PERFORM public.assert_brokerage_database_capability(scope_token);
    RETURN QUERY SELECT
      'LOCAL_RETIREMENT_NOT_RECONCILABLE'::text,NULL::text,NULL::text,NULL::text,
      NULL::bigint,NULL::bigint,NULL::bigint,NULL::bigint,NULL::text,NULL::timestamptz,
      NULL::integer,NULL::boolean;
    RETURN;
  END IF;
  RETURN QUERY SELECT * FROM public.apply_stored_order_fills(payload,scope_token);
END
$apply_stored_order_fills_authorized_v2_local_retirement$;
ALTER FUNCTION public.apply_stored_order_fills_authorized_v2(text,text) OWNER TO flyway;

DO $v219_retire_demo_user_legacy_order$
DECLARE
  order_row public.orders%ROWTYPE;
  demo_owner text;
  bound_account text;
  control_state_value text;
  -- SHA-256 of the opaque local order ID; keep that raw account-history identifier out of source.
  expected_order_id_sha256 constant text := '59ac1cd9ca4c736d29c86493325a508db084e17501e22580a11152f61af40cea';
  evidence text;
BEGIN
  SELECT user_id INTO demo_owner
  FROM public.users
  WHERE username='demo-user' AND status='ACTIVE';
  IF demo_owner IS NULL THEN RETURN; END IF;

  SELECT credential.account_id INTO bound_account
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=demo_owner
    AND credential.brokerage_mode='KIS_MOCK'
    AND credential.credential_state IN ('CONNECTED','CERTIFIED');
  SELECT control.control_state INTO control_state_value
  FROM public.automation_control control WHERE control.user_id=demo_owner;

  SELECT * INTO order_row
  FROM public.orders
  WHERE user_id=demo_owner
    AND public.digest(convert_to(order_id,'UTF8'),'sha256')=decode(expected_order_id_sha256,'hex')
  FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;

  IF bound_account IS NULL
     OR control_state_value IS DISTINCT FROM 'DISARMED'
     OR order_row.user_id IS DISTINCT FROM demo_owner
     OR order_row.brokerage_mode IS DISTINCT FROM 'KIS_MOCK'
     OR order_row.account_id IS NOT DISTINCT FROM bound_account
     OR order_row.symbol IS DISTINCT FROM '005930'
     OR order_row.side IS DISTINCT FROM 'BUY'
     OR order_row.quantity IS DISTINCT FROM 1
     OR order_row.status IS DISTINCT FROM 'SUBMITTED'
     OR order_row.filled_quantity IS DISTINCT FROM 0
     OR order_row.leaves_quantity IS DISTINCT FROM 1
     OR order_row.unfilled_terminated_quantity IS DISTINCT FROM 0
     OR order_row.reconciliation_status IS DISTINCT FROM 'NOT_APPLICABLE'
     OR order_row.provider_order_ref_hash IS NOT NULL
     OR order_row.provider_tr_id IS NOT NULL
     OR order_row.provider_received_at IS NOT NULL
     OR NOT EXISTS (
       SELECT 1 FROM public.automation_order_integrity_events_v218 integrity
       WHERE integrity.order_id=order_row.order_id
         AND integrity.owner_user_id=demo_owner
         AND integrity.source_account_id=order_row.account_id
         AND integrity.reason_code='LEGACY_UNLINKED_ORDER_NO_RECONCILIATION'
         AND integrity.disposition_code='USER_REQUESTED_QUARANTINE_BLOCK_START'
     )
     OR EXISTS (
       SELECT 1 FROM public.automation_order_reservations reservation
       WHERE reservation.order_id=order_row.order_id
     )
     OR EXISTS (
       SELECT 1 FROM public.automation_runs run
       WHERE run.user_id=demo_owner AND run.account_id=order_row.account_id
     )
     OR EXISTS (
       SELECT 1 FROM public.automation_runtime_schedule schedule
       WHERE schedule.user_id=demo_owner AND schedule.schedule_state IN ('ARMED','CLAIMED')
     )
     OR EXISTS (
       SELECT 1 FROM public.portfolio_balance_observations balance
       WHERE balance.owner_user_id=demo_owner AND balance.source='KIS_MOCK'
         AND balance.account_scope_hash=substr(order_row.account_id,6)||repeat('0',32)
     )
     OR EXISTS (
       SELECT 1 FROM public.full_owner_account_aliases_v218 alias
       WHERE alias.owner_user_id=demo_owner AND alias.source_account_id=order_row.account_id
         AND alias.canonical_account_id=bound_account
     )
     OR EXISTS (
       SELECT 1 FROM public.order_events event
       WHERE event.order_id=order_row.order_id
         AND event.event_type NOT IN ('MOCK_ORDER_SUBMITTED','MOCK_ORDER_CANCEL_REQUESTED')
     ) THEN
    RAISE EXCEPTION 'V219 legacy order no longer matches owner-confirmed retirement preconditions';
  END IF;

  evidence:=encode(public.digest(convert_to(
    'owner-confirmed-local-order-retirement-v219:'||order_row.order_id||':'||demo_owner||':'||
    order_row.account_id||':SUBMITTED:005930:BUY:1:0:1:broker-cancel-confirmed=false',
    'UTF8'),'sha256'),'hex');

  UPDATE public.orders
  SET status='CANCELLED',
      leaves_quantity=0,
      unfilled_terminated_quantity=1,
      updated_at=statement_timestamp()
  WHERE order_id=order_row.order_id;

  INSERT INTO public.full_owner_order_resolution_events_v219(
    order_id,owner_user_id,source_account_id,prior_status,resolved_status,quantity,
    filled_quantity,prior_leaves_quantity,unfilled_terminated_quantity,
    broker_cancel_confirmed,resolution_code,evidence_sha256
  ) VALUES (
    order_row.order_id,demo_owner,order_row.account_id,'SUBMITTED','CANCELLED',1,0,1,1,false,
    'OWNER_CONFIRMED_LOCAL_UNRECONCILED_ORDER_RETIREMENT',evidence
  );
END
$v219_retire_demo_user_legacy_order$;

ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_schedule FORCE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_account_aliases_v218 FORCE ROW LEVEL SECURITY;
ALTER TABLE public.order_events FORCE ROW LEVEL SECURITY;
