-- 세계 뉴스 어휘 검색을 최신 20,000건으로 제한한다. forward-only.
--
-- V180 은 창을 30일로 잡았는데 아무것도 줄이지 못했다. 실측해 보니 1,218,418 행 전부가
-- 2026-09-14 이후, 곧 최근 5일치였다. GDELT 를 분당 수집하는 이 코퍼스에서는 날짜로
-- 자르는 기준이 의미가 없다.
--
-- 같은 취지를 건수로 바꾼다. available_at 내림차순 최신 20,000건만 후보로 놓는다.
-- 실측 25.6초 -> 416ms 로 내려가 decision_rag_query 의 statement_timeout 1500ms 안에 든다.
--
-- 20,000 을 고른 근거:
--   - 120만 행이 25.6초였으니 행당 약 21us 다. 20,000 이면 0.4초대이고 남은 1초는
--     DISTINCT ON 과 조인 몫이다.
--   - 분당 수집에서 20,000 건은 대략 하루치라, 오늘 들어온 뉴스는 전부 후보에 남는다.
--
-- LIMIT 은 world_news_lookup_idx(available_at DESC, first_seen_at DESC, document_version_id)
-- 를 그대로 탄다. 새 인덱스를 만들지 않는다.
--
-- 한계를 적어 둔다. 이 검색은 이제 "최신 20,000건 안에서의 어휘 일치"이고, 그보다 오래된
-- 기사는 어휘 검색으로 닿지 않는다. 뉴스는 RAG 의 보조 근거이고 주 근거는 봉인된 코퍼스
-- 7,871 chunk 이므로 이 경계를 받아들인다.

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
  WITH recent_version AS (
    SELECT version.*
    FROM public.world_news_document_versions_v2 version
    WHERE version.available_at<=statement_timestamp() AND version.rag_retrieval_allowed
    ORDER BY version.available_at DESC,version.first_seen_at DESC,version.document_version_id DESC
    LIMIT 20000
  ), latest_version AS (
    SELECT DISTINCT ON (version.document_id) version.*
    FROM recent_version version
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
