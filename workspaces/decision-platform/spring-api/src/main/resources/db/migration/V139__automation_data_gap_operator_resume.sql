-- One explicit operator recovery of a same-day, pre-order data failure.
-- Existing events, ticks, run identity, policy and all call counters are preserved.
SET LOCAL row_security = on;
ALTER TABLE public.automation_runtime_events
 DROP CONSTRAINT automation_runtime_events_event_type_check;
ALTER TABLE public.automation_runtime_events ADD CHECK(event_type IN (
 'ACTIVATION_GATE_AUTHORED','SCHEDULE_ARMED','SCHEDULE_DISARMED','SESSION_CLAIMED',
 'CHECKPOINT_TRANSITIONED','SESSION_RELEASED','SESSION_RESUMED'
));

CREATE FUNCTION public.p1_resume_automation_data_gap_v1(p_user_id text,p_expected_control_version integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE local_now timestamp := statement_timestamp() AT TIME ZONE 'Asia/Seoul';
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE readiness record;
DECLARE event_key text;
DECLARE new_version integer;
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
 event_key:='auto_rte_'||md5(run_row.run_id||':OPERATOR_DATA_GAP_RESUME');
 IF EXISTS(SELECT 1 FROM public.automation_runtime_events e WHERE e.event_id=event_key) THEN
  RAISE EXCEPTION 'automation resume already consumed' USING ERRCODE='40001';
 END IF;
 SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint c
 WHERE c.run_id=run_row.run_id FOR UPDATE;
 IF run_row.state<>'SKIPPED_DATA_UNAVAILABLE' OR checkpoint_row.state<>'SKIPPED_DATA_UNAVAILABLE'
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
 -- Reserve two failed quote attempts from the pre-fix incident; never lower accounting.
 UPDATE public.automation_runtime_checkpoint c SET state='SCHEDULED',
   checkpoint_version=c.checkpoint_version+1,provider_call_count=greatest(c.provider_call_count,2),
   updated_at=statement_timestamp() WHERE c.run_id=run_row.run_id
   RETURNING checkpoint_version INTO new_version;
 UPDATE public.automation_runs r SET state='SCHEDULED',provider_calls=greatest(r.provider_calls,2),
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
   ':EXPLICIT_OPERATOR_PRE_ORDER_DATA_GAP','UTF8'),'sha256'),'hex'),true,statement_timestamp());
 RETURN new_version;
END $f$;
ALTER FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_resume_automation_data_gap_v1(text,integer) TO decision_automation_runtime;
