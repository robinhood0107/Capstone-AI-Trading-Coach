-- FULL can use an owner-bound CONNECTED KIS_MOCK account after a successful complete
-- read-only balance proof. This is deliberately separate from the optional one-share
-- certification: no certification receipt or CERTIFIED state is synthesized.
SET LOCAL row_security=on;

CREATE TABLE public.full_owner_mock_connection_proofs_v217 (
  owner_user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  credential_revision bigint NOT NULL CHECK (credential_revision > 0),
  balance_observation_id text NOT NULL CHECK (balance_observation_id ~ '^pbo_[0-9a-f]{64}$'),
  balance_digest_sha256 text NOT NULL CHECK (balance_digest_sha256 ~ '^[0-9a-f]{64}$'),
  verified_at timestamptz NOT NULL,
  order_path_verified_at timestamptz,
  order_path_evidence_sha256 text CHECK (
    order_path_evidence_sha256 IS NULL OR order_path_evidence_sha256 ~ '^[0-9a-f]{64}$'
  ),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  CHECK ((order_path_verified_at IS NULL) = (order_path_evidence_sha256 IS NULL))
);
ALTER TABLE public.full_owner_mock_connection_proofs_v217 OWNER TO flyway;
GRANT SELECT, INSERT, UPDATE ON public.full_owner_mock_connection_proofs_v217 TO flyway;
CREATE POLICY user_mock_certification_runtime_read_v217 ON public.user_broker_credential_certification_attempts
  FOR SELECT TO PUBLIC USING (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  );

-- The provider-read-only connection path may provision only the matching user's gate.
ALTER TABLE public.automation_activation_gate NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credential_certification_attempts NO FORCE ROW LEVEL SECURITY;
CREATE POLICY automation_activation_full_owner_v217 ON public.automation_activation_gate TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_app'
    AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
  );
CREATE POLICY automation_activation_full_release_template_v217 ON public.automation_activation_gate TO PUBLIC
  USING (current_user='flyway' AND session_user='decision_app' AND user_id='usr_demo_user');

-- Preserve account owners already proven by V216's complete live balance observation.
INSERT INTO public.full_owner_mock_connection_proofs_v217 AS existing_proof(
  owner_user_id,account_id,credential_revision,balance_observation_id,
  balance_digest_sha256,verified_at,updated_at
)
SELECT credential.owner_user_id,credential.account_id,credential.revision,
       balance.observation_id,balance.artifact_hash,balance.observed_at,statement_timestamp()
FROM public.user_broker_credentials credential
JOIN LATERAL (
  SELECT item.observation_id,item.artifact_hash,item.observed_at
  FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=credential.owner_user_id
    AND item.account_scope_hash=substr(credential.account_id,6)||repeat('0',32)
    AND item.source='KIS_MOCK' AND item.context_status='ACTIVE'
    AND item.completeness='COMPLETE' AND item.source_version='kis-mock-online-complete-v2'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id DESC LIMIT 1
) balance ON true
WHERE credential.brokerage_mode='KIS_MOCK'
  AND credential.credential_state IN ('CONNECTED','CERTIFIED')
  AND NOT EXISTS (
    SELECT 1 FROM public.user_broker_credential_certification_attempts attempt
    WHERE attempt.owner_user_id=credential.owner_user_id
      AND attempt.account_id=credential.account_id
      AND attempt.credential_revision=credential.revision
      AND attempt.status IN ('RUNNING','RECOVERY_REQUIRED')
  )
ON CONFLICT (owner_user_id) DO UPDATE SET
  account_id=excluded.account_id,
  credential_revision=excluded.credential_revision,
  balance_observation_id=excluded.balance_observation_id,
  balance_digest_sha256=excluded.balance_digest_sha256,
  verified_at=excluded.verified_at,
  order_path_verified_at=CASE
    WHEN existing_proof.account_id=excluded.account_id
      AND existing_proof.credential_revision=excluded.credential_revision
      THEN existing_proof.order_path_verified_at
    ELSE NULL END,
  order_path_evidence_sha256=CASE
    WHEN existing_proof.account_id=excluded.account_id
      AND existing_proof.credential_revision=excluded.credential_revision
      THEN existing_proof.order_path_evidence_sha256
    ELSE NULL END,
  updated_at=statement_timestamp();

-- Reuse only the global source/release and Team-B proof carried by the installed FULL admin.
-- Each USER row stays REQUIRED with no receipt until the account owner completes the optional
-- one-share order test. The distinct FULL connection proof is the admission path.
INSERT INTO public.automation_activation_gate(
  user_id,certification_status,clean_release_binding,real_team_b_pointer_active,
  release_binding_sha256,updated_at,gate_version,certification_receipt_sha256,
  certification_session_date,strategy_eligible_from_session_date,source_binding_sha256,
  team_b_integrity_receipt_sha256
)
SELECT proof.owner_user_id,'REQUIRED',template.clean_release_binding,
       template.real_team_b_pointer_active,template.release_binding_sha256,
       statement_timestamp(),1,NULL,NULL,
       COALESCE((SELECT min(session.session_date) FROM public.trading_sessions session
                 WHERE session.exchange_mic='XKRX' AND session.is_open
                   AND session.session_date >= (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date),
                template.strategy_eligible_from_session_date),
       template.source_binding_sha256,template.team_b_integrity_receipt_sha256
FROM public.full_owner_mock_connection_proofs_v217 proof
CROSS JOIN public.automation_activation_gate template
WHERE template.user_id='usr_demo_user'
  AND proof.owner_user_id<>'usr_demo_user'
  AND template.clean_release_binding AND template.real_team_b_pointer_active
  AND template.release_binding_sha256 IS NOT NULL
  AND template.source_binding_sha256 IS NOT NULL
  AND template.team_b_integrity_receipt_sha256 IS NOT NULL
ON CONFLICT (user_id) DO NOTHING;

ALTER TABLE public.automation_activation_gate FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credential_certification_attempts FORCE ROW LEVEL SECURITY;

ALTER TABLE public.owner_kill_switch
  DROP CONSTRAINT IF EXISTS owner_kill_switch_reason_class_check,
  ADD CONSTRAINT owner_kill_switch_reason_class_v217_check CHECK (
    reason_class IN ('INITIAL_STATE','USER_MANUAL_STOP','USER_RESUME','BROKERAGE_FAILURE_STOP')
  ),
  ADD COLUMN failure_code text CHECK (
    failure_code IS NULL OR failure_code IN ('KIS_ORDER_REJECTED','KIS_ORDER_RESULT_UNCERTAIN')
  );

CREATE TABLE public.full_owner_order_failures_v217 (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  reason_code text NOT NULL CHECK (reason_code IN ('KIS_ORDER_REJECTED','KIS_ORDER_RESULT_UNCERTAIN')),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  resolved_at timestamptz,
  PRIMARY KEY (owner_user_id,run_id),
  CHECK (resolved_at IS NULL OR resolved_at>=recorded_at)
);
ALTER TABLE public.full_owner_order_failures_v217 OWNER TO flyway;
ALTER TABLE public.full_owner_order_failures_v217 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_order_failures_v217 FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.full_owner_order_failures_v217 FROM PUBLIC,decision_app,decision_automation_runtime;
CREATE POLICY full_owner_order_failures_runtime_v217 ON public.full_owner_order_failures_v217 TO PUBLIC
  USING (current_user='flyway' AND session_user='decision_automation_runtime'
    AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
  WITH CHECK (current_user='flyway' AND session_user='decision_automation_runtime'
    AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true));
CREATE POLICY full_owner_order_failures_reader_v217 ON public.full_owner_order_failures_v217 TO PUBLIC
  USING (current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true))
  WITH CHECK (current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true));

CREATE FUNCTION public.p1_stop_full_owner_after_order_failure_v217(
  p_run_id text,p_claim_token_hash text,p_reason_code text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_stop_full_owner_after_order_failure_v217$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE owner_stop_row public.owner_kill_switch%ROWTYPE;
DECLARE next_generation bigint;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id !~ '^auto_run_[A-Za-z0-9_-]{8,96}$'
     OR p_claim_token_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_reason_code NOT IN ('KIS_ORDER_REJECTED','KIS_ORDER_RESULT_UNCERTAIN') THEN
    RAISE EXCEPTION 'FULL KIS order failure stop input invalid' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);
  IF claim_row.run_id IS NULL THEN
    RAISE EXCEPTION 'FULL KIS order failure claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('app.automation_owner_user_id',claim_row.user_id,true);
  PERFORM pg_catalog.pg_advisory_xact_lock(hashtextextended('automation-control:'||claim_row.user_id,91));
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR UPDATE;
  IF control_row.user_id IS NULL OR control_row.account_id IS NULL OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RAISE EXCEPTION 'FULL KIS order failure control unavailable' USING ERRCODE='40001';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(199,pg_catalog.hashtext(claim_row.user_id));
  SELECT * INTO owner_stop_row FROM public.owner_kill_switch WHERE user_id=claim_row.user_id FOR UPDATE;
  IF owner_stop_row.user_id IS NULL THEN
    RAISE EXCEPTION 'FULL KIS order failure stop unavailable' USING ERRCODE='40001';
  END IF;
  IF NOT owner_stop_row.active OR owner_stop_row.reason_class<>'BROKERAGE_FAILURE_STOP'
     OR owner_stop_row.failure_code IS DISTINCT FROM p_reason_code THEN
    next_generation:=owner_stop_row.generation+1;
    UPDATE public.owner_kill_switch SET active=true,generation=next_generation,
      reason_class='BROKERAGE_FAILURE_STOP',failure_code=p_reason_code,
      changed_at=statement_timestamp()
    WHERE user_id=claim_row.user_id AND generation=owner_stop_row.generation;
    INSERT INTO public.owner_kill_switch_events(user_id,generation,active,changed_at,request_id)
    VALUES (claim_row.user_id,next_generation,true,statement_timestamp(),'order-failure:'||p_run_id)
    ON CONFLICT (user_id,generation) DO NOTHING;
  END IF;
  IF control_row.control_state<>'DISARMED' THEN
    IF control_row.version=2147483647 THEN
      RAISE EXCEPTION 'automation control version exhausted' USING ERRCODE='40001';
    END IF;
    UPDATE public.automation_control SET control_state='DISARMED',version=version+1,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id;
  END IF;
  UPDATE public.automation_runtime_schedule SET schedule_state='HALTED',updated_at=statement_timestamp()
  WHERE user_id=claim_row.user_id AND schedule_state IN ('ARMED','CLAIMED');
  INSERT INTO public.full_owner_order_failures_v217(owner_user_id,run_id,account_id,reason_code)
  VALUES (claim_row.user_id,p_run_id,control_row.account_id,p_reason_code)
  ON CONFLICT (owner_user_id,run_id) DO UPDATE SET
    account_id=excluded.account_id,reason_code=excluded.reason_code,
    recorded_at=statement_timestamp(),resolved_at=NULL;
  RETURN true;
END
$p1_stop_full_owner_after_order_failure_v217$;
ALTER FUNCTION public.p1_stop_full_owner_after_order_failure_v217(text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_stop_full_owner_after_order_failure_v217(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_stop_full_owner_after_order_failure_v217(text,text,text)
  TO decision_automation_runtime;

CREATE FUNCTION public.p1_full_owner_order_failure_code_v217(p_owner_user_id text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_full_owner_order_failure_code_v217$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'FULL KIS order failure reader scope denied' USING ERRCODE='42501';
  END IF;
  RETURN (SELECT failure.reason_code FROM public.full_owner_order_failures_v217 failure
    WHERE failure.owner_user_id=p_owner_user_id AND failure.resolved_at IS NULL
    ORDER BY failure.recorded_at DESC,failure.run_id DESC LIMIT 1);
END
$p1_full_owner_order_failure_code_v217$;
ALTER FUNCTION public.p1_full_owner_order_failure_code_v217(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_full_owner_order_failure_code_v217(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_full_owner_order_failure_code_v217(text) TO decision_app;

CREATE FUNCTION public.resolve_full_owner_order_failure_v217()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $resolve_full_owner_order_failure_v217$
BEGIN
  IF OLD.active AND NOT NEW.active AND OLD.reason_class='BROKERAGE_FAILURE_STOP' THEN
    UPDATE public.full_owner_order_failures_v217
    SET resolved_at=statement_timestamp()
    WHERE owner_user_id=OLD.user_id AND resolved_at IS NULL;
    NEW.failure_code:=NULL;
  END IF;
  RETURN NEW;
END
$resolve_full_owner_order_failure_v217$;
ALTER FUNCTION public.resolve_full_owner_order_failure_v217() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.resolve_full_owner_order_failure_v217() FROM PUBLIC;
CREATE TRIGGER owner_order_failure_resume_v217 BEFORE UPDATE OF active ON public.owner_kill_switch
  FOR EACH ROW EXECUTE FUNCTION public.resolve_full_owner_order_failure_v217();

ALTER TABLE public.full_owner_mock_connection_proofs_v217 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_mock_connection_proofs_v217 FORCE ROW LEVEL SECURITY;
CREATE POLICY full_owner_mock_connection_proof_definer_v217
  ON public.full_owner_mock_connection_proofs_v217 FOR SELECT TO PUBLIC
  USING (
    current_user='flyway' AND session_user IN ('decision_app','decision_automation_runtime')
    AND owner_user_id=COALESCE(
      NULLIF(pg_catalog.current_setting('app.actor_user_id',true),''),
      NULLIF(pg_catalog.current_setting('app.automation_owner_user_id',true),'')
    )
  );
CREATE POLICY full_owner_mock_connection_proof_owner_write_v217
  ON public.full_owner_mock_connection_proofs_v217 FOR ALL TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
  );
CREATE POLICY full_owner_mock_connection_proof_runtime_update_v217
  ON public.full_owner_mock_connection_proofs_v217 FOR UPDATE TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  );
REVOKE ALL ON public.full_owner_mock_connection_proofs_v217
  FROM PUBLIC,decision_app,decision_automation_runtime,decision_auth,decision_worker;

CREATE FUNCTION public.p1_full_owner_connection_readiness_v1(p_owner_user_id text,p_account_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_full_owner_connection_readiness_v1$
DECLARE scope_owner text;
BEGIN
  IF current_user<>'flyway' OR session_user NOT IN ('decision_app','decision_automation_runtime')
     OR p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_account_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'FULL KIS connection readiness scope denied' USING ERRCODE='42501';
  END IF;
  scope_owner:=COALESCE(
    NULLIF(pg_catalog.current_setting('app.actor_user_id',true),''),
    NULLIF(pg_catalog.current_setting('app.automation_owner_user_id',true),'')
  );
  IF scope_owner IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'FULL KIS connection readiness owner mismatch' USING ERRCODE='42501';
  END IF;
  RETURN EXISTS (
    SELECT 1
    FROM public.full_owner_mock_connection_proofs_v217 proof
    JOIN public.user_broker_credentials credential
      ON credential.owner_user_id=proof.owner_user_id
     AND credential.brokerage_mode='KIS_MOCK'
     AND credential.account_id=proof.account_id
     AND credential.revision=proof.credential_revision
     AND credential.credential_state IN ('CONNECTED','CERTIFIED')
    JOIN public.portfolio_balance_observations balance
      ON balance.observation_id=proof.balance_observation_id
     AND balance.owner_user_id=proof.owner_user_id
     AND balance.artifact_hash=proof.balance_digest_sha256
     AND balance.account_scope_hash=substr(proof.account_id,6)||repeat('0',32)
     AND balance.source='KIS_MOCK' AND balance.context_status='ACTIVE'
     AND balance.completeness='COMPLETE'
     AND balance.source_version='kis-mock-online-complete-v2'
    JOIN public.automation_activation_gate gate ON gate.user_id=proof.owner_user_id
    WHERE proof.owner_user_id=p_owner_user_id AND proof.account_id=p_account_id
      AND gate.clean_release_binding AND gate.release_binding_sha256 IS NOT NULL
      AND gate.real_team_b_pointer_active AND gate.source_binding_sha256 IS NOT NULL
      AND gate.team_b_integrity_receipt_sha256 IS NOT NULL
      AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
      AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1
      AND EXISTS (
        SELECT 1 FROM public.p1_return_artifact_bundle bundle
        WHERE bundle.bundle_sha256=(SELECT min(pointer.bundle_sha256)
          FROM public.current_p1_return_signal_pointer pointer)
          AND bundle.real_team_b AND bundle.mock_runtime_eligible
          AND bundle.packet_sha256=gate.team_b_integrity_receipt_sha256
      )
      AND NOT EXISTS (
        SELECT 1 FROM public.user_broker_credential_certification_attempts attempt
        WHERE attempt.owner_user_id=proof.owner_user_id
          AND attempt.account_id=proof.account_id
          AND attempt.credential_revision=proof.credential_revision
          AND attempt.status IN ('RUNNING','RECOVERY_REQUIRED')
      )
  );
END
$p1_full_owner_connection_readiness_v1$;
ALTER FUNCTION public.p1_full_owner_connection_readiness_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_full_owner_connection_readiness_v1(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_full_owner_connection_readiness_v1(text,text)
  TO decision_app,decision_automation_runtime;

CREATE FUNCTION public.record_full_owner_mock_connection_proof_v1(
  p_owner_user_id text,p_account_id text,p_revision bigint,p_observation_id text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $record_full_owner_mock_connection_proof_v1$
DECLARE credential public.user_broker_credentials%ROWTYPE;
DECLARE template public.automation_activation_gate%ROWTYPE;
DECLARE observation public.portfolio_balance_observations%ROWTYPE;
DECLARE eligible_session date;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision<1 OR p_observation_id !~ '^pbo_[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'FULL KIS connection proof input invalid' USING ERRCODE='42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id,'MARK_MOCK_CREDENTIAL_CONNECTED','BROKER_CREDENTIAL',p_account_id,
    'sha256:'||encode(public.digest(p_account_id,'sha256'),'hex')
  );
  SELECT * INTO credential FROM public.user_broker_credentials
  WHERE owner_user_id=p_owner_user_id AND brokerage_mode='KIS_MOCK' FOR UPDATE;
  IF credential.owner_user_id IS NULL OR credential.account_id<>p_account_id
     OR credential.revision<>p_revision OR credential.credential_state NOT IN ('CONNECTED','CERTIFIED') THEN
    RAISE EXCEPTION 'FULL KIS connection proof credential drift' USING ERRCODE='40001';
  END IF;
  SELECT * INTO observation FROM public.portfolio_balance_observations
  WHERE observation_id=p_observation_id AND owner_user_id=p_owner_user_id
    AND account_scope_hash=substr(p_account_id,6)||repeat('0',32)
    AND artifact_hash IS NOT NULL AND source='KIS_MOCK' AND context_status='ACTIVE'
    AND completeness='COMPLETE' AND source_version='kis-mock-online-complete-v2';
  IF observation.observation_id IS NULL THEN
    RAISE EXCEPTION 'FULL KIS connection proof observation unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO template FROM public.automation_activation_gate
  WHERE user_id='usr_demo_user';
  IF template.user_id IS NULL OR NOT template.clean_release_binding
     OR NOT template.real_team_b_pointer_active OR template.release_binding_sha256 IS NULL
     OR template.source_binding_sha256 IS NULL OR template.team_b_integrity_receipt_sha256 IS NULL
     OR (SELECT count(*) FROM public.current_p1_return_signal_pointer)<>31
     OR (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)<>1
     OR NOT EXISTS (
       SELECT 1 FROM public.p1_return_artifact_bundle bundle
       WHERE bundle.bundle_sha256=(SELECT min(pointer.bundle_sha256)
         FROM public.current_p1_return_signal_pointer pointer)
         AND bundle.real_team_b AND bundle.mock_runtime_eligible
         AND bundle.packet_sha256=template.team_b_integrity_receipt_sha256
     ) THEN
    RAISE EXCEPTION 'FULL automation release gate unavailable' USING ERRCODE='40001';
  END IF;
  SELECT min(session.session_date) INTO eligible_session
  FROM public.trading_sessions session
  WHERE session.exchange_mic='XKRX' AND session.is_open
    AND session.session_date >= (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
  IF eligible_session IS NULL THEN
    RAISE EXCEPTION 'FULL XKRX calendar unavailable' USING ERRCODE='40001';
  END IF;
  IF p_owner_user_id<>'usr_demo_user' THEN
    INSERT INTO public.automation_activation_gate AS existing_gate(
      user_id,certification_status,clean_release_binding,real_team_b_pointer_active,
      release_binding_sha256,updated_at,gate_version,certification_receipt_sha256,
      certification_session_date,strategy_eligible_from_session_date,source_binding_sha256,
      team_b_integrity_receipt_sha256
    ) VALUES (
      p_owner_user_id,'REQUIRED',true,true,template.release_binding_sha256,
      statement_timestamp(),1,NULL,NULL,eligible_session,template.source_binding_sha256,
      template.team_b_integrity_receipt_sha256
    ) ON CONFLICT (user_id) DO UPDATE SET
      clean_release_binding=true,real_team_b_pointer_active=true,
      release_binding_sha256=excluded.release_binding_sha256,
      source_binding_sha256=excluded.source_binding_sha256,
      team_b_integrity_receipt_sha256=excluded.team_b_integrity_receipt_sha256,
      strategy_eligible_from_session_date=CASE
        WHEN existing_gate.certification_status='VALID'
          AND existing_gate.certification_receipt_sha256 IS NOT NULL
          THEN existing_gate.strategy_eligible_from_session_date
        ELSE eligible_session END,
      certification_status=CASE
        WHEN existing_gate.certification_status='VALID'
          AND existing_gate.certification_receipt_sha256 IS NOT NULL
          THEN 'VALID' ELSE 'REQUIRED' END,
      certification_receipt_sha256=CASE
        WHEN existing_gate.certification_status='VALID'
          AND existing_gate.certification_receipt_sha256 IS NOT NULL
          THEN existing_gate.certification_receipt_sha256 ELSE NULL END,
      certification_session_date=CASE
        WHEN existing_gate.certification_status='VALID'
          AND existing_gate.certification_receipt_sha256 IS NOT NULL
          THEN existing_gate.certification_session_date ELSE NULL END,
      gate_version=existing_gate.gate_version+1,
      updated_at=statement_timestamp();
  END IF;
  INSERT INTO public.full_owner_mock_connection_proofs_v217 AS existing_proof(
    owner_user_id,account_id,credential_revision,balance_observation_id,
    balance_digest_sha256,verified_at,updated_at
  ) VALUES (
    p_owner_user_id,p_account_id,p_revision,p_observation_id,observation.artifact_hash,
    observation.observed_at,statement_timestamp()
  ) ON CONFLICT (owner_user_id) DO UPDATE SET
    account_id=excluded.account_id,
    order_path_verified_at=CASE
      WHEN existing_proof.account_id=excluded.account_id
       AND existing_proof.credential_revision=excluded.credential_revision
       THEN existing_proof.order_path_verified_at ELSE NULL END,
    order_path_evidence_sha256=CASE
      WHEN existing_proof.account_id=excluded.account_id
       AND existing_proof.credential_revision=excluded.credential_revision
       THEN existing_proof.order_path_evidence_sha256 ELSE NULL END,
    credential_revision=excluded.credential_revision,
    balance_observation_id=excluded.balance_observation_id,
    balance_digest_sha256=excluded.balance_digest_sha256,
    verified_at=excluded.verified_at,
    updated_at=statement_timestamp();
  RETURN true;
END
$record_full_owner_mock_connection_proof_v1$;
ALTER FUNCTION public.record_full_owner_mock_connection_proof_v1(text,text,bigint,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_full_owner_mock_connection_proof_v1(text,text,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_full_owner_mock_connection_proof_v1(text,text,bigint,text) TO decision_app;

CREATE FUNCTION public.p1_full_owner_order_path_verified_v1(p_owner_user_id text,p_account_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_full_owner_order_path_verified_v1$
BEGIN
  IF current_user<>'flyway' OR session_user NOT IN ('decision_app','decision_automation_runtime')
     OR p_owner_user_id IS NULL OR p_account_id IS NULL THEN
    RAISE EXCEPTION 'FULL KIS order-path status scope denied' USING ERRCODE='42501';
  END IF;
  IF COALESCE(NULLIF(pg_catalog.current_setting('app.actor_user_id',true),''),
              NULLIF(pg_catalog.current_setting('app.automation_owner_user_id',true),''))
       IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'FULL KIS order-path owner mismatch' USING ERRCODE='42501';
  END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.full_owner_mock_connection_proofs_v217 proof
    JOIN public.user_broker_credentials credential
      ON credential.owner_user_id=proof.owner_user_id
     AND credential.brokerage_mode='KIS_MOCK' AND credential.account_id=proof.account_id
     AND credential.revision=proof.credential_revision
     AND credential.credential_state IN ('CONNECTED','CERTIFIED')
    WHERE proof.owner_user_id=p_owner_user_id AND proof.account_id=p_account_id
      AND proof.order_path_verified_at IS NOT NULL
      AND proof.order_path_evidence_sha256 IS NOT NULL
  );
END
$p1_full_owner_order_path_verified_v1$;
ALTER FUNCTION public.p1_full_owner_order_path_verified_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_full_owner_order_path_verified_v1(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_full_owner_order_path_verified_v1(text,text)
  TO decision_app,decision_automation_runtime;

CREATE FUNCTION public.mark_full_owner_order_path_reconciled_v217()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $mark_full_owner_order_path_reconciled_v217$
DECLARE bound_account text;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR NEW.state NOT IN ('COMPLETED','CANCELLED_UNFILLED')
     OR NEW.physical_submit_count<1 THEN
    RETURN NEW;
  END IF;
  PERFORM pg_catalog.set_config('app.automation_owner_user_id',NEW.user_id,true);
  SELECT account_id INTO bound_account FROM public.automation_control
  WHERE user_id=NEW.user_id AND brokerage_mode='KIS_MOCK';
  IF bound_account IS NULL THEN RETURN NEW; END IF;
  UPDATE public.full_owner_mock_connection_proofs_v217 proof
  SET order_path_verified_at=statement_timestamp(),
      order_path_evidence_sha256=encode(public.digest(convert_to(
        NEW.run_id||':'||NEW.state||':'||NEW.session_date::text||':'||bound_account||':'||
        NEW.physical_submit_count::text||':'||NEW.updated_at::text,'UTF8'),'sha256'),'hex'),
      updated_at=statement_timestamp()
  FROM public.user_broker_credentials credential
  WHERE proof.owner_user_id=NEW.user_id AND proof.account_id=bound_account
    AND credential.owner_user_id=proof.owner_user_id AND credential.brokerage_mode='KIS_MOCK'
    AND credential.account_id=proof.account_id AND credential.revision=proof.credential_revision
    AND credential.credential_state IN ('CONNECTED','CERTIFIED');
  RETURN NEW;
END
$mark_full_owner_order_path_reconciled_v217$;
ALTER FUNCTION public.mark_full_owner_order_path_reconciled_v217() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.mark_full_owner_order_path_reconciled_v217() FROM PUBLIC;
CREATE TRIGGER automation_runs_full_owner_order_path_v217
  AFTER UPDATE OF state,physical_submit_count ON public.automation_runs
  FOR EACH ROW WHEN (NEW.state IN ('COMPLETED','CANCELLED_UNFILLED') AND NEW.physical_submit_count>0)
  EXECUTE FUNCTION public.mark_full_owner_order_path_reconciled_v217();

-- Keep the existing credential receipt path intact. The extra FULL path is enabled only by
-- p1_arm_automation_full_v1 below after its owner/account/revision proof has been verified.
CREATE OR REPLACE FUNCTION public.p1_arm_automation_v2(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_v2$
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
DECLARE owner_connection_ready boolean;
DECLARE gate_certified boolean;
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
  owner_connection_ready:=
    current_setting('app.full_connected_credential_arm',true)='on'
    AND public.p1_full_owner_connection_readiness_v1(p_user_id,p_account_id);
  gate_certified:=gate_row.user_id IS NOT NULL AND gate_row.certification_status='VALID'
    AND gate_row.certification_receipt_sha256 IS NOT NULL
    AND gate_row.strategy_eligible_from_session_date IS NOT NULL;
  IF gate_row.user_id IS NULL OR (NOT gate_certified AND NOT owner_connection_ready)
     OR NOT gate_row.clean_release_binding OR NOT gate_row.real_team_b_pointer_active
     OR gate_row.release_binding_sha256 IS NULL OR gate_row.source_binding_sha256 IS NULL
     OR gate_row.team_b_integrity_receipt_sha256 IS NULL THEN
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
    AND (session_date>local_now::date OR (session_date=local_now::date AND local_now::time<time '09:30'))
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
    legacy_digest,CASE WHEN owner_connection_ready AND NOT gate_certified THEN 'REQUIRED' ELSE 'VALID' END,
    false,statement_timestamp(),statement_timestamp(),p_policy_id,policy_row.version,
    policy_row.principle_version_id,policy_row.principle_version,active_receipt,
    risk_digest,risk_digest,risk_projection
  ) ON CONFLICT (user_id) DO UPDATE SET
    control_state='ARMED',version=excluded.version,brokerage_mode='KIS_MOCK',account_id=excluded.account_id,
    principle_id=excluded.principle_id,strategy_id=excluded.strategy_id,
    baseline_account_digest=excluded.baseline_account_digest,certification_status=excluded.certification_status,
    kill_switch_active=false,updated_at=excluded.updated_at,policy_id=excluded.policy_id,
    policy_version=excluded.policy_version,principle_version_id=excluded.principle_version_id,
    principle_version=excluded.principle_version,
    team_b_integrity_receipt_sha256_v2=excluded.team_b_integrity_receipt_sha256_v2,
    initial_account_digest_v2=excluded.initial_account_digest_v2,
    expected_account_digest_v2=excluded.expected_account_digest_v2,
    expected_account_projection_v2=excluded.expected_account_projection_v2;
  new_schedule_id:='auto_sched_'||substr(encode(public.digest(convert_to(p_user_id||':'||target_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (new_schedule_id,p_user_id,target_session,next_version,'ARMED',
    (target_session+time '09:30') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp())
  ON CONFLICT (user_id,session_date) DO UPDATE SET control_version=excluded.control_version,
    schedule_state='ARMED',run_at=excluded.run_at,updated_at=excluded.updated_at;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(p_user_id||':'||next_version::text||':ARM_BASELINE','UTF8'),'sha256'),'hex'),1,32),
    p_user_id,NULL,COALESCE((SELECT max(sequence)+1 FROM public.automation_account_lineage WHERE user_id=p_user_id),1),
    'ARM_BASELINE',NULL,risk_digest,NULL,NULL,NULL,statement_timestamp()
  );
  projection:=jsonb_build_object(
    'blocker',NULL,'brokerageMode','KIS_MOCK',
    'certificationStatus',CASE WHEN owner_connection_ready AND NOT gate_certified THEN 'REQUIRED' ELSE 'VALID' END,
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
$p1_arm_automation_v2$;
ALTER FUNCTION public.p1_arm_automation_v2(text,text,text,integer,integer,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_arm_automation_v2(text,text,text,integer,integer,text,text)
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_v2(text,text,text,integer,integer,text,text) TO decision_app;

CREATE FUNCTION public.p1_arm_automation_full_v1(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text,
  p_provider_capability_ready boolean,p_operator_provider_ready boolean
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_full_v1$
BEGIN
  IF session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id
     OR NOT public.p1_full_owner_connection_readiness_v1(p_user_id,p_account_id) THEN
    RAISE EXCEPTION 'FULL KIS read-only connection proof required' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('app.full_connected_credential_arm','on',true);
  RETURN QUERY SELECT * FROM public.p1_arm_automation_v3(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,p_expected_control_version,
    p_scope_hash,p_request_hash,p_provider_capability_ready,p_operator_provider_ready
  );
END
$p1_arm_automation_full_v1$;
ALTER FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text, text,boolean,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text,text,boolean,boolean)
  FROM PUBLIC,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text,text,boolean,boolean)
  TO decision_app;

CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(p_user_id text,p_target_session date)
 RETURNS TABLE(control_configured boolean,certification_valid boolean,release_source_bound boolean,
   real_team_b_ready boolean,principle_current boolean,kill_switch_inactive boolean,
   account_baseline_matches boolean,unresolved_state_clear boolean,target_available boolean,
   current_control_version integer,all_ready boolean)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_runtime_readiness_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE observed_digest text;
DECLARE owner_connection_ready boolean;
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
  owner_connection_ready:=control_row.user_id IS NOT NULL
    AND public.p1_full_owner_connection_readiness_v1(p_user_id,control_row.account_id);
  release_source_bound:=gate_row.user_id IS NOT NULL AND gate_row.clean_release_binding
    AND gate_row.release_binding_sha256 IS NOT NULL AND gate_row.source_binding_sha256 IS NOT NULL;
  real_team_b_ready:=gate_row.user_id IS NOT NULL AND gate_row.real_team_b_pointer_active
    AND gate_row.team_b_integrity_receipt_sha256 IS NOT NULL
    AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
    AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1;
  principle_current:=control_row.user_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.principles principle WHERE principle.user_id=p_user_id
      AND principle.principle_id=control_row.principle_id AND principle.status='ACTIVE'
      AND (control_row.control_state<>'ARMED' OR control_row.principle_version IS NULL
        OR principle.current_version=control_row.principle_version)
  );
  kill_switch_inactive:=COALESCE((SELECT NOT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),false)
    AND NOT public.owner_stop_active(p_user_id);
  IF control_configured THEN
    IF control_row.expected_account_digest_v2 IS NOT NULL THEN
      observed_digest:=encode(public.digest(convert_to(
        public.p1_automation_risk_balance_projection_v2(p_user_id,control_row.account_id)::text,'UTF8'),'sha256'),'hex');
    ELSE
      observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
    END IF;
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=COALESCE(control_row.expected_account_digest_v2,control_row.baseline_account_digest);
  unresolved_state_clear:=control_row.user_id IS NOT NULL
    AND public.p1_automation_open_work_clear_v3(p_user_id,control_row.account_id);
  IF control_row.control_state='ARMED' THEN
    target_available:=EXISTS (SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED') AND schedule.control_version=control_row.version);
  ELSE
    target_available:=NOT EXISTS (SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED') AND schedule.control_version=control_row.version);
  END IF;
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND (certification_valid OR owner_connection_ready)
    AND release_source_bound AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$p1_automation_runtime_readiness_v1$;
ALTER FUNCTION public.p1_automation_runtime_readiness_v1(text,date) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_runtime_readiness_v1(text,date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_runtime_readiness_v1(text,date) TO decision_automation_runtime;

-- V214 replaced the owner-list function but omitted the short-lived scan GUC required by the
-- automation_control RLS policy. The runtime then saw an empty owner set and silently never
-- started any user's schedule. The extra permissive policy is read-only; writes remain owner-bound.
CREATE POLICY automation_control_runtime_owner_scan_v217 ON public.automation_control TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND pg_catalog.current_setting('app.automation_claim_scan',true)='1'
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  );

CREATE OR REPLACE FUNCTION public.p1_list_armed_automation_users_v1()
RETURNS TABLE(user_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_list_armed_automation_users_v1_v217$
DECLARE active_count integer;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation owner listing denied' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','1',true);
  SELECT count(*) INTO active_count
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id=control.user_id
  WHERE app_user.status='ACTIVE' AND (control.control_state='ARMED' OR EXISTS (
    SELECT 1 FROM public.automation_runtime_claim claim
    WHERE claim.user_id=control.user_id AND claim.claim_state='ACTIVE'
  ));
  IF active_count>100 THEN
    RAISE EXCEPTION 'automation owner admission cap exceeded' USING ERRCODE='54000';
  END IF;
  RETURN QUERY SELECT control.user_id
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id=control.user_id
  WHERE app_user.status='ACTIVE' AND (control.control_state='ARMED' OR EXISTS (
    SELECT 1 FROM public.automation_runtime_claim claim
    WHERE claim.user_id=control.user_id AND claim.claim_state='ACTIVE'
  ))
  ORDER BY control.user_id;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);
END
$p1_list_armed_automation_users_v1_v217$;
ALTER FUNCTION public.p1_list_armed_automation_users_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_list_armed_automation_users_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_list_armed_automation_users_v1() TO decision_automation_runtime;

-- V207's RETURNS TABLE output names overlap schedule columns. Qualify them so PL/pgSQL does
-- not confuse the `user_id` output variable with a row field while claiming the first session.
CREATE OR REPLACE FUNCTION public.p1_claim_automation_session_for_owner_v1(
  p_owner_user_id text,p_session_date date,p_claim_token_hash text
)
RETURNS TABLE(
  user_id text,run_id text,control_version integer,account_id text,principle_id text,
  strategy_id text,baseline_account_digest text,replayed boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_claim_automation_session_for_owner_v1$
DECLARE schedule_row public.automation_runtime_schedule%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE new_run_id text;
DECLARE pinned_version public.principle_versions%ROWTYPE;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_session_date IS NULL OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation claim input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','1',true);
  SELECT schedule.* INTO schedule_row
  FROM public.automation_runtime_schedule schedule
  JOIN public.automation_runtime_claim claim
    ON claim.user_id=schedule.user_id AND claim.session_date=schedule.session_date
  WHERE schedule.user_id=p_owner_user_id AND schedule.session_date=p_session_date
    AND schedule.schedule_state='CLAIMED' AND claim.claim_state='ACTIVE'
  ORDER BY schedule.user_id LIMIT 1 FOR UPDATE OF schedule,claim;
  IF FOUND THEN
    PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);
    PERFORM pg_catalog.set_config('app.automation_owner_user_id',schedule_row.user_id,true);
    UPDATE public.automation_runtime_claim claim SET claim_token_hash=p_claim_token_hash
      WHERE claim.user_id=schedule_row.user_id AND claim.session_date=p_session_date
        AND claim.claim_state='ACTIVE' AND claim.claim_token_hash<>p_claim_token_hash;
    SELECT * INTO claim_row FROM public.automation_runtime_claim claim
      WHERE claim.user_id=schedule_row.user_id AND claim.session_date=p_session_date;
    SELECT * INTO control_row FROM public.automation_control control
      WHERE control.user_id=schedule_row.user_id;
    user_id:=schedule_row.user_id;run_id:=claim_row.run_id;control_version:=control_row.version;
    account_id:=control_row.account_id;principle_id:=control_row.principle_id;
    strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
    replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT schedule.* INTO schedule_row FROM public.automation_runtime_schedule schedule
  WHERE schedule.user_id=p_owner_user_id AND schedule.session_date=p_session_date
    AND schedule.schedule_state='ARMED'
  ORDER BY schedule.user_id LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN
    PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);RETURN;
  END IF;
  PERFORM pg_catalog.set_config('app.automation_claim_scan','0',true);
  PERFORM pg_catalog.set_config('app.automation_owner_user_id',schedule_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control control
    WHERE control.user_id=schedule_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.control_state<>'ARMED' OR control_row.version<>schedule_row.control_version
     OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RAISE EXCEPTION 'automation schedule control drift' USING ERRCODE='40001';
  END IF;
  PERFORM 1 FROM public.principles principle WHERE principle.user_id=schedule_row.user_id
    AND principle.principle_id=control_row.principle_id AND principle.status='ACTIVE' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'inactive automation principle' USING ERRCODE='40001'; END IF;
  IF EXISTS(SELECT 1 FROM public.automation_runtime_claim prior_claim
    WHERE prior_claim.user_id=schedule_row.user_id AND prior_claim.claim_state='ACTIVE') THEN
    RAISE EXCEPTION 'prior automation session remains active' USING ERRCODE='40001';
  END IF;
  SELECT version_row.* INTO STRICT pinned_version FROM public.principles principle
    JOIN public.principle_versions version_row
      ON version_row.principle_id=principle.principle_id AND version_row.version=principle.current_version
    WHERE principle.user_id=schedule_row.user_id AND principle.principle_id=control_row.principle_id
      AND version_row.status='ACTIVE';
  UPDATE public.automation_control control SET principle_version_id=pinned_version.principle_version_id,
    principle_version=pinned_version.version WHERE control.user_id=schedule_row.user_id
      AND control.policy_id IS NOT NULL;
  control_row.principle_version_id:=pinned_version.principle_version_id;
  control_row.principle_version:=pinned_version.version;
  new_run_id:='auto_run_'||substr(encode(public.digest(
    convert_to(schedule_row.user_id||':'||p_session_date::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runs(
    run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
    physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at,
    principle_id,principle_version_id,principle_version
  ) VALUES (
    new_run_id,schedule_row.user_id,p_session_date,'SCHEDULED','KIS_MOCK',NULL,NULL,
    0,0,0,statement_timestamp(),statement_timestamp(),
    control_row.principle_id,control_row.principle_version_id,control_row.principle_version
  );
  INSERT INTO public.automation_runtime_claim(
    user_id,session_date,run_id,claim_token_hash,claim_state,claimed_at,released_at
  ) VALUES (schedule_row.user_id,p_session_date,new_run_id,p_claim_token_hash,'ACTIVE',statement_timestamp(),NULL);
  INSERT INTO public.automation_runtime_checkpoint(
    run_id,user_id,session_date,checkpoint_version,state,selected_symbol,selected_side,decision_id,
    vertex_call_count,provider_call_count,logical_submit_count,updated_at
  ) VALUES (new_run_id,schedule_row.user_id,p_session_date,1,'SCHEDULED',NULL,NULL,NULL,0,0,0,statement_timestamp());
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES
    ('auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':1:BASELINE_CAPTURED','UTF8'),'sha256'),'hex'),1,32),
     new_run_id,schedule_row.user_id,1,'BASELINE_CAPTURED',statement_timestamp(),
     encode(public.digest(convert_to(control_row.baseline_account_digest,'UTF8'),'sha256'),'hex'),0,0,true),
    ('auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':2:RUN_TRANSITIONED','UTF8'),'sha256'),'hex'),1,32),
     new_run_id,schedule_row.user_id,2,'RUN_TRANSITIONED',statement_timestamp(),
     encode(public.digest(convert_to('SCHEDULED','UTF8'),'sha256'),'hex'),0,0,true);
  UPDATE public.automation_runtime_schedule schedule SET schedule_state='CLAIMED',updated_at=statement_timestamp()
  WHERE schedule.schedule_id=schedule_row.schedule_id;
  event_seed:=new_run_id||':SESSION_CLAIMED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    schedule_row.user_id,p_session_date,new_run_id,'SESSION_CLAIMED',
    encode(public.digest(convert_to(p_claim_token_hash,'UTF8'),'sha256'),'hex'),true,statement_timestamp()
  );
  user_id:=schedule_row.user_id;run_id:=new_run_id;control_version:=control_row.version;
  account_id:=control_row.account_id;principle_id:=control_row.principle_id;
  strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
  replayed:=false;RETURN NEXT;
END
$p1_claim_automation_session_for_owner_v1$;
ALTER FUNCTION public.p1_claim_automation_session_for_owner_v1(text,date,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_claim_automation_session_for_owner_v1(text,date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_claim_automation_session_for_owner_v1(text,date,text)
  TO decision_automation_runtime;
