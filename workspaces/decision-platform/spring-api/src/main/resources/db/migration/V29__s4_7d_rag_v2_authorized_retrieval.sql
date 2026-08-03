-- V29는 historical V24~V28 graph를 바꾸지 않는다. active immutable bundle의 chunk만
-- opaque scope claim으로 읽어 application RRF에 전달하며, raw artifact나 provider transport를 만들지 않는다.

CREATE FUNCTION rag_v2_immutable_retrieval_topics_are_valid(p_topics text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_retrieval_topics_are_valid$
  SELECT cardinality(p_topics) BETWEEN 1 AND 6
    AND p_topics <@ ARRAY[
      'API', 'DATA', 'FINANCIAL_ENGINEERING', 'METHODOLOGY', 'PRODUCT_RISK', 'RISK'
    ]::text[]
    AND cardinality(p_topics) = cardinality(
      ARRAY(SELECT DISTINCT topic FROM unnest(p_topics) AS values(topic))
    )
$rag_v2_immutable_retrieval_topics_are_valid$;
ALTER FUNCTION rag_v2_immutable_retrieval_topics_are_valid(text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_retrieval_topics_are_valid(text[]) FROM PUBLIC;

-- V28 이전 staged record는 historical/incomplete로 그대로 남긴다. 새 materializer가 metadata를
-- 붙이기 전에는 query function이 해당 record를 읽지 않으므로 과거 row를 재작성하지 않는다.
ALTER TABLE rag_v2_immutable_source_revisions
  ADD COLUMN retrieval_topics text[],
  ADD COLUMN citation_title text;

ALTER TABLE rag_v2_immutable_source_revisions
  ADD CONSTRAINT rag_v2_immutable_source_retrieval_topics_check
    CHECK (
      retrieval_topics IS NULL
      OR public.rag_v2_immutable_retrieval_topics_are_valid(retrieval_topics)
    ),
  ADD CONSTRAINT rag_v2_immutable_source_citation_title_check
    CHECK (
      citation_title IS NULL
      OR (
        char_length(citation_title) BETWEEN 1 AND 500
        AND btrim(citation_title) <> ''
        AND citation_title !~ '[[:cntrl:]]'
      )
    );
CREATE INDEX rag_v2_immutable_source_retrieval_topics_idx
  ON rag_v2_immutable_source_revisions USING gin (retrieval_topics)
  WHERE retrieval_topics IS NOT NULL;

CREATE TABLE rag_v2_retrieval_scope_claims (
  scope_claim_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  session_id text NOT NULL,
  allowed_topics text[] NOT NULL,
  exact30_generation_id text NOT NULL,
  oa112_generation_id text NOT NULL,
  owner_private_generation_id text,
  owner_bundle_id text,
  embedding_profile_id text NOT NULL,
  public_pointer_version bigint NOT NULL,
  owner_pointer_version bigint NOT NULL,
  policy_version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  expires_at timestamptz NOT NULL,
  CONSTRAINT rag_v2_retrieval_scope_claim_id_check
    CHECK (scope_claim_id ~ '^rvs_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_retrieval_scope_owner_session_check
    CHECK (
      owner_user_id ~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
      AND char_length(session_id) BETWEEN 16 AND 128
      AND session_id ~ '^[A-Za-z0-9._:-]+$'
    ),
  CONSTRAINT rag_v2_retrieval_scope_topics_check
    CHECK (public.rag_v2_immutable_retrieval_topics_are_valid(allowed_topics)),
  CONSTRAINT rag_v2_retrieval_scope_component_check
    CHECK (
      exact30_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND oa112_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND exact30_generation_id <> oa112_generation_id
      AND (
        (owner_private_generation_id IS NULL AND owner_bundle_id IS NULL AND owner_pointer_version = 0)
        OR (
          owner_private_generation_id ~ '^rgr_[0-9a-f]{32}$'
          AND owner_bundle_id ~ '^rgb_[0-9a-f]{32}$'
          AND owner_private_generation_id <> exact30_generation_id
          AND owner_private_generation_id <> oa112_generation_id
          AND owner_pointer_version >= 1
        )
      )
    ),
  CONSTRAINT rag_v2_retrieval_scope_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_retrieval_scope_version_check
    CHECK (public_pointer_version >= 1 AND policy_version = 1),
  CONSTRAINT rag_v2_retrieval_scope_expiry_check
    CHECK (expires_at = created_at + interval '2 minutes')
);
CREATE INDEX rag_v2_retrieval_scope_claim_expiry_idx
  ON rag_v2_retrieval_scope_claims (expires_at, scope_claim_id);
CREATE INDEX rag_v2_retrieval_scope_claim_owner_session_idx
  ON rag_v2_retrieval_scope_claims (owner_user_id, session_id, expires_at DESC);

ALTER TABLE rag_v2_retrieval_scope_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_retrieval_scope_claims FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_retrieval_scope_claim_owner_read_policy
  ON rag_v2_retrieval_scope_claims
  FOR SELECT
  USING (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_retrieval_scope_claim_issue_policy
  ON rag_v2_retrieval_scope_claims
  FOR INSERT
  WITH CHECK (
    current_user = 'flyway'
    AND session_user = 'decision_app'
    AND owner_user_id = current_setting('app.actor_user_id', true)
  );

-- public pointer는 retrieval capability function 안에서만 active row를 읽는다. role이 custom
-- GUC를 직접 설정해도 direct table grant가 없으므로 standalone read capability가 되지 않는다.
CREATE POLICY rag_v2_immutable_public_pointer_retrieval_scope_read_policy
  ON rag_v2_immutable_public_bundle_pointers
  FOR SELECT
  USING (
    session_user IN ('decision_app', 'decision_rag_query')
    AND current_setting('app.rag_v2_retrieval_scope', true) = 'enabled'
    AND state_id = 'default'
    AND state = 'ACTIVE'
  );

CREATE FUNCTION issue_rag_v2_retrieval_scope(
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
  policy_version bigint,
  allowed_topics text[],
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_retrieval_scope$
#variable_conflict use_column
DECLARE
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
  exact_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  oa_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  owner_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  generated_scope_claim_id text;
  claim_created_at timestamptz := transaction_timestamp();
  claim_expires_at timestamptz := transaction_timestamp() + interval '2 minutes';
  selected_owner_generation_id text := NULL;
  selected_owner_bundle_id text := NULL;
  selected_owner_pointer_version bigint := 0;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_allowed_topics)
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public bundle is not active'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO exact_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = public_pointer.exact30_generation_id
    AND component_scope = 'EXACT30'
    AND owner_user_id IS NULL
    AND embedding_profile_id = public_pointer.embedding_profile_id
    AND state = 'ACTIVE'
    AND evaluation_status = 'PASSED';
  SELECT * INTO oa_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = public_pointer.oa112_generation_id
    AND component_scope = 'OA112'
    AND owner_user_id IS NULL
    AND embedding_profile_id = public_pointer.embedding_profile_id
    AND state = 'ACTIVE'
    AND evaluation_status = 'PASSED';
  IF NOT FOUND OR exact_generation.component_generation_id IS NULL OR oa_generation.component_generation_id IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 active public components are invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  IF FOUND AND owner_pointer.state IN ('BUILDING', 'FAILED') THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay is not ready'
      USING ERRCODE = '55000';
  END IF;
  IF FOUND AND owner_pointer.state = 'READY' THEN
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles
    WHERE bundle_id = owner_pointer.active_bundle_id
      AND owner_user_id = p_owner_user_id
      AND state = 'ACTIVE';
    IF NOT FOUND
       OR owner_bundle.exact30_generation_id IS DISTINCT FROM public_pointer.exact30_generation_id
       OR owner_bundle.oa112_generation_id IS DISTINCT FROM public_pointer.oa112_generation_id
       OR owner_bundle.embedding_profile_id IS DISTINCT FROM public_pointer.embedding_profile_id THEN
      RAISE EXCEPTION 'immutable RAG v2 owner bundle is not pinned to public base'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_generation
    FROM public.rag_v2_immutable_component_generations
    WHERE component_generation_id = owner_bundle.owner_private_generation_id
      AND component_scope = 'OWNER_PRIVATE'
      AND owner_user_id = p_owner_user_id
      AND embedding_profile_id = public_pointer.embedding_profile_id
      AND state = 'ACTIVE'
      AND evaluation_status = 'PASSED';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 owner component is invalid'
        USING ERRCODE = '55000';
    END IF;
    selected_owner_generation_id := owner_generation.component_generation_id;
    selected_owner_bundle_id := owner_bundle.bundle_id;
    selected_owner_pointer_version := owner_pointer.bundle_version;
  ELSIF FOUND AND owner_pointer.state <> 'ABSENT' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner pointer state is invalid'
      USING ERRCODE = '55000';
  ELSIF FOUND THEN
    selected_owner_pointer_version := owner_pointer.bundle_version;
  END IF;

  generated_scope_claim_id :=
    'rvs_' || substr(
      encode(
        digest(
          gen_random_bytes(32) || convert_to(
            concat_ws(
              E'\n',
              p_owner_user_id,
              p_session_id,
              public_pointer.exact30_generation_id,
              public_pointer.oa112_generation_id,
              coalesce(selected_owner_generation_id, ''),
              public_pointer.embedding_profile_id,
              claim_created_at::text
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );
  INSERT INTO public.rag_v2_retrieval_scope_claims (
    scope_claim_id,
    owner_user_id,
    session_id,
    allowed_topics,
    exact30_generation_id,
    oa112_generation_id,
    owner_private_generation_id,
    owner_bundle_id,
    embedding_profile_id,
    public_pointer_version,
    owner_pointer_version,
    policy_version,
    created_at,
    expires_at
  ) VALUES (
    generated_scope_claim_id,
    p_owner_user_id,
    p_session_id,
    p_allowed_topics,
    public_pointer.exact30_generation_id,
    public_pointer.oa112_generation_id,
    selected_owner_generation_id,
    selected_owner_bundle_id,
    public_pointer.embedding_profile_id,
    public_pointer.pointer_version,
    selected_owner_pointer_version,
    1,
    claim_created_at,
    claim_expires_at
  );
  RETURN QUERY
  SELECT
    generated_scope_claim_id,
    p_owner_user_id,
    p_session_id,
    public_pointer.exact30_generation_id,
    public_pointer.oa112_generation_id,
    selected_owner_generation_id,
    public_pointer.embedding_profile_id,
    1::bigint,
    p_allowed_topics,
    claim_expires_at;
END;
$issue_rag_v2_retrieval_scope$;
ALTER FUNCTION issue_rag_v2_retrieval_scope(text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_retrieval_scope(text, text, text[]) FROM PUBLIC;

CREATE FUNCTION resolve_rag_v2_retrieval_scope(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  allowed_topics text[],
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $resolve_rag_v2_retrieval_scope$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$' THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope resolution arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);

  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims
  WHERE scope_claim_id = p_scope_claim_id
    AND owner_user_id = p_owner_user_id
    AND session_id = p_session_id
    AND expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope is absent or expired'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default'
    AND state = 'ACTIVE'
    AND pointer_version = claim_row.public_pointer_version
    AND exact30_generation_id = claim_row.exact30_generation_id
    AND oa112_generation_id = claim_row.oa112_generation_id
    AND embedding_profile_id = claim_row.embedding_profile_id;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = claim_row.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = claim_row.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = claim_row.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = claim_row.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope public pointer changed'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF (FOUND AND (owner_pointer.state <> 'ABSENT' OR owner_pointer.bundle_version <> claim_row.owner_pointer_version))
       OR (NOT FOUND AND claim_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner pointer changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF NOT FOUND
       OR owner_pointer.state <> 'READY'
       OR owner_pointer.active_bundle_id IS DISTINCT FROM claim_row.owner_bundle_id
       OR owner_pointer.bundle_version <> claim_row.owner_pointer_version THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner bundle changed'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles
    WHERE bundle_id = claim_row.owner_bundle_id
      AND owner_user_id = p_owner_user_id
      AND owner_private_generation_id = claim_row.owner_private_generation_id
      AND exact30_generation_id = claim_row.exact30_generation_id
      AND oa112_generation_id = claim_row.oa112_generation_id
      AND embedding_profile_id = claim_row.embedding_profile_id
      AND state = 'ACTIVE';
    IF NOT FOUND
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_component_generations AS owner_generation
         WHERE owner_generation.component_generation_id = claim_row.owner_private_generation_id
           AND owner_generation.component_scope = 'OWNER_PRIVATE'
           AND owner_generation.owner_user_id = p_owner_user_id
           AND owner_generation.embedding_profile_id = claim_row.embedding_profile_id
           AND owner_generation.state = 'ACTIVE'
           AND owner_generation.evaluation_status = 'PASSED'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner component changed'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    claim_row.scope_claim_id,
    claim_row.owner_user_id,
    claim_row.session_id,
    claim_row.allowed_topics,
    claim_row.exact30_generation_id,
    claim_row.oa112_generation_id,
    claim_row.owner_private_generation_id,
    claim_row.embedding_profile_id,
    claim_row.policy_version;
END;
$resolve_rag_v2_retrieval_scope$;
ALTER FUNCTION resolve_rag_v2_retrieval_scope(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION resolve_rag_v2_retrieval_scope(text, text, text) FROM PUBLIC;

CREATE FUNCTION read_rag_v2_retrieval_scope(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  allowed_topics text[],
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_retrieval_scope$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_query' THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope read is not authorized'
      USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
  SELECT *
  FROM public.resolve_rag_v2_retrieval_scope(p_scope_claim_id, p_owner_user_id, p_session_id);
END;
$read_rag_v2_retrieval_scope$;
ALTER FUNCTION read_rag_v2_retrieval_scope(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope(text, text, text) FROM PUBLIC;

CREATE FUNCTION authorized_rag_v2_retrieval_rows(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[]
)
RETURNS TABLE (
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $authorized_rag_v2_retrieval_rows$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_topics) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval row arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH scope AS (
    SELECT *
    FROM public.resolve_rag_v2_retrieval_scope(p_scope_claim_id, p_owner_user_id, p_session_id)
  )
  SELECT
    chunk.canonical_text,
    chunk.canonical_text_sha256,
    source.canonical_https_url,
    chunk.chunk_id,
    source.document_id,
    scope.embedding_profile_id,
    source.external_processing_eligible,
    membership.component_generation_id,
    chunk.heading_path,
    chunk.locator,
    source.owner_user_id,
    scope.policy_version,
    source.sanitized_display_name,
    scope.scope_claim_id,
    scope.session_id,
    source.source_id,
    source.source_revision_id,
    source.source_scope,
    source.citation_title,
    source.retrieval_topics
  FROM scope
  JOIN public.rag_v2_immutable_generation_memberships AS membership
    ON membership.component_generation_id = ANY(
      ARRAY[
        scope.exact30_generation_id,
        scope.oa112_generation_id,
        scope.owner_private_generation_id
      ]::text[]
    )
  JOIN public.rag_v2_immutable_chunks AS chunk
    ON chunk.chunk_id = membership.chunk_id
   AND chunk.source_revision_id = membership.source_revision_id
   AND chunk.source_scope = membership.component_scope
   AND chunk.owner_partition_key = membership.owner_partition_key
  JOIN public.rag_v2_immutable_source_revisions AS source
    ON source.source_revision_id = membership.source_revision_id
   AND source.source_scope = membership.component_scope
   AND source.owner_partition_key = membership.owner_partition_key
  JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
   AND embedding.component_scope = membership.component_scope
   AND embedding.owner_partition_key = membership.owner_partition_key
   AND embedding.embedding_profile_id = scope.embedding_profile_id
  WHERE p_topics <@ scope.allowed_topics
    AND source.retrieval_topics && p_topics
    AND (
      (
        source.source_scope IN ('EXACT30', 'OA112')
        AND source.owner_user_id IS NULL
        AND source.citation_title IS NOT NULL
        AND source.sanitized_display_name IS NULL
        AND public.rag_v2_immutable_public_https_url_is_valid(source.canonical_https_url)
      )
      OR (
        source.source_scope = 'OWNER_PRIVATE'
        AND source.owner_user_id = scope.owner_user_id
        AND source.citation_title IS NULL
        AND source.sanitized_display_name IS NOT NULL
        AND source.canonical_https_url IS NULL
      )
    )
    AND (
      source.source_scope <> 'OA112'
      OR EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_oa_source_cards AS card
        WHERE card.source_revision_id = source.source_revision_id
          AND card.source_id = source.source_id
          AND card.active_oa112_eligible
          AND card.canonical_https_url = source.canonical_https_url
      )
    );
END;
$authorized_rag_v2_retrieval_rows$;
ALTER FUNCTION authorized_rag_v2_retrieval_rows(text, text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION authorized_rag_v2_retrieval_rows(text, text, text, text[]) FROM PUBLIC;

CREATE FUNCTION search_authorized_rag_v2_exact(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_identifiers text[]
)
RETURNS TABLE (
  rank_no integer,
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_v2_exact$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_identifiers IS NULL
     OR cardinality(p_identifiers) NOT BETWEEN 1 AND 20
     OR cardinality(ARRAY(SELECT DISTINCT identifier FROM unnest(p_identifiers) AS values(identifier))) <> cardinality(p_identifiers)
     OR EXISTS (
       SELECT 1
       FROM unnest(p_identifiers) AS values(identifier)
       WHERE char_length(identifier) NOT BETWEEN 1 AND 256
          OR identifier ~ '[[:cntrl:]]'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 exact retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT *
    FROM public.authorized_rag_v2_retrieval_rows(
      p_scope_claim_id, p_owner_user_id, p_session_id, p_topics
    )
  ), matched AS (
    SELECT
      candidate.*,
      min(
        CASE
          WHEN candidate.source_id = identifier THEN 0
          WHEN lower(coalesce(candidate.citation_title, candidate.sanitized_display_name, '')) = lower(identifier) THEN 1
          ELSE 2
        END
      ) AS match_kind
    FROM candidates AS candidate
    JOIN unnest(p_identifiers) AS values(identifier)
      ON candidate.source_id = identifier
      OR lower(coalesce(candidate.citation_title, candidate.sanitized_display_name, '')) = lower(identifier)
      OR strpos(lower(candidate.canonical_content), lower(identifier)) > 0
    GROUP BY
      candidate.canonical_content,
      candidate.canonical_content_sha256,
      candidate.canonical_https_url,
      candidate.chunk_id,
      candidate.document_id,
      candidate.embedding_profile_id,
      candidate.external_processing_eligible,
      candidate.generation_id,
      candidate.heading_path,
      candidate.locator,
      candidate.candidate_owner_user_id,
      candidate.policy_version,
      candidate.sanitized_display_name,
      candidate.scope_claim_id,
      candidate.session_id,
      candidate.source_id,
      candidate.source_revision_id,
      candidate.source_scope,
      candidate.citation_title,
      candidate.retrieval_topics
  )
  SELECT
    row_number() OVER (
      ORDER BY match_kind, source_id COLLATE "C", chunk_id COLLATE "C"
    )::integer,
    canonical_content,
    canonical_content_sha256,
    canonical_https_url,
    chunk_id,
    document_id,
    embedding_profile_id,
    external_processing_eligible,
    generation_id,
    heading_path,
    locator,
    candidate_owner_user_id,
    policy_version,
    sanitized_display_name,
    scope_claim_id,
    session_id,
    source_id,
    source_revision_id,
    source_scope,
    citation_title,
    retrieval_topics
  FROM matched
  ORDER BY match_kind, source_id COLLATE "C", chunk_id COLLATE "C"
  LIMIT 30;
END;
$search_authorized_rag_v2_exact$;
ALTER FUNCTION search_authorized_rag_v2_exact(text, text, text, text[], text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_exact(text, text, text, text[], text[]) FROM PUBLIC;

CREATE FUNCTION search_authorized_rag_v2_lexical(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_query_text text
)
RETURNS TABLE (
  rank_no integer,
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_v2_lexical$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_query_text IS NULL
     OR octet_length(p_query_text) NOT BETWEEN 1 AND 12288
     OR p_query_text ~ '[[:cntrl:]]' THEN
    RAISE EXCEPTION 'immutable RAG v2 lexical retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT *
    FROM public.authorized_rag_v2_retrieval_rows(
      p_scope_claim_id, p_owner_user_id, p_session_id, p_topics
    )
  ), scored AS (
    SELECT
      candidate.*,
      similarity(
        lower(concat_ws(' ', candidate.source_id, candidate.citation_title, candidate.sanitized_display_name, candidate.canonical_content)),
        lower(p_query_text)
      ) AS lexical_score
    FROM candidates AS candidate
  )
  SELECT
    row_number() OVER (
      ORDER BY lexical_score DESC, source_id COLLATE "C", chunk_id COLLATE "C"
    )::integer,
    canonical_content,
    canonical_content_sha256,
    canonical_https_url,
    chunk_id,
    document_id,
    embedding_profile_id,
    external_processing_eligible,
    generation_id,
    heading_path,
    locator,
    candidate_owner_user_id,
    policy_version,
    sanitized_display_name,
    scope_claim_id,
    session_id,
    source_id,
    source_revision_id,
    source_scope,
    citation_title,
    retrieval_topics
  FROM scored
  WHERE lexical_score >= 0.05
  ORDER BY lexical_score DESC, source_id COLLATE "C", chunk_id COLLATE "C"
  LIMIT 30;
END;
$search_authorized_rag_v2_lexical$;
ALTER FUNCTION search_authorized_rag_v2_lexical(text, text, text, text[], text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_lexical(text, text, text, text[], text) FROM PUBLIC;

CREATE FUNCTION search_authorized_rag_v2_dense(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_query_embedding vector(1024)
)
RETURNS TABLE (
  rank_no integer,
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_v2_dense$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_query_embedding IS NULL
     OR vector_dims(p_query_embedding) <> 1024
     OR vector_norm(p_query_embedding)::text IN ('NaN', 'Infinity', '-Infinity')
     OR abs(vector_norm(p_query_embedding)::double precision - 1.0) > 0.00001 THEN
    RAISE EXCEPTION 'immutable RAG v2 dense retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH scope AS (
    SELECT *
    FROM public.resolve_rag_v2_retrieval_scope(p_scope_claim_id, p_owner_user_id, p_session_id)
  ), candidates AS (
    SELECT
      rows.*,
      embedding.embedding <=> p_query_embedding AS dense_distance
    FROM scope
    JOIN public.rag_v2_immutable_generation_memberships AS membership
      ON membership.component_generation_id = ANY(
        ARRAY[
          scope.exact30_generation_id,
          scope.oa112_generation_id,
          scope.owner_private_generation_id
        ]::text[]
      )
    JOIN public.rag_v2_immutable_chunks AS chunk
      ON chunk.chunk_id = membership.chunk_id
     AND chunk.source_revision_id = membership.source_revision_id
     AND chunk.source_scope = membership.component_scope
     AND chunk.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
     AND source.source_scope = membership.component_scope
     AND source.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_generation_embeddings AS embedding
      ON embedding.component_generation_id = membership.component_generation_id
     AND embedding.chunk_id = membership.chunk_id
     AND embedding.component_scope = membership.component_scope
     AND embedding.owner_partition_key = membership.owner_partition_key
     AND embedding.embedding_profile_id = scope.embedding_profile_id
    CROSS JOIN LATERAL (
      SELECT
        chunk.canonical_text AS canonical_content,
        chunk.canonical_text_sha256 AS canonical_content_sha256,
        source.canonical_https_url,
        chunk.chunk_id,
        source.document_id,
        scope.embedding_profile_id,
        source.external_processing_eligible,
        membership.component_generation_id AS generation_id,
        chunk.heading_path,
        chunk.locator,
        source.owner_user_id AS candidate_owner_user_id,
        scope.policy_version,
        source.sanitized_display_name,
        scope.scope_claim_id,
        scope.session_id,
        source.source_id,
        source.source_revision_id,
        source.source_scope,
        source.citation_title,
        source.retrieval_topics
    ) AS rows
    WHERE p_topics <@ scope.allowed_topics
      AND source.retrieval_topics && p_topics
      AND (
        (
          source.source_scope IN ('EXACT30', 'OA112')
          AND source.owner_user_id IS NULL
          AND source.citation_title IS NOT NULL
          AND source.sanitized_display_name IS NULL
          AND public.rag_v2_immutable_public_https_url_is_valid(source.canonical_https_url)
        )
        OR (
          source.source_scope = 'OWNER_PRIVATE'
          AND source.owner_user_id = scope.owner_user_id
          AND source.citation_title IS NULL
          AND source.sanitized_display_name IS NOT NULL
          AND source.canonical_https_url IS NULL
        )
      )
      AND (
        source.source_scope <> 'OA112'
        OR EXISTS (
          SELECT 1
          FROM public.rag_v2_immutable_oa_source_cards AS card
          WHERE card.source_revision_id = source.source_revision_id
            AND card.source_id = source.source_id
            AND card.active_oa112_eligible
            AND card.canonical_https_url = source.canonical_https_url
        )
      )
  )
  SELECT
    row_number() OVER (
      ORDER BY dense_distance, source_id COLLATE "C", chunk_id COLLATE "C"
    )::integer,
    canonical_content,
    canonical_content_sha256,
    canonical_https_url,
    chunk_id,
    document_id,
    embedding_profile_id,
    external_processing_eligible,
    generation_id,
    heading_path,
    locator,
    candidate_owner_user_id,
    policy_version,
    sanitized_display_name,
    scope_claim_id,
    session_id,
    source_id,
    source_revision_id,
    source_scope,
    citation_title,
    retrieval_topics
  FROM candidates
  WHERE dense_distance <= 0.55
  ORDER BY dense_distance, source_id COLLATE "C", chunk_id COLLATE "C"
  LIMIT 30;
END;
$search_authorized_rag_v2_dense$;
ALTER FUNCTION search_authorized_rag_v2_dense(text, text, text, text[], vector) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_dense(text, text, text, text[], vector) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM PUBLIC;

DO $rag_v2_authorized_retrieval_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM decision_app;
    GRANT EXECUTE ON FUNCTION issue_rag_v2_retrieval_scope(text, text, text[]) TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM decision_rag_query;
    GRANT EXECUTE ON FUNCTION read_rag_v2_retrieval_scope(text, text, text) TO decision_rag_query;
    GRANT EXECUTE ON FUNCTION search_authorized_rag_v2_exact(text, text, text, text[], text[]) TO decision_rag_query;
    GRANT EXECUTE ON FUNCTION search_authorized_rag_v2_lexical(text, text, text, text[], text) TO decision_rag_query;
    GRANT EXECUTE ON FUNCTION search_authorized_rag_v2_dense(text, text, text, text[], vector) TO decision_rag_query;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM decision_rag_admin;
  END IF;
END;
$rag_v2_authorized_retrieval_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_retrieval_scope(text, text, text[]) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope(text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_exact(text, text, text, text[], text[]) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_lexical(text, text, text, text[], text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_dense(text, text, text, text[], vector) FROM PUBLIC;
