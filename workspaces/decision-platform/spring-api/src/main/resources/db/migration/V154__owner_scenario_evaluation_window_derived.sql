-- 백테스트 평가 구간을 데이터에서 유도한다.
--
-- V127 이 시연 창을 리터럴로 못박았다.
--   session_date <= DATE '2026-09-03'   ... 상한
--   LIMIT 104 / <> 104 / bar_count <> 3224
--   'evaluationStart','2026-08-18' / 'evaluationEnd','2026-09-03'
-- 그래서 백테스트 리포트가 시간이 지나도 자라지 않았다. 재계산 자체는 이미 매번 일어난다 -
-- `./capstone up` 이 owner-scenario-materializer 를 일회성 컨테이너로 돌리고 DB 일봉으로
-- 다시 시뮬레이션한다. 얼어 있던 것은 계산이 아니라 창이다.
--
-- 무엇을 유도하고 무엇을 남기는가.
--   * 문맥 세션 = exact-31 이 모두 있는 모든 세션. 시작 리터럴도 LIMIT 도 없다.
--   * evaluationEnd = 그중 가장 최근 세션. 거래일이 하나 늘면 곡선이 하나 늘어난다.
--   * evaluationStart = 2026-08-18 을 그대로 둔다. 이것은 임의의 창이 아니라 운용 시작일이고
--     그 앞에는 평가할 운용이 없다. 유일하게 정직한 상수다.
--
-- 왜 등식이 아니라 하한인가. 104 는 "이 코드를 쓸 때 있던 세션 수"였을 뿐 계약이 아니다.
-- 등식으로 두면 거래일이 하나 늘 때마다 게이트가 거짓이 되고, 그것이 지금 상태다. 대신
-- 하한을 둔다 - 자라는 것은 허용하고 줄어드는 것(적재 소실·부분 적재)은 여전히 막는다.
-- 바 개수도 31 x 세션수 로 유도해 부분 적재를 계속 잡는다.
--
-- 왜 "exact-31 이 있는 세션"으로 완결성을 정의하나. 하류가 exact-31 을 가정하고, 일부
-- 종목만 있는 세션을 문맥에 넣으면 그 세션의 지표가 종목마다 다른 표본에서 나온다.
-- 수집기도 같은 규칙으로 부분 적재를 거부한다(`yfinance_daily_cli`).
--
-- 바꾸지 않는 것: 새 표, 새 컬럼, 새 함수, 새 권한, OpenAPI 변경은 없다. V124 의
-- `publish_owner_scenario_dashboard_v1` 에는 얼어 있는 개수가 없어 손대지 않는다.
-- V124·V127 파일 본문은 계약 테스트가 문자열로 고정하고 있으므로 그대로 두고 여기서 덮는다.

SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.read_owner_scenario_materialization_inputs_v1(p_owner_user_id text)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_owner_scenario_materialization_inputs_v1$
DECLARE
  pointer_row public.current_p1_return_model_pointer%ROWTYPE;
  rules jsonb;
  sessions date[];
  session_count integer;
  evaluation_start date := DATE '2026-08-18';
  evaluation_end date;
  evaluation_count integer;
  bars jsonb;
  symbol_count integer;
  bar_count integer;
BEGIN
  IF session_user <> 'decision_worker' OR p_owner_user_id <> 'usr_demo_user' THEN
    RAISE EXCEPTION 'owner scenario input denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO pointer_row FROM public.current_p1_return_model_pointer LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'owner scenario model pointer unavailable' USING ERRCODE='P0002'; END IF;

  SELECT version.rules_json INTO rules
  FROM public.principles principle
  JOIN public.principle_versions version
    ON version.principle_id=principle.principle_id AND version.version=principle.current_version
  WHERE principle.user_id=p_owner_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id
  LIMIT 1;
  IF rules IS NULL THEN RAISE EXCEPTION 'owner scenario principle unavailable' USING ERRCODE='P0002'; END IF;

  -- exact-31 이 모두 있는 세션만 문맥이 된다. 상한도 LIMIT 도 없다.
  SELECT array_agg(complete.session_date ORDER BY complete.session_date) INTO sessions
  FROM (
    SELECT bar.session_date
    FROM public.market_data_bars bar
    GROUP BY bar.session_date
    HAVING count(DISTINCT bar.symbol)=31
  ) complete;
  session_count:=coalesce(array_length(sessions,1),0);
  -- 하한만 본다. 자라는 것은 허용하고 줄어드는 것은 막는다.
  IF session_count < 104 THEN
    RAISE EXCEPTION 'owner scenario requires at least 104 context sessions' USING ERRCODE='22023';
  END IF;
  evaluation_end:=sessions[session_count];
  SELECT count(*) INTO evaluation_count
  FROM unnest(sessions) AS session_date
  WHERE session_date BETWEEN evaluation_start AND evaluation_end;
  -- 운용 시작일 이후 평가할 세션이 시연 시점의 13 보다 적어질 수는 없다.
  IF evaluation_count < 13 THEN
    RAISE EXCEPTION 'owner scenario requires at least 13 evaluation sessions' USING ERRCODE='22023';
  END IF;

  WITH latest AS (
    SELECT DISTINCT ON (symbol,session_date)
      symbol,session_date,open_price,high_price,low_price,close_price,volume,
      manifest_sha256,source_receipt_sha256
    FROM public.market_data_bars
    WHERE session_date=ANY(sessions)
    ORDER BY symbol,session_date,generation DESC
  )
  SELECT count(DISTINCT symbol),count(*),jsonb_agg(
    jsonb_build_object(
      'symbol',symbol,'sessionDate',session_date,'open',open_price,'high',high_price,
      'low',low_price,'close',close_price,'volume',volume,
      'manifestSha256',manifest_sha256,'sourceReceiptSha256',source_receipt_sha256
    ) ORDER BY session_date,symbol
  ) INTO symbol_count,bar_count,bars FROM latest;
  -- 개수를 유도한다. 부분 적재는 그대로 잡힌다.
  IF symbol_count <> 31 OR bar_count <> 31*session_count THEN
    RAISE EXCEPTION 'owner scenario requires exact-31 by every context session' USING ERRCODE='22023';
  END IF;

  RETURN jsonb_build_object(
    'contractId','owner-scenario-materialization-input.v1',
    'ownerUserId',p_owner_user_id,
    'bundleSha256',pointer_row.bundle_sha256,
    'artifactId',pointer_row.artifact_id,
    'sourceRunId',pointer_row.run_id,
    'modelSha256',pointer_row.model_sha256,
    'modelQuality',pointer_row.model_quality,
    'evaluationStart',to_char(evaluation_start,'YYYY-MM-DD'),
    'evaluationEnd',to_char(evaluation_end,'YYYY-MM-DD'),
    'sessions',to_jsonb(sessions),
    'rules',rules,
    'bars',bars
  );
END
$read_owner_scenario_materialization_inputs_v1$;
