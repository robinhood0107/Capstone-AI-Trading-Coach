-- S4.9 LangGraph/Google grounding은 V1~V69와 기존 usage row를 그대로 보존하는 forward-only overlay다.
-- raw query, page body, owner text, model request/response는 어떤 새 table에도 저장하지 않는다.

ALTER TABLE public.s4_9_strong_llm_usage_ledger
  ADD COLUMN usage_schema_version integer NOT NULL DEFAULT 1 CHECK (usage_schema_version IN (1, 2)),
  ADD COLUMN vertex_generate_call_count integer NOT NULL DEFAULT 1 CHECK (vertex_generate_call_count BETWEEN 0 AND 4),
  ADD COLUMN google_grounding_query_count integer NOT NULL DEFAULT 0 CHECK (google_grounding_query_count BETWEEN 0 AND 128),
  ADD COLUMN search_backend text NOT NULL DEFAULT 'NONE'
    CHECK (search_backend IN ('NONE', 'VERTEX_GOOGLE', 'SEARXNG')),
  ADD COLUMN evidence_validation_mode text NOT NULL DEFAULT 'CANONICAL_EXACT'
    CHECK (evidence_validation_mode IN ('CANONICAL_EXACT', 'GOOGLE_GROUNDING', 'MIXED', 'NONE')),
  ADD COLUMN failure_leaf text CHECK (failure_leaf IS NULL OR failure_leaf ~ '^[A-Z0-9_]{3,96}$');

CREATE TABLE public.s4_9_google_grounding_monthly_budget (
  billing_account_fingerprint text NOT NULL,
  billing_period_start date NOT NULL,
  observed_query_count integer NOT NULL DEFAULT 0 CHECK (observed_query_count >= 0),
  reserved_query_count integer NOT NULL DEFAULT 0 CHECK (reserved_query_count >= 0),
  unknown_query_count integer NOT NULL DEFAULT 0 CHECK (unknown_query_count >= 0),
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (billing_account_fingerprint, billing_period_start),
  CHECK (billing_account_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE TABLE public.s4_9_google_grounding_reservations (
  reservation_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  request_id text NOT NULL,
  billing_account_fingerprint text NOT NULL,
  billing_period_start date NOT NULL,
  reserved_query_count integer NOT NULL CHECK (reserved_query_count BETWEEN 1 AND 8),
  actual_query_count integer CHECK (actual_query_count BETWEEN 0 AND 128),
  state text NOT NULL CHECK (state IN ('RESERVED', 'COMMITTED', 'UNKNOWN_BILLING', 'RELEASED')),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  completed_at timestamptz,
  UNIQUE (request_id),
  FOREIGN KEY (billing_account_fingerprint, billing_period_start)
    REFERENCES public.s4_9_google_grounding_monthly_budget(billing_account_fingerprint, billing_period_start),
  CHECK (reservation_id ~ '^s49_gbr_[0-9a-f]{32}$'),
  CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$')
);

CREATE TABLE public.s4_9_grounding_source_nodes (
  source_node_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  request_id text NOT NULL,
  result_id text NOT NULL,
  citation_id text NOT NULL CHECK (citation_id ~ '^cit_[1-5]$'),
  source_type text NOT NULL CHECK (source_type IN ('GOOGLE_GROUNDING','SEARXNG_RESULT','USER_ROOT','DISCOVERED_LINK')),
  title text NOT NULL,
  canonical_url text NOT NULL CHECK (canonical_url ~ '^https://'),
  domain text NOT NULL,
  chunk_index integer,
  content_sha256 text,
  raw_body_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_body_stored),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  UNIQUE (owner_user_id, request_id, result_id),
  CHECK (source_node_id ~ '^s49_src_[0-9a-f]{32}$'),
  CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$'),
  CHECK (result_id ~ '^[a-z][A-Za-z0-9_-]{2,95}$'),
  CHECK (char_length(title) BETWEEN 1 AND 500 AND char_length(domain) BETWEEN 1 AND 253),
  CHECK (chunk_index IS NULL OR chunk_index BETWEEN 0 AND 127),
  CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE public.s4_9_grounding_support_segments (
  support_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  request_id text NOT NULL,
  segment_sha256 text NOT NULL,
  start_index integer NOT NULL CHECK (start_index >= 0),
  end_index integer NOT NULL CHECK (end_index > start_index),
  chunk_indices integer[] NOT NULL CHECK (cardinality(chunk_indices) BETWEEN 1 AND 5),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (support_id ~ '^s49_sup_[0-9a-f]{32}$'),
  CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$'),
  CHECK (segment_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE public.s4_9_grounding_support_edges (
  support_id text NOT NULL REFERENCES public.s4_9_grounding_support_segments(support_id) ON DELETE CASCADE,
  source_node_id text NOT NULL REFERENCES public.s4_9_grounding_source_nodes(source_node_id) ON DELETE CASCADE,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  request_id text NOT NULL,
  PRIMARY KEY (support_id, source_node_id)
);

CREATE TABLE public.s4_9_search_attempts (
  search_attempt_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  request_id text NOT NULL,
  backend text NOT NULL CHECK (backend IN ('VERTEX_GOOGLE','SEARXNG')),
  outcome text NOT NULL CHECK (outcome IN ('COMMITTED','NO_RESULTS','SEARCH_UNAVAILABLE','UNKNOWN_BILLING')),
  result_count integer NOT NULL CHECK (result_count BETWEEN 0 AND 128),
  raw_query_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_query_stored),
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CHECK (search_attempt_id ~ '^s49_sra_[0-9a-f]{32}$'),
  CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$')
);

ALTER TABLE public.s4_9_google_grounding_monthly_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_google_grounding_monthly_budget FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_google_grounding_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_google_grounding_reservations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_source_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_source_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_support_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_support_segments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_support_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_grounding_support_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_search_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.s4_9_search_attempts FORCE ROW LEVEL SECURITY;

CREATE POLICY s4_9_google_budget_definer_policy ON public.s4_9_google_grounding_monthly_budget
  TO flyway USING (true) WITH CHECK (true);
CREATE POLICY s4_9_google_reservation_owner_policy ON public.s4_9_google_grounding_reservations
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_grounding_source_owner_policy ON public.s4_9_grounding_source_nodes
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_grounding_support_owner_policy ON public.s4_9_grounding_support_segments
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_grounding_edge_owner_policy ON public.s4_9_grounding_support_edges
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));
CREATE POLICY s4_9_search_attempt_owner_policy ON public.s4_9_search_attempts
  USING (owner_user_id = nullif(current_setting('app.actor_user_id', true), ''));

CREATE FUNCTION public.reserve_s4_9_google_grounding_budget(
  p_reservation_id text, p_owner_user_id text, p_request_id text,
  p_billing_account_fingerprint text, p_billing_period_start date,
  p_reserve_count integer, p_soft_cap integer
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_s4_9_google_grounding_budget$
DECLARE
  budget_row public.s4_9_google_grounding_monthly_budget%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_reservation_id !~ '^s49_gbr_[0-9a-f]{32}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_billing_account_fingerprint !~ '^[0-9a-f]{64}$'
     OR p_reserve_count NOT BETWEEN 1 AND 8 OR p_soft_cap NOT BETWEEN 1 AND 5000
     OR NOT EXISTS (SELECT 1 FROM public.users u WHERE u.user_id = p_owner_user_id AND u.status = 'ACTIVE') THEN
    RAISE EXCEPTION 'S4.9 Google budget reservation is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_google_grounding_monthly_budget(
    billing_account_fingerprint, billing_period_start
  ) VALUES (p_billing_account_fingerprint, p_billing_period_start)
  ON CONFLICT DO NOTHING;
  SELECT * INTO budget_row FROM public.s4_9_google_grounding_monthly_budget
  WHERE billing_account_fingerprint = p_billing_account_fingerprint
    AND billing_period_start = p_billing_period_start
  FOR UPDATE;
  IF budget_row.observed_query_count + budget_row.reserved_query_count
       + budget_row.unknown_query_count + p_reserve_count > p_soft_cap THEN
    RETURN false;
  END IF;
  UPDATE public.s4_9_google_grounding_monthly_budget
  SET reserved_query_count = reserved_query_count + p_reserve_count,
      updated_at = transaction_timestamp()
  WHERE billing_account_fingerprint = p_billing_account_fingerprint
    AND billing_period_start = p_billing_period_start;
  INSERT INTO public.s4_9_google_grounding_reservations(
    reservation_id, owner_user_id, request_id, billing_account_fingerprint,
    billing_period_start, reserved_query_count, state
  ) VALUES (
    p_reservation_id, p_owner_user_id, p_request_id, p_billing_account_fingerprint,
    p_billing_period_start, p_reserve_count, 'RESERVED'
  );
  RETURN true;
END
$reserve_s4_9_google_grounding_budget$;
ALTER FUNCTION public.reserve_s4_9_google_grounding_budget(text,text,text,text,date,integer,integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_s4_9_google_grounding_budget(text,text,text,text,date,integer,integer) FROM PUBLIC;

CREATE FUNCTION public.settle_s4_9_google_grounding_budget(
  p_owner_user_id text, p_reservation_id text, p_outcome text, p_actual_query_count integer
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $settle_s4_9_google_grounding_budget$
DECLARE
  reservation public.s4_9_google_grounding_reservations%ROWTYPE;
  applied_count integer;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_outcome NOT IN ('COMMITTED','UNKNOWN_BILLING','RELEASED') THEN
    RAISE EXCEPTION 'S4.9 Google budget settlement is invalid' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation FROM public.s4_9_google_grounding_reservations
  WHERE reservation_id = p_reservation_id AND owner_user_id = p_owner_user_id FOR UPDATE;
  IF NOT FOUND OR reservation.state <> 'RESERVED' THEN
    RAISE EXCEPTION 'S4.9 Google reservation is unavailable' USING ERRCODE = '55000';
  END IF;
  IF p_outcome = 'COMMITTED' AND p_actual_query_count NOT BETWEEN 0 AND 128 THEN
    RAISE EXCEPTION 'S4.9 actual query count is invalid' USING ERRCODE = '22023';
  END IF;
  applied_count := CASE WHEN p_outcome = 'UNKNOWN_BILLING' THEN reservation.reserved_query_count
                        WHEN p_outcome = 'RELEASED' THEN 0 ELSE p_actual_query_count END;
  UPDATE public.s4_9_google_grounding_monthly_budget
  SET reserved_query_count = reserved_query_count - reservation.reserved_query_count,
      observed_query_count = observed_query_count + CASE WHEN p_outcome = 'COMMITTED' THEN applied_count ELSE 0 END,
      unknown_query_count = unknown_query_count + CASE WHEN p_outcome = 'UNKNOWN_BILLING' THEN applied_count ELSE 0 END,
      updated_at = transaction_timestamp()
  WHERE billing_account_fingerprint = reservation.billing_account_fingerprint
    AND billing_period_start = reservation.billing_period_start;
  UPDATE public.s4_9_google_grounding_reservations
  SET state = p_outcome, actual_query_count = applied_count, completed_at = transaction_timestamp()
  WHERE reservation_id = p_reservation_id;
END
$settle_s4_9_google_grounding_budget$;
ALTER FUNCTION public.settle_s4_9_google_grounding_budget(text,text,text,integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.settle_s4_9_google_grounding_budget(text,text,text,integer) FROM PUBLIC;

CREATE FUNCTION public.record_s4_9_grounding_provenance(
  p_owner_user_id text, p_request_id text, p_sources jsonb, p_supports jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_grounding_provenance$
DECLARE
  item jsonb;
  source_count integer := 0;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR jsonb_typeof(p_sources) <> 'array' OR jsonb_array_length(p_sources) NOT BETWEEN 1 AND 5
     OR jsonb_typeof(p_supports) <> 'array' OR jsonb_array_length(p_supports) NOT BETWEEN 1 AND 64
     OR octet_length(p_sources::text) > 32768 OR octet_length(p_supports::text) > 65536 THEN
    RAISE EXCEPTION 'S4.9 grounding provenance arguments are invalid' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(p_sources)
  LOOP
    IF jsonb_typeof(item) <> 'object'
       OR NOT (item ?& ARRAY['sourceNodeId','resultId','citationId','title','canonicalUrl','domain','chunkIndex'])
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(item) AS key_name
         WHERE key_name NOT IN ('sourceNodeId','resultId','citationId','title','canonicalUrl','domain','chunkIndex')
       )
       OR item ->> 'sourceNodeId' !~ '^s49_src_[0-9a-f]{32}$'
       OR item ->> 'resultId' !~ '^google_[1-9][0-9]{0,2}$'
       OR item ->> 'citationId' !~ '^cit_[1-5]$'
       OR jsonb_typeof(item -> 'chunkIndex') <> 'number'
       OR (item ->> 'chunkIndex')::integer NOT BETWEEN 0 AND 127
       OR char_length(item ->> 'title') NOT BETWEEN 1 AND 500
       OR item ->> 'canonicalUrl' !~ '^https://'
       OR octet_length(item ->> 'canonicalUrl') NOT BETWEEN 9 AND 2048
       OR char_length(item ->> 'domain') NOT BETWEEN 1 AND 253 THEN
      RAISE EXCEPTION 'S4.9 grounding source is invalid' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.s4_9_grounding_source_nodes(
      source_node_id, owner_user_id, request_id, result_id, citation_id, source_type,
      title, canonical_url, domain, chunk_index
    ) VALUES (
      item ->> 'sourceNodeId', p_owner_user_id, p_request_id, item ->> 'resultId',
      item ->> 'citationId', 'GOOGLE_GROUNDING', item ->> 'title', item ->> 'canonicalUrl',
      item ->> 'domain', (item ->> 'chunkIndex')::integer
    );
    source_count := source_count + 1;
  END LOOP;

  FOR item IN SELECT value FROM jsonb_array_elements(p_supports)
  LOOP
    IF jsonb_typeof(item) <> 'object'
       OR NOT (item ?& ARRAY['supportId','segmentSha256','startIndex','endIndex','chunkIndices'])
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(item) AS key_name
         WHERE key_name NOT IN ('supportId','segmentSha256','startIndex','endIndex','chunkIndices')
       )
       OR item ->> 'supportId' !~ '^s49_sup_[0-9a-f]{32}$'
       OR item ->> 'segmentSha256' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(item -> 'startIndex') <> 'number'
       OR jsonb_typeof(item -> 'endIndex') <> 'number'
       OR (item ->> 'startIndex')::integer < 0
       OR (item ->> 'endIndex')::integer <= (item ->> 'startIndex')::integer
       OR jsonb_typeof(item -> 'chunkIndices') <> 'array'
       OR jsonb_array_length(item -> 'chunkIndices') NOT BETWEEN 1 AND 5
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(item -> 'chunkIndices') AS chunk(value)
         WHERE jsonb_typeof(chunk.value) <> 'number' OR (chunk.value #>> '{}')::integer NOT BETWEEN 0 AND 127
       ) THEN
      RAISE EXCEPTION 'S4.9 grounding support is invalid' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.s4_9_grounding_support_segments(
      support_id, owner_user_id, request_id, segment_sha256, start_index, end_index, chunk_indices
    ) VALUES (
      item ->> 'supportId', p_owner_user_id, p_request_id, item ->> 'segmentSha256',
      (item ->> 'startIndex')::integer, (item ->> 'endIndex')::integer,
      ARRAY(SELECT (value #>> '{}')::integer FROM jsonb_array_elements(item -> 'chunkIndices'))
    );
    INSERT INTO public.s4_9_grounding_support_edges(support_id, source_node_id, owner_user_id, request_id)
    SELECT item ->> 'supportId', source.source_node_id, p_owner_user_id, p_request_id
    FROM public.s4_9_grounding_source_nodes AS source
    WHERE source.owner_user_id = p_owner_user_id AND source.request_id = p_request_id
      AND source.chunk_index = ANY(
        ARRAY(SELECT (value #>> '{}')::integer FROM jsonb_array_elements(item -> 'chunkIndices'))
      );
    IF NOT FOUND THEN
      RAISE EXCEPTION 'S4.9 grounding support has no source edge' USING ERRCODE = '22023';
    END IF;
  END LOOP;
  IF source_count <> jsonb_array_length(p_sources) THEN
    RAISE EXCEPTION 'S4.9 grounding source count changed' USING ERRCODE = '55000';
  END IF;
END
$record_s4_9_grounding_provenance$;
ALTER FUNCTION public.record_s4_9_grounding_provenance(text,text,jsonb,jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_grounding_provenance(text,text,jsonb,jsonb) FROM PUBLIC;

CREATE FUNCTION public.record_s4_9_read_provenance(
  p_owner_user_id text, p_request_id text, p_source_node_id text, p_result_id text,
  p_citation_id text, p_source_type text, p_title text, p_canonical_url text,
  p_domain text, p_content_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_read_provenance$
DECLARE
  support_id text := 's49_sup_' || substring(p_source_node_id from 9 for 32);
  chunk_index integer := substring(p_citation_id from 5)::integer - 1;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_source_node_id !~ '^s49_src_[0-9a-f]{32}$'
     OR p_result_id !~ '^[a-z][A-Za-z0-9_-]{2,95}$'
     OR p_citation_id !~ '^cit_[1-5]$'
     OR p_source_type NOT IN ('SEARXNG_RESULT','USER_ROOT','DISCOVERED_LINK')
     OR char_length(p_title) NOT BETWEEN 1 AND 500
     OR p_canonical_url !~ '^https://' OR octet_length(p_canonical_url) NOT BETWEEN 9 AND 2048
     OR char_length(p_domain) NOT BETWEEN 1 AND 253
     OR p_content_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S4.9 read provenance arguments are invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_grounding_source_nodes(
    source_node_id,owner_user_id,request_id,result_id,citation_id,source_type,title,
    canonical_url,domain,chunk_index,content_sha256
  ) VALUES (
    p_source_node_id,p_owner_user_id,p_request_id,p_result_id,p_citation_id,p_source_type,p_title,
    p_canonical_url,p_domain,chunk_index,p_content_sha256
  );
  INSERT INTO public.s4_9_grounding_support_segments(
    support_id,owner_user_id,request_id,segment_sha256,start_index,end_index,chunk_indices
  ) VALUES (support_id,p_owner_user_id,p_request_id,p_content_sha256,0,64,ARRAY[chunk_index]);
  INSERT INTO public.s4_9_grounding_support_edges(support_id,source_node_id,owner_user_id,request_id)
  VALUES (support_id,p_source_node_id,p_owner_user_id,p_request_id);
END
$record_s4_9_read_provenance$;
ALTER FUNCTION public.record_s4_9_read_provenance(text,text,text,text,text,text,text,text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_read_provenance(text,text,text,text,text,text,text,text,text,text) FROM PUBLIC;

CREATE FUNCTION public.record_s4_9_search_attempt(
  p_search_attempt_id text, p_owner_user_id text, p_request_id text,
  p_backend text, p_outcome text, p_result_count integer
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_search_attempt$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_search_attempt_id !~ '^s49_sra_[0-9a-f]{32}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_backend NOT IN ('VERTEX_GOOGLE','SEARXNG')
     OR p_outcome NOT IN ('COMMITTED','NO_RESULTS','SEARCH_UNAVAILABLE','UNKNOWN_BILLING')
     OR p_result_count NOT BETWEEN 0 AND 128 THEN
    RAISE EXCEPTION 'S4.9 search attempt arguments are invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.s4_9_search_attempts(
    search_attempt_id,owner_user_id,request_id,backend,outcome,result_count
  ) VALUES (p_search_attempt_id,p_owner_user_id,p_request_id,p_backend,p_outcome,p_result_count);
END
$record_s4_9_search_attempt$;
ALTER FUNCTION public.record_s4_9_search_attempt(text,text,text,text,text,integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_search_attempt(text,text,text,text,text,integer) FROM PUBLIC;

CREATE FUNCTION public.canonicalize_s4_9_strong_llm_citations_v2(
  p_owner_user_id text, p_request_id text, p_session_id text, p_scope_claim_id text, p_citations jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $canonicalize_s4_9_strong_llm_citations_v2$
DECLARE
  item jsonb;
  canonical_one jsonb;
  source_row public.s4_9_grounding_source_nodes%ROWTYPE;
  output jsonb := '[]'::jsonb;
  expected_ordinal integer := 1;
  seen_keys text[] := ARRAY[]::text[];
  receipt_key text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR jsonb_typeof(p_citations) <> 'array' OR jsonb_array_length(p_citations) NOT BETWEEN 1 AND 5
     OR octet_length(p_citations::text) > 32768 THEN
    RAISE EXCEPTION 'S4.9 v2 citation arguments are invalid' USING ERRCODE = '22023';
  END IF;

  FOR item IN SELECT value FROM jsonb_array_elements(p_citations)
  LOOP
    IF jsonb_typeof(item) <> 'object'
       OR item ->> 'ordinal' <> expected_ordinal::text
       OR item ->> 'citationId' <> ('cit_' || expected_ordinal::text)
       OR item ->> 'citationKind' NOT IN ('PUBLIC_WEB','LOCAL_DOCUMENT') THEN
      RAISE EXCEPTION 'S4.9 v2 citation order is invalid' USING ERRCODE = '22023';
    END IF;

    IF item ? 'provenanceResultId' THEN
      IF NOT (item ?& ARRAY['sourceId','title','canonicalUrl','locator'])
         OR item ->> 'citationKind' <> 'PUBLIC_WEB'
         OR item ->> 'provenanceResultId' !~ '^google_[1-9][0-9]{0,2}$'
         OR item ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
         OR jsonb_typeof(item -> 'locator') <> 'object'
         OR NOT (item -> 'locator' ? 'section') THEN
        RAISE EXCEPTION 'S4.9 web citation receipt is invalid' USING ERRCODE = '22023';
      END IF;
      SELECT * INTO source_row FROM public.s4_9_grounding_source_nodes AS source
      WHERE source.owner_user_id = p_owner_user_id AND source.request_id = p_request_id
        AND source.result_id = item ->> 'provenanceResultId'
        AND source.title = item ->> 'title'
        AND source.canonical_url = item ->> 'canonicalUrl'
        AND source.domain = item -> 'locator' ->> 'section'
        AND EXISTS (
          SELECT 1 FROM public.s4_9_grounding_support_edges AS edge
          WHERE edge.owner_user_id = p_owner_user_id AND edge.request_id = p_request_id
            AND edge.source_node_id = source.source_node_id
        );
      IF NOT FOUND THEN
        RAISE EXCEPTION 'S4.9 web citation is outside grounded provenance' USING ERRCODE = '55000';
      END IF;
      receipt_key := 'web:' || source_row.source_node_id;
      canonical_one := jsonb_build_object(
        'citationKind','PUBLIC_WEB', 'citationId',item ->> 'citationId',
        'sourceId',item ->> 'sourceId', 'title',source_row.title,
        'canonicalUrl',source_row.canonical_url, 'locator',jsonb_build_object('section',source_row.domain)
      );
    ELSE
      IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(item) AS key_name
        WHERE key_name NOT IN (
          'ordinal','citationId','sourceId','sourceRevisionId','chunkRevisionId','generationId','citationKind'
        )
      ) THEN
        RAISE EXCEPTION 'S4.9 canonical citation receipt is invalid' USING ERRCODE = '22023';
      END IF;
      canonical_one := public.canonicalize_rag_v2_immutable_retrieval_citations(
        p_owner_user_id, p_session_id, p_scope_claim_id,
        jsonb_build_array(
          jsonb_build_object(
            'ordinal',1, 'citationId','cit_1', 'sourceId',item ->> 'sourceId',
            'sourceRevisionId',item ->> 'sourceRevisionId',
            'chunkRevisionId',item ->> 'chunkRevisionId', 'generationId',item ->> 'generationId',
            'citationKind',item ->> 'citationKind'
          )
        )
      ) -> 0;
      receipt_key := 'canonical:' || (canonical_one ->> 'chunkRevisionId');
      canonical_one := canonical_one || jsonb_build_object('citationId',item ->> 'citationId');
    END IF;
    IF receipt_key = ANY(seen_keys) THEN
      RAISE EXCEPTION 'S4.9 v2 citation is duplicated' USING ERRCODE = '22023';
    END IF;
    seen_keys := array_append(seen_keys, receipt_key);
    output := output || jsonb_build_array(canonical_one);
    expected_ordinal := expected_ordinal + 1;
  END LOOP;
  RETURN output;
END
$canonicalize_s4_9_strong_llm_citations_v2$;
ALTER FUNCTION public.canonicalize_s4_9_strong_llm_citations_v2(text,text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.canonicalize_s4_9_strong_llm_citations_v2(text,text,text,text,jsonb) FROM PUBLIC;

CREATE FUNCTION public.persist_s4_9_strong_llm_history_v2(
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
AS $persist_s4_9_strong_llm_history_v2$
DECLARE
  canonical_citations jsonb := '[]'::jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_answer_mode NOT IN ('CONCISE','DETAILED')
     OR p_answer_basis NOT IN ('EVIDENCE','MODEL_KNOWLEDGE')
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$'
     OR octet_length(p_wrap_nonce) <> 12 OR octet_length(p_wrapped_dek) <> 32
     OR octet_length(p_wrap_tag) <> 16 OR octet_length(p_question_nonce) <> 12
     OR octet_length(p_question_ciphertext) NOT BETWEEN 1 AND 8192 OR octet_length(p_question_tag) <> 16
     OR octet_length(p_answer_nonce) <> 12 OR octet_length(p_answer_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_answer_tag) <> 16
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds'
       AND transaction_timestamp() + interval '60 seconds' THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 history arguments are invalid' USING ERRCODE = '22023';
  END IF;
  IF p_answer_basis = 'EVIDENCE' THEN
    IF p_citation_coverage < 0.8 OR coalesce(cardinality(p_guardrail_flags), 0) > 6
       OR NOT coalesce(p_guardrail_flags, ARRAY[]::text[]) <@ ARRAY[
         'SINGLE_SOURCE','STALE_SOURCE','CONFLICTING_SOURCES','LOW_RELEVANCE',
         'SECONDARY_SOURCE','GOOGLE_GROUNDING_ONLY'
       ]::text[] THEN
      RAISE EXCEPTION 'S4.9 v2 evidence history arguments are invalid' USING ERRCODE = '22023';
    END IF;
    canonical_citations := public.canonicalize_s4_9_strong_llm_citations_v2(
      p_owner_user_id, p_request_id, p_session_id, p_scope_claim_id, p_citations
    );
  ELSE
    IF p_citation_coverage <> 0.0 OR p_guardrail_flags <> ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]
       OR p_citations <> '[]'::jsonb THEN
      RAISE EXCEPTION 'S4.9 v2 model knowledge arguments are invalid' USING ERRCODE = '22023';
    END IF;
  END IF;

  SELECT * INTO claim_row FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 scope disappeared' USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_answer_history(
    answer_id,owner_user_id,request_id,answer_mode,generation_status,citation_coverage,
    retrieval_failure,guardrail_flags,public_corpus_version,private_overlay_state,kek_version,
    wrap_nonce,wrapped_dek,wrap_tag,question_nonce,question_ciphertext,question_tag,
    answer_nonce,answer_ciphertext,answer_tag,citation_count,created_at,expires_at
  ) VALUES (
    p_answer_id,p_owner_user_id,p_request_id,p_answer_mode,'ANSWERED',p_citation_coverage,
    false,p_guardrail_flags,'immutable-v2-' || claim_row.public_pointer_version::text,
    CASE WHEN claim_row.owner_private_generation_id IS NULL THEN 'ABSENT' ELSE 'READY' END,
    p_kek_version,p_wrap_nonce,p_wrapped_dek,p_wrap_tag,p_question_nonce,p_question_ciphertext,
    p_question_tag,p_answer_nonce,p_answer_ciphertext,p_answer_tag,jsonb_array_length(canonical_citations),
    p_created_at,p_created_at + interval '30 days'
  );
  INSERT INTO public.rag_v2_answer_citations(
    answer_id,owner_user_id,ordinal,citation_kind,source_id,title,canonical_url,
    document_id,sanitized_display_name,locator
  )
  SELECT p_answer_id,p_owner_user_id,ordinal::integer,citation.value ->> 'citationKind',
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'sourceId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'title' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'canonicalUrl' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'documentId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'displayName' END,
    citation.value -> 'locator'
  FROM jsonb_array_elements(canonical_citations) WITH ORDINALITY AS citation(value,ordinal)
  ORDER BY ordinal;
  RETURN canonical_citations;
END
$persist_s4_9_strong_llm_history_v2$;
ALTER FUNCTION public.persist_s4_9_strong_llm_history_v2(
  text,text,text,text,text,text,text,double precision,text[],text,
  bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.persist_s4_9_strong_llm_history_v2(
  text,text,text,text,text,text,text,double precision,text[],text,
  bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
) FROM PUBLIC;

CREATE FUNCTION public.record_s4_9_strong_llm_usage_v2(
  p_usage_event_id text, p_owner_user_id text, p_request_id text, p_model_id text,
  p_answer_basis text, p_outcome text, p_tool_round_count integer,
  p_search_call_count integer, p_read_call_count integer,
  p_prompt_token_count integer, p_output_token_count integer, p_evidence_set_sha256 text,
  p_vertex_generate_call_count integer, p_google_grounding_query_count integer,
  p_search_backend text, p_evidence_validation_mode text, p_failure_leaf text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_strong_llm_usage_v2$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^s49_llu_[0-9a-f]{32}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_model_id !~ '^[a-z][a-z0-9.-]{2,127}$'
     OR p_outcome NOT IN ('COMMITTED','REJECTED','UNKNOWN_BILLING')
     OR p_tool_round_count NOT BETWEEN 0 AND 3
     OR p_search_call_count NOT BETWEEN 0 AND 3 OR p_read_call_count NOT BETWEEN 0 AND 8
     OR p_vertex_generate_call_count NOT BETWEEN 0 AND 4
     OR p_google_grounding_query_count NOT BETWEEN 0 AND 128
     OR p_search_backend NOT IN ('NONE','VERTEX_GOOGLE','SEARXNG')
     OR p_evidence_validation_mode NOT IN ('CANONICAL_EXACT','GOOGLE_GROUNDING','MIXED','NONE')
     OR p_evidence_set_sha256 !~ '^[0-9a-f]{64}$'
     OR (p_failure_leaf IS NOT NULL AND p_failure_leaf !~ '^[A-Z0-9_]{3,96}$') THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 usage is invalid' USING ERRCODE = '22023';
  END IF;
  IF p_outcome = 'COMMITTED' THEN
    IF p_answer_basis NOT IN ('EVIDENCE','MODEL_KNOWLEDGE','INSUFFICIENT_EVIDENCE')
       OR p_prompt_token_count NOT BETWEEN 0 AND 500000 OR p_output_token_count NOT BETWEEN 0 AND 100000
       OR p_failure_leaf IS NOT NULL THEN
      RAISE EXCEPTION 'S4.9 committed v2 usage is invalid' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF p_answer_basis IS NOT NULL OR p_prompt_token_count IS NOT NULL OR p_output_token_count IS NOT NULL
       OR p_failure_leaf IS NULL THEN
      RAISE EXCEPTION 'S4.9 failed v2 usage is invalid' USING ERRCODE = '22023';
    END IF;
  END IF;
  INSERT INTO public.s4_9_strong_llm_usage_ledger(
    usage_event_id, owner_user_id, request_id, provider, model_id, answer_basis, outcome,
    tool_round_count, search_call_count, read_call_count, prompt_token_count, output_token_count,
    evidence_set_sha256, usage_schema_version, vertex_generate_call_count,
    google_grounding_query_count, search_backend, evidence_validation_mode, failure_leaf
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, 'VERTEX_AI', p_model_id, p_answer_basis, p_outcome,
    p_tool_round_count, p_search_call_count, p_read_call_count, p_prompt_token_count, p_output_token_count,
    p_evidence_set_sha256, 2, p_vertex_generate_call_count,
    p_google_grounding_query_count, p_search_backend, p_evidence_validation_mode, p_failure_leaf
  );
END
$record_s4_9_strong_llm_usage_v2$;
ALTER FUNCTION public.record_s4_9_strong_llm_usage_v2(
  text,text,text,text,text,text,integer,integer,integer,integer,integer,text,integer,integer,text,text,text
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.record_s4_9_strong_llm_usage_v2(
  text,text,text,text,text,text,integer,integer,integer,integer,integer,text,integer,integer,text,text,text
) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE
  public.s4_9_google_grounding_monthly_budget,
  public.s4_9_google_grounding_reservations,
  public.s4_9_grounding_source_nodes,
  public.s4_9_grounding_support_segments,
  public.s4_9_grounding_support_edges,
  public.s4_9_search_attempts
FROM PUBLIC;

DO $grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      public.s4_9_google_grounding_monthly_budget,
      public.s4_9_google_grounding_reservations,
      public.s4_9_grounding_source_nodes,
      public.s4_9_grounding_support_segments,
      public.s4_9_grounding_support_edges,
      public.s4_9_search_attempts
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION public.reserve_s4_9_google_grounding_budget(text,text,text,text,date,integer,integer)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.settle_s4_9_google_grounding_budget(text,text,text,integer)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_grounding_provenance(text,text,jsonb,jsonb)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_read_provenance(text,text,text,text,text,text,text,text,text,text)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_search_attempt(text,text,text,text,text,integer)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.canonicalize_s4_9_strong_llm_citations_v2(text,text,text,text,jsonb)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.persist_s4_9_strong_llm_history_v2(
      text,text,text,text,text,text,text,double precision,text[],text,
      bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamptz,jsonb
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.record_s4_9_strong_llm_usage_v2(
      text,text,text,text,text,text,integer,integer,integer,integer,integer,text,integer,integer,text,text,text
    ) TO decision_app;
  END IF;
END
$grants$;
