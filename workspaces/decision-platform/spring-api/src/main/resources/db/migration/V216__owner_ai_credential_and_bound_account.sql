-- FULL 에서 자동매매 무장이 "AI 검토 제공자 준비"와 "주문 계좌"를 실제 실행 경로와 같은 규칙으로
-- 판정하게 한다.
--
-- 1) AI 검토 제공자. 지금까지 arm 은 AI 판단이 켜져 있으면 소유자 PRIMARY 자격증명 행을 요구했다.
--    그런데 FULL 은 소유자 키 화면을 열지 않았고, 실행 경로는 운영자 Vertex 서비스 계정만 썼다. 그래서
--    AI 검토를 켠 FULL 사용자는 영영 무장할 수 없었고, 상태 화면은 vertex 를 면제해 "시작 가능"이라고
--    거짓말했다. 이제 준비 조건은 "소유자 PRIMARY 키가 있다" 또는 "운영자 공용 Vertex 가 이 배포에서
--    허용됐다(p_operator_provider_ready)" 이다. 두 번째 값은 Spring 이 설정 스위치와 evidence transport
--    존재로만 정해 넘긴다. LOCAL 은 스위치가 꺼져 있어 기존 규칙과 같다.
-- 2) 주문 계좌. FULL 소유자의 주문·잔고는 자기 KIS 자격증명에 묶인 계좌로만 나간다. 묶인 계좌가
--    있으면 다른 계좌로 무장하지 못한다(P1K01).
-- 3) 어느 자격증명으로 AI 를 불렀는지(OWNER/OPERATOR)를 실행별 사용량 행에 남긴다. 청구는 만들지
--    않는다 - 나중에 사용자별로 셀 수 있게 근거만 남긴다.

ALTER TABLE public.automation_v3_usage
  ADD COLUMN ai_credential_source text,
  ADD CONSTRAINT automation_v3_usage_ai_credential_source_v216 CHECK (
    ai_credential_source IS NULL OR ai_credential_source IN ('OWNER','OPERATOR')
  );

-- 아래 definer 함수들이 읽는 열. 봉투(암호문) 열은 여전히 flyway 에도 열지 않는다.
GRANT SELECT (owner_user_id, brokerage_mode, account_id, account_no_last4, credential_state)
  ON public.user_broker_credentials TO flyway;
GRANT SELECT (owner_user_id, slot) ON public.strong_llm_owner_credentials TO flyway;
GRANT SELECT ON public.strong_llm_owner_settings TO flyway;

-- 상태 화면이 arm 과 같은 원칙 버전 판정을 하도록 한다. arm(p1_arm_automation_v2)은 최신 정책이 묶은 원칙
-- 버전이 지금 활성 버전과 다르면 40001 로 거부한다. 앱 역할은 principles 를 직접 읽지 못해 definer 로 둔다.
CREATE FUNCTION public.p1_automation_principle_drift_v1(p_user_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_principle_drift_v1$
BEGIN
  IF session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'automation principle drift actor is invalid' USING ERRCODE='42501';
  END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.automation_policy_versions policy
    WHERE policy.user_id=p_user_id
      AND policy.version=(SELECT max(version) FROM public.automation_policy_versions WHERE user_id=p_user_id)
      AND NOT EXISTS (
        SELECT 1 FROM public.principles item
        WHERE item.user_id=p_user_id AND item.principle_id=policy.principle_id
          AND item.status='ACTIVE' AND item.current_version=policy.principle_version
      )
  );
END
$p1_automation_principle_drift_v1$;
ALTER FUNCTION public.p1_automation_principle_drift_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_principle_drift_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_principle_drift_v1(text) TO decision_app;

-- 소유자에게 묶인 KIS_MOCK 계좌 식별자. 복호화 재료는 나가지 않는다.
CREATE FUNCTION public.p1_owner_bound_mock_account_v1(p_user_id text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_owner_bound_mock_account_v1$
DECLARE bound text;
BEGIN
  IF session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'bound mock account actor is invalid' USING ERRCODE='42501';
  END IF;
  SELECT item.account_id INTO bound FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_user_id AND item.brokerage_mode='KIS_MOCK' AND item.account_id IS NOT NULL;
  RETURN bound;
END
$p1_owner_bound_mock_account_v1$;
ALTER FUNCTION public.p1_owner_bound_mock_account_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_owner_bound_mock_account_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_owner_bound_mock_account_v1(text) TO decision_app;

-- 자격증명을 다시 저장할 때 쓸 계좌 식별자. 같은 실계좌의 이력(주문·보유·실행·관측)이 새 무작위
-- 식별자로 끊기지 않게 한다.
--   a) 이미 묶인 행이 있고 계좌 끝 4자리가 같으면 그 식별자를 그대로 쓴다.
--   b) 아직 어느 자격증명에도 묶이지 않은 소유자 자신의 자동매매 계좌(예: 개인 스택에서 옮겨 온
--      고정 식별자)가 있으면 그것을 쓴다. 이 식별자는 이미 이 소유자의 이력에만 붙어 있다.
--   c) 그 밖에는 NULL 을 돌려주고 Spring 이 새 식별자를 만든다. 다른 실계좌를 같은 식별자에 섞지
--      않기 위해 끝 4자리가 다른 재저장은 항상 새 식별자다.
CREATE FUNCTION public.resolve_bound_mock_account_reuse_v1(p_owner_user_id text, p_account_no_last4 text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $resolve_bound_mock_account_reuse_v1$
DECLARE previous record;
DECLARE legacy text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id
     OR p_account_no_last4!~'^[0-9]{4}$' THEN
    RAISE EXCEPTION 'mock credential actor is invalid' USING ERRCODE='42501';
  END IF;
  SELECT item.owner_user_id, item.account_id, item.account_no_last4 INTO previous
  FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_owner_user_id AND item.brokerage_mode='KIS_MOCK';
  SELECT control.account_id INTO legacy FROM public.automation_control control
  WHERE control.user_id=p_owner_user_id AND control.account_id IS NOT NULL;
  IF legacy IS NOT NULL
     AND legacy IS DISTINCT FROM previous.account_id
     AND NOT EXISTS (SELECT 1 FROM public.user_broker_credentials other WHERE other.account_id=legacy)
     AND (previous.owner_user_id IS NULL OR previous.account_no_last4=p_account_no_last4) THEN
    RETURN legacy;
  END IF;
  IF previous.account_id IS NOT NULL AND previous.account_no_last4=p_account_no_last4 THEN
    RETURN previous.account_id;
  END IF;
  RETURN NULL;
END
$resolve_bound_mock_account_reuse_v1$;
ALTER FUNCTION public.resolve_bound_mock_account_reuse_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.resolve_bound_mock_account_reuse_v1(text,text)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.resolve_bound_mock_account_reuse_v1(text,text) TO decision_app;

-- 연결 확인이 KIS 에서 방금 읽은 잔고를 소유자 계좌의 온라인 관측으로 남긴다. 무장(위험 잔고 투영)과
-- 잔고 화면이 같은 행을 읽는다. 금 ETF/ETN 분류는 KIS 가 주지 않으므로 종목 카탈로그에서 가져온다.
CREATE FUNCTION public.record_bound_mock_balance_observation_v1(
  p_owner_user_id text, p_account_id text, p_cash_krw bigint, p_portfolio_equity_krw bigint,
  p_positions jsonb
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $record_bound_mock_balance_observation_v1$
DECLARE scope_hash text;
DECLARE observed timestamptz;
DECLARE payload jsonb;
DECLARE digest_value text;
DECLARE observation text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id
     OR p_account_id!~'^acct_[0-9a-f]{32}$'
     OR p_cash_krw<0 OR p_portfolio_equity_krw<0
     OR jsonb_typeof(p_positions)<>'array' OR jsonb_array_length(p_positions)>1000
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_positions) item
       WHERE jsonb_typeof(item)<>'object' OR NOT (item ? 'symbol' AND item ? 'quantity' AND item ? 'marketValueKrw')
          OR (item->>'symbol')!~'^[0-9]{6}$' OR (item->>'quantity')!~'^[0-9]{1,18}$'
          OR (item->>'marketValueKrw')!~'^[0-9]{1,18}$'
     )
     OR (SELECT count(DISTINCT item->>'symbol') FROM jsonb_array_elements(p_positions) item)
        <>jsonb_array_length(p_positions) THEN
    RAISE EXCEPTION 'bound mock balance observation input invalid' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.user_broker_credentials item
    WHERE item.owner_user_id=p_owner_user_id AND item.brokerage_mode='KIS_MOCK'
      AND item.account_id=p_account_id AND item.credential_state IN ('STORED','CONNECTED','CERTIFIED')
  ) THEN
    RAISE EXCEPTION 'bound mock balance account is not the owner account' USING ERRCODE='42501';
  END IF;
  scope_hash:=substr(p_account_id,6)||repeat('0',32);
  observed:=statement_timestamp();
  payload:=jsonb_build_object(
    'cashKrw',p_cash_krw,'completeness','COMPLETE','marginRequirementKrw',0,
    'ownerScopeHash',scope_hash,'portfolioEquityKrw',p_portfolio_equity_krw,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'isGoldEtfEtn',COALESCE((
          SELECT catalog.is_gold_etf_etn FROM public.instrument_catalog_observations catalog
          WHERE catalog.symbol=item->>'symbol' AND catalog.completeness='COMPLETE'
          ORDER BY catalog.observed_at DESC,catalog.received_at DESC,catalog.observation_id DESC LIMIT 1
        ),false),
        'marketValueKrw',(item->>'marketValueKrw')::bigint,
        'quantity',(item->>'quantity')::bigint,'symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_positions) item
    ),'[]'::jsonb)
  );
  digest_value:=encode(public.digest(convert_to(payload::text,'UTF8'),'sha256'),'hex');
  observation:='pbo_'||encode(public.digest(convert_to(
    p_owner_user_id||':'||p_account_id||':'||observed::text||':'||digest_value,'UTF8'),'sha256'),'hex');
  INSERT INTO public.portfolio_balance_observations(
    observation_id,owner_user_id,account_scope_hash,source,context_status,cash_krw,
    portfolio_equity_krw,margin_requirement_krw,completeness,position_count,observed_at,
    received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
  ) VALUES (
    observation,p_owner_user_id,scope_hash,'KIS_MOCK','ACTIVE',p_cash_krw,
    p_portfolio_equity_krw,0,'COMPLETE',jsonb_array_length(p_positions),observed,observed,
    '2','kis-mock-online-complete-v2',payload,
    encode(public.digest(convert_to('mock-connection:'||digest_value,'UTF8'),'sha256'),'hex'),
    digest_value
  );
  INSERT INTO public.portfolio_position_observations(
    balance_observation_id,symbol,quantity,market_value_krw,is_gold_etf_etn
  )
  SELECT observation,item->>'symbol',(item->>'quantity')::bigint,(item->>'marketValueKrw')::bigint,
    (item->>'isGoldEtfEtn')::boolean
  FROM jsonb_array_elements(payload->'positions') item;
  RETURN observation;
END
$record_bound_mock_balance_observation_v1$;
ALTER FUNCTION public.record_bound_mock_balance_observation_v1(text,text,bigint,bigint,jsonb) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_bound_mock_balance_observation_v1(text,text,bigint,bigint,jsonb)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.record_bound_mock_balance_observation_v1(text,text,bigint,bigint,jsonb)
  TO decision_app;

-- 설정 화면의 "연결 확인됨" 요약. 예수금·보유 종목 수·관측 시각만 나간다.
CREATE FUNCTION public.read_bound_mock_balance_confirmation_v1(p_owner_user_id text)
RETURNS TABLE(cash_krw bigint, position_count integer, observed_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $read_bound_mock_balance_confirmation_v1$
DECLARE bound text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'mock credential actor is invalid' USING ERRCODE='42501';
  END IF;
  SELECT item.account_id INTO bound FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_owner_user_id AND item.brokerage_mode='KIS_MOCK' AND item.account_id IS NOT NULL;
  IF bound IS NULL THEN RETURN; END IF;
  RETURN QUERY
  SELECT balance.cash_krw,balance.position_count,balance.observed_at
  FROM public.portfolio_balance_observations balance
  WHERE balance.owner_user_id=p_owner_user_id AND balance.source='KIS_MOCK'
    AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
    AND balance.source_version='kis-mock-online-complete-v2'
    AND balance.account_scope_hash=substr(bound,6)||repeat('0',32)
  ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id
  LIMIT 1;
END
$read_bound_mock_balance_confirmation_v1$;
ALTER FUNCTION public.read_bound_mock_balance_confirmation_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_bound_mock_balance_confirmation_v1(text)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.read_bound_mock_balance_confirmation_v1(text) TO decision_app;

GRANT SELECT, INSERT ON TABLE public.portfolio_balance_observations,
  public.portfolio_position_observations TO flyway;
GRANT SELECT ON TABLE public.instrument_catalog_observations TO flyway;

-- 인자 하나가 늘어 시그니처가 바뀐다. 옛 8인자 함수를 남기면 옛 규칙으로 무장하는 길이 남는다.
DROP FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean);

CREATE FUNCTION public.p1_arm_automation_v3(
  p_user_id text, p_account_id text, p_policy_id text, p_expected_policy_version integer,
  p_expected_control_version integer, p_scope_hash text, p_request_hash text,
  p_provider_capability_ready boolean, p_operator_provider_ready boolean
)
 RETURNS TABLE(result_json text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE settings_row public.strong_llm_owner_settings%ROWTYPE;
DECLARE base_result record;
DECLARE history_ready boolean;
DECLARE provider_ready boolean;
DECLARE bound_account text;
DECLARE settings_projection jsonb;
DECLARE settings_sha text;
BEGIN
  -- 주문은 소유자 자격증명에 묶인 계좌로만 나간다. 묶인 계좌가 있는데 다른 계좌로 무장하면 그
  -- 계좌의 잔고로 위험을 재고 다른 계좌에 주문하게 된다.
  SELECT item.account_id INTO bound_account FROM public.user_broker_credentials item
  WHERE item.owner_user_id=p_user_id AND item.brokerage_mode='KIS_MOCK' AND item.account_id IS NOT NULL;
  IF bound_account IS NOT NULL AND bound_account IS DISTINCT FROM p_account_id THEN
    RAISE EXCEPTION 'BOUND_ACCOUNT_MISMATCH' USING ERRCODE='P1K01';
  END IF;
  SELECT * INTO base_result FROM public.p1_arm_automation_v2(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,
    p_expected_control_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions_effective
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR policy_row.max_holding_sessions IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy unavailable' USING ERRCODE='40001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions_effective
    WHERE user_id=p_user_id AND account_id=p_account_id AND status IN ('OPEN','EXIT_PENDING')
      AND max_holding_sessions IS NULL
  ) THEN RAISE EXCEPTION 'LEGACY_POSITION_PRESENT' USING ERRCODE='P1L01'; END IF;
  SELECT EXISTS (SELECT 1 FROM public.market_data_manifests WHERE status='ACCEPTED')
    AND (SELECT count(*) FROM public.market_data_operational_universe)=31
    AND NOT EXISTS (
      SELECT 1 FROM public.market_data_operational_universe universe
      WHERE (SELECT count(*) FROM public.market_data_operational_bars bars
             WHERE bars.symbol=universe.symbol)<policy_row.atr_period+1
    ) INTO history_ready;
  IF NOT history_ready THEN
    RAISE EXCEPTION 'MARKET_DATA_CATCHUP_REQUIRED' USING ERRCODE='P1M01';
  END IF;
  SELECT * INTO settings_row FROM public.strong_llm_owner_settings
  WHERE owner_user_id=p_user_id;
  IF NOT FOUND THEN
    settings_row.provider:='vertex';settings_row.answer_language:='ko';
    settings_row.daily_generate_call_cap:=50;settings_row.ai_judgement_enabled:=false;
    settings_row.thinking_level:='low';
  END IF;
  provider_ready:=NOT settings_row.ai_judgement_enabled OR (
    COALESCE(p_provider_capability_ready,false)
    AND
    settings_row.daily_generate_call_cap>=3
    AND (
      EXISTS (
        SELECT 1 FROM public.strong_llm_owner_credentials credential
        WHERE credential.owner_user_id=p_user_id AND credential.slot='PRIMARY'
      )
      OR COALESCE(p_operator_provider_ready,false)
    )
  );
  IF NOT provider_ready THEN
    RAISE EXCEPTION 'AI_PROVIDER_NOT_READY' USING ERRCODE='P1A01';
  END IF;
  settings_projection:=jsonb_build_object(
    'aiJudgementEnabled',settings_row.ai_judgement_enabled,
    'answerLanguage',settings_row.answer_language,
    'baseUrl',settings_row.base_url,
    'dailyGenerateCallCap',settings_row.daily_generate_call_cap,
    'fallbackBaseUrl',settings_row.fallback_base_url,
    'fallbackModelId',settings_row.fallback_model_id,
    'fallbackProvider',settings_row.fallback_provider,
    'modelId',settings_row.model_id,'provider',settings_row.provider,
    'thinkingLevel',settings_row.thinking_level
  );
  settings_sha:=encode(public.digest(convert_to(settings_projection::text,'UTF8'),'sha256'),'hex');
  UPDATE public.automation_control SET
    ai_settings_sha256=settings_sha,
    ai_settings_snapshot_json=settings_projection,
    ai_judgement_enabled_snapshot=settings_row.ai_judgement_enabled,
    ai_thinking_level_snapshot=settings_row.thinking_level
  WHERE user_id=p_user_id;
  result_json:=base_result.result_json;replayed:=false;RETURN NEXT;
END
$function$;
ALTER FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean,boolean)
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_v3(text,text,text,integer,integer,text,text,boolean,boolean)
  TO decision_app;

-- 4) 운영자 공용 Vertex 허용 스위치. 관리자 콘솔에서 켜고 끄며, 바꿀 때마다 감사 기록을 남긴다.
--    배포 설정(app.automation.ai.operator-vertex-fallback-enabled)은 상한이다: 설정이 꺼져 있으면 이
--    스위치가 켜져 있어도 공용 경로는 없다. 기본값은 지금 동작(허용)과 같다.
ALTER TABLE public.service_limits
  ADD COLUMN operator_vertex_fallback_enabled boolean NOT NULL DEFAULT true,
  ADD COLUMN operator_vertex_updated_by text REFERENCES public.users(user_id),
  ADD COLUMN operator_vertex_updated_at timestamptz;

-- 앱(무장·상태·AI 검토 실행)이 읽는 값. 불리언 하나만 나간다.
CREATE FUNCTION public.p1_operator_vertex_fallback_enabled_v1()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_operator_vertex_fallback_enabled_v1$
  SELECT COALESCE((SELECT l.operator_vertex_fallback_enabled FROM public.service_limits l WHERE l.limits_id=1),false)
$p1_operator_vertex_fallback_enabled_v1$;
ALTER FUNCTION public.p1_operator_vertex_fallback_enabled_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_operator_vertex_fallback_enabled_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_operator_vertex_fallback_enabled_v1() TO decision_app, decision_auth;

-- 5) AI 검토 사용량. 한 실행(run) 한 행이고 provider 호출 수와 자격증명 출처를 담는다. 오늘·이번 달은
--    한국 시각 기준이다. 사용자는 자기 것만, 관리자는 전체를 본다. 키 자체는 어디에서도 나가지 않는다.
-- 에이전트(RAG 질문) 호출도 같은 규칙(자기 키 → 허용 시 공용)으로 부르고 한 run 한 행으로 센다.
CREATE TABLE public.agent_ai_usage_events (
  run_id text PRIMARY KEY CHECK (run_id ~ '^s49_run_[0-9a-f]{32}$'),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  ai_credential_source text NOT NULL CHECK (ai_credential_source IN ('OWNER','OPERATOR')),
  provider_call_count integer NOT NULL CHECK (provider_call_count BETWEEN 1 AND 16),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.agent_ai_usage_events OWNER TO flyway;
ALTER TABLE public.agent_ai_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_ai_usage_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.agent_ai_usage_events FROM PUBLIC;
CREATE POLICY agent_ai_usage_events_definer_v216 ON public.agent_ai_usage_events TO PUBLIC
  USING (current_user='flyway') WITH CHECK (current_user='flyway');

CREATE FUNCTION public.record_agent_ai_usage_v1(p_owner_user_id text, p_run_id text, p_source text, p_calls integer)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $record_agent_ai_usage_v1$
BEGIN
  IF session_user<>'decision_app' OR p_owner_user_id IS NULL THEN
    RAISE EXCEPTION 'agent ai usage actor is invalid' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.agent_ai_usage_events(run_id,owner_user_id,ai_credential_source,provider_call_count)
  VALUES (p_run_id,p_owner_user_id,p_source,p_calls) ON CONFLICT (run_id) DO NOTHING;
  RETURN true;
END
$record_agent_ai_usage_v1$;
ALTER FUNCTION public.record_agent_ai_usage_v1(text,text,text,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_agent_ai_usage_v1(text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_agent_ai_usage_v1(text,text,text,integer) TO decision_app;

-- 사용량 읽기는 자동매매(automation_v3_usage)와 에이전트(agent_ai_usage_events)를 합친 한 뷰로 센다.
CREATE VIEW public.ai_usage_calls_v216 AS
  SELECT u.user_id, u.ai_credential_source, u.provider_call_count, u.updated_at AS at
  FROM public.automation_v3_usage u WHERE u.ai_credential_source IS NOT NULL
  UNION ALL
  SELECT a.owner_user_id, a.ai_credential_source, a.provider_call_count, a.created_at
  FROM public.agent_ai_usage_events a;
ALTER VIEW public.ai_usage_calls_v216 OWNER TO flyway;
REVOKE ALL ON public.ai_usage_calls_v216 FROM PUBLIC;

CREATE POLICY automation_v3_usage_ai_usage_reader_v216 ON public.automation_v3_usage FOR SELECT TO PUBLIC
  USING (current_user='flyway' AND pg_catalog.current_setting('app.v216_ai_usage_read',true)='on');
CREATE POLICY strong_llm_owner_credentials_ai_usage_reader_v216 ON public.strong_llm_owner_credentials
  FOR SELECT TO PUBLIC
  USING (current_user='flyway' AND pg_catalog.current_setting('app.v216_ai_usage_read',true)='on');
CREATE POLICY strong_llm_owner_settings_ai_usage_reader_v216 ON public.strong_llm_owner_settings
  FOR SELECT TO PUBLIC
  USING (current_user='flyway' AND pg_catalog.current_setting('app.v216_ai_usage_read',true)='on');

CREATE FUNCTION public.read_owner_ai_usage_v1(p_owner_user_id text)
RETURNS TABLE(own_today bigint, shared_today bigint, own_month bigint, shared_month bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $read_owner_ai_usage_v1$
DECLARE today date := (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
BEGIN
  IF session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'ai usage actor is invalid' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('app.v216_ai_usage_read','on',true);
  RETURN QUERY
  SELECT
    COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OWNER'
      AND (u.at AT TIME ZONE 'Asia/Seoul')::date=today),0)::bigint,
    COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OPERATOR'
      AND (u.at AT TIME ZONE 'Asia/Seoul')::date=today),0)::bigint,
    COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OWNER'),0)::bigint,
    COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OPERATOR'),0)::bigint
  FROM public.ai_usage_calls_v216 u
  WHERE u.user_id=p_owner_user_id
    AND (u.at AT TIME ZONE 'Asia/Seoul')>=date_trunc('month',today::timestamp);
  PERFORM pg_catalog.set_config('app.v216_ai_usage_read','off',true);
END
$read_owner_ai_usage_v1$;
ALTER FUNCTION public.read_owner_ai_usage_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_owner_ai_usage_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_owner_ai_usage_v1(text) TO decision_app;

CREATE FUNCTION public.admin_read_operator_vertex_v1(p_actor_user_id text)
RETURNS TABLE(fallback_enabled boolean, updated_by text, updated_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $admin_read_operator_vertex_v1$
BEGIN
  PERFORM public.admin_require_actor_v1(p_actor_user_id);
  RETURN QUERY
  SELECT l.operator_vertex_fallback_enabled,l.operator_vertex_updated_by,l.operator_vertex_updated_at
  FROM public.service_limits l WHERE l.limits_id=1;
END
$admin_read_operator_vertex_v1$;

CREATE FUNCTION public.admin_set_operator_vertex_fallback_v1(p_actor_user_id text, p_enabled boolean)
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $admin_set_operator_vertex_fallback_v1$
DECLARE actor public.users%ROWTYPE;
DECLARE previous boolean;
BEGIN
  actor:=public.admin_require_actor_v1(p_actor_user_id);
  IF p_enabled IS NULL THEN
    RAISE EXCEPTION 'operator vertex switch input invalid' USING ERRCODE='22023';
  END IF;
  SELECT l.operator_vertex_fallback_enabled INTO previous FROM public.service_limits l WHERE l.limits_id=1 FOR UPDATE;
  UPDATE public.service_limits SET operator_vertex_fallback_enabled=p_enabled,
    operator_vertex_updated_by=actor.user_id,operator_vertex_updated_at=statement_timestamp()
  WHERE limits_id=1;
  INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,payload_json)
  VALUES ('aud_admin_'||encode(public.gen_random_bytes(16),'hex'),actor.user_id,'ADMIN',
          'ADMIN_OPERATOR_VERTEX_CHANGED','SYSTEM','service_limits',
          jsonb_build_object('previous',previous,'enabled',p_enabled));
END
$admin_set_operator_vertex_fallback_v1$;

-- 사용자별 AI 검토 사용량. 자기 키 등록 여부만 보인다 - 키·마지막 네 글자·프로젝트는 싣지 않는다.
CREATE FUNCTION public.admin_list_ai_usage_v1(p_actor_user_id text)
RETURNS TABLE(
  user_id text, username text, email text, has_own_key boolean, ai_judgement_enabled boolean,
  own_today bigint, shared_today bigint, own_month bigint, shared_month bigint
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $admin_list_ai_usage_v1$
DECLARE today date := (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
BEGIN
  PERFORM public.admin_require_actor_v1(p_actor_user_id);
  PERFORM pg_catalog.set_config('app.v216_ai_usage_read','on',true);
  RETURN QUERY
  WITH usage AS (
    SELECT u.user_id,
      COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OWNER'
        AND (u.at AT TIME ZONE 'Asia/Seoul')::date=today),0)::bigint own_today,
      COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OPERATOR'
        AND (u.at AT TIME ZONE 'Asia/Seoul')::date=today),0)::bigint shared_today,
      COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OWNER'),0)::bigint own_month,
      COALESCE(sum(u.provider_call_count) FILTER (WHERE u.ai_credential_source='OPERATOR'),0)::bigint shared_month
    FROM public.ai_usage_calls_v216 u
    WHERE (u.at AT TIME ZONE 'Asia/Seoul')>=date_trunc('month',today::timestamp)
    GROUP BY u.user_id
  )
  SELECT usr.user_id,usr.username,pw.email_normalized,
    EXISTS (SELECT 1 FROM public.strong_llm_owner_credentials c WHERE c.owner_user_id=usr.user_id AND c.slot='PRIMARY'),
    COALESCE((SELECT s.ai_judgement_enabled FROM public.strong_llm_owner_settings s WHERE s.owner_user_id=usr.user_id),false),
    COALESCE(usage.own_today,0)::bigint,COALESCE(usage.shared_today,0)::bigint,
    COALESCE(usage.own_month,0)::bigint,COALESCE(usage.shared_month,0)::bigint
  FROM public.users usr
  LEFT JOIN public.password_login_identities pw ON pw.user_id=usr.user_id
  LEFT JOIN usage ON usage.user_id=usr.user_id
  ORDER BY COALESCE(usage.own_month,0)+COALESCE(usage.shared_month,0) DESC,usr.created_at DESC,usr.user_id
  LIMIT 500;
  PERFORM pg_catalog.set_config('app.v216_ai_usage_read','off',true);
END
$admin_list_ai_usage_v1$;

DO $admin_ai_grants$
DECLARE fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'admin_read_operator_vertex_v1(text)',
    'admin_set_operator_vertex_fallback_v1(text,boolean)',
    'admin_list_ai_usage_v1(text)'
  ] LOOP
    EXECUTE format('ALTER FUNCTION public.%s OWNER TO flyway', fn);
    EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay', fn);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO decision_auth', fn);
  END LOOP;
END
$admin_ai_grants$;
