-- V25가 이미 적용된 database에서도 owner별 consent 순서와 created_at 순서를 일치시킨다.
CREATE OR REPLACE FUNCTION record_rag_v2_immutable_consent(
  p_owner_user_id text,
  p_consent_event_id text,
  p_action text,
  p_disclosure_digest text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_rag_v2_immutable_consent$
DECLARE
  recorded_at timestamptz;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_consent_event_id !~ '^cns_v2_[0-9a-f]{32}$'
     OR p_action NOT IN ('GRANT', 'REVOKE')
     OR p_disclosure_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 consent arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-consent|' || p_owner_user_id, 0)
  );
  recorded_at := clock_timestamp();
  INSERT INTO public.rag_v2_immutable_consent_events (
    consent_event_id,
    owner_user_id,
    action,
    policy_version,
    disclosure_digest,
    created_at
  )
  VALUES (
    p_consent_event_id,
    p_owner_user_id,
    p_action,
    'EXTERNAL_AI_RAG_V2',
    p_disclosure_digest,
    recorded_at
  );
  RETURN recorded_at;
END;
$record_rag_v2_immutable_consent$;

ALTER FUNCTION record_rag_v2_immutable_consent(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_immutable_consent(text, text, text, text) FROM PUBLIC;

DO $rag_v2_legacy_consent_ordering_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION record_rag_v2_immutable_consent(text, text, text, text) TO decision_app;
  END IF;
END;
$rag_v2_legacy_consent_ordering_acl$;
