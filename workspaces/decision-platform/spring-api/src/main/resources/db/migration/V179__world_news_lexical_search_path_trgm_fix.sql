-- 금융 Agent 가 무엇을 물어도 "충분한 근거를 찾지 못했습니다" 로 끝나던 원인을 닫는다.
-- forward-only.
--
-- 증상: 모든 질문이 generationStatus=RETRIEVAL_FAILURE 로 돌아왔다. Spring 로그에는
-- `RAG_RETRIEVAL_CHANNEL_UNAVAILABLE` 만 남고 파이썬은 예외를 삼켜 이유가 보이지 않았다.
-- PostgreSQL 로그에 진짜 원인이 있었다:
--   ERROR: function similarity(text, text) does not exist
--
-- 원인: `search_authorized_world_news_rag_v2` 만 `SET search_path TO 'pg_catalog'` 다.
-- pg_trgm 은 public 스키마에 설치돼 있어 similarity() 가 해석되지 않는다. 형제 함수
-- 둘은 처음부터 `pg_catalog, public, pg_temp` 였다.
--   search_authorized_rag_lexical        pg_catalog, public, pg_temp
--   search_authorized_rag_v2_lexical     pg_catalog, public, pg_temp
--   search_authorized_world_news_rag_v2  pg_catalog            <- 이것만 다르다
--
-- 세계 뉴스 검색이 RAG 경로에 들어오면서 이 차이가 드러났고, 세 갈래 RRF 중 trigram
-- 갈래가 죽어 검색 전체가 fail-closed 로 닫혔다.
--
-- 본문은 살아 있는 정의를 그대로 옮기고 SET 절만 형제 함수에 맞춘다. 권한 검사
-- (current_user='flyway', session_user='decision_rag_query'), 토픽 게이트, scope 재확인은
-- 원본 그대로다. CREATE OR REPLACE 는 OWNER 와 ACL 을 보존한다.

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
    WHERE version.available_at<=statement_timestamp() AND version.rag_retrieval_allowed
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
