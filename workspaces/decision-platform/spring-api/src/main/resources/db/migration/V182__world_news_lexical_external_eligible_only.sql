-- 외부 LLM 에 보낼 수 없는 뉴스를 생성 후보에서 뺀다. forward-only.
--
-- V181 로 세계 뉴스 어휘 검색이 살아나자 그 인용이 top-5 를 차지했다. 그런데
-- world_news_document_versions_v2 는 1,219,507 행 전부가 external_llm_allowed = false 다.
-- RAG 검색은 허용하되 외부 LLM 전송은 금지하는 데이터 거버넌스 경계이고, 이 경계는 옳다.
--
-- 문제는 그 인용이 후보에 섞이면 생성이 통째로 거부된다는 점이다.
-- read_rag_v2_vertex_generation_evidence_legacy_v87 은 external_llm_allowed 가 아닌 뉴스를
-- 만나면 'RAG v2 evidence is not externally eligible' 로 예외를 던지고, 호출하는 Kotlin 은
-- evidence.size == citations.size 를 요구하므로 하나만 걸려도 전부 닫힌다. 그 예외는
-- RagV2VertexEvidenceUnavailableException 으로 바뀌어 로그 한 줄 없이 사라진다.
--
-- 그래서 증상이 "검색은 되는데 답이 없다" 였다. 화면에는 근거만 보이고 설명이 없었다.
--
-- 외부로 보낼 수 없는 근거는 애초에 생성 후보로 올리지 않는다. 지금은 그런 뉴스가 0건이라
-- 세계 뉴스가 후보에서 빠지고 봉인 코퍼스 7,871 chunk 가 인용을 공급한다. 나중에 외부
-- 반출이 허용된 뉴스가 생기면 이 조건이 그것만 자동으로 통과시킨다.
--
-- 자동매매의 뉴스 거부권은 이 함수를 쓰지 않는다. 그쪽은 p1_read_world_news_evidence_v1
-- (V177) 이므로 영향이 없다.

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
      AND version.external_llm_allowed
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
