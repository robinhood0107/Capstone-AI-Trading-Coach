-- The daily shard gate had an upper bound but no lower bound.
--
-- `daily_ready`(V135 의 p1_read_automation_runtime_state_v1)는 이렇게 판정했다.
--   manifest.session_date <= claim.session_date
--   AND manifest.as_of <= (claim.session_date + 09:20 KST)
-- 상한만 있고 하한이 없다. 그래서 임의로 오래된 shard 가 미래의 어떤 세션도 만족시켰다.
-- 이름은 `dailyShardFreshComplete` 인데 최신성을 전혀 보지 않았다.
--
-- 왜 드러나지 않았나. 일일 추론의 manifest 선택(V116)과 이력 선택(V110)도 둘 다 상한만
-- 갖는다. 양쪽이 같은 stale 집합에서 각자 "target 직전 최신"을 뽑으므로
-- `daily_inference` 의 정합성 검사는 지연 폭과 무관하게 항상 일치했다. 즉 오래된 종가로
-- 조용히 매매하면서 어느 화면에도 경고가 없었다. `yfinance_daily_cli` 의 모듈 주석이
-- "가용성 문제가 아니라 최신성 문제였고 그것을 걸러내는 게이트가 없었다"고 이미 적어 뒀다.
--
-- 하한은 직전 XKRX 세션이다. "N 일"이 아니다 - 주말·휴일·대체공휴일 때문에 달력이
-- 유일한 권위이고, AGENTS.md 가 weekday 가정을 금지한다. `trading_sessions` 에서 읽는다.
--
-- 막지 않고 채우는 것이 먼저다. 같은 변경에서 수집기가 마감 후 오늘 봉까지 받고, 상주
-- 런타임이 빠진 거래일의 일별 배치를 멱등하게 소급 생성한다. 그것으로도 채워지지 않으면
-- 그때 이 하한이 세션을 닫고(`automation.py` 가 이미 SKIPPED_DATA_UNAVAILABLE 로 닫는다)
-- V142 의 bounded fallback 이 2분·5분 간격으로 두 번 재시도한다.
--
-- 왜 v4 인가. V152 와 같은 이유다 - production 이 부르는 상태 함수는 v4 하나뿐이고
-- (실측: app 과 spring main 에서 v4 2회, v1~v3 0회) v1 을 다시 쓰면 150 줄을 옮겨 적어야
-- 한다. 이 파일은 V152 의 principleActiveCurrent 덮어쓰기를 그대로 이어받고 daily 하한을
-- 더한다. v1 만 보는 사람은 두 값 모두 여기서 덮인다는 것을 알 수 없으므로 그 사실을 남긴다.
--
-- 바꾸지 않는 것: 새 상태, 새 컬럼, 새 HTTP operation, OpenAPI, Python 변경은 없다.

SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v4(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE base jsonb;
DECLARE target date;
DECLARE signals_json jsonb;
DECLARE principle_version_current boolean;
DECLARE previous_session date;
DECLARE daily_fresh boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation state v4 denied' USING ERRCODE='42501';
  END IF;
  base:=public.p1_read_automation_runtime_state_v3(p_run_id,p_claim_token_hash)::jsonb;
  target:=(base->>'sessionDate')::date;
  -- ARM 이 고정한 원칙 버전이 아직 현재 버전인가(V152). DISARMED 이거나 아직 고정한 것이
  -- 없으면 활성 여부만 본다 - 그러지 않으면 첫 무장 전에 영구히 거짓이 된다.
  SELECT EXISTS (
    SELECT 1
    FROM public.automation_control control
    JOIN public.principles principle
      ON principle.user_id=control.user_id
     AND principle.principle_id=control.principle_id
    WHERE control.principle_id IS NOT NULL
      AND principle.status='ACTIVE'
      AND (
        control.control_state<>'ARMED'
        OR control.principle_version IS NULL
        OR principle.current_version=control.principle_version
      )
  ) INTO principle_version_current;
  -- 이 세션의 직전 개장일. 달력이 유일한 권위다.
  SELECT max(calendar.session_date) INTO previous_session
  FROM public.trading_sessions calendar
  WHERE calendar.exchange_mic='XKRX' AND calendar.is_open
    AND calendar.session_date<target;
  -- 상한(기존)에 하한을 더한다. 직전 세션보다 오래된 shard 로는 매매하지 않는다.
  -- 달력이 직전 세션을 모르면(첫 세션 등) 하한을 걸 근거가 없으므로 기존 판정을 따른다.
  daily_fresh:=EXISTS (
    SELECT 1 FROM public.market_data_manifests manifest
    WHERE manifest.manifest_kind IN ('DAILY','AUTOMATION_BOOTSTRAP')
      AND manifest.status='ACCEPTED'
      AND manifest.session_date<=target
      AND (previous_session IS NULL OR manifest.session_date>=previous_session)
      AND manifest.as_of<=((target+time '09:20') AT TIME ZONE 'Asia/Seoul')
  );
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'symbol',candidate.symbol,'lstmSignal',candidate.lstm_signal,
    'baselineSignal',candidate.baseline_signal,'expectedReturn',candidate.expected_return,
    'lstmExpectedReturn',candidate.lstm_return,'ridgeExpectedReturn',candidate.ridge_return,
    'forecastClose',candidate.forecast_close,'ridgeModelSha256',candidate.ridge_model_sha,
    'combinationMethod','EQUAL_WEIGHT_50_50'
  ) ORDER BY candidate.expected_return DESC,candidate.symbol),'[]'::jsonb)
  INTO signals_json
  FROM (
    SELECT signal.symbol,
      max(signal.signal) FILTER (WHERE signal.producer='LSTM') AS lstm_signal,
      max(signal.signal) FILTER (WHERE signal.producer='RULE_BASELINE') AS baseline_signal,
      avg(signal.expected_return) AS expected_return,
      max(signal.expected_return) FILTER(WHERE signal.producer='LSTM') AS lstm_return,
      max(signal.expected_return) FILTER(WHERE signal.producer='RULE_BASELINE') AS ridge_return,
      max(ridge.model_sha256) AS ridge_model_sha,
      max((ridge.forecasts->0->>'forecastClose')::numeric / (1+(ridge.forecasts->0->>'expectedReturn')::numeric)) * (1+avg(signal.expected_return)) AS forecast_close
    FROM public.p1_return_daily_signal_batch batch
    JOIN public.p1_return_daily_signal_projection signal USING (batch_sha256)
    JOIN public.p1_ridge_daily_forecasts ridge ON ridge.batch_sha256=batch.batch_sha256 AND ridge.symbol=signal.symbol
    JOIN public.current_p1_return_model_pointer model USING (bundle_sha256)
    WHERE batch.target_session=target AND batch.status='COMPLETE'
    GROUP BY signal.symbol
    HAVING count(DISTINCT signal.producer)=2
  ) candidate;
  RETURN (base || jsonb_build_object(
    'releaseActive',jsonb_array_length(signals_json)=31,
    'signals',signals_json,
    -- v1 이 만든 두 값을 덮는다. 런타임이 보는 것은 이 값들이다.
    'principleActiveCurrent',COALESCE(principle_version_current,false),
    'dailyShardFreshComplete',COALESCE(daily_fresh,false)
  ))::text;
END
$function$;
