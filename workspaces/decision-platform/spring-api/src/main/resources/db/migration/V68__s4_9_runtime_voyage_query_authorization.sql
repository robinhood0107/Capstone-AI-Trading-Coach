-- S4.9 일반 사용자/MCP 질문은 Pre-S5 릴리스 검증용 수동 packet을 재사용하지 않는다.
-- 인증된 Spring 요청이 짧은 one-shot authorization을 발급하고, query writer는 그 authorization을
-- 기존 immutable Voyage usage ledger에 결속한 뒤에만 provider socket을 열 수 있다.

CREATE TABLE public.s4_9_voyage_query_authorizations (
  authorization_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  scope_claim_id text NOT NULL REFERENCES public.rag_v2_retrieval_scope_claims(scope_claim_id) ON DELETE RESTRICT,
  question_sha256 text NOT NULL,
  consent_event_id text NOT NULL,
  policy_digest text NOT NULL,
  processor_set_digest text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT s4_9_voyage_query_authorization_id_check
    CHECK (authorization_id ~ '^s49_vqa_[0-9a-f]{32}$'),
  CONSTRAINT s4_9_voyage_query_authorization_hash_check
    CHECK (
      question_sha256 ~ '^[0-9a-f]{64}$'
      AND policy_digest ~ '^[0-9a-f]{64}$'
      AND processor_set_digest ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT s4_9_voyage_query_authorization_consent_check
    CHECK (consent_event_id ~ '^rce_[A-Za-z0-9_-]{12,96}$'),
  CONSTRAINT s4_9_voyage_query_authorization_expiry_check
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes'),
  CONSTRAINT s4_9_voyage_query_authorization_scope_question_unique
    UNIQUE (scope_claim_id, question_sha256)
);

CREATE TABLE public.s4_9_voyage_query_usage_links (
  authorization_id text PRIMARY KEY
    REFERENCES public.s4_9_voyage_query_authorizations(authorization_id) ON DELETE RESTRICT,
  usage_event_id text NOT NULL UNIQUE
    REFERENCES public.rag_v2_immutable_voyage_query_usage_reservations(usage_event_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

ALTER TABLE public.s4_9_voyage_query_authorizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_voyage_query_authorizations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_voyage_query_usage_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_voyage_query_usage_links FORCE ROW LEVEL SECURITY;

CREATE POLICY s4_9_voyage_query_authorization_definer_policy
  ON public.s4_9_voyage_query_authorizations TO flyway
  USING (true) WITH CHECK (true);
CREATE POLICY s4_9_voyage_query_usage_link_definer_policy
  ON public.s4_9_voyage_query_usage_links TO flyway
  USING (true) WITH CHECK (true);

CREATE FUNCTION public.reject_s4_9_voyage_query_authorization_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_s4_9_voyage_query_authorization_mutation$
BEGIN
  RAISE EXCEPTION 'S4.9 Voyage query authorization ledger mutation is forbidden'
    USING ERRCODE = '55000';
END
$reject_s4_9_voyage_query_authorization_mutation$;
ALTER FUNCTION public.reject_s4_9_voyage_query_authorization_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reject_s4_9_voyage_query_authorization_mutation() FROM PUBLIC;

CREATE TRIGGER s4_9_voyage_query_authorizations_append_only
BEFORE UPDATE OR DELETE ON public.s4_9_voyage_query_authorizations
FOR EACH ROW EXECUTE FUNCTION public.reject_s4_9_voyage_query_authorization_mutation();
CREATE TRIGGER s4_9_voyage_query_usage_links_append_only
BEFORE UPDATE OR DELETE ON public.s4_9_voyage_query_usage_links
FOR EACH ROW EXECUTE FUNCTION public.reject_s4_9_voyage_query_authorization_mutation();

CREATE FUNCTION public.authorize_s4_9_runtime_voyage_query(
  p_owner_user_id text,
  p_scope_claim_id text,
  p_question_sha256 text
)
RETURNS TABLE (authorization_id text, expires_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $authorize_s4_9_runtime_voyage_query$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  consent_row public.rag_v2_immutable_consent_events%ROWTYPE;
  generated_authorization_id text;
  authorization_expires_at timestamptz;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_sha256 !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'S4.9 Voyage query authorization arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- FORCE RLS owner policy를 definer 내부에서도 같은 authenticated actor에 고정한다.
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS claim
  WHERE claim.scope_claim_id = p_scope_claim_id
    AND claim.owner_user_id = p_owner_user_id
    AND claim.embedding_profile_id = 'voyage_context_4_1024_v1'
    AND claim.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 Voyage query scope is unavailable' USING ERRCODE = '55000';
  END IF;

  SELECT * INTO consent_row
  FROM public.rag_v2_immutable_consent_events AS consent
  WHERE consent.owner_user_id = p_owner_user_id
    AND consent.public_consent_event_id IS NOT NULL
    AND consent.policy_digest IS NOT NULL
    AND consent.processor_set_digest IS NOT NULL
  ORDER BY consent.created_at DESC, consent.consent_event_id DESC
  LIMIT 1;
  IF NOT FOUND OR consent_row.action <> 'GRANT' THEN
    RAISE EXCEPTION 'S4.9 Voyage query requires effective consent' USING ERRCODE = '55000';
  END IF;

  generated_authorization_id :=
    's49_vqa_' || substr(
      encode(
        digest(
          gen_random_bytes(32) || convert_to(
            concat_ws(E'\n', p_owner_user_id, p_scope_claim_id, p_question_sha256, statement_timestamp()::text),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );
  authorization_expires_at := least(claim_row.expires_at, statement_timestamp() + interval '2 minutes');

  INSERT INTO public.s4_9_voyage_query_authorizations (
    authorization_id, owner_user_id, scope_claim_id, question_sha256,
    consent_event_id, policy_digest, processor_set_digest, expires_at
  ) VALUES (
    generated_authorization_id, p_owner_user_id, p_scope_claim_id, p_question_sha256,
    consent_row.public_consent_event_id, consent_row.policy_digest,
    consent_row.processor_set_digest, authorization_expires_at
  );

  RETURN QUERY SELECT generated_authorization_id, authorization_expires_at;
END
$authorize_s4_9_runtime_voyage_query$;
ALTER FUNCTION public.authorize_s4_9_runtime_voyage_query(text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.authorize_s4_9_runtime_voyage_query(text,text,text) FROM PUBLIC;

CREATE FUNCTION public.reserve_s4_9_runtime_voyage_query_usage(
  p_scope_claim_id text,
  p_question_sha256 text,
  p_official_tokenizer_sha256 text
)
RETURNS TABLE (
  usage_event_id text,
  packet_sha256 text,
  nonce_sha256 text,
  rate_evidence_sha256 text,
  expires_at timestamptz,
  token_cap integer,
  byte_cap integer,
  cost_cap_microusd bigint,
  input_microusd_per_token bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_s4_9_runtime_voyage_query_usage$
#variable_conflict use_column
DECLARE
  authorization_row public.s4_9_voyage_query_authorizations%ROWTYPE;
  active_tokenizer_sha256 text;
  generated_usage_event_id text;
  generated_packet_sha256 text;
  generated_nonce_sha256 text;
  generated_rate_evidence_sha256 text;
  reservation_expires_at timestamptz;
  fixed_token_cap constant integer := 8192;
  fixed_byte_cap constant integer := 4194304;
  fixed_cost_cap_microusd constant bigint := 8192;
  fixed_input_microusd_per_token constant bigint := 1;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_sha256 !~ '^[0-9a-f]{64}$'
     OR p_official_tokenizer_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S4.9 Voyage query reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT * INTO authorization_row
  FROM public.s4_9_voyage_query_authorizations AS authorized_query
  WHERE authorized_query.scope_claim_id = p_scope_claim_id
    AND authorized_query.question_sha256 = p_question_sha256
    AND authorized_query.expires_at > statement_timestamp()
  FOR UPDATE;
  IF NOT FOUND OR EXISTS (
    SELECT 1 FROM public.s4_9_voyage_query_usage_links AS link
    WHERE link.authorization_id = authorization_row.authorization_id
  ) THEN
    RAISE EXCEPTION 'S4.9 Voyage query authorization is unavailable' USING ERRCODE = '55000';
  END IF;

  -- query writer는 owner ID를 입력으로 받지 않으므로 one-shot authorization의 owner를 RLS actor로 복원한다.
  PERFORM set_config('app.actor_user_id', authorization_row.owner_user_id, true);
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_retrieval_scope_claims AS claim
    WHERE claim.scope_claim_id = p_scope_claim_id
      AND claim.owner_user_id = authorization_row.owner_user_id
      AND claim.embedding_profile_id = 'voyage_context_4_1024_v1'
      AND claim.expires_at > statement_timestamp()
  ) THEN
    RAISE EXCEPTION 'S4.9 Voyage query scope is unavailable' USING ERRCODE = '55000';
  END IF;

  SELECT plan.official_tokenizer_sha256 INTO active_tokenizer_sha256
  FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
  WHERE plan.state = 'COMPLETE'
    AND plan.embedding_profile_id = 'voyage_context_4_1024_v1'
  ORDER BY plan.completed_at DESC, plan.created_at DESC
  LIMIT 1;
  IF active_tokenizer_sha256 IS DISTINCT FROM p_official_tokenizer_sha256 THEN
    RAISE EXCEPTION 'S4.9 Voyage query tokenizer is unavailable' USING ERRCODE = '55000';
  END IF;

  generated_packet_sha256 := encode(
    digest(convert_to('s4.9-runtime-voyage-query-packet-v1' || authorization_row.authorization_id, 'UTF8'), 'sha256'),
    'hex'
  );
  generated_nonce_sha256 := encode(digest(gen_random_bytes(32), 'sha256'), 'hex');
  generated_rate_evidence_sha256 := encode(
    digest(convert_to('s4.9-runtime-voyage-query-conservative-cap-v1', 'UTF8'), 'sha256'),
    'hex'
  );
  generated_usage_event_id :=
    'rgr_vqu_' || substr(
      encode(
        digest(
          convert_to(
            authorization_row.authorization_id || generated_packet_sha256 || generated_nonce_sha256,
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );
  reservation_expires_at := least(authorization_row.expires_at, statement_timestamp() + interval '2 minutes');

  INSERT INTO public.rag_v2_immutable_voyage_query_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, query_sha256, scope_claim_sha256,
    rate_evidence_sha256, official_tokenizer_sha256, evaluation_component_scope,
    expires_at, token_cap, byte_cap, cost_cap_microusd, input_microusd_per_token
  ) VALUES (
    generated_usage_event_id, generated_packet_sha256, generated_nonce_sha256, p_question_sha256,
    encode(digest(convert_to(p_scope_claim_id, 'UTF8'), 'sha256'), 'hex'),
    generated_rate_evidence_sha256, p_official_tokenizer_sha256, 'RUNTIME',
    reservation_expires_at, fixed_token_cap, fixed_byte_cap,
    fixed_cost_cap_microusd, fixed_input_microusd_per_token
  );
  INSERT INTO public.s4_9_voyage_query_usage_links (authorization_id, usage_event_id)
  VALUES (authorization_row.authorization_id, generated_usage_event_id);

  RETURN QUERY SELECT
    generated_usage_event_id,
    generated_packet_sha256,
    generated_nonce_sha256,
    generated_rate_evidence_sha256,
    reservation_expires_at,
    fixed_token_cap,
    fixed_byte_cap,
    fixed_cost_cap_microusd,
    fixed_input_microusd_per_token;
END
$reserve_s4_9_runtime_voyage_query_usage$;
ALTER FUNCTION public.reserve_s4_9_runtime_voyage_query_usage(text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_s4_9_runtime_voyage_query_usage(text,text,text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  public.s4_9_voyage_query_authorizations,
  public.s4_9_voyage_query_usage_links
FROM PUBLIC;

DO $s4_9_runtime_voyage_query_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      public.s4_9_voyage_query_authorizations,
      public.s4_9_voyage_query_usage_links
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION public.authorize_s4_9_runtime_voyage_query(text,text,text)
      TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      public.s4_9_voyage_query_authorizations,
      public.s4_9_voyage_query_usage_links
    FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION public.reserve_s4_9_runtime_voyage_query_usage(text,text,text)
      TO decision_rag_writer;
  END IF;
END
$s4_9_runtime_voyage_query_acl$;
