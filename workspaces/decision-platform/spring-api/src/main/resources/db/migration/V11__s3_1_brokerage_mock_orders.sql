-- S3.1은 배포된 주문 이력이 없다는 계약 아래 legacy skeleton을 KIS Mock order ledger로 전환한다.
DO $v11_precondition$
DECLARE
  order_count bigint;
  order_event_count bigint;
BEGIN
  SELECT count(*) INTO order_count FROM orders;
  SELECT count(*) INTO order_event_count FROM order_events;
  IF order_count <> 0 OR order_event_count <> 0 THEN
    RAISE EXCEPTION
      'S3.1 V11 precondition failed: orders=% order_events=%; legacy order migration is not approved',
      order_count,
      order_event_count;
  END IF;
END
$v11_precondition$;

ALTER TABLE orders
  DROP CONSTRAINT IF EXISTS orders_idempotency_key_unique,
  DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders
  DROP COLUMN IF EXISTS idempotency_key;
ALTER TABLE orders
  RENAME COLUMN limit_price TO submitted_price_krw;
ALTER TABLE orders
  ADD COLUMN decision_evaluation_id text NOT NULL,
  ADD COLUMN brokerage_mode text NOT NULL DEFAULT 'KIS_MOCK',
  ADD COLUMN account_scope_hash text NOT NULL,
  ADD COLUMN idempotency_scope_hash text NOT NULL,
  ADD COLUMN idempotency_owner_scope_hash text NOT NULL,
  ADD COLUMN request_hash text NOT NULL,
  ADD COLUMN result_canonical_json text NOT NULL,
  ADD COLUMN acknowledged_by text REFERENCES users(user_id) ON DELETE RESTRICT,
  ADD COLUMN acknowledged_at timestamptz;
ALTER TABLE orders
  ALTER COLUMN submitted_at SET NOT NULL,
  ALTER COLUMN acknowledged_by SET NOT NULL,
  ALTER COLUMN acknowledged_at SET NOT NULL,
  ALTER COLUMN order_intent_json SET NOT NULL,
  ALTER COLUMN brokerage_mode DROP DEFAULT;
ALTER TABLE orders
  ADD CONSTRAINT orders_identity_check CHECK (order_id ~ '^ord_mock_[0-9a-f]{32}$'),
  ADD CONSTRAINT orders_account_identity_check CHECK (
    account_id ~ '^acct_[0-9a-f]{32}$'
    AND account_scope_hash ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT orders_brokerage_mode_check CHECK (brokerage_mode = 'KIS_MOCK'),
  ADD CONSTRAINT orders_status_check CHECK (
    status IN (
      'SUBMITTED',
      'ACCEPTED',
      'PARTIALLY_FILLED',
      'FILLED',
      'CANCEL_REQUESTED',
      'CANCELLED',
      'REJECTED'
    )
  ),
  ADD CONSTRAINT orders_mock_price_check CHECK (
    (order_type = 'MARKET' AND submitted_price_krw IS NULL)
    OR (order_type = 'LIMIT' AND submitted_price_krw IS NOT NULL AND submitted_price_krw > 0)
  ),
  ADD CONSTRAINT orders_idempotency_hash_check CHECK (
    idempotency_scope_hash ~ '^[0-9a-f]{64}$'
    AND idempotency_owner_scope_hash ~ '^[0-9a-f]{64}$'
    AND request_hash ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT orders_decision_evaluation_fkey
    FOREIGN KEY (decision_id, decision_evaluation_id)
    REFERENCES decisions(decision_id, evaluation_id)
    ON DELETE RESTRICT,
  ADD CONSTRAINT orders_ack_actor_check CHECK (acknowledged_by = user_id),
  ADD CONSTRAINT orders_time_check CHECK (
    submitted_at >= created_at
    AND updated_at >= created_at
    AND acknowledged_at >= created_at
  ),
  ADD CONSTRAINT orders_payload_check CHECK (
    jsonb_typeof(order_intent_json) = 'object'
    AND octet_length(order_intent_json::text) BETWEEN 2 AND 65536
    AND octet_length(result_canonical_json) BETWEEN 2 AND 65536
    AND jsonb_typeof(result_canonical_json::jsonb) = 'object'
  ),
  -- V1의 orders_decision_id_unique는 그대로 보존해 1 decision = 1 order를 DB 최후방어로 둔다.
  ADD CONSTRAINT orders_idempotency_scope_unique UNIQUE (idempotency_scope_hash);
CREATE INDEX orders_owner_created_idx
  ON orders (user_id, created_at DESC, order_id);
CREATE INDEX orders_owner_decision_idx
  ON orders (user_id, decision_id);

ALTER TABLE order_events
  ADD CONSTRAINT order_events_identity_check CHECK (order_event_id ~ '^oev_[0-9a-f]{32}$'),
  ADD CONSTRAINT order_events_type_check CHECK (
    event_type IN (
      'MOCK_ORDER_SUBMITTED',
      'MOCK_ORDER_ACCEPTED',
      'MOCK_ORDER_REJECTED',
      'MOCK_ORDER_PARTIALLY_FILLED',
      'MOCK_ORDER_FILLED',
      'MOCK_ORDER_CANCEL_REQUESTED',
      'MOCK_ORDER_CANCELLED',
      'INVALID_TRANSITION'
    )
  ),
  ADD CONSTRAINT order_events_status_check CHECK (
    event_status IS NULL OR event_status IN (
      'SUBMITTED',
      'ACCEPTED',
      'PARTIALLY_FILLED',
      'FILLED',
      'CANCEL_REQUESTED',
      'CANCELLED',
      'REJECTED'
    )
  ),
  ADD CONSTRAINT order_events_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 65536
  );

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
CREATE POLICY orders_owner_select_policy
  ON orders
  FOR SELECT
  USING (user_id = current_setting('app.actor_user_id', true));
CREATE POLICY orders_owner_insert_policy
  ON orders
  FOR INSERT
  TO decision_app
  WITH CHECK (user_id = current_setting('app.actor_user_id', true));
CREATE POLICY orders_definer_select_policy
  ON orders
  FOR SELECT
  TO flyway
  USING (true);

ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_events FORCE ROW LEVEL SECURITY;
CREATE POLICY order_events_owner_select_policy
  ON order_events
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1
      FROM orders owned_order
      WHERE owned_order.order_id = order_events.order_id
        AND owned_order.user_id = current_setting('app.actor_user_id', true)
    )
  );
CREATE POLICY order_events_owner_insert_policy
  ON order_events
  FOR INSERT
  TO decision_app
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM orders owned_order
      WHERE owned_order.order_id = order_events.order_id
        AND owned_order.user_id = current_setting('app.actor_user_id', true)
    )
  );
CREATE POLICY order_events_definer_select_policy
  ON order_events
  FOR SELECT
  TO flyway
  USING (true);

CREATE FUNCTION read_mock_order_decision()
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
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_mock_order_decision$
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
    artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}' AS portfolio_owner_scope_hash,
    invalidation.invalidation_id IS NOT NULL AS invalidated,
    invalidation.reason_class AS invalidation_reason_class,
    consumed.order_id AS consumed_by_order_id
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
  WHERE decision.user_id = current_setting('app.actor_user_id', true)
    AND decision.decision_id = current_setting('app.requested_decision_id', true)
$read_mock_order_decision$;
ALTER FUNCTION read_mock_order_decision() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_mock_order_decision() FROM PUBLIC;

CREATE FUNCTION find_mock_order_idempotency_result(
  requested_scope_hash text,
  requested_owner_scope_hash text,
  requested_now timestamptz
)
RETURNS TABLE (
  request_hash text,
  result_canonical_json text,
  expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $find_mock_order_idempotency_result$
  SELECT
    stored.request_hash,
    stored.result_canonical_json,
    stored.created_at + interval '24 hours' AS expires_at
  FROM public.orders stored
  WHERE stored.idempotency_scope_hash = requested_scope_hash
    AND stored.idempotency_owner_scope_hash = requested_owner_scope_hash
    AND stored.created_at + interval '24 hours' > requested_now
  LIMIT 1
$find_mock_order_idempotency_result$;
ALTER FUNCTION find_mock_order_idempotency_result(text, text, timestamptz)
  OWNER TO flyway;
REVOKE ALL ON FUNCTION find_mock_order_idempotency_result(text, text, timestamptz)
  FROM PUBLIC;

CREATE FUNCTION read_mock_order_owner_projection()
RETURNS TABLE (
  order_id text,
  account_id text,
  brokerage_mode text,
  status text,
  submitted_at timestamptz,
  decision_id text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_mock_order_owner_projection$
  SELECT
    orders.order_id,
    orders.account_id,
    orders.brokerage_mode,
    COALESCE(latest_event.event_status, orders.status) AS status,
    orders.submitted_at,
    orders.decision_id
  FROM public.orders orders
  LEFT JOIN LATERAL (
    SELECT event.event_status
    FROM public.order_events event
    WHERE event.order_id = orders.order_id
      AND event.event_status IS NOT NULL
    -- 같은 microsecond에 submit/cancel이 들어와도 상태 투영이 event ID 난수 정렬에 기대지 않게 한다.
    ORDER BY
      event.created_at DESC,
      CASE event.event_type
        WHEN 'MOCK_ORDER_CANCEL_REQUESTED' THEN 20
        WHEN 'MOCK_ORDER_SUBMITTED' THEN 10
        ELSE 0
      END DESC,
      event.order_event_id DESC
    LIMIT 1
  ) latest_event ON true
  WHERE orders.user_id = current_setting('app.actor_user_id', true)
    AND orders.order_id = current_setting('app.requested_order_id', true)
  LIMIT 1
$read_mock_order_owner_projection$;
ALTER FUNCTION read_mock_order_owner_projection() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_mock_order_owner_projection() FROM PUBLIC;

CREATE VIEW mock_order_owner_projection
WITH (security_barrier = true, security_invoker = true)
AS
SELECT * FROM read_mock_order_owner_projection();

REVOKE ALL PRIVILEGES ON TABLE
  orders,
  order_events,
  mock_order_owner_projection
FROM PUBLIC;
REVOKE ALL ON FUNCTION
  read_mock_order_decision(),
  find_mock_order_idempotency_result(text, text, timestamptz),
  read_mock_order_owner_projection()
FROM PUBLIC;

DO $v11_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flyway') THEN
    GRANT SELECT ON TABLE
      decisions,
      decision_artifacts,
      decision_invalidations,
      orders,
      order_events
    TO flyway;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      orders,
      order_events,
      mock_order_owner_projection
    FROM decision_app;

    GRANT INSERT, SELECT ON TABLE orders TO decision_app;
    GRANT INSERT, SELECT ON TABLE order_events TO decision_app;
    GRANT SELECT ON TABLE mock_order_owner_projection TO decision_app;
    GRANT EXECUTE ON FUNCTION
      read_mock_order_decision(),
      find_mock_order_idempotency_result(text, text, timestamptz),
      read_mock_order_owner_projection()
    TO decision_app;

    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$v11_privileges$;
