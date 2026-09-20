-- 세계 뉴스 조회를 코퍼스 크기와 무관하게 만든다.
--
-- V157 의 본문은 10 건을 돌려주려고 **모든 문서의 최신 버전**과 **모든 버전의 최신
-- 관측**을 CTE 로 통째로 만든 뒤 조인하고 잘랐다. LIMIT 이 아래로 내려가지 않아 비용이
-- 코퍼스에 비례한다. 실측: 버전 91,891 행에서 400ms, 138,190 행에서 1,558ms(정렬이
-- 디스크로 6MB 스필). 2026-09-14 수집이 한 사이클에 8,986 건을 적재하는 동안 이 조회가
-- 503 으로 떨어졌다. 30 일 보존 × 일 42 만 행이면 정상 상태가 천만 행대라 확실히 무너진다.
--
-- 그런데 그 전수 스캔이 실제로 제거하는 중복은 거의 없다. 실측:
--   문서 138,186 개가 버전 1 개, 2 개만 버전 2 개
--   버전 137,992 개가 관측 1 개, 198 개만 관측 2 개
-- 즉 138,000 행을 훑어 200 행어치 중복을 지우고 있었다.
--
-- 그래서 의미는 그대로 두고 순서만 뒤집는다 - 최신순 인덱스를 걸어 두고 위에서부터
-- p_limit 개가 채워질 때까지만 본다. "그 문서의 더 최신 버전이 없다"는 조건은 행마다
-- NOT EXISTS 로 확인하고(거의 항상 즉시 참), 관측은 선택된 행에만 LATERAL 로 붙인다.
-- 비용이 O(p_limit x log n) 이 되어 코퍼스가 커져도 변하지 않는다.
--
-- 시그니처·반환 컬럼·권한·게이트는 V157 그대로다. 계약 변경 0.
SET LOCAL row_security = on;

-- 최신순 top-N 을 인덱스 순서로 바로 걷기 위한 것. ORDER BY 식과 정확히 같아야 한다.
CREATE INDEX IF NOT EXISTS world_news_recency_idx
  ON public.world_news_document_versions_v2
  ((COALESCE(published_at, first_seen_at)) DESC, first_seen_at DESC, document_id)
  WHERE lookup_allowed;

-- "이 문서의 더 최신 버전이 있는가"를 한 번의 인덱스 탐색으로 끝내기 위한 것.
CREATE INDEX IF NOT EXISTS world_news_current_version_idx
  ON public.world_news_document_versions_v2
  (document_id, available_at DESC, first_seen_at DESC, document_version_id DESC)
  WHERE lookup_allowed;

CREATE OR REPLACE FUNCTION public.read_world_news_documents_v2(
  p_query text, p_as_of timestamptz, p_limit integer
)
RETURNS TABLE(
  document_id text, document_version_id text, source_id text, provider text,
  provider_document_id text, canonical_url text, republication_of_document_id text,
  identity_status text, title text, bounded_quote text, bounded_passage text, language text,
  published_at timestamptz, publication_status text, provider_observed_at timestamptz,
  first_seen_at timestamptz, available_at timestamptz, rights_profile text,
  external_llm_allowed boolean, lookup_allowed boolean, rag_retrieval_allowed boolean,
  prompt_untrusted boolean, collection_status text, content_sha256 text, version_sha256 text
) LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $read$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app' OR p_query IS NULL OR p_as_of IS NULL
     OR char_length(p_query)>200
     OR p_limit NOT BETWEEN 1 AND 50 THEN
    RAISE EXCEPTION 'world-news lookup arguments invalid' USING ERRCODE='22023'; END IF;
  RETURN QUERY
  SELECT document.document_id,version.document_version_id,document.source_id,document.provider,
    observation.provider_document_id,document.canonical_url,document.republication_of_document_id,
    observation.identity_status,version.title,version.bounded_quote,version.bounded_passage,
    version.language,version.published_at,version.publication_status,
    observation.provider_observed_at,version.first_seen_at,version.available_at,
    version.rights_profile,version.external_llm_allowed,version.lookup_allowed,
    version.rag_retrieval_allowed,version.prompt_untrusted,version.collection_status,
    version.content_sha256,version.version_sha256
  FROM public.world_news_document_versions_v2 version
  JOIN public.world_news_documents_v2 document USING(document_id)
  JOIN LATERAL (
    SELECT candidate.provider_document_id,candidate.identity_status,candidate.provider_observed_at
    FROM public.world_news_observations_v2 candidate
    WHERE candidate.document_version_id=version.document_version_id
    ORDER BY candidate.provider_observed_at DESC
    LIMIT 1
  ) observation ON true
  WHERE version.available_at<=p_as_of AND version.lookup_allowed
    AND NOT EXISTS (
      SELECT 1 FROM public.world_news_document_versions_v2 newer
      WHERE newer.document_id=version.document_id
        AND newer.available_at<=p_as_of AND newer.lookup_allowed
        AND (newer.available_at,newer.first_seen_at,newer.document_version_id)
            >(version.available_at,version.first_seen_at,version.document_version_id)
    )
    AND (btrim(p_query)=''
      OR concat_ws(' ',version.title,version.bounded_quote,version.bounded_passage,document.provider)
         ILIKE '%'||p_query||'%')
  ORDER BY COALESCE(version.published_at,version.first_seen_at) DESC,version.first_seen_at DESC,
    document.document_id
  LIMIT p_limit;
END $read$;
ALTER FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_world_news_documents_v2(text,timestamptz,integer)
  TO decision_app;
