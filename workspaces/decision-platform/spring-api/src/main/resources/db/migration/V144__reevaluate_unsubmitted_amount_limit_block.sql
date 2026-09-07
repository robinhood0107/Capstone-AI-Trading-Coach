-- Explicit owner correction: re-evaluate the same unsubmitted intent after removing
-- only the single-order ceiling. Preserve the old decision and snapshot in audit_logs.
SET LOCAL row_security = on;
CREATE FUNCTION public.p1_replan_amount_limit_block_v1(
 p_user_id text,p_expected_control_version integer,p_new_policy_version integer
) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE c public.automation_control%ROWTYPE;
DECLARE r public.automation_runs%ROWTYPE;
DECLARE cp public.automation_runtime_checkpoint%ROWTYPE;
DECLARE reservation public.automation_order_reservations%ROWTYPE;
DECLARE old_decision public.decisions%ROWTYPE;
DECLARE new_policy public.automation_policy_versions%ROWTYPE;
DECLARE old_rules jsonb;
DECLARE new_rules jsonb;
DECLARE now_local timestamp := statement_timestamp() AT TIME ZONE 'Asia/Seoul';
DECLARE audit_id text;
DECLARE ready record;
BEGIN
 IF session_user<>'decision_automation_runtime' OR p_user_id IS NULL
    OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
    OR now_local::time<time '09:30' OR now_local::time>=time '15:20' THEN
  RAISE EXCEPTION 'policy replan scope closed' USING ERRCODE='42501'; END IF;
 PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
 PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));
 SELECT * INTO c FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
 IF c.user_id IS NULL OR c.control_state<>'DISARMED' OR c.brokerage_mode<>'KIS_MOCK'
    OR c.version IS DISTINCT FROM p_expected_control_version THEN
  RAISE EXCEPTION 'policy replan control drift' USING ERRCODE='40001'; END IF;
 SELECT * INTO r FROM public.automation_runs WHERE user_id=p_user_id
  AND account_id=c.account_id AND session_date=now_local::date AND brokerage_mode='KIS_MOCK' FOR UPDATE;
 IF r.run_id IS NULL THEN RAISE EXCEPTION 'policy replan run missing' USING ERRCODE='40001'; END IF;
 audit_id:='aud_'||md5(r.run_id||':AMOUNT_LIMIT_REPLAN');
 IF EXISTS(SELECT 1 FROM public.audit_logs WHERE audit_log_id=audit_id) THEN
  RAISE EXCEPTION 'policy replan already consumed' USING ERRCODE='40001'; END IF;
 SELECT * INTO cp FROM public.automation_runtime_checkpoint WHERE run_id=r.run_id FOR UPDATE;
 SELECT * INTO reservation FROM public.automation_order_reservations WHERE run_id=r.run_id FOR UPDATE;
 SELECT * INTO old_decision FROM public.decisions WHERE decision_id=cp.decision_id;
 IF r.state<>'SKIPPED_NO_ACTION' OR cp.state<>r.state OR r.physical_submit_count<>0
    OR cp.logical_submit_count<>0 OR reservation.reservation_id IS NULL
    OR reservation.logical_submit_count<>0 OR reservation.order_id IS NOT NULL
    OR reservation.provider_order_ref_hash IS NOT NULL OR reservation.filled_quantity<>0
    OR reservation.unfilled_terminated_quantity<>0
    OR old_decision.decision_id IS NULL OR old_decision.outcome<>'BLOCK'
    OR jsonb_array_length(old_decision.result_json->'riskDecision'->'violations')<>1
    OR old_decision.result_json#>>'{riskDecision,violations,0,ruleId}'<>'max_single_order_amount' THEN
  RAISE EXCEPTION 'policy replan requires an unsubmitted amount-only block' USING ERRCODE='40001'; END IF;
 SELECT * INTO new_policy FROM public.automation_policy_versions_effective
  WHERE user_id=p_user_id AND policy_id=c.policy_id AND version=p_new_policy_version;
 IF new_policy.policy_id IS NULL OR new_policy.version<=c.policy_version
    OR new_policy.principle_id<>c.principle_id OR NOT EXISTS(
      SELECT 1 FROM public.principles p WHERE p.principle_id=c.principle_id
       AND p.user_id=p_user_id AND p.status='ACTIVE' AND p.current_version=new_policy.principle_version
    ) THEN RAISE EXCEPTION 'policy replan new policy invalid' USING ERRCODE='40001'; END IF;
 SELECT rules_json INTO old_rules FROM public.principle_versions
  WHERE principle_version_id=old_decision.principle_version_id;
 SELECT rules_json INTO new_rules FROM public.principle_versions
  WHERE principle_version_id=new_policy.principle_version_id;
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(new_rules) item
      WHERE item->>'ruleId'='max_single_order_amount' AND item->>'enabled'='false')
    OR (SELECT jsonb_agg(item ORDER BY item->>'ruleId') FROM jsonb_array_elements(old_rules) item
        WHERE item->>'ruleId'<>'max_single_order_amount') IS DISTINCT FROM
       (SELECT jsonb_agg(item ORDER BY item->>'ruleId') FROM jsonb_array_elements(new_rules) item
        WHERE item->>'ruleId'<>'max_single_order_amount') THEN
  RAISE EXCEPTION 'policy replan contains unrelated rule changes' USING ERRCODE='40001'; END IF;
 SELECT * INTO ready FROM public.p1_automation_runtime_readiness_v1(p_user_id,now_local::date);
 IF NOT COALESCE(ready.certification_valid AND ready.release_source_bound AND ready.real_team_b_ready
    AND ready.principle_current AND ready.kill_switch_inactive AND ready.account_baseline_matches
    AND ready.unresolved_state_clear,false) THEN
  RAISE EXCEPTION 'policy replan readiness closed' USING ERRCODE='40001'; END IF;
 INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json)
 VALUES(audit_id,p_user_id,'USER','AUTOMATION_POLICY_REPLAN','AUTOMATION_RUN',r.run_id,
  'req_'||md5(audit_id),jsonb_build_object('priorCheckpoint',to_jsonb(cp),
   'priorReservation',to_jsonb(reservation),'priorControlVersion',c.version,
   'priorPolicyVersion',c.policy_version,'newPolicyVersion',new_policy.version,
   'priorPrincipleVersionId',c.principle_version_id,'newPrincipleVersionId',new_policy.principle_version_id));
 UPDATE public.automation_control SET control_state='ARMED',version=c.version+1,policy_version=new_policy.version,
  principle_version_id=new_policy.principle_version_id,principle_version=new_policy.principle_version,
  updated_at=statement_timestamp() WHERE user_id=p_user_id;
 UPDATE public.automation_runs SET state='ORDER_SIZING',policy_version=new_policy.version,
  updated_at=statement_timestamp() WHERE run_id=r.run_id;
 UPDATE public.automation_runtime_checkpoint SET state='ORDER_SIZING',decision_id=NULL,
  checkpoint_version=cp.checkpoint_version+1,updated_at=statement_timestamp() WHERE run_id=r.run_id;
 UPDATE public.automation_order_reservations SET policy_version=new_policy.version,
  principle_version_id=new_policy.principle_version_id,updated_at=statement_timestamp()
  WHERE run_id=r.run_id;
 UPDATE public.automation_runtime_claim SET claim_state='ACTIVE',released_at=NULL
  WHERE run_id=r.run_id AND claim_state='RELEASED';
 IF NOT FOUND THEN RAISE EXCEPTION 'policy replan claim missing' USING ERRCODE='40001'; END IF;
 UPDATE public.automation_runtime_schedule SET control_version=c.version+1,
  schedule_state=CASE WHEN session_date=now_local::date THEN 'CLAIMED' ELSE 'ARMED' END,
  updated_at=statement_timestamp() WHERE user_id=p_user_id
   AND (session_date=now_local::date OR
     (session_date>now_local::date AND schedule_state='DISARMED' AND control_version=c.version-1));
 IF reservation.side='SELL' THEN
  UPDATE public.automation_positions SET status='EXIT_PENDING',exit_reason=reservation.exit_reason
   WHERE user_id=p_user_id AND account_id=c.account_id AND symbol=reservation.symbol
    AND status='OPEN' AND quantity>=reservation.quantity;
  IF NOT FOUND THEN RAISE EXCEPTION 'policy replan sell position missing' USING ERRCODE='40001'; END IF;
 END IF;
 RETURN c.version+1;
END $f$;
ALTER FUNCTION public.p1_replan_amount_limit_block_v1(text,integer,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_replan_amount_limit_block_v1(text,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_replan_amount_limit_block_v1(text,integer,integer) TO decision_automation_runtime;

CREATE OR REPLACE FUNCTION public.p1_read_automation_v3_metadata_v1(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE result jsonb;
DECLARE checkpoint_calls integer;
DECLARE decision_epoch integer;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation v3 metadata actor invalid' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT provider_call_count INTO checkpoint_calls
  FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  checkpoint_calls:=COALESCE(checkpoint_calls,0);
  SELECT jsonb_build_object(
    'candidateSetSha256',usage.candidate_set_sha256,
    'evidenceSetSha256',usage.evidence_set_sha256,
    'groundingQueryCount',COALESCE(usage.grounding_query_count,0),
    'providerCallCount',greatest(COALESCE(usage.provider_call_count,0),checkpoint_calls),
    'screeningProviderCallCount',COALESCE(usage.screening_provider_call_count,0),
    'screenings',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'evidence',COALESCE((SELECT jsonb_agg(jsonb_build_object(
          'ageWarning',evidence.age_warning,'boundedQuote',evidence.bounded_quote,
          'citationId',evidence.citation_id,'quoteSha256',evidence.quote_sha256,
          'sourceEventDate',evidence.source_event_date,'sourceId',evidence.source_id,
          'sourceType',evidence.source_type,'symbol',evidence.symbol,
          'uriSha256',evidence.uri_sha256,'verified',evidence.verified
        ) ORDER BY evidence.citation_id) FROM public.automation_candidate_evidence evidence
          WHERE evidence.run_id=screening.run_id AND evidence.symbol=screening.symbol),'[]'::jsonb),
        'isEtfEtn',screening.is_etf_etn,'lowerLimitKrw',screening.lower_limit_krw,
        'priceKrw',screening.quote_price_krw,'reason',screening.reason,
        'scoreBps',screening.score_bps,'status',screening.status,'symbol',screening.symbol,
        'upperLimitKrw',screening.upper_limit_krw,'verdict',screening.verdict
      ) ORDER BY screening.symbol)
      FROM public.automation_candidate_screenings screening
      WHERE screening.run_id=p_run_id
    ),'[]'::jsonb)
  ) INTO result
  FROM (SELECT * FROM public.automation_v3_usage WHERE run_id=p_run_id) usage;
  SELECT count(*) INTO decision_epoch FROM public.audit_logs
  WHERE user_id=claim_row.user_id AND target_id=p_run_id AND action='AUTOMATION_POLICY_REPLAN';
  RETURN (COALESCE(result,jsonb_build_object(
    'candidateSetSha256',NULL,'evidenceSetSha256',NULL,'groundingQueryCount',0,
    'providerCallCount',checkpoint_calls,'screeningProviderCallCount',0,'screenings','[]'::jsonb
  ))||jsonb_build_object('decisionEpoch',decision_epoch))::text;
END
$function$;
