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
    CHECK (created_at >= evaluation_as_of AND valid_until > evaluation_as_of),
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
  scope_hash text PRIMARY KEY CHECK (scope_hash ~ '^[0-9a-f]{64}$'),
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
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT market_quote_time_check CHECK (received_at >= observed_at),
  CONSTRAINT market_quote_spread_check CHECK (
    bid_krw IS NULL OR ask_krw IS NULL OR bid_krw <= ask_krw
  ),
  CONSTRAINT market_quote_identity_unique
    UNIQUE (symbol, source, observed_at, artifact_hash)
);
CREATE INDEX market_quote_latest_idx
  ON market_quote_observations (symbol, observed_at DESC, received_at DESC, observation_id);

CREATE TABLE portfolio_balance_observations (
  observation_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  account_scope_hash text NOT NULL CHECK (account_scope_hash ~ '^[0-9a-f]{64}$'),
  source text NOT NULL CHECK (source = 'KIS_MOCK'),
  context_status text NOT NULL CHECK (context_status IN ('ACTIVE', 'INACTIVE')),
  cash_krw bigint NOT NULL CHECK (cash_krw >= 0),
  portfolio_equity_krw bigint NOT NULL CHECK (portfolio_equity_krw >= 0),
  completeness text NOT NULL CHECK (completeness IN ('COMPLETE', 'PARTIAL')),
  position_count integer NOT NULL CHECK (position_count BETWEEN 0 AND 1000),
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  schema_version text NOT NULL CHECK (char_length(schema_version) BETWEEN 1 AND 128),
  source_version text NOT NULL CHECK (char_length(source_version) BETWEEN 1 AND 128),
  source_ref text NOT NULL CHECK (source_ref ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT portfolio_balance_time_check CHECK (received_at >= observed_at),
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
  is_gold_etf_etn boolean NOT NULL DEFAULT false,
  PRIMARY KEY (balance_observation_id, symbol)
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
  observed_at,
  received_at,
  schema_version,
  source_version,
  source_ref,
  artifact_hash
FROM market_quote_observations
ORDER BY symbol, observed_at DESC, received_at DESC, observation_id;

CREATE VIEW latest_portfolio_balance_observations
WITH (security_barrier = true)
AS
SELECT DISTINCT ON (balance.owner_user_id, balance.account_scope_hash)
  balance.observation_id,
  balance.owner_user_id,
  balance.account_scope_hash,
  balance.source,
  balance.cash_krw,
  balance.portfolio_equity_krw,
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
FROM portfolio_balance_observations balance
WHERE
  balance.context_status = 'ACTIVE'
  AND balance.owner_user_id = current_setting('app.actor_user_id', true)
ORDER BY
  balance.owner_user_id,
  balance.account_scope_hash,
  balance.observed_at DESC,
  balance.received_at DESC,
  balance.observation_id;

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
  account.updated_at AS observed_at,
  COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'symbol', position.symbol,
          'quantity', position.quantity,
          'marketValueKrw', position.market_value,
          'isGoldEtfEtn', false
        )
        ORDER BY position.symbol
      )
      FROM paper_positions position
      WHERE position.account_id = account.account_id
    ),
    '[]'::jsonb
  ) AS positions_json
FROM paper_accounts account
WHERE
  account.status = 'ACTIVE'
  AND account.user_id = current_setting('app.actor_user_id', true);

CREATE VIEW decision_owner_projection
WITH (security_barrier = true)
AS
SELECT
  decision_id,
  evaluation_id,
  user_id AS owner_user_id,
  principle_id,
  principle_version_id,
  principle_version,
  portfolio_source,
  mode,
  outcome,
  can_submit_order,
  enforcement_action,
  created_at,
  evaluation_as_of,
  valid_until,
  result_schema_version,
  snapshot_schema_version,
  catalog_version,
  readiness_policy_version,
  mapping_versions_json,
  semantic_input_hash,
  snapshot_artifact_hash,
  result_json
FROM decisions
WHERE user_id = current_setting('app.actor_user_id', true);

CREATE VIEW decision_audit_projection
WITH (security_barrier = true)
AS
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
FROM audit_logs audit
JOIN decisions decision ON decision.decision_id = audit.target_id
WHERE
  audit.action = 'DECISION_EVALUATED'
  AND audit.target_type = 'DECISION'
  AND decision.user_id = current_setting('app.actor_user_id', true);

ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY decisions_owner_insert_policy
  ON decisions
  FOR INSERT
  TO decision_app
  WITH CHECK (user_id = current_setting('app.actor_user_id', true));

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
  portfolio_balance_observations,
  portfolio_position_observations,
  decision_owner_projection,
  decision_audit_projection,
  latest_market_quote_observations,
  latest_portfolio_balance_observations,
  active_paper_portfolio_projection
FROM PUBLIC;

DO $v9_runtime_grants$
BEGIN
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
      portfolio_balance_observations,
      portfolio_position_observations,
      decision_owner_projection,
      decision_audit_projection,
      latest_market_quote_observations,
      latest_portfolio_balance_observations,
      active_paper_portfolio_projection
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
      decision_idempotency_results,
      decision_owner_projection,
      decision_audit_projection,
      latest_market_quote_observations,
      latest_portfolio_balance_observations,
      active_paper_portfolio_projection
    TO decision_app;

    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$v9_runtime_grants$;
