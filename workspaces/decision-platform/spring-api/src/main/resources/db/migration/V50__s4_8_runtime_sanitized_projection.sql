-- S4.8 Core 6/Optional 3 runtime은 provider body 없이 local typed state만 append한다.
-- 이 migration은 entitlement/packet을 넓히지 않으며 provider physical call은 계속 0이다.
CREATE FUNCTION s48_runtime_source_pair_is_valid(
  p_source_family text,
  p_source_id text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $s48_runtime_source_pair_is_valid$
  SELECT COALESCE(
    (p_source_family = 'KIS' AND p_source_id = 'S48_CORE6_KIS')
    OR (p_source_family = 'OPENDART' AND p_source_id = 'S48_CORE6_OPENDART')
    OR (p_source_family = 'SEC_EDGAR' AND p_source_id = 'S48_CORE6_SEC_EDGAR')
    OR (p_source_family = 'KRX' AND p_source_id = 'S48_CORE6_KRX')
    OR (p_source_family = 'KOFIA' AND p_source_id = 'S48_CORE6_KOFIA')
    OR (p_source_family = 'ECOS' AND p_source_id = 'S48_CORE6_ECOS')
    OR (p_source_family = 'FINNHUB_OPTIONAL3' AND p_source_id = 'S48_OPTIONAL3_FINNHUB')
    OR (p_source_family = 'TWELVE_DATA' AND p_source_id = 'S48_OPTIONAL3_TWELVE_DATA')
    OR (p_source_family = 'MASSIVE' AND p_source_id = 'S48_OPTIONAL3_MASSIVE'),
    false
  )
$s48_runtime_source_pair_is_valid$;
ALTER FUNCTION s48_runtime_source_pair_is_valid(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION s48_runtime_source_pair_is_valid(text, text) FROM PUBLIC;

CREATE FUNCTION s48_runtime_state_is_safe(
  p_source_family text,
  p_ingestion_mode text,
  p_status text,
  p_reason text,
  p_projection_hash text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $s48_runtime_state_is_safe$
  SELECT COALESCE(
    (
      p_source_family IN ('OPENDART', 'ECOS')
      AND p_ingestion_mode = 'REUSE_AUTHORIZED_PROJECTION'
      AND (
        (
          p_status = 'AVAILABLE'
          AND p_reason = 'AUTHORIZED_PROJECTION_AVAILABLE'
          AND p_projection_hash ~ '^[0-9a-f]{64}$'
        )
        OR (
          p_status = 'ABSTAIN'
          AND p_reason = 'REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE'
          AND p_projection_hash IS NULL
        )
      )
    )
    OR (
      p_source_family IN ('KIS', 'SEC_EDGAR', 'KRX')
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND p_status = 'ABSTAIN'
      AND p_reason = 'APPROVAL_PACKET_REQUIRED'
      AND p_projection_hash IS NULL
    )
    OR (
      p_source_family = 'KOFIA'
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND p_status = 'BLOCKED'
      AND p_reason = 'BLOCKED_NO_CREDENTIAL_OR_APPROVAL'
      AND p_projection_hash IS NULL
    )
    OR (
      p_source_family IN ('FINNHUB_OPTIONAL3', 'TWELVE_DATA', 'MASSIVE')
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND p_status = 'BLOCKED'
      AND p_reason = 'BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT'
      AND p_projection_hash IS NULL
    ),
    false
  )
$s48_runtime_state_is_safe$;
ALTER FUNCTION s48_runtime_state_is_safe(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION s48_runtime_state_is_safe(text, text, text, text, text) FROM PUBLIC;

CREATE TABLE s48_runtime_sanitized_projections (
  logical_identity_hash text PRIMARY KEY,
  source_family text NOT NULL,
  source_id text NOT NULL,
  evaluated_at timestamptz NOT NULL,
  ingestion_mode text NOT NULL,
  status text NOT NULL,
  reason text NOT NULL,
  projection_hash text,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT s48_runtime_sanitized_identity_hash_check
    CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT s48_runtime_sanitized_source_pair_check
    CHECK (public.s48_runtime_source_pair_is_valid(source_family, source_id)),
  CONSTRAINT s48_runtime_sanitized_hash_check
    CHECK (
      payload_hash ~ '^[0-9a-f]{64}$'
      AND artifact_hash ~ '^[0-9a-f]{64}$'
      AND (projection_hash IS NULL OR projection_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT s48_runtime_sanitized_state_check
    CHECK (public.s48_runtime_state_is_safe(
      source_family, ingestion_mode, status, reason, projection_hash
    )),
  CONSTRAINT s48_runtime_sanitized_payload_check
    CHECK (jsonb_typeof(payload_json) = 'object'),
  CONSTRAINT s48_runtime_sanitized_source_as_of_unique
    UNIQUE (source_id, evaluated_at)
);
CREATE INDEX s48_runtime_sanitized_latest_idx
  ON s48_runtime_sanitized_projections (source_id, evaluated_at DESC, logical_identity_hash DESC);
ALTER TABLE s48_runtime_sanitized_projections OWNER TO flyway;

CREATE FUNCTION reject_s48_runtime_sanitized_projection_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_s48_runtime_sanitized_projection_mutation$
BEGIN
  RAISE EXCEPTION 'S4.8 runtime sanitized projections are append-only'
    USING ERRCODE = '55000';
END
$reject_s48_runtime_sanitized_projection_mutation$;
ALTER FUNCTION reject_s48_runtime_sanitized_projection_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_s48_runtime_sanitized_projection_mutation() FROM PUBLIC;

CREATE TRIGGER s48_runtime_sanitized_projections_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON s48_runtime_sanitized_projections
  FOR EACH STATEMENT EXECUTE FUNCTION reject_s48_runtime_sanitized_projection_mutation();

CREATE FUNCTION append_s48_runtime_sanitized_projection(p_record jsonb)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_s48_runtime_sanitized_projection$
DECLARE
  evaluated_at_value timestamptz;
  projection_hash_value text;
  inserted_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_market_writer'
     OR jsonb_typeof(p_record) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'S4.8 runtime writer arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  IF NOT (p_record ?& ARRAY[
       'artifactHash', 'contractId', 'decisionAuthority', 'evaluatedAt', 'ingestionMode',
       'logicalIdentityHash', 'orderAuthority', 'payloadHash', 'projectionHash',
       'providerPhysicalCalls', 'rawProviderDataStored', 'reason', 'retryCount',
       'riskSignalOrderAuthority', 'schemaVersion', 'sourceFamily', 'sourceId', 'status'
     ])
     OR (SELECT count(*) FROM jsonb_object_keys(p_record)) <> 18
     OR jsonb_typeof(p_record -> 'artifactHash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'contractId') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'decisionAuthority') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'ingestionMode') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'logicalIdentityHash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'orderAuthority') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'payloadHash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'reason') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'riskSignalOrderAuthority') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'sourceFamily') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'sourceId') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'status') IS DISTINCT FROM 'string'
     OR NOT COALESCE(p_record ->> 'artifactHash' ~ '^[0-9a-f]{64}$', false)
     OR NOT COALESCE(p_record ->> 'logicalIdentityHash' ~ '^[0-9a-f]{64}$', false)
     OR NOT COALESCE(p_record ->> 'payloadHash' ~ '^[0-9a-f]{64}$', false)
     OR p_record ->> 'contractId' IS DISTINCT FROM 's4-8-runtime-lane.v1'
     OR p_record ->> 'decisionAuthority' IS DISTINCT FROM 'NONE'
     OR p_record ->> 'orderAuthority' IS DISTINCT FROM 'NONE'
     OR p_record ->> 'riskSignalOrderAuthority' IS DISTINCT FROM 'NONE'
     OR jsonb_typeof(p_record -> 'schemaVersion') IS DISTINCT FROM 'number'
     OR p_record -> 'schemaVersion' IS DISTINCT FROM '1'::jsonb
     OR jsonb_typeof(p_record -> 'providerPhysicalCalls') IS DISTINCT FROM 'number'
     OR p_record -> 'providerPhysicalCalls' IS DISTINCT FROM '0'::jsonb
     OR jsonb_typeof(p_record -> 'rawProviderDataStored') IS DISTINCT FROM 'boolean'
     OR p_record -> 'rawProviderDataStored' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(p_record -> 'retryCount') IS DISTINCT FROM 'number'
     OR p_record -> 'retryCount' IS DISTINCT FROM '0'::jsonb
     OR jsonb_typeof(p_record -> 'evaluatedAt') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'projectionHash') NOT IN ('null', 'string') THEN
    RAISE EXCEPTION 'S4.8 runtime writer arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  IF p_record -> 'projectionHash' = 'null'::jsonb THEN
    projection_hash_value := NULL;
  ELSE
    projection_hash_value := p_record ->> 'projectionHash';
  END IF;
  BEGIN
    evaluated_at_value := (p_record ->> 'evaluatedAt')::timestamptz;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'S4.8 runtime evaluated-at is invalid'
      USING ERRCODE = '22023';
  END;
  IF NOT public.s48_runtime_source_pair_is_valid(
      p_record ->> 'sourceFamily', p_record ->> 'sourceId'
    )
    OR NOT public.s48_runtime_state_is_safe(
      p_record ->> 'sourceFamily', p_record ->> 'ingestionMode', p_record ->> 'status',
      p_record ->> 'reason', projection_hash_value
    ) THEN
    RAISE EXCEPTION 'S4.8 runtime sanitized payload is invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.s48_runtime_sanitized_projections (
    logical_identity_hash, source_family, source_id, evaluated_at, ingestion_mode, status,
    reason, projection_hash, payload_hash, artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_record ->> 'sourceFamily', p_record ->> 'sourceId',
    evaluated_at_value, p_record ->> 'ingestionMode', p_record ->> 'status',
    p_record ->> 'reason', projection_hash_value, p_record ->> 'payloadHash',
    p_record ->> 'artifactHash', p_record
  ) ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN
    RETURN 'INSERTED';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.s48_runtime_sanitized_projections AS projection
    WHERE projection.logical_identity_hash = p_record ->> 'logicalIdentityHash'
      AND projection.payload_json = p_record
      AND projection.payload_hash = p_record ->> 'payloadHash'
      AND projection.artifact_hash = p_record ->> 'artifactHash'
  ) THEN
    RETURN 'REPLAY';
  END IF;
  RAISE EXCEPTION 'S4.8 runtime sanitized projection identity conflict'
    USING ERRCODE = '23505';
END
$append_s48_runtime_sanitized_projection$;
ALTER FUNCTION append_s48_runtime_sanitized_projection(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION append_s48_runtime_sanitized_projection(jsonb) FROM PUBLIC;

CREATE FUNCTION read_latest_s48_runtime_sanitized_projection(p_source_id text)
RETURNS TABLE (payload_json jsonb)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_latest_s48_runtime_sanitized_projection$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_source_id NOT IN (
       'S48_CORE6_KIS', 'S48_CORE6_OPENDART', 'S48_CORE6_SEC_EDGAR', 'S48_CORE6_KRX',
       'S48_CORE6_KOFIA', 'S48_CORE6_ECOS', 'S48_OPTIONAL3_FINNHUB',
       'S48_OPTIONAL3_TWELVE_DATA', 'S48_OPTIONAL3_MASSIVE'
     ) THEN
    RAISE EXCEPTION 'S4.8 runtime reader arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT projection.payload_json
  FROM public.s48_runtime_sanitized_projections AS projection
  WHERE projection.source_id = p_source_id
  ORDER BY projection.evaluated_at DESC, projection.logical_identity_hash DESC
  LIMIT 1;
END
$read_latest_s48_runtime_sanitized_projection$;
ALTER FUNCTION read_latest_s48_runtime_sanitized_projection(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_latest_s48_runtime_sanitized_projection(text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE s48_runtime_sanitized_projections
FROM PUBLIC, decision_app, decision_market_writer;
REVOKE ALL PRIVILEGES ON FUNCTION
  append_s48_runtime_sanitized_projection(jsonb),
  read_latest_s48_runtime_sanitized_projection(text)
FROM PUBLIC, decision_app, decision_market_writer;

DO $s48_runtime_sanitized_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
    GRANT EXECUTE ON FUNCTION append_s48_runtime_sanitized_projection(jsonb)
    TO decision_market_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION read_latest_s48_runtime_sanitized_projection(text)
    TO decision_app;
  END IF;
END
$s48_runtime_sanitized_acl$;
