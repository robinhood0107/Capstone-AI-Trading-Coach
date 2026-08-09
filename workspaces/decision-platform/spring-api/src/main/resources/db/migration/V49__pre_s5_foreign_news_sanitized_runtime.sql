-- foreign-news는 raw article/provider payload를 저장하지 않는 owner-scoped sanitized aggregate만 가진다.
-- GDELT HTTP transport/executor는 이 migration으로 추가하지 않으며, writer는 V23의 market role을 재사용한다.
CREATE TABLE foreign_news_sentiment_aggregates (
  logical_identity_hash text PRIMARY KEY,
  -- append-only aggregate를 user delete cascade가 우회해 지우지 못하게 한다.
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  symbol text NOT NULL,
  as_of timestamptz NOT NULL,
  status text NOT NULL,
  lane_states jsonb NOT NULL,
  payload_hash text NOT NULL,
  artifact_hash text NOT NULL,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT foreign_news_sentiment_identity_hash_check
    CHECK (logical_identity_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT foreign_news_sentiment_symbol_check
    CHECK (symbol ~ '^[0-9A-Z._:-]{1,20}$'),
  CONSTRAINT foreign_news_sentiment_status_check
    CHECK (status IN ('AVAILABLE', 'ABSTAIN')),
  CONSTRAINT foreign_news_sentiment_lanes_check
    CHECK (jsonb_typeof(lane_states) = 'array' AND jsonb_array_length(lane_states) = 4),
  CONSTRAINT foreign_news_sentiment_hash_check
    CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND artifact_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT foreign_news_sentiment_payload_check
    CHECK (jsonb_typeof(payload_json) = 'object'),
  CONSTRAINT foreign_news_sentiment_owner_symbol_as_of_unique
    UNIQUE (owner_user_id, symbol, as_of)
);

CREATE INDEX foreign_news_sentiment_owner_latest_idx
  ON foreign_news_sentiment_aggregates (owner_user_id, symbol, as_of DESC, logical_identity_hash);

ALTER TABLE foreign_news_sentiment_aggregates OWNER TO flyway;
ALTER TABLE foreign_news_sentiment_aggregates ENABLE ROW LEVEL SECURITY;
ALTER TABLE foreign_news_sentiment_aggregates FORCE ROW LEVEL SECURITY;
CREATE POLICY foreign_news_sentiment_owner_policy
  ON foreign_news_sentiment_aggregates
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));

CREATE FUNCTION reject_foreign_news_sentiment_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_foreign_news_sentiment_mutation$
BEGIN
  RAISE EXCEPTION 'foreign-news sentiment aggregates are append-only'
    USING ERRCODE = '55000';
END
$reject_foreign_news_sentiment_mutation$;
ALTER FUNCTION reject_foreign_news_sentiment_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_foreign_news_sentiment_mutation() FROM PUBLIC;

CREATE TRIGGER foreign_news_sentiment_aggregates_immutable
  BEFORE UPDATE OR DELETE OR TRUNCATE ON foreign_news_sentiment_aggregates
  FOR EACH STATEMENT EXECUTE FUNCTION reject_foreign_news_sentiment_mutation();

-- lane payload는 state 두 field 외 어떤 article/provider attribute도 가질 수 없다.
CREATE FUNCTION foreign_news_lane_states_are_safe(p_lanes jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $foreign_news_lane_states_are_safe$
  SELECT
    jsonb_typeof(p_lanes) = 'array'
    AND jsonb_array_length(p_lanes) = 4
    AND p_lanes -> 0 = jsonb_build_object(
      'laneId', 'FINNHUB_PERSONAL_LOCAL',
      'state', p_lanes -> 0 ->> 'state'
    )
    AND p_lanes -> 1 = jsonb_build_object(
      'laneId', 'SEC_OFFICIAL',
      'state', p_lanes -> 1 ->> 'state'
    )
    AND p_lanes -> 2 = jsonb_build_object(
      'laneId', 'FED_OFFICIAL',
      'state', p_lanes -> 2 ->> 'state'
    )
    AND p_lanes -> 3 = jsonb_build_object(
      'laneId', 'GDELT_OFFLINE_REFERENCE',
      'state', p_lanes -> 3 ->> 'state'
    )
    AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(p_lanes) AS lane
      WHERE jsonb_typeof(lane) <> 'object'
         OR lane ->> 'state' IS NULL
         OR lane ->> 'state' NOT IN ('AVAILABLE', 'ABSTAIN', 'NOT_ACTIVATED')
    )
$foreign_news_lane_states_are_safe$;
ALTER FUNCTION foreign_news_lane_states_are_safe(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION foreign_news_lane_states_are_safe(jsonb) FROM PUBLIC;

CREATE FUNCTION append_owned_foreign_news_sentiment(
  p_owner_user_id text,
  p_record jsonb
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_owned_foreign_news_sentiment$
DECLARE
  payload jsonb;
  inserted_count integer;
  has_available_lane boolean;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_market_writer'
     OR p_owner_user_id !~ '^[A-Za-z0-9._:-]{3,128}$'
     OR jsonb_typeof(p_record) <> 'object'
     OR NOT (p_record ?& ARRAY['artifactHash', 'logicalIdentityHash', 'payload', 'payloadHash'])
     OR (SELECT count(*) FROM jsonb_object_keys(p_record)) <> 4
     OR jsonb_typeof(p_record -> 'artifactHash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'logicalIdentityHash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_record -> 'payloadHash') IS DISTINCT FROM 'string'
     OR p_record ->> 'logicalIdentityHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'payloadHash' !~ '^[0-9a-f]{64}$'
     OR p_record ->> 'artifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_record -> 'payload') <> 'object'
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'foreign-news writer arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  payload := p_record -> 'payload';
  IF NOT (payload ?& ARRAY[
      'allowedUses', 'articleMetadataStored', 'asOf', 'contractId', 'decisionAuthority', 'lanes',
      'rawProviderDataStored', 'riskDecisionHashIncluded', 's5FeatureEligible',
      'schemaVersion', 'status', 'symbol'
    ])
    OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 12
    OR payload -> 'allowedUses' <> '["EXPLANATION_ONLY"]'::jsonb
    OR jsonb_typeof(payload -> 'articleMetadataStored') IS DISTINCT FROM 'boolean'
    OR payload -> 'articleMetadataStored' IS DISTINCT FROM 'false'::jsonb
    OR jsonb_typeof(payload -> 'asOf') IS DISTINCT FROM 'string'
    OR jsonb_typeof(payload -> 'contractId') IS DISTINCT FROM 'string'
    OR payload ->> 'contractId' <> 'foreign-news-sentiment-v1'
    OR jsonb_typeof(payload -> 'decisionAuthority') IS DISTINCT FROM 'string'
    OR payload ->> 'decisionAuthority' <> 'NONE'
    OR jsonb_typeof(payload -> 'rawProviderDataStored') IS DISTINCT FROM 'boolean'
    OR payload -> 'rawProviderDataStored' IS DISTINCT FROM 'false'::jsonb
    OR jsonb_typeof(payload -> 'riskDecisionHashIncluded') IS DISTINCT FROM 'boolean'
    OR payload -> 'riskDecisionHashIncluded' IS DISTINCT FROM 'false'::jsonb
    OR jsonb_typeof(payload -> 's5FeatureEligible') IS DISTINCT FROM 'boolean'
    OR payload -> 's5FeatureEligible' IS DISTINCT FROM 'false'::jsonb
    OR jsonb_typeof(payload -> 'schemaVersion') IS DISTINCT FROM 'number'
    OR payload -> 'schemaVersion' IS DISTINCT FROM '1'::jsonb
    OR jsonb_typeof(payload -> 'symbol') IS DISTINCT FROM 'string'
    OR payload ->> 'symbol' !~ '^[0-9A-Z._:-]{1,20}$'
    OR jsonb_typeof(payload -> 'status') IS DISTINCT FROM 'string'
    OR payload ->> 'status' NOT IN ('AVAILABLE', 'ABSTAIN')
    OR NOT public.foreign_news_lane_states_are_safe(payload -> 'lanes') THEN
    RAISE EXCEPTION 'foreign-news sanitized payload is invalid'
      USING ERRCODE = '22023';
  END IF;

  BEGIN
    PERFORM (payload ->> 'asOf')::timestamptz;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'foreign-news as-of is invalid'
      USING ERRCODE = '22023';
  END;
  SELECT EXISTS (
    SELECT 1
    FROM jsonb_array_elements(payload -> 'lanes') AS lane
    WHERE lane ->> 'state' = 'AVAILABLE'
  ) INTO has_available_lane;
  IF (payload ->> 'status' = 'AVAILABLE') IS DISTINCT FROM has_available_lane THEN
    RAISE EXCEPTION 'foreign-news availability does not match lanes'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.foreign_news_sentiment_aggregates (
    logical_identity_hash, owner_user_id, symbol, as_of, status, lane_states,
    payload_hash, artifact_hash, payload_json
  ) VALUES (
    p_record ->> 'logicalIdentityHash', p_owner_user_id, payload ->> 'symbol',
    (payload ->> 'asOf')::timestamptz, payload ->> 'status', payload -> 'lanes',
    p_record ->> 'payloadHash', p_record ->> 'artifactHash', payload
  ) ON CONFLICT (logical_identity_hash) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  IF inserted_count = 1 THEN
    RETURN 'INSERTED';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.foreign_news_sentiment_aggregates AS aggregate
    WHERE aggregate.logical_identity_hash = p_record ->> 'logicalIdentityHash'
      AND aggregate.owner_user_id = p_owner_user_id
      AND aggregate.payload_json = payload
      AND aggregate.payload_hash = p_record ->> 'payloadHash'
      AND aggregate.artifact_hash = p_record ->> 'artifactHash'
  ) THEN
    RETURN 'REPLAY';
  END IF;
  RAISE EXCEPTION 'foreign-news aggregate identity conflict'
    USING ERRCODE = '23505';
END
$append_owned_foreign_news_sentiment$;
ALTER FUNCTION append_owned_foreign_news_sentiment(text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION append_owned_foreign_news_sentiment(text, jsonb) FROM PUBLIC;

CREATE FUNCTION read_owned_foreign_news_sentiment(
  p_owner_user_id text,
  p_symbol text
)
RETURNS TABLE (payload_json jsonb)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_owned_foreign_news_sentiment$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_symbol !~ '^[0-9A-Z._:-]{1,20}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'foreign-news owner read arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT aggregate.payload_json
  FROM public.foreign_news_sentiment_aggregates AS aggregate
  WHERE aggregate.owner_user_id = p_owner_user_id
    AND aggregate.symbol = p_symbol
  ORDER BY aggregate.as_of DESC, aggregate.logical_identity_hash DESC
  LIMIT 1;
END
$read_owned_foreign_news_sentiment$;
ALTER FUNCTION read_owned_foreign_news_sentiment(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_owned_foreign_news_sentiment(text, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE foreign_news_sentiment_aggregates
FROM PUBLIC, decision_app, decision_market_writer;
REVOKE ALL PRIVILEGES ON FUNCTION
  append_owned_foreign_news_sentiment(text, jsonb),
  read_owned_foreign_news_sentiment(text, text)
FROM PUBLIC, decision_app, decision_market_writer;

DO $foreign_news_sentiment_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
    GRANT EXECUTE ON FUNCTION append_owned_foreign_news_sentiment(text, jsonb)
    TO decision_market_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION read_owned_foreign_news_sentiment(text, text)
    TO decision_app;
  END IF;
END
$foreign_news_sentiment_acl$;
