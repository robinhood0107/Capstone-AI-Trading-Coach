-- S4.8B는 provider 호출 없는 synthetic/manual EOD evidence만 저장한다.
-- risk snapshot schema는 future reader 계약을 위해 고정하지만 이번 migration에는 writer 권한이 없다.
CREATE TABLE market_source_entitlements (
  logical_identity_hash text PRIMARY KEY,
  source_id text NOT NULL UNIQUE,
  source_family text NOT NULL,
  category text NOT NULL,
  activation_status text NOT NULL,
  provider_calls_allowed boolean NOT NULL,
  decision_authority text NOT NULL,
  contract_expiry timestamptz NOT NULL,
  record_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT market_source_entitlements_identity_hash_check
    CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_source_entitlements_source_id_check
    CHECK (source_id ~ '^(KIS_DISABLED_[0-9]{2}|GDELT_AGGREGATE)$'),
  CONSTRAINT market_source_entitlements_source_family_check
    CHECK (source_family IN ('KIS', 'GDELT')),
  CONSTRAINT market_source_entitlements_category_check
    CHECK (category IN ('OVERSEAS_LEAD', 'DOMESTIC_AMPLIFICATION', 'ANALYST', 'NEWS_AGGREGATE')),
  CONSTRAINT market_source_entitlements_disabled_check
    CHECK (
      activation_status = 'CANDIDATE_DISABLED'
      AND NOT provider_calls_allowed
      AND decision_authority = 'NONE'
    ),
  CONSTRAINT market_source_entitlements_record_hash_check
    CHECK (record_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_source_entitlements_payload_check
    CHECK (jsonb_typeof(payload_json) = 'object')
);

CREATE TABLE cross_market_exposure_catalog_entries (
  logical_identity_hash text PRIMARY KEY,
  symbol text NOT NULL,
  classification text NOT NULL,
  config_version text NOT NULL,
  effective_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  in_scope boolean NOT NULL,
  validation_state text NOT NULL,
  source_lineage jsonb NOT NULL,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT cross_market_exposure_identity_hash_check
    CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_exposure_symbol_check
    CHECK (symbol ~ '^[0-9A-Z._-]{1,32}$'),
  CONSTRAINT cross_market_exposure_classification_check
    CHECK (classification IN ('SEMICONDUCTOR', 'BROAD_MARKET', 'FX', 'DOMESTIC_AMPLIFICATION')),
  CONSTRAINT cross_market_exposure_time_check CHECK (effective_at <= available_at),
  CONSTRAINT cross_market_exposure_validation_check CHECK (validation_state IN ('AVAILABLE', 'UNAVAILABLE')),
  CONSTRAINT cross_market_exposure_lineage_check CHECK (jsonb_typeof(source_lineage) = 'array'),
  CONSTRAINT cross_market_exposure_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_exposure_payload_check CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX cross_market_exposure_symbol_config_idx
  ON cross_market_exposure_catalog_entries (symbol, config_version, available_at DESC, logical_identity_hash);

CREATE TABLE cross_market_observations (
  logical_identity_hash text PRIMARY KEY,
  instrument text NOT NULL,
  market text NOT NULL,
  value_type text NOT NULL,
  observed_value numeric,
  session_date date NOT NULL,
  timeframe text NOT NULL,
  status text NOT NULL,
  completeness text NOT NULL,
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  evaluated_at timestamptz NOT NULL,
  source_ref text NOT NULL REFERENCES market_source_entitlements(logical_identity_hash) ON DELETE RESTRICT,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT cross_market_observations_identity_hash_check
    CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_observations_instrument_check CHECK (instrument ~ '^[0-9A-Z._-]{1,64}$'),
  CONSTRAINT cross_market_observations_timeframe_check CHECK (timeframe = 'EOD'),
  CONSTRAINT cross_market_observations_status_check CHECK (status IN ('AVAILABLE', 'UNAVAILABLE')),
  CONSTRAINT cross_market_observations_completeness_check CHECK (completeness IN ('COMPLETE', 'PARTIAL', 'MISSING')),
  CONSTRAINT cross_market_observations_value_check
    CHECK (
      (status = 'AVAILABLE' AND completeness = 'COMPLETE' AND observed_value IS NOT NULL)
      OR (status = 'UNAVAILABLE' AND observed_value IS NULL)
    ),
  CONSTRAINT cross_market_observations_time_check
    CHECK (observed_at <= received_at AND received_at <= available_at AND available_at <= evaluated_at),
  CONSTRAINT cross_market_observations_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_observations_payload_check CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX cross_market_observations_latest_idx
  ON cross_market_observations (instrument, value_type, available_at DESC, observed_at DESC, logical_identity_hash);
CREATE INDEX cross_market_observations_history_idx
  ON cross_market_observations (instrument, value_type, session_date DESC, logical_identity_hash);

CREATE TABLE analyst_revision_evidence (
  logical_identity_hash text PRIMARY KEY,
  original_evidence_id text NOT NULL UNIQUE,
  symbol text NOT NULL,
  broker_id text NOT NULL,
  estimate_period text NOT NULL,
  contributor_count integer NOT NULL,
  buy_opinion_weight numeric NOT NULL,
  dispersion numeric NOT NULL,
  published_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  retracted boolean NOT NULL,
  supersedes_evidence_id text,
  dedupe_key_hash text NOT NULL,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT analyst_revision_identity_hash_check CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT analyst_revision_symbol_check CHECK (symbol ~ '^[0-9A-Z._-]{1,32}$'),
  CONSTRAINT analyst_revision_broker_check CHECK (broker_id ~ '^broker_[0-9a-f]{16,64}$'),
  CONSTRAINT analyst_revision_coverage_check CHECK (contributor_count BETWEEN 0 AND 1000),
  CONSTRAINT analyst_revision_buy_weight_check CHECK (buy_opinion_weight = 0),
  CONSTRAINT analyst_revision_dispersion_check CHECK (dispersion >= 0),
  CONSTRAINT analyst_revision_time_check CHECK (published_at <= received_at AND received_at <= available_at),
  CONSTRAINT analyst_revision_dedupe_hash_check CHECK (dedupe_key_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT analyst_revision_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT analyst_revision_payload_check CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX analyst_revision_latest_idx
  ON analyst_revision_evidence (symbol, broker_id, estimate_period, available_at DESC, logical_identity_hash);

CREATE TABLE market_cause_evidence (
  logical_identity_hash text PRIMARY KEY,
  source_family text NOT NULL,
  source_lineage_hash text NOT NULL,
  dedupe_key_hash text NOT NULL,
  classification text NOT NULL,
  relation text NOT NULL,
  counterargument boolean NOT NULL,
  retracted boolean NOT NULL,
  supersedes_evidence_id text,
  occurred_at timestamptz NOT NULL,
  published_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  sanitized_summary text NOT NULL,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT market_cause_identity_hash_check CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_cause_lineage_hash_check CHECK (source_lineage_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_cause_dedupe_hash_check CHECK (dedupe_key_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_cause_classification_check
    CHECK (classification IN ('CONFIRMED_FACT', 'REPORTED_CLAIM', 'MARKET_INTERPRETATION', 'HYPOTHESIS')),
  CONSTRAINT market_cause_relation_check
    CHECK (relation IN ('PRECEDES', 'CO_MOVES_WITH', 'REPORTED_AS_CAUSE', 'CORROBORATES', 'CONTRADICTS')),
  CONSTRAINT market_cause_gdelt_authority_check
    CHECK (
      source_family <> 'GDELT_AGGREGATE'
      OR (classification IN ('REPORTED_CLAIM', 'MARKET_INTERPRETATION', 'HYPOTHESIS') AND relation <> 'REPORTED_AS_CAUSE')
    ),
  CONSTRAINT market_cause_time_check
    CHECK (occurred_at <= published_at AND published_at <= received_at AND received_at <= available_at),
  CONSTRAINT market_cause_summary_check CHECK (length(sanitized_summary) BETWEEN 1 AND 1000),
  CONSTRAINT market_cause_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT market_cause_payload_check CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX market_cause_latest_idx
  ON market_cause_evidence (source_family, dedupe_key_hash, available_at DESC, logical_identity_hash);

CREATE TABLE cross_market_risk_snapshots (
  logical_identity_hash text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_scope_hash text NOT NULL,
  config_version text NOT NULL,
  availability text NOT NULL,
  evidence_mode text NOT NULL,
  snapshot_available_at timestamptz NOT NULL,
  decision_authority text NOT NULL,
  order_authority text NOT NULL,
  validation_status text NOT NULL,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT cross_market_risk_snapshot_identity_hash_check CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_risk_snapshot_owner_hash_check CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_risk_snapshot_availability_check CHECK (availability IN ('AVAILABLE', 'UNAVAILABLE')),
  CONSTRAINT cross_market_risk_snapshot_evidence_mode_check
    CHECK (evidence_mode IN ('SYNTHETIC_FIXTURE', 'MANUAL_EOD', 'STORED_SNAPSHOT')),
  CONSTRAINT cross_market_risk_snapshot_authority_check
    CHECK (decision_authority IN ('NONE', 'NEW_BUY_ALLOW_TO_WARN_ONLY') AND order_authority = 'NONE'),
  CONSTRAINT cross_market_risk_snapshot_validation_check CHECK (validation_status IN ('UNVALIDATED', 'VALIDATED')),
  CONSTRAINT cross_market_risk_snapshot_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_risk_snapshot_payload_check CHECK (jsonb_typeof(payload_json) = 'object')
);
CREATE INDEX cross_market_risk_snapshots_latest_idx
  ON cross_market_risk_snapshots (
    owner_user_id,
    owner_scope_hash,
    config_version,
    snapshot_available_at DESC,
    logical_identity_hash
  );

CREATE TABLE cross_market_snapshot_evidence_links (
  snapshot_logical_identity_hash text NOT NULL
    REFERENCES cross_market_risk_snapshots(logical_identity_hash) ON DELETE RESTRICT,
  evidence_logical_identity_hash text NOT NULL,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_scope_hash text NOT NULL,
  evidence_kind text NOT NULL,
  ordinal integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT cross_market_snapshot_evidence_links_pkey
    PRIMARY KEY (snapshot_logical_identity_hash, evidence_logical_identity_hash),
  CONSTRAINT cross_market_snapshot_evidence_owner_hash_check CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_snapshot_evidence_hash_check CHECK (evidence_logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT cross_market_snapshot_evidence_kind_check
    CHECK (evidence_kind IN ('OBSERVATION', 'ANALYST_REVISION', 'MARKET_CAUSE')),
  CONSTRAINT cross_market_snapshot_evidence_ordinal_check CHECK (ordinal BETWEEN 1 AND 64),
  CONSTRAINT cross_market_snapshot_evidence_ordinal_unique
    UNIQUE (snapshot_logical_identity_hash, ordinal)
);

ALTER TABLE market_source_entitlements OWNER TO flyway;
ALTER TABLE cross_market_exposure_catalog_entries OWNER TO flyway;
ALTER TABLE cross_market_observations OWNER TO flyway;
ALTER TABLE analyst_revision_evidence OWNER TO flyway;
ALTER TABLE market_cause_evidence OWNER TO flyway;
ALTER TABLE cross_market_risk_snapshots OWNER TO flyway;
ALTER TABLE cross_market_snapshot_evidence_links OWNER TO flyway;

ALTER TABLE cross_market_risk_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE cross_market_risk_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY cross_market_risk_snapshots_owner_policy
  ON cross_market_risk_snapshots
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''))
  WITH CHECK (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));

ALTER TABLE cross_market_snapshot_evidence_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE cross_market_snapshot_evidence_links FORCE ROW LEVEL SECURITY;
CREATE POLICY cross_market_snapshot_evidence_links_owner_policy
  ON cross_market_snapshot_evidence_links
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''))
  WITH CHECK (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));

-- table owner까지 accidental rewrite를 하지 못하게 UPDATE/DELETE/TRUNCATE를 statement trigger로 거부한다.
CREATE FUNCTION reject_cross_market_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $reject_cross_market_mutation$
BEGIN
  RAISE EXCEPTION 'cross-market evidence is append-only'
    USING ERRCODE = '55000';
END
$reject_cross_market_mutation$;
ALTER FUNCTION reject_cross_market_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_cross_market_mutation() FROM PUBLIC;

CREATE TRIGGER market_source_entitlements_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON market_source_entitlements
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER cross_market_exposure_catalog_entries_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON cross_market_exposure_catalog_entries
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER cross_market_observations_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON cross_market_observations
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER analyst_revision_evidence_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON analyst_revision_evidence
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER market_cause_evidence_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON market_cause_evidence
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER cross_market_risk_snapshots_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON cross_market_risk_snapshots
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();
CREATE TRIGGER cross_market_snapshot_evidence_links_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON cross_market_snapshot_evidence_links
  FOR EACH STATEMENT EXECUTE FUNCTION reject_cross_market_mutation();

CREATE VIEW latest_cross_market_observations AS
SELECT
  logical_identity_hash,
  instrument,
  market,
  value_type,
  observed_value,
  session_date,
  status,
  completeness,
  observed_at,
  received_at,
  available_at,
  evaluated_at,
  source_ref,
  artifact_hash,
  payload_json
FROM (
  SELECT
    observation.*,
    row_number() OVER (
      PARTITION BY observation.instrument, observation.value_type
      ORDER BY observation.available_at DESC, observation.observed_at DESC, observation.logical_identity_hash
    ) AS bounded_rank
  FROM cross_market_observations AS observation
) AS ranked
WHERE bounded_rank = 1;
ALTER VIEW latest_cross_market_observations OWNER TO flyway;

CREATE VIEW latest_analyst_revision_evidence AS
SELECT
  logical_identity_hash,
  original_evidence_id,
  symbol,
  broker_id,
  estimate_period,
  contributor_count,
  buy_opinion_weight,
  dispersion,
  published_at,
  received_at,
  available_at,
  retracted,
  supersedes_evidence_id,
  artifact_hash,
  payload_json
FROM (
  SELECT
    evidence.*,
    row_number() OVER (
      PARTITION BY evidence.symbol, evidence.broker_id, evidence.estimate_period
      ORDER BY evidence.available_at DESC, evidence.published_at DESC, evidence.logical_identity_hash
    ) AS bounded_rank
  FROM analyst_revision_evidence AS evidence
) AS ranked
WHERE bounded_rank = 1;
ALTER VIEW latest_analyst_revision_evidence OWNER TO flyway;

CREATE VIEW latest_market_cause_evidence AS
SELECT
  logical_identity_hash,
  source_family,
  source_lineage_hash,
  dedupe_key_hash,
  classification,
  relation,
  counterargument,
  retracted,
  supersedes_evidence_id,
  occurred_at,
  published_at,
  received_at,
  available_at,
  sanitized_summary,
  artifact_hash,
  payload_json
FROM (
  SELECT
    evidence.*,
    row_number() OVER (
      PARTITION BY evidence.source_family, evidence.dedupe_key_hash
      ORDER BY evidence.available_at DESC, evidence.published_at DESC, evidence.logical_identity_hash
    ) AS bounded_rank
  FROM market_cause_evidence AS evidence
) AS ranked
WHERE bounded_rank = 1;
ALTER VIEW latest_market_cause_evidence OWNER TO flyway;

CREATE VIEW latest_cross_market_risk_snapshots AS
SELECT
  logical_identity_hash,
  owner_user_id,
  owner_scope_hash,
  config_version,
  availability,
  evidence_mode,
  snapshot_available_at,
  decision_authority,
  order_authority,
  validation_status,
  artifact_hash,
  payload_json
FROM (
  SELECT
    snapshot.*,
    row_number() OVER (
      PARTITION BY snapshot.owner_user_id, snapshot.owner_scope_hash, snapshot.config_version
      ORDER BY snapshot.snapshot_available_at DESC, snapshot.logical_identity_hash
    ) AS bounded_rank
  FROM cross_market_risk_snapshots AS snapshot
) AS ranked
WHERE bounded_rank = 1;
ALTER VIEW latest_cross_market_risk_snapshots OWNER TO flyway;

-- 계약의 object key profile을 재귀 확인해 허용되지 않은 raw 필드가 append-only payload로 남지 않게 한다.
CREATE FUNCTION cross_market_fixture_value_is_safe(p_value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public, pg_temp
AS $cross_market_fixture_value_is_safe$
  WITH RECURSIVE nested_value(value) AS (
    SELECT p_value
    UNION ALL
    SELECT child.value
    FROM nested_value AS parent
    CROSS JOIN LATERAL jsonb_array_elements(
      CASE
        WHEN jsonb_typeof(parent.value) = 'array' THEN parent.value
        ELSE '[]'::jsonb
      END
    ) AS child(value)
  )
  SELECT NOT EXISTS (
    SELECT 1
    FROM nested_value
    WHERE jsonb_typeof(value) = 'object'
  )
$cross_market_fixture_value_is_safe$;
ALTER FUNCTION cross_market_fixture_value_is_safe(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION cross_market_fixture_value_is_safe(jsonb) FROM PUBLIC;

-- profile leaf는 primitive 또는 primitive만 재귀 포함한 array만 허용하고 object는 명시 profile을 요구한다.
CREATE FUNCTION cross_market_fixture_payload_keys_are_allowed(
  p_value jsonb,
  p_key_profile jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public, pg_temp
AS $cross_market_fixture_payload_keys_are_allowed$
DECLARE
  record_key text;
  nested_profile jsonb;
BEGIN
  IF jsonb_typeof(p_value) IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_key_profile) IS DISTINCT FROM 'object' THEN
    RETURN false;
  END IF;

  FOR record_key IN SELECT jsonb_object_keys(p_value)
  LOOP
    nested_profile := p_key_profile -> record_key;
    IF nested_profile IS NULL THEN
      RETURN false;
    END IF;
    IF jsonb_typeof(nested_profile) = 'object'
       AND NOT public.cross_market_fixture_payload_keys_are_allowed(
         p_value -> record_key,
         nested_profile
       ) THEN
      RETURN false;
    END IF;
    IF jsonb_typeof(nested_profile) <> 'object'
       AND NOT public.cross_market_fixture_value_is_safe(p_value -> record_key) THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END
$cross_market_fixture_payload_keys_are_allowed$;
ALTER FUNCTION cross_market_fixture_payload_keys_are_allowed(jsonb, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION cross_market_fixture_payload_keys_are_allowed(jsonb, jsonb) FROM PUBLIC;

-- JSON key 검사로 raw provider/article/PDF/account/credential persistence를 함수 진입점에서 막는다.
CREATE FUNCTION validate_cross_market_fixture_payload(
  p_record jsonb,
  p_key_profile jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $validate_cross_market_fixture_payload$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_market_writer'
     OR jsonb_typeof(p_record) IS DISTINCT FROM 'object'
     OR NOT public.cross_market_fixture_payload_keys_are_allowed(p_record, p_key_profile)
     OR p_record::text ~* '"(raw_?body|article_?(body|title|url|metadata)|pdf_?(content|text)|account_?(number|id)|access_?token|api_?key|credential|provider_?raw|providerPhysicalCalls|externalLlmCalls)"[[:space:]]*:'
     OR (p_record ? 'decisionAuthority' AND p_record ->> 'decisionAuthority' <> 'NONE') THEN
    RAISE EXCEPTION 'cross-market fixture payload is not permitted'
      USING ERRCODE = '22023';
  END IF;
END
$validate_cross_market_fixture_payload$;
ALTER FUNCTION validate_cross_market_fixture_payload(jsonb, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION validate_cross_market_fixture_payload(jsonb, jsonb) FROM PUBLIC;

CREATE FUNCTION append_market_source_entitlement(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_market_source_entitlement$
DECLARE
  inserted_count integer;
BEGIN
  PERFORM public.validate_cross_market_fixture_payload(
    p_record,
    jsonb_build_object(
      'activationStatus', true,
      'attributionRequired', true,
      'category', true,
      'contractExpiry', true,
      'decisionAuthority', true,
      'deletionOwner', true,
      'derivedDataAllowed', true,
      'embeddingAllowed', true,
      'endpointIdentityHash', true,
      'entitlementVersion', true,
      'externalLlmAllowed', true,
      'logicalIdentityHash', true,
      'machineFetchAllowed', true,
      'materializationDeclaration', jsonb_build_object(
        'derivedPayloadProduced', true,
        'embedded', true,
        'externalLlmProcessed', true,
        'nonDisplayUsed', true,
        'rawStored', true
      ),
      'nonDisplayAllowed', true,
      'projectionRetentionMaxDays', true,
      'providerCallsAllowed', true,
      'rawRetentionMaxHours', true,
      'rawStoreAllowed', true,
      'region', true,
      'sourceFamily', true,
      'sourceId', true
    )
  );
  IF p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'activationStatus' <> 'CANDIDATE_DISABLED'
     OR (p_record ->> 'providerCallsAllowed')::boolean
     OR (p_record ->> 'machineFetchAllowed')::boolean
     OR (p_record ->> 'rawStoreAllowed')::boolean
     OR (p_record ->> 'embeddingAllowed')::boolean
     OR (p_record ->> 'externalLlmAllowed')::boolean THEN
    RAISE EXCEPTION 'cross-market entitlement must remain disabled'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.market_source_entitlements (
    logical_identity_hash,
    source_id,
    source_family,
    category,
    activation_status,
    provider_calls_allowed,
    decision_authority,
    contract_expiry,
    record_hash,
    payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash',
    p_record ->> 'sourceId',
    p_record ->> 'sourceFamily',
    p_record ->> 'category',
    p_record ->> 'activationStatus',
    (p_record ->> 'providerCallsAllowed')::boolean,
    p_record ->> 'decisionAuthority',
    (p_record ->> 'contractExpiry')::timestamptz,
    encode(digest(convert_to(p_record::text, 'UTF8'), 'sha256'), 'hex'),
    p_record
  )
  ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN
    RETURN 'INSERTED';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.market_source_entitlements
    WHERE logical_identity_hash = p_record ->> 'logicalIdentityHash'
      AND payload_json = p_record
  ) THEN
    RETURN 'REPLAY';
  END IF;
  RAISE EXCEPTION 'cross-market entitlement identity conflict'
    USING ERRCODE = '23505';
END
$append_market_source_entitlement$;

CREATE FUNCTION append_cross_market_exposure_catalog_entry(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_cross_market_exposure_catalog_entry$
DECLARE
  inserted_count integer;
BEGIN
  PERFORM public.validate_cross_market_fixture_payload(
    p_record,
    jsonb_build_object(
      'artifactHash', true,
      'availableAt', true,
      'classification', true,
      'configVersion', true,
      'contractId', true,
      'effectiveAt', true,
      'inScope', true,
      'logicalIdentityHash', true,
      'payloadHash', true,
      'schemaVersion', true,
      'sourceLineage', true,
      'symbol', true,
      'validationState', true
    )
  );
  IF p_record ->> 'contractId' <> 'cross_market_exposure_catalog.v1'
     OR p_record ->> 'schemaVersion' <> '1'
     OR p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'payloadHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'artifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_record -> 'sourceLineage') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'cross-market exposure payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.cross_market_exposure_catalog_entries (
    logical_identity_hash, symbol, classification, config_version, effective_at,
    available_at, in_scope, validation_state, source_lineage, payload_hash,
    artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_record ->> 'symbol', p_record ->> 'classification',
    p_record ->> 'configVersion', (p_record ->> 'effectiveAt')::timestamptz,
    (p_record ->> 'availableAt')::timestamptz, (p_record ->> 'inScope')::boolean,
    p_record ->> 'validationState', p_record -> 'sourceLineage', p_record ->> 'payloadHash',
    p_record ->> 'artifactHash', p_record
  )
  ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN RETURN 'INSERTED'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.cross_market_exposure_catalog_entries
    WHERE logical_identity_hash = p_record ->> 'logicalIdentityHash' AND payload_json = p_record
  ) THEN RETURN 'REPLAY'; END IF;
  RAISE EXCEPTION 'cross-market exposure identity conflict' USING ERRCODE = '23505';
END
$append_cross_market_exposure_catalog_entry$;

CREATE FUNCTION append_cross_market_observation(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_cross_market_observation$
DECLARE
  inserted_count integer;
BEGIN
  PERFORM public.validate_cross_market_fixture_payload(
    p_record,
    jsonb_build_object(
      'abstainReason', true,
      'artifactHash', true,
      'availableAt', true,
      'completeness', true,
      'contractId', true,
      'decisionAuthority', true,
      'evaluatedAt', true,
      'instrument', true,
      'logicalIdentityHash', true,
      'market', true,
      'observedAt', true,
      'payloadHash', true,
      'receivedAt', true,
      'schemaVersion', true,
      'sessionDate', true,
      'sourceRef', true,
      'status', true,
      'timeframe', true,
      'value', true,
      'valueType', true
    )
  );
  IF p_record ->> 'contractId' <> 'cross_market_observation.v1'
     OR p_record ->> 'schemaVersion' <> '1'
     OR p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'payloadHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'artifactHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'sourceRef' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'cross-market observation payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.cross_market_observations (
    logical_identity_hash, instrument, market, value_type, observed_value,
    session_date, timeframe, status, completeness, observed_at, received_at,
    available_at, evaluated_at, source_ref, payload_hash, artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_record ->> 'instrument', p_record ->> 'market',
    p_record ->> 'valueType', (p_record ->> 'value')::numeric,
    (p_record ->> 'sessionDate')::date, p_record ->> 'timeframe', p_record ->> 'status',
    p_record ->> 'completeness', (p_record ->> 'observedAt')::timestamptz,
    (p_record ->> 'receivedAt')::timestamptz, (p_record ->> 'availableAt')::timestamptz,
    (p_record ->> 'evaluatedAt')::timestamptz, p_record ->> 'sourceRef',
    p_record ->> 'payloadHash', p_record ->> 'artifactHash', p_record
  )
  ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN RETURN 'INSERTED'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.cross_market_observations
    WHERE logical_identity_hash = p_record ->> 'logicalIdentityHash' AND payload_json = p_record
  ) THEN RETURN 'REPLAY'; END IF;
  RAISE EXCEPTION 'cross-market observation identity conflict' USING ERRCODE = '23505';
END
$append_cross_market_observation$;

CREATE FUNCTION append_analyst_revision_evidence(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_analyst_revision_evidence$
DECLARE
  inserted_count integer;
BEGIN
  PERFORM public.validate_cross_market_fixture_payload(
    p_record,
    jsonb_build_object(
      'artifactHash', true,
      'availableAt', true,
      'brokerId', true,
      'buyOpinionWeight', true,
      'contractId', true,
      'contributorCount', true,
      'current', jsonb_build_object(
        'eps', true,
        'rating', true,
        'revenue', true,
        'targetPrice', true
      ),
      'decisionAuthority', true,
      'dedupeKeyHash', true,
      'dispersion', true,
      'estimatePeriod', true,
      'logicalIdentityHash', true,
      'originalEvidenceId', true,
      'payloadHash', true,
      'previous', jsonb_build_object(
        'eps', true,
        'rating', true,
        'revenue', true,
        'targetPrice', true
      ),
      'publishedAt', true,
      'rawTextStored', true,
      'receivedAt', true,
      'retracted', true,
      'revision', jsonb_build_object(
        'epsDelta', true,
        'ratingChanged', true,
        'revenueDelta', true,
        'targetPriceDelta', true
      ),
      'schemaVersion', true,
      'sourceLicense', true,
      'supersedesEvidenceId', true,
      'symbol', true,
      'userConfirmedTags', true
    )
  );
  IF p_record ->> 'contractId' <> 'analyst_revision_evidence.v1'
     OR p_record ->> 'schemaVersion' <> '1'
     OR p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'payloadHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'artifactHash' !~ '^[0-9a-f]{64}$'
     OR (p_record ->> 'rawTextStored')::boolean
     OR (p_record ->> 'buyOpinionWeight')::numeric <> 0 THEN
    RAISE EXCEPTION 'analyst revision payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.analyst_revision_evidence (
    logical_identity_hash, original_evidence_id, symbol, broker_id, estimate_period,
    contributor_count, buy_opinion_weight, dispersion, published_at, received_at,
    available_at, retracted, supersedes_evidence_id, dedupe_key_hash, payload_hash,
    artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_record ->> 'originalEvidenceId', p_record ->> 'symbol',
    p_record ->> 'brokerId', p_record ->> 'estimatePeriod',
    (p_record ->> 'contributorCount')::integer, (p_record ->> 'buyOpinionWeight')::numeric,
    (p_record ->> 'dispersion')::numeric, (p_record ->> 'publishedAt')::timestamptz,
    (p_record ->> 'receivedAt')::timestamptz, (p_record ->> 'availableAt')::timestamptz,
    (p_record ->> 'retracted')::boolean, nullif(p_record ->> 'supersedesEvidenceId', ''),
    p_record ->> 'dedupeKeyHash', p_record ->> 'payloadHash', p_record ->> 'artifactHash', p_record
  )
  ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN RETURN 'INSERTED'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.analyst_revision_evidence
    WHERE logical_identity_hash = p_record ->> 'logicalIdentityHash' AND payload_json = p_record
  ) THEN RETURN 'REPLAY'; END IF;
  RAISE EXCEPTION 'analyst revision identity conflict' USING ERRCODE = '23505';
END
$append_analyst_revision_evidence$;

CREATE FUNCTION append_market_cause_evidence(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_market_cause_evidence$
DECLARE
  inserted_count integer;
BEGIN
  PERFORM public.validate_cross_market_fixture_payload(
    p_record,
    jsonb_build_object(
      'artifactHash', true,
      'availableAt', true,
      'classification', true,
      'contractId', true,
      'contradictionEvidenceIds', true,
      'counterargument', true,
      'decisionAuthority', true,
      'dedupeKeyHash', true,
      'logicalIdentityHash', true,
      'occurredAt', true,
      'payloadHash', true,
      'publishedAt', true,
      'receivedAt', true,
      'relatedEvidenceIds', true,
      'relation', true,
      'retracted', true,
      'sanitizedSummary', true,
      'schemaVersion', true,
      'sourceFamily', true,
      'sourceLineageHash', true,
      'supersedesEvidenceId', true
    )
  );
  IF p_record ->> 'contractId' <> 'market_cause_evidence.v1'
     OR p_record ->> 'schemaVersion' <> '1'
     OR p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'payloadHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'artifactHash' !~ '^[0-9a-f]{64}$'
     OR (
       p_record ->> 'sourceFamily' = 'GDELT_AGGREGATE'
       AND (
         p_record ->> 'classification' = 'CONFIRMED_FACT'
         OR p_record ->> 'relation' = 'REPORTED_AS_CAUSE'
       )
     ) THEN
    RAISE EXCEPTION 'market cause payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.market_cause_evidence (
    logical_identity_hash, source_family, source_lineage_hash, dedupe_key_hash,
    classification, relation, counterargument, retracted, supersedes_evidence_id,
    occurred_at, published_at, received_at, available_at, sanitized_summary,
    payload_hash, artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_record ->> 'sourceFamily',
    p_record ->> 'sourceLineageHash', p_record ->> 'dedupeKeyHash',
    p_record ->> 'classification', p_record ->> 'relation',
    (p_record ->> 'counterargument')::boolean, (p_record ->> 'retracted')::boolean,
    nullif(p_record ->> 'supersedesEvidenceId', ''), (p_record ->> 'occurredAt')::timestamptz,
    (p_record ->> 'publishedAt')::timestamptz, (p_record ->> 'receivedAt')::timestamptz,
    (p_record ->> 'availableAt')::timestamptz, p_record ->> 'sanitizedSummary',
    p_record ->> 'payloadHash', p_record ->> 'artifactHash', p_record
  )
  ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN RETURN 'INSERTED'; END IF;
  IF EXISTS (
    SELECT 1 FROM public.market_cause_evidence
    WHERE logical_identity_hash = p_record ->> 'logicalIdentityHash' AND payload_json = p_record
  ) THEN RETURN 'REPLAY'; END IF;
  RAISE EXCEPTION 'market cause identity conflict' USING ERRCODE = '23505';
END
$append_market_cause_evidence$;

ALTER FUNCTION append_market_source_entitlement(jsonb) OWNER TO flyway;
ALTER FUNCTION append_cross_market_exposure_catalog_entry(jsonb) OWNER TO flyway;
ALTER FUNCTION append_cross_market_observation(jsonb) OWNER TO flyway;
ALTER FUNCTION append_analyst_revision_evidence(jsonb) OWNER TO flyway;
ALTER FUNCTION append_market_cause_evidence(jsonb) OWNER TO flyway;

REVOKE ALL PRIVILEGES ON TABLE
  market_source_entitlements,
  cross_market_exposure_catalog_entries,
  cross_market_observations,
  analyst_revision_evidence,
  market_cause_evidence,
  cross_market_risk_snapshots,
  cross_market_snapshot_evidence_links,
  latest_cross_market_observations,
  latest_analyst_revision_evidence,
  latest_market_cause_evidence,
  latest_cross_market_risk_snapshots
FROM PUBLIC, decision_app, decision_market_writer;

REVOKE ALL PRIVILEGES ON FUNCTION
  append_market_source_entitlement(jsonb),
  append_cross_market_exposure_catalog_entry(jsonb),
  append_cross_market_observation(jsonb),
  append_analyst_revision_evidence(jsonb),
  append_market_cause_evidence(jsonb)
FROM PUBLIC, decision_app, decision_market_writer;

DO $s4_8b_cross_market_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
    GRANT EXECUTE ON FUNCTION
      append_market_source_entitlement(jsonb),
      append_cross_market_exposure_catalog_entry(jsonb),
      append_cross_market_observation(jsonb),
      append_analyst_revision_evidence(jsonb),
      append_market_cause_evidence(jsonb)
    TO decision_market_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT SELECT ON TABLE
      latest_cross_market_observations,
      latest_analyst_revision_evidence,
      latest_market_cause_evidence,
      latest_cross_market_risk_snapshots
    TO decision_app;
  END IF;
END
$s4_8b_cross_market_acl$;
