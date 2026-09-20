-- 세계 뉴스를 거부권 근거로 읽는 경로를 연다.
--
-- 기존 `read_world_news_documents_v2` 는 **화면 조회용**이다. session_user 를 decision_app
-- 으로 묶고 `lookup_allowed` 로 거른다 - 그 경로의 권한 표기가 decision/signal/order 모두
-- NONE 인 이유가 그것이다. 근거로 쓰려면 스키마가 이미 구분해 둔 다른 깃발,
-- `rag_retrieval_allowed` 를 봐야 한다.
--
-- 그래서 조회 함수를 넓히지 않고 근거 전용 읽기를 따로 둔다. 조회와 근거가 같은 함수를
-- 공유하면 한쪽을 넓힐 때 다른 쪽이 조용히 따라 넓어진다.
--
-- 발행일은 돌려주지 않는다. 근거의 날짜는 수집일(`first_seen_at`)로 통일했기 때문이다 -
-- 원문 발행일은 대부분 MISSING/CONFLICT 라 그것을 기준으로 삼으면 확인된 소수만 근거가
-- 되고 나머지가 통째로 빠진다.

CREATE FUNCTION public.p1_read_world_news_evidence_v1(
  p_query text,
  p_window_from date,
  p_window_to date,
  p_limit integer
) RETURNS TABLE(
  canonical_url text,
  title text,
  bounded_quote text,
  bounded_passage text,
  first_seen_at timestamptz
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_world_news_evidence_v1$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_disclosure_reader'
     OR p_query IS NULL OR char_length(p_query)>200
     OR p_window_from IS NULL OR p_window_to IS NULL OR p_window_from>p_window_to
     OR p_limit NOT BETWEEN 1 AND 500 THEN
    RAISE EXCEPTION 'world-news evidence arguments invalid' USING ERRCODE='22023'; END IF;
  RETURN QUERY
  SELECT document.canonical_url, version.title, version.bounded_quote, version.bounded_passage,
         version.first_seen_at
  FROM public.world_news_document_versions_v2 version
  JOIN public.world_news_documents_v2 document USING(document_id)
  WHERE version.rag_retrieval_allowed
    AND version.first_seen_at >= p_window_from::timestamptz
    AND version.first_seen_at < (p_window_to + 1)::timestamptz
    -- 문서당 최신 판본 하나만. 같은 기사의 옛 판본이 상한을 먹으면 다른 기사가 밀린다.
    AND NOT EXISTS (
      SELECT 1 FROM public.world_news_document_versions_v2 newer
      WHERE newer.document_id=version.document_id
        AND newer.rag_retrieval_allowed
        AND (newer.first_seen_at,newer.document_version_id)
            >(version.first_seen_at,version.document_version_id)
    )
    AND (btrim(p_query)=''
      OR concat_ws(' ',version.title,version.bounded_quote,version.bounded_passage)
         ILIKE '%'||p_query||'%')
  ORDER BY version.first_seen_at DESC, document.document_id
  LIMIT p_limit;
END
$p1_read_world_news_evidence_v1$;

ALTER FUNCTION public.p1_read_world_news_evidence_v1(text,date,date,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_world_news_evidence_v1(text,date,date,integer)
  FROM PUBLIC, decision_app, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_world_news_evidence_v1(text,date,date,integer)
  TO decision_disclosure_reader;

-- 종목 정식명. 정확 일치 매칭에 필요하다.
--
-- 표에 직접 SELECT 를 주지 않는 이유: 역할 권한은 bootstrap 이 세우고 migration 은 객체를
-- 만든다. migration 에서만 표 권한을 주면 둘이 어긋나고, 그것을 잡는 불변식 검사가 있다.
-- 함수 한 칸으로 노출하면 표 권한이 필요 없어 그 분기 자체가 생기지 않는다.
CREATE FUNCTION public.p1_read_instrument_display_name_v1(p_symbol text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_instrument_display_name_v1$
DECLARE display_name text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_disclosure_reader'
     OR p_symbol IS NULL OR p_symbol !~ '^[0-9]{6}$' THEN
    RAISE EXCEPTION 'instrument display name arguments invalid' USING ERRCODE='22023'; END IF;
  SELECT metadata.name_ko INTO display_name
  FROM public.instrument_display_metadata metadata
  WHERE metadata.symbol=p_symbol;
  RETURN display_name;
END
$p1_read_instrument_display_name_v1$;

ALTER FUNCTION public.p1_read_instrument_display_name_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_instrument_display_name_v1(text)
  FROM PUBLIC, decision_app, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_instrument_display_name_v1(text)
  TO decision_disclosure_reader;
