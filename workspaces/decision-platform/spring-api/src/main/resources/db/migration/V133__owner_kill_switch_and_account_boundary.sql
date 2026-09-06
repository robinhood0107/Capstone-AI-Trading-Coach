-- baseline 복원 후 남은 row_security=off를 상속하지 않는다.
SET LOCAL row_security = on;
-- 개인 중지와 전역 비상정지를 별도 권위로 둔다. 모든 주문은 기존 전역 잠금 아래 두 상태를 검증한다.
CREATE TABLE public.owner_kill_switch (
 user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE RESTRICT,
 active boolean NOT NULL DEFAULT false,
 generation bigint NOT NULL DEFAULT 1 CHECK(generation>0),
 reason_class text NOT NULL DEFAULT 'INITIAL_STATE' CHECK(reason_class IN ('INITIAL_STATE','USER_MANUAL_STOP','USER_RESUME')),
 changed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.owner_kill_switch OWNER TO flyway;
ALTER TABLE public.owner_kill_switch ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.owner_kill_switch FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_stop_definer ON public.owner_kill_switch TO flyway USING(true) WITH CHECK(true);
REVOKE ALL ON public.owner_kill_switch FROM PUBLIC,decision_app,decision_automation_runtime;
INSERT INTO public.owner_kill_switch(user_id) SELECT user_id FROM public.users;
CREATE TABLE public.owner_kill_switch_events (
 user_id text NOT NULL REFERENCES public.users(user_id), generation bigint NOT NULL,
 active boolean NOT NULL, changed_at timestamptz NOT NULL, request_id text NOT NULL,
 PRIMARY KEY(user_id,generation)
);
ALTER TABLE public.owner_kill_switch_events OWNER TO flyway;
REVOKE ALL ON public.owner_kill_switch_events FROM PUBLIC,decision_app,decision_automation_runtime;
CREATE TABLE public.owner_kill_switch_requests (
 user_id text NOT NULL REFERENCES public.users(user_id), scope_hash text NOT NULL,
 requested_active boolean NOT NULL, result_json jsonb NOT NULL,
 PRIMARY KEY(user_id,scope_hash)
);
ALTER TABLE public.owner_kill_switch_requests OWNER TO flyway;
REVOKE ALL ON public.owner_kill_switch_requests FROM PUBLIC,decision_app,decision_automation_runtime;

CREATE FUNCTION public.owner_stop_initialize() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN INSERT INTO public.owner_kill_switch(user_id) VALUES(NEW.user_id); RETURN NEW; END $f$;
ALTER FUNCTION public.owner_stop_initialize() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.owner_stop_initialize() FROM PUBLIC;
CREATE TRIGGER owner_stop_new_user AFTER INSERT ON public.users FOR EACH ROW EXECUTE FUNCTION public.owner_stop_initialize();

CREATE FUNCTION public.owner_stop_active(p_user_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
 SELECT COALESCE((SELECT active FROM public.owner_kill_switch WHERE user_id=p_user_id),true)
$f$;
ALTER FUNCTION public.owner_stop_active(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.owner_stop_active(text) FROM PUBLIC;

CREATE FUNCTION public.owner_kill_switch_authorized(
 p_capability text,p_user_id text,p_security_version bigint,p_active boolean,p_scope_hash text,p_request_id text
) RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE actor_role text; gate public.owner_kill_switch%ROWTYPE; global_gate public.risk_kill_switch%ROWTYPE;
DECLARE result jsonb; prior public.owner_kill_switch_requests%ROWTYPE;
BEGIN
 IF session_user<>'decision_app' OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' THEN
  RAISE EXCEPTION 'owner stop denied' USING ERRCODE='42501'; END IF;
 SELECT role INTO actor_role FROM public.users WHERE user_id=p_user_id AND status='ACTIVE'
  AND security_version=p_security_version AND role IN ('USER','ADMIN') FOR SHARE;
 IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
  p_capability,p_user_id,p_security_version,actor_role,
  CASE WHEN p_active IS NULL THEN 'READ_OWNER_KILL_SWITCH' ELSE 'CHANGE_OWNER_KILL_SWITCH' END,
  'OWNER_KILL_SWITCH',p_user_id,
  public.actor_capability_payload_hash(p_user_id,p_security_version::text,p_active::text,p_scope_hash,p_request_id)
 ) THEN RAISE EXCEPTION 'owner stop capability denied' USING ERRCODE='42501'; END IF;
 -- 조회는 한 SQL snapshot으로 읽으며 진행 중인 주문의 잠금을 기다리지 않는다.
 IF p_active IS NULL THEN
  SELECT jsonb_build_object('active',o.active,'globalActive',g.active,'effectiveActive',o.active OR g.active,
   'reasonClass',o.reason_class,'changedAt',o.changed_at,'globalGeneration',g.generation)
  INTO result FROM public.owner_kill_switch o CROSS JOIN public.risk_kill_switch g
  WHERE o.user_id=p_user_id AND g.kill_switch_id='GLOBAL';
  IF result IS NULL THEN RAISE EXCEPTION 'owner stop unavailable' USING ERRCODE='P5501'; END IF;
  RETURN result;
 END IF;
 PERFORM set_config('lock_timeout','1000ms',true);
 -- 사용자별 상태를 분리하고 주문과 같은 순서로 전역과 개인 행을 잠근다.
 SELECT * INTO STRICT global_gate FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL' FOR UPDATE;
 SELECT * INTO STRICT gate FROM public.owner_kill_switch WHERE user_id=p_user_id FOR UPDATE;
 IF p_active IS NOT NULL THEN
  IF p_scope_hash !~ '^sha256:[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid idempotency scope' USING ERRCODE='22023'; END IF;
  SELECT * INTO prior FROM public.owner_kill_switch_requests WHERE user_id=p_user_id AND scope_hash=p_scope_hash;
  IF FOUND THEN
   IF prior.requested_active<>p_active THEN RAISE EXCEPTION 'owner stop idempotency conflict' USING ERRCODE='40001'; END IF;
   RETURN prior.result_json;
  END IF;
  IF gate.active<>p_active THEN
   UPDATE public.owner_kill_switch SET active=p_active,generation=generation+1,
    reason_class=CASE WHEN p_active THEN 'USER_MANUAL_STOP' ELSE 'USER_RESUME' END,changed_at=statement_timestamp()
    WHERE user_id=p_user_id AND generation=gate.generation RETURNING * INTO gate;
   INSERT INTO public.owner_kill_switch_events VALUES(p_user_id,gate.generation,gate.active,gate.changed_at,p_request_id);
   IF p_active THEN
    INSERT INTO public.decision_invalidations(invalidation_id,decision_id,evaluation_id,owner_user_id,reason_class,source_generation,invalidated_at,request_id)
    SELECT 'dinv_'||md5('owner:'||d.decision_id||':'||gate.generation::text),d.decision_id,d.evaluation_id,p_user_id,
      'KILL_SWITCH_ACTIVATED',gate.generation,gate.changed_at,p_request_id
    FROM public.decisions d WHERE d.user_id=p_user_id AND d.valid_until>gate.changed_at
      AND NOT EXISTS(SELECT 1 FROM public.orders o WHERE o.decision_id=d.decision_id)
    ON CONFLICT(decision_id,reason_class) DO NOTHING;
   END IF;
  END IF;
 END IF;
 result:=jsonb_build_object('active',gate.active,'globalActive',global_gate.active,
  'effectiveActive',gate.active OR global_gate.active,'reasonClass',gate.reason_class,'changedAt',gate.changed_at,
  'globalGeneration',global_gate.generation);
 IF p_active IS NOT NULL THEN INSERT INTO public.owner_kill_switch_requests VALUES(p_user_id,p_scope_hash,p_active,result); END IF;
 RETURN result;
END $f$;
ALTER FUNCTION public.owner_kill_switch_authorized(text,text,bigint,boolean,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.owner_kill_switch_authorized(text,text,bigint,boolean,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.owner_kill_switch_authorized(text,text,bigint,boolean,text,text) TO decision_app;

CREATE OR REPLACE FUNCTION public.p1_arm_automation_v3(p_user_id text, p_account_id text, p_policy_id text, p_expected_policy_version integer, p_expected_control_version integer, p_scope_hash text, p_request_hash text, p_provider_capability_ready boolean)
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
$function$
;

CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(p_user_id text, p_target_session date)
 RETURNS TABLE(control_configured boolean, certification_valid boolean, release_source_bound boolean, real_team_b_ready boolean, principle_current boolean, kill_switch_inactive boolean, account_baseline_matches boolean, unresolved_state_clear boolean, target_available boolean, current_control_version integer, all_ready boolean)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE observed_digest text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_target_session IS NULL THEN
    RAISE EXCEPTION 'automation readiness scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  control_configured:=control_row.user_id IS NOT NULL
    AND control_row.control_state IN ('DISARMED','ARMED')
    AND control_row.brokerage_mode='KIS_MOCK'
    AND control_row.baseline_account_digest~'^[0-9a-f]{64}$';
  certification_valid:=gate_row.user_id IS NOT NULL AND gate_row.certification_status='VALID'
    AND gate_row.certification_receipt_sha256 IS NOT NULL
    AND gate_row.strategy_eligible_from_session_date IS NOT NULL
    AND p_target_session>=gate_row.strategy_eligible_from_session_date;
  release_source_bound:=gate_row.user_id IS NOT NULL AND gate_row.clean_release_binding
    AND gate_row.release_binding_sha256 IS NOT NULL AND gate_row.source_binding_sha256 IS NOT NULL;
  real_team_b_ready:=gate_row.user_id IS NOT NULL AND gate_row.real_team_b_pointer_active
    AND gate_row.team_b_integrity_receipt_sha256 IS NOT NULL
    AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
    AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1;
  principle_current:=control_row.user_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.principles principle
    WHERE principle.user_id=p_user_id AND principle.principle_id=control_row.principle_id
      AND principle.status='ACTIVE'
  );
  kill_switch_inactive:=COALESCE((
    SELECT NOT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'
  ),false);
  kill_switch_inactive:=kill_switch_inactive AND NOT public.owner_stop_active(p_user_id);
  IF control_configured THEN
    observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=control_row.baseline_account_digest;
  unresolved_state_clear:=control_row.user_id IS NOT NULL
    AND public.p1_automation_open_work_clear_v3(p_user_id,control_row.account_id);
  IF control_row.control_state='ARMED' THEN
    target_available:=EXISTS (
      SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED')
        AND schedule.control_version=control_row.version
    );
  ELSE
    target_available:=NOT EXISTS (
      SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED')
        AND schedule.control_version=control_row.version
    );
  END IF;
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND certification_valid AND release_source_bound
    AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$function$
;

CREATE OR REPLACE FUNCTION public.create_mock_order(requested_payload jsonb, requested_capability_token text)
 RETURNS TABLE(operation_outcome text, projection_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_decision_id text;
  requested_order_id text;
  requested_scope_hash text;
  requested_owner_scope_hash text;
  requested_request_hash text;
  requested_account_id text;
  requested_account_scope_hash text;
  requested_symbol text;
  requested_side text;
  requested_order_type text;
  requested_quantity bigint;
  requested_submitted_price bigint;
  requested_result_json text;
  requested_warnings_accepted boolean;
  requested_observed_generation bigint;
  requested_submitted_at timestamptz;
  requested_created_at timestamptz;
  requested_order_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_gate record;
  stored_decision record;
  stored_request_hash text;
  stored_result_json text;
  reference_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'decisionId',
       'orderId',
       'observedKillSwitchGeneration',
       'idempotencyScopeHash',
       'idempotencyOwnerScopeHash',
       'requestHash',
       'accountId',
       'accountScopeHash',
       'symbol',
       'side',
       'orderType',
       'quantity',
       'submittedPriceKrw',
       'orderIntent',
       'resultCanonicalJson',
       'warningsAccepted',
       'submittedAt',
       'createdAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId',
       'actorRole',
       'securityVersion',
       'requestId',
       'decisionId',
       'orderId',
       'observedKillSwitchGeneration',
       'idempotencyScopeHash',
       'idempotencyOwnerScopeHash',
       'requestHash',
       'accountId',
       'accountScopeHash',
       'symbol',
       'side',
       'orderType',
       'quantity',
       'submittedPriceKrw',
       'orderIntent',
       'resultCanonicalJson',
       'warningsAccepted',
       'submittedAt',
       'createdAt',
       'orderEventId',
       'auditLogId',
       'outboxEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  requested_actor_user_id := requested_payload ->> 'actorUserId';
  requested_actor_role := requested_payload ->> 'actorRole';
  requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
  requested_request_id := requested_payload ->> 'requestId';
  requested_decision_id := requested_payload ->> 'decisionId';
  requested_order_id := requested_payload ->> 'orderId';
  requested_observed_generation := (requested_payload ->> 'observedKillSwitchGeneration')::bigint;
  requested_scope_hash := requested_payload ->> 'idempotencyScopeHash';
  requested_owner_scope_hash := requested_payload ->> 'idempotencyOwnerScopeHash';
  requested_request_hash := requested_payload ->> 'requestHash';
  requested_account_id := requested_payload ->> 'accountId';
  requested_account_scope_hash := requested_payload ->> 'accountScopeHash';
  requested_symbol := requested_payload ->> 'symbol';
  requested_side := requested_payload ->> 'side';
  requested_order_type := requested_payload ->> 'orderType';
  requested_quantity := (requested_payload ->> 'quantity')::bigint;
  requested_submitted_price :=
    CASE
      WHEN jsonb_typeof(requested_payload -> 'submittedPriceKrw') = 'null' THEN NULL
      ELSE (requested_payload ->> 'submittedPriceKrw')::bigint
    END;
  requested_result_json := requested_payload ->> 'resultCanonicalJson';
  requested_warnings_accepted := (requested_payload ->> 'warningsAccepted')::boolean;
  requested_submitted_at := (requested_payload ->> 'submittedAt')::timestamptz;
  requested_created_at := (requested_payload ->> 'createdAt')::timestamptz;
  requested_order_event_id := requested_payload ->> 'orderEventId';
  requested_audit_log_id := requested_payload ->> 'auditLogId';
  requested_outbox_event_id := requested_payload ->> 'outboxEventId';

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> requested_actor_role
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;

  -- direct table 권한이 없는 SECURITY DEFINER 경계 안에서만 Decision FORCE RLS owner를 설정한다.
  PERFORM set_config('app.actor_user_id', requested_actor_user_id, true);

  PERFORM pg_advisory_xact_lock(
    hashtextextended('mock-order:idempotency:' || requested_scope_hash, 3101)
  );
  PERFORM pg_advisory_xact_lock(
    hashtextextended('mock-order:decision:' || requested_decision_id, 3101)
  );

  SELECT gate.active, gate.generation
  INTO stored_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id = 'GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;
  IF public.owner_stop_active(requested_actor_user_id) OR stored_gate.active
     OR stored_gate.generation <> requested_observed_generation THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT stored.request_hash, stored.result_canonical_json
  INTO stored_request_hash, stored_result_json
  FROM public.orders stored
  WHERE stored.idempotency_scope_hash = requested_scope_hash
    AND stored.idempotency_owner_scope_hash = requested_owner_scope_hash
  LIMIT 1;
  IF FOUND THEN
    IF stored_request_hash = requested_request_hash THEN
      RETURN QUERY SELECT 'REPLAY'::text, stored_result_json;
    ELSE
      RETURN QUERY SELECT 'IDEMPOTENCY_CONFLICT'::text, NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT
    decision.evaluation_id,
    decision.portfolio_source,
    decision.outcome,
    decision.can_submit_order,
    decision.enforcement_action,
    decision.valid_until,
    artifact.snapshot_artifact_canonical_json,
    artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}' AS owner_scope_hash,
    EXISTS (
      SELECT 1
      FROM public.decision_invalidations invalidation
      WHERE invalidation.decision_id = decision.decision_id
    ) AS invalidated,
    EXISTS (
      SELECT 1
      FROM public.orders consumed
      WHERE consumed.decision_id = decision.decision_id
    ) AS consumed
  INTO stored_decision
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
   AND artifact.evaluation_id = decision.evaluation_id
  WHERE decision.user_id = requested_actor_user_id
    AND decision.decision_id = requested_decision_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'DECISION_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.invalidated
     OR NOT stored_decision.can_submit_order
     OR stored_decision.outcome NOT IN ('ALLOW', 'WARN')
     OR (
       stored_decision.enforcement_action <> 'NONE'
       AND NOT requested_warnings_accepted
     ) THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.consumed THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.portfolio_source <> 'KIS_MOCK'
     OR stored_decision.owner_scope_hash <> requested_account_scope_hash
     OR requested_account_id <> 'acct_' || left(requested_account_scope_hash, 32)
     OR stored_decision.snapshot_artifact_canonical_json::jsonb -> 'orderIntent'
       <> requested_payload -> 'orderIntent' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;
  -- app service clock은 요청 metadata일 뿐이다. 주문 소비 직전 DB live clock으로 expiry를 최종 판정한다.
  IF NOT stored_decision.valid_until > pg_catalog.clock_timestamp() THEN
    RETURN QUERY SELECT 'DECISION_EXPIRED'::text, NULL::text;
    RETURN;
  END IF;

  INSERT INTO public.orders (
    order_id, user_id, account_id, account_scope_hash, decision_id,
    decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
    idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
    quantity, submitted_price_krw, status, order_intent_json,
    result_canonical_json, acknowledged_by, acknowledged_at, submitted_at,
    created_at, updated_at
  )
  VALUES (
    requested_order_id, requested_actor_user_id, requested_account_id,
    requested_account_scope_hash, requested_decision_id,
    stored_decision.evaluation_id, 'KIS_MOCK', requested_scope_hash,
    requested_owner_scope_hash, requested_request_hash, requested_symbol,
    requested_side, requested_order_type, requested_quantity,
    requested_submitted_price, 'SUBMITTED', requested_payload -> 'orderIntent',
    requested_result_json, requested_actor_user_id, requested_created_at,
    requested_submitted_at, requested_created_at, requested_created_at
  );

  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id, requested_order_id, 'MOCK_ORDER_SUBMITTED',
    'SUBMITTED',
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'SUBMITTED'
    ),
    requested_created_at,
    1
  );

  reference_payload :=
    jsonb_build_object(
      'orderId', requested_order_id,
      'decisionId', requested_decision_id,
      'evaluationId', stored_decision.evaluation_id,
      'brokerageMode', 'KIS_MOCK',
      'status', 'SUBMITTED',
      'idempotencyScopeHash', requested_scope_hash
    );
  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    'MOCK_ORDER_SUBMITTED', 'ORDER', requested_order_id,
    requested_request_id, reference_payload, requested_created_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id, 'brokerage.mock-order-submitted.v1', 'ORDER',
    requested_order_id, requested_order_id, reference_payload, '1.0.0',
    'PENDING', 0, requested_created_at, requested_created_at
  );

  RETURN QUERY SELECT 'CREATED'::text, requested_result_json;
EXCEPTION
  WHEN unique_violation THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.create_paper_order(requested_payload jsonb, requested_capability_token text)
 RETURNS TABLE(operation_outcome text, projection_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
  requested_actor_user_id text;
  requested_actor_role text;
  requested_security_version bigint;
  requested_request_id text;
  requested_decision_id text;
  requested_order_id text;
  requested_scope_hash text;
  requested_owner_scope_hash text;
  requested_request_hash text;
  requested_account_id text;
  requested_account_scope_hash text;
  requested_quote_observation_id text;
  requested_symbol text;
  requested_side text;
  requested_order_type text;
  requested_quantity bigint;
  requested_submitted_price bigint;
  requested_result_json text;
  requested_warnings_accepted boolean;
  requested_observed_generation bigint;
  requested_status text;
  requested_fill_price bigint;
  requested_fill_amount bigint;
  requested_price_basis text;
  requested_slippage_bps integer;
  requested_fee_model text;
  requested_quote_observed_at timestamptz;
  requested_price_max_age_seconds integer;
  requested_submitted_at timestamptz;
  requested_created_at timestamptz;
  requested_order_event_id text;
  requested_paper_event_id text;
  requested_audit_log_id text;
  requested_outbox_event_id text;
  stored_actor record;
  stored_gate record;
  stored_decision record;
  stored_account record;
  stored_position record;
  stored_quote record;
  stored_request_hash text;
  stored_result_json text;
  base_price bigint;
  computed_fill_price numeric;
  computed_fill_amount numeric;
  before_cash bigint;
  after_cash bigint;
  before_quantity bigint;
  after_quantity bigint;
  before_average_price bigint;
  after_average_price bigint;
  before_market_value bigint;
  after_market_value bigint;
  next_paper_event_seq integer;
  order_event_type text;
  reference_payload jsonb;
  ledger_payload jsonb;
BEGIN
  PERFORM public.assert_brokerage_database_capability(requested_capability_token);
  IF requested_payload IS NULL
     OR jsonb_typeof(requested_payload) <> 'object'
     OR NOT requested_payload ?& ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'decisionId', 'orderId', 'observedKillSwitchGeneration',
       'idempotencyScopeHash', 'idempotencyOwnerScopeHash', 'requestHash',
       'accountId', 'accountScopeHash', 'quoteObservationId', 'symbol',
       'side', 'orderType', 'quantity', 'submittedPriceKrw', 'orderIntent',
       'resultCanonicalJson', 'warningsAccepted', 'status', 'fillPriceKrw',
       'fillAmountKrw', 'priceBasis', 'slippageBps', 'feeModel',
       'quoteObservedAt', 'priceMaxAgeSeconds', 'submittedAt', 'createdAt',
       'orderEventId', 'paperEventId', 'auditLogId', 'outboxEventId'
     ]
     OR requested_payload - ARRAY[
       'actorUserId', 'actorRole', 'securityVersion', 'requestId',
       'decisionId', 'orderId', 'observedKillSwitchGeneration',
       'idempotencyScopeHash', 'idempotencyOwnerScopeHash', 'requestHash',
       'accountId', 'accountScopeHash', 'quoteObservationId', 'symbol',
       'side', 'orderType', 'quantity', 'submittedPriceKrw', 'orderIntent',
       'resultCanonicalJson', 'warningsAccepted', 'status', 'fillPriceKrw',
       'fillAmountKrw', 'priceBasis', 'slippageBps', 'feeModel',
       'quoteObservedAt', 'priceMaxAgeSeconds', 'submittedAt', 'createdAt',
       'orderEventId', 'paperEventId', 'auditLogId', 'outboxEventId'
     ] <> '{}'::jsonb THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  BEGIN
    requested_actor_user_id := requested_payload ->> 'actorUserId';
    requested_actor_role := requested_payload ->> 'actorRole';
    requested_security_version := (requested_payload ->> 'securityVersion')::bigint;
    requested_request_id := requested_payload ->> 'requestId';
    requested_decision_id := requested_payload ->> 'decisionId';
    requested_order_id := requested_payload ->> 'orderId';
    requested_observed_generation := (requested_payload ->> 'observedKillSwitchGeneration')::bigint;
    requested_scope_hash := requested_payload ->> 'idempotencyScopeHash';
    requested_owner_scope_hash := requested_payload ->> 'idempotencyOwnerScopeHash';
    requested_request_hash := requested_payload ->> 'requestHash';
    requested_account_id := requested_payload ->> 'accountId';
    requested_account_scope_hash := requested_payload ->> 'accountScopeHash';
    requested_quote_observation_id := requested_payload ->> 'quoteObservationId';
    requested_symbol := requested_payload ->> 'symbol';
    requested_side := requested_payload ->> 'side';
    requested_order_type := requested_payload ->> 'orderType';
    requested_quantity := (requested_payload ->> 'quantity')::bigint;
    requested_submitted_price :=
      CASE WHEN jsonb_typeof(requested_payload -> 'submittedPriceKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'submittedPriceKrw')::bigint END;
    requested_result_json := requested_payload ->> 'resultCanonicalJson';
    requested_warnings_accepted := (requested_payload ->> 'warningsAccepted')::boolean;
    requested_status := requested_payload ->> 'status';
    requested_fill_price :=
      CASE WHEN jsonb_typeof(requested_payload -> 'fillPriceKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'fillPriceKrw')::bigint END;
    requested_fill_amount :=
      CASE WHEN jsonb_typeof(requested_payload -> 'fillAmountKrw') = 'null'
        THEN NULL ELSE (requested_payload ->> 'fillAmountKrw')::bigint END;
    requested_price_basis :=
      CASE WHEN jsonb_typeof(requested_payload -> 'priceBasis') = 'null'
        THEN NULL ELSE requested_payload ->> 'priceBasis' END;
    requested_slippage_bps := (requested_payload ->> 'slippageBps')::integer;
    requested_fee_model :=
      CASE WHEN jsonb_typeof(requested_payload -> 'feeModel') = 'null'
        THEN NULL ELSE requested_payload ->> 'feeModel' END;
    requested_quote_observed_at := (requested_payload ->> 'quoteObservedAt')::timestamptz;
    requested_price_max_age_seconds := (requested_payload ->> 'priceMaxAgeSeconds')::integer;
    requested_submitted_at := (requested_payload ->> 'submittedAt')::timestamptz;
    requested_created_at := (requested_payload ->> 'createdAt')::timestamptz;
    requested_order_event_id := requested_payload ->> 'orderEventId';
    requested_paper_event_id :=
      CASE WHEN jsonb_typeof(requested_payload -> 'paperEventId') = 'null'
        THEN NULL ELSE requested_payload ->> 'paperEventId' END;
    requested_audit_log_id := requested_payload ->> 'auditLogId';
    requested_outbox_event_id := requested_payload ->> 'outboxEventId';
  EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range OR datetime_field_overflow THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
  END;

  IF requested_order_id !~ '^ord_paper_[0-9a-f]{32}$'
     OR requested_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_owner_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_request_hash !~ '^[0-9a-f]{64}$'
     OR requested_account_id !~ '^acct_[0-9a-f]{32}$'
     OR requested_account_scope_hash !~ '^[0-9a-f]{64}$'
     OR requested_side NOT IN ('BUY', 'SELL')
     OR requested_order_type NOT IN ('MARKET', 'LIMIT')
     OR requested_quantity <= 0
     OR requested_status NOT IN ('ACCEPTED', 'FILLED')
     OR requested_price_max_age_seconds NOT BETWEEN 1 AND 300
     OR jsonb_typeof(requested_payload -> 'orderIntent') <> 'object'
     OR jsonb_typeof(requested_result_json::jsonb) <> 'object' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;
  IF NOT FOUND
     OR stored_actor.status <> 'ACTIVE'
     OR stored_actor.role <> requested_actor_role
     OR stored_actor.security_version <> requested_security_version THEN
    RETURN QUERY SELECT 'ACTOR_UNAUTHORIZED'::text, NULL::text;
    RETURN;
  END IF;

  PERFORM set_config('app.actor_user_id', requested_actor_user_id, true);
  PERFORM pg_advisory_xact_lock(
    hashtextextended('paper-order:idempotency:' || requested_scope_hash, 3201)
  );
  PERFORM pg_advisory_xact_lock(
    hashtextextended('paper-order:decision:' || requested_decision_id, 3201)
  );

  SELECT gate.active, gate.generation
  INTO stored_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id = 'GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;
  IF public.owner_stop_active(requested_actor_user_id) OR stored_gate.active OR stored_gate.generation <> requested_observed_generation THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT stored.request_hash, stored.result_canonical_json
  INTO stored_request_hash, stored_result_json
  FROM public.orders stored
  WHERE stored.idempotency_scope_hash = requested_scope_hash
    AND stored.idempotency_owner_scope_hash = requested_owner_scope_hash
  LIMIT 1;
  IF FOUND THEN
    IF stored_request_hash = requested_request_hash THEN
      RETURN QUERY SELECT 'REPLAY'::text, stored_result_json;
    ELSE
      RETURN QUERY SELECT 'IDEMPOTENCY_CONFLICT'::text, NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT
    decision.evaluation_id,
    decision.portfolio_source,
    decision.outcome,
    decision.can_submit_order,
    decision.enforcement_action,
    decision.valid_until,
    artifact.snapshot_artifact_canonical_json,
    artifact.snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}' AS owner_scope_hash,
    EXISTS (
      SELECT 1
      FROM public.decision_invalidations invalidation
      WHERE invalidation.decision_id = decision.decision_id
    ) AS invalidated,
    EXISTS (
      SELECT 1
      FROM public.orders consumed
      WHERE consumed.decision_id = decision.decision_id
    ) AS consumed
  INTO stored_decision
  FROM public.decisions decision
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id = decision.decision_id
   AND artifact.evaluation_id = decision.evaluation_id
  WHERE decision.user_id = requested_actor_user_id
    AND decision.decision_id = requested_decision_id;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'DECISION_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.invalidated
     OR NOT stored_decision.can_submit_order
     OR stored_decision.outcome NOT IN ('ALLOW', 'WARN')
     OR (
       stored_decision.enforcement_action <> 'NONE'
       AND NOT requested_warnings_accepted
     ) THEN
    RETURN QUERY SELECT 'RISK_BLOCKED'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.consumed THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_decision.portfolio_source <> 'INTERNAL_PAPER'
     OR stored_decision.owner_scope_hash <> requested_account_scope_hash
     OR stored_decision.snapshot_artifact_canonical_json::jsonb -> 'orderIntent'
       <> requested_payload -> 'orderIntent' THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;
  IF NOT stored_decision.valid_until > pg_catalog.clock_timestamp() THEN
    RETURN QUERY SELECT 'DECISION_EXPIRED'::text, NULL::text;
    RETURN;
  END IF;

  SELECT
    account.account_id,
    account.user_id,
    account.owner_scope_hash,
    account.status,
    account.cash_balance,
    account.updated_at
  INTO stored_account
  FROM public.paper_accounts account
  WHERE account.account_id = requested_account_id
    AND account.user_id = requested_actor_user_id
    AND account.owner_scope_hash = requested_account_scope_hash
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'DECISION_NOT_FOUND'::text, NULL::text;
    RETURN;
  END IF;
  IF stored_account.status <> 'ACTIVE' THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;

  SELECT
    quote.observation_id,
    quote.symbol,
    quote.price_krw,
    quote.previous_close_krw,
    quote.completeness,
    quote.observed_at
  INTO stored_quote
  FROM public.market_quote_observations quote
  WHERE quote.observation_id = requested_quote_observation_id
    AND quote.symbol = requested_symbol
    AND quote.source = 'KIS_MOCK';
  IF NOT FOUND
     OR stored_quote.completeness <> 'COMPLETE'
     OR stored_quote.observed_at <> requested_quote_observed_at THEN
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;
  IF requested_created_at > stored_quote.observed_at
       + make_interval(secs => requested_price_max_age_seconds)
     OR stored_quote.observed_at > requested_created_at THEN
    RETURN QUERY SELECT 'DATA_STALE'::text, NULL::text;
    RETURN;
  END IF;

  IF stored_quote.price_krw IS NOT NULL THEN
    base_price := stored_quote.price_krw;
    IF requested_price_basis <> 'LAST_QUOTE' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSIF stored_quote.previous_close_krw IS NOT NULL THEN
    base_price := stored_quote.previous_close_krw;
    IF requested_price_basis <> 'PREVIOUS_CLOSE' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSE
    RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
    RETURN;
  END IF;

  IF requested_status = 'ACCEPTED' THEN
    IF requested_order_type <> 'LIMIT'
       OR requested_fill_price IS NOT NULL
       OR requested_fill_amount IS NOT NULL
       OR requested_slippage_bps <> 0
       OR requested_fee_model IS NOT NULL
       OR requested_paper_event_id IS NOT NULL
       OR (
         requested_side = 'BUY'
         AND base_price <= requested_submitted_price
       )
       OR (
         requested_side = 'SELL'
         AND base_price >= requested_submitted_price
       ) THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  ELSE
    IF requested_fill_price IS NULL
       OR requested_fill_amount IS NULL
       OR requested_paper_event_id IS NULL
       OR requested_fee_model <> 'NONE_V1' THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
    IF requested_order_type = 'MARKET' THEN
      IF requested_slippage_bps NOT BETWEEN 0 AND 100 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      IF requested_side = 'BUY' THEN
        computed_fill_price :=
          ceil((base_price::numeric * (10000 + requested_slippage_bps)) / 10000);
      ELSE
        computed_fill_price :=
          floor((base_price::numeric * (10000 - requested_slippage_bps)) / 10000);
      END IF;
    ELSE
      IF requested_slippage_bps <> 0
         OR requested_submitted_price IS NULL
         OR (
           requested_side = 'BUY'
           AND base_price > requested_submitted_price
         )
         OR (
           requested_side = 'SELL'
           AND base_price < requested_submitted_price
         ) THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      computed_fill_price :=
        CASE
          WHEN requested_side = 'BUY'
            THEN least(base_price, requested_submitted_price)
          ELSE greatest(base_price, requested_submitted_price)
        END;
    END IF;
    IF computed_fill_price <= 0 THEN
      RETURN QUERY SELECT 'BROKERAGE_UNAVAILABLE'::text, NULL::text;
      RETURN;
    END IF;
    computed_fill_amount := requested_quantity::numeric * computed_fill_price;
    IF computed_fill_price > 9223372036854775807
       OR computed_fill_amount > 9223372036854775807
       OR requested_fill_price <> computed_fill_price::bigint
       OR requested_fill_amount <> computed_fill_amount::bigint THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
  END IF;

  -- canonical response도 DB가 다시 계산한 mode/status/fill과 일치해야 replay가 신뢰 가능하다.
  IF requested_result_json::jsonb ->> 'orderId' <> requested_order_id
     OR requested_result_json::jsonb ->> 'accountId' <> requested_account_id
     OR requested_result_json::jsonb ->> 'brokerageMode' <> 'INTERNAL_PAPER'
     OR requested_result_json::jsonb ->> 'status' <> requested_status THEN
    RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
    RETURN;
  END IF;

  IF requested_status = 'FILLED' THEN
    SELECT
      position.position_id,
      position.quantity,
      position.average_price,
      position.market_value
    INTO stored_position
    FROM public.paper_positions position
    WHERE position.account_id = requested_account_id
      AND position.symbol = requested_symbol
    FOR UPDATE;
    before_cash := stored_account.cash_balance;
    before_quantity := COALESCE(stored_position.quantity, 0);
    before_average_price := COALESCE(stored_position.average_price, 0);
    before_market_value := COALESCE(stored_position.market_value, 0);
    IF requested_side = 'BUY' THEN
      IF before_cash < requested_fill_amount THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_cash := before_cash - requested_fill_amount;
      after_quantity := before_quantity + requested_quantity;
      IF after_quantity < before_quantity THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      computed_fill_amount :=
        before_quantity::numeric * before_average_price
        + requested_quantity::numeric * requested_fill_price;
      IF computed_fill_amount > 9223372036854775807 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_average_price := floor(computed_fill_amount / after_quantity)::bigint;
    ELSE
      IF before_quantity < requested_quantity THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      IF before_cash::numeric + requested_fill_amount > 9223372036854775807 THEN
        RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
        RETURN;
      END IF;
      after_cash := before_cash + requested_fill_amount;
      after_quantity := before_quantity - requested_quantity;
      after_average_price :=
        CASE WHEN after_quantity = 0 THEN 0 ELSE before_average_price END;
    END IF;
    computed_fill_amount := after_quantity::numeric * requested_fill_price;
    IF computed_fill_amount > 9223372036854775807 THEN
      RETURN QUERY SELECT 'VALIDATION_ERROR'::text, NULL::text;
      RETURN;
    END IF;
    after_market_value := computed_fill_amount::bigint;

    UPDATE public.paper_accounts
    SET cash_balance = after_cash,
        updated_at = requested_created_at
    WHERE account_id = requested_account_id;
    IF stored_position.position_id IS NULL THEN
      INSERT INTO public.paper_positions (
        position_id, account_id, symbol, quantity, average_price,
        market_value, updated_at
      )
      VALUES (
        'ppos_' || substr(encode(sha256(convert_to(
          requested_account_id || ':' || requested_symbol, 'UTF8'
        )), 'hex'), 1, 32),
        requested_account_id,
        requested_symbol,
        after_quantity,
        after_average_price,
        after_market_value,
        requested_created_at
      );
    ELSE
      UPDATE public.paper_positions
      SET quantity = after_quantity,
          average_price = after_average_price,
          market_value = after_market_value,
          updated_at = requested_created_at
      WHERE position_id = stored_position.position_id;
    END IF;
  END IF;

  INSERT INTO public.orders (
    order_id, user_id, account_id, account_scope_hash, decision_id,
    decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
    idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
    quantity, submitted_price_krw, status, order_intent_json,
    result_canonical_json, acknowledged_by, acknowledged_at, submitted_at,
    created_at, updated_at
  )
  VALUES (
    requested_order_id, requested_actor_user_id, requested_account_id,
    requested_account_scope_hash, requested_decision_id,
    stored_decision.evaluation_id, 'INTERNAL_PAPER', requested_scope_hash,
    requested_owner_scope_hash, requested_request_hash, requested_symbol,
    requested_side, requested_order_type, requested_quantity,
    requested_submitted_price, requested_status, requested_payload -> 'orderIntent',
    requested_result_json, requested_actor_user_id, requested_created_at,
    requested_submitted_at, requested_created_at, requested_created_at
  );

  order_event_type :=
    CASE WHEN requested_status = 'FILLED'
      THEN 'PAPER_ORDER_FILLED' ELSE 'PAPER_ORDER_ACCEPTED' END;
  INSERT INTO public.order_events (
    order_event_id, order_id, event_type, event_status, payload_json,
    created_at, event_seq
  )
  VALUES (
    requested_order_event_id,
    requested_order_id,
    order_event_type,
    requested_status,
    jsonb_build_object(
      'orderId', requested_order_id,
      'brokerageMode', 'INTERNAL_PAPER',
      'status', requested_status
    ),
    requested_created_at,
    1
  );

  IF requested_status = 'FILLED' THEN
    SELECT COALESCE(max(event.event_seq), 0) + 1
    INTO next_paper_event_seq
    FROM public.paper_order_events event
    WHERE event.account_id = requested_account_id;
    ledger_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'symbol', requested_symbol,
        'side', requested_side,
        'fillQuantity', requested_quantity,
        'fillPriceKrw', requested_fill_price,
        'fillAmountKrw', requested_fill_amount,
        'priceBasis', requested_price_basis,
        'slippageBps', requested_slippage_bps,
        'feeModel', requested_fee_model,
        'observedAt', requested_quote_observed_at::text,
        'beforeCashKrw', before_cash,
        'afterCashKrw', after_cash,
        'beforeQuantity', before_quantity,
        'afterQuantity', after_quantity,
        'beforeAveragePriceKrw', before_average_price,
        'afterAveragePriceKrw', after_average_price,
        'beforeMarketValueKrw', before_market_value,
        'afterMarketValueKrw', after_market_value
      );
    INSERT INTO public.paper_order_events (
      paper_order_event_id, account_id, order_id, event_type, payload_json,
      created_at, event_seq
    )
    VALUES (
      requested_paper_event_id, requested_account_id, requested_order_id,
      'PAPER_ORDER_FILLED', ledger_payload, requested_created_at,
      next_paper_event_seq
    );
    reference_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'decisionId', requested_decision_id,
        'evaluationId', stored_decision.evaluation_id,
        'brokerageMode', 'INTERNAL_PAPER',
        'status', 'FILLED',
        'idempotencyScopeHash', requested_scope_hash,
        'fillPriceKrw', requested_fill_price,
        'fillQuantity', requested_quantity,
        'priceBasis', requested_price_basis,
        'slippageBps', requested_slippage_bps,
        'feeModel', requested_fee_model
      );
  ELSE
    reference_payload :=
      jsonb_build_object(
        'orderId', requested_order_id,
        'decisionId', requested_decision_id,
        'evaluationId', stored_decision.evaluation_id,
        'brokerageMode', 'INTERNAL_PAPER',
        'status', 'ACCEPTED',
        'idempotencyScopeHash', requested_scope_hash
      );
  END IF;

  INSERT INTO public.audit_logs (
    audit_log_id, user_id, actor_role, action, target_type, target_id,
    request_id, payload_json, created_at
  )
  VALUES (
    requested_audit_log_id, requested_actor_user_id, requested_actor_role,
    CASE WHEN requested_status = 'FILLED'
      THEN 'PAPER_ORDER_FILLED' ELSE 'PAPER_ORDER_ACCEPTED' END,
    'ORDER', requested_order_id, requested_request_id, reference_payload,
    requested_created_at
  );
  INSERT INTO public.event_outbox (
    event_id, event_type, aggregate_type, aggregate_id, partition_key,
    payload_json, schema_version, status, retry_count, created_at, updated_at
  )
  VALUES (
    requested_outbox_event_id,
    CASE WHEN requested_status = 'FILLED'
      THEN 'brokerage.paper-order-filled.v1'
      ELSE 'brokerage.paper-order-accepted.v1' END,
    'ORDER', requested_order_id, requested_order_id, reference_payload,
    '1.0.0', 'PENDING', 0, requested_created_at, requested_created_at
  );
  RETURN QUERY SELECT 'CREATED'::text, requested_result_json;
EXCEPTION
  WHEN unique_violation THEN
    RETURN QUERY SELECT 'DECISION_CONFLICT'::text, NULL::text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.transition_kill_switch_authorized(p_capability text, p_actor_user_id text, p_actor_security_version bigint, p_requested_active boolean, p_observed_generation bigint, p_request_id text)
 RETURNS TABLE(active boolean, reason_class text, changed_at timestamp with time zone, changed boolean, previous_active boolean, generation bigint, invalidated_decision_count integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE
  actor_role text;
  current_gate public.risk_kill_switch%ROWTYPE;
  next_reason text;
  transition_time timestamptz;
  invalidated_count integer := 0;
BEGIN
  IF session_user <> 'decision_app'
     OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_observed_generation <= 0 THEN
    RAISE EXCEPTION 'kill switch capability request denied' USING ERRCODE='42501';
  END IF;
  SELECT actor.role INTO actor_role
  FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
    AND actor.security_version=p_actor_security_version AND actor.role='ADMIN'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch current actor denied' USING ERRCODE='42501';
  END IF;
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_actor_security_version,
    'ADMIN',
    'TRANSITION_KILL_SWITCH','KILL_SWITCH','GLOBAL',
    public.actor_capability_payload_hash(
      p_actor_user_id,p_actor_security_version::text,p_requested_active::text,
      p_observed_generation::text,p_request_id
    )
  ) THEN
    RAISE EXCEPTION 'kill switch actor capability denied' USING ERRCODE='42501';
  END IF;

  SELECT * INTO current_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE='P5501';
  END IF;
  IF current_gate.generation<>p_observed_generation THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE='40001';
  END IF;
  IF current_gate.active=p_requested_active THEN
    RETURN QUERY SELECT current_gate.active,current_gate.reason_class,current_gate.changed_at,false,
      current_gate.active,current_gate.generation,0;
    RETURN;
  END IF;

  next_reason:=CASE
    WHEN NOT p_requested_active THEN 'ADMIN_RESUME'
    WHEN actor_role='ADMIN' THEN 'OPERATOR_MANUAL_STOP'
    ELSE 'USER_MANUAL_STOP'
  END;
  transition_time:=greatest(statement_timestamp(),current_gate.changed_at);
  UPDATE public.risk_kill_switch gate
  SET active=p_requested_active,
      reason_class=next_reason,
      generation=current_gate.generation+1,
      changed_by=p_actor_user_id,
      changed_by_role=actor_role,
      changed_at=transition_time,
      request_id=p_request_id
  WHERE gate.kill_switch_id='GLOBAL' AND gate.generation=p_observed_generation;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE='40001';
  END IF;
  IF p_requested_active THEN
    invalidated_count:=public.invalidate_unused_decisions_for_kill_switch(
      current_gate.generation+1,transition_time,p_request_id
    );
  END IF;

  INSERT INTO public.risk_kill_switch_transitions(
    transition_id,generation,previous_active,next_active,reason_class,changed_by,
    changed_by_role,changed_at,request_id,invalidated_decision_count
  ) VALUES (
    'kst_'||replace(gen_random_uuid()::text,'-',''),current_gate.generation+1,
    current_gate.active,p_requested_active,next_reason,p_actor_user_id,actor_role,
    transition_time,p_request_id,invalidated_count
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),p_actor_user_id,actor_role,
    'KILL_SWITCH_CHANGED','KILL_SWITCH','GLOBAL',p_request_id,
    jsonb_build_object(
      'generation',current_gate.generation+1,
      'previousActive',current_gate.active,
      'nextActive',p_requested_active,
      'reasonClass',next_reason,
      'changedBy',p_actor_user_id,
      'changedByRole',actor_role,
      'correlationId',p_request_id,
      'invalidatedDecisionCount',invalidated_count
    ),transition_time
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'kill-switch.changed','KILL_SWITCH',
    'GLOBAL','GLOBAL',jsonb_build_object('active',p_requested_active,'changedAt',transition_time::text),
    '1.0.0','PENDING',0,transition_time,transition_time
  );
  RETURN QUERY SELECT p_requested_active,next_reason,transition_time,true,current_gate.active,
    current_gate.generation+1,invalidated_count;
END
$function$
;

CREATE OR REPLACE FUNCTION public.register_actor_identity_handle_v1(p_session_handle text, p_operation text, p_target_kind text, p_target_id text, p_payload_hash text, p_role_policy text, p_ttl_seconds integer)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE actor record;
DECLARE raw_handle text;
DECLARE now_at timestamptz:=statement_timestamp();
BEGIN
  IF p_operation='TRANSITION_KILL_SWITCH' AND p_role_policy<>'ADMIN_ONLY' THEN
    RAISE EXCEPTION 'global stop requires admin capability' USING ERRCODE='42501';
  END IF;
  IF session_user<>'decision_auth'
     OR p_session_handle!~'^sid1_[0-9a-f]{64}$'
     OR p_operation!~'^[A-Z][A-Z0-9_]{2,63}$'
     OR p_target_kind!~'^[A-Z][A-Z0-9_]{2,31}$'
     OR char_length(p_target_id) NOT BETWEEN 1 AND 160
     OR p_payload_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_role_policy NOT IN ('OWNER','ADMIN_ONLY')
     OR p_ttl_seconds NOT BETWEEN 1 AND 15 THEN
    RAISE EXCEPTION 'actor identity handle registration denied' USING ERRCODE='42501';
  END IF;
  SELECT current_actor.user_id,current_actor.role,current_actor.status,current_actor.security_version
  INTO actor
  FROM public.actor_auth_session session
  JOIN public.users current_actor ON current_actor.user_id=session.actor_user_id
  WHERE session.session_hash='sha256:'||encode(public.digest(p_session_handle,'sha256'),'hex')
    AND session.revoked_at IS NULL AND session.expires_at>now_at
    AND current_actor.role=session.actor_role
    AND current_actor.security_version=session.actor_security_version
  FOR SHARE OF current_actor;
  IF NOT FOUND OR actor.status<>'ACTIVE'
     OR actor.role NOT IN ('USER','ADMIN')
     OR (p_role_policy='ADMIN_ONLY' AND actor.role<>'ADMIN') THEN
    RAISE EXCEPTION 'actor identity handle registration denied' USING ERRCODE='42501';
  END IF;
  DELETE FROM public.actor_identity_handle expired
  WHERE expired.expires_at<=now_at OR expired.consumed_at IS NOT NULL;
  raw_handle:='idh1_'||encode(public.gen_random_bytes(32),'hex');
  INSERT INTO public.actor_identity_handle(
    handle_hash,actor_user_id,actor_role,actor_security_version,
    operation,target_kind,target_id,payload_hash,role_policy,issued_at,expires_at
  ) VALUES (
    'sha256:'||encode(public.digest(raw_handle,'sha256'),'hex'),
    actor.user_id,actor.role,actor.security_version,
    p_operation,p_target_kind,p_target_id,p_payload_hash,p_role_policy,
    now_at,now_at+make_interval(secs=>p_ttl_seconds)
  );
  RETURN raw_handle;
END
$function$
;

CREATE OR REPLACE FUNCTION public.persist_decision_bundle_authorized(p_capability text, p_bundle jsonb)
 RETURNS TABLE(outcome text, result_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE
  actor_user_id text;
  actor_role text;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user <> 'decision_app'
     OR jsonb_typeof(p_bundle) <> 'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value = 'null'::jsonb)
     OR p_bundle - ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] <> '{}'::jsonb
     OR NOT p_bundle ?& ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE = '22023';
  END IF;

  BEGIN
    actor_user_id := p_bundle->>'actorUserId';
    actor_role := p_bundle->>'actorRole';
    created_at := (p_bundle->>'createdAt')::timestamptz;
    result_text := p_bundle->>'resultCanonicalJson';
    snapshot_text := p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE = '22023';
  END;

  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions') <> 'object'
     OR jsonb_typeof(p_bundle->'violations') <> 'array'
     OR jsonb_array_length(p_bundle->'violations') > 14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb) <> 'object'
     OR jsonb_typeof(snapshot_text::jsonb) <> 'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE = '22023';
  END IF;

  IF NOT public.consume_current_actor_capability(p_capability,actor_user_id) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  ) THEN
    RAISE EXCEPTION 'decision current actor denied' USING ERRCODE = '42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE = 'P5501';
  END IF;
  IF gate_active OR public.owner_stop_active(actor_user_id) THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND item.current_version=(p_bundle->>'principleVersion')::integer
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE = '40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal := ordinal + 1;
    IF jsonb_typeof(violation)<>'object'
       OR violation - ARRAY['ruleId','severity','observedValue','thresholdValue','message'] <> '{}'::jsonb
       OR NOT violation ?& ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal := array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );

  reference_payload := jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.persist_decision_bundle_authorized_v2(p_capability text, p_bundle_text text)
 RETURNS TABLE(outcome text, result_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE
  p_bundle jsonb;
  actor_user_id text;
  actor_role text;
  actor_security_version bigint;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user<>'decision_app' OR octet_length(p_bundle_text) NOT BETWEEN 2 AND 4194304 THEN
    RAISE EXCEPTION 'decision bundle request denied' USING ERRCODE='42501';
  END IF;
  BEGIN
    p_bundle:=p_bundle_text::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF jsonb_typeof(p_bundle)<>'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value='null'::jsonb)
     OR p_bundle-ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ]<>'{}'::jsonb
     OR NOT p_bundle?&ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE='22023';
  END IF;
  BEGIN
    actor_user_id:=p_bundle->>'actorUserId';
    actor_role:=p_bundle->>'actorRole';
    created_at:=(p_bundle->>'createdAt')::timestamptz;
    result_text:=p_bundle->>'resultCanonicalJson';
    snapshot_text:=p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'decisionId' !~ '^dec_[0-9a-f]{32}$'
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions')<>'object'
     OR jsonb_typeof(p_bundle->'violations')<>'array'
     OR jsonb_array_length(p_bundle->'violations')>14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb)<>'object'
     OR jsonb_typeof(snapshot_text::jsonb)<>'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE='22023';
  END IF;
  SELECT actor.security_version INTO actor_security_version
  FROM public.users actor
  WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  FOR SHARE;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,actor_user_id,actor_security_version,actor_role,
    'PERSIST_DECISION_BUNDLE','DECISION',p_bundle->>'decisionId',
    public.actor_capability_payload_hash(p_bundle_text)
  ) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE='42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE='P5501';
  END IF;
  IF gate_active OR public.owner_stop_active(actor_user_id) THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE='55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND item.current_version=(p_bundle->>'principleVersion')::integer
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE='40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal:=ordinal+1;
    IF jsonb_typeof(violation)<>'object'
       OR violation-ARRAY['ruleId','severity','observedValue','thresholdValue','message']<>'{}'::jsonb
       OR NOT violation?&ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal:=array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );
  reference_payload:=jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_automation_kill_switch_active_v1(p_user_id text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE gate_active boolean;
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation kill switch scope denied' USING ERRCODE='42501';
  END IF;
  SELECT active INTO gate_active FROM public.risk_kill_switch
  WHERE kill_switch_id='GLOBAL' FOR SHARE;
  RETURN COALESCE(gate_active,true) OR public.owner_stop_active(p_user_id);
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v1(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE reservation_json jsonb;
DECLARE positions_json jsonb;
DECLARE signals_json jsonb;
DECLARE manual_symbols_json jsonb;
DECLARE observed_digest text;
DECLARE baseline_projection jsonb;
DECLARE daily_ready boolean;
DECLARE no_open_order boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation state input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE';
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
  IF control_row.user_id IS NULL OR checkpoint_row.run_id IS NULL OR run_row.run_id IS NULL THEN
    RAISE EXCEPTION 'automation state unavailable' USING ERRCODE='P0002';
  END IF;
  observed_digest:=public.p1_automation_runtime_account_digest_v1(claim_row.user_id,control_row.account_id);
  SELECT jsonb_build_object(
    'accountId',control_row.account_id,
    'cashKrw',balance.cash_krw,
    'marginRequirementKrw',balance.margin_requirement_krw,
    'portfolioEquityKrw',balance.portfolio_equity_krw,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'marketValueKrw',position.market_value_krw,
        'quantity',position.quantity,
        'symbol',position.symbol
      ) ORDER BY position.symbol)
      FROM public.portfolio_position_observations position
      WHERE position.balance_observation_id=balance.observation_id
    ),'[]'::jsonb)
  ) INTO baseline_projection
  FROM public.portfolio_balance_observations balance
  WHERE balance.owner_user_id=claim_row.user_id AND balance.source='KIS_MOCK'
    AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
    AND balance.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
  ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id LIMIT 1;
  no_open_order:=public.p1_automation_open_work_clear_v3(claim_row.user_id,control_row.account_id);
  daily_ready:=EXISTS (
    SELECT 1 FROM public.market_data_manifests manifest
    WHERE manifest.manifest_kind IN ('DAILY','AUTOMATION_BOOTSTRAP')
      AND manifest.status='ACCEPTED'
      AND manifest.session_date<=claim_row.session_date
      AND manifest.as_of<=((claim_row.session_date+time '09:20') AT TIME ZONE 'Asia/Seoul')
  );
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'positionId',position.position_id,'accountId',position.account_id,'symbol',position.symbol,
    'entrySession',position.entry_session,'expirySession',position.expiry_session,
    'createdAt',position.created_at,'status',position.status,'closedAt',position.closed_at
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions position
  WHERE position.user_id=claim_row.user_id AND position.account_id=control_row.account_id;
  SELECT to_jsonb(reservation) INTO reservation_json FROM (
    SELECT item.reservation_id AS "reservationId",item.symbol,item.side,item.quantity,
      item.limit_price_krw AS "limitPriceKrw",item.logical_submit_count AS "logicalSubmitCount",
      item.order_id AS "orderId",item.provider_order_ref_hash AS "providerOrderRefHash"
    FROM public.automation_order_reservations item WHERE item.run_id=p_run_id
  ) reservation;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'symbol',candidate.symbol,'lstmSignal',candidate.lstm_signal,
    'baselineSignal',candidate.baseline_signal,'expectedReturn',candidate.expected_return,
    'confidence',candidate.confidence
  ) ORDER BY candidate.symbol),'[]'::jsonb) INTO signals_json
  FROM (
    SELECT pointer.symbol,
      max(signal.signal) FILTER (WHERE signal.producer='LSTM') AS lstm_signal,
      max(signal.signal) FILTER (WHERE signal.producer='RULE_BASELINE') AS baseline_signal,
      max(signal.predicted_return) FILTER (WHERE signal.producer='LSTM') AS expected_return,
      max(signal.confidence) FILTER (WHERE signal.producer='LSTM') AS confidence
    FROM public.current_p1_return_signal_pointer pointer
    JOIN public.p1_return_signal_projection signal
      ON signal.bundle_sha256=pointer.bundle_sha256 AND signal.symbol=pointer.symbol
    WHERE pointer.session_date=claim_row.session_date
    GROUP BY pointer.symbol
    HAVING count(DISTINCT signal.producer)=2
  ) candidate;
  SELECT COALESCE(jsonb_agg(symbol ORDER BY symbol),'[]'::jsonb) INTO manual_symbols_json FROM (
    SELECT DISTINCT position.symbol
    FROM public.portfolio_position_observations position
    WHERE position.balance_observation_id=(
      SELECT balance.observation_id FROM public.portfolio_balance_observations balance
      WHERE balance.owner_user_id=claim_row.user_id AND balance.source='KIS_MOCK'
        AND balance.context_status='ACTIVE' AND balance.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
      ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id LIMIT 1
    )
  ) manual_position;
  RETURN jsonb_build_object(
    'accountComplete',observed_digest IS NOT NULL,
    'accountDigestMatches',observed_digest IS NOT NULL AND observed_digest=control_row.baseline_account_digest,
    'accountId',control_row.account_id,
    'baselineAccountDigest',control_row.baseline_account_digest,
    'baselineAccountProjection',baseline_projection,
    'brokerageMode',control_row.brokerage_mode,
    'checkpointVersion',checkpoint_row.checkpoint_version,
    'controlState',control_row.control_state,
    'controlVersion',control_row.version,
    'dailyShardFreshComplete',daily_ready,
    'decisionId',checkpoint_row.decision_id,
    'killSwitchActive',public.owner_stop_active(claim_row.user_id) OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true),
    'manualPositionSymbols',manual_symbols_json,
    'noOpenOrder',no_open_order,
    'positions',positions_json,
    'principleId',control_row.principle_id,
    'principleActiveCurrent',EXISTS (
      SELECT 1 FROM public.principles principle
      WHERE principle.user_id=claim_row.user_id AND principle.principle_id=control_row.principle_id
        AND principle.status='ACTIVE'
    ),
    'releaseActive',jsonb_array_length(signals_json)=31,
    'reservation',reservation_json,
    'runId',p_run_id,
    'runStartedAt',run_row.started_at,
    'selectedSide',checkpoint_row.selected_side,
    'selectedSymbol',checkpoint_row.selected_symbol,
    'sessionDate',claim_row.session_date,
    'signals',signals_json,
    'state',checkpoint_row.state,
    'strategyId',control_row.strategy_id,
    'unfinishedPreviousOrder',NOT no_open_order,
    'vertexCallCount',checkpoint_row.vertex_call_count,
    'providerCallCount',checkpoint_row.provider_call_count,
    'logicalSubmitCount',checkpoint_row.logical_submit_count
  )::text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.p1_arm_automation_v2(p_user_id text, p_account_id text, p_policy_id text, p_expected_policy_version integer, p_expected_control_version integer, p_scope_hash text, p_request_hash text)
 RETURNS TABLE(result_json text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE prior public.automation_control_idempotency%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE risk_projection jsonb;
DECLARE risk_digest text;
DECLARE legacy_digest text;
DECLARE current_version integer;
DECLARE next_version integer;
DECLARE local_now timestamp;
DECLARE target_session date;
DECLARE new_schedule_id text;
DECLARE projection jsonb;
DECLARE active_receipt text;
BEGIN
  PERFORM public.assert_actor_rls_scope_exact_v1(p_user_id,'ARM_AUTOMATION','AUTOMATION',p_user_id,p_request_hash);
  IF p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_account_id!~'^acct_[0-9a-f]{32}$' OR p_policy_id!~'^auto_pol_[0-9a-f]{32}$'
     OR p_expected_policy_version<1 OR p_expected_control_version<1 THEN
    RAISE EXCEPTION 'automation v2 arm input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,91));
  PERFORM pg_advisory_xact_lock(hashtextextended(p_scope_hash,91));
  SELECT * INTO prior FROM public.automation_control_idempotency WHERE scope_hash=p_scope_hash;
  IF FOUND THEN
    IF prior.user_id<>p_user_id OR prior.operation<>'ARM' OR prior.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR EXISTS (
    SELECT 1 FROM public.automation_policy_versions newer
    WHERE newer.user_id=p_user_id AND newer.version>p_expected_policy_version
  ) THEN RAISE EXCEPTION 'automation policy version conflict' USING ERRCODE='40001'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.principles item
    WHERE item.user_id=p_user_id AND item.principle_id=policy_row.principle_id
      AND item.status='ACTIVE' AND item.current_version=policy_row.principle_version
  ) THEN RAISE EXCEPTION 'automation principle version drift' USING ERRCODE='40001'; END IF;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  current_version:=CASE WHEN FOUND THEN control_row.version ELSE 1 END;
  IF current_version<>p_expected_control_version OR (control_row.user_id IS NOT NULL AND control_row.control_state<>'DISARMED')
     OR current_version=2147483647 THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  IF public.owner_stop_active(p_user_id) OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true) THEN
    RAISE EXCEPTION 'automation kill switch active' USING ERRCODE='40001';
  END IF;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  IF NOT FOUND OR gate_row.certification_status<>'VALID' OR NOT gate_row.clean_release_binding
     OR NOT gate_row.real_team_b_pointer_active OR gate_row.team_b_integrity_receipt_sha256 IS NULL THEN
    RAISE EXCEPTION 'automation activation gate closed' USING ERRCODE='40001';
  END IF;
  IF (SELECT count(*) FROM public.current_p1_return_signal_pointer)<>31
     OR (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)<>1 THEN
    RAISE EXCEPTION 'automation real Team B pointer unavailable' USING ERRCODE='40001';
  END IF;
  SELECT bundle.packet_sha256 INTO active_receipt FROM public.p1_return_artifact_bundle bundle
  WHERE bundle.bundle_sha256=(SELECT min(bundle_sha256) FROM public.current_p1_return_signal_pointer)
    AND bundle.real_team_b AND bundle.mock_runtime_eligible;
  IF active_receipt IS DISTINCT FROM gate_row.team_b_integrity_receipt_sha256 THEN
    RAISE EXCEPTION 'automation Team B receipt drift' USING ERRCODE='40001';
  END IF;
  risk_projection:=public.p1_automation_risk_balance_projection_v2(p_user_id,p_account_id);
  IF risk_projection IS NULL THEN
    RAISE EXCEPTION 'BLOCKED_INCOMPLETE_RISK_BALANCE' USING ERRCODE='P1B01';
  END IF;
  risk_digest:=encode(public.digest(convert_to(risk_projection::text,'UTF8'),'sha256'),'hex');
  legacy_digest:=public.p1_automation_account_digest_v1(p_user_id,'KIS_MOCK',p_account_id);
  local_now:=statement_timestamp() AT TIME ZONE 'Asia/Seoul';
  SELECT session_date INTO target_session FROM public.trading_sessions
  WHERE exchange_mic='XKRX' AND is_open
    AND (
      session_date>local_now::date
      OR (session_date=local_now::date AND local_now::time<time '09:30')
    )
  ORDER BY session_date LIMIT 1;
  IF target_session IS NULL THEN RAISE EXCEPTION 'automation next XKRX session unavailable' USING ERRCODE='40001'; END IF;
  next_version:=current_version+1;
  INSERT INTO public.automation_control(
    user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
    baseline_account_digest,certification_status,kill_switch_active,created_at,updated_at,
    policy_id,policy_version,principle_version_id,principle_version,
    team_b_integrity_receipt_sha256_v2,initial_account_digest_v2,expected_account_digest_v2,
    expected_account_projection_v2
  ) VALUES (
    p_user_id,'ARMED',next_version,'KIS_MOCK',p_account_id,policy_row.principle_id,'strategy_rule_lstm_v1',
    legacy_digest,'VALID',false,statement_timestamp(),statement_timestamp(),p_policy_id,
    policy_row.version,policy_row.principle_version_id,policy_row.principle_version,active_receipt,
    risk_digest,risk_digest,risk_projection
  ) ON CONFLICT (user_id) DO UPDATE SET
    control_state='ARMED',version=excluded.version,brokerage_mode='KIS_MOCK',account_id=excluded.account_id,
    principle_id=excluded.principle_id,strategy_id=excluded.strategy_id,
    baseline_account_digest=excluded.baseline_account_digest,certification_status='VALID',
    kill_switch_active=false,updated_at=excluded.updated_at,policy_id=excluded.policy_id,
    policy_version=excluded.policy_version,principle_version_id=excluded.principle_version_id,
    principle_version=excluded.principle_version,
    team_b_integrity_receipt_sha256_v2=excluded.team_b_integrity_receipt_sha256_v2,
    initial_account_digest_v2=excluded.initial_account_digest_v2,
    expected_account_digest_v2=excluded.expected_account_digest_v2,
    expected_account_projection_v2=excluded.expected_account_projection_v2;
  new_schedule_id:='auto_sched_'||substr(encode(public.digest(
    convert_to(p_user_id||':'||target_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (
    new_schedule_id,p_user_id,target_session,next_version,'ARMED',
    (target_session+time '09:30') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id,session_date) DO UPDATE SET
    control_version=excluded.control_version,schedule_state='ARMED',run_at=excluded.run_at,updated_at=excluded.updated_at;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(p_user_id||':'||next_version::text||':ARM_BASELINE','UTF8'),'sha256'),'hex'),1,32),
    p_user_id,NULL,COALESCE((SELECT max(sequence)+1 FROM public.automation_account_lineage WHERE user_id=p_user_id),1),
    'ARM_BASELINE',NULL,risk_digest,NULL,NULL,NULL,statement_timestamp()
  );
  projection:=jsonb_build_object(
    'blocker',NULL,'brokerageMode','KIS_MOCK','certificationStatus','VALID',
    'contractId','automation-status.v2','controlState','ARMED','controlVersion',next_version,
    'killSwitchActive',false,'nextSessionDate',target_session,'openPositionCount',0,
    'policyId',p_policy_id,'policyVersion',policy_row.version,'projectionState','ARMED',
    'riskBalanceStatus','COMPLETE'
  );
  INSERT INTO public.automation_control_idempotency(
    scope_hash,user_id,operation,request_hash,control_version,result_json
  ) VALUES (p_scope_hash,p_user_id,'ARM',p_request_hash,next_version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$function$
;
