-- At most two retries of a pre-order data failure; one run and one order budget per day.
SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.p1_resume_automation_data_gap_v1(p_user_id text,p_expected_control_version integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE local_now timestamp := statement_timestamp() AT TIME ZONE 'Asia/Seoul';
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE readiness record;
DECLARE event_key text;
DECLARE new_version integer;
DECLARE retry_count integer;
BEGIN
 IF session_user<>'decision_automation_runtime' OR p_user_id IS NULL
    OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$' OR p_expected_control_version IS NULL
    OR local_now::time<time '09:30' OR local_now::time>=time '15:20' THEN
  RAISE EXCEPTION 'automation resume scope closed' USING ERRCODE='42501';
 END IF;
 PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
 PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));
 SELECT * INTO control_row FROM public.automation_control c WHERE c.user_id=p_user_id FOR UPDATE;
 IF control_row.user_id IS NULL OR control_row.control_state<>'ARMED'
    OR control_row.version<>p_expected_control_version OR control_row.brokerage_mode<>'KIS_MOCK' THEN
  RAISE EXCEPTION 'automation resume control drift' USING ERRCODE='40001';
 END IF;
 SELECT r.* INTO run_row FROM public.automation_runs r
 WHERE r.user_id=p_user_id AND r.session_date=local_now::date
   AND r.account_id=control_row.account_id AND r.brokerage_mode='KIS_MOCK' FOR UPDATE;
 IF run_row.run_id IS NULL THEN
  RAISE EXCEPTION 'automation resume run missing' USING ERRCODE='40001';
 END IF;
 SELECT count(*) INTO retry_count FROM public.automation_runtime_events e
 WHERE e.run_id=run_row.run_id AND e.event_type='SESSION_RESUMED';
 IF retry_count>=2 THEN
  RAISE EXCEPTION 'automation fallback limit reached' USING ERRCODE='40001';
 END IF;
 IF local_now < (run_row.updated_at AT TIME ZONE 'Asia/Seoul')
      + (CASE WHEN retry_count=0 THEN interval '2 minutes' ELSE interval '5 minutes' END) THEN
  RAISE EXCEPTION 'automation fallback not due' USING ERRCODE='40001';
 END IF;
 event_key:='auto_rte_'||md5(run_row.run_id||':BOUNDED_DATA_GAP_RETRY:'||(retry_count+1)::text);
 SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint c
 WHERE c.run_id=run_row.run_id FOR UPDATE;
 IF run_row.state<>'SKIPPED_DATA_UNAVAILABLE' OR checkpoint_row.state<>'SKIPPED_DATA_UNAVAILABLE'
    OR checkpoint_row.provider_call_count>=16 OR run_row.provider_calls>=16
    OR run_row.physical_submit_count<>0 OR checkpoint_row.logical_submit_count<>0
    OR run_row.selected_symbol IS NOT NULL OR checkpoint_row.selected_symbol IS NOT NULL
    OR checkpoint_row.decision_id IS NOT NULL
    OR run_row.policy_id IS DISTINCT FROM control_row.policy_id
    OR run_row.policy_version IS DISTINCT FROM control_row.policy_version
    OR EXISTS(SELECT 1 FROM public.automation_order_reservations r WHERE r.run_id=run_row.run_id)
    OR EXISTS(SELECT 1 FROM public.automation_runtime_claim c
      WHERE c.user_id=p_user_id AND c.claim_state='ACTIVE') THEN
  RAISE EXCEPTION 'automation resume is not a pre-order data gap' USING ERRCODE='40001';
 END IF;
 SELECT * INTO readiness FROM public.p1_automation_runtime_readiness_v1(p_user_id,local_now::date);
 IF NOT COALESCE(readiness.control_configured AND readiness.certification_valid
    AND readiness.release_source_bound AND readiness.real_team_b_ready
    AND readiness.principle_current AND readiness.kill_switch_inactive
    AND readiness.account_baseline_matches AND readiness.unresolved_state_clear,false) THEN
  RAISE EXCEPTION 'automation resume readiness closed' USING ERRCODE='40001';
 END IF;
 -- Reserve a potentially unrecorded failed quote attempt; preserve the existing budget.
 UPDATE public.automation_runtime_checkpoint c SET state='SCHEDULED',
   checkpoint_version=c.checkpoint_version+1,provider_call_count=c.provider_call_count+1,
   updated_at=statement_timestamp() WHERE c.run_id=run_row.run_id
   RETURNING checkpoint_version INTO new_version;
 UPDATE public.automation_runs r SET state='SCHEDULED',provider_calls=r.provider_calls+1,
   updated_at=statement_timestamp() WHERE r.run_id=run_row.run_id;
 UPDATE public.automation_runtime_claim c SET claim_state='ACTIVE',released_at=NULL
   WHERE c.run_id=run_row.run_id AND c.claim_state='RELEASED';
 IF NOT FOUND THEN RAISE EXCEPTION 'automation resume claim missing' USING ERRCODE='40001'; END IF;
 UPDATE public.automation_runtime_schedule s SET schedule_state='CLAIMED',run_at=statement_timestamp(),
   updated_at=statement_timestamp() WHERE s.user_id=p_user_id AND s.session_date=local_now::date
   AND s.control_version=control_row.version AND s.schedule_state='COMPLETED';
 IF NOT FOUND THEN RAISE EXCEPTION 'automation resume schedule missing' USING ERRCODE='40001'; END IF;
 INSERT INTO public.automation_runtime_events VALUES(event_key,p_user_id,local_now::date,run_row.run_id,
   'SESSION_RESUMED',encode(public.digest(convert_to(run_row.run_id||':'||new_version::text||
   ':BOUNDED_PRE_ORDER_DATA_GAP_RETRY','UTF8'),'sha256'),'hex'),true,statement_timestamp());
 RETURN new_version;
END $f$;
ALTER FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) TO decision_automation_runtime;

CREATE FUNCTION public.p1_automation_data_gap_retry_at_v1(p_user_id text,p_session date)
RETURNS timestamptz LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE target_time timestamptz;
BEGIN
 IF session_user<>'decision_automation_runtime' OR p_user_id IS NULL
    OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$' THEN
  RAISE EXCEPTION 'automation retry schedule scope denied' USING ERRCODE='42501'; END IF;
 PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
 SELECT r.updated_at + CASE WHEN retry.used=0 THEN interval '2 minutes' ELSE interval '5 minutes' END
 INTO target_time
 FROM public.automation_runs r JOIN public.automation_control c USING(user_id)
 JOIN public.automation_runtime_checkpoint cp USING(run_id)
 CROSS JOIN LATERAL (SELECT count(*) AS used FROM public.automation_runtime_events e
   WHERE e.run_id=r.run_id AND e.event_type='SESSION_RESUMED') retry
 WHERE r.user_id=p_user_id AND r.session_date=p_session AND retry.used<2
  AND r.brokerage_mode='KIS_MOCK' AND r.account_id=c.account_id
  AND r.state='SKIPPED_DATA_UNAVAILABLE' AND cp.state=r.state
  AND r.physical_submit_count=0 AND cp.logical_submit_count=0
  AND r.selected_symbol IS NULL AND cp.selected_symbol IS NULL AND cp.decision_id IS NULL
  AND cp.provider_call_count<16 AND r.provider_calls<16
  AND c.control_state='ARMED' AND r.policy_id=c.policy_id AND r.policy_version=c.policy_version
  AND NOT EXISTS(SELECT 1 FROM public.automation_order_reservations ar WHERE ar.run_id=r.run_id);
 IF target_time >= (p_session+time '15:20') AT TIME ZONE 'Asia/Seoul' THEN RETURN NULL; END IF;
 RETURN target_time;
END $f$;
ALTER FUNCTION public.p1_automation_data_gap_retry_at_v1(text,date) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_data_gap_retry_at_v1(text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_data_gap_retry_at_v1(text,date) TO decision_automation_runtime;
