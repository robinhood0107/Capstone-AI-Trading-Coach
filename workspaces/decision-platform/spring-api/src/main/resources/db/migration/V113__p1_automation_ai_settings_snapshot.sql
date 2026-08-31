-- Durable owner AI enablement/thinking settings and arm-time immutable snapshot.

ALTER TABLE public.strong_llm_owner_settings
  ADD COLUMN ai_judgement_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN thinking_level text NOT NULL DEFAULT 'low',
  ADD CONSTRAINT strong_llm_owner_settings_thinking_v113_check CHECK (
    thinking_level IN ('minimal','low','medium')
  );

CREATE FUNCTION public.put_strong_llm_owner_settings_v2(
  p_owner_user_id text,p_provider text,p_fallback_provider text,p_model_id text,
  p_fallback_model_id text,p_base_url text,p_fallback_base_url text,
  p_answer_language text,p_daily_generate_call_cap integer,
  p_ai_judgement_enabled boolean,p_thinking_level text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $put_strong_llm_owner_settings_v2$
BEGIN
  IF p_thinking_level IS NOT NULL AND p_thinking_level NOT IN ('minimal','low','medium') THEN
    RAISE EXCEPTION 'strong llm thinking level invalid' USING ERRCODE='22023';
  END IF;
  PERFORM public.put_strong_llm_owner_settings_v1(
    p_owner_user_id,p_provider,p_fallback_provider,p_model_id,p_fallback_model_id,
    p_base_url,p_fallback_base_url,p_answer_language,p_daily_generate_call_cap
  );
  UPDATE public.strong_llm_owner_settings SET
    ai_judgement_enabled=COALESCE(p_ai_judgement_enabled,ai_judgement_enabled),
    thinking_level=COALESCE(p_thinking_level,thinking_level),updated_at=pg_catalog.now()
  WHERE owner_user_id=p_owner_user_id;
END
$put_strong_llm_owner_settings_v2$;

ALTER TABLE public.automation_control
  ADD COLUMN ai_settings_sha256 text,
  ADD COLUMN ai_settings_snapshot_json jsonb,
  ADD COLUMN ai_judgement_enabled_snapshot boolean,
  ADD COLUMN ai_thinking_level_snapshot text,
  ADD CONSTRAINT automation_control_ai_snapshot_v113_check CHECK (
    (ai_settings_sha256 IS NULL AND ai_settings_snapshot_json IS NULL
      AND ai_judgement_enabled_snapshot IS NULL
      AND ai_thinking_level_snapshot IS NULL)
    OR (ai_settings_sha256~'^[0-9a-f]{64}$'
      AND jsonb_typeof(ai_settings_snapshot_json)='object'
      AND ai_judgement_enabled_snapshot IS NOT NULL
      AND ai_thinking_level_snapshot IN ('minimal','low','medium'))
  );

ALTER TABLE public.automation_runs
  ADD COLUMN ai_settings_sha256 text,
  ADD COLUMN ai_settings_snapshot_json jsonb,
  ADD COLUMN ai_judgement_enabled_snapshot boolean,
  ADD COLUMN ai_thinking_level_snapshot text,
  ADD CONSTRAINT automation_runs_ai_snapshot_v113_check CHECK (
    (ai_settings_sha256 IS NULL AND ai_settings_snapshot_json IS NULL
      AND ai_judgement_enabled_snapshot IS NULL
      AND ai_thinking_level_snapshot IS NULL)
    OR (ai_settings_sha256~'^[0-9a-f]{64}$'
      AND jsonb_typeof(ai_settings_snapshot_json)='object'
      AND ai_judgement_enabled_snapshot IS NOT NULL
      AND ai_thinking_level_snapshot IN ('minimal','low','medium'))
  );

ALTER TABLE public.automation_ai_judgements
  ADD COLUMN ai_settings_sha256 text,
  ADD CONSTRAINT automation_ai_judgements_settings_v113_check CHECK (
    ai_settings_sha256 IS NULL OR ai_settings_sha256~'^[0-9a-f]{64}$'
  );

CREATE FUNCTION public.p1_automation_run_ai_snapshot_v1()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_run_ai_snapshot_v1$
DECLARE control_row public.automation_control%ROWTYPE;
BEGIN
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=NEW.user_id;
  IF control_row.ai_settings_sha256 IS NOT NULL THEN
    NEW.ai_settings_sha256:=control_row.ai_settings_sha256;
    NEW.ai_settings_snapshot_json:=control_row.ai_settings_snapshot_json;
    NEW.ai_judgement_enabled_snapshot:=control_row.ai_judgement_enabled_snapshot;
    NEW.ai_thinking_level_snapshot:=control_row.ai_thinking_level_snapshot;
  END IF;
  RETURN NEW;
END
$p1_automation_run_ai_snapshot_v1$;

CREATE TRIGGER automation_run_ai_snapshot_v113
BEFORE INSERT ON public.automation_runs
FOR EACH ROW EXECUTE FUNCTION public.p1_automation_run_ai_snapshot_v1();

CREATE OR REPLACE FUNCTION public.p1_arm_automation_v3(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text,
  p_provider_capability_ready boolean
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_v3$
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE settings_row public.strong_llm_owner_settings%ROWTYPE;
DECLARE base_result record;
DECLARE history_ready boolean;
DECLARE provider_ready boolean;
DECLARE settings_projection jsonb;
DECLARE settings_sha text;
BEGIN
  SELECT * INTO base_result FROM public.p1_arm_automation_v2(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,
    p_expected_control_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR policy_row.max_holding_sessions IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy unavailable' USING ERRCODE='40001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions
    WHERE user_id=p_user_id AND status IN ('OPEN','EXIT_PENDING')
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
    AND EXISTS (
      SELECT 1 FROM public.strong_llm_owner_credentials credential
      WHERE credential.owner_user_id=p_user_id AND credential.slot='PRIMARY'
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
$p1_arm_automation_v3$;

CREATE FUNCTION public.p1_read_automation_ai_settings_snapshot_v1(
  p_run_id text,p_claim_token_hash text
) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_ai_settings_snapshot_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE provider_ready boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation settings snapshot actor invalid' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
  -- AI-enabled runs reached this row only after arm-time provider readiness passed.
  -- Later credential drift still fails closed in the Spring provider bridge.
  provider_ready:=true;
  RETURN jsonb_build_object(
    'aiJudgementEnabled',COALESCE(run_row.ai_judgement_enabled_snapshot,false),
    'aiProviderReady',provider_ready,'aiSettingsSha256',run_row.ai_settings_sha256,
    'thinkingLevel',COALESCE(run_row.ai_thinking_level_snapshot,'low')
  )::text;
END
$p1_read_automation_ai_settings_snapshot_v1$;

CREATE FUNCTION public.p1_record_automation_ai_judgement_v2(
  p_run_id text,p_claim_token_hash text,p_checkpoint_version integer,p_participation text,
  p_provider_id text,p_prompt_version text,p_confidence_bps integer,p_baseline_symbol text,
  p_selected_symbol text,p_vetoed_symbol_count integer,p_judge_call_count integer,
  p_candidate_count integer,p_quantity_before integer,p_quantity_after integer,
  p_verdicts_json text,p_ai_settings_sha256 text,p_evidence_set_sha256 text,
  p_grounding_call_count integer,p_grounding_query_count integer,p_evidence_count integer
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_record_automation_ai_judgement_v2$
DECLARE recorded boolean;
BEGIN
  IF p_ai_settings_sha256!~'^[0-9a-f]{64}$'
     OR (p_evidence_set_sha256 IS NOT NULL AND p_evidence_set_sha256!~'^[0-9a-f]{64}$')
     OR p_grounding_call_count NOT BETWEEN 0 AND 1
     OR p_grounding_query_count NOT BETWEEN 0 AND 32
     OR p_evidence_count NOT BETWEEN 0 AND 155 THEN
    RAISE EXCEPTION 'automation ai v2 judgement input invalid' USING ERRCODE='22023';
  END IF;
  recorded:=public.p1_record_automation_ai_judgement_v1(
    p_run_id,p_claim_token_hash,p_checkpoint_version,p_participation,p_provider_id,
    p_prompt_version,p_confidence_bps,p_baseline_symbol,p_selected_symbol,
    p_vetoed_symbol_count,p_judge_call_count,p_candidate_count,p_quantity_before,
    p_quantity_after,p_verdicts_json
  );
  UPDATE public.automation_ai_judgements SET
    ai_settings_sha256=p_ai_settings_sha256,evidence_set_sha256=p_evidence_set_sha256,
    grounding_call_count=p_grounding_call_count,grounding_query_count=p_grounding_query_count,
    evidence_count=p_evidence_count
  WHERE run_id=p_run_id AND checkpoint_version=p_checkpoint_version
    AND (ai_settings_sha256 IS NULL OR ai_settings_sha256=p_ai_settings_sha256)
    AND (evidence_set_sha256 IS NULL OR evidence_set_sha256 IS NOT DISTINCT FROM p_evidence_set_sha256);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation ai v2 judgement conflict' USING ERRCODE='40001'; END IF;
  RETURN recorded;
END
$p1_record_automation_ai_judgement_v2$;

ALTER FUNCTION public.put_strong_llm_owner_settings_v2(text,text,text,text,text,text,text,text,integer,boolean,text) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_run_ai_snapshot_v1() OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_ai_settings_snapshot_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_record_automation_ai_judgement_v2(text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text,text,text,integer,integer,integer) OWNER TO flyway;

REVOKE ALL ON FUNCTION public.put_strong_llm_owner_settings_v2(text,text,text,text,text,text,text,text,integer,boolean,text) FROM PUBLIC,decision_app;
REVOKE ALL ON FUNCTION public.p1_read_automation_ai_settings_snapshot_v1(text,text) FROM PUBLIC,decision_app,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_record_automation_ai_judgement_v2(text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text,text,text,integer,integer,integer) FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.put_strong_llm_owner_settings_v2(text,text,text,text,text,text,text,text,integer,boolean,text) TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_ai_settings_snapshot_v1(text,text) TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_record_automation_ai_judgement_v2(text,text,integer,text,text,text,integer,text,text,integer,integer,integer,integer,integer,text,text,text,integer,integer,integer) TO decision_automation_runtime;
