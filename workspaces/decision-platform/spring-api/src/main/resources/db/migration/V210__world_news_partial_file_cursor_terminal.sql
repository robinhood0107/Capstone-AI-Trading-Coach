-- GQG/GEMG 분별 파일은 게시 후 불변이다. PARTIAL은 일부 행을 제외했어도
-- 허용된 문서와 collection receipt를 원자 commit한 결과이므로 재다운로드하지 않는다.
-- COLLECTION_FAILED/NOT_COLLECTED는 여전히 완료 cursor가 아니다.
CREATE OR REPLACE FUNCTION public.world_news_collection_completed_v1(p_provider text,p_cursor_sha256 text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $completed$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_market_writer'
     OR p_provider NOT IN ('GDELT_GQG','GDELT_GEMG')
     OR p_cursor_sha256!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'world-news collection cursor input invalid' USING ERRCODE='22023'; END IF;
  RETURN EXISTS(
    SELECT 1 FROM public.world_news_collection_runs_v2 run
    WHERE run.provider=p_provider AND run.cursor_sha256=p_cursor_sha256
      AND run.collection_status IN ('COMPLETE','PARTIAL')
  );
END $completed$;
ALTER FUNCTION public.world_news_collection_completed_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.world_news_collection_completed_v1(text,text) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.world_news_collection_completed_v1(text,text) TO decision_market_writer;
