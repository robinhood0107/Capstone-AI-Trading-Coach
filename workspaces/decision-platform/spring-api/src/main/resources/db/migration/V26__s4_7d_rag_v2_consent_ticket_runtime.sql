-- V25의 immutable event/ticket 저장 경계를 유지한 채 public v2 consent contract만 추가한다.
-- legacy V25 event는 metadata가 모두 NULL인 historical row로 남기고 변경하거나 재해석하지 않는다.
ALTER TABLE rag_v2_immutable_consent_events
  ADD COLUMN public_consent_event_id text,
  ADD COLUMN policy_digest text,
  ADD COLUMN processor_set_digest text;

ALTER TABLE rag_v2_immutable_consent_events
  ADD CONSTRAINT rag_v2_immutable_consent_public_contract_check
  CHECK (
    (
      public_consent_event_id IS NULL
      AND policy_digest IS NULL
      AND processor_set_digest IS NULL
    )
    OR (
      public_consent_event_id ~ '^rce_[A-Za-z0-9_-]{12,96}$'
      AND policy_digest ~ '^[0-9a-f]{64}$'
      AND processor_set_digest ~ '^[0-9a-f]{64}$'
    )
  );

CREATE UNIQUE INDEX rag_v2_immutable_consent_public_event_unique_idx
  ON rag_v2_immutable_consent_events (public_consent_event_id)
  WHERE public_consent_event_id IS NOT NULL;

CREATE FUNCTION record_rag_v2_immutable_consent_v2(
  p_owner_user_id text,
  p_internal_consent_event_id text,
  p_public_consent_event_id text,
  p_action text,
  p_disclosure_digest text,
  p_policy_digest text,
  p_processor_set_digest text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_rag_v2_immutable_consent_v2$
DECLARE
  recorded_at timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_internal_consent_event_id !~ '^cns_v2_[0-9a-f]{32}$'
     OR p_public_consent_event_id !~ '^rce_[A-Za-z0-9_-]{12,96}$'
     OR p_action NOT IN ('GRANT', 'REVOKE')
     OR p_disclosure_digest !~ '^[0-9a-f]{64}$'
     OR p_policy_digest !~ '^[0-9a-f]{64}$'
     OR p_processor_set_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 consent control-plane arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-consent|' || p_owner_user_id, 0)
  );
  INSERT INTO public.rag_v2_immutable_consent_events (
    consent_event_id,
    owner_user_id,
    action,
    policy_version,
    disclosure_digest,
    public_consent_event_id,
    policy_digest,
    processor_set_digest,
    created_at
  )
  VALUES (
    p_internal_consent_event_id,
    p_owner_user_id,
    p_action,
    'EXTERNAL_AI_RAG_V2',
    p_disclosure_digest,
    p_public_consent_event_id,
    p_policy_digest,
    p_processor_set_digest,
    recorded_at
  );
  RETURN recorded_at;
END;
$record_rag_v2_immutable_consent_v2$;
ALTER FUNCTION record_rag_v2_immutable_consent_v2(text, text, text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_immutable_consent_v2(text, text, text, text, text, text, text) FROM PUBLIC;

CREATE FUNCTION read_rag_v2_immutable_effective_consent(
  p_owner_user_id text
)
RETURNS TABLE (
  consent_event_id text,
  action text,
  policy_digest text,
  processor_set_digest text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_immutable_effective_consent$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 consent read arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    event.public_consent_event_id,
    event.action,
    event.policy_digest,
    event.processor_set_digest
  FROM public.rag_v2_immutable_consent_events AS event
  WHERE event.owner_user_id = p_owner_user_id
    AND event.public_consent_event_id IS NOT NULL
    AND event.policy_digest IS NOT NULL
    AND event.processor_set_digest IS NOT NULL
  ORDER BY event.created_at DESC, event.consent_event_id DESC
  LIMIT 1;
END;
$read_rag_v2_immutable_effective_consent$;
ALTER FUNCTION read_rag_v2_immutable_effective_consent(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_immutable_effective_consent(text) FROM PUBLIC;

DO $rag_v2_consent_control_plane_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION record_rag_v2_immutable_consent_v2(text, text, text, text, text, text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION read_rag_v2_immutable_effective_consent(text) TO decision_app;
  END IF;
END;
$rag_v2_consent_control_plane_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_immutable_consent_v2(text, text, text, text, text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_immutable_effective_consent(text) FROM PUBLIC;
