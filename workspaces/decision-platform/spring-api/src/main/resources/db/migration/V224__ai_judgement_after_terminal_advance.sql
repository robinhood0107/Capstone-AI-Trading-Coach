-- advance_with_ai_judgement 는 한 트랜잭션에서 체크포인트를 먼저 넘기고 AI 판정을 기록한다.
-- 다음 상태가 종료(NEWS_VETOED·COMPLETED 등)면 p1_advance_automation_checkpoint_v2 가 같은
-- 트랜잭션 안에서 클레임을 RELEASED 로 닫는다. 이 함수는 ACTIVE 클레임만 찾았고, 조회 뒤
-- PERFORM set_config 가 FOUND 를 덮어써 "클레임 없음" 검사가 발동하지 않았다. 그래서 소유자
-- 설정이 빈 채 INSERT 해 RLS 위반으로 트랜잭션이 되돌아가고, 세션은 같은 틱을 영원히 재시도했다.
-- 이 트랜잭션에서 방금 닫힌 클레임은 받아들이고, FOUND 는 조회 직후에 잡는다.
CREATE OR REPLACE FUNCTION public.p1_record_automation_ai_judgement_v1(
  p_run_id text, p_claim_token_hash text, p_checkpoint_version integer, p_participation text,
  p_provider_id text, p_prompt_version text, p_confidence_bps integer, p_baseline_symbol text,
  p_selected_symbol text, p_vetoed_symbol_count integer, p_judge_call_count integer,
  p_candidate_count integer, p_quantity_before integer, p_quantity_after integer,
  p_verdicts_json text
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE claim_found boolean;
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
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE'
         OR (claim_state='RELEASED' AND released_at>=pg_catalog.transaction_timestamp()));
  claim_found:=FOUND;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT claim_found THEN
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
$function$;
