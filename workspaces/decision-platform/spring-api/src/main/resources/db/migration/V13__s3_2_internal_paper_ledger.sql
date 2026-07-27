-- S3.2는 병합된 V1/V9/V11/V12를 수정하지 않고 INTERNAL_PAPER append-only 원장을 연다.
DO $v13_precondition$
DECLARE
  non_mock_order_count bigint;
  paper_account_count bigint;
  paper_position_count bigint;
  paper_event_count bigint;
BEGIN
  IF to_regclass('public.paper_accounts') IS NULL
     OR to_regclass('public.paper_positions') IS NULL
     OR to_regclass('public.paper_order_events') IS NULL THEN
    RAISE EXCEPTION 'S3.2 V13 precondition failed: paper ledger tables are missing';
  END IF;
  SELECT count(*) INTO non_mock_order_count
  FROM orders
  WHERE brokerage_mode <> 'KIS_MOCK';
  SELECT count(*) INTO paper_account_count FROM paper_accounts;
  SELECT count(*) INTO paper_position_count FROM paper_positions;
  SELECT count(*) INTO paper_event_count FROM paper_order_events;
  IF non_mock_order_count <> 0
     OR paper_account_count <> 0
     OR paper_position_count <> 0
     OR paper_event_count <> 0 THEN
    RAISE EXCEPTION
      'S3.2 V13 precondition failed: non_mock_orders=% paper_accounts=% paper_positions=% paper_events=%',
      non_mock_order_count,
      paper_account_count,
      paper_position_count,
      paper_event_count;
  END IF;
END
$v13_precondition$;

ALTER TABLE orders
  DROP CONSTRAINT orders_brokerage_mode_check,
  DROP CONSTRAINT orders_identity_check;
ALTER TABLE order_events
  DROP CONSTRAINT order_events_type_check,
  DROP CONSTRAINT order_events_type_status_pair_check;
ALTER TABLE audit_logs
  DROP CONSTRAINT audit_logs_brokerage_order_contract_check;
ALTER TABLE event_outbox
  DROP CONSTRAINT event_outbox_brokerage_order_contract_check;
DROP TRIGGER audit_logs_brokerage_writer_guard ON audit_logs;
DROP TRIGGER event_outbox_brokerage_writer_guard ON event_outbox;

ALTER TABLE orders
  ADD CONSTRAINT orders_brokerage_mode_check
    CHECK (brokerage_mode IN ('KIS_MOCK', 'INTERNAL_PAPER')),
  ADD CONSTRAINT orders_identity_check CHECK (
    (brokerage_mode = 'KIS_MOCK' AND order_id ~ '^ord_mock_[0-9a-f]{32}$')
    OR (
      brokerage_mode = 'INTERNAL_PAPER'
      AND order_id ~ '^ord_paper_[0-9a-f]{32}$'
    )
  );

ALTER TABLE order_events
  ADD CONSTRAINT order_events_type_check CHECK (
    event_type IN (
      'MOCK_ORDER_SUBMITTED',
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

-- 저장 quote source는 현재가가 없을 때 같은 sanitized row의 직전 종가만 허용한다.
ALTER TABLE market_quote_observations
  ALTER COLUMN price_krw DROP NOT NULL,
  ADD COLUMN previous_close_krw bigint;
ALTER TABLE market_quote_observations
  ADD CONSTRAINT market_quote_previous_close_check
    CHECK (previous_close_krw IS NULL OR previous_close_krw > 0),
  ADD CONSTRAINT market_quote_price_source_check
    CHECK (price_krw IS NOT NULL OR previous_close_krw IS NOT NULL);
CREATE OR REPLACE VIEW latest_market_quote_observations
WITH (security_barrier = true)
AS
SELECT DISTINCT ON (symbol)
  observation_id,
  symbol,
  source,
  price_krw,
  bid_krw,
  ask_krw,
  completeness,
  observed_at,
  received_at,
  schema_version,
  source_version,
  source_ref,
  artifact_hash,
  previous_close_krw
FROM market_quote_observations
ORDER BY symbol, observed_at DESC, received_at DESC, observation_id;

ALTER TABLE paper_accounts
  ADD COLUMN owner_scope_hash text NOT NULL,
  -- 값이 없는 기존/미완성 source는 계속 HOLD이며 0을 합성하지 않는다.
  ADD COLUMN margin_requirement_krw bigint;
ALTER TABLE paper_accounts
  ADD CONSTRAINT paper_accounts_identity_check
    CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  ADD CONSTRAINT paper_accounts_owner_scope_check
    CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT paper_accounts_cash_check CHECK (cash_balance >= 0),
  ADD CONSTRAINT paper_accounts_margin_check
    CHECK (margin_requirement_krw IS NULL OR margin_requirement_krw >= 0),
  ADD CONSTRAINT paper_accounts_currency_check CHECK (currency = 'KRW'),
  ADD CONSTRAINT paper_accounts_owner_scope_unique UNIQUE (owner_scope_hash);

ALTER TABLE paper_positions
  ADD CONSTRAINT paper_positions_quantity_check CHECK (quantity >= 0),
  ADD CONSTRAINT paper_positions_average_price_check CHECK (
    average_price >= 0
    AND (
      (quantity = 0 AND average_price = 0)
      OR (quantity > 0 AND average_price > 0)
    )
  ),
  ADD CONSTRAINT paper_positions_market_value_check CHECK (market_value >= 0);

ALTER TABLE paper_order_events
  ADD COLUMN event_seq integer,
  ALTER COLUMN order_id SET NOT NULL,
  ALTER COLUMN event_seq SET NOT NULL;
ALTER TABLE paper_order_events
  ADD CONSTRAINT paper_order_events_sequence_check CHECK (event_seq > 0),
  ADD CONSTRAINT paper_order_events_type_check CHECK (event_type = 'PAPER_ORDER_FILLED'),
  ADD CONSTRAINT paper_order_events_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 65536
    AND payload_json ?& ARRAY[
      'orderId',
      'symbol',
      'side',
      'fillQuantity',
      'fillPriceKrw',
      'fillAmountKrw',
      'priceBasis',
      'slippageBps',
      'feeModel',
      'observedAt',
      'beforeCashKrw',
      'afterCashKrw',
      'beforeQuantity',
      'afterQuantity',
      'beforeAveragePriceKrw',
      'afterAveragePriceKrw',
      'beforeMarketValueKrw',
      'afterMarketValueKrw'
    ]
    AND payload_json - ARRAY[
      'orderId',
      'symbol',
      'side',
      'fillQuantity',
      'fillPriceKrw',
      'fillAmountKrw',
      'priceBasis',
      'slippageBps',
      'feeModel',
      'observedAt',
      'beforeCashKrw',
      'afterCashKrw',
      'beforeQuantity',
      'afterQuantity',
      'beforeAveragePriceKrw',
      'afterAveragePriceKrw',
      'beforeMarketValueKrw',
      'afterMarketValueKrw'
    ] = '{}'::jsonb
    AND payload_json ->> 'orderId' = order_id
    AND payload_json ->> 'side' IN ('BUY', 'SELL')
    AND (payload_json ->> 'fillQuantity')::bigint > 0
    AND (payload_json ->> 'fillPriceKrw')::bigint > 0
    AND (payload_json ->> 'fillAmountKrw')::bigint > 0
    AND payload_json ->> 'priceBasis' IN ('LAST_QUOTE', 'PREVIOUS_CLOSE')
    AND (payload_json ->> 'slippageBps')::integer BETWEEN 0 AND 100
    AND payload_json ->> 'feeModel' = 'NONE_V1'
  ),
  ADD CONSTRAINT paper_order_events_account_sequence_unique
    UNIQUE (account_id, event_seq),
  ADD CONSTRAINT paper_order_events_order_unique UNIQUE (order_id);
CREATE INDEX paper_order_events_account_latest_idx
  ON paper_order_events (account_id, event_seq DESC);

ALTER TABLE paper_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE paper_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE paper_order_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE paper_order_events FORCE ROW LEVEL SECURITY;

CREATE POLICY paper_accounts_definer_select_policy
  ON paper_accounts FOR SELECT TO flyway USING (true);
CREATE POLICY paper_accounts_definer_insert_policy
  ON paper_accounts FOR INSERT TO flyway WITH CHECK (true);
CREATE POLICY paper_accounts_definer_update_policy
  ON paper_accounts FOR UPDATE TO flyway USING (true) WITH CHECK (true);
CREATE POLICY paper_positions_definer_select_policy
  ON paper_positions FOR SELECT TO flyway USING (true);
CREATE POLICY paper_positions_definer_insert_policy
  ON paper_positions FOR INSERT TO flyway WITH CHECK (true);
CREATE POLICY paper_positions_definer_update_policy
  ON paper_positions FOR UPDATE TO flyway USING (true) WITH CHECK (true);
CREATE POLICY paper_order_events_definer_select_policy
  ON paper_order_events FOR SELECT TO flyway USING (true);
CREATE POLICY paper_order_events_definer_insert_policy
  ON paper_order_events FOR INSERT TO flyway WITH CHECK (true);

-- active_paper_portfolio_projection은 변경하지 않고 명시적으로 저장된 margin만 별도 owner projection으로 연다.
CREATE VIEW paper_margin_owner_projection
WITH (security_barrier = true)
AS
SELECT
  account.account_id,
  account.user_id AS owner_user_id,
  account.owner_scope_hash,
  account.margin_requirement_krw,
  account.updated_at AS observed_at
FROM paper_accounts account
WHERE account.status = 'ACTIVE'
  AND account.user_id = current_setting('app.actor_user_id', true);

-- 기존 mock evidence 분기를 보존하면서 paper accepted/filled/cancelled exact key set을 추가한다.
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_brokerage_order_contract_check
  CHECK (
    target_type <> 'ORDER'
    OR (
      action = 'MOCK_ORDER_SUBMITTED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' = 'KIS_MOCK'
      AND payload_json ->> 'status' = 'SUBMITTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      action = 'MOCK_ORDER_CANCEL_REQUESTED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' = 'KIS_MOCK'
      AND payload_json ->> 'status' = 'CANCEL_REQUESTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ] = '{}'::jsonb
    )
    OR (
      action = 'PAPER_ORDER_ACCEPTED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'ACCEPTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      action = 'PAPER_ORDER_FILLED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'FILLED'
      AND payload_json ->> 'priceBasis' IN ('LAST_QUOTE', 'PREVIOUS_CLOSE')
      AND (payload_json ->> 'fillPriceKrw')::bigint > 0
      AND (payload_json ->> 'fillQuantity')::bigint > 0
      AND (payload_json ->> 'slippageBps')::integer BETWEEN 0 AND 100
      AND payload_json ->> 'feeModel' = 'NONE_V1'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash', 'fillPriceKrw', 'fillQuantity',
        'priceBasis', 'slippageBps', 'feeModel'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash', 'fillPriceKrw', 'fillQuantity',
        'priceBasis', 'slippageBps', 'feeModel'
      ] = '{}'::jsonb
    )
    OR (
      action = 'PAPER_ORDER_CANCELLED'
      AND user_id IS NOT NULL
      AND target_id = payload_json ->> 'orderId'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'CANCELLED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ] = '{}'::jsonb
    )
  );

ALTER TABLE event_outbox
  ADD CONSTRAINT event_outbox_brokerage_order_contract_check
  CHECK (
    event_type NOT IN (
      'brokerage.mock-order-submitted.v1',
      'brokerage.mock-order-cancel-requested.v1',
      'brokerage.paper-order-accepted.v1',
      'brokerage.paper-order-filled.v1',
      'brokerage.paper-order-cancelled.v1'
    )
    OR (
      event_type = 'brokerage.mock-order-submitted.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' = 'KIS_MOCK'
      AND payload_json ->> 'status' = 'SUBMITTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      event_type = 'brokerage.mock-order-cancel-requested.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' = 'KIS_MOCK'
      AND payload_json ->> 'status' = 'CANCEL_REQUESTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ] = '{}'::jsonb
    )
    OR (
      event_type = 'brokerage.paper-order-accepted.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'ACCEPTED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash'
      ] = '{}'::jsonb
    )
    OR (
      event_type = 'brokerage.paper-order-filled.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'FILLED'
      AND payload_json ->> 'priceBasis' IN ('LAST_QUOTE', 'PREVIOUS_CLOSE')
      AND (payload_json ->> 'fillPriceKrw')::bigint > 0
      AND (payload_json ->> 'fillQuantity')::bigint > 0
      AND (payload_json ->> 'slippageBps')::integer BETWEEN 0 AND 100
      AND payload_json ->> 'feeModel' = 'NONE_V1'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash', 'fillPriceKrw', 'fillQuantity',
        'priceBasis', 'slippageBps', 'feeModel'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'evaluationId', 'brokerageMode',
        'status', 'idempotencyScopeHash', 'fillPriceKrw', 'fillQuantity',
        'priceBasis', 'slippageBps', 'feeModel'
      ] = '{}'::jsonb
    )
    OR (
      event_type = 'brokerage.paper-order-cancelled.v1'
      AND aggregate_type = 'ORDER'
      AND aggregate_id = payload_json ->> 'orderId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ->> 'brokerageMode' = 'INTERNAL_PAPER'
      AND payload_json ->> 'status' = 'CANCELLED'
      AND payload_json ?& ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ]
      AND payload_json - ARRAY[
        'orderId', 'decisionId', 'brokerageMode', 'status',
        'requestedByRole', 'requestedAt'
      ] = '{}'::jsonb
    )
  );

CREATE TRIGGER audit_logs_brokerage_writer_guard
BEFORE INSERT ON audit_logs
FOR EACH ROW
WHEN (NEW.target_type = 'ORDER')
EXECUTE FUNCTION enforce_brokerage_evidence_writer();

CREATE TRIGGER event_outbox_brokerage_writer_guard
BEFORE INSERT ON event_outbox
FOR EACH ROW
WHEN (
  NEW.event_type IN (
    'brokerage.mock-order-submitted.v1',
    'brokerage.mock-order-cancel-requested.v1',
    'brokerage.paper-order-accepted.v1',
    'brokerage.paper-order-filled.v1',
    'brokerage.paper-order-cancelled.v1'
  )
)
EXECUTE FUNCTION enforce_brokerage_evidence_writer();

CREATE FUNCTION read_paper_order_context(
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
  consumed_by_order_id text,
  account_id text,
  account_status text,
  quote_observation_id text,
  quote_price_krw bigint,
  quote_previous_close_krw bigint,
  quote_completeness text,
  quote_observed_at timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_paper_order_context$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
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
    EXISTS (
      SELECT 1
      FROM public.decision_invalidations invalidation
      WHERE invalidation.decision_id = decision.decision_id
    ),
    consumed.order_id,
    account.account_id,
    account.status,
    quote.observation_id,
    quote.price_krw,
    quote.previous_close_krw,
    quote.completeness,
    quote.observed_at
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
   AND artifact.evaluation_id = decision.evaluation_id
  LEFT JOIN public.paper_accounts account
    ON account.user_id = decision.user_id
   AND account.owner_scope_hash =
     artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}'
  LEFT JOIN public.orders consumed
    ON consumed.decision_id = decision.decision_id
  LEFT JOIN LATERAL (
    SELECT
      stored.observation_id,
      stored.price_krw,
      stored.previous_close_krw,
      stored.completeness,
      stored.observed_at
    FROM public.market_quote_observations stored
    WHERE stored.symbol =
      artifact.snapshot_artifact_canonical_json::jsonb #>> '{orderIntent,symbol}'
      AND stored.source = 'KIS_MOCK'
    ORDER BY stored.observed_at DESC, stored.received_at DESC, stored.observation_id
    LIMIT 1
  ) quote ON true
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
$read_paper_order_context$;
ALTER FUNCTION read_paper_order_context(text, text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_paper_order_context(text, text, text) FROM PUBLIC;

CREATE FUNCTION find_paper_order_idempotency_result(
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
AS $find_paper_order_idempotency_result$
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
    AND stored.brokerage_mode = 'INTERNAL_PAPER'
    AND stored.created_at + interval '24 hours' > requested_now
  LIMIT 1;
END
$find_paper_order_idempotency_result$;
ALTER FUNCTION find_paper_order_idempotency_result(text, text, timestamptz, text)
  OWNER TO flyway;
REVOKE ALL ON FUNCTION
  find_paper_order_idempotency_result(text, text, timestamptz, text)
FROM PUBLIC;

CREATE FUNCTION read_paper_balance_projection(
  requested_actor_user_id text,
  requested_account_id text,
  requested_capability_token text
)
RETURNS TABLE (
  account_id text,
  cash_krw bigint,
  total_equity_krw bigint,
  positions_json jsonb,
  as_of timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_paper_balance_projection$
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  RETURN QUERY
  SELECT
    account.account_id,
    account.cash_balance,
    account.cash_balance + COALESCE(sum(position.market_value), 0)::bigint,
    COALESCE(
      jsonb_agg(
        jsonb_build_object(
          'symbol', position.symbol,
          'quantity', position.quantity,
          'marketValueKrw', position.market_value,
          'averagePriceKrw', position.average_price
        )
        ORDER BY position.symbol
      ) FILTER (WHERE position.position_id IS NOT NULL),
      '[]'::jsonb
    ),
    GREATEST(account.updated_at, COALESCE(max(position.updated_at), account.updated_at))
  FROM public.paper_accounts account
  LEFT JOIN public.paper_positions position
    ON position.account_id = account.account_id
  WHERE account.account_id = requested_account_id
    AND account.user_id = requested_actor_user_id
    AND account.status = 'ACTIVE'
    AND EXISTS (
      SELECT 1
      FROM public.users actor
      WHERE actor.user_id = requested_actor_user_id
        AND actor.status = 'ACTIVE'
    )
  GROUP BY account.account_id, account.cash_balance, account.updated_at
  LIMIT 1;
END
$read_paper_balance_projection$;
ALTER FUNCTION read_paper_balance_projection(text, text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION read_paper_balance_projection(text, text, text) FROM PUBLIC;

CREATE FUNCTION create_paper_order(
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
AS $create_paper_order$
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
  requested_quote_observation_id text;
  requested_symbol text;
  requested_side text;
  requested_order_type text;
  requested_quantity bigint;
  requested_submitted_price bigint;
  requested_result_json text;
  requested_warnings_accepted boolean;
  requested_observed_generation bigint;
  requested_status text;
  requested_fill_price bigint;
  requested_fill_amount bigint;
  requested_price_basis text;
  requested_slippage_bps integer;
  requested_fee_model text;
  requested_quote_observed_at timestamptz;
  requested_price_max_age_seconds integer;
  requested_submitted_at timestamptz;
  requested_created_at timestamptz;
  requested_order_event_id text;
  requested_paper_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_gate record;
  stored_decision record;
  stored_account record;
  stored_position record;
  stored_quote record;
  stored_request_hash text;
  stored_result_json text;
  base_price bigint;
  computed_fill_price numeric;
  computed_fill_amount numeric;
  before_cash bigint;
  after_cash bigint;
  before_quantity bigint;
  after_quantity bigint;
  before_average_price bigint;
  after_average_price bigint;
  before_market_value bigint;
  after_market_value bigint;
  next_paper_event_seq integer;
  order_event_type text;
  reference_payload jsonb;
  ledger_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'decisionId', 'orderId', 'observedKillSwitchGeneration',
       'idempotencyScopeHash', 'idempotencyOwnerScopeHash', 'requestHash',
       'accountId', 'accountScopeHash', 'quoteObservationId', 'symbol',
       'side', 'orderType', 'quantity', 'submittedPriceKrw', 'orderIntent',
       'resultCanonicalJson', 'warningsAccepted', 'status', 'fillPriceKrw',
       'fillAmountKrw', 'priceBasis', 'slippageBps', 'feeModel',
       'quoteObservedAt', 'priceMaxAgeSeconds', 'submittedAt', 'createdAt',
       'orderEventId', 'paperEventId', 'auditLogId', 'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'decisionId', 'orderId', 'observedKillSwitchGeneration',
       'idempotencyScopeHash', 'idempotencyOwnerScopeHash', 'requestHash',
       'accountId', 'accountScopeHash', 'quoteObservationId', 'symbol',
       'side', 'orderType', 'quantity', 'submittedPriceKrw', 'orderIntent',
       'resultCanonicalJson', 'warningsAccepted', 'status', 'fillPriceKrw',
       'fillAmountKrw', 'priceBasis', 'slippageBps', 'feeModel',
       'quoteObservedAt', 'priceMaxAgeSeconds', 'submittedAt', 'createdAt',
       'orderEventId', 'paperEventId', 'auditLogId', 'outboxEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  BEGIN
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
    requested_quote_observation_id := requested_payload ->> 'quoteObservationId';
    requested_symbol := requested_payload ->> 'symbol';
    requested_side := requested_payload ->> 'side';
    requested_order_type := requested_payload ->> 'orderType';
    requested_quantity := (requested_payload ->> 'quantity')::bigint;
    requested_submitted_price :=
      CASE WHEN jsonb_typeof(requested_payload -> 'submittedPriceKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'submittedPriceKrw')::bigint END;
    requested_result_json := requested_payload ->> 'resultCanonicalJson';
    requested_warnings_accepted := (requested_payload ->> 'warningsAccepted')::boolean;
    requested_status := requested_payload ->> 'status';
    requested_fill_price :=
      CASE WHEN jsonb_typeof(requested_payload -> 'fillPriceKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'fillPriceKrw')::bigint END;
    requested_fill_amount :=
      CASE WHEN jsonb_typeof(requested_payload -> 'fillAmountKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'fillAmountKrw')::bigint END;
    requested_price_basis :=
      CASE WHEN jsonb_typeof(requested_payload -> 'priceBasis') = 'null'
        THEN NULL ELSE requested_payload ->> 'priceBasis' END;
    requested_slippage_bps := (requested_payload ->> 'slippageBps')::integer;
    requested_fee_model :=
      CASE WHEN jsonb_typeof(requested_payload -> 'feeModel') = 'null'
        THEN NULL ELSE requested_payload ->> 'feeModel' END;
    requested_quote_observed_at := (requested_payload ->> 'quoteObservedAt')::timestamptz;
    requested_price_max_age_seconds := (requested_payload ->> 'priceMaxAgeSeconds')::integer;
    requested_submitted_at := (requested_payload ->> 'submittedAt')::timestamptz;
    requested_created_at := (requested_payload ->> 'createdAt')::timestamptz;
    requested_order_event_id := requested_payload ->> 'orderEventId';
    requested_paper_event_id :=
      CASE WHEN jsonb_typeof(requested_payload -> 'paperEventId') = 'null'
        THEN NULL ELSE requested_payload ->> 'paperEventId' END;
    requested_audit_log_id := requested_payload ->> 'auditLogId';
    requested_outbox_event_id := requested_payload ->> 'outboxEventId';
  EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range OR datetime_field_overflow THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
  END;

  IF requested_order_id !~ '^ord_paper_[0-9a-f]{32}$'
     OR requested_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_owner_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_request_hash !~ '^[0-9a-f]{64}$'
     OR requested_account_id !~ '^acct_[0-9a-f]{32}$'
     OR requested_account_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_side NOT IN ('BUY', 'SELL')
     OR requested_order_type NOT IN ('MARKET', 'LIMIT')
     OR requested_quantity <= 0
     OR requested_status NOT IN ('ACCEPTED', 'FILLED')
     OR requested_price_max_age_seconds NOT BETWEEN 1 AND 300
     OR jsonb_typeof(requested_payload -> 'orderIntent') <> 'object'
     OR jsonb_typeof(requested_result_json::jsonb) <> 'object' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
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
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;

  PERFORM set_config('app.actor_user_id', requested_actor_user_id, true);
  PERFORM pg_advisory_xact_lock(
    hashtextextended('paper-order:idempotency:' || requested_scope_hash, 3201)
  );
  PERFORM pg_advisory_xact_lock(
    hashtextextended('paper-order:decision:' || requested_decision_id, 3201)
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
  IF stored_gate.active OR stored_gate.generation <> requested_observed_generation THEN
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
  IF stored_decision.portfolio_source <> 'INTERNAL_PAPER'
     OR stored_decision.owner_scope_hash <> requested_account_scope_hash
     OR stored_decision.snapshot_artifact_canonical_json::jsonb -> 'orderIntent'
       <> requested_payload -> 'orderIntent' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;
  IF NOT stored_decision.valid_until > pg_catalog.clock_timestamp() THEN
    RETURN QUERY SELECT 'DECISION_EXPIRED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT
    account.account_id,
    account.user_id,
    account.owner_scope_hash,
    account.status,
    account.cash_balance,
    account.updated_at
  INTO stored_account
  FROM public.paper_accounts account
  WHERE account.account_id = requested_account_id
    AND account.user_id = requested_actor_user_id
    AND account.owner_scope_hash = requested_account_scope_hash
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'DECISION_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_account.status <> 'ACTIVE' THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;

  SELECT
    quote.observation_id,
    quote.symbol,
    quote.price_krw,
    quote.previous_close_krw,
    quote.completeness,
    quote.observed_at
  INTO stored_quote
  FROM public.market_quote_observations quote
  WHERE quote.observation_id = requested_quote_observation_id
    AND quote.symbol = requested_symbol
    AND quote.source = 'KIS_MOCK';
  IF NOT FOUND
     OR stored_quote.completeness <> 'COMPLETE'
     OR stored_quote.observed_at <> requested_quote_observed_at THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;
  IF requested_created_at > stored_quote.observed_at
       + make_interval(secs => requested_price_max_age_seconds)
     OR stored_quote.observed_at > requested_created_at THEN
    RETURN QUERY SELECT 'DATA_STALE'::text, NULL::text;
    RETURN;
  END IF;

  IF stored_quote.price_krw IS NOT NULL THEN
    base_price := stored_quote.price_krw;
    IF requested_price_basis <> 'LAST_QUOTE' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSIF stored_quote.previous_close_krw IS NOT NULL THEN
    base_price := stored_quote.previous_close_krw;
    IF requested_price_basis <> 'PREVIOUS_CLOSE' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSE
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;

  IF requested_status = 'ACCEPTED' THEN
    IF requested_order_type <> 'LIMIT'
       OR requested_fill_price IS NOT NULL
       OR requested_fill_amount IS NOT NULL
       OR requested_slippage_bps <> 0
       OR requested_fee_model IS NOT NULL
       OR requested_paper_event_id IS NOT NULL
       OR (
         requested_side = 'BUY'
         AND base_price <= requested_submitted_price
       )
       OR (
         requested_side = 'SELL'
         AND base_price >= requested_submitted_price
       ) THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSE
    IF requested_fill_price IS NULL
       OR requested_fill_amount IS NULL
       OR requested_paper_event_id IS NULL
       OR requested_fee_model <> 'NONE_V1' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
    IF requested_order_type = 'MARKET' THEN
      IF requested_slippage_bps NOT BETWEEN 0 AND 100 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      IF requested_side = 'BUY' THEN
        computed_fill_price :=
          ceil((base_price::numeric * (10000 + requested_slippage_bps)) / 10000);
      ELSE
        computed_fill_price :=
          floor((base_price::numeric * (10000 - requested_slippage_bps)) / 10000);
      END IF;
    ELSE
      IF requested_slippage_bps <> 0
         OR requested_submitted_price IS NULL
         OR (
           requested_side = 'BUY'
           AND base_price > requested_submitted_price
         )
         OR (
           requested_side = 'SELL'
           AND base_price < requested_submitted_price
         ) THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      computed_fill_price :=
        CASE
          WHEN requested_side = 'BUY'
            THEN least(base_price, requested_submitted_price)
          ELSE greatest(base_price, requested_submitted_price)
        END;
    END IF;
    IF computed_fill_price <= 0 THEN
      RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
      RETURN;
    END IF;
    computed_fill_amount := requested_quantity::numeric * computed_fill_price;
    IF computed_fill_price > 9223372036854775807
       OR computed_fill_amount > 9223372036854775807
       OR requested_fill_price <> computed_fill_price::bigint
       OR requested_fill_amount <> computed_fill_amount::bigint THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  END IF;

  -- canonical response도 DB가 다시 계산한 mode/status/fill과 일치해야 replay가 신뢰 가능하다.
  IF requested_result_json::jsonb ->> 'orderId' <> requested_order_id
     OR requested_result_json::jsonb ->> 'accountId' <> requested_account_id
     OR requested_result_json::jsonb ->> 'brokerageMode' <> 'INTERNAL_PAPER'
     OR requested_result_json::jsonb ->> 'status' <> requested_status THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  IF requested_status = 'FILLED' THEN
    SELECT
      position.position_id,
      position.quantity,
      position.average_price,
      position.market_value
    INTO stored_position
    FROM public.paper_positions position
    WHERE position.account_id = requested_account_id
      AND position.symbol = requested_symbol
    FOR UPDATE;
    before_cash := stored_account.cash_balance;
    before_quantity := COALESCE(stored_position.quantity, 0);
    before_average_price := COALESCE(stored_position.average_price, 0);
    before_market_value := COALESCE(stored_position.market_value, 0);
    IF requested_side = 'BUY' THEN
      IF before_cash < requested_fill_amount THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_cash := before_cash - requested_fill_amount;
      after_quantity := before_quantity + requested_quantity;
      IF after_quantity < before_quantity THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      computed_fill_amount :=
        before_quantity::numeric * before_average_price
        + requested_quantity::numeric * requested_fill_price;
      IF computed_fill_amount > 9223372036854775807 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_average_price := floor(computed_fill_amount / after_quantity)::bigint;
    ELSE
      IF before_quantity < requested_quantity THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      IF before_cash::numeric + requested_fill_amount > 9223372036854775807 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_cash := before_cash + requested_fill_amount;
      after_quantity := before_quantity - requested_quantity;
      after_average_price :=
        CASE WHEN after_quantity = 0 THEN 0 ELSE before_average_price END;
    END IF;
    computed_fill_amount := after_quantity::numeric * requested_fill_price;
    IF computed_fill_amount > 9223372036854775807 THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
    after_market_value := computed_fill_amount::bigint;

    UPDATE public.paper_accounts
    SET cash_balance = after_cash,
        updated_at = requested_created_at
    WHERE account_id = requested_account_id;
    IF stored_position.position_id IS NULL THEN
      INSERT INTO public.paper_positions (
        position_id, account_id, symbol, quantity, average_price,
        market_value, updated_at
      )
      VALUES (
        'ppos_' || substr(encode(sha256(convert_to(
          requested_account_id || ':' || requested_symbol, 'UTF8'
        )), 'hex'), 1, 32),
        requested_account_id,
        requested_symbol,
        after_quantity,
        after_average_price,
        after_market_value,
        requested_created_at
      );
    ELSE
      UPDATE public.paper_positions
      SET quantity = after_quantity,
          average_price = after_average_price,
          market_value = after_market_value,
          updated_at = requested_created_at
      WHERE position_id = stored_position.position_id;
    END IF;
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
    stored_decision.evaluation_id, 'INTERNAL_PAPER', requested_scope_hash,
    requested_owner_scope_hash, requested_request_hash, requested_symbol,
    requested_side, requested_order_type, requested_quantity,
    requested_submitted_price, requested_status, requested_payload -> 'orderIntent',
    requested_result_json, requested_actor_user_id, requested_created_at,
    requested_submitted_at, requested_created_at, requested_created_at
  );

  order_event_type :=
    CASE WHEN requested_status = 'FILLED'
      THEN 'PAPER_ORDER_FILLED' ELSE 'PAPER_ORDER_ACCEPTED' END;
  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id,
    requested_order_id,
    order_event_type,
    requested_status,
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', 'INTERNAL_PAPER',
      'status', requested_status
    ),
    requested_created_at,
    1
  );

  IF requested_status = 'FILLED' THEN
    SELECT COALESCE(max(event.event_seq), 0) + 1
    INTO next_paper_event_seq
    FROM public.paper_order_events event
    WHERE event.account_id = requested_account_id;
    ledger_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'symbol', requested_symbol,
        'side', requested_side,
        'fillQuantity', requested_quantity,
        'fillPriceKrw', requested_fill_price,
        'fillAmountKrw', requested_fill_amount,
        'priceBasis', requested_price_basis,
        'slippageBps', requested_slippage_bps,
        'feeModel', requested_fee_model,
        'observedAt', requested_quote_observed_at::text,
        'beforeCashKrw', before_cash,
        'afterCashKrw', after_cash,
        'beforeQuantity', before_quantity,
        'afterQuantity', after_quantity,
        'beforeAveragePriceKrw', before_average_price,
        'afterAveragePriceKrw', after_average_price,
        'beforeMarketValueKrw', before_market_value,
        'afterMarketValueKrw', after_market_value
      );
    INSERT INTO public.paper_order_events (
      paper_order_event_id, account_id, order_id, event_type, payload_json,
      created_at, event_seq
    )
    VALUES (
      requested_paper_event_id, requested_account_id, requested_order_id,
      'PAPER_ORDER_FILLED', ledger_payload, requested_created_at,
      next_paper_event_seq
    );
    reference_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'decisionId', requested_decision_id,
        'evaluationId', stored_decision.evaluation_id,
        'brokerageMode', 'INTERNAL_PAPER',
        'status', 'FILLED',
        'idempotencyScopeHash', requested_scope_hash,
        'fillPriceKrw', requested_fill_price,
        'fillQuantity', requested_quantity,
        'priceBasis', requested_price_basis,
        'slippageBps', requested_slippage_bps,
        'feeModel', requested_fee_model
      );
  ELSE
    reference_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'decisionId', requested_decision_id,
        'evaluationId', stored_decision.evaluation_id,
        'brokerageMode', 'INTERNAL_PAPER',
        'status', 'ACCEPTED',
        'idempotencyScopeHash', requested_scope_hash
      );
  END IF;

  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    CASE WHEN requested_status = 'FILLED'
      THEN 'PAPER_ORDER_FILLED' ELSE 'PAPER_ORDER_ACCEPTED' END,
    'ORDER', requested_order_id, requested_request_id, reference_payload,
    requested_created_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id,
    CASE WHEN requested_status = 'FILLED'
      THEN 'brokerage.paper-order-filled.v1'
      ELSE 'brokerage.paper-order-accepted.v1' END,
    'ORDER', requested_order_id, requested_order_id, reference_payload,
    '1.0.0', 'PENDING', 0, requested_created_at, requested_created_at
  );
  RETURN QUERY SELECT 'CREATED'::text, requested_result_json;
EXCEPTION
  WHEN unique_violation THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
END
$create_paper_order$;
ALTER FUNCTION create_paper_order(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION create_paper_order(jsonb, text) FROM PUBLIC;

CREATE FUNCTION rebuild_paper_state(
  requested_account_id text,
  requested_capability_token text
)
RETURNS TABLE (
  operation_outcome text,
  event_count bigint,
  rebuilt_cash_krw bigint,
  stored_cash_krw bigint,
  positions_match boolean
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $rebuild_paper_state$
DECLARE
  stored_cash bigint;
  count_events bigint;
  final_cash bigint;
  cash_chain_matches boolean;
  position_chain_matches boolean;
  final_positions_match boolean;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  SELECT account.cash_balance
  INTO stored_cash
  FROM public.paper_accounts account
  WHERE account.account_id = requested_account_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'NOT_FOUND'::text, 0::bigint, NULL::bigint, NULL::bigint, false;
    RETURN;
  END IF;
  SELECT count(*), (array_agg(
    (event.payload_json ->> 'afterCashKrw')::bigint
    ORDER BY event.event_seq DESC
  ))[1]
  INTO count_events, final_cash
  FROM public.paper_order_events event
  WHERE event.account_id = requested_account_id;
  IF count_events = 0 THEN
    RETURN QUERY SELECT 'NO_EVENTS'::text, 0::bigint, NULL::bigint, stored_cash, false;
    RETURN;
  END IF;

  SELECT bool_and(
    previous_after IS NULL
    OR previous_after = before_cash
  )
  INTO cash_chain_matches
  FROM (
    SELECT
      (event.payload_json ->> 'beforeCashKrw')::bigint AS before_cash,
      lag((event.payload_json ->> 'afterCashKrw')::bigint)
        OVER (ORDER BY event.event_seq) AS previous_after
    FROM public.paper_order_events event
    WHERE event.account_id = requested_account_id
  ) chain;

  SELECT bool_and(
    previous_quantity IS NULL
    OR (
      previous_quantity = before_quantity
      AND previous_average = before_average
      AND previous_market_value = before_market_value
    )
  )
  INTO position_chain_matches
  FROM (
    SELECT
      (event.payload_json ->> 'beforeQuantity')::bigint AS before_quantity,
      (event.payload_json ->> 'beforeAveragePriceKrw')::bigint AS before_average,
      (event.payload_json ->> 'beforeMarketValueKrw')::bigint AS before_market_value,
      lag((event.payload_json ->> 'afterQuantity')::bigint)
        OVER (PARTITION BY event.payload_json ->> 'symbol' ORDER BY event.event_seq)
        AS previous_quantity,
      lag((event.payload_json ->> 'afterAveragePriceKrw')::bigint)
        OVER (PARTITION BY event.payload_json ->> 'symbol' ORDER BY event.event_seq)
        AS previous_average,
      lag((event.payload_json ->> 'afterMarketValueKrw')::bigint)
        OVER (PARTITION BY event.payload_json ->> 'symbol' ORDER BY event.event_seq)
        AS previous_market_value
    FROM public.paper_order_events event
    WHERE event.account_id = requested_account_id
  ) chain;

  WITH ranked AS (
    SELECT
      event.payload_json ->> 'symbol' AS symbol,
      (event.payload_json ->> 'afterQuantity')::bigint AS quantity,
      (event.payload_json ->> 'afterAveragePriceKrw')::bigint AS average_price,
      (event.payload_json ->> 'afterMarketValueKrw')::bigint AS market_value,
      row_number() OVER (
        PARTITION BY event.payload_json ->> 'symbol'
        ORDER BY event.event_seq DESC
      ) AS rank
    FROM public.paper_order_events event
    WHERE event.account_id = requested_account_id
  ),
  rebuilt AS (
    SELECT symbol, quantity, average_price, market_value
    FROM ranked
    WHERE rank = 1
  )
  SELECT
    NOT EXISTS (
      SELECT symbol, quantity, average_price, market_value
      FROM rebuilt
      EXCEPT
      SELECT position.symbol, position.quantity, position.average_price, position.market_value
      FROM public.paper_positions position
      WHERE position.account_id = requested_account_id
    )
    AND NOT EXISTS (
      SELECT position.symbol, position.quantity, position.average_price, position.market_value
      FROM public.paper_positions position
      WHERE position.account_id = requested_account_id
      EXCEPT
      SELECT symbol, quantity, average_price, market_value
      FROM rebuilt
    )
  INTO final_positions_match;

  RETURN QUERY
  SELECT
    CASE
      WHEN final_cash = stored_cash
       AND cash_chain_matches
       AND position_chain_matches
       AND final_positions_match
      THEN 'MATCHED'
      ELSE 'MISMATCH'
    END,
    count_events,
    final_cash,
    stored_cash,
    final_positions_match AND position_chain_matches;
END
$rebuild_paper_state$;
ALTER FUNCTION rebuild_paper_state(text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION rebuild_paper_state(text, text) FROM PUBLIC;

-- 공통 cancel route는 mock 요청을 그대로 보존하고 paper ACCEPTED는 즉시 CANCELLED로 닫는다.
CREATE OR REPLACE FUNCTION request_mock_order_cancel(
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
AS $request_brokerage_order_cancel$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_order_id text;
  requested_cancelled_at timestamptz;
  requested_order_event_id text;
  terminal_order_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_order record;
  current_status text;
  next_event_seq integer;
  target_status text;
  target_event_type text;
  target_action text;
  target_outbox_type text;
  reference_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'orderId', 'cancelledAt', 'orderEventId', 'auditLogId', 'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'orderId', 'cancelledAt', 'orderEventId', 'auditLogId', 'outboxEventId'
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
  terminal_order_event_id := requested_order_event_id;
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
    hashtextextended('brokerage-order:cancel:' || requested_order_id, 3201)
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

  IF stored_order.brokerage_mode = 'INTERNAL_PAPER' THEN
    IF current_status <> 'ACCEPTED' THEN
      RETURN QUERY
      SELECT 'ORDER_CONFLICT'::text, NULL::text, NULL::text, NULL::text,
             NULL::text, NULL::timestamptz, NULL::text;
      RETURN;
    END IF;
    target_status := 'CANCELLED';
    target_event_type := 'PAPER_ORDER_CANCELLED';
    target_action := 'PAPER_ORDER_CANCELLED';
    target_outbox_type := 'brokerage.paper-order-cancelled.v1';
  ELSE
    IF current_status NOT IN ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED') THEN
      RETURN QUERY
      SELECT 'ORDER_CONFLICT'::text, NULL::text, NULL::text, NULL::text,
             NULL::text, NULL::timestamptz, NULL::text;
      RETURN;
    END IF;
    target_status := 'CANCEL_REQUESTED';
    target_event_type := 'MOCK_ORDER_CANCEL_REQUESTED';
    target_action := 'MOCK_ORDER_CANCEL_REQUESTED';
    target_outbox_type := 'brokerage.mock-order-cancel-requested.v1';
  END IF;

  SELECT COALESCE(max(event.event_seq), 0) + 1
  INTO next_event_seq
  FROM public.order_events event
  WHERE event.order_id = requested_order_id;

  IF stored_order.brokerage_mode = 'INTERNAL_PAPER' THEN
    INSERT INTO public.order_events (
      order_event_id, order_id, event_type, event_status, payload_json,
      created_at, event_seq
    )
    VALUES (
      requested_order_event_id, requested_order_id,
      'PAPER_ORDER_CANCEL_REQUESTED', 'CANCEL_REQUESTED',
      jsonb_build_object(
        'orderId', requested_order_id,
        'brokerageMode', stored_order.brokerage_mode,
        'status', 'CANCEL_REQUESTED'
      ),
      requested_cancelled_at,
      next_event_seq
    );
    next_event_seq := next_event_seq + 1;
    -- 단일 cancel 입력에서도 두 append-only 이벤트가 충돌하지 않도록 확정 ID를 결정적으로 분리한다.
    terminal_order_event_id :=
      'oev_' || substr(
        encode(
          sha256(convert_to(requested_order_event_id || ':paper-cancelled', 'UTF8')),
          'hex'
        ),
        1,
        32
      );
  END IF;

  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    terminal_order_event_id, requested_order_id, target_event_type,
    target_status,
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', stored_order.brokerage_mode,
      'status', target_status
    ),
    requested_cancelled_at,
    next_event_seq
  );
  reference_payload :=
    jsonb_build_object(
      'orderId', requested_order_id,
      'decisionId', stored_order.decision_id,
      'brokerageMode', stored_order.brokerage_mode,
      'status', target_status,
      'requestedByRole', requested_actor_role,
      'requestedAt', requested_cancelled_at::text
    );
  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    target_action, 'ORDER', requested_order_id, requested_request_id,
    reference_payload, requested_cancelled_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id, target_outbox_type, 'ORDER',
    requested_order_id, requested_order_id, reference_payload,
    '1.0.0', 'PENDING', 0, requested_cancelled_at, requested_cancelled_at
  );
  RETURN QUERY
  SELECT
    target_status,
    stored_order.order_id,
    stored_order.account_id,
    stored_order.brokerage_mode,
    target_status,
    stored_order.submitted_at,
    stored_order.decision_id;
END
$request_brokerage_order_cancel$;
ALTER FUNCTION request_mock_order_cancel(jsonb, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION request_mock_order_cancel(jsonb, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  paper_accounts,
  paper_positions,
  paper_order_events,
  paper_margin_owner_projection,
  market_quote_observations,
  latest_market_quote_observations
FROM PUBLIC;
REVOKE ALL ON FUNCTION
  read_paper_order_context(text, text, text),
  find_paper_order_idempotency_result(text, text, timestamptz, text),
  read_paper_balance_projection(text, text, text),
  create_paper_order(jsonb, text),
  rebuild_paper_state(text, text)
FROM PUBLIC;

DO $v13_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      paper_accounts,
      paper_positions,
      paper_order_events,
      paper_margin_owner_projection,
      market_quote_observations,
      latest_market_quote_observations
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION
      read_paper_order_context(text, text, text),
      find_paper_order_idempotency_result(text, text, timestamptz, text),
      read_paper_balance_projection(text, text, text),
      create_paper_order(jsonb, text)
    TO decision_app;
    -- S2.3 evaluator의 기존 bounded quote read는 유지하고 base table 접근만 닫는다.
    GRANT SELECT ON TABLE
      latest_market_quote_observations,
      paper_margin_owner_projection
    TO decision_app;
    REVOKE ALL ON FUNCTION rebuild_paper_state(text, text) FROM decision_app;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
    GRANT INSERT ON TABLE market_quote_observations TO decision_market_writer;
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE market_quote_observations
      FROM decision_market_writer;
  END IF;
END
$v13_privileges$;
