-- S4.9는 기존 RAG/Vertex 원장을 보존하면서 OAuth2.1 MCP와 provider-neutral Strong LLM metadata만 추가한다.
-- authorization code, refresh token, validation receipt는 원문 대신 SHA-256 hash만 저장한다.

CREATE TABLE public.s4_9_mcp_oauth_clients (
  client_id text PRIMARY KEY,
  client_name text NOT NULL,
  metadata_sha256 text NOT NULL,
  redirect_uris text[] NOT NULL,
  allowed_scopes text[] NOT NULL,
  client_kind text NOT NULL CHECK (client_kind IN ('STATIC_ALLOWLIST', 'CIMD_VERIFIED')),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (client_id ~ '^mcp_[a-z0-9][a-z0-9._-]{2,95}$'
    OR client_id ~ '^https://[A-Za-z0-9.-]+/[A-Za-z0-9._~!$&''()*+,;=:@%/-]{1,190}$'),
  CHECK (metadata_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (cardinality(redirect_uris) BETWEEN 1 AND 8),
  CHECK (cardinality(allowed_scopes) BETWEEN 1 AND 5),
  CHECK (allowed_scopes <@ ARRAY['mcp:rag.public','mcp:rag.owner','mcp:web.read','mcp:answer.validate','mcp:history.write']::text[])
);

CREATE TABLE public.s4_9_mcp_oauth_authorization_codes (
  code_sha256 text PRIMARY KEY,
  client_id text NOT NULL REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  security_version bigint NOT NULL CHECK (security_version > 0),
  redirect_uri text NOT NULL,
  resource_uri text NOT NULL CHECK (resource_uri ~ '^https://[^[:space:]]+/mcp$|^http://(?:127[.]0[.]0[.]1|localhost)(?::[0-9]{1,5})?/mcp$'),
  scopes text[] NOT NULL,
  code_challenge text NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43,128}$'),
  code_challenge_method text NOT NULL DEFAULT 'S256' CHECK (code_challenge_method = 'S256'),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (owner_user_id ~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'),
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes')
);

CREATE TABLE public.s4_9_mcp_oauth_refresh_tokens (
  token_sha256 text PRIMARY KEY,
  token_family_id text NOT NULL,
  client_id text NOT NULL REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  security_version bigint NOT NULL CHECK (security_version > 0),
  resource_uri text NOT NULL,
  scopes text[] NOT NULL,
  previous_token_sha256 text REFERENCES public.s4_9_mcp_oauth_refresh_tokens(token_sha256),
  expires_at timestamptz NOT NULL,
  rotated_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (token_sha256 ~ '^[0-9a-f]{64}$' AND token_family_id ~ '^mrf_[0-9a-f]{32}$'),
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '7 days')
);
CREATE UNIQUE INDEX s4_9_mcp_refresh_active_family_unique
  ON public.s4_9_mcp_oauth_refresh_tokens(token_family_id)
  WHERE rotated_at IS NULL AND revoked_at IS NULL;

CREATE TABLE public.s4_9_strong_llm_usage_ledger (
  usage_event_id text PRIMARY KEY,
  owner_user_id text REFERENCES public.users(user_id) ON DELETE SET NULL,
  oauth_client_id text REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  request_id text NOT NULL,
  provider text NOT NULL,
  model_id text NOT NULL,
  answer_basis text CHECK (answer_basis IN ('EVIDENCE','MODEL_KNOWLEDGE','INSUFFICIENT_EVIDENCE')),
  outcome text NOT NULL CHECK (outcome IN ('COMMITTED','REJECTED','UNKNOWN_BILLING')),
  tool_round_count integer NOT NULL CHECK (tool_round_count BETWEEN 0 AND 3),
  search_call_count integer NOT NULL CHECK (search_call_count BETWEEN 0 AND 3),
  read_call_count integer NOT NULL CHECK (read_call_count BETWEEN 0 AND 8),
  prompt_token_count integer,
  output_token_count integer,
  evidence_set_sha256 text NOT NULL,
  raw_request_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_request_stored),
  raw_response_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_response_stored),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (usage_event_id ~ '^s49_llu_[0-9a-f]{32}$'),
  CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$'),
  CHECK (evidence_set_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE public.s4_9_web_evidence_metadata (
  evidence_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  oauth_client_id text REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  research_context_id text NOT NULL,
  canonical_url text NOT NULL CHECK (canonical_url ~ '^https://'),
  title text NOT NULL,
  section_locator text,
  retrieved_at timestamptz NOT NULL,
  content_sha256 text NOT NULL,
  expires_at timestamptz NOT NULL,
  raw_body_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_body_stored),
  CHECK (evidence_id ~ '^s49_web_[0-9a-f]{32}$'),
  CHECK (research_context_id ~ '^s49_ctx_[0-9a-f]{32}$'),
  CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (expires_at > retrieved_at)
);

CREATE TABLE public.s4_9_answer_validation_receipts (
  receipt_sha256 text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  oauth_client_id text NOT NULL REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  research_context_id text NOT NULL,
  source_set_sha256 text NOT NULL,
  draft_sha256 text NOT NULL,
  validation_status text NOT NULL CHECK (validation_status IN ('VALID','VALID_WITH_WARNINGS')),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$' AND source_set_sha256 ~ '^[0-9a-f]{64}$'
    AND draft_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (expires_at > created_at AND expires_at <= created_at + interval '5 minutes')
);

CREATE TABLE public.s4_9_saved_answer_history (
  answer_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  oauth_client_id text NOT NULL REFERENCES public.s4_9_mcp_oauth_clients(client_id),
  receipt_sha256 text NOT NULL UNIQUE REFERENCES public.s4_9_answer_validation_receipts(receipt_sha256),
  research_context_id text NOT NULL,
  source_set_sha256 text NOT NULL,
  validation_status text NOT NULL CHECK (validation_status IN ('VALID','VALID_WITH_WARNINGS')),
  kek_version text NOT NULL,
  wrap_nonce bytea NOT NULL,
  wrapped_dek bytea NOT NULL,
  wrap_tag bytea NOT NULL,
  question_nonce bytea NOT NULL,
  question_ciphertext bytea NOT NULL,
  question_tag bytea NOT NULL,
  answer_nonce bytea NOT NULL,
  answer_ciphertext bytea NOT NULL,
  answer_tag bytea NOT NULL,
  raw_question_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_question_stored),
  raw_answer_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_answer_stored),
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CHECK (answer_id ~ '^rag_[A-Za-z0-9_-]{12,96}$'),
  CHECK (research_context_id ~ '^s49_ctx_[0-9a-f]{32}$' AND source_set_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (kek_version ~ '^kek-v[1-9][0-9]{0,8}$' AND octet_length(wrap_nonce) = 12
    AND octet_length(wrapped_dek) = 32 AND octet_length(wrap_tag) = 16
    AND octet_length(question_nonce) = 12 AND octet_length(question_ciphertext) BETWEEN 1 AND 8192
    AND octet_length(question_tag) = 16 AND octet_length(answer_nonce) = 12
    AND octet_length(answer_ciphertext) BETWEEN 1 AND 8192 AND octet_length(answer_tag) = 16),
  CHECK (expires_at = created_at + interval '30 days')
);

-- S4.9 일반 교육 답변은 citation 없이도 ANSWERED가 될 수 있지만 명시 flag와 coverage=0을 강제한다.
ALTER TABLE public.rag_v2_answer_history
  DROP CONSTRAINT rag_v2_answer_history_status_result_check;
ALTER TABLE public.rag_v2_answer_history
  ADD CONSTRAINT rag_v2_answer_history_status_result_check
  CHECK (
    (
      generation_status = 'ANSWERED'
      AND NOT retrieval_failure
      AND (
        (citation_count BETWEEN 1 AND 5 AND citation_coverage >= 0.8)
        OR (
          citation_count = 0 AND citation_coverage = 0.0
          AND guardrail_flags = ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]
        )
      )
    )
    OR (
      generation_status = 'RETRIEVAL_ONLY'
      AND citation_count BETWEEN 0 AND 5
      AND NOT retrieval_failure
    )
    OR (
      generation_status = 'RETRIEVAL_FAILURE'
      AND citation_count = 0
      AND citation_coverage = 0.0
      AND retrieval_failure
    )
  );

ALTER TABLE public.s4_9_mcp_oauth_authorization_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_mcp_oauth_authorization_codes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_mcp_oauth_refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_mcp_oauth_refresh_tokens FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_strong_llm_usage_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_strong_llm_usage_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_web_evidence_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_web_evidence_metadata FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_answer_validation_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_answer_validation_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_saved_answer_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_saved_answer_history FORCE ROW LEVEL SECURITY;

CREATE POLICY s4_9_oauth_code_definer_policy ON public.s4_9_mcp_oauth_authorization_codes
  TO flyway USING (true) WITH CHECK (true);
CREATE POLICY s4_9_oauth_refresh_definer_policy ON public.s4_9_mcp_oauth_refresh_tokens
  TO flyway USING (true) WITH CHECK (true);
CREATE POLICY s4_9_usage_definer_policy ON public.s4_9_strong_llm_usage_ledger
  TO flyway USING (true) WITH CHECK (true);
CREATE POLICY s4_9_web_owner_policy ON public.s4_9_web_evidence_metadata
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_validation_owner_policy ON public.s4_9_answer_validation_receipts
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_saved_history_owner_policy ON public.s4_9_saved_answer_history
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));

CREATE FUNCTION public.record_s4_9_web_evidence_metadata(
  p_evidence_id text, p_owner_user_id text, p_oauth_client_id text, p_research_context_id text,
  p_canonical_url text, p_title text, p_section_locator text, p_retrieved_at timestamptz,
  p_content_sha256 text, p_expires_at timestamptz
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_web_evidence_metadata$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_evidence_id !~ '^s49_web_[0-9a-f]{32}$' OR p_research_context_id !~ '^s49_ctx_[0-9a-f]{32}$'
     OR p_canonical_url !~ '^https://' OR octet_length(p_canonical_url) NOT BETWEEN 9 AND 2048
     OR char_length(p_title) NOT BETWEEN 1 AND 500 OR p_content_sha256 !~ '^[0-9a-f]{64}$'
     OR p_retrieved_at NOT BETWEEN transaction_timestamp() - interval '60 seconds' AND transaction_timestamp() + interval '60 seconds'
     OR p_expires_at <= p_retrieved_at OR p_expires_at > p_retrieved_at + interval '24 hours'
     OR (p_oauth_client_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM public.s4_9_mcp_oauth_clients c WHERE c.client_id = p_oauth_client_id AND c.status = 'ACTIVE'
     )) THEN
    RAISE EXCEPTION 'S4.9 web evidence metadata is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_web_evidence_metadata (
    evidence_id, owner_user_id, oauth_client_id, research_context_id, canonical_url, title,
    section_locator, retrieved_at, content_sha256, expires_at
  ) VALUES (
    p_evidence_id, p_owner_user_id, p_oauth_client_id, p_research_context_id, p_canonical_url, p_title,
    p_section_locator, p_retrieved_at, p_content_sha256, p_expires_at
  ) ON CONFLICT (evidence_id) DO NOTHING;
END
$record_s4_9_web_evidence_metadata$;
ALTER FUNCTION public.record_s4_9_web_evidence_metadata(text,text,text,text,text,text,text,timestamptz,text,timestamptz) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_web_evidence_metadata(text,text,text,text,text,text,text,timestamptz,text,timestamptz) FROM PUBLIC;

CREATE FUNCTION public.sync_s4_9_mcp_oauth_client(
  p_client_id text, p_client_name text, p_metadata_sha256 text,
  p_redirect_uris text[], p_allowed_scopes text[], p_client_kind text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $sync_s4_9_mcp_oauth_client$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_metadata_sha256 !~ '^[0-9a-f]{64}$' OR p_client_kind NOT IN ('STATIC_ALLOWLIST','CIMD_VERIFIED')
     OR cardinality(p_redirect_uris) NOT BETWEEN 1 AND 8 OR cardinality(p_allowed_scopes) NOT BETWEEN 1 AND 5
     OR NOT p_allowed_scopes <@ ARRAY['mcp:rag.public','mcp:rag.owner','mcp:web.read','mcp:answer.validate','mcp:history.write']::text[]
     OR EXISTS (SELECT 1 FROM unnest(p_redirect_uris) u WHERE u !~ '^https://'
       AND u !~ '^http://(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?/') THEN
    RAISE EXCEPTION 'S4.9 MCP client metadata is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_mcp_oauth_clients (
    client_id, client_name, metadata_sha256, redirect_uris, allowed_scopes, client_kind
  ) VALUES (p_client_id, p_client_name, p_metadata_sha256, p_redirect_uris, p_allowed_scopes, p_client_kind)
  ON CONFLICT (client_id) DO UPDATE SET
    client_name = EXCLUDED.client_name,
    metadata_sha256 = EXCLUDED.metadata_sha256,
    redirect_uris = EXCLUDED.redirect_uris,
    allowed_scopes = EXCLUDED.allowed_scopes,
    client_kind = EXCLUDED.client_kind,
    status = 'ACTIVE',
    updated_at = transaction_timestamp();
END
$sync_s4_9_mcp_oauth_client$;
ALTER FUNCTION public.sync_s4_9_mcp_oauth_client(text,text,text,text[],text[],text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.sync_s4_9_mcp_oauth_client(text,text,text,text[],text[],text) FROM PUBLIC;

CREATE FUNCTION public.upsert_s4_9_mcp_oauth_code_hash(
  p_code_sha256 text, p_client_id text, p_owner_user_id text, p_security_version bigint,
  p_redirect_uri text, p_resource_uri text, p_scopes text[], p_code_challenge text,
  p_expires_at timestamptz
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $upsert_s4_9_mcp_oauth_code_hash$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_code_sha256 !~ '^[0-9a-f]{64}$' OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_security_version <= 0 OR p_code_challenge !~ '^[A-Za-z0-9_-]{43,128}$'
     OR p_expires_at <= transaction_timestamp() OR p_expires_at > transaction_timestamp() + interval '5 minutes'
     OR p_resource_uri !~ '^https://[^[:space:]]+/mcp$|^http://(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?/mcp$'
     OR NOT p_scopes <@ ARRAY['mcp:rag.public','mcp:rag.owner','mcp:web.read','mcp:answer.validate','mcp:history.write']::text[] THEN
    RAISE EXCEPTION 'S4.9 authorization code hash is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_mcp_oauth_authorization_codes (
    code_sha256, client_id, owner_user_id, security_version, redirect_uri, resource_uri,
    scopes, code_challenge, expires_at
  ) VALUES (
    p_code_sha256, p_client_id, p_owner_user_id, p_security_version, p_redirect_uri, p_resource_uri,
    p_scopes, p_code_challenge, p_expires_at
  ) ON CONFLICT (code_sha256) DO NOTHING;
END
$upsert_s4_9_mcp_oauth_code_hash$;
ALTER FUNCTION public.upsert_s4_9_mcp_oauth_code_hash(text,text,text,bigint,text,text,text[],text,timestamptz) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.upsert_s4_9_mcp_oauth_code_hash(text,text,text,bigint,text,text,text[],text,timestamptz) FROM PUBLIC;

CREATE FUNCTION public.consume_s4_9_mcp_oauth_code_hash(p_code_sha256 text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $consume_s4_9_mcp_oauth_code_hash$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app' OR p_code_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S4.9 authorization code hash is invalid' USING ERRCODE = '22023';
  END IF;
  UPDATE public.s4_9_mcp_oauth_authorization_codes
  SET consumed_at = transaction_timestamp()
  WHERE code_sha256 = p_code_sha256 AND consumed_at IS NULL;
  IF NOT FOUND THEN
    IF EXISTS (SELECT 1 FROM public.s4_9_mcp_oauth_authorization_codes WHERE code_sha256 = p_code_sha256) THEN
      RETURN;
    END IF;
    RAISE EXCEPTION 'S4.9 authorization code hash is unavailable' USING ERRCODE = '55000';
  END IF;
END
$consume_s4_9_mcp_oauth_code_hash$;
ALTER FUNCTION public.consume_s4_9_mcp_oauth_code_hash(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.consume_s4_9_mcp_oauth_code_hash(text) FROM PUBLIC;

CREATE FUNCTION public.rotate_s4_9_mcp_refresh_token_hash(
  p_token_sha256 text, p_client_id text, p_owner_user_id text, p_security_version bigint,
  p_resource_uri text, p_scopes text[], p_expires_at timestamptz
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $rotate_s4_9_mcp_refresh_token_hash$
DECLARE family_id text;
DECLARE previous_hash text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_token_sha256 !~ '^[0-9a-f]{64}$' OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_security_version <= 0 OR p_expires_at <= transaction_timestamp()
     OR p_expires_at > transaction_timestamp() + interval '7 days 1 minute'
     OR NOT p_scopes <@ ARRAY['mcp:rag.public','mcp:rag.owner','mcp:web.read','mcp:answer.validate','mcp:history.write']::text[] THEN
    RAISE EXCEPTION 'S4.9 refresh token hash is invalid' USING ERRCODE = '22023';
  END IF;
  IF EXISTS (SELECT 1 FROM public.s4_9_mcp_oauth_refresh_tokens WHERE token_sha256 = p_token_sha256) THEN
    RETURN;
  END IF;
  family_id := 'mrf_' || substr(encode(digest(p_client_id || ':' || p_owner_user_id, 'sha256'), 'hex'), 1, 32);
  SELECT token_sha256 INTO previous_hash FROM public.s4_9_mcp_oauth_refresh_tokens
  WHERE token_family_id = family_id AND rotated_at IS NULL AND revoked_at IS NULL FOR UPDATE;
  IF FOUND THEN
    UPDATE public.s4_9_mcp_oauth_refresh_tokens SET rotated_at = transaction_timestamp()
    WHERE token_sha256 = previous_hash;
  END IF;
  INSERT INTO public.s4_9_mcp_oauth_refresh_tokens (
    token_sha256, token_family_id, client_id, owner_user_id, security_version,
    resource_uri, scopes, previous_token_sha256, expires_at
  ) VALUES (
    p_token_sha256, family_id, p_client_id, p_owner_user_id, p_security_version,
    p_resource_uri, p_scopes, previous_hash, p_expires_at
  );
END
$rotate_s4_9_mcp_refresh_token_hash$;
ALTER FUNCTION public.rotate_s4_9_mcp_refresh_token_hash(text,text,text,bigint,text,text[],timestamptz) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.rotate_s4_9_mcp_refresh_token_hash(text,text,text,bigint,text,text[],timestamptz) FROM PUBLIC;

CREATE FUNCTION public.revoke_s4_9_mcp_refresh_token_family(p_token_sha256 text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $revoke_s4_9_mcp_refresh_token_family$
DECLARE family text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app' OR p_token_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S4.9 refresh token hash is invalid' USING ERRCODE = '22023';
  END IF;
  SELECT token_family_id INTO family FROM public.s4_9_mcp_oauth_refresh_tokens
  WHERE token_sha256 = p_token_sha256 FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 refresh token hash is unavailable' USING ERRCODE = '55000';
  END IF;
  UPDATE public.s4_9_mcp_oauth_refresh_tokens
  SET revoked_at = coalesce(revoked_at, transaction_timestamp())
  WHERE token_family_id = family;
END
$revoke_s4_9_mcp_refresh_token_family$;
ALTER FUNCTION public.revoke_s4_9_mcp_refresh_token_family(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.revoke_s4_9_mcp_refresh_token_family(text) FROM PUBLIC;

CREATE FUNCTION public.issue_s4_9_answer_validation_receipt(
  p_receipt_sha256 text, p_owner_user_id text, p_oauth_client_id text,
  p_research_context_id text, p_source_set_sha256 text, p_draft_sha256 text,
  p_validation_status text, p_expires_at timestamptz
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_s4_9_answer_validation_receipt$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_receipt_sha256 !~ '^[0-9a-f]{64}$' OR p_source_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_draft_sha256 !~ '^[0-9a-f]{64}$' OR p_research_context_id !~ '^s49_ctx_[0-9a-f]{32}$'
     OR p_validation_status NOT IN ('VALID','VALID_WITH_WARNINGS')
     OR p_expires_at <= transaction_timestamp() OR p_expires_at > transaction_timestamp() + interval '5 minutes'
     OR NOT EXISTS (SELECT 1 FROM public.s4_9_mcp_oauth_clients c
                    WHERE c.client_id = p_oauth_client_id AND c.status = 'ACTIVE') THEN
    RAISE EXCEPTION 'S4.9 validation receipt arguments are invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_answer_validation_receipts (
    receipt_sha256, owner_user_id, oauth_client_id, research_context_id, source_set_sha256,
    draft_sha256, validation_status, expires_at
  ) VALUES (
    p_receipt_sha256, p_owner_user_id, p_oauth_client_id, p_research_context_id, p_source_set_sha256,
    p_draft_sha256, p_validation_status, p_expires_at
  );
END
$issue_s4_9_answer_validation_receipt$;
ALTER FUNCTION public.issue_s4_9_answer_validation_receipt(text,text,text,text,text,text,text,timestamptz) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.issue_s4_9_answer_validation_receipt(text,text,text,text,text,text,text,timestamptz) FROM PUBLIC;

CREATE FUNCTION public.consume_s4_9_validation_and_save_history(
  p_receipt_sha256 text, p_owner_user_id text, p_oauth_client_id text, p_answer_id text,
  p_draft_sha256 text, p_kek_version text, p_wrap_nonce bytea, p_wrapped_dek bytea, p_wrap_tag bytea,
  p_question_nonce bytea, p_question_ciphertext bytea, p_question_tag bytea,
  p_answer_nonce bytea, p_answer_ciphertext bytea, p_answer_tag bytea, p_created_at timestamptz
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $consume_s4_9_validation_and_save_history$
DECLARE receipt_row public.s4_9_answer_validation_receipts%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_receipt_sha256 !~ '^[0-9a-f]{64}$' OR p_draft_sha256 !~ '^[0-9a-f]{64}$'
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds' AND transaction_timestamp() + interval '60 seconds'
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$' OR octet_length(p_wrap_nonce) <> 12
     OR octet_length(p_wrapped_dek) <> 32 OR octet_length(p_wrap_tag) <> 16
     OR octet_length(p_question_nonce) <> 12 OR octet_length(p_question_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_question_tag) <> 16 OR octet_length(p_answer_nonce) <> 12
     OR octet_length(p_answer_ciphertext) NOT BETWEEN 1 AND 8192 OR octet_length(p_answer_tag) <> 16 THEN
    RAISE EXCEPTION 'S4.9 saved history arguments are invalid' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO receipt_row FROM public.s4_9_answer_validation_receipts r
  WHERE r.receipt_sha256 = p_receipt_sha256 AND r.owner_user_id = p_owner_user_id
    AND r.oauth_client_id = p_oauth_client_id AND r.draft_sha256 = p_draft_sha256
    AND r.expires_at > statement_timestamp() AND r.consumed_at IS NULL
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 validation receipt is unavailable' USING ERRCODE = '55000';
  END IF;
  UPDATE public.s4_9_answer_validation_receipts SET consumed_at = transaction_timestamp()
  WHERE receipt_sha256 = p_receipt_sha256;
  INSERT INTO public.s4_9_saved_answer_history (
    answer_id, owner_user_id, oauth_client_id, receipt_sha256, research_context_id,
    source_set_sha256, validation_status, kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    question_nonce, question_ciphertext, question_tag, answer_nonce, answer_ciphertext, answer_tag,
    created_at, expires_at
  ) VALUES (
    p_answer_id, p_owner_user_id, p_oauth_client_id, p_receipt_sha256, receipt_row.research_context_id,
    receipt_row.source_set_sha256, receipt_row.validation_status, p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_question_nonce, p_question_ciphertext, p_question_tag, p_answer_nonce, p_answer_ciphertext, p_answer_tag,
    p_created_at, p_created_at + interval '30 days'
  );
END
$consume_s4_9_validation_and_save_history$;
ALTER FUNCTION public.consume_s4_9_validation_and_save_history(
  text,text,text,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.consume_s4_9_validation_and_save_history(
  text,text,text,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz
) FROM PUBLIC;

CREATE FUNCTION public.persist_s4_9_strong_llm_history(
  p_owner_user_id text, p_answer_id text, p_request_id text, p_answer_mode text,
  p_session_id text, p_scope_claim_id text, p_answer_basis text,
  p_citation_coverage double precision, p_guardrail_flags text[], p_kek_version text,
  p_wrap_nonce bytea, p_wrapped_dek bytea, p_wrap_tag bytea,
  p_question_nonce bytea, p_question_ciphertext bytea, p_question_tag bytea,
  p_answer_nonce bytea, p_answer_ciphertext bytea, p_answer_tag bytea,
  p_created_at timestamptz, p_citations jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $persist_s4_9_strong_llm_history$
DECLARE
  canonical_citations jsonb := '[]'::jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  private_state text;
  public_version text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_answer_mode NOT IN ('CONCISE','DETAILED')
     OR p_answer_basis NOT IN ('EVIDENCE','MODEL_KNOWLEDGE')
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$'
     OR octet_length(p_wrap_nonce) <> 12 OR octet_length(p_wrapped_dek) <> 32
     OR octet_length(p_wrap_tag) <> 16 OR octet_length(p_question_nonce) NOT BETWEEN 1 AND 8192
     OR octet_length(p_question_tag) <> 16 OR octet_length(p_answer_nonce) <> 12
     OR octet_length(p_answer_ciphertext) NOT BETWEEN 1 AND 8192 OR octet_length(p_answer_tag) <> 16
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds'
       AND transaction_timestamp() + interval '60 seconds' THEN
    RAISE EXCEPTION 'S4.9 Strong LLM history arguments are invalid' USING ERRCODE = '22023';
  END IF;

  IF p_answer_basis = 'EVIDENCE' THEN
    IF p_citation_coverage < 0.8 OR coalesce(cardinality(p_guardrail_flags), 0) > 5
       OR NOT coalesce(p_guardrail_flags, ARRAY[]::text[]) <@
         ARRAY['SINGLE_SOURCE','STALE_SOURCE','CONFLICTING_SOURCES','LOW_RELEVANCE','SECONDARY_SOURCE']::text[] THEN
      RAISE EXCEPTION 'S4.9 evidence history arguments are invalid' USING ERRCODE = '22023';
    END IF;
    canonical_citations := public.canonicalize_rag_v2_immutable_retrieval_citations(
      p_owner_user_id, p_session_id, p_scope_claim_id, p_citations
    );
    IF jsonb_array_length(canonical_citations) NOT BETWEEN 1 AND 5 THEN
      RAISE EXCEPTION 'S4.9 evidence history citations are invalid' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF p_citation_coverage <> 0.0 OR p_guardrail_flags <> ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]
       OR p_citations <> '[]'::jsonb THEN
      RAISE EXCEPTION 'S4.9 model knowledge history arguments are invalid' USING ERRCODE = '22023';
    END IF;
  END IF;

  SELECT * INTO claim_row FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 Strong LLM history scope disappeared' USING ERRCODE = '55000';
  END IF;
  private_state := CASE WHEN claim_row.owner_private_generation_id IS NULL THEN 'ABSENT' ELSE 'READY' END;
  public_version := 'immutable-v2-' || claim_row.public_pointer_version::text;

  INSERT INTO public.rag_v2_answer_history (
    answer_id, owner_user_id, request_id, answer_mode, generation_status,
    citation_coverage, retrieval_failure, guardrail_flags, public_corpus_version,
    private_overlay_state, kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    question_nonce, question_ciphertext, question_tag,
    answer_nonce, answer_ciphertext, answer_tag, citation_count, created_at, expires_at
  ) VALUES (
    p_answer_id, p_owner_user_id, p_request_id, p_answer_mode, 'ANSWERED',
    p_citation_coverage, false, p_guardrail_flags, public_version,
    private_state, p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_question_nonce, p_question_ciphertext, p_question_tag,
    p_answer_nonce, p_answer_ciphertext, p_answer_tag,
    jsonb_array_length(canonical_citations), p_created_at, p_created_at + interval '30 days'
  );
  INSERT INTO public.rag_v2_answer_citations (
    answer_id, owner_user_id, ordinal, citation_kind, source_id, title,
    canonical_url, document_id, sanitized_display_name, locator
  )
  SELECT p_answer_id, p_owner_user_id, ordinal::integer,
    citation.value ->> 'citationKind',
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'sourceId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'title' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'canonicalUrl' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'documentId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'displayName' END,
    citation.value -> 'locator'
  FROM jsonb_array_elements(canonical_citations) WITH ORDINALITY AS citation(value, ordinal)
  ORDER BY ordinal;
  RETURN canonical_citations;
END
$persist_s4_9_strong_llm_history$;
ALTER FUNCTION public.persist_s4_9_strong_llm_history(
  text,text,text,text,text,text,text,double precision,text[],text,
  bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.persist_s4_9_strong_llm_history(
  text,text,text,text,text,text,text,double precision,text[],text,
  bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
) FROM PUBLIC;

CREATE FUNCTION public.record_s4_9_strong_llm_usage(
  p_usage_event_id text,
  p_owner_user_id text,
  p_request_id text,
  p_provider text,
  p_model_id text,
  p_answer_basis text,
  p_outcome text,
  p_tool_round_count integer,
  p_search_call_count integer,
  p_read_call_count integer,
  p_prompt_token_count integer,
  p_output_token_count integer,
  p_evidence_set_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_strong_llm_usage$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_usage_event_id !~ '^s49_llu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_provider <> 'VERTEX_AI'
     OR p_model_id !~ '^[a-z][a-z0-9.-]{2,127}$'
     OR p_outcome NOT IN ('COMMITTED','REJECTED','UNKNOWN_BILLING')
     OR p_tool_round_count NOT BETWEEN 0 AND 3
     OR p_search_call_count NOT BETWEEN 0 AND 3
     OR p_read_call_count NOT BETWEEN 0 AND 8
     OR p_evidence_set_sha256 !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE' AND actor.security_version > 0
     ) THEN
    RAISE EXCEPTION 'S4.9 Strong LLM usage arguments are invalid' USING ERRCODE = '22023';
  END IF;
  IF p_outcome = 'COMMITTED' THEN
    IF p_answer_basis NOT IN ('EVIDENCE','MODEL_KNOWLEDGE','INSUFFICIENT_EVIDENCE')
       OR p_prompt_token_count NOT BETWEEN 0 AND 500000
       OR p_output_token_count NOT BETWEEN 0 AND 100000 THEN
      RAISE EXCEPTION 'S4.9 committed usage shape is invalid' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF p_answer_basis IS NOT NULL OR p_prompt_token_count IS NOT NULL OR p_output_token_count IS NOT NULL THEN
      RAISE EXCEPTION 'S4.9 non-committed usage must be content-free' USING ERRCODE = '22023';
    END IF;
  END IF;
  INSERT INTO public.s4_9_strong_llm_usage_ledger (
    usage_event_id, owner_user_id, request_id, provider, model_id, answer_basis, outcome,
    tool_round_count, search_call_count, read_call_count, prompt_token_count, output_token_count,
    evidence_set_sha256
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, p_provider, p_model_id, p_answer_basis, p_outcome,
    p_tool_round_count, p_search_call_count, p_read_call_count, p_prompt_token_count, p_output_token_count,
    p_evidence_set_sha256
  );
END
$record_s4_9_strong_llm_usage$;
ALTER FUNCTION public.record_s4_9_strong_llm_usage(
  text,text,text,text,text,text,text,integer,integer,integer,integer,integer,text
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_strong_llm_usage(
  text,text,text,text,text,text,text,integer,integer,integer,integer,integer,text
) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  public.s4_9_mcp_oauth_clients,
  public.s4_9_mcp_oauth_authorization_codes,
  public.s4_9_mcp_oauth_refresh_tokens,
  public.s4_9_strong_llm_usage_ledger,
  public.s4_9_web_evidence_metadata,
  public.s4_9_answer_validation_receipts,
  public.s4_9_saved_answer_history
FROM PUBLIC;

DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      public.s4_9_mcp_oauth_clients,
      public.s4_9_mcp_oauth_authorization_codes,
      public.s4_9_mcp_oauth_refresh_tokens,
      public.s4_9_strong_llm_usage_ledger,
      public.s4_9_web_evidence_metadata,
      public.s4_9_answer_validation_receipts,
      public.s4_9_saved_answer_history
    FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_strong_llm_usage(
      text,text,text,text,text,text,text,integer,integer,integer,integer,integer,text
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_web_evidence_metadata(
      text,text,text,text,text,text,text,timestamptz,text,timestamptz
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.sync_s4_9_mcp_oauth_client(
      text,text,text,text[],text[],text
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.upsert_s4_9_mcp_oauth_code_hash(
      text,text,text,bigint,text,text,text[],text,timestamptz
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.consume_s4_9_mcp_oauth_code_hash(text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.rotate_s4_9_mcp_refresh_token_hash(
      text,text,text,bigint,text,text[],timestamptz
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.revoke_s4_9_mcp_refresh_token_family(text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.issue_s4_9_answer_validation_receipt(
      text,text,text,text,text,text,text,timestamptz
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.consume_s4_9_validation_and_save_history(
      text,text,text,text,text,text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.persist_s4_9_strong_llm_history(
      text,text,text,text,text,text,text,double precision,text[],text,
      bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
    ) TO decision_app;
  END IF;
END
$grants$;
