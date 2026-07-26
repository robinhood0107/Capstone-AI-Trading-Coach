-- S3.1 post-merge 보안 finding을 additive migration으로 닫고 V10/V11 이력을 변경하지 않는다.
DO $v12_precondition$
BEGIN
  IF to_regclass('public.brokerage_db_capability_keys') IS NOT NULL THEN
    RAISE EXCEPTION 'S3.1 V12 precondition failed: brokerage capability table already exists';
  END IF;
  IF '${brokerageDbCapabilityTokenSha256}' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S3.1 V12 precondition failed: capability token digest is invalid';
  END IF;
END
$v12_precondition$;

CREATE TABLE brokerage_db_capability_keys (
  capability_id text PRIMARY KEY CHECK (capability_id = 'S3_1_RUNTIME'),
  token_sha256 text NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

INSERT INTO brokerage_db_capability_keys (capability_id, token_sha256)
VALUES ('S3_1_RUNTIME', '${brokerageDbCapabilityTokenSha256}');

-- event sequence를 과거 row에도 결정적으로 채운 뒤 lifecycle projection의 유일한 순서 기준으로 승격한다.
ALTER TABLE order_events
  ADD COLUMN event_seq integer;
WITH ranked AS (
  SELECT
    event.ctid,
    row_number() OVER (
      PARTITION BY event.order_id
      ORDER BY event.created_at, event.order_event_id
    )::integer AS event_seq
  FROM order_events event
)
UPDATE order_events event
SET event_seq = ranked.event_seq
FROM ranked
WHERE event.ctid = ranked.ctid;
ALTER TABLE order_events
  ALTER COLUMN event_seq SET NOT NULL,
  ADD CONSTRAINT order_events_sequence_check CHECK (event_seq > 0),
  ADD CONSTRAINT order_events_type_status_pair_check CHECK (
    (event_type = 'MOCK_ORDER_SUBMITTED' AND event_status = 'SUBMITTED')
    OR (event_type = 'MOCK_ORDER_ACCEPTED' AND event_status = 'ACCEPTED')
    OR (event_type = 'MOCK_ORDER_REJECTED' AND event_status = 'REJECTED')
    OR (event_type = 'MOCK_ORDER_PARTIALLY_FILLED' AND event_status = 'PARTIALLY_FILLED')
    OR (event_type = 'MOCK_ORDER_FILLED' AND event_status = 'FILLED')
    OR (event_type = 'MOCK_ORDER_CANCEL_REQUESTED' AND event_status = 'CANCEL_REQUESTED')
    OR (event_type = 'MOCK_ORDER_CANCELLED' AND event_status = 'CANCELLED')
    OR (event_type = 'INVALID_TRANSITION' AND event_status IS NULL)
  );
CREATE UNIQUE INDEX order_events_order_sequence_unique
  ON order_events (order_id, event_seq);

-- V11의 caller-writable GUC owner boundary와 direct table DML을 제거한다.
DROP VIEW mock_order_owner_projection;
DROP FUNCTION read_mock_order_decision();
DROP FUNCTION find_mock_order_idempotency_result(text, text, timestamptz);
DROP FUNCTION read_mock_order_owner_projection();

DROP POLICY orders_owner_select_policy ON orders;
DROP POLICY orders_owner_insert_policy ON orders;
DROP POLICY order_events_owner_select_policy ON order_events;
DROP POLICY order_events_owner_insert_policy ON order_events;
CREATE POLICY orders_definer_insert_policy
  ON orders
  FOR INSERT
  TO flyway
  WITH CHECK (true);
CREATE POLICY orders_definer_update_lock_policy
  ON orders
  FOR UPDATE
  TO flyway
  USING (true)
  WITH CHECK (true);
CREATE POLICY order_events_definer_insert_policy
  ON order_events
  FOR INSERT
  TO flyway
  WITH CHECK (true);

-- 기존 이름은 무권한 compatibility tombstone으로만 남겨 실수로 재부여해도 row를 노출하지 않는다.
CREATE VIEW mock_order_owner_projection
WITH (security_barrier = true, security_invoker = true)
AS
SELECT
  orders.order_id,
  orders.account_id,
  orders.brokerage_mode,
  orders.status,
  orders.submitted_at,
  orders.decision_id
FROM orders
WHERE false;

ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_brokerage_order_contract_check
  CHECK (
    target_type <> 'ORDER'
    OR (
      action = 'MOCK_ORDER_SUBMITTED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ?& ARRAY[
        'orderId',
        'decisionId',
        'evaluationId',
        'brokerageMode',
        'status',
        'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'decisionId',
        'evaluationId',
        'brokerageMode',
        'status',
        'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      action = 'MOCK_ORDER_CANCEL_REQUESTED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ?& ARRAY[
        'orderId',
        'decisionId',
        'brokerageMode',
        'status',
        'requestedByRole',
        'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'decisionId',
        'brokerageMode',
        'status',
        'requestedByRole',
        'requestedAt'
      ] = '{}'::jsonb
    )
  );

ALTER TABLE event_outbox
  ADD CONSTRAINT event_outbox_brokerage_order_contract_check
  CHECK (
    event_type NOT IN (
      'brokerage.mock-order-submitted.v1',
      'brokerage.mock-order-cancel-requested.v1'
    )
    OR (
      event_type = 'brokerage.mock-order-submitted.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ?& ARRAY[
        'orderId',
        'decisionId',
        'evaluationId',
        'brokerageMode',
        'status',
        'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'decisionId',
        'evaluationId',
        'brokerageMode',
        'status',
        'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      event_type = 'brokerage.mock-order-cancel-requested.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ?& ARRAY[
        'orderId',
        'decisionId',
        'brokerageMode',
        'status',
        'requestedByRole',
        'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'decisionId',
        'brokerageMode',
        'status',
        'requestedByRole',
        'requestedAt'
      ] = '{}'::jsonb
    )
  );

CREATE FUNCTION assert_brokerage_database_capability(requested_token text)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $assert_brokerage_database_capability$
BEGIN
  IF requested_token IS NULL
     OR octet_length(requested_token) < 32
     OR NOT EXISTS (
       SELECT 1
       FROM public.brokerage_db_capability_keys capability
       WHERE capability.capability_id = 'S3_1_RUNTIME'
         AND capability.token_sha256 =
           encode(sha256(convert_to(requested_token, 'UTF8')), 'hex')
     ) THEN
    RAISE EXCEPTION 'S3.1 brokerage database capability denied'
      USING ERRCODE = '42501';
  END IF;
END
$assert_brokerage_database_capability$;
ALTER FUNCTION assert_brokerage_database_capability(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION assert_brokerage_database_capability(text) FROM PUBLIC;

CREATE FUNCTION read_mock_order_decision(
  requested_actor_user_id text,
  requested_decision_id text,
  requested_capability_token text
)
RETURNS TABLE (
  decision_id text,
  evaluation_id text,
  portfolio_source text,
  outcome text,
  mode text,
  can_submit_order boolean,
  enforcement_action text,
  valid_until timestamptz,
  snapshot_artifact_canonical_json text,
  portfolio_owner_scope_hash text,
  invalidated boolean,
  invalidation_reason_class text,
  consumed_by_order_id text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_mock_order_decision$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  -- decisions는 FORCE RLS이므로 capability 검증 뒤 함수 인자로 받은 owner를 transaction-local로 고정한다.
  PERFORM set_config('app.actor_user_id', requested_actor_user_id, true);
  RETURN QUERY
  SELECT
    decision.decision_id,
    decision.evaluation_id,
    decision.portfolio_source,
    decision.outcome,
    decision.mode,
    decision.can_submit_order,
    decision.enforcement_action,
    decision.valid_until,
    artifact.snapshot_artifact_canonical_json,
    artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}',
    invalidation.invalidation_id IS NOT NULL,
    invalidation.reason_class,
    consumed.order_id
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
   AND artifact.evaluation_id = decision.evaluation_id
  LEFT JOIN LATERAL (
    SELECT entry.invalidation_id, entry.reason_class
    FROM public.decision_invalidations entry
    WHERE entry.decision_id = decision.decision_id
    ORDER BY entry.invalidated_at DESC, entry.invalidation_id DESC
    LIMIT 1
  ) invalidation ON true
  LEFT JOIN public.orders consumed
    ON consumed.decision_id = decision.decision_id
  WHERE decision.user_id = requested_actor_user_id
    AND decision.decision_id = requested_decision_id
    AND EXISTS (
      SELECT 1
      FROM public.users actor
      WHERE actor.user_id = requested_actor_user_id
        AND actor.status = 'ACTIVE'
    )
  LIMIT 1;
END
$read_mock_order_decision$;
ALTER FUNCTION read_mock_order_decision(text, text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_mock_order_decision(text, text, text) FROM PUBLIC;

CREATE FUNCTION find_mock_order_idempotency_result(
  requested_scope_hash text,
  requested_owner_scope_hash text,
  requested_now timestamptz,
  requested_capability_token text
)
RETURNS TABLE (
  request_hash text,
  result_canonical_json text,
  expires_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $find_mock_order_idempotency_result$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  RETURN QUERY
  SELECT
    stored.request_hash,
    stored.result_canonical_json,
    stored.created_at + interval '24 hours'
  FROM public.orders stored
  WHERE stored.idempotency_scope_hash = requested_scope_hash
    AND stored.idempotency_owner_scope_hash = requested_owner_scope_hash
    AND stored.created_at + interval '24 hours' > requested_now
  LIMIT 1;
END
$find_mock_order_idempotency_result$;
ALTER FUNCTION find_mock_order_idempotency_result(text, text, timestamptz, text)
  OWNER TO flyway;
REVOKE ALL ON FUNCTION find_mock_order_idempotency_result(text, text, timestamptz, text)
  FROM PUBLIC;

CREATE FUNCTION read_mock_order_owner_projection(
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
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_mock_order_owner_projection$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  RETURN QUERY
  SELECT
    stored.order_id,
    stored.account_id,
    stored.brokerage_mode,
    COALESCE(latest_event.event_status, stored.status),
    stored.submitted_at,
    stored.decision_id
  FROM public.orders stored
  LEFT JOIN LATERAL (
    SELECT event.event_status
    FROM public.order_events event
    WHERE event.order_id = stored.order_id
      AND event.event_status IS NOT NULL
    ORDER BY event.event_seq DESC
    LIMIT 1
  ) latest_event ON true
  WHERE stored.user_id = requested_actor_user_id
    AND stored.order_id = requested_order_id
    AND EXISTS (
      SELECT 1
      FROM public.users actor
      WHERE actor.user_id = requested_actor_user_id
        AND actor.status = 'ACTIVE'
    )
  LIMIT 1;
END
$read_mock_order_owner_projection$;
ALTER FUNCTION read_mock_order_owner_projection(text, text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_mock_order_owner_projection(text, text, text) FROM PUBLIC;

CREATE FUNCTION create_mock_order(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  projection_canonical_json text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $create_mock_order$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_decision_id text;
  requested_order_id text;
  requested_scope_hash text;
  requested_owner_scope_hash text;
  requested_request_hash text;
  requested_account_id text;
  requested_account_scope_hash text;
  requested_symbol text;
  requested_side text;
  requested_order_type text;
  requested_quantity bigint;
  requested_submitted_price bigint;
  requested_result_json text;
  requested_warnings_accepted boolean;
  requested_observed_generation bigint;
  requested_submitted_at timestamptz;
  requested_created_at timestamptz;
  requested_order_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_gate record;
  stored_decision record;
  stored_request_hash text;
  stored_result_json text;
  reference_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'decisionId',
       'orderId',
       'observedKillSwitchGeneration',
       'idempotencyScopeHash',
       'idempotencyOwnerScopeHash',
       'requestHash',
       'accountId',
       'accountScopeHash',
       'symbol',
       'side',
       'orderType',
       'quantity',
       'submittedPriceKrw',
       'orderIntent',
       'resultCanonicalJson',
       'warningsAccepted',
       'submittedAt',
       'createdAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'decisionId',
       'orderId',
       'observedKillSwitchGeneration',
       'idempotencyScopeHash',
       'idempotencyOwnerScopeHash',
       'requestHash',
       'accountId',
       'accountScopeHash',
       'symbol',
       'side',
       'orderType',
       'quantity',
       'submittedPriceKrw',
       'orderIntent',
       'resultCanonicalJson',
       'warningsAccepted',
       'submittedAt',
       'createdAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_request_id := requested_payload ->> 'requestId';
  requested_decision_id := requested_payload ->> 'decisionId';
  requested_order_id := requested_payload ->> 'orderId';
  requested_observed_generation := (requested_payload ->> 'observedKillSwitchGeneration')::bigint;
  requested_scope_hash := requested_payload ->> 'idempotencyScopeHash';
  requested_owner_scope_hash := requested_payload ->> 'idempotencyOwnerScopeHash';
  requested_request_hash := requested_payload ->> 'requestHash';
  requested_account_id := requested_payload ->> 'accountId';
  requested_account_scope_hash := requested_payload ->> 'accountScopeHash';
  requested_symbol := requested_payload ->> 'symbol';
  requested_side := requested_payload ->> 'side';
  requested_order_type := requested_payload ->> 'orderType';
  requested_quantity := (requested_payload ->> 'quantity')::bigint;
  requested_submitted_price :=
    CASE
      WHEN jsonb_typeof(requested_payload -> 'submittedPriceKrw') = 'null' THEN NULL
      ELSE (requested_payload ->> 'submittedPriceKrw')::bigint
    END;
  requested_result_json := requested_payload ->> 'resultCanonicalJson';
  requested_warnings_accepted := (requested_payload ->> 'warningsAccepted')::boolean;
  requested_submitted_at := (requested_payload ->> 'submittedAt')::timestamptz;
  requested_created_at := (requested_payload ->> 'createdAt')::timestamptz;
  requested_order_event_id := requested_payload ->> 'orderEventId';
  requested_audit_log_id := requested_payload ->> 'auditLogId';
  requested_outbox_event_id := requested_payload ->> 'outboxEventId';

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> requested_actor_role
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;

  -- direct table 권한이 없는 SECURITY DEFINER 경계 안에서만 Decision FORCE RLS owner를 설정한다.
  PERFORM set_config('app.actor_user_id', requested_actor_user_id, true);

  PERFORM pg_advisory_xact_lock(
    hashtextextended('mock-order:idempotency:' || requested_scope_hash, 3101)
  );
  PERFORM pg_advisory_xact_lock(
    hashtextextended('mock-order:decision:' || requested_decision_id, 3101)
  );

  SELECT gate.active, gate.generation
  INTO stored_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id = 'GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_gate.active
     OR stored_gate.generation <> requested_observed_generation THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT stored.request_hash, stored.result_canonical_json
  INTO stored_request_hash, stored_result_json
  FROM public.orders stored
  WHERE stored.idempotency_scope_hash = requested_scope_hash
    AND stored.idempotency_owner_scope_hash = requested_owner_scope_hash
  LIMIT 1;
  IF FOUND THEN
    IF stored_request_hash = requested_request_hash THEN
      RETURN QUERY SELECT 'REPLAY'::text, stored_result_json;
    ELSE
      RETURN QUERY SELECT 'IDEMPOTENCY_CONFLICT'::text, NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT
    decision.evaluation_id,
    decision.portfolio_source,
    decision.outcome,
    decision.can_submit_order,
    decision.enforcement_action,
    decision.valid_until,
    artifact.snapshot_artifact_canonical_json,
    artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}' AS owner_scope_hash,
    EXISTS (
      SELECT 1
      FROM public.decision_invalidations invalidation
      WHERE invalidation.decision_id = decision.decision_id
    ) AS invalidated,
    EXISTS (
      SELECT 1
      FROM public.orders consumed
      WHERE consumed.decision_id = decision.decision_id
    ) AS consumed
  INTO stored_decision
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
   AND artifact.evaluation_id = decision.evaluation_id
  WHERE decision.user_id = requested_actor_user_id
    AND decision.decision_id = requested_decision_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'DECISION_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF NOT stored_decision.valid_until > requested_created_at THEN
    RETURN QUERY SELECT 'DECISION_EXPIRED'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.invalidated
     OR NOT stored_decision.can_submit_order
     OR stored_decision.outcome NOT IN ('ALLOW', 'WARN')
     OR (
       stored_decision.enforcement_action <> 'NONE'
       AND NOT requested_warnings_accepted
     ) THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.consumed THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.portfolio_source <> 'KIS_MOCK'
     OR stored_decision.owner_scope_hash <> requested_account_scope_hash
     OR requested_account_id <> 'acct_' || left(requested_account_scope_hash, 32)
     OR stored_decision.snapshot_artifact_canonical_json::jsonb -> 'orderIntent'
       <> requested_payload -> 'orderIntent' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  INSERT INTO public.orders (
    order_id, user_id, account_id, account_scope_hash, decision_id,
    decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
    idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
    quantity, submitted_price_krw, status, order_intent_json,
    result_canonical_json, acknowledged_by, acknowledged_at, submitted_at,
    created_at, updated_at
  )
  VALUES (
    requested_order_id, requested_actor_user_id, requested_account_id,
    requested_account_scope_hash, requested_decision_id,
    stored_decision.evaluation_id, 'KIS_MOCK', requested_scope_hash,
    requested_owner_scope_hash, requested_request_hash, requested_symbol,
    requested_side, requested_order_type, requested_quantity,
    requested_submitted_price, 'SUBMITTED', requested_payload -> 'orderIntent',
    requested_result_json, requested_actor_user_id, requested_created_at,
    requested_submitted_at, requested_created_at, requested_created_at
  );

  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id, requested_order_id, 'MOCK_ORDER_SUBMITTED',
    'SUBMITTED',
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'SUBMITTED'
    ),
    requested_created_at,
    1
  );

  reference_payload :=
    jsonb_build_object(
      'orderId', requested_order_id,
      'decisionId', requested_decision_id,
      'evaluationId', stored_decision.evaluation_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'SUBMITTED',
      'idempotencyScopeHash', requested_scope_hash
    );
  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    'MOCK_ORDER_SUBMITTED', 'ORDER', requested_order_id,
    requested_request_id, reference_payload, requested_created_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id, 'brokerage.mock-order-submitted.v1', 'ORDER',
    requested_order_id, requested_order_id, reference_payload, '1.0.0',
    'PENDING', 0, requested_created_at, requested_created_at
  );

  RETURN QUERY SELECT 'CREATED'::text, requested_result_json;
EXCEPTION
  WHEN unique_violation THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
END
$create_mock_order$;
ALTER FUNCTION create_mock_order(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION create_mock_order(jsonb, text) FROM PUBLIC;

CREATE FUNCTION request_mock_order_cancel(
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
AS $request_mock_order_cancel$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_order_id text;
  requested_cancelled_at timestamptz;
  requested_order_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_order record;
  current_status text;
  next_event_seq integer;
  reference_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'orderId',
       'cancelledAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'orderId',
       'cancelledAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY
    SELECT 'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_request_id := requested_payload ->> 'requestId';
  requested_order_id := requested_payload ->> 'orderId';
  requested_cancelled_at := (requested_payload ->> 'cancelledAt')::timestamptz;
  requested_order_event_id := requested_payload ->> 'orderEventId';
  requested_audit_log_id := requested_payload ->> 'auditLogId';
  requested_outbox_event_id := requested_payload ->> 'outboxEventId';

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
    hashtextextended('mock-order:cancel:' || requested_order_id, 3101)
  );
  SELECT
    stored.order_id,
    stored.account_id,
    stored.brokerage_mode,
    stored.status,
    stored.submitted_at,
    stored.decision_id
  INTO stored_order
  FROM public.orders stored
  WHERE stored.order_id = requested_order_id
    AND stored.user_id = requested_actor_user_id
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
  IF current_status NOT IN ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED') THEN
    RETURN QUERY
    SELECT 'ORDER_CONFLICT'::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::timestamptz, NULL::text;
    RETURN;
  END IF;

  SELECT COALESCE(max(event.event_seq), 0) + 1
  INTO next_event_seq
  FROM public.order_events event
  WHERE event.order_id = requested_order_id;
  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id, requested_order_id, 'MOCK_ORDER_CANCEL_REQUESTED',
    'CANCEL_REQUESTED',
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'CANCEL_REQUESTED'
    ),
    requested_cancelled_at,
    next_event_seq
  );

  reference_payload :=
    jsonb_build_object(
      'orderId', requested_order_id,
      'decisionId', stored_order.decision_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'CANCEL_REQUESTED',
      'requestedByRole', requested_actor_role,
      'requestedAt', requested_cancelled_at::text
    );
  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    'MOCK_ORDER_CANCEL_REQUESTED', 'ORDER', requested_order_id,
    requested_request_id, reference_payload, requested_cancelled_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id, 'brokerage.mock-order-cancel-requested.v1',
    'ORDER', requested_order_id, requested_order_id, reference_payload,
    '1.0.0', 'PENDING', 0, requested_cancelled_at, requested_cancelled_at
  );

  RETURN QUERY
  SELECT
    'CANCEL_REQUESTED'::text,
    stored_order.order_id,
    stored_order.account_id,
    stored_order.brokerage_mode,
    'CANCEL_REQUESTED'::text,
    stored_order.submitted_at,
    stored_order.decision_id;
END
$request_mock_order_cancel$;
ALTER FUNCTION request_mock_order_cancel(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION request_mock_order_cancel(jsonb, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  orders,
  order_events,
  mock_order_owner_projection,
  brokerage_db_capability_keys
FROM PUBLIC;
REVOKE ALL ON FUNCTION
  assert_brokerage_database_capability(text),
  read_mock_order_decision(text, text, text),
  find_mock_order_idempotency_result(text, text, timestamptz, text),
  read_mock_order_owner_projection(text, text, text),
  create_mock_order(jsonb, text),
  request_mock_order_cancel(jsonb, text)
FROM PUBLIC;

DO $v12_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      orders,
      order_events,
      mock_order_owner_projection,
      brokerage_db_capability_keys
    FROM decision_app;
    REVOKE ALL ON FUNCTION assert_brokerage_database_capability(text)
      FROM decision_app;
    GRANT EXECUTE ON FUNCTION
      read_mock_order_decision(text, text, text),
      find_mock_order_idempotency_result(text, text, timestamptz, text),
      read_mock_order_owner_projection(text, text, text),
      create_mock_order(jsonb, text),
      request_mock_order_cancel(jsonb, text)
    TO decision_app;

    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$v12_privileges$;
