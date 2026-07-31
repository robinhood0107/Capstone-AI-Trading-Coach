-- S4.4는 질문/답변 plaintext와 raw idempotency key를 DB에 두지 않고,
-- decision_app이 호출할 수 있는 owner-scoped SECURITY DEFINER 함수만 연다.
CREATE TABLE rag_consent_events (
  consent_event_id text PRIMARY KEY,
  consent_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  consent_type text NOT NULL,
  action text NOT NULL,
  policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_consent_events_id_check
    CHECK (consent_event_id ~ '^cns_[0-9a-f]{32}$'),
  CONSTRAINT rag_consent_events_type_check
    CHECK (consent_type = 'EXTERNAL_AI_RAG_V1'),
  CONSTRAINT rag_consent_events_action_check
    CHECK (action IN ('GRANT', 'REVOKE')),
  CONSTRAINT rag_consent_events_policy_check
    CHECK (policy_version = 'EXTERNAL_AI_RAG_V1')
);
CREATE INDEX rag_consent_events_owner_created_idx
  ON rag_consent_events (owner_user_id, consent_sequence DESC);

CREATE TABLE rag_answer_claims (
  scope_hmac text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  request_fingerprint text NOT NULL,
  state text NOT NULL,
  answer_id text,
  provider_attempts integer NOT NULL DEFAULT 0,
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  pending_expires_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_answer_claims_scope_check
    CHECK (scope_hmac ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_answer_claims_fingerprint_check
    CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_answer_claims_state_check
    CHECK (
      state IN (
        'PENDING',
        'COMPLETE',
        'FAILED_BEFORE_PROVIDER',
        'UNKNOWN_AFTER_PROVIDER'
      )
    ),
  CONSTRAINT rag_answer_claims_answer_check
    CHECK (
      (state = 'COMPLETE' AND answer_id ~ '^rag_ans_[0-9a-f]{32}$')
      OR
      (state <> 'COMPLETE' AND answer_id IS NULL)
    ),
  CONSTRAINT rag_answer_claims_attempt_check
    CHECK (provider_attempts BETWEEN 0 AND 1),
  CONSTRAINT rag_answer_claims_pending_expiry_check
    CHECK (pending_expires_at >= claimed_at)
);
CREATE INDEX rag_answer_claims_owner_updated_idx
  ON rag_answer_claims (owner_user_id, updated_at DESC);

CREATE TABLE rag_answer_claim_transitions (
  transition_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scope_hmac text NOT NULL REFERENCES rag_answer_claims(scope_hmac) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  from_state text,
  to_state text NOT NULL,
  provider_attempts integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_answer_claim_transitions_scope_check
    CHECK (scope_hmac ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_answer_claim_transitions_from_check
    CHECK (
      from_state IS NULL
      OR
      from_state IN (
        'PENDING',
        'COMPLETE',
        'FAILED_BEFORE_PROVIDER',
        'UNKNOWN_AFTER_PROVIDER'
      )
    ),
  CONSTRAINT rag_answer_claim_transitions_to_check
    CHECK (
      to_state IN (
        'PENDING',
        'COMPLETE',
        'FAILED_BEFORE_PROVIDER',
        'UNKNOWN_AFTER_PROVIDER'
      )
    ),
  CONSTRAINT rag_answer_claim_transitions_attempt_check
    CHECK (provider_attempts BETWEEN 0 AND 1)
);
CREATE INDEX rag_answer_claim_transitions_scope_created_idx
  ON rag_answer_claim_transitions (scope_hmac, created_at, transition_id);

CREATE TABLE rag_answer_history (
  answer_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  answer_mode text NOT NULL,
  generation_status text NOT NULL,
  citation_coverage double precision NOT NULL,
  retrieval_failure boolean NOT NULL,
  guardrail_flags text[] NOT NULL,
  citation_count integer NOT NULL,
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
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT rag_answer_history_id_check
    CHECK (answer_id ~ '^rag_ans_[0-9a-f]{32}$'),
  CONSTRAINT rag_answer_history_mode_check
    CHECK (answer_mode IN ('CONCISE', 'DETAILED')),
  CONSTRAINT rag_answer_history_status_check
    CHECK (
      generation_status IN (
        'ANSWERED',
        'RETRIEVAL_ONLY',
        'RETRIEVAL_FAILURE',
        'BLOCKED_SENSITIVE',
        'BLOCKED_ADVICE',
        'GENERATION_UNAVAILABLE'
      )
    ),
  CONSTRAINT rag_answer_history_coverage_check
    CHECK (citation_coverage BETWEEN 0.0 AND 1.0),
  CONSTRAINT rag_answer_history_status_result_check
    CHECK (
      (
        generation_status = 'ANSWERED'
        AND citation_count BETWEEN 1 AND 5
        AND citation_coverage = 1.0
        AND NOT retrieval_failure
      )
      OR
      (
        generation_status = 'RETRIEVAL_FAILURE'
        AND citation_count = 0
        AND citation_coverage = 0.0
        AND retrieval_failure
      )
      OR
      (
        generation_status IN (
          'RETRIEVAL_ONLY',
          'BLOCKED_SENSITIVE',
          'BLOCKED_ADVICE',
          'GENERATION_UNAVAILABLE'
        )
        AND citation_count = 0
        AND citation_coverage = 0.0
        AND NOT retrieval_failure
      )
    ),
  CONSTRAINT rag_answer_history_flags_check
    CHECK (
      cardinality(guardrail_flags) BETWEEN 0 AND 8
      AND array_position(guardrail_flags, '') IS NULL
      AND octet_length(array_to_string(guardrail_flags, '')) <= 512
      AND array_to_string(guardrail_flags, '') ~ '^[A-Z0-9_]*$'
    ),
  CONSTRAINT rag_answer_history_kek_check
    CHECK (kek_version ~ '^kek-v[1-9][0-9]{0,8}$'),
  CONSTRAINT rag_answer_history_wrap_check
    CHECK (
      octet_length(wrap_nonce) = 12
      AND octet_length(wrapped_dek) = 32
      AND octet_length(wrap_tag) = 16
    ),
  CONSTRAINT rag_answer_history_question_check
    CHECK (
      octet_length(question_nonce) = 12
      AND octet_length(question_ciphertext) BETWEEN 1 AND 8192
      AND octet_length(question_tag) = 16
    ),
  CONSTRAINT rag_answer_history_answer_check
    CHECK (
      octet_length(answer_nonce) = 12
      AND octet_length(answer_ciphertext) BETWEEN 0 AND 8192
      AND octet_length(answer_tag) = 16
    ),
  CONSTRAINT rag_answer_history_expiry_check
    CHECK (expires_at = created_at + interval '30 days')
);
CREATE INDEX rag_answer_history_owner_created_idx
  ON rag_answer_history (owner_user_id, created_at DESC, answer_id DESC);
CREATE INDEX rag_answer_history_expiry_idx
  ON rag_answer_history (expires_at, answer_id);

CREATE TABLE rag_answer_citations (
  answer_id text NOT NULL REFERENCES rag_answer_history(answer_id) ON DELETE CASCADE,
  ordinal integer NOT NULL,
  citation_id text NOT NULL,
  source_id text NOT NULL REFERENCES rag_sources(source_id) ON DELETE RESTRICT,
  source_revision_id text NOT NULL
    REFERENCES rag_source_revisions(source_revision_id) ON DELETE RESTRICT,
  chunk_revision_id text NOT NULL,
  generation_id text NOT NULL
    REFERENCES rag_corpus_generations(corpus_generation_id) ON DELETE RESTRICT,
  title text NOT NULL,
  section_title text NOT NULL,
  canonical_url text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_answer_citations_ordinal_check CHECK (ordinal BETWEEN 1 AND 5),
  CONSTRAINT rag_answer_citations_id_check CHECK (citation_id ~ '^cit_[1-5]$'),
  CONSTRAINT rag_answer_citations_title_check
    CHECK (octet_length(title) BETWEEN 1 AND 1024),
  CONSTRAINT rag_answer_citations_section_check
    CHECK (octet_length(section_title) BETWEEN 1 AND 512),
  CONSTRAINT rag_answer_citations_url_check
    CHECK (
      canonical_url ~ '^https://'
      AND octet_length(canonical_url) BETWEEN 9 AND 2048
    ),
  CONSTRAINT rag_answer_citations_chunk_source_fkey
    FOREIGN KEY (chunk_revision_id, source_revision_id)
    REFERENCES rag_chunk_revisions(chunk_revision_id, source_revision_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_answer_citations_pkey PRIMARY KEY (answer_id, ordinal),
  CONSTRAINT rag_answer_citations_identity_unique UNIQUE (answer_id, citation_id)
);

CREATE TABLE rag_answer_feedback (
  answer_id text PRIMARY KEY REFERENCES rag_answer_history(answer_id) ON DELETE CASCADE,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  helpful boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE rag_provider_usage_ledger (
  usage_event_id text PRIMARY KEY,
  scope_hmac text NOT NULL REFERENCES rag_answer_claims(scope_hmac) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  provider_usage_hmac text NOT NULL,
  provider text NOT NULL,
  physical_attempt integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_provider_usage_ledger_id_check
    CHECK (usage_event_id ~ '^rpu_[0-9a-f]{32}$'),
  CONSTRAINT rag_provider_usage_ledger_scope_check
    CHECK (scope_hmac ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_provider_usage_ledger_hmac_check
    CHECK (provider_usage_hmac ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_provider_usage_ledger_provider_check
    CHECK (provider IN ('GEMINI', 'OPENAI')),
  CONSTRAINT rag_provider_usage_ledger_attempt_check
    CHECK (physical_attempt = 1),
  CONSTRAINT rag_provider_usage_ledger_scope_unique UNIQUE (scope_hmac, physical_attempt)
);

CREATE FUNCTION reject_rag_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_rag_append_only_mutation$
BEGIN
  RAISE EXCEPTION 'RAG append-only ledger mutation is forbidden'
    USING ERRCODE = '55000';
END
$reject_rag_append_only_mutation$;
ALTER FUNCTION reject_rag_append_only_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_rag_append_only_mutation() FROM PUBLIC;

CREATE TRIGGER rag_consent_events_append_only
BEFORE UPDATE OR DELETE ON rag_consent_events
FOR EACH ROW EXECUTE FUNCTION reject_rag_append_only_mutation();
CREATE TRIGGER rag_answer_claim_transitions_append_only
BEFORE UPDATE OR DELETE ON rag_answer_claim_transitions
FOR EACH ROW EXECUTE FUNCTION reject_rag_append_only_mutation();
CREATE TRIGGER rag_provider_usage_ledger_append_only
BEFORE UPDATE OR DELETE ON rag_provider_usage_ledger
FOR EACH ROW EXECUTE FUNCTION reject_rag_append_only_mutation();

CREATE FUNCTION record_rag_consent_event(
  p_owner_user_id text,
  p_consent_event_id text,
  p_action text,
  p_policy_version text
)
RETURNS TABLE (
  consent_event_id text,
  consent_type text,
  action text,
  policy_version text,
  created_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_rag_consent_event$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_consent_event_id !~ '^cns_[0-9a-f]{32}$'
     OR p_action NOT IN ('GRANT', 'REVOKE')
     OR p_policy_version <> 'EXTERNAL_AI_RAG_V1'
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG consent arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- consent event와 provider-attempt claim은 owner별 transaction lock을 공유해 revoke ordering을 고정한다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-consent-v1|' || p_owner_user_id, 0)
  );
  RETURN QUERY
  INSERT INTO public.rag_consent_events (
    consent_event_id,
    owner_user_id,
    consent_type,
    action,
    policy_version,
    created_at
  )
  VALUES (
    p_consent_event_id,
    p_owner_user_id,
    'EXTERNAL_AI_RAG_V1',
    p_action,
    p_policy_version,
    clock_timestamp()
  )
  RETURNING
    rag_consent_events.consent_event_id,
    rag_consent_events.consent_type,
    rag_consent_events.action,
    rag_consent_events.policy_version,
    rag_consent_events.created_at;
END
$record_rag_consent_event$;
ALTER FUNCTION record_rag_consent_event(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION record_rag_consent_event(text, text, text, text)
  FROM PUBLIC;

CREATE FUNCTION read_effective_rag_consent(p_owner_user_id text)
RETURNS TABLE (
  granted boolean,
  policy_version text,
  recorded_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_effective_rag_consent$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'RAG consent actor binding is invalid'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    coalesce(latest.action = 'GRANT', false),
    latest.policy_version,
    latest.created_at
  FROM (SELECT 1) AS singleton
  LEFT JOIN LATERAL (
    SELECT event.action, event.policy_version, event.created_at
    FROM public.rag_consent_events AS event
    WHERE event.owner_user_id = p_owner_user_id
      AND event.consent_type = 'EXTERNAL_AI_RAG_V1'
    ORDER BY event.consent_sequence DESC
    LIMIT 1
  ) AS latest ON true;
END
$read_effective_rag_consent$;
ALTER FUNCTION read_effective_rag_consent(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_effective_rag_consent(text)
  FROM PUBLIC;

CREATE FUNCTION claim_rag_answer(
  p_owner_user_id text,
  p_scope_hmac text,
  p_request_fingerprint text,
  p_claim_ttl_seconds integer
)
RETURNS TABLE (
  outcome text,
  answer_id text
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_answer$
DECLARE
  inserted_rows integer;
  existing_claim public.rag_answer_claims%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_scope_hmac !~ '^[0-9a-f]{64}$'
     OR p_request_fingerprint !~ '^[0-9a-f]{64}$'
     OR p_claim_ttl_seconds NOT BETWEEN 30 AND 300
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG answer claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.rag_answer_claims (
    scope_hmac,
    owner_user_id,
    request_fingerprint,
    state,
    pending_expires_at
  )
  VALUES (
    p_scope_hmac,
    p_owner_user_id,
    p_request_fingerprint,
    'PENDING',
    transaction_timestamp() + make_interval(secs => p_claim_ttl_seconds)
  )
  ON CONFLICT (scope_hmac) DO NOTHING;
  GET DIAGNOSTICS inserted_rows = ROW_COUNT;

  IF inserted_rows = 1 THEN
    INSERT INTO public.rag_answer_claim_transitions (
      scope_hmac, owner_user_id, from_state, to_state, provider_attempts
    )
    VALUES (p_scope_hmac, p_owner_user_id, NULL, 'PENDING', 0);
    RETURN QUERY SELECT 'CLAIMED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT claim.*
  INTO existing_claim
  FROM public.rag_answer_claims AS claim
  WHERE claim.scope_hmac = p_scope_hmac
    AND claim.owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG answer claim owner mismatch'
      USING ERRCODE = '42501';
  END IF;
  IF existing_claim.request_fingerprint <> p_request_fingerprint THEN
    RETURN QUERY SELECT 'CONFLICT'::text, NULL::text;
    RETURN;
  END IF;
  IF existing_claim.state = 'COMPLETE' THEN
    IF existing_claim.answer_id IS NOT NULL
       AND EXISTS (
         SELECT 1
         FROM public.rag_answer_history AS history
         WHERE history.answer_id = existing_claim.answer_id
           AND history.owner_user_id = p_owner_user_id
           AND history.expires_at > statement_timestamp()
       ) THEN
      RETURN QUERY SELECT 'REPLAY'::text, existing_claim.answer_id;
    ELSE
      RETURN QUERY SELECT 'RESULT_UNAVAILABLE'::text, NULL::text;
    END IF;
    RETURN;
  END IF;
  IF existing_claim.state = 'PENDING'
     AND existing_claim.pending_expires_at <= statement_timestamp() THEN
    UPDATE public.rag_answer_claims
    SET state = 'UNKNOWN_AFTER_PROVIDER',
        updated_at = transaction_timestamp()
    WHERE scope_hmac = p_scope_hmac;
    INSERT INTO public.rag_answer_claim_transitions (
      scope_hmac, owner_user_id, from_state, to_state, provider_attempts
    )
    VALUES (
      p_scope_hmac,
      p_owner_user_id,
      'PENDING',
      'UNKNOWN_AFTER_PROVIDER',
      existing_claim.provider_attempts
    );
    RETURN QUERY SELECT 'UNKNOWN_AFTER_PROVIDER'::text, NULL::text;
    RETURN;
  END IF;
  RETURN QUERY
  SELECT
    CASE existing_claim.state
      WHEN 'PENDING' THEN 'IN_PROGRESS'
      ELSE existing_claim.state
    END,
    NULL::text;
END
$claim_rag_answer$;
ALTER FUNCTION claim_rag_answer(text, text, text, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION claim_rag_answer(text, text, text, integer)
  FROM PUBLIC;

CREATE FUNCTION mark_rag_provider_attempt(
  p_owner_user_id text,
  p_scope_hmac text,
  p_request_fingerprint text,
  p_usage_event_id text,
  p_provider_usage_hmac text,
  p_provider text,
  p_context_citations jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_provider_attempt$
DECLARE
  context_count integer;
  valid_context_count integer;
  distinct_context_count integer;
BEGIN
  context_count :=
    CASE
      WHEN jsonb_typeof(p_context_citations) = 'array'
        THEN jsonb_array_length(p_context_citations)
      ELSE -1
    END;
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rpu_[0-9a-f]{32}$'
     OR p_provider_usage_hmac !~ '^[0-9a-f]{64}$'
     OR p_provider NOT IN ('GEMINI', 'OPENAI')
     OR context_count NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'RAG provider attempt arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-consent-v1|' || p_owner_user_id, 0)
  );
  IF coalesce(
    (
      SELECT event.action = 'GRANT'
      FROM public.rag_consent_events AS event
      WHERE event.owner_user_id = p_owner_user_id
        AND event.consent_type = 'EXTERNAL_AI_RAG_V1'
        AND event.policy_version = 'EXTERNAL_AI_RAG_V1'
      ORDER BY event.consent_sequence DESC
      LIMIT 1
    ),
    false
  ) IS NOT TRUE THEN
    RAISE EXCEPTION 'RAG provider attempt requires effective consent'
      USING ERRCODE = '42501';
  END IF;
  SELECT
    count(*)::integer,
    count(DISTINCT item.value ->> 'chunkRevisionId')::integer
  INTO valid_context_count, distinct_context_count
  FROM jsonb_array_elements(p_context_citations) AS item(value)
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = item.value ->> 'sourceRevisionId'
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
   AND revision.initial_processing = 'PROJECT_AUTHORED_CARD'
   AND revision.external_processing_allowed
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = item.value ->> 'chunkRevisionId'
   AND chunk.source_revision_id = revision.source_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_embedding_policy_state AS policy
    ON policy.state_id = 'default'
   AND policy.active_generation_id = item.value ->> 'generationId'
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy.active_generation_id
   AND generation.embedding_profile_id = policy.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = generation.corpus_generation_id
   AND membership.embedding_profile_id = generation.embedding_profile_id
   AND membership.chunk_revision_id = chunk.chunk_revision_id
  WHERE jsonb_typeof(item.value) = 'object'
    AND (
      SELECT count(*)
      FROM jsonb_object_keys(item.value)
    ) = 3
    AND item.value ?& ARRAY[
      'sourceRevisionId',
      'chunkRevisionId',
      'generationId'
    ]
    AND (item.value ->> 'sourceRevisionId') ~ '^src_rev_[0-9a-f]{32}$'
    AND (item.value ->> 'chunkRevisionId') ~ '^rag_chk_[0-9a-f]{32}$';
  IF valid_context_count <> context_count
     OR distinct_context_count <> context_count THEN
    RAISE EXCEPTION 'RAG provider context is not externally processable'
      USING ERRCODE = '42501';
  END IF;
  UPDATE public.rag_answer_claims
  SET provider_attempts = 1,
      updated_at = transaction_timestamp()
  WHERE scope_hmac = p_scope_hmac
    AND owner_user_id = p_owner_user_id
    AND request_fingerprint = p_request_fingerprint
    AND state = 'PENDING'
    AND provider_attempts = 0
    AND pending_expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG provider attempt claim is not sendable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_provider_usage_ledger (
    usage_event_id,
    scope_hmac,
    owner_user_id,
    provider_usage_hmac,
    provider,
    physical_attempt
  )
  VALUES (
    p_usage_event_id,
    p_scope_hmac,
    p_owner_user_id,
    p_provider_usage_hmac,
    p_provider,
    1
  );
END
$mark_rag_provider_attempt$;
ALTER FUNCTION mark_rag_provider_attempt(text, text, text, text, text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION mark_rag_provider_attempt(text, text, text, text, text, text, jsonb)
  FROM PUBLIC;

CREATE FUNCTION complete_rag_answer(
  p_owner_user_id text,
  p_scope_hmac text,
  p_request_fingerprint text,
  p_answer_id text,
  p_answer_mode text,
  p_generation_status text,
  p_citation_coverage double precision,
  p_retrieval_failure boolean,
  p_guardrail_flags text[],
  p_kek_version text,
  p_wrap_nonce bytea,
  p_wrapped_dek bytea,
  p_wrap_tag bytea,
  p_question_nonce bytea,
  p_question_ciphertext bytea,
  p_question_tag bytea,
  p_answer_nonce bytea,
  p_answer_ciphertext bytea,
  p_answer_tag bytea,
  p_created_at timestamptz,
  p_provider_attempts integer,
  p_citations jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $complete_rag_answer$
DECLARE
  citation_count integer;
  valid_citation_count integer;
  locked_claim public.rag_answer_claims%ROWTYPE;
BEGIN
  citation_count :=
    CASE
      WHEN jsonb_typeof(p_citations) = 'array' THEN jsonb_array_length(p_citations)
      ELSE -1
    END;
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_scope_hmac !~ '^[0-9a-f]{64}$'
     OR p_request_fingerprint !~ '^[0-9a-f]{64}$'
     OR p_answer_id !~ '^rag_ans_[0-9a-f]{32}$'
     OR p_answer_mode NOT IN ('CONCISE', 'DETAILED')
     OR p_generation_status NOT IN (
       'ANSWERED',
       'RETRIEVAL_ONLY',
       'RETRIEVAL_FAILURE',
       'BLOCKED_SENSITIVE',
       'BLOCKED_ADVICE',
       'GENERATION_UNAVAILABLE'
     )
     OR p_citation_coverage IS NULL
     OR p_citation_coverage NOT BETWEEN 0.0 AND 1.0
     OR p_guardrail_flags IS NULL
     OR cardinality(p_guardrail_flags) NOT BETWEEN 0 AND 8
     OR EXISTS (
       SELECT 1 FROM unnest(p_guardrail_flags) AS flag
       WHERE flag !~ '^[A-Z0-9_]{1,64}$'
     )
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$'
     OR octet_length(p_wrap_nonce) <> 12
     OR octet_length(p_wrapped_dek) <> 32
     OR octet_length(p_wrap_tag) <> 16
     OR octet_length(p_question_nonce) <> 12
     OR octet_length(p_question_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_question_tag) <> 16
     OR octet_length(p_answer_nonce) <> 12
     OR octet_length(p_answer_ciphertext) NOT BETWEEN 0 AND 8192
     OR octet_length(p_answer_tag) <> 16
     OR p_created_at IS NULL
     OR p_created_at > transaction_timestamp() + interval '5 seconds'
     OR p_created_at < transaction_timestamp() - interval '5 minutes'
     OR p_provider_attempts NOT BETWEEN 0 AND 1
     OR citation_count NOT BETWEEN 0 AND 5
     OR (p_generation_status = 'ANSWERED' AND citation_count = 0)
     OR (p_generation_status <> 'ANSWERED' AND citation_count <> 0)
     OR (
       p_generation_status = 'ANSWERED'
       AND (p_citation_coverage <> 1.0 OR p_retrieval_failure)
     )
     OR (
       p_generation_status = 'RETRIEVAL_FAILURE'
       AND (p_citation_coverage <> 0.0 OR NOT p_retrieval_failure)
     )
     OR (
       p_generation_status NOT IN ('ANSWERED', 'RETRIEVAL_FAILURE')
       AND (p_citation_coverage <> 0.0 OR p_retrieval_failure)
     ) THEN
    RAISE EXCEPTION 'RAG answer completion arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT claim.*
  INTO locked_claim
  FROM public.rag_answer_claims AS claim
  WHERE claim.scope_hmac = p_scope_hmac
    AND claim.owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_claim.request_fingerprint <> p_request_fingerprint
     OR locked_claim.state <> 'PENDING'
     OR locked_claim.provider_attempts <> p_provider_attempts THEN
    RAISE EXCEPTION 'RAG answer claim cannot be completed'
      USING ERRCODE = '55000';
  END IF;

  SELECT count(*)::integer
  INTO valid_citation_count
  FROM jsonb_array_elements(p_citations) AS item(value)
  JOIN public.rag_sources AS source
    ON source.source_id = item.value ->> 'sourceId'
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = item.value ->> 'sourceRevisionId'
   AND revision.source_id = source.source_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_embedding_policy_state AS policy
    ON policy.state_id = 'default'
   AND policy.active_generation_id = item.value ->> 'generationId'
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy.active_generation_id
   AND generation.embedding_profile_id = policy.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = item.value ->> 'chunkRevisionId'
   AND chunk.source_revision_id = revision.source_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = generation.corpus_generation_id
   AND membership.embedding_profile_id = generation.embedding_profile_id
   AND membership.chunk_revision_id = chunk.chunk_revision_id
  WHERE jsonb_typeof(item.value) = 'object'
    AND (
      SELECT count(*)
      FROM jsonb_object_keys(item.value)
    ) = 9
    AND item.value ?& ARRAY[
      'ordinal',
      'citationId',
      'sourceId',
      'sourceRevisionId',
      'chunkRevisionId',
      'generationId',
      'title',
      'sectionTitle',
      'canonicalUrl'
    ]
    AND (item.value ->> 'ordinal') ~ '^[1-5]$'
    AND (item.value ->> 'citationId') ~ '^cit_[1-5]$'
    AND (item.value ->> 'chunkRevisionId') ~ '^rag_chk_[0-9a-f]{32}$'
    AND (item.value ->> 'ordinal')::integer BETWEEN 1 AND citation_count
    AND item.value ->> 'citationId' =
        'cit_' || (item.value ->> 'ordinal')
    AND item.value ->> 'title' = revision.title
    AND item.value ->> 'sectionTitle' =
        chunk.heading_path[cardinality(chunk.heading_path)]
    AND item.value ->> 'canonicalUrl' = revision.canonical_url
    AND octet_length(item.value ->> 'title') BETWEEN 1 AND 1024
    AND octet_length(item.value ->> 'sectionTitle') BETWEEN 1 AND 512
    AND (item.value ->> 'canonicalUrl') ~ '^https://'
    AND octet_length(item.value ->> 'canonicalUrl') BETWEEN 9 AND 2048;
  IF valid_citation_count <> citation_count THEN
    RAISE EXCEPTION 'RAG citation access recheck failed'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.rag_answer_history (
    answer_id,
    owner_user_id,
    answer_mode,
    generation_status,
    citation_coverage,
    retrieval_failure,
    guardrail_flags,
    citation_count,
    kek_version,
    wrap_nonce,
    wrapped_dek,
    wrap_tag,
    question_nonce,
    question_ciphertext,
    question_tag,
    answer_nonce,
    answer_ciphertext,
    answer_tag,
    created_at,
    expires_at
  )
  VALUES (
    p_answer_id,
    p_owner_user_id,
    p_answer_mode,
    p_generation_status,
    p_citation_coverage,
    p_retrieval_failure,
    p_guardrail_flags,
    citation_count,
    p_kek_version,
    p_wrap_nonce,
    p_wrapped_dek,
    p_wrap_tag,
    p_question_nonce,
    p_question_ciphertext,
    p_question_tag,
    p_answer_nonce,
    p_answer_ciphertext,
    p_answer_tag,
    p_created_at,
    p_created_at + interval '30 days'
  );

  INSERT INTO public.rag_answer_citations (
    answer_id,
    ordinal,
    citation_id,
    source_id,
    source_revision_id,
    chunk_revision_id,
    generation_id,
    title,
    section_title,
    canonical_url
  )
  SELECT
    p_answer_id,
    (item.value ->> 'ordinal')::integer,
    item.value ->> 'citationId',
    item.value ->> 'sourceId',
    item.value ->> 'sourceRevisionId',
    item.value ->> 'chunkRevisionId',
    item.value ->> 'generationId',
    item.value ->> 'title',
    item.value ->> 'sectionTitle',
    item.value ->> 'canonicalUrl'
  FROM jsonb_array_elements(p_citations) AS item(value)
  ORDER BY (item.value ->> 'ordinal')::integer;

  UPDATE public.rag_answer_claims
  SET state = 'COMPLETE',
      answer_id = p_answer_id,
      updated_at = transaction_timestamp()
  WHERE scope_hmac = p_scope_hmac;
  INSERT INTO public.rag_answer_claim_transitions (
    scope_hmac, owner_user_id, from_state, to_state, provider_attempts
  )
  VALUES (
    p_scope_hmac,
    p_owner_user_id,
    'PENDING',
    'COMPLETE',
    p_provider_attempts
  );
END
$complete_rag_answer$;
ALTER FUNCTION complete_rag_answer(
  text, text, text, text, text, text, double precision, boolean, text[],
  text, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea,
  timestamptz, integer, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION complete_rag_answer(
    text, text, text, text, text, text, double precision, boolean, text[],
    text, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea,
    timestamptz, integer, jsonb
  )
  FROM PUBLIC;

CREATE FUNCTION fail_rag_answer_before_provider(
  p_owner_user_id text,
  p_scope_hmac text,
  p_request_fingerprint text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $fail_rag_answer_before_provider$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'RAG failed claim actor binding is invalid'
      USING ERRCODE = '42501';
  END IF;
  UPDATE public.rag_answer_claims
  SET state = 'FAILED_BEFORE_PROVIDER',
      updated_at = transaction_timestamp()
  WHERE scope_hmac = p_scope_hmac
    AND owner_user_id = p_owner_user_id
    AND request_fingerprint = p_request_fingerprint
    AND state = 'PENDING'
    AND provider_attempts = 0;
  IF FOUND THEN
    INSERT INTO public.rag_answer_claim_transitions (
      scope_hmac, owner_user_id, from_state, to_state, provider_attempts
    )
    VALUES (
      p_scope_hmac,
      p_owner_user_id,
      'PENDING',
      'FAILED_BEFORE_PROVIDER',
      0
    );
  END IF;
END
$fail_rag_answer_before_provider$;
ALTER FUNCTION fail_rag_answer_before_provider(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION fail_rag_answer_before_provider(text, text, text)
  FROM PUBLIC;

CREATE FUNCTION mark_rag_answer_unknown_after_provider(
  p_owner_user_id text,
  p_scope_hmac text,
  p_request_fingerprint text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_answer_unknown_after_provider$
DECLARE
  attempts integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'RAG unknown claim actor binding is invalid'
      USING ERRCODE = '42501';
  END IF;
  UPDATE public.rag_answer_claims
  SET state = 'UNKNOWN_AFTER_PROVIDER',
      updated_at = transaction_timestamp()
  WHERE scope_hmac = p_scope_hmac
    AND owner_user_id = p_owner_user_id
    AND request_fingerprint = p_request_fingerprint
    AND state = 'PENDING'
  RETURNING provider_attempts INTO attempts;
  IF FOUND THEN
    INSERT INTO public.rag_answer_claim_transitions (
      scope_hmac, owner_user_id, from_state, to_state, provider_attempts
    )
    VALUES (
      p_scope_hmac,
      p_owner_user_id,
      'PENDING',
      'UNKNOWN_AFTER_PROVIDER',
      attempts
    );
  END IF;
END
$mark_rag_answer_unknown_after_provider$;
ALTER FUNCTION mark_rag_answer_unknown_after_provider(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION mark_rag_answer_unknown_after_provider(text, text, text)
  FROM PUBLIC;

CREATE FUNCTION read_rag_history_metadata(
  p_owner_user_id text,
  p_before_created_at timestamptz,
  p_before_answer_id text,
  p_limit integer
)
RETURNS TABLE (
  answer_id text,
  created_at timestamptz,
  expires_at timestamptz,
  answer_mode text,
  generation_status text,
  helpful boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_history_metadata$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_limit NOT BETWEEN 1 AND 51
     OR (p_before_created_at IS NULL) <> (p_before_answer_id IS NULL)
     OR (
       p_before_answer_id IS NOT NULL
       AND p_before_answer_id !~ '^rag_ans_[0-9a-f]{32}$'
     ) THEN
    RAISE EXCEPTION 'RAG history metadata arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  SELECT
    history.answer_id,
    history.created_at,
    history.expires_at,
    history.answer_mode,
    history.generation_status,
    feedback.helpful
  FROM public.rag_answer_history AS history
  LEFT JOIN public.rag_answer_feedback AS feedback
    ON feedback.answer_id = history.answer_id
   AND feedback.owner_user_id = p_owner_user_id
  WHERE history.owner_user_id = p_owner_user_id
    AND history.expires_at > statement_timestamp()
    AND (
      p_before_created_at IS NULL
      OR (history.created_at, history.answer_id) <
         (p_before_created_at, p_before_answer_id)
    )
  ORDER BY history.created_at DESC, history.answer_id DESC
  LIMIT p_limit;
END
$read_rag_history_metadata$;
ALTER FUNCTION read_rag_history_metadata(text, timestamptz, text, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_rag_history_metadata(text, timestamptz, text, integer)
  FROM PUBLIC;

CREATE FUNCTION read_rag_history_detail(
  p_owner_user_id text,
  p_answer_id text
)
RETURNS TABLE (
  answer_id text,
  owner_user_id text,
  answer_mode text,
  generation_status text,
  citation_coverage double precision,
  retrieval_failure boolean,
  guardrail_flags text[],
  citation_count integer,
  kek_version text,
  wrap_nonce bytea,
  wrapped_dek bytea,
  wrap_tag bytea,
  question_nonce bytea,
  question_ciphertext bytea,
  question_tag bytea,
  answer_nonce bytea,
  answer_ciphertext bytea,
  answer_tag bytea,
  created_at timestamptz,
  expires_at timestamptz,
  helpful boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_history_detail$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_ans_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'RAG history detail arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  SELECT
    history.answer_id,
    history.owner_user_id,
    history.answer_mode,
    history.generation_status,
    history.citation_coverage,
    history.retrieval_failure,
    history.guardrail_flags,
    history.citation_count,
    history.kek_version,
    history.wrap_nonce,
    history.wrapped_dek,
    history.wrap_tag,
    history.question_nonce,
    history.question_ciphertext,
    history.question_tag,
    history.answer_nonce,
    history.answer_ciphertext,
    history.answer_tag,
    history.created_at,
    history.expires_at,
    feedback.helpful
  FROM public.rag_answer_history AS history
  LEFT JOIN public.rag_answer_feedback AS feedback
    ON feedback.answer_id = history.answer_id
   AND feedback.owner_user_id = p_owner_user_id
  WHERE history.answer_id = p_answer_id
    AND history.owner_user_id = p_owner_user_id
    AND history.expires_at > statement_timestamp()
  LIMIT 1;
END
$read_rag_history_detail$;
ALTER FUNCTION read_rag_history_detail(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_rag_history_detail(text, text)
  FROM PUBLIC;

CREATE FUNCTION read_rag_history_citations(
  p_owner_user_id text,
  p_answer_id text
)
RETURNS TABLE (
  ordinal integer,
  citation_id text,
  source_id text,
  source_revision_id text,
  chunk_revision_id text,
  generation_id text,
  title text,
  section_title text,
  canonical_url text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_history_citations$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_ans_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'RAG history citation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  SELECT
    citation.ordinal,
    citation.citation_id,
    citation.source_id,
    citation.source_revision_id,
    citation.chunk_revision_id,
    citation.generation_id,
    citation.title,
    citation.section_title,
    citation.canonical_url
  FROM public.rag_answer_history AS history
  JOIN public.rag_answer_citations AS citation
    ON citation.answer_id = history.answer_id
  JOIN public.rag_sources AS source
    ON source.source_id = citation.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = citation.source_revision_id
   AND revision.source_id = source.source_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_embedding_policy_state AS policy
    ON policy.state_id = 'default'
   AND policy.active_generation_id = citation.generation_id
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy.active_generation_id
   AND generation.embedding_profile_id = policy.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  WHERE history.answer_id = p_answer_id
    AND history.owner_user_id = p_owner_user_id
    AND history.expires_at > statement_timestamp()
    AND EXISTS (
      SELECT 1
      FROM public.rag_generation_chunks AS membership
      JOIN public.rag_chunk_revisions AS chunk
        ON chunk.chunk_revision_id = membership.chunk_revision_id
       AND chunk.chunk_revision_id = citation.chunk_revision_id
       AND chunk.source_revision_id = revision.source_revision_id
       AND chunk.access_level = 'PUBLIC'
       AND chunk.tier = 'PROJECT'
      WHERE membership.corpus_generation_id = generation.corpus_generation_id
        AND membership.embedding_profile_id = generation.embedding_profile_id
        AND citation.section_title =
            chunk.heading_path[cardinality(chunk.heading_path)]
    )
  ORDER BY citation.ordinal
  LIMIT 5;
END
$read_rag_history_citations$;
ALTER FUNCTION read_rag_history_citations(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_rag_history_citations(text, text)
  FROM PUBLIC;

CREATE FUNCTION delete_owned_rag_history(
  p_owner_user_id text,
  p_answer_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $delete_owned_rag_history$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_ans_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'RAG history delete arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  DELETE FROM public.rag_answer_history
  WHERE answer_id = p_answer_id
    AND owner_user_id = p_owner_user_id;
END
$delete_owned_rag_history$;
ALTER FUNCTION delete_owned_rag_history(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION delete_owned_rag_history(text, text)
  FROM PUBLIC;

CREATE FUNCTION upsert_owned_rag_answer_feedback(
  p_owner_user_id text,
  p_answer_id text,
  p_helpful boolean
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $upsert_owned_rag_answer_feedback$
DECLARE
  written boolean;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_ans_[0-9a-f]{32}$'
     OR p_helpful IS NULL THEN
    RAISE EXCEPTION 'RAG feedback arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  WITH owned AS (
    SELECT history.answer_id
    FROM public.rag_answer_history AS history
    WHERE history.answer_id = p_answer_id
      AND history.owner_user_id = p_owner_user_id
      AND history.expires_at > statement_timestamp()
  ),
  persisted AS (
    INSERT INTO public.rag_answer_feedback (
      answer_id,
      owner_user_id,
      helpful
    )
    SELECT owned.answer_id, p_owner_user_id, p_helpful
    FROM owned
    ON CONFLICT (answer_id) DO UPDATE
    SET helpful = EXCLUDED.helpful,
        updated_at = transaction_timestamp()
    WHERE rag_answer_feedback.owner_user_id = p_owner_user_id
    RETURNING true AS written
  )
  SELECT coalesce(bool_or(persisted.written), false)
  INTO written
  FROM persisted;
  RETURN written;
END
$upsert_owned_rag_answer_feedback$;
ALTER FUNCTION upsert_owned_rag_answer_feedback(text, text, boolean) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION upsert_owned_rag_answer_feedback(text, text, boolean)
  FROM PUBLIC;

CREATE FUNCTION purge_expired_rag_history(p_limit integer)
RETURNS TABLE (
  deleted_count integer,
  oldest_expired_lag_seconds bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $purge_expired_rag_history$
DECLARE
  oldest_expired timestamptz;
  removed_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_limit NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'RAG purge arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  WITH targets AS (
    SELECT history.answer_id
    FROM public.rag_answer_history AS history
    WHERE history.expires_at <= statement_timestamp()
    ORDER BY history.expires_at, history.answer_id
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
  ),
  deleted AS (
    DELETE FROM public.rag_answer_history AS history
    USING targets
    WHERE history.answer_id = targets.answer_id
    RETURNING 1
  )
  SELECT count(*)::integer
  INTO removed_count
  FROM deleted;

  SELECT min(history.expires_at)
  INTO oldest_expired
  FROM public.rag_answer_history AS history
  WHERE history.expires_at <= statement_timestamp();

  RETURN QUERY
  SELECT
    removed_count,
    CASE
      WHEN oldest_expired IS NULL THEN 0::bigint
      ELSE greatest(
        0,
        extract(epoch FROM statement_timestamp() - oldest_expired)::bigint
      )
    END;
END
$purge_expired_rag_history$;
ALTER FUNCTION purge_expired_rag_history(integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION purge_expired_rag_history(integer)
  FROM PUBLIC;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

DO $rag_s4_4_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_consent_events,
      rag_answer_claims,
      rag_answer_claim_transitions,
      rag_answer_history,
      rag_answer_citations,
      rag_answer_feedback,
      rag_provider_usage_ledger
    FROM decision_app;
    REVOKE ALL PRIVILEGES ON SEQUENCE
      rag_consent_events_consent_sequence_seq,
      rag_answer_claim_transitions_transition_id_seq
      FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_consent_events,
      rag_answer_claims,
      rag_answer_claim_transitions,
      rag_answer_history,
      rag_answer_citations,
      rag_answer_feedback,
      rag_provider_usage_ledger
    FROM decision_rag_writer;
    REVOKE ALL PRIVILEGES ON SEQUENCE
      rag_consent_events_consent_sequence_seq,
      rag_answer_claim_transitions_transition_id_seq
      FROM decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_consent_events,
      rag_answer_claims,
      rag_answer_claim_transitions,
      rag_answer_history,
      rag_answer_citations,
      rag_answer_feedback,
      rag_provider_usage_ledger
    FROM decision_rag_query;
    REVOKE ALL PRIVILEGES ON SEQUENCE
      rag_consent_events_consent_sequence_seq,
      rag_answer_claim_transitions_transition_id_seq
      FROM decision_rag_query;
  END IF;
END
$rag_s4_4_acl$;

GRANT EXECUTE ON FUNCTION record_rag_consent_event(text, text, text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION read_effective_rag_consent(text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION claim_rag_answer(text, text, text, integer)
  TO decision_app;
GRANT EXECUTE ON FUNCTION mark_rag_provider_attempt(text, text, text, text, text, text, jsonb)
  TO decision_app;
GRANT EXECUTE ON FUNCTION complete_rag_answer(
  text, text, text, text, text, text, double precision, boolean, text[],
  text, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea,
  timestamptz, integer, jsonb
) TO decision_app;
GRANT EXECUTE ON FUNCTION fail_rag_answer_before_provider(text, text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION mark_rag_answer_unknown_after_provider(text, text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION read_rag_history_metadata(text, timestamptz, text, integer)
  TO decision_app;
GRANT EXECUTE ON FUNCTION read_rag_history_detail(text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION read_rag_history_citations(text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION delete_owned_rag_history(text, text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION upsert_owned_rag_answer_feedback(text, text, boolean)
  TO decision_app;
GRANT EXECUTE ON FUNCTION purge_expired_rag_history(integer)
  TO decision_app;
