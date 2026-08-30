-- 자동운용 상태기에 AI_JUDGING을 넣는다. 후보 선정과 뉴스 확인 사이가 아니라 그 앞이다.
-- Return Engine이 고른 후보 집합에 대해 Strong LLM이 순위와 차단을 말하고, 무엇을 얼마나
-- 살지는 엔진이 결정론적으로 계산한다. 모델은 이 상태 밖으로 나가지 못한다.
--
-- 전이 표는 app/p1_owner/automation.py::_LEGAL_TRANSITIONS 와 한 글자도 어긋나면 안 된다.
-- tests/p1_owner/test_automation_sql_alignment.py 가 양쪽을 대조한다.

ALTER TABLE public.automation_runs
  DROP CONSTRAINT automation_runs_state_check,
  ADD CONSTRAINT automation_runs_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','AI_JUDGING',
    'BUY_CANDIDATE_SELECTED','NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING','RISK_CHECKING',
    'ORDER_SUBMITTING','ORDER_SUBMITTED','PENDING_RECONCILIATION','CANCELLED_UNFILLED',
    'COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ));

ALTER TABLE public.automation_runtime_checkpoint
  DROP CONSTRAINT automation_runtime_checkpoint_state_check,
  ADD CONSTRAINT automation_runtime_checkpoint_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','AI_JUDGING',
    'BUY_CANDIDATE_SELECTED','NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING','RISK_CHECKING',
    'ORDER_SUBMITTING','ORDER_SUBMITTED','PENDING_RECONCILIATION','CANCELLED_UNFILLED',
    'COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ));

-- PRECHECK -> BUY_CANDIDATE_SELECTED 직행은 남겨 둔다. 이 변경 전에 그 상태로 checkpoint된
-- run이 재개될 수 있어야 하고, 엔진은 더 이상 그 전이를 내지 않는다.
CREATE OR REPLACE FUNCTION public.p1_automation_transition_valid_v2(p_current text,p_next text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_transition_valid_v2$
  SELECT (p_current,p_next) IN (
    ('AI_JUDGING','BUY_CANDIDATE_SELECTED'),('AI_JUDGING','HALTED'),
    ('AI_JUDGING','SKIPPED_NO_ACTION'),
    ('BUY_CANDIDATE_SELECTED','HALTED'),('BUY_CANDIDATE_SELECTED','NEWS_CHECKING'),
    ('EXIT_SELECTED','HALTED'),('EXIT_SELECTED','ORDER_SIZING'),
    ('NEWS_CHECKING','HALTED'),('NEWS_CHECKING','NEWS_VETOED'),
    ('NEWS_CHECKING','ORDER_SIZING'),('NEWS_CHECKING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SIZING','HALTED'),('ORDER_SIZING','RISK_CHECKING'),
    ('ORDER_SIZING','SKIPPED_DATA_UNAVAILABLE'),('ORDER_SIZING','SKIPPED_LATE_START'),
    ('ORDER_SIZING','SKIPPED_NO_ACTION'),('ORDER_SUBMITTED','CANCELLED_UNFILLED'),
    ('ORDER_SUBMITTED','COMPLETED'),('ORDER_SUBMITTED','HALTED'),
    ('ORDER_SUBMITTED','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','HALTED'),
    ('ORDER_SUBMITTING','ORDER_SUBMITTED'),('ORDER_SUBMITTING','ORDER_SUBMITTING'),
    ('ORDER_SUBMITTING','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SUBMITTING','SKIPPED_LATE_START'),('PENDING_RECONCILIATION','AI_JUDGING'),
    ('PENDING_RECONCILIATION','BUY_CANDIDATE_SELECTED'),
    ('PENDING_RECONCILIATION','CANCELLED_UNFILLED'),('PENDING_RECONCILIATION','COMPLETED'),
    ('PENDING_RECONCILIATION','EXIT_SELECTED'),('PENDING_RECONCILIATION','HALTED'),
    ('PENDING_RECONCILIATION','PENDING_RECONCILIATION'),('PENDING_RECONCILIATION','SKIPPED_DATA_UNAVAILABLE'),
    ('PENDING_RECONCILIATION','SKIPPED_NO_ACTION'),('PRECHECK','AI_JUDGING'),
    ('PRECHECK','BUY_CANDIDATE_SELECTED'),
    ('PRECHECK','EXIT_SELECTED'),('PRECHECK','HALTED'),
    ('PRECHECK','RECONCILING_PREVIOUS'),('PRECHECK','SKIPPED_DATA_UNAVAILABLE'),
    ('PRECHECK','SKIPPED_NO_ACTION'),('RECONCILING_PREVIOUS','AI_JUDGING'),
    ('RECONCILING_PREVIOUS','BUY_CANDIDATE_SELECTED'),
    ('RECONCILING_PREVIOUS','EXIT_SELECTED'),('RECONCILING_PREVIOUS','HALTED'),
    ('RECONCILING_PREVIOUS','PENDING_RECONCILIATION'),('RECONCILING_PREVIOUS','SKIPPED_DATA_UNAVAILABLE'),
    ('RECONCILING_PREVIOUS','SKIPPED_NO_ACTION'),('RISK_CHECKING','HALTED'),
    ('RISK_CHECKING','ORDER_SUBMITTING'),('RISK_CHECKING','SKIPPED_NO_ACTION'),
    ('SCHEDULED','HALTED'),('SCHEDULED','PRECHECK'),
    ('SCHEDULED','SCHEDULED'),('SCHEDULED','SKIPPED_DATA_UNAVAILABLE'),
    ('SCHEDULED','SKIPPED_LATE_START'),('SCHEDULED','SKIPPED_NO_ACTION')
  )
$p1_automation_transition_valid_v2$;

-- AI가 무엇을 바꿨는지 남기는 자리다. 이것이 없으면 "AI의 판단이 반영된다"는 말을 사후에
-- 확인할 수 없고, 그러면 이 권한 승격 자체가 검증 불가능해진다.
--
-- checkpoint 함수에 붙이지 않고 따로 둔다. 그 함수는 31개 인자를 받는 CAS 경로이고, 판단
-- 기록이 늘 때마다 그 경로를 다시 쓰면 매매 전이의 원자성을 판단 기록 스키마가 흔든다.
-- (run_id,checkpoint_version) upsert라 재생돼도 같은 행 하나로 수렴한다.
CREATE TABLE public.automation_ai_judgements (
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  checkpoint_version integer NOT NULL CHECK (checkpoint_version >= 1),
  participation text NOT NULL CHECK (participation IN ('APPLIED','NOT_PARTICIPATED')),
  provider_id text NOT NULL CHECK (provider_id ~ '^[a-z_]{0,32}$'),
  prompt_version text NOT NULL CHECK (prompt_version ~ '^[A-Za-z0-9/.+-]{0,128}$'),
  -- 0..10000 basis point로 담는다. 부동소수로 저장하면 같은 판단이 저장 왕복에서 달라진다.
  confidence_bps integer CHECK (confidence_bps IS NULL OR confidence_bps BETWEEN 0 AND 10000),
  baseline_symbol text CHECK (baseline_symbol IS NULL OR baseline_symbol ~ '^[0-9]{6}$'),
  selected_symbol text CHECK (selected_symbol IS NULL OR selected_symbol ~ '^[0-9]{6}$'),
  vetoed_symbol_count integer NOT NULL CHECK (vetoed_symbol_count BETWEEN 0 AND 32),
  -- 1차와 2차, 그 둘이 전부다. 물어보지 않은 run은 0이고 그것도 사실로 남는다.
  judge_call_count integer NOT NULL CHECK (judge_call_count BETWEEN 0 AND 2),
  candidate_count integer NOT NULL CHECK (candidate_count BETWEEN 0 AND 32),
  quantity_before integer CHECK (quantity_before IS NULL OR quantity_before >= 0),
  quantity_after integer CHECK (quantity_after IS NULL OR quantity_after >= 0),
  -- 후보별 점수와 사유. 모델이 쓴 글이므로 여기 밖으로 나가 결정에 쓰이지 않는다.
  verdicts_json text NOT NULL CHECK (pg_catalog.length(verdicts_json) BETWEEN 2 AND 16384),
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,checkpoint_version),
  -- AI는 수량을 늘리지 못한다. 이 불변식을 코드가 아니라 여기에서도 지킨다.
  CONSTRAINT automation_ai_judgements_size_only_shrinks_check CHECK (
    quantity_before IS NULL OR quantity_after IS NULL OR quantity_after <= quantity_before
  )
);

ALTER TABLE public.automation_ai_judgements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_ai_judgements FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_ai_judgements_scope_v106 ON public.automation_ai_judgements TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND EXISTS (
    SELECT 1 FROM public.automation_runtime_checkpoint checkpoint
    WHERE checkpoint.run_id=automation_ai_judgements.run_id
      AND checkpoint.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND EXISTS (
    SELECT 1 FROM public.automation_runtime_checkpoint checkpoint
    WHERE checkpoint.run_id=automation_ai_judgements.run_id
      AND checkpoint.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
);

-- 화면이 "AI가 무엇을 바꿨는지"를 보여주려면 소유자 세션도 이 기록을 읽어야 한다. 쓰기는
-- 여전히 자동운용 role의 definer 함수만 할 수 있고, 여기는 읽기만 연다.
CREATE POLICY automation_ai_judgements_owner_v106 ON public.automation_ai_judgements
FOR SELECT TO PUBLIC
USING (
  session_user='decision_app' AND public.actor_rls_scope_is_open_v1()
  AND EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.run_id=automation_ai_judgements.run_id
      AND (
        current_user='flyway'
        OR run.user_id=pg_catalog.current_setting('app.actor_user_id',true)
      )
  )
);

GRANT SELECT ON TABLE public.automation_ai_judgements TO decision_app;

CREATE OR REPLACE FUNCTION public.p1_record_automation_ai_judgement_v1(
  p_run_id text,
  p_claim_token_hash text,
  p_checkpoint_version integer,
  p_participation text,
  p_provider_id text,
  p_prompt_version text,
  p_confidence_bps integer,
  p_baseline_symbol text,
  p_selected_symbol text,
  p_vetoed_symbol_count integer,
  p_judge_call_count integer,
  p_candidate_count integer,
  p_quantity_before integer,
  p_quantity_after integer,
  p_verdicts_json text
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $p1_record_automation_ai_judgement_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_checkpoint_version<1
     OR p_participation NOT IN ('APPLIED','NOT_PARTICIPATED')
     OR (p_quantity_before IS NOT NULL AND p_quantity_after IS NOT NULL
         AND p_quantity_after>p_quantity_before) THEN
    RAISE EXCEPTION 'automation ai judgement input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE';
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  INSERT INTO public.automation_ai_judgements (
    run_id,checkpoint_version,participation,provider_id,prompt_version,confidence_bps,
    baseline_symbol,selected_symbol,vetoed_symbol_count,judge_call_count,candidate_count,
    quantity_before,quantity_after,verdicts_json,recorded_at
  ) VALUES (
    p_run_id,p_checkpoint_version,p_participation,p_provider_id,p_prompt_version,p_confidence_bps,
    p_baseline_symbol,p_selected_symbol,p_vetoed_symbol_count,p_judge_call_count,p_candidate_count,
    p_quantity_before,p_quantity_after,p_verdicts_json,pg_catalog.now()
  )
  -- 같은 tick이 재생되면 같은 판단이 다시 온다. 행을 늘리지 않고 그대로 둔다.
  ON CONFLICT (run_id,checkpoint_version) DO NOTHING;
  RETURN true;
END;
$p1_record_automation_ai_judgement_v1$;

ALTER FUNCTION public.p1_record_automation_ai_judgement_v1(
  text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_record_automation_ai_judgement_v1(
  text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_record_automation_ai_judgement_v1(
  text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text
) TO decision_automation_runtime;

-- 판단은 AI_JUDGING tick에서 나오고 수량은 ORDER_SIZING tick에서 정해진다. 서로 다른 tick이라
-- 엔진의 run 객체로는 건너오지 못한다. 그래서 저장된 판단을 상태 읽기가 다시 실어 온다.
-- 이것을 `p1_read_automation_runtime_state_v2` 안에 넣지 않는다. 그 함수는 140줄짜리 정의라
-- 한 줄을 더하려고 전체를 다시 쓰면 매번 그 전체가 재검토 대상이 된다.
CREATE FUNCTION public.p1_read_automation_ai_judgement_v1(p_run_id text,p_claim_token_hash text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public
AS $p1_read_automation_ai_judgement_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE judgement_row public.automation_ai_judgements%ROWTYPE;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation ai judgement read invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE';
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO judgement_row FROM public.automation_ai_judgements
  WHERE run_id=p_run_id ORDER BY checkpoint_version DESC LIMIT 1;
  IF NOT FOUND OR judgement_row.participation<>'APPLIED' THEN
    RETURN NULL;
  END IF;
  -- 모델이 쓴 글(verdicts_json)은 여기로 돌려주지 않는다. 그것은 사후 감사용 기록이고,
  -- 다시 읽어 결정에 쓰면 프롬프트 출력이 두 번째 통로로 매매에 닿는다.
  RETURN jsonb_build_object(
    'baselineSymbol',judgement_row.baseline_symbol,
    'checkpointVersion',judgement_row.checkpoint_version,
    'confidenceBps',judgement_row.confidence_bps,
    'participation',judgement_row.participation,
    'selectedSymbol',judgement_row.selected_symbol
  )::text;
END;
$p1_read_automation_ai_judgement_v1$;

ALTER FUNCTION public.p1_read_automation_ai_judgement_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_ai_judgement_v1(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_ai_judgement_v1(text,text)
  TO decision_automation_runtime;
