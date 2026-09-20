-- 세계 뉴스 어휘 검색을 최근 30일로 제한한다. forward-only.
--
-- V179 로 similarity() 는 해석되게 됐지만 이번엔 시간이 걸렸다. 실측 25.6초,
-- decision_rag_query 의 statement_timeout 은 1500ms 다. 17배를 넘어 RAG 가 계속
-- RETRIEVAL_FAILURE 로 닫혔다.
--
-- 원인은 코퍼스 크기다. world_news_document_versions_v2 가 1,218,291 행이고 trigram
-- 인덱스가 없어 질문마다 전수 스캔한다. 수집기가 분당 돌며 쌓인 결과라, 같은 스택이
-- 오전에는 답하고 저녁에는 닫히는 식으로 서서히 넘어갔다.
--
-- 창을 최근 30일로 자른다. 근거가 둘이다.
--   1. 금융 개념을 묻는 질문에 오래된 기사를 뒤질 이유가 없다. 오래된 뉴스는 개념
--      설명의 근거가 아니라 잡음이다.
--   2. 보관 정책이 이미 일반 뉴스를 firstSeenAt + 30일에 정리하도록 되어 있다(V160).
--      검색 창을 그 정책과 같게 맞추는 것이 일관적이다.
--
-- 하한을 CTE 안에 넣어 DISTINCT ON 이 도는 집합 자체를 줄인다. 뒤에서 거르면 이미 늦다.
-- 본문은 V179 그대로이고 WHERE 절 한 줄만 늘었다. 권한 검사, 토픽 게이트, scope 재확인,
-- 출력 컬럼은 건드리지 않는다.

CREATE OR REPLACE FUNCTION public.search_authorized_world_news_rag_v2(p_scope_claim_id text, p_owner_user_id text, p_session_id text, p_topics text[], p_query_text text)
 RETURNS TABLE(rank_no integer, canonical_content text, canonical_content_sha256 text, canonical_https_url text, chunk_id text, document_id text, embedding_profile_id text, external_processing_eligible boolean, generation_id text, heading_path text[], locator jsonb, candidate_owner_user_id text, policy_version bigint, sanitized_display_name text, scope_claim_id text, session_id text, source_id text, source_revision_id text, source_scope text, citation_title text, retrieval_topics text[])
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
DECLARE scope_row record;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_rag_query' OR p_query_text IS NULL
     OR char_length(p_query_text) NOT BETWEEN 1 AND 1000 OR p_topics IS NULL
     OR NOT ('DATA'=ANY(p_topics)) THEN
    RETURN; END IF;
  SELECT * INTO scope_row FROM public.read_rag_v2_retrieval_scope_v2(
    p_scope_claim_id,p_owner_user_id,p_session_id
  );
  IF NOT FOUND OR NOT ('DATA'=ANY(scope_row.allowed_topics)) THEN RETURN; END IF;
  RETURN QUERY
  WITH latest_version AS (
    SELECT DISTINCT ON (version.document_id) version.*
    FROM public.world_news_document_versions_v2 version
    WHERE version.available_at<=statement_timestamp()
      AND version.available_at>statement_timestamp()-interval '30 days'
      AND version.rag_retrieval_allowed
    ORDER BY version.document_id,version.available_at DESC,version.first_seen_at DESC,version.document_version_id DESC
  ), latest_observation AS (
    SELECT DISTINCT ON (observation.document_version_id) observation.*
    FROM public.world_news_observations_v2 observation
    ORDER BY observation.document_version_id,observation.provider_observed_at DESC
  ), candidates AS (
    SELECT version.*,document.source_id,document.canonical_url,
      similarity(lower(version.canonical_content),lower(p_query_text)) AS score
    FROM latest_version version JOIN public.world_news_documents_v2 document USING(document_id)
    JOIN latest_observation observation USING(document_version_id)
    WHERE version.canonical_content ILIKE '%'||p_query_text||'%'
       OR similarity(lower(version.canonical_content),lower(p_query_text))>=0.05
  )
  SELECT row_number() OVER(ORDER BY candidate.score DESC,candidate.first_seen_at DESC,candidate.document_version_id)::integer,
    candidate.canonical_content,candidate.content_sha256,candidate.canonical_url,
    'rag_v2_chk_'||left(candidate.version_sha256,32),NULL::text,scope_row.embedding_profile_id,
    candidate.external_llm_allowed,scope_row.oa112_generation_id,ARRAY['세계 뉴스']::text[],
    jsonb_build_object('section','world-news-v2'),NULL::text,scope_row.policy_version,NULL::text,
    p_scope_claim_id,p_session_id,candidate.source_id,'srv_news_'||left(candidate.version_sha256,32),
    'WORLD_NEWS',COALESCE(candidate.title,'세계 뉴스'),ARRAY['DATA']::text[]
  FROM candidates candidate ORDER BY candidate.score DESC,candidate.first_seen_at DESC,candidate.document_version_id LIMIT 10;
END $function$
