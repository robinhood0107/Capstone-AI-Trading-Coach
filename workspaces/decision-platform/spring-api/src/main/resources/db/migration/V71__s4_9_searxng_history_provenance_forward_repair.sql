-- V70은 Google grounding ID만 history citation으로 허용해, 같은 host가 기록한
-- SearXNG/USER_ROOT/DISCOVERED_LINK read provenance가 최종 history 단계에서 거부됐다.
-- 기존 row와 함수를 삭제하지 않고 canonicalizer의 source-type 결속만 forward 확장한다.
CREATE OR REPLACE FUNCTION public.canonicalize_s4_9_strong_llm_citations_v2(
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
         OR NOT (
           item ->> 'provenanceResultId' ~ '^google_[1-9][0-9]{0,2}$'
           OR item ->> 'provenanceResultId' ~ '^searxng_[0-9a-f]{24}$'
           OR item ->> 'provenanceResultId' ~ '^user_[0-9a-f]{24}$'
           OR item ->> 'provenanceResultId' ~ '^link_[0-9a-f]{24}$'
         )
         OR item ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
         OR jsonb_typeof(item -> 'locator') <> 'object'
         OR NOT (item -> 'locator' ? 'section') THEN
        RAISE EXCEPTION 'S4.9 web citation receipt is invalid' USING ERRCODE = '22023';
      END IF;
      SELECT * INTO source_row FROM public.s4_9_grounding_source_nodes AS source
      WHERE source.owner_user_id = p_owner_user_id AND source.request_id = p_request_id
        AND source.result_id = item ->> 'provenanceResultId'
        AND (
          (source.source_type = 'GOOGLE_GROUNDING' AND source.result_id ~ '^google_[1-9][0-9]{0,2}$')
          OR (source.source_type = 'SEARXNG_RESULT' AND source.result_id ~ '^searxng_[0-9a-f]{24}$')
          OR (source.source_type = 'USER_ROOT' AND source.result_id ~ '^user_[0-9a-f]{24}$')
          OR (source.source_type = 'DISCOVERED_LINK' AND source.result_id ~ '^link_[0-9a-f]{24}$')
        )
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
