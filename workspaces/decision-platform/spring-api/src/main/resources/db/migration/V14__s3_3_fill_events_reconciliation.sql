-- S3.3은 저장된 sanitized 체결 관측만 소비하고 provider 호출이나 public fill claim 경로를 만들지 않는다.
DO $v14_precondition$
DECLARE
  mock_fill_event_count bigint;
BEGIN
  IF to_regclass('public.order_fill_observations') IS NOT NULL THEN
    RAISE EXCEPTION 'S3.3 V14 precondition failed: fill observation table already exists';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'orders'
      AND column_name = 'filled_quantity'
  ) THEN
    RAISE EXCEPTION 'S3.3 V14 precondition failed: order fill projection already exists';
  END IF;
  SELECT count(*)
  INTO mock_fill_event_count
  FROM order_events
  WHERE event_type IN ('MOCK_ORDER_PARTIALLY_FILLED', 'MOCK_ORDER_FILLED');
  IF mock_fill_event_count <> 0 THEN
    RAISE EXCEPTION
      'S3.3 V14 precondition failed: mock fill events=% require an approved backfill',
      mock_fill_event_count;
  END IF;
END
$v14_precondition$;

ALTER TABLE orders
  ADD COLUMN filled_quantity bigint,
  ADD COLUMN leaves_quantity bigint,
  ADD COLUMN unfilled_terminated_quantity bigint,
  ADD COLUMN average_fill_price_krw bigint,
  ADD COLUMN reconciliation_status text NOT NULL DEFAULT 'NOT_APPLICABLE',
  ADD COLUMN reconciled_at timestamptz;

-- S3.2의 deterministic full fill은 paper event가 이미 가진 sanitized 체결가로만 backfill한다.
UPDATE orders stored
SET filled_quantity = stored.quantity,
    leaves_quantity = 0,
    unfilled_terminated_quantity = 0,
    average_fill_price_krw = (paper.payload_json ->> 'fillPriceKrw')::bigint
FROM paper_order_events paper
WHERE stored.order_id = paper.order_id
  AND stored.brokerage_mode = 'INTERNAL_PAPER'
  AND stored.status = 'FILLED';

UPDATE orders
SET filled_quantity = 0,
    leaves_quantity =
      CASE WHEN status IN ('CANCELLED', 'REJECTED') THEN 0 ELSE quantity END,
    unfilled_terminated_quantity =
      CASE WHEN status IN ('CANCELLED', 'REJECTED') THEN quantity ELSE 0 END,
    average_fill_price_krw = NULL
WHERE filled_quantity IS NULL;

ALTER TABLE orders
  ALTER COLUMN filled_quantity SET DEFAULT 0,
  ALTER COLUMN filled_quantity SET NOT NULL,
  ALTER COLUMN leaves_quantity SET NOT NULL,
  ALTER COLUMN unfilled_terminated_quantity SET DEFAULT 0,
  ALTER COLUMN unfilled_terminated_quantity SET NOT NULL,
  ADD CONSTRAINT orders_filled_quantity_nonnegative_check
    CHECK (filled_quantity >= 0),
  ADD CONSTRAINT orders_leaves_quantity_nonnegative_check
    CHECK (leaves_quantity >= 0),
  ADD CONSTRAINT orders_unfilled_terminated_nonnegative_check
    CHECK (unfilled_terminated_quantity >= 0),
  ADD CONSTRAINT orders_average_fill_price_positive_check
    CHECK (average_fill_price_krw IS NULL OR average_fill_price_krw > 0),
  ADD CONSTRAINT orders_quantity_conservation_check CHECK (
    filled_quantity + leaves_quantity + unfilled_terminated_quantity = quantity
  ),
  ADD CONSTRAINT orders_terminal_leaves_check CHECK (
    status NOT IN ('FILLED', 'CANCELLED', 'REJECTED')
    OR leaves_quantity = 0
  ),
  ADD CONSTRAINT orders_active_unfilled_terminated_check CHECK (
    status IN ('FILLED', 'CANCELLED', 'REJECTED')
    OR unfilled_terminated_quantity = 0
  ),
  ADD CONSTRAINT orders_filled_status_check CHECK (
    status <> 'FILLED' OR filled_quantity = quantity
  ),
  ADD CONSTRAINT orders_average_price_presence_check CHECK (
    (filled_quantity = 0) = (average_fill_price_krw IS NULL)
  ),
  ADD CONSTRAINT orders_reconciliation_status_check CHECK (
    reconciliation_status IN ('NOT_APPLICABLE', 'MATCHED', 'MISMATCH')
  ),
  ADD CONSTRAINT orders_reconciliation_time_check CHECK (
    (reconciliation_status = 'NOT_APPLICABLE') = (reconciled_at IS NULL)
  );
CREATE INDEX orders_reconciliation_mismatch_idx
  ON orders (reconciliation_status, updated_at, order_id)
  WHERE reconciliation_status = 'MISMATCH';

-- 기존 S3.1/S3.2 insert/cancel 함수가 새 projection을 빠뜨리지 않도록 flyway definer 호출만 보완한다.
CREATE FUNCTION initialize_order_fill_projection()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog
AS $initialize_order_fill_projection$
BEGIN
  IF NOT pg_has_role(current_user, 'flyway', 'USAGE') THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.brokerage_mode = 'INTERNAL_PAPER' AND NEW.status = 'FILLED' THEN
      NEW.filled_quantity := NEW.quantity;
      NEW.leaves_quantity := 0;
      NEW.unfilled_terminated_quantity := 0;
      NEW.average_fill_price_krw :=
        (NEW.result_canonical_json::jsonb #>> '{fill,priceKrw}')::bigint;
    ELSE
      NEW.filled_quantity := 0;
      NEW.leaves_quantity := NEW.quantity;
      NEW.unfilled_terminated_quantity := 0;
      NEW.average_fill_price_krw := NULL;
    END IF;
    NEW.reconciliation_status := 'NOT_APPLICABLE';
    NEW.reconciled_at := NULL;
  ELSIF OLD.status <> NEW.status
        AND NEW.status IN ('CANCELLED', 'REJECTED')
        AND NEW.filled_quantity = OLD.filled_quantity
        AND NEW.leaves_quantity = OLD.leaves_quantity THEN
    NEW.leaves_quantity := 0;
    NEW.unfilled_terminated_quantity := NEW.quantity - NEW.filled_quantity;
  END IF;
  RETURN NEW;
END
$initialize_order_fill_projection$;
ALTER FUNCTION initialize_order_fill_projection() OWNER TO flyway;
REVOKE ALL ON FUNCTION initialize_order_fill_projection() FROM PUBLIC;
CREATE TRIGGER orders_fill_projection_guard
BEFORE INSERT OR UPDATE OF status ON orders
FOR EACH ROW
EXECUTE FUNCTION initialize_order_fill_projection();

CREATE TABLE order_fill_observations (
  observation_id text PRIMARY KEY
    CHECK (observation_id ~ '^ofo_[0-9a-f]{32}$'),
  order_id text NOT NULL REFERENCES orders(order_id) ON DELETE RESTRICT,
  provider_exec_ref_hash text NOT NULL
    CHECK (provider_exec_ref_hash ~ '^[0-9a-f]{64}$'),
  exec_type text NOT NULL
    CHECK (exec_type IN ('PARTIAL_FILL', 'FILL', 'CANCELLED', 'REJECTED')),
  fill_quantity bigint NOT NULL CHECK (fill_quantity >= 0),
  fill_price_krw bigint CHECK (fill_price_krw IS NULL OR fill_price_krw > 0),
  cumulative_quantity bigint NOT NULL CHECK (cumulative_quantity >= 0),
  leaves_quantity bigint NOT NULL CHECK (leaves_quantity >= 0),
  average_fill_price_krw bigint
    CHECK (average_fill_price_krw IS NULL OR average_fill_price_krw > 0),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (schema_version = '1'),
  source_version text NOT NULL CHECK (octet_length(source_version) BETWEEN 1 AND 128),
  source_ref text NOT NULL
    CHECK (source_ref ~ '^[0-9A-Za-z._:-]{1,128}$'),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT order_fill_observations_order_exec_unique
    UNIQUE (order_id, provider_exec_ref_hash),
  CONSTRAINT order_fill_observations_price_pair_check CHECK (
    (exec_type IN ('PARTIAL_FILL', 'FILL')) = (fill_price_krw IS NOT NULL)
  ),
  CONSTRAINT order_fill_observations_quantity_check CHECK (
    fill_quantity <= cumulative_quantity
    AND (
      (exec_type IN ('PARTIAL_FILL', 'FILL') AND fill_quantity > 0)
      OR (exec_type IN ('CANCELLED', 'REJECTED') AND fill_quantity = 0)
    )
  ),
  CONSTRAINT order_fill_observations_time_check CHECK (received_at >= observed_at),
  CONSTRAINT order_fill_observations_notional_check CHECK (
    fill_price_krw IS NULL
    OR fill_quantity = 0
    OR fill_price_krw <= 9223372036854775807 / fill_quantity
  )
);
CREATE INDEX order_fill_observations_order_sequence_idx
  ON order_fill_observations (order_id, observed_at, observation_id);
CREATE INDEX order_fill_observations_complete_idx
  ON order_fill_observations (order_id, received_at, observation_id)
  WHERE completeness = 'COMPLETE';

-- observation은 수정하지 않고 별도 append-only receipt로 bounded consumer 진행 위치만 남긴다.
CREATE TABLE order_fill_application_receipts (
  receipt_id text PRIMARY KEY CHECK (receipt_id ~ '^ofr_[0-9a-f]{32}$'),
  observation_id text NOT NULL UNIQUE
    REFERENCES order_fill_observations(observation_id) ON DELETE RESTRICT,
  order_id text NOT NULL REFERENCES orders(order_id) ON DELETE RESTRICT,
  outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'DUPLICATE', 'INVALID')),
  invalid_reason text CHECK (
    (outcome = 'INVALID') = (invalid_reason IS NOT NULL)
    AND (
      invalid_reason IS NULL
      OR invalid_reason IN (
        'NON_MONOTONIC_CUM_QTY',
        'CUM_QTY_OVERFLOW',
        'TERMINAL_STATE',
        'INVALID_QUANTITY',
        'INVALID_LEAVES_QUANTITY',
        'INVALID_FILL_PRICE',
        'CANCEL_REQUESTED_PARTIAL_FILL'
      )
    )
  ),
  applied_at timestamptz NOT NULL
);
CREATE INDEX order_fill_receipts_order_idx
  ON order_fill_application_receipts (order_id, applied_at, observation_id);

ALTER TABLE order_fill_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_fill_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE order_fill_application_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_fill_application_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY order_fill_observations_definer_select_policy
  ON order_fill_observations FOR SELECT TO flyway USING (true);
CREATE POLICY order_fill_observations_definer_insert_policy
  ON order_fill_observations FOR INSERT TO flyway WITH CHECK (true);
CREATE POLICY order_fill_receipts_definer_select_policy
  ON order_fill_application_receipts FOR SELECT TO flyway USING (true);
CREATE POLICY order_fill_receipts_definer_insert_policy
  ON order_fill_application_receipts FOR INSERT TO flyway WITH CHECK (true);
DO $v14_writer_policy$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_fill_writer') THEN
    EXECUTE
      'CREATE POLICY order_fill_observations_writer_insert_policy '
      'ON order_fill_observations FOR INSERT TO decision_fill_writer WITH CHECK (true)';
  END IF;
END
$v14_writer_policy$;

-- S3.3 evidence는 기존 ORDER contract를 느슨하게 하지 않고 별도 exact target/event constraint로 격리한다.
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_order_reconciliation_contract_check CHECK (
    target_type <> 'ORDER_RECONCILIATION'
    OR (
      action = 'ORDER_RECONCILED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' IN ('KIS_MOCK', 'INTERNAL_PAPER')
      AND payload_json ->> 'reconciliationStatus'
        IN ('NOT_APPLICABLE', 'MATCHED', 'MISMATCH')
      AND (payload_json ->> 'appliedEventCount')::integer BETWEEN 0 AND 200
      AND jsonb_typeof(payload_json -> 'hasMore') = 'boolean'
      AND payload_json ?& ARRAY[
        'orderId',
        'brokerageMode',
        'reconciliationStatus',
        'appliedEventCount',
        'invalidEventCount',
        'hasMore',
        'checkedAt'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'brokerageMode',
        'reconciliationStatus',
        'appliedEventCount',
        'invalidEventCount',
        'hasMore',
        'checkedAt'
      ] = '{}'::jsonb
    )
  );
ALTER TABLE event_outbox
  ADD CONSTRAINT event_outbox_order_reconciliation_contract_check CHECK (
    event_type <> 'brokerage.order-reconciled.v1'
    OR (
      aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' IN ('KIS_MOCK', 'INTERNAL_PAPER')
      AND payload_json ->> 'reconciliationStatus'
        IN ('NOT_APPLICABLE', 'MATCHED', 'MISMATCH')
      AND payload_json ?& ARRAY[
        'orderId',
        'brokerageMode',
        'reconciliationStatus',
        'appliedEventCount',
        'invalidEventCount',
        'hasMore',
        'checkedAt'
      ]
      AND payload_json - ARRAY[
        'orderId',
        'brokerageMode',
        'reconciliationStatus',
        'appliedEventCount',
        'invalidEventCount',
        'hasMore',
        'checkedAt'
      ] = '{}'::jsonb
    )
  );
CREATE TRIGGER audit_logs_order_reconciliation_writer_guard
BEFORE INSERT ON audit_logs
FOR EACH ROW
WHEN (NEW.target_type = 'ORDER_RECONCILIATION')
EXECUTE FUNCTION enforce_brokerage_evidence_writer();
CREATE TRIGGER event_outbox_order_reconciliation_writer_guard
BEFORE INSERT ON event_outbox
FOR EACH ROW
WHEN (NEW.event_type = 'brokerage.order-reconciled.v1')
EXECUTE FUNCTION enforce_brokerage_evidence_writer();

CREATE FUNCTION read_order_reconciliation_state(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  state_json text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_order_reconciliation_state$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_order_id text;
  requested_reconciled_at timestamptz;
  stored_actor record;
  stored_order record;
  current_status text;
  observations jsonb;
  remaining boolean;
  observation_count bigint;
  observed_fill_quantity bigint;
  recomputed_average bigint;
  provider_final_average bigint;
  fill_notional numeric;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'orderId', 'reconciledAt'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'orderId', 'reconciledAt'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;
  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_order_id := requested_payload ->> 'orderId';
  requested_reconciled_at := (requested_payload ->> 'reconciledAt')::timestamptz;

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> 'ADMIN'
     OR requested_actor_role <> 'ADMIN'
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT stored.*
  INTO stored_order
  FROM public.orders stored
  WHERE stored.order_id = requested_order_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'ORDER_NOT_FOUND'::text, NULL::text;
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

  IF stored_order.brokerage_mode = 'KIS_MOCK' THEN
    SELECT COALESCE(
      sum(
        applied_observation.fill_quantity::numeric
        * applied_observation.fill_price_krw::numeric
      ) FILTER (
        WHERE applied_receipt.outcome = 'APPLIED'
          AND applied_observation.exec_type IN ('PARTIAL_FILL', 'FILL')
      ),
      0
    )
    INTO fill_notional
    FROM public.order_fill_application_receipts applied_receipt
    JOIN public.order_fill_observations applied_observation
      ON applied_observation.observation_id = applied_receipt.observation_id
    WHERE applied_receipt.order_id = requested_order_id;
  ELSE
    fill_notional :=
      stored_order.filled_quantity::numeric
      * COALESCE(stored_order.average_fill_price_krw, 0)::numeric;
  END IF;

  IF stored_order.brokerage_mode = 'KIS_MOCK' THEN
    SELECT COALESCE(jsonb_agg(candidate.item ORDER BY candidate.observed_at, candidate.observation_id), '[]'::jsonb)
    INTO observations
    FROM (
      SELECT
        observation.observed_at,
        observation.observation_id,
        jsonb_build_object(
          'observationId', observation.observation_id,
          'providerExecRefHash', observation.provider_exec_ref_hash,
          'execType', observation.exec_type,
          'fillQuantity', observation.fill_quantity,
          'fillPriceKrw', observation.fill_price_krw,
          'cumulativeQuantity', observation.cumulative_quantity,
          'leavesQuantity', observation.leaves_quantity,
          'averageFillPriceKrw', observation.average_fill_price_krw,
          'observedAt', observation.observed_at::text
        ) AS item
      FROM public.order_fill_observations observation
      WHERE observation.order_id = requested_order_id
        AND observation.completeness = 'COMPLETE'
        AND observation.observed_at <= requested_reconciled_at
        AND observation.received_at <= requested_reconciled_at
        AND NOT EXISTS (
          SELECT 1
          FROM public.order_fill_application_receipts receipt
          WHERE receipt.observation_id = observation.observation_id
        )
      ORDER BY observation.observed_at, observation.observation_id
      LIMIT 200
    ) candidate;
    SELECT count(*) > 200
    INTO remaining
    FROM public.order_fill_observations observation
    WHERE observation.order_id = requested_order_id
      AND observation.completeness = 'COMPLETE'
      AND observation.observed_at <= requested_reconciled_at
      AND observation.received_at <= requested_reconciled_at
      AND NOT EXISTS (
        SELECT 1
        FROM public.order_fill_application_receipts receipt
        WHERE receipt.observation_id = observation.observation_id
      );
  ELSE
    observations := '[]'::jsonb;
    remaining := false;
  END IF;

  IF stored_order.brokerage_mode = 'INTERNAL_PAPER' THEN
    SELECT
      count(*),
      COALESCE(sum((paper.payload_json ->> 'fillQuantity')::bigint), 0),
      CASE
        WHEN count(*) = 0 THEN NULL
        ELSE trunc(
          sum(
            (paper.payload_json ->> 'fillQuantity')::numeric
            * (paper.payload_json ->> 'fillPriceKrw')::numeric
          )
          / sum((paper.payload_json ->> 'fillQuantity')::numeric)
        )::bigint
      END
    INTO observation_count, observed_fill_quantity, recomputed_average
    FROM public.paper_order_events paper
    WHERE paper.order_id = requested_order_id;
    provider_final_average := recomputed_average;
  ELSE
    SELECT
      count(*),
      COALESCE(
        sum(aggregate_observation.fill_quantity)
          FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL')),
        0
      ),
      CASE
        WHEN COALESCE(
          sum(aggregate_observation.fill_quantity)
            FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL')),
          0
        ) = 0 THEN NULL
        ELSE trunc(
          sum(
            aggregate_observation.fill_quantity::numeric
            * aggregate_observation.fill_price_krw::numeric
          ) FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL'))
          / sum(aggregate_observation.fill_quantity::numeric)
            FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL'))
        )::bigint
      END
    INTO observation_count, observed_fill_quantity, recomputed_average
    FROM public.order_fill_observations aggregate_observation
    WHERE aggregate_observation.order_id = requested_order_id
      AND aggregate_observation.observed_at <= requested_reconciled_at
      AND aggregate_observation.received_at <= requested_reconciled_at;
    SELECT final_observation.average_fill_price_krw
    INTO provider_final_average
    FROM public.order_fill_observations final_observation
    WHERE final_observation.order_id = requested_order_id
      AND final_observation.exec_type IN ('PARTIAL_FILL', 'FILL')
      AND final_observation.observed_at <= requested_reconciled_at
      AND final_observation.received_at <= requested_reconciled_at
    ORDER BY
      final_observation.cumulative_quantity DESC,
      final_observation.observed_at DESC,
      final_observation.observation_id DESC
    LIMIT 1;
  END IF;

  RETURN QUERY
  SELECT
    'READY'::text,
    jsonb_build_object(
      'orderId', stored_order.order_id,
      'brokerageMode', stored_order.brokerage_mode,
      'status', current_status,
      'quantity', stored_order.quantity,
      'filledQuantity', stored_order.filled_quantity,
      'leavesQuantity', stored_order.leaves_quantity,
      'unfilledTerminatedQuantity', stored_order.unfilled_terminated_quantity,
      'fillNotionalKrw', fill_notional,
      'averageFillPriceKrw', stored_order.average_fill_price_krw,
      'reconciliationStatus', stored_order.reconciliation_status,
      'observationCount', observation_count,
      'observedFillQuantity', observed_fill_quantity,
      'recomputedAverageFillPriceKrw', recomputed_average,
      'providerFinalAverageFillPriceKrw', provider_final_average,
      'observations', observations,
      'hasMore', remaining
    )::text;
END
$read_order_reconciliation_state$;
ALTER FUNCTION read_order_reconciliation_state(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_order_reconciliation_state(jsonb, text) FROM PUBLIC;

-- 대기 중 snapshot이 낡지 않도록 advisory lock은 state read와 분리된 선행 statement에서 획득한다.
CREATE FUNCTION acquire_order_fill_reconciliation_lock(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $acquire_order_fill_reconciliation_lock$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_order_id text;
  stored_actor record;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'orderId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'orderId'
     ] <> '{}'::jsonb THEN
    RETURN 'VALIDATION_ERROR';
  END IF;
  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_order_id := requested_payload ->> 'orderId';

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> 'ADMIN'
     OR requested_actor_role <> 'ADMIN'
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN 'ACTOR_UNAUTHORIZED';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended('order-fill:' || requested_order_id, 3301)
  );
  IF NOT EXISTS (
    SELECT 1 FROM public.orders stored WHERE stored.order_id = requested_order_id
  ) THEN
    RETURN 'ORDER_NOT_FOUND';
  END IF;
  RETURN 'LOCKED';
END
$acquire_order_fill_reconciliation_lock$;
ALTER FUNCTION acquire_order_fill_reconciliation_lock(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION acquire_order_fill_reconciliation_lock(jsonb, text) FROM PUBLIC;

CREATE FUNCTION apply_stored_order_fills(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  order_id text,
  brokerage_mode text,
  status text,
  filled_quantity bigint,
  leaves_quantity bigint,
  unfilled_terminated_quantity bigint,
  average_fill_price_krw bigint,
  reconciliation_status text,
  reconciled_at timestamptz,
  applied_event_count integer,
  has_more boolean
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $apply_stored_order_fills$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_order_id text;
  requested_reconciled_at timestamptz;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  requested_expected_final jsonb;
  stored_actor record;
  stored_order record;
  observation record;
  current_status text;
  current_filled bigint;
  current_leaves bigint;
  current_unfilled_terminated bigint;
  current_average bigint;
  current_notional numeric;
  delta_quantity bigint;
  next_average bigint;
  next_event_seq integer;
  transition_outcome text;
  transition_reason text;
  transition_status text;
  transition_event_type text;
  transition_event_status text;
  transition_payload jsonb;
  event_count integer := 0;
  invalid_count integer := 0;
  observation_count bigint;
  observed_fill_quantity bigint;
  recomputed_average bigint;
  provider_final_average bigint;
  conservation_matches boolean;
  remaining boolean;
  final_reconciliation_status text;
  final_reconciled_at timestamptz;
  actual_final jsonb;
  reference_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId', 'orderId',
       'reconciledAt', 'auditLogId', 'outboxEventId', 'expectedFinal'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId', 'orderId',
       'reconciledAt', 'auditLogId', 'outboxEventId', 'expectedFinal'
     ] <> '{}'::jsonb
     OR jsonb_typeof(requested_payload -> 'expectedFinal') <> 'object' THEN
    RETURN QUERY
    SELECT
      'VALIDATION_ERROR'::text, NULL::text, NULL::text, NULL::text,
      NULL::bigint, NULL::bigint, NULL::bigint, NULL::bigint,
      NULL::text, NULL::timestamptz, NULL::integer, NULL::boolean;
    RETURN;
  END IF;

  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_request_id := requested_payload ->> 'requestId';
  requested_order_id := requested_payload ->> 'orderId';
  requested_reconciled_at := (requested_payload ->> 'reconciledAt')::timestamptz;
  requested_audit_log_id := requested_payload ->> 'auditLogId';
  requested_outbox_event_id := requested_payload ->> 'outboxEventId';
  requested_expected_final := requested_payload -> 'expectedFinal';

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> 'ADMIN'
     OR requested_actor_role <> 'ADMIN'
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY
    SELECT
      'ACTOR_UNAUTHORIZED'::text, NULL::text, NULL::text, NULL::text,
      NULL::bigint, NULL::bigint, NULL::bigint, NULL::bigint,
      NULL::text, NULL::timestamptz, NULL::integer, NULL::boolean;
    RETURN;
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended('order-fill:' || requested_order_id, 3301)
  );
  SELECT stored.*
  INTO stored_order
  FROM public.orders stored
  WHERE stored.order_id = requested_order_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY
    SELECT
      'ORDER_NOT_FOUND'::text, NULL::text, NULL::text, NULL::text,
      NULL::bigint, NULL::bigint, NULL::bigint, NULL::bigint,
      NULL::text, NULL::timestamptz, NULL::integer, NULL::boolean;
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
  current_filled := stored_order.filled_quantity;
  current_leaves := stored_order.leaves_quantity;
  current_unfilled_terminated := stored_order.unfilled_terminated_quantity;
  current_average := stored_order.average_fill_price_krw;
  IF stored_order.brokerage_mode = 'KIS_MOCK' THEN
    SELECT COALESCE(
      sum(
        applied_observation.fill_quantity::numeric
        * applied_observation.fill_price_krw::numeric
      ) FILTER (
        WHERE applied_receipt.outcome = 'APPLIED'
          AND applied_observation.exec_type IN ('PARTIAL_FILL', 'FILL')
      ),
      0
    )
    INTO current_notional
    FROM public.order_fill_application_receipts applied_receipt
    JOIN public.order_fill_observations applied_observation
      ON applied_observation.observation_id = applied_receipt.observation_id
    WHERE applied_receipt.order_id = requested_order_id;
  ELSE
    current_notional :=
      current_filled::numeric * COALESCE(current_average, 0)::numeric;
  END IF;
  SELECT COALESCE(max(event.event_seq), 0) + 1
  INTO next_event_seq
  FROM public.order_events event
  WHERE event.order_id = requested_order_id;

  IF stored_order.brokerage_mode = 'KIS_MOCK' THEN
    FOR observation IN
      SELECT source.*
      FROM public.order_fill_observations source
      WHERE source.order_id = requested_order_id
        AND source.completeness = 'COMPLETE'
        AND source.observed_at <= requested_reconciled_at
        AND source.received_at <= requested_reconciled_at
        AND NOT EXISTS (
          SELECT 1
          FROM public.order_fill_application_receipts receipt
          WHERE receipt.observation_id = source.observation_id
        )
      ORDER BY source.observed_at, source.observation_id
      LIMIT 200
    LOOP
      transition_outcome := NULL;
      transition_reason := NULL;
      transition_status := current_status;
      transition_event_type := NULL;
      transition_event_status := NULL;
      IF current_status IN ('FILLED', 'CANCELLED', 'REJECTED') THEN
        transition_outcome := 'INVALID';
        transition_reason := 'TERMINAL_STATE';
      ELSIF observation.cumulative_quantity < current_filled THEN
        transition_outcome := 'INVALID';
        transition_reason := 'NON_MONOTONIC_CUM_QTY';
      ELSIF observation.cumulative_quantity > stored_order.quantity THEN
        transition_outcome := 'INVALID';
        transition_reason := 'CUM_QTY_OVERFLOW';
      ELSIF current_status = 'CANCEL_REQUESTED'
            AND observation.exec_type = 'PARTIAL_FILL' THEN
        transition_outcome := 'INVALID';
        transition_reason := 'CANCEL_REQUESTED_PARTIAL_FILL';
      ELSIF observation.exec_type IN ('PARTIAL_FILL', 'FILL') THEN
        IF observation.cumulative_quantity = current_filled THEN
          transition_outcome := 'DUPLICATE';
        ELSE
          delta_quantity := observation.cumulative_quantity - current_filled;
          IF observation.fill_quantity <> delta_quantity THEN
            transition_outcome := 'INVALID';
            transition_reason := 'INVALID_QUANTITY';
          ELSIF observation.fill_price_krw IS NULL OR observation.fill_price_krw <= 0 THEN
            transition_outcome := 'INVALID';
            transition_reason := 'INVALID_FILL_PRICE';
          ELSIF observation.leaves_quantity <>
                stored_order.quantity - observation.cumulative_quantity
                OR (
                  observation.exec_type = 'PARTIAL_FILL'
                  AND observation.leaves_quantity = 0
                )
                OR (
                  observation.exec_type = 'FILL'
                  AND observation.leaves_quantity <> 0
                ) THEN
            transition_outcome := 'INVALID';
            transition_reason := 'INVALID_LEAVES_QUANTITY';
          ELSE
            current_notional :=
              current_notional
              + observation.fill_price_krw::numeric * delta_quantity::numeric;
            next_average :=
              trunc(
                current_notional / observation.cumulative_quantity::numeric
              )::bigint;
            current_filled := observation.cumulative_quantity;
            current_leaves := observation.leaves_quantity;
            current_unfilled_terminated := 0;
            current_average := next_average;
            transition_status :=
              CASE WHEN current_leaves = 0 THEN 'FILLED' ELSE 'PARTIALLY_FILLED' END;
            transition_outcome := 'APPLIED';
            transition_event_type :=
              CASE
                WHEN transition_status = 'FILLED' THEN 'MOCK_ORDER_FILLED'
                ELSE 'MOCK_ORDER_PARTIALLY_FILLED'
              END;
            transition_event_status := transition_status;
          END IF;
        END IF;
      ELSIF observation.exec_type IN ('CANCELLED', 'REJECTED') THEN
        IF observation.fill_quantity <> 0
           OR observation.fill_price_krw IS NOT NULL
           OR observation.cumulative_quantity <> current_filled
           OR observation.leaves_quantity <> 0 THEN
          transition_outcome := 'INVALID';
          transition_reason := 'INVALID_QUANTITY';
        ELSE
          current_leaves := 0;
          current_unfilled_terminated := stored_order.quantity - current_filled;
          transition_status := observation.exec_type;
          transition_outcome := 'APPLIED';
          transition_event_type :=
            CASE
              WHEN observation.exec_type = 'CANCELLED' THEN 'MOCK_ORDER_CANCELLED'
              ELSE 'MOCK_ORDER_REJECTED'
            END;
          transition_event_status := transition_status;
        END IF;
      END IF;

      INSERT INTO public.order_fill_application_receipts (
        receipt_id, observation_id, order_id, outcome, invalid_reason, applied_at
      )
      VALUES (
        'ofr_' || substr(
          encode(sha256(convert_to('receipt:' || observation.observation_id, 'UTF8')), 'hex'),
          1,
          32
        ),
        observation.observation_id,
        requested_order_id,
        transition_outcome,
        transition_reason,
        requested_reconciled_at
      );

      IF transition_outcome IN ('APPLIED', 'INVALID') THEN
        IF transition_outcome = 'INVALID' THEN
          transition_event_type := 'INVALID_TRANSITION';
          transition_event_status := NULL;
          invalid_count := invalid_count + 1;
        ELSE
          current_status := transition_status;
        END IF;
        transition_payload :=
          CASE
            WHEN transition_outcome = 'INVALID' THEN
              jsonb_build_object(
                'orderId', requested_order_id,
                'brokerageMode', 'KIS_MOCK',
                'execRefHash', observation.provider_exec_ref_hash,
                'observationId', observation.observation_id,
                'reason', transition_reason
              )
            ELSE
              jsonb_build_object(
                'orderId', requested_order_id,
                'brokerageMode', 'KIS_MOCK',
                'status', transition_event_status,
                'execRefHash', observation.provider_exec_ref_hash,
                'fillQuantity', observation.fill_quantity,
                'fillPriceKrw', observation.fill_price_krw,
                'filledAt', observation.observed_at::text
              )
          END;
        INSERT INTO public.order_events (
          order_event_id, order_id, event_type, event_status, payload_json,
          created_at, event_seq
        )
        VALUES (
          'oev_' || substr(
            encode(sha256(convert_to('fill-event:' || observation.observation_id, 'UTF8')), 'hex'),
            1,
            32
          ),
          requested_order_id,
          transition_event_type,
          transition_event_status,
          transition_payload,
          observation.observed_at,
          next_event_seq
        );
        next_event_seq := next_event_seq + 1;
        event_count := event_count + 1;
      END IF;
    END LOOP;
  END IF;

  IF stored_order.brokerage_mode = 'INTERNAL_PAPER' THEN
    SELECT
      count(*),
      COALESCE(sum((paper.payload_json ->> 'fillQuantity')::bigint), 0),
      CASE
        WHEN count(*) = 0 THEN NULL
        ELSE trunc(
          sum(
            (paper.payload_json ->> 'fillQuantity')::numeric
            * (paper.payload_json ->> 'fillPriceKrw')::numeric
          )
          / sum((paper.payload_json ->> 'fillQuantity')::numeric)
        )::bigint
      END
    INTO observation_count, observed_fill_quantity, recomputed_average
    FROM public.paper_order_events paper
    WHERE paper.order_id = requested_order_id;
    provider_final_average := recomputed_average;
  ELSE
    SELECT
      count(*),
      COALESCE(
        sum(aggregate_observation.fill_quantity)
          FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL')),
        0
      ),
      CASE
        WHEN COALESCE(
          sum(aggregate_observation.fill_quantity)
            FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL')),
          0
        ) = 0 THEN NULL
        ELSE trunc(
          sum(
            aggregate_observation.fill_quantity::numeric
            * aggregate_observation.fill_price_krw::numeric
          ) FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL'))
          / sum(aggregate_observation.fill_quantity::numeric)
            FILTER (WHERE aggregate_observation.exec_type IN ('PARTIAL_FILL', 'FILL'))
        )::bigint
      END
    INTO observation_count, observed_fill_quantity, recomputed_average
    FROM public.order_fill_observations aggregate_observation
    WHERE aggregate_observation.order_id = requested_order_id
      AND aggregate_observation.observed_at <= requested_reconciled_at
      AND aggregate_observation.received_at <= requested_reconciled_at;
    SELECT final_observation.average_fill_price_krw
    INTO provider_final_average
    FROM public.order_fill_observations final_observation
    WHERE final_observation.order_id = requested_order_id
      AND final_observation.exec_type IN ('PARTIAL_FILL', 'FILL')
      AND final_observation.observed_at <= requested_reconciled_at
      AND final_observation.received_at <= requested_reconciled_at
    ORDER BY
      final_observation.cumulative_quantity DESC,
      final_observation.observed_at DESC,
      final_observation.observation_id DESC
    LIMIT 1;
  END IF;

  conservation_matches :=
    current_filled + current_leaves + current_unfilled_terminated
      = stored_order.quantity;
  IF observation_count = 0 THEN
    final_reconciliation_status := 'NOT_APPLICABLE';
    final_reconciled_at := NULL;
  ELSIF observed_fill_quantity = current_filled
        AND conservation_matches
        AND recomputed_average IS NOT DISTINCT FROM current_average
        AND provider_final_average IS NOT DISTINCT FROM recomputed_average THEN
    final_reconciliation_status := 'MATCHED';
    final_reconciled_at := requested_reconciled_at;
  ELSE
    final_reconciliation_status := 'MISMATCH';
    final_reconciled_at := requested_reconciled_at;
  END IF;

  UPDATE public.orders stored
  SET status = current_status,
      filled_quantity = current_filled,
      leaves_quantity = current_leaves,
      unfilled_terminated_quantity = current_unfilled_terminated,
      average_fill_price_krw = current_average,
      reconciliation_status = final_reconciliation_status,
      reconciled_at = final_reconciled_at,
      updated_at = GREATEST(stored.updated_at, requested_reconciled_at)
  WHERE stored.order_id = requested_order_id;

  SELECT EXISTS (
    SELECT 1
    FROM public.order_fill_observations remaining_observation
    WHERE remaining_observation.order_id = requested_order_id
      AND remaining_observation.completeness = 'COMPLETE'
      AND remaining_observation.observed_at <= requested_reconciled_at
      AND remaining_observation.received_at <= requested_reconciled_at
      AND NOT EXISTS (
        SELECT 1
        FROM public.order_fill_application_receipts receipt
        WHERE receipt.observation_id = remaining_observation.observation_id
      )
  )
  INTO remaining;

  actual_final :=
    jsonb_build_object(
      'status', current_status,
      'filledQuantity', current_filled,
      'leavesQuantity', current_leaves,
      'unfilledTerminatedQuantity', current_unfilled_terminated,
      'fillNotionalKrw', current_notional,
      'averageFillPriceKrw', current_average,
      'reconciliationStatus', final_reconciliation_status,
      'appliedEventCount', event_count,
      'hasMore', remaining
    );
  IF actual_final <> requested_expected_final THEN
    RAISE EXCEPTION 'S3.3 reconciliation logic divergence'
      USING ERRCODE = 'P0001';
  END IF;

  reference_payload :=
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', stored_order.brokerage_mode,
      'reconciliationStatus', final_reconciliation_status,
      'appliedEventCount', event_count,
      'invalidEventCount', invalid_count,
      'hasMore', remaining,
      'checkedAt', final_reconciled_at
    );
  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    'ORDER_RECONCILED', 'ORDER_RECONCILIATION', requested_order_id,
    requested_request_id, reference_payload, requested_reconciled_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id, 'brokerage.order-reconciled.v1',
    'ORDER', requested_order_id, requested_order_id, reference_payload,
    '1.0.0', 'PENDING', 0, requested_reconciled_at, requested_reconciled_at
  );

  RETURN QUERY
  SELECT
    'APPLIED'::text,
    requested_order_id,
    stored_order.brokerage_mode,
    current_status,
    current_filled,
    current_leaves,
    current_unfilled_terminated,
    current_average,
    final_reconciliation_status,
    final_reconciled_at,
    event_count,
    remaining;
END
$apply_stored_order_fills$;
ALTER FUNCTION apply_stored_order_fills(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION apply_stored_order_fills(jsonb, text) FROM PUBLIC;

CREATE FUNCTION read_owned_order_fills(
  requested_payload jsonb,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  page_json text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_owned_order_fills$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_account_id text;
  requested_brokerage_mode text;
  requested_from timestamptz;
  requested_to timestamptz;
  requested_last_filled_at timestamptz;
  requested_last_order_id text;
  requested_last_exec_ref_hash text;
  stored_actor record;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'accountId',
       'brokerageMode', 'fromInclusive', 'toExclusive', 'lastFilledAt',
       'lastOrderId', 'lastExecRefHash'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'accountId',
       'brokerageMode', 'fromInclusive', 'toExclusive', 'lastFilledAt',
       'lastOrderId', 'lastExecRefHash'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;
  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_account_id := requested_payload ->> 'accountId';
  requested_brokerage_mode := requested_payload ->> 'brokerageMode';
  requested_from := (requested_payload ->> 'fromInclusive')::timestamptz;
  requested_to := (requested_payload ->> 'toExclusive')::timestamptz;
  requested_last_filled_at :=
    NULLIF(requested_payload ->> 'lastFilledAt', '')::timestamptz;
  requested_last_order_id := NULLIF(requested_payload ->> 'lastOrderId', '');
  requested_last_exec_ref_hash :=
    NULLIF(requested_payload ->> 'lastExecRefHash', '');

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> requested_actor_role
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;
  IF requested_brokerage_mode NOT IN ('KIS_MOCK', 'INTERNAL_PAPER')
     OR requested_to <= requested_from
     OR requested_to - requested_from > interval '31 days'
     OR (
       (requested_last_filled_at IS NULL)::integer
       + (requested_last_order_id IS NULL)::integer
       + (requested_last_exec_ref_hash IS NULL)::integer
     ) NOT IN (0, 3) THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  IF requested_brokerage_mode = 'KIS_MOCK'
     AND NOT EXISTS (
       SELECT 1
       FROM public.orders owned
       WHERE owned.user_id = requested_actor_user_id
         AND owned.account_id = requested_account_id
         AND owned.brokerage_mode = 'KIS_MOCK'
     ) THEN
    RETURN QUERY SELECT 'ACCOUNT_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF requested_brokerage_mode = 'INTERNAL_PAPER'
     AND NOT EXISTS (
       SELECT 1
       FROM public.paper_accounts account
       WHERE account.user_id = requested_actor_user_id
         AND account.account_id = requested_account_id
     ) THEN
    RETURN QUERY SELECT 'ACCOUNT_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;

  RETURN QUERY
  WITH candidate AS (
    SELECT source.*
    FROM (
      SELECT
        stored.order_id,
        stored.brokerage_mode,
        stored.symbol,
        stored.side,
        observation.fill_quantity,
        observation.fill_price_krw,
        observation.fill_quantity * observation.fill_price_krw AS fill_amount_krw,
        observation.observed_at AS filled_at,
        observation.provider_exec_ref_hash AS exec_ref_hash
      FROM public.orders stored
      JOIN public.order_fill_observations observation
        ON observation.order_id = stored.order_id
      JOIN public.order_fill_application_receipts receipt
        ON receipt.observation_id = observation.observation_id
       AND receipt.outcome = 'APPLIED'
      WHERE requested_brokerage_mode = 'KIS_MOCK'
        AND stored.brokerage_mode = 'KIS_MOCK'
        AND stored.user_id = requested_actor_user_id
        AND stored.account_id = requested_account_id
        AND observation.exec_type IN ('PARTIAL_FILL', 'FILL')
        AND observation.observed_at >= requested_from
        AND observation.observed_at < requested_to
      UNION ALL
      SELECT
        stored.order_id,
        stored.brokerage_mode,
        stored.symbol,
        stored.side,
        (paper.payload_json ->> 'fillQuantity')::bigint,
        (paper.payload_json ->> 'fillPriceKrw')::bigint,
        (paper.payload_json ->> 'fillAmountKrw')::bigint,
        paper.created_at,
        encode(
          sha256(convert_to('paper-fill:' || stored.order_id, 'UTF8')),
          'hex'
        )
      FROM public.orders stored
      JOIN public.paper_order_events paper ON paper.order_id = stored.order_id
      WHERE requested_brokerage_mode = 'INTERNAL_PAPER'
        AND stored.brokerage_mode = 'INTERNAL_PAPER'
        AND stored.user_id = requested_actor_user_id
        AND stored.account_id = requested_account_id
        AND paper.created_at >= requested_from
        AND paper.created_at < requested_to
    ) source
    WHERE requested_last_filled_at IS NULL
       OR (source.filled_at, source.order_id, source.exec_ref_hash)
          < (
            requested_last_filled_at,
            requested_last_order_id,
            requested_last_exec_ref_hash
          )
    ORDER BY source.filled_at DESC, source.order_id DESC, source.exec_ref_hash DESC
    LIMIT 51
  )
  SELECT
    'READY'::text,
    COALESCE(
      jsonb_agg(
        jsonb_build_object(
          'orderId', candidate.order_id,
          'brokerageMode', candidate.brokerage_mode,
          'symbol', candidate.symbol,
          'side', candidate.side,
          'fillQuantity', candidate.fill_quantity,
          'fillPriceKrw', candidate.fill_price_krw,
          'fillAmountKrw', candidate.fill_amount_krw,
          'filledAt', candidate.filled_at::text,
          'execRefHash', candidate.exec_ref_hash
        )
        ORDER BY candidate.filled_at DESC, candidate.order_id DESC, candidate.exec_ref_hash DESC
      ),
      '[]'::jsonb
    )::text
  FROM candidate;
END
$read_owned_order_fills$;
ALTER FUNCTION read_owned_order_fills(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_owned_order_fills(jsonb, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  order_fill_observations,
  order_fill_application_receipts
FROM PUBLIC;
REVOKE ALL ON FUNCTION
  initialize_order_fill_projection(),
  read_order_reconciliation_state(jsonb, text),
  acquire_order_fill_reconciliation_lock(jsonb, text),
  apply_stored_order_fills(jsonb, text),
  read_owned_order_fills(jsonb, text)
FROM PUBLIC;

DO $v14_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      order_fill_observations,
      order_fill_application_receipts
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION
      read_order_reconciliation_state(jsonb, text),
      acquire_order_fill_reconciliation_lock(jsonb, text),
      apply_stored_order_fills(jsonb, text),
      read_owned_order_fills(jsonb, text)
    TO decision_app;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_fill_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      orders,
      order_events,
      order_fill_application_receipts
    FROM decision_fill_writer;
    GRANT INSERT ON TABLE order_fill_observations TO decision_fill_writer;
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE order_fill_observations
      FROM decision_fill_writer;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
      FROM decision_fill_writer;
    REVOKE CREATE ON SCHEMA public FROM decision_fill_writer;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history
      FROM decision_fill_writer;
  END IF;
END
$v14_privileges$;
