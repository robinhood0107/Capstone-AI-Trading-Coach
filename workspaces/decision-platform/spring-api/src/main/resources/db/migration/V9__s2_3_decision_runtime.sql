-- S2.3은 배포된 Decision 이력이 없다는 계약 아래 legacy skeleton을 원자 판단 저장소로 전환한다.
DO $v9_precondition$
DECLARE
  decision_count bigint;
BEGIN
  SELECT count(*) INTO decision_count FROM decisions;
  IF decision_count <> 0 THEN
    RAISE EXCEPTION
      'S2.3 V9 precondition failed: decisions=%; V1 compatibility is not approved',
      decision_count;
  END IF;
END
$v9_precondition$;

DROP INDEX idx_decisions_account_created_at;
ALTER TABLE decisions
  DROP CONSTRAINT decisions_valid_until_after_created_at_check,
  DROP COLUMN account_id,
  DROP COLUMN reason_json,
  DROP COLUMN signal_snapshot_json;
ALTER TABLE decisions RENAME COLUMN decision TO outcome;
ALTER TABLE principle_versions
  ADD CONSTRAINT principle_versions_pinned_identity_unique
    UNIQUE (principle_version_id, principle_id, version);
ALTER TABLE decisions
  ADD COLUMN evaluation_id text NOT NULL,
  ADD COLUMN principle_id text NOT NULL REFERENCES principles(principle_id) ON DELETE RESTRICT,
  ADD COLUMN principle_version integer NOT NULL,
  ADD COLUMN portfolio_source text NOT NULL,
  ADD COLUMN can_submit_order boolean NOT NULL,
  ADD COLUMN enforcement_action text NOT NULL,
  ADD COLUMN evaluation_as_of timestamptz NOT NULL,
  ADD COLUMN result_schema_version text NOT NULL,
  ADD COLUMN snapshot_schema_version text NOT NULL,
  ADD COLUMN catalog_version integer NOT NULL,
  ADD COLUMN readiness_policy_version text NOT NULL,
  ADD COLUMN mapping_versions_json jsonb NOT NULL,
  ADD COLUMN semantic_input_hash text NOT NULL,
  ADD COLUMN snapshot_artifact_hash text NOT NULL,
  ADD COLUMN result_json jsonb NOT NULL;
ALTER TABLE decisions
  ADD CONSTRAINT decisions_evaluation_id_unique UNIQUE (evaluation_id),
  ADD CONSTRAINT decisions_pinned_principle_fkey
    FOREIGN KEY (principle_version_id, principle_id, principle_version)
    REFERENCES principle_versions(principle_version_id, principle_id, version)
    ON DELETE RESTRICT,
  ADD CONSTRAINT decisions_principle_version_check CHECK (principle_version > 0),
  ADD CONSTRAINT decisions_portfolio_source_check
    CHECK (portfolio_source IN ('KIS_MOCK', 'INTERNAL_PAPER')),
  ADD CONSTRAINT decisions_enforcement_contract_check
    CHECK (
      (outcome = 'ALLOW' AND can_submit_order AND enforcement_action = 'NONE')
      OR
      (
        outcome = 'WARN'
        AND can_submit_order
        AND (
          (mode = 'GUIDE' AND enforcement_action = 'ACKNOWLEDGE_WARNING')
          OR (mode = 'STRICT' AND enforcement_action = 'RECONFIRM_PRINCIPLE')
        )
      )
      OR (outcome = 'HOLD' AND NOT can_submit_order AND enforcement_action = 'RE_EVALUATE')
      OR (outcome = 'BLOCK' AND NOT can_submit_order AND enforcement_action = 'DO_NOT_SUBMIT')
    ),
  ADD CONSTRAINT decisions_evaluation_time_check
    CHECK (created_at >= evaluation_as_of),
  ADD CONSTRAINT decisions_version_text_check
    CHECK (
      char_length(result_schema_version) BETWEEN 1 AND 128
      AND char_length(snapshot_schema_version) BETWEEN 1 AND 128
      AND char_length(readiness_policy_version) BETWEEN 1 AND 128
      AND catalog_version > 0
    ),
  ADD CONSTRAINT decisions_hash_check
    CHECK (
      semantic_input_hash ~ '^[0-9a-f]{64}$'
      AND snapshot_artifact_hash ~ '^[0-9a-f]{64}$'
    ),
  ADD CONSTRAINT decisions_mapping_versions_check
    CHECK (jsonb_typeof(mapping_versions_json) = 'object'),
  ADD CONSTRAINT decisions_result_check
    CHECK (
      jsonb_typeof(result_json) = 'object'
      AND octet_length(result_json::text) <= 1048576
    );
CREATE INDEX decisions_owner_created_idx
  ON decisions (user_id, created_at DESC, decision_id);
CREATE INDEX decisions_owner_principle_idx
  ON decisions (user_id, principle_id, created_at DESC);

ALTER TABLE decision_violations
  DROP CONSTRAINT decision_violations_decision_id_fkey,
  ALTER COLUMN observed_value TYPE numeric(38,10),
  ALTER COLUMN threshold_value TYPE numeric(38,10),
  ADD COLUMN evaluation_id text NOT NULL,
  ADD COLUMN ordinal integer NOT NULL,
  ADD COLUMN public_code text;
ALTER TABLE decision_violations
  ADD CONSTRAINT decision_violations_decision_id_fkey
    FOREIGN KEY (decision_id) REFERENCES decisions(decision_id) ON DELETE RESTRICT,
  ADD CONSTRAINT decision_violations_evaluation_id_fkey
    FOREIGN KEY (evaluation_id) REFERENCES decisions(evaluation_id) ON DELETE RESTRICT,
  ADD CONSTRAINT decision_violations_order_unique UNIQUE (decision_id, ordinal),
  ADD CONSTRAINT decision_violations_bounds_check
    CHECK (
      ordinal BETWEEN 1 AND 14
      AND char_length(rule_id) BETWEEN 1 AND 128
      AND (metric IS NULL OR char_length(metric) BETWEEN 1 AND 128)
      AND (public_code IS NULL OR char_length(public_code) BETWEEN 1 AND 128)
      AND char_length(message) BETWEEN 1 AND 1024
    );

CREATE TABLE decision_artifacts (
  decision_id text PRIMARY KEY REFERENCES decisions(decision_id) ON DELETE RESTRICT,
  evaluation_id text NOT NULL UNIQUE REFERENCES decisions(evaluation_id) ON DELETE RESTRICT,
  result_canonical_json text NOT NULL,
  snapshot_artifact_canonical_json text NOT NULL,
  semantic_input_hash text NOT NULL,
  snapshot_artifact_hash text NOT NULL,
  created_at timestamptz NOT NULL,
  CONSTRAINT decision_artifacts_hash_check CHECK (
    semantic_input_hash ~ '^[0-9a-f]{64}$'
    AND snapshot_artifact_hash ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT decision_artifacts_bounds_check CHECK (
    octet_length(result_canonical_json) BETWEEN 2 AND 1048576
    AND octet_length(snapshot_artifact_canonical_json) BETWEEN 2 AND 1048576
  ),
  CONSTRAINT decision_artifacts_json_check CHECK (
    jsonb_typeof(result_canonical_json::jsonb) = 'object'
    AND jsonb_typeof(snapshot_artifact_canonical_json::jsonb) = 'object'
  )
);

CREATE TABLE decision_traces (
  trace_id text PRIMARY KEY,
  decision_id text NOT NULL REFERENCES decisions(decision_id) ON DELETE RESTRICT,
  evaluation_id text NOT NULL REFERENCES decisions(evaluation_id) ON DELETE RESTRICT,
  step integer NOT NULL CHECK (step BETWEEN 1 AND 7),
  trace_type text NOT NULL CHECK (
    trace_type IN (
      'ORDER_VALIDATED',
      'PRINCIPLE_PINNED',
      'FRESHNESS_EVALUATED',
      'RULES_EVALUATED',
      'FINDINGS_COMPOSED',
      'POLICY_APPLIED',
      'PERSISTED'
    )
  ),
  trace_json jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  CONSTRAINT decision_traces_step_unique UNIQUE (decision_id, step),
  CONSTRAINT decision_traces_payload_check CHECK (
    jsonb_typeof(trace_json) = 'object'
    AND octet_length(trace_json::text) <= 262144
  )
);

CREATE TABLE decision_idempotency_results (
  idempotency_result_id text PRIMARY KEY,
  scope_hash text NOT NULL CHECK (scope_hash ~ '^[0-9a-f]{64}$'),
  generation integer NOT NULL CHECK (generation > 0),
  request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  owner_scope_hash text NOT NULL CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  purpose_version text NOT NULL CHECK (char_length(purpose_version) BETWEEN 1 AND 128),
  decision_id text NOT NULL UNIQUE REFERENCES decisions(decision_id) ON DELETE RESTRICT,
  evaluation_id text NOT NULL UNIQUE REFERENCES decisions(evaluation_id) ON DELETE RESTRICT,
  http_status integer NOT NULL CHECK (http_status = 200),
  content_type text NOT NULL CHECK (content_type = 'application/json'),
  result_canonical_json text NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT decision_idempotency_scope_generation_unique UNIQUE (scope_hash, generation),
  CONSTRAINT decision_idempotency_result_bounds_check CHECK (
    octet_length(result_canonical_json) BETWEEN 2 AND 1048576
    AND jsonb_typeof(result_canonical_json::jsonb) = 'object'
  ),
  CONSTRAINT decision_idempotency_expiry_check CHECK (
    expires_at = created_at + interval '24 hours'
  )
);

-- S2.3은 source schema와 consumer projection만 소유하며 production row를 seed하지 않는다.
CREATE TABLE market_quote_observations (
  observation_id text PRIMARY KEY,
  symbol text NOT NULL CHECK (symbol ~ '^[0-9A-Z._:-]{1,20}$'),
  source text NOT NULL CHECK (source = 'KIS_MOCK'),
  price_krw bigint NOT NULL CHECK (price_krw > 0),
  bid_krw bigint CHECK (bid_krw > 0),
  ask_krw bigint CHECK (ask_krw > 0),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT market_quote_time_check CHECK (received_at >= observed_at),
  CONSTRAINT market_quote_spread_check CHECK (
    bid_krw IS NULL OR ask_krw IS NULL OR bid_krw <= ask_krw
  ),
  CONSTRAINT market_quote_completeness_check CHECK (
    completeness = 'PARTIAL' OR (bid_krw IS NOT NULL AND ask_krw IS NOT NULL)
  ),
  CONSTRAINT market_quote_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 65536
  ),
  CONSTRAINT market_quote_identity_unique
    UNIQUE (symbol, source, observed_at, artifact_hash)
);
CREATE INDEX market_quote_latest_idx
  ON market_quote_observations (symbol, observed_at DESC, received_at DESC, observation_id);

CREATE TABLE instrument_catalog_observations (
  observation_id text PRIMARY KEY,
  symbol text NOT NULL CHECK (symbol ~ '^[0-9A-Z._:-]{1,20}$'),
  is_etf_etn boolean NOT NULL,
  is_gold_etf_etn boolean NOT NULL,
  product_risk_score numeric(9,8),
  catalog_version text NOT NULL CHECK (char_length(catalog_version) BETWEEN 1 AND 128),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT instrument_catalog_time_check CHECK (received_at >= observed_at),
  CONSTRAINT instrument_catalog_classification_check CHECK (
    NOT is_gold_etf_etn OR is_etf_etn
  ),
  CONSTRAINT instrument_catalog_risk_check CHECK (
    product_risk_score IS NULL
    OR (product_risk_score >= 0 AND product_risk_score <= 1)
  ),
  CONSTRAINT instrument_catalog_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 65536
  ),
  CONSTRAINT instrument_catalog_identity_unique
    UNIQUE (symbol, catalog_version, artifact_hash)
);
CREATE INDEX instrument_catalog_latest_idx
  ON instrument_catalog_observations (
    symbol,
    observed_at DESC,
    received_at DESC,
    catalog_version DESC,
    observation_id
  );

CREATE TABLE portfolio_balance_observations (
  observation_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  account_scope_hash text NOT NULL CHECK (account_scope_hash ~ '^[0-9a-f]{64}$'),
  source text NOT NULL CHECK (source = 'KIS_MOCK'),
  context_status text NOT NULL CHECK (context_status IN ('ACTIVE', 'INACTIVE')),
  cash_krw bigint NOT NULL CHECK (cash_krw >= 0),
  portfolio_equity_krw bigint NOT NULL CHECK (portfolio_equity_krw >= 0),
  margin_requirement_krw bigint NOT NULL CHECK (margin_requirement_krw >= 0),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  position_count integer NOT NULL CHECK (position_count BETWEEN 0 AND 1000),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT portfolio_balance_time_check CHECK (received_at >= observed_at),
  CONSTRAINT portfolio_balance_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 262144
  ),
  CONSTRAINT portfolio_balance_identity_unique
    UNIQUE (owner_user_id, account_scope_hash, source, observed_at, artifact_hash)
);
CREATE INDEX portfolio_balance_latest_idx
  ON portfolio_balance_observations (
    owner_user_id,
    source,
    context_status,
    observed_at DESC,
    received_at DESC,
    observation_id
  );

CREATE TABLE portfolio_position_observations (
  balance_observation_id text NOT NULL
    REFERENCES portfolio_balance_observations(observation_id) ON DELETE RESTRICT,
  symbol text NOT NULL CHECK (symbol ~ '^[0-9A-Z._:-]{1,20}$'),
  quantity bigint NOT NULL CHECK (quantity >= 0),
  market_value_krw bigint NOT NULL CHECK (market_value_krw >= 0),
  is_gold_etf_etn boolean NOT NULL,
  PRIMARY KEY (balance_observation_id, symbol)
);

CREATE TABLE deterministic_risk_observations (
  observation_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_scope_hash text NOT NULL CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  portfolio_source text NOT NULL CHECK (portfolio_source IN ('KIS_MOCK', 'INTERNAL_PAPER')),
  daily_loss_rate numeric(19,18),
  max_drawdown numeric(19,18),
  annualized_volatility numeric(19,18),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT deterministic_risk_time_check CHECK (received_at >= observed_at),
  CONSTRAINT deterministic_risk_values_check CHECK (
    (daily_loss_rate IS NULL OR daily_loss_rate BETWEEN -1 AND 0)
    AND (max_drawdown IS NULL OR max_drawdown BETWEEN -1 AND 0)
    AND (annualized_volatility IS NULL OR annualized_volatility >= 0)
    AND (
      completeness = 'PARTIAL'
      OR (
        daily_loss_rate IS NOT NULL
        AND max_drawdown IS NOT NULL
        AND annualized_volatility IS NOT NULL
      )
    )
  ),
  CONSTRAINT deterministic_risk_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 262144
  ),
  CONSTRAINT deterministic_risk_identity_unique
    UNIQUE (owner_user_id, owner_scope_hash, portfolio_source, observed_at, artifact_hash)
);
CREATE INDEX deterministic_risk_latest_idx
  ON deterministic_risk_observations (
    owner_user_id,
    owner_scope_hash,
    portfolio_source,
    observed_at DESC,
    received_at DESC,
    observation_id
  );

CREATE TABLE daily_order_count_observations (
  observation_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_scope_hash text NOT NULL CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  portfolio_source text NOT NULL CHECK (portfolio_source IN ('KIS_MOCK', 'INTERNAL_PAPER')),
  trading_date date NOT NULL,
  order_count integer,
  covered_through timestamptz NOT NULL,
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT daily_order_count_time_check CHECK (
    received_at >= observed_at
    AND covered_through <= observed_at
  ),
  CONSTRAINT daily_order_count_value_check CHECK (
    (order_count IS NULL OR order_count BETWEEN 0 AND 1000000)
    AND (completeness = 'PARTIAL' OR order_count IS NOT NULL)
  ),
  CONSTRAINT daily_order_count_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 262144
  ),
  CONSTRAINT daily_order_count_identity_unique
    UNIQUE (
      owner_user_id,
      owner_scope_hash,
      portfolio_source,
      trading_date,
      covered_through,
      artifact_hash
    )
);
CREATE INDEX daily_order_count_latest_idx
  ON daily_order_count_observations (
    owner_user_id,
    owner_scope_hash,
    portfolio_source,
    trading_date,
    covered_through DESC,
    received_at DESC,
    observation_id
  );

CREATE TABLE corporation_registry_observations (
  observation_id text PRIMARY KEY,
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  corp_code text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
  registry_status text NOT NULL CHECK (registry_status IN ('ACTIVE', 'INACTIVE')),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  payload_json jsonb NOT NULL,
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT corporation_registry_time_check CHECK (received_at >= observed_at),
  CONSTRAINT corporation_registry_payload_check CHECK (
    jsonb_typeof(payload_json) = 'object'
    AND octet_length(payload_json::text) BETWEEN 2 AND 65536
  ),
  CONSTRAINT corporation_registry_identity_unique
    UNIQUE (symbol, corp_code, observed_at, artifact_hash)
);
CREATE INDEX corporation_registry_current_idx
  ON corporation_registry_observations (
    symbol,
    corp_code,
    observed_at DESC,
    received_at DESC,
    observation_id
  );

CREATE VIEW latest_market_quote_observations
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
  artifact_hash
FROM market_quote_observations
ORDER BY symbol, observed_at DESC, received_at DESC, observation_id;

CREATE VIEW latest_instrument_catalog_observations
WITH (security_barrier = true)
AS
SELECT DISTINCT ON (symbol)
  observation_id,
  symbol,
  is_etf_etn,
  is_gold_etf_etn,
  product_risk_score,
  catalog_version,
  observed_at,
  received_at,
  completeness,
  schema_version,
  source_version,
  source_ref,
  artifact_hash
FROM instrument_catalog_observations
ORDER BY
  symbol,
  observed_at DESC,
  received_at DESC,
  catalog_version DESC,
  observation_id;

CREATE VIEW latest_portfolio_balance_observations
WITH (security_barrier = true)
AS
WITH latest_balance AS (
  SELECT DISTINCT ON (candidate.owner_user_id, candidate.account_scope_hash)
    candidate.*
  FROM portfolio_balance_observations candidate
  WHERE candidate.owner_user_id = current_setting('app.actor_user_id', true)
  ORDER BY
    candidate.owner_user_id,
    candidate.account_scope_hash,
    candidate.observed_at DESC,
    candidate.received_at DESC,
    candidate.observation_id
)
SELECT
  balance.observation_id,
  balance.owner_user_id,
  balance.account_scope_hash,
  balance.source,
  balance.cash_krw,
  balance.portfolio_equity_krw,
  balance.margin_requirement_krw,
  balance.completeness,
  balance.position_count,
  balance.observed_at,
  balance.received_at,
  balance.schema_version,
  balance.source_version,
  balance.source_ref,
  balance.artifact_hash,
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'symbol', position.symbol,
          'quantity', position.quantity,
          'marketValueKrw', position.market_value_krw,
          'isGoldEtfEtn', position.is_gold_etf_etn
        )
        ORDER BY position.symbol
      )
      FROM portfolio_position_observations position
      WHERE position.balance_observation_id = balance.observation_id
    ),
    '[]'::jsonb
  ) AS positions_json
FROM latest_balance balance
WHERE balance.context_status = 'ACTIVE';

CREATE VIEW latest_deterministic_risk_observations
WITH (security_barrier = true)
AS
SELECT DISTINCT ON (risk.owner_user_id, risk.owner_scope_hash, risk.portfolio_source)
  risk.observation_id,
  risk.owner_user_id,
  risk.owner_scope_hash,
  risk.portfolio_source,
  risk.daily_loss_rate,
  risk.max_drawdown,
  risk.annualized_volatility,
  risk.completeness,
  risk.observed_at,
  risk.received_at,
  risk.schema_version,
  risk.source_version,
  risk.source_ref,
  risk.artifact_hash
FROM deterministic_risk_observations risk
WHERE risk.owner_user_id = current_setting('app.actor_user_id', true)
ORDER BY
  risk.owner_user_id,
  risk.owner_scope_hash,
  risk.portfolio_source,
  risk.observed_at DESC,
  risk.received_at DESC,
  risk.observation_id;

CREATE VIEW latest_daily_order_count_observations
WITH (security_barrier = true)
AS
SELECT DISTINCT ON (
  orders.owner_user_id,
  orders.owner_scope_hash,
  orders.portfolio_source,
  orders.trading_date
)
  orders.observation_id,
  orders.owner_user_id,
  orders.owner_scope_hash,
  orders.portfolio_source,
  orders.trading_date,
  orders.order_count,
  orders.covered_through,
  orders.completeness,
  orders.observed_at,
  orders.received_at,
  orders.schema_version,
  orders.source_version,
  orders.source_ref,
  orders.artifact_hash
FROM daily_order_count_observations orders
WHERE orders.owner_user_id = current_setting('app.actor_user_id', true)
ORDER BY
  orders.owner_user_id,
  orders.owner_scope_hash,
  orders.portfolio_source,
  orders.trading_date,
  orders.covered_through DESC,
  orders.received_at DESC,
  orders.observation_id;

CREATE VIEW current_corporation_registry_projection
WITH (security_barrier = true)
AS
WITH latest_registry AS (
  SELECT DISTINCT ON (candidate.symbol, candidate.corp_code)
    candidate.*
  FROM corporation_registry_observations candidate
  ORDER BY
    candidate.symbol,
    candidate.corp_code,
    candidate.observed_at DESC,
    candidate.received_at DESC,
    candidate.observation_id
)
SELECT
  registry.observation_id,
  registry.symbol,
  registry.corp_code,
  registry.observed_at,
  registry.received_at,
  registry.schema_version,
  registry.source_version,
  registry.source_ref,
  registry.artifact_hash
FROM latest_registry registry
WHERE
  registry.registry_status = 'ACTIVE'
  AND registry.completeness = 'COMPLETE';

CREATE VIEW active_paper_portfolio_projection
WITH (security_barrier = true)
AS
SELECT
  account.account_id,
  account.user_id AS owner_user_id,
  account.cash_balance AS cash_krw,
  COALESCE(
    (
      SELECT sum(position.market_value)
      FROM paper_positions position
      WHERE position.account_id = account.account_id
    ),
    0
  ) + account.cash_balance AS portfolio_equity_krw,
  NULL::bigint AS margin_requirement_krw,
  GREATEST(
    account.updated_at,
    COALESCE(
      (
        SELECT max(position.updated_at)
        FROM paper_positions position
        WHERE position.account_id = account.account_id
      ),
      account.updated_at
    )
  ) AS observed_at,
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'symbol', position.symbol,
          'quantity', position.quantity,
          'marketValueKrw', position.market_value,
          'isGoldEtfEtn',
            CASE
              WHEN instrument.completeness = 'COMPLETE'
              THEN instrument.is_gold_etf_etn
              ELSE NULL
            END
        )
        ORDER BY position.symbol
      )
      FROM paper_positions position
      LEFT JOIN latest_instrument_catalog_observations instrument
        ON instrument.symbol = position.symbol
      WHERE position.account_id = account.account_id
    ),
    '[]'::jsonb
  ) AS positions_json
FROM paper_accounts account
WHERE
  account.status = 'ACTIVE'
  AND account.user_id = current_setting('app.actor_user_id', true);

-- Python sidecar는 provider client 대신 이 allowlisted projection만 읽는다.
CREATE VIEW disclosure_event_observation_projection
WITH (security_barrier = true)
AS
WITH structured_events AS (
  SELECT
    current_event.event_id,
    current_event.symbol,
    current_event.detail ->> 'corp_code' AS corp_code,
    'OPENDART:' || (current_event.detail ->> 'endpoint_id') AS event_code,
    stored_event.source_event_key AS receipt_no,
    current_event.event_date AS occurred_on,
    current_event.detail
  FROM current_calendar_events current_event
  JOIN calendar_events stored_event
    ON stored_event.event_id = current_event.event_id
  WHERE
    stored_event.source_id = 'opendart-structured-events'
    AND current_event.event_type = 'DISCLOSURE'
    AND current_event.status <> 'CANCELLED'
    AND current_event.detail ? 'endpoint_id'

  UNION ALL

  SELECT
    current_event.event_id,
    current_event.symbol,
    active_state.corp_code,
    'OPENDART:bnkMngtPcbg' AS event_code,
    stored_event.source_event_key AS receipt_no,
    current_event.event_date AS occurred_on,
    current_event.detail
  FROM active_disclosure_risk_states active_state
  JOIN current_calendar_events current_event
    ON current_event.event_id = active_state.canonical_event_id
  JOIN calendar_events stored_event
    ON stored_event.event_id = current_event.event_id
  WHERE
    active_state.state_type = 'BANK_MANAGEMENT'
    AND stored_event.source_id = 'opendart-structured-events'
    AND current_event.status <> 'CANCELLED'
)
SELECT
  event.event_id,
  event.symbol,
  event.corp_code,
  event.event_code,
  event.receipt_no,
  event.occurred_on,
  observation.observed_at,
  observation.mapping_version AS source_mapping_version,
  source.opaque_source_ref AS source_ref,
  jsonb_strip_nulls(
    jsonb_build_object('adt_opinion', event.detail ->> 'adt_opinion')
  ) AS attributes_json
FROM structured_events event
JOIN calendar_event_sources source
  ON source.event_id = event.event_id
JOIN calendar_observations observation
  ON observation.observation_id = source.observation_id
WHERE
  event.symbol ~ '^[0-9]{6}$'
  AND event.corp_code ~ '^[0-9]{8}$'
  AND event.event_code ~ '^OPENDART:[A-Za-z0-9._:-]{1,118}$'
  AND event.receipt_no ~ '^[0-9]{14}$'
  AND source.opaque_source_ref ~ '^[0-9a-f]{64}$';

CREATE VIEW disclosure_collection_status_projection
WITH (security_barrier = true)
AS
SELECT
  source_id,
  operation,
  subject AS corp_code,
  window_from,
  window_to,
  mapping_version,
  completed,
  updated_at
FROM calendar_collection_cursors
WHERE
  source_id = 'opendart-structured-events'
  AND subject ~ '^[0-9]{8}$'
  AND operation IN (
    'piicDecsn',
    'cvbdIsDecsn',
    'lwstLg',
    'accnutAdtorNmNdAdtOpinion',
    'dfOcr',
    'ctrcvsBgrq',
    'dsRsOcr',
    'bnkMngtPcbg',
    'bnkMngtPcsp',
    'bsnSp',
    'crDecsn',
    'bdwtIsDecsn',
    'exbdIsDecsn',
    'cmpMgDecsn',
    'cmpDvDecsn',
    'cmpDvmgDecsn',
    'bsnTrfDecsn'
  );

ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY decisions_owner_select_policy
  ON decisions
  FOR SELECT
  USING (user_id = current_setting('app.actor_user_id', true));
CREATE POLICY decisions_owner_insert_policy
  ON decisions
  FOR INSERT
  TO decision_app
  WITH CHECK (user_id = current_setting('app.actor_user_id', true));

-- security_invoker view가 base SELECT를 application role에 요구하지 않도록 fixed-search-path
-- bounded definer function에서 FORCE RLS owner predicate를 먼저 적용한다.
CREATE FUNCTION read_decision_owner_projection()
RETURNS TABLE (
  decision_id text,
  evaluation_id text,
  owner_user_id text,
  principle_id text,
  principle_version_id text,
  principle_version integer,
  portfolio_source text,
  mode text,
  outcome text,
  can_submit_order boolean,
  enforcement_action text,
  created_at timestamptz,
  evaluation_as_of timestamptz,
  valid_until timestamptz,
  result_schema_version text,
  snapshot_schema_version text,
  catalog_version integer,
  readiness_policy_version text,
  mapping_versions_json jsonb,
  semantic_input_hash text,
  snapshot_artifact_hash text,
  result_json jsonb,
  result_canonical_json text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_decision_owner_projection$
  SELECT
    decision.decision_id,
    decision.evaluation_id,
    decision.user_id AS owner_user_id,
    decision.principle_id,
    decision.principle_version_id,
    decision.principle_version,
    decision.portfolio_source,
    decision.mode,
    decision.outcome,
    decision.can_submit_order,
    decision.enforcement_action,
    decision.created_at,
    decision.evaluation_as_of,
    decision.valid_until,
    decision.result_schema_version,
    decision.snapshot_schema_version,
    decision.catalog_version,
    decision.readiness_policy_version,
    decision.mapping_versions_json,
    decision.semantic_input_hash,
    decision.snapshot_artifact_hash,
    decision.result_json,
    artifact.result_canonical_json
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
  WHERE decision.user_id = current_setting('app.actor_user_id', true)
$read_decision_owner_projection$;
ALTER FUNCTION read_decision_owner_projection() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_decision_owner_projection() FROM PUBLIC;

CREATE FUNCTION read_decision_audit_projection()
RETURNS TABLE (
  audit_log_id text,
  decision_id text,
  action text,
  request_id text,
  created_at timestamptz,
  payload_json jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_decision_audit_projection$
  SELECT
    audit.audit_log_id,
    audit.target_id AS decision_id,
    audit.action,
    audit.request_id,
    audit.created_at,
    jsonb_build_object(
      'evaluationId', audit.payload_json -> 'evaluationId',
      'decisionId', audit.payload_json -> 'decisionId',
      'outcome', audit.payload_json -> 'outcome',
      'principleVersionId', audit.payload_json -> 'principleVersionId',
      'semanticInputHash', audit.payload_json -> 'semanticInputHash',
      'snapshotArtifactHash', audit.payload_json -> 'snapshotArtifactHash'
    ) AS payload_json
  FROM public.audit_logs audit
  JOIN public.decisions decision
    ON decision.decision_id = audit.target_id
  WHERE
    audit.action = 'DECISION_EVALUATED'
    AND audit.target_type = 'DECISION'
    AND decision.user_id = current_setting('app.actor_user_id', true)
$read_decision_audit_projection$;
ALTER FUNCTION read_decision_audit_projection() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_decision_audit_projection() FROM PUBLIC;

CREATE VIEW decision_owner_projection
WITH (security_barrier = true, security_invoker = true)
AS
SELECT * FROM read_decision_owner_projection();

CREATE VIEW decision_audit_projection
WITH (security_barrier = true, security_invoker = true)
AS
SELECT * FROM read_decision_audit_projection();

CREATE FUNCTION find_decision_idempotency_result(
  requested_scope_hash text,
  requested_owner_scope_hash text,
  requested_at timestamptz
)
RETURNS TABLE (
  generation integer,
  request_hash text,
  result_canonical_json text,
  expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $find_decision_idempotency_result$
  SELECT
    result.generation,
    result.request_hash,
    result.result_canonical_json,
    result.expires_at
  FROM public.decision_idempotency_results result
  WHERE
    result.scope_hash = requested_scope_hash
    AND result.owner_scope_hash = requested_owner_scope_hash
    AND result.expires_at > requested_at
  ORDER BY result.generation DESC
  LIMIT 1
$find_decision_idempotency_result$;
ALTER FUNCTION find_decision_idempotency_result(text, text, timestamptz) OWNER TO flyway;
REVOKE ALL ON FUNCTION find_decision_idempotency_result(text, text, timestamptz) FROM PUBLIC;

CREATE FUNCTION next_decision_idempotency_generation(
  requested_scope_hash text,
  requested_owner_scope_hash text
)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $next_decision_idempotency_generation$
  SELECT COALESCE(max(result.generation), 0) + 1
  FROM public.decision_idempotency_results result
  WHERE
    result.scope_hash = requested_scope_hash
    AND result.owner_scope_hash = requested_owner_scope_hash
$next_decision_idempotency_generation$;
ALTER FUNCTION next_decision_idempotency_generation(text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION next_decision_idempotency_generation(text, text) FROM PUBLIC;

ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_decision_contract_check
  CHECK (
    target_type <> 'DECISION'
    OR (
      action = 'DECISION_EVALUATED'
      AND user_id IS NOT NULL
      AND target_id IS NOT NULL
      AND payload_json ?& ARRAY[
        'evaluationId',
        'decisionId',
        'outcome',
        'principleVersionId',
        'semanticInputHash',
        'snapshotArtifactHash'
      ]
      AND payload_json - ARRAY[
        'evaluationId',
        'decisionId',
        'outcome',
        'principleVersionId',
        'semanticInputHash',
        'snapshotArtifactHash'
      ] = '{}'::jsonb
    )
  );

ALTER TABLE event_outbox
  ADD CONSTRAINT event_outbox_decision_contract_check
  CHECK (
    event_type <> 'risk.decision-created.v1'
    OR (
      aggregate_type = 'DECISION'
      AND aggregate_id = payload_json ->> 'decisionId'
      AND partition_key = aggregate_id
      AND schema_version = '1.0.0'
      AND payload_json ?& ARRAY[
        'evaluationId',
        'decisionId',
        'outcome',
        'principleVersionId',
        'semanticInputHash',
        'snapshotArtifactHash'
      ]
      AND payload_json - ARRAY[
        'evaluationId',
        'decisionId',
        'outcome',
        'principleVersionId',
        'semanticInputHash',
        'snapshotArtifactHash'
      ] = '{}'::jsonb
    )
  );

REVOKE ALL PRIVILEGES ON TABLE
  decisions,
  decision_violations,
  decision_artifacts,
  decision_traces,
  decision_idempotency_results,
  audit_logs,
  event_outbox,
  market_quote_observations,
  instrument_catalog_observations,
  portfolio_balance_observations,
  portfolio_position_observations,
  deterministic_risk_observations,
  daily_order_count_observations,
  corporation_registry_observations,
  decision_owner_projection,
  decision_audit_projection,
  latest_market_quote_observations,
  latest_instrument_catalog_observations,
  latest_portfolio_balance_observations,
  latest_deterministic_risk_observations,
  latest_daily_order_count_observations,
  current_corporation_registry_projection,
  active_paper_portfolio_projection,
  disclosure_event_observation_projection,
  disclosure_collection_status_projection
FROM PUBLIC;

-- V5의 historical default SELECT를 현재와 미래 object 모두에서 회수한다.
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
  REVOKE SELECT ON TABLES FROM decision_app;

DO $v9_runtime_grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flyway') THEN
    GRANT SELECT ON TABLE
      decisions,
      decision_artifacts,
      audit_logs,
      decision_idempotency_results
    TO flyway;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    -- V5의 과거 broad SELECT와 bootstrap 재실행 흔적을 먼저 제거하고 누적 allowlist를 복원한다.
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE
      decisions,
      decision_violations,
      decision_artifacts,
      decision_traces,
      decision_idempotency_results,
      audit_logs,
      event_outbox,
      market_quote_observations,
      instrument_catalog_observations,
      portfolio_balance_observations,
      portfolio_position_observations,
      deterministic_risk_observations,
      daily_order_count_observations,
      corporation_registry_observations,
      decision_owner_projection,
      decision_audit_projection,
      latest_market_quote_observations,
      latest_instrument_catalog_observations,
      latest_portfolio_balance_observations,
      latest_deterministic_risk_observations,
      latest_daily_order_count_observations,
      current_corporation_registry_projection,
      active_paper_portfolio_projection,
      disclosure_event_observation_projection,
      disclosure_collection_status_projection
    FROM decision_app;

    GRANT INSERT ON TABLE
      decisions,
      decision_violations,
      decision_artifacts,
      decision_traces,
      audit_logs,
      event_outbox,
      decision_idempotency_results
    TO decision_app;
    GRANT SELECT ON TABLE
      users,
      principle_presets,
      principles,
      principle_versions,
      trading_sessions,
      current_calendar_events,
      active_disclosure_risk_states,
      market_calendar
    TO decision_app;
    GRANT INSERT ON TABLE principles, principle_versions TO decision_app;
    GRANT UPDATE (title, mode, status, current_version, updated_at)
      ON TABLE principles TO decision_app;
    GRANT SELECT ON TABLE
      decision_owner_projection,
      decision_audit_projection,
      latest_market_quote_observations,
      latest_instrument_catalog_observations,
      latest_portfolio_balance_observations,
      latest_deterministic_risk_observations,
      latest_daily_order_count_observations,
      current_corporation_registry_projection,
      active_paper_portfolio_projection,
      disclosure_event_observation_projection,
      disclosure_collection_status_projection
    TO decision_app;
    GRANT EXECUTE ON FUNCTION
      read_decision_owner_projection(),
      read_decision_audit_projection(),
      find_decision_idempotency_result(text, text, timestamptz),
      next_decision_idempotency_generation(text, text)
    TO decision_app;

    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_market_writer;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_market_writer;
    GRANT INSERT ON TABLE
      market_quote_observations,
      instrument_catalog_observations
    TO decision_market_writer;
    REVOKE CREATE ON SCHEMA public FROM decision_market_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_portfolio_writer') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_portfolio_writer;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_portfolio_writer;
    GRANT INSERT ON TABLE
      portfolio_balance_observations,
      portfolio_position_observations
    TO decision_portfolio_writer;
    REVOKE CREATE ON SCHEMA public FROM decision_portfolio_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_risk_writer') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_risk_writer;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_risk_writer;
    GRANT INSERT ON TABLE
      deterministic_risk_observations,
      daily_order_count_observations
    TO decision_risk_writer;
    REVOKE CREATE ON SCHEMA public FROM decision_risk_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_collector') THEN
    REVOKE ALL PRIVILEGES ON TABLE corporation_registry_observations FROM decision_collector;
    GRANT INSERT ON TABLE corporation_registry_observations TO decision_collector;
  END IF;
END
$v9_runtime_grants$;
