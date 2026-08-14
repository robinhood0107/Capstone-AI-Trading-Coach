-- Pre-S5 exact approval 왕복이 가능한 5분 provider preparation scope를 별도 v3 issuer로 제한한다.
ALTER TABLE public.rag_v2_retrieval_scope_claims
  DROP CONSTRAINT rag_v2_retrieval_scope_expiry_check;
ALTER TABLE public.rag_v2_retrieval_scope_claims
  ADD CONSTRAINT rag_v2_retrieval_scope_expiry_check
  CHECK (
    expires_at = created_at + interval '2 minutes'
    OR expires_at = created_at + interval '5 minutes'
  );

CREATE POLICY rag_v2_retrieval_scope_claim_provider_ttl_update_policy
  ON public.rag_v2_retrieval_scope_claims
  FOR UPDATE
  USING (
    current_user = 'flyway'
    AND owner_user_id = current_setting('app.actor_user_id', true)
    AND expires_at = created_at + interval '2 minutes'
  )
  WITH CHECK (
    current_user = 'flyway'
    AND owner_user_id = current_setting('app.actor_user_id', true)
    AND expires_at = created_at + interval '5 minutes'
  );

CREATE FUNCTION public.issue_rag_v2_retrieval_scope_v3(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics text[]
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  owner_embedding_profile_id text,
  policy_version bigint,
  allowed_topics text[],
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_retrieval_scope_v3$
#variable_conflict use_column
DECLARE
  issued record;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope caller is invalid'
      USING ERRCODE = '42501';
  END IF;

  SELECT * INTO STRICT issued
  FROM public.issue_rag_v2_retrieval_scope_v2(
    p_owner_user_id,
    p_session_id,
    p_allowed_topics
  );

  RETURN QUERY
  UPDATE public.rag_v2_retrieval_scope_claims AS claim
  SET expires_at = claim.created_at + interval '5 minutes'
  WHERE claim.scope_claim_id = issued.scope_claim_id
    AND claim.owner_user_id = p_owner_user_id
    AND claim.session_id = p_session_id
    AND claim.expires_at = claim.created_at + interval '2 minutes'
  RETURNING
    claim.scope_claim_id,
    claim.owner_user_id,
    claim.session_id,
    claim.exact30_generation_id,
    claim.oa112_generation_id,
    claim.owner_private_generation_id,
    claim.embedding_profile_id,
    claim.owner_embedding_profile_id,
    claim.policy_version,
    claim.allowed_topics,
    claim.expires_at;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 provider retrieval scope extension failed'
      USING ERRCODE = '55000';
  END IF;
END;
$issue_rag_v2_retrieval_scope_v3$;
ALTER FUNCTION public.issue_rag_v2_retrieval_scope_v3(text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.issue_rag_v2_retrieval_scope_v3(text, text, text[]) FROM PUBLIC;

DO $pre_s5_retrieval_scope_ttl_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.issue_rag_v2_retrieval_scope_v3(text, text, text[])
      TO decision_app;
  END IF;
END;
$pre_s5_retrieval_scope_ttl_acl$;

REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM decision_app;
