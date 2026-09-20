-- GQG/GEMG 는 15분 heartbeat 로 생성돼 대부분의 분에는 파일이 없다(공식 문서).
-- 즉 404 는 오류가 아니라 정상 상태다. 그런데 완료 cursor 만 건너뛰는 현재 선정 규칙은
-- 그 빈 분들을 매 cycle 다시 물어 호출을 태우고, 최근 몇 분만 보면 공백 구간을 건너지
-- 못해 수집이 0 이 된다.
--
-- 게시 유예가 지난 뒤에도 404 인 분은 앞으로도 생기지 않는다. 그런 cursor 를 한 번에
-- 걸러낼 수 있게 해, 같은 예산으로 더 넓은 구간을 훑고 반복 실행이 싸지게 한다.
-- 유예 판정은 호출자가 한다 - 이 함수는 "404 로 기록된 적이 있는가"만 답한다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.world_news_unpublished_cursors_v1(
  p_provider text, p_cursors text[]
) RETURNS text[]
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $world_news_unpublished_cursors_v1$
DECLARE result text[];
BEGIN
  IF p_provider NOT IN ('GDELT_GQG','GDELT_GEMG','FINNHUB_MARKET_NEWS') THEN
    RAISE EXCEPTION 'world-news provider is invalid' USING ERRCODE='22023';
  END IF;
  IF p_cursors IS NULL OR array_length(p_cursors,1) IS NULL THEN
    RETURN ARRAY[]::text[];
  END IF;
  IF array_length(p_cursors,1) > 4096 THEN
    RAISE EXCEPTION 'world-news cursor batch is too large' USING ERRCODE='22023';
  END IF;
  SELECT COALESCE(array_agg(DISTINCT run.cursor_sha256),ARRAY[]::text[]) INTO result
  FROM public.world_news_collection_runs_v2 run
  WHERE run.provider=p_provider
    AND run.collection_status='COLLECTION_FAILED'
    AND run.error_code='GDELT_NOT_PUBLISHED'
    AND run.cursor_sha256 = ANY(p_cursors)
    -- 같은 cursor 가 나중에 COMPLETE/PARTIAL 로 들어왔다면 미게시가 아니다.
    AND NOT EXISTS (
      SELECT 1 FROM public.world_news_collection_runs_v2 done
      WHERE done.provider=run.provider AND done.cursor_sha256=run.cursor_sha256
        AND done.collection_status IN ('COMPLETE','PARTIAL')
    );
  RETURN result;
END
$world_news_unpublished_cursors_v1$;

ALTER FUNCTION public.world_news_unpublished_cursors_v1(text,text[]) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.world_news_unpublished_cursors_v1(text,text[])
  FROM PUBLIC, decision_app;
GRANT EXECUTE ON FUNCTION public.world_news_unpublished_cursors_v1(text,text[])
  TO decision_market_writer;
