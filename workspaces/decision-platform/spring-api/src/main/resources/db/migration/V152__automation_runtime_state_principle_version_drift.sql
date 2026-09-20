-- The resident runtime must see principle version drift, not only readiness.
--
-- V151 이 readiness 의 `principle_current` 에 버전 일치를 넣었지만 그것으로는 부족했다.
-- 세션을 실제로 닫는 지점은 `automation.py:1163` 의
--   if not (daily_shard_fresh_complete and principle_active_current): -> SKIPPED_DATA_UNAVAILABLE
-- 이고, 그 `principleActiveCurrent` 는 readiness 가 아니라 런타임 상태 함수가 만든다
-- (최신 정의는 V135 의 `p1_read_automation_runtime_state_v1`, `status='ACTIVE'` 만 확인).
-- 게다가 상주 데몬의 `serve()` 는 claim 전에 readiness 를 부르지 않는다 - 호출 순서가
-- ensure_daily_signals -> claim -> _drive_claim 이고 readiness 는 fallback 과 CLI 에서만 쓰인다.
-- 그래서 V151 만으로는 **이미 무장돼 예약된 세션**이 그대로 claim 하고 옛 스냅샷으로 돌다가
-- 주문 직전 결속 검사(V141)에서 죽는 동작이 남아 있었다.
--
-- 왜 v4 를 재정의하는가. production 코드가 부르는 것은 `_v4` 하나뿐이다(실측: app 과
-- spring main 에서 `_v4` 2회, `_v1`/`_v2`/`_v3` 0회). v1~v3 는 위임 사슬로만 존재한다.
-- v1 을 다시 쓰면 150 줄을 옮겨 적어야 하고 그 과정에서 다른 절이 틀어질 위험이 있다.
-- 런타임의 유일한 진입점에서 한 값을 덮는 것이 더 작고 안전하다.
--
-- 읽는 사람을 위한 경고. `_v1` 만 보면 `principleActiveCurrent` 에 버전 검사가 없어 보인다.
-- 런타임이 보는 값은 이 파일이 덮은 값이다. v1 을 다시 쓸 일이 생기면 이 절을 그쪽으로
-- 옮기고 여기서 빼는 것이 맞다.
--
-- 의미. 소유자가 고른 것은 "원칙 변경은 허용하고 다음 세션부터 적용"이다. 그래서 드리프트가
-- 생긴 세션은 주문 직전에 죽는 대신 시작 전에 건너뛴다. 회복은 정지 -> 정책 재저장(그 순간의
-- 최신 원칙을 다시 스냅샷) -> 재무장이고, ARM 이 이미 같은 드리프트를 거부한다(V133).
--
-- 바꾸지 않는 것: 원칙 편집을 막지 않는다. 사이징과 판정이 읽는 버전을 바꾸지 않는다.
-- 새 상태, 새 컬럼, 새 HTTP operation, OpenAPI 변경, Python 변경은 없다.

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
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation state v4 denied' USING ERRCODE='42501';
  END IF;
  base:=public.p1_read_automation_runtime_state_v3(p_run_id,p_claim_token_hash)::jsonb;
  target:=(base->>'sessionDate')::date;
  -- ARM 이 고정한 원칙 버전이 아직 현재 버전인가. DISARMED 이거나 아직 고정한 것이 없으면
  -- 활성 여부만 본다 - 그러지 않으면 첫 무장 전에 영구히 거짓이 된다.
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
    -- v1 이 만든 값을 덮는다. 런타임이 보는 것은 이 값이다.
    'principleActiveCurrent',COALESCE(principle_version_current,false)
  ))::text;
END
$function$;
