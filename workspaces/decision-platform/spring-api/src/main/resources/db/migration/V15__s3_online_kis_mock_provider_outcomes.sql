-- S3-online은 기존 V11~V14를 바꾸지 않고 mock provider 결과 반영 함수만 추가한다.
DO $v15_precondition$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'orders'
      AND column_name = 'provider_order_ref_hash'
  ) THEN
    RAISE EXCEPTION 'S3-online V15 precondition failed: provider outcome columns already exist';
  END IF;
END
$v15_precondition$;

ALTER TABLE orders
  DROP CONSTRAINT orders_status_check;
ALTER TABLE order_events
  DROP CONSTRAINT order_events_type_check,
  DROP CONSTRAINT order_events_type_status_pair_check;

ALTER TABLE orders
  ADD COLUMN provider_order_ref_hash text,
  ADD COLUMN provider_tr_id text,
  ADD COLUMN provider_received_at timestamptz,
  ADD CONSTRAINT orders_status_check CHECK (
    status IN (
      'SUBMITTED',
      'PENDING_RECONCILIATION',
      'ACCEPTED',
      'PARTIALLY_FILLED',
      'FILLED',
      'CANCEL_REQUESTED',
      'CANCELLED',
      'REJECTED'
    )
  ),
  ADD CONSTRAINT orders_provider_reference_check CHECK (
    (
      provider_order_ref_hash IS NULL
      AND provider_tr_id IS NULL
      AND provider_received_at IS NULL
    )
    OR (
      brokerage_mode = 'KIS_MOCK'
      AND provider_order_ref_hash ~ '^[0-9a-f]{64}$'
      AND provider_tr_id IN ('VTTC0011U', 'VTTC0012U')
      AND provider_received_at IS NOT NULL
    )
  );
CREATE INDEX orders_mock_pending_reconciliation_idx
  ON orders (updated_at, order_id)
  WHERE brokerage_mode = 'KIS_MOCK'
    AND status = 'PENDING_RECONCILIATION';

ALTER TABLE order_events
  ADD CONSTRAINT order_events_type_check CHECK (
    event_type IN (
      'MOCK_ORDER_SUBMITTED',
      'MOCK_ORDER_PROVIDER_PENDING',
      'MOCK_ORDER_ACCEPTED',
      'MOCK_ORDER_REJECTED',
      'MOCK_ORDER_PARTIALLY_FILLED',
      'MOCK_ORDER_FILLED',
      'MOCK_ORDER_CANCEL_REQUESTED',
      'MOCK_ORDER_CANCELLED',
      'PAPER_ORDER_ACCEPTED',
      'PAPER_ORDER_FILLED',
      'PAPER_ORDER_CANCEL_REQUESTED',
      'PAPER_ORDER_CANCELLED',
      'INVALID_TRANSITION'
    )
  ),
  ADD CONSTRAINT order_events_type_status_pair_check CHECK (
    (event_type = 'MOCK_ORDER_SUBMITTED' AND event_status = 'SUBMITTED')
    OR (
      event_type = 'MOCK_ORDER_PROVIDER_PENDING'
      AND event_status = 'PENDING_RECONCILIATION'
    )
    OR (event_type = 'MOCK_ORDER_ACCEPTED' AND event_status = 'ACCEPTED')
    OR (event_type = 'MOCK_ORDER_REJECTED' AND event_status = 'REJECTED')
    OR (event_type = 'MOCK_ORDER_PARTIALLY_FILLED' AND event_status = 'PARTIALLY_FILLED')
    OR (event_type = 'MOCK_ORDER_FILLED' AND event_status = 'FILLED')
    OR (event_type = 'MOCK_ORDER_CANCEL_REQUESTED' AND event_status = 'CANCEL_REQUESTED')
    OR (event_type = 'MOCK_ORDER_CANCELLED' AND event_status = 'CANCELLED')
    OR (event_type = 'PAPER_ORDER_ACCEPTED' AND event_status = 'ACCEPTED')
    OR (event_type = 'PAPER_ORDER_FILLED' AND event_status = 'FILLED')
    OR (event_type = 'PAPER_ORDER_CANCEL_REQUESTED' AND event_status = 'CANCEL_REQUESTED')
    OR (event_type = 'PAPER_ORDER_CANCELLED' AND event_status = 'CANCELLED')
    OR (event_type = 'INVALID_TRANSITION' AND event_status IS NULL)
  );

CREATE FUNCTION record_mock_order_provider_outcome(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  order_id text,
  account_id text,
  brokerage_mode text,
  status text,
  submitted_at timestamptz,
  decision_id text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $record_mock_order_provider_outcome$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_order_id text;
  requested_status text;
  requested_provider_ref_hash text;
  requested_tr_id text;
  requested_received_at timestamptz;
  requested_order_event_id text;
  stored_actor record;
  stored_order record;
  current_status text;
  expected_tr_id text;
  next_event_seq integer;
  next_event_type text;
  event_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'orderId', 'status', 'providerOrderRefHash', 'trId',
       'receivedAt', 'orderEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'orderId', 'status', 'providerOrderRefHash', 'trId',
       'receivedAt', 'orderEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  IF jsonb_typeof(requested_payload -> 'actorUserId') <> 'string'
     OR jsonb_typeof(requested_payload -> 'actorRole') <> 'string'
     OR jsonb_typeof(requested_payload -> 'securityVersion') <> 'number'
     OR (requested_payload ->> 'securityVersion') !~ '^[0-9]{1,18}$'
     OR jsonb_typeof(requested_payload -> 'requestId') <> 'string'
     OR jsonb_typeof(requested_payload -> 'orderId') <> 'string'
     OR jsonb_typeof(requested_payload -> 'status') <> 'string'
     OR jsonb_typeof(requested_payload -> 'providerOrderRefHash')
       NOT IN ('string', 'null')
     OR jsonb_typeof(requested_payload -> 'trId') NOT IN ('string', 'null')
     OR jsonb_typeof(requested_payload -> 'receivedAt') <> 'string'
     OR jsonb_typeof(requested_payload -> 'orderEventId') <> 'string' THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  BEGIN
    requested_actor_user_id := requested_payload ->> 'actorUserId';
    requested_actor_role := requested_payload ->> 'actorRole';
    requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
    requested_request_id := requested_payload ->> 'requestId';
    requested_order_id := requested_payload ->> 'orderId';
    requested_status := requested_payload ->> 'status';
    requested_provider_ref_hash := requested_payload ->> 'providerOrderRefHash';
    requested_tr_id := requested_payload ->> 'trId';
    requested_received_at := (requested_payload ->> 'receivedAt')::timestamptz;
    requested_order_event_id := requested_payload ->> 'orderEventId';
  EXCEPTION
    WHEN invalid_text_representation
      OR invalid_datetime_format
      OR datetime_field_overflow
      OR numeric_value_out_of_range THEN
      RETURN QUERY
      SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
             NULL::text, NULL::timestamptz, NULL::text;
      RETURN;
  END;

  IF requested_actor_user_id !~ '^[A-Za-z0-9._:-]{1,128}$'
     OR requested_actor_role NOT IN ('USER', 'ADMIN')
     OR requested_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'
     OR requested_order_id !~ '^ord_mock_[0-9a-f]{32}$'
     OR requested_order_event_id !~ '^oev_[0-9a-f]{32}$'
     OR requested_status NOT IN (
       'ACCEPTED', 'REJECTED', 'PENDING_RECONCILIATION', 'CANCELLED'
     ) THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> requested_actor_role
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY
    SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended('mock-order:provider-outcome:' || requested_order_id, 3301)
  );
  SELECT
    stored.order_id,
    stored.account_id,
    stored.brokerage_mode,
    stored.status,
    stored.submitted_at,
    stored.decision_id,
    stored.side
  INTO stored_order
  FROM public.orders stored
  WHERE stored.order_id = requested_order_id
    AND stored.user_id = requested_actor_user_id
    AND stored.brokerage_mode = 'KIS_MOCK'
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY
    SELECT 'ORDER_NOT_FOUND'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  SELECT event.event_status
  INTO current_status
  FROM public.order_events event
  WHERE event.order_id = requested_order_id
    AND event.event_status IS NOT NULL
  ORDER BY event.event_seq DESC
  LIMIT 1;
  current_status := COALESCE(current_status, stored_order.status);
  IF NOT (
    (current_status = 'SUBMITTED' AND requested_status IN (
      'ACCEPTED', 'REJECTED', 'PENDING_RECONCILIATION'
    ))
    OR (current_status = 'CANCEL_REQUESTED' AND requested_status = 'CANCELLED')
  ) THEN
    RETURN QUERY
    SELECT 'ORDER_CONFLICT'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  expected_tr_id :=
    CASE stored_order.side
      WHEN 'BUY' THEN 'VTTC0012U'
      WHEN 'SELL' THEN 'VTTC0011U'
      ELSE NULL
    END;
  IF requested_status = 'ACCEPTED' THEN
    IF requested_provider_ref_hash IS NULL
       OR requested_provider_ref_hash !~ '^[0-9a-f]{64}$'
       OR requested_tr_id IS DISTINCT FROM expected_tr_id THEN
      RETURN QUERY
      SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
             NULL::text, NULL::timestamptz, NULL::text;
      RETURN;
    END IF;
  ELSIF requested_provider_ref_hash IS NOT NULL OR requested_tr_id IS NOT NULL THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;
  IF requested_received_at < stored_order.submitted_at
     OR requested_received_at > pg_catalog.clock_timestamp() + interval '5 minutes' THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  next_event_type :=
    CASE requested_status
      WHEN 'ACCEPTED' THEN 'MOCK_ORDER_ACCEPTED'
      WHEN 'REJECTED' THEN 'MOCK_ORDER_REJECTED'
      WHEN 'PENDING_RECONCILIATION' THEN 'MOCK_ORDER_PROVIDER_PENDING'
      WHEN 'CANCELLED' THEN 'MOCK_ORDER_CANCELLED'
    END;
  event_payload :=
    CASE requested_status
      WHEN 'ACCEPTED' THEN
        jsonb_build_object(
          'orderId', requested_order_id,
          'brokerageMode', 'KIS_MOCK',
          'status', requested_status,
          'providerOrderRefHash', requested_provider_ref_hash,
          'trId', requested_tr_id,
          'receivedAt', requested_received_at::text
        )
      ELSE
        jsonb_build_object(
          'orderId', requested_order_id,
          'brokerageMode', 'KIS_MOCK',
          'status', requested_status,
          'receivedAt', requested_received_at::text
        )
    END;

  SELECT COALESCE(max(event.event_seq), 0) + 1
  INTO next_event_seq
  FROM public.order_events event
  WHERE event.order_id = requested_order_id;
  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id, requested_order_id, next_event_type,
    requested_status, event_payload, requested_received_at, next_event_seq
  );

  UPDATE public.orders stored
  SET status = requested_status,
      provider_order_ref_hash =
        CASE
          WHEN requested_status = 'ACCEPTED' THEN requested_provider_ref_hash
          ELSE stored.provider_order_ref_hash
        END,
      provider_tr_id =
        CASE
          WHEN requested_status = 'ACCEPTED' THEN requested_tr_id
          ELSE stored.provider_tr_id
        END,
      provider_received_at =
        CASE
          WHEN requested_status = 'ACCEPTED' THEN requested_received_at
          ELSE stored.provider_received_at
        END,
      result_canonical_json =
        jsonb_set(
          stored.result_canonical_json::jsonb,
          '{status}',
          to_jsonb(requested_status)
        )::text,
      updated_at = GREATEST(stored.updated_at, requested_received_at)
  WHERE stored.order_id = requested_order_id;

  RETURN QUERY
  SELECT
    'APPLIED'::text,
    stored_order.order_id,
    stored_order.account_id,
    stored_order.brokerage_mode,
    requested_status,
    stored_order.submitted_at,
    stored_order.decision_id;
END
$record_mock_order_provider_outcome$;
ALTER FUNCTION record_mock_order_provider_outcome(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION record_mock_order_provider_outcome(jsonb, text) FROM PUBLIC;

DO $v15_grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION record_mock_order_provider_outcome(jsonb, text)
      TO decision_app;
  END IF;
END
$v15_grants$;
