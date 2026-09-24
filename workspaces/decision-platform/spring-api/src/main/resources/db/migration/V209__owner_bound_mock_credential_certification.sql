-- A CONNECTED owner can request one server-fixed KIS_MOCK test order. Only the
-- exact account/revision that completed cancellation and reconciliation becomes CERTIFIED.
CREATE TABLE public.user_broker_credential_certification_attempts (
  certification_id text PRIMARY KEY CHECK (certification_id ~ '^cert_[0-9a-f]{32}$'),
  owner_user_id text NOT NULL REFERENCES public.users(user_id),
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  credential_revision bigint NOT NULL CHECK (credential_revision > 0),
  session_date date NOT NULL,
  status text NOT NULL CHECK (status IN ('RUNNING','RECOVERY_REQUIRED','PASS','FAILED')),
  lease_token_sha256 text CHECK (lease_token_sha256 IS NULL OR lease_token_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  receipt_sha256 text CHECK (receipt_sha256 IS NULL OR receipt_sha256 ~ '^[0-9a-f]{64}$'),
  failure_code text CHECK (failure_code IS NULL OR failure_code IN (
    'MARKET_CLOSED','QUOTE_INVALID','BUYABLE_UNAVAILABLE','PROVIDER_FAILED',
    'TEST_ORDER_RECOVERED','TEST_ORDER_UNCERTAIN','EXECUTION_FILLED','BALANCE_CHANGED'
  )),
  quote_calls smallint NOT NULL DEFAULT 0 CHECK (quote_calls BETWEEN 0 AND 1),
  brokerage_calls smallint NOT NULL DEFAULT 0 CHECK (brokerage_calls BETWEEN 0 AND 7),
  token_calls smallint NOT NULL DEFAULT 0 CHECK (token_calls BETWEEN 0 AND 1),
  attempt_number smallint NOT NULL DEFAULT 1 CHECK (attempt_number BETWEEN 1 AND 10),
  started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  completed_at timestamptz,
  CHECK ((status = 'PASS') = (receipt_sha256 IS NOT NULL)),
  CHECK ((status IN ('FAILED','RECOVERY_REQUIRED')) = (failure_code IS NOT NULL)),
  CHECK ((status = 'RUNNING') = (lease_token_sha256 IS NOT NULL))
);
ALTER TABLE public.user_broker_credential_certification_attempts OWNER TO flyway;
ALTER TABLE public.user_broker_credential_certification_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credential_certification_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY user_mock_certification_definer_v209 ON public.user_broker_credential_certification_attempts
  TO PUBLIC USING (current_user = 'flyway' AND session_user = 'decision_app')
  WITH CHECK (current_user = 'flyway' AND session_user = 'decision_app');
REVOKE ALL ON public.user_broker_credential_certification_attempts
  FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker,
  decision_replay, decision_rag_writer, decision_automation_runtime;
CREATE UNIQUE INDEX user_mock_certification_active_v209
  ON public.user_broker_credential_certification_attempts(owner_user_id,account_id,credential_revision)
  WHERE status IN ('RUNNING','RECOVERY_REQUIRED');
CREATE INDEX user_mock_certification_summary_v209
  ON public.user_broker_credential_certification_attempts(owner_user_id,account_id,credential_revision,started_at DESC);

CREATE FUNCTION public.guard_mock_credential_certification_rotation_v209()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $guard_mock_credential_certification_rotation_v209$
DECLARE replaces_credential boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    replaces_credential := true;
  ELSE
    replaces_credential :=
      NEW.account_id IS DISTINCT FROM OLD.account_id
      OR NEW.revision IS DISTINCT FROM OLD.revision
      OR NEW.kek_version IS DISTINCT FROM OLD.kek_version
      OR NEW.wrap_nonce IS DISTINCT FROM OLD.wrap_nonce
      OR NEW.wrapped_dek IS DISTINCT FROM OLD.wrapped_dek
      OR NEW.wrap_tag IS DISTINCT FROM OLD.wrap_tag
      OR NEW.secret_nonce IS DISTINCT FROM OLD.secret_nonce
      OR NEW.secret_ciphertext IS DISTINCT FROM OLD.secret_ciphertext
      OR NEW.secret_tag IS DISTINCT FROM OLD.secret_tag;
    replaces_credential := replaces_credential
      OR (NEW.credential_state IS DISTINCT FROM OLD.credential_state
          AND NEW.credential_state = 'DISCONNECTING');
  END IF;
  IF replaces_credential AND EXISTS (
    SELECT 1 FROM public.user_broker_credential_certification_attempts attempt
    WHERE attempt.owner_user_id = OLD.owner_user_id AND attempt.account_id = OLD.account_id
      AND attempt.credential_revision = OLD.revision
      AND attempt.status IN ('RUNNING','RECOVERY_REQUIRED')
  ) THEN
    RAISE EXCEPTION 'mock credential certification requires recovery' USING ERRCODE = '40001';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$guard_mock_credential_certification_rotation_v209$;
ALTER FUNCTION public.guard_mock_credential_certification_rotation_v209() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.guard_mock_credential_certification_rotation_v209()
  FROM PUBLIC, decision_app, decision_worker, decision_auth, decision_identity;
CREATE TRIGGER user_broker_certification_rotation_guard_v209
  BEFORE UPDATE OR DELETE ON public.user_broker_credentials
  FOR EACH ROW EXECUTE FUNCTION public.guard_mock_credential_certification_rotation_v209();

CREATE FUNCTION public.begin_bound_mock_certification_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint, p_lease_token_sha256 text
) RETURNS TABLE(certification_id text, certification_status text, session_date date,
                recovery boolean, already_certified boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $begin_bound_mock_certification_v1$
DECLARE credential public.user_broker_credentials%ROWTYPE;
DECLARE previous_attempt public.user_broker_credential_certification_attempts%ROWTYPE;
DECLARE selected_id text;
DECLARE selected_session date;
DECLARE is_recovery boolean := false;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_account_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1
     OR p_lease_token_sha256 IS NULL OR p_lease_token_sha256 !~ '^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'mock certification actor denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'BEGIN_MOCK_CERTIFICATION', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT * INTO credential FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF credential.owner_user_id IS NULL OR credential.account_id IS DISTINCT FROM p_account_id
     OR credential.revision IS DISTINCT FROM p_revision
     OR credential.credential_state NOT IN ('CONNECTED','CERTIFIED') THEN
    RAISE EXCEPTION 'mock certification credential unavailable' USING ERRCODE = '40001';
  END IF;

  SELECT * INTO previous_attempt FROM public.user_broker_credential_certification_attempts attempt
  WHERE attempt.owner_user_id = p_owner_user_id AND attempt.account_id = p_account_id
    AND attempt.credential_revision = p_revision
  ORDER BY attempt.started_at DESC LIMIT 1 FOR UPDATE;
  IF credential.credential_state = 'CERTIFIED' THEN
    IF previous_attempt.certification_id IS NULL OR previous_attempt.status <> 'PASS' THEN
      RAISE EXCEPTION 'mock certification receipt unavailable' USING ERRCODE = '40001';
    END IF;
    RETURN QUERY SELECT previous_attempt.certification_id,'PASS',previous_attempt.session_date,false,true;
    RETURN;
  END IF;
  IF previous_attempt.certification_id IS NOT NULL AND previous_attempt.status = 'PASS' THEN
    RAISE EXCEPTION 'mock certification state drift' USING ERRCODE = '40001';
  END IF;

  IF previous_attempt.certification_id IS NOT NULL
     AND previous_attempt.status IN ('RUNNING','RECOVERY_REQUIRED') THEN
    IF previous_attempt.status = 'RUNNING'
       AND previous_attempt.updated_at > statement_timestamp() - interval '2 minutes' THEN
      RAISE EXCEPTION 'mock certification already running' USING ERRCODE = '40001';
    END IF;
    selected_id := previous_attempt.certification_id;
    selected_session := previous_attempt.session_date;
    is_recovery := true;
    UPDATE public.user_broker_credential_certification_attempts
    SET status = 'RUNNING', failure_code = NULL, lease_token_sha256 = p_lease_token_sha256,
        attempt_number = least(attempt_number + 1, 10), updated_at = statement_timestamp()
    WHERE public.user_broker_credential_certification_attempts.certification_id = selected_id;
  ELSE
    selected_id := 'cert_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex');
    selected_session := (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
    INSERT INTO public.user_broker_credential_certification_attempts(
      certification_id,owner_user_id,account_id,credential_revision,session_date,status,lease_token_sha256
    ) VALUES (
      selected_id,p_owner_user_id,p_account_id,p_revision,selected_session,'RUNNING',p_lease_token_sha256
    );
  END IF;
  INSERT INTO public.audit_logs(audit_log_id,user_id,action,target_type,target_id,payload_json)
  VALUES (
    'aud_mock_cert_begin_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id,'MOCK_CREDENTIAL_CERTIFICATION_STARTED','BROKER_CREDENTIAL',p_account_id,
    pg_catalog.jsonb_build_object('certificationId',selected_id,'revision',p_revision,'recovery',is_recovery)
  );
  RETURN QUERY SELECT selected_id,'RUNNING',selected_session,is_recovery,false;
END;
$begin_bound_mock_certification_v1$;
ALTER FUNCTION public.begin_bound_mock_certification_v1(text,text,bigint,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.begin_bound_mock_certification_v1(text,text,bigint,text)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.begin_bound_mock_certification_v1(text,text,bigint,text) TO decision_app;

CREATE FUNCTION public.complete_bound_mock_certification_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint, p_certification_id text,
  p_lease_token_sha256 text, p_receipt_sha256 text, p_session_date date,
  p_quote_calls integer, p_brokerage_calls integer, p_token_calls integer
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $complete_bound_mock_certification_v1$
DECLARE completed integer;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_account_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1
     OR p_certification_id IS NULL OR p_certification_id !~ '^cert_[0-9a-f]{32}$'
     OR p_lease_token_sha256 IS NULL OR p_lease_token_sha256 !~ '^sha256:[0-9a-f]{64}$'
     OR p_receipt_sha256 IS NULL OR p_receipt_sha256 !~ '^[0-9a-f]{64}$'
     OR p_session_date IS NULL OR p_quote_calls <> 1
     OR p_quote_calls IS NULL OR p_brokerage_calls IS NULL OR p_token_calls IS NULL
     OR p_brokerage_calls <> 7 OR p_token_calls NOT BETWEEN 0 AND 1 THEN
    RAISE EXCEPTION 'mock certification completion invalid' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'COMPLETE_MOCK_CERTIFICATION', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  UPDATE public.user_broker_credential_certification_attempts
  SET status = 'PASS', lease_token_sha256 = NULL, receipt_sha256 = p_receipt_sha256,
      failure_code = NULL, session_date = p_session_date, quote_calls = p_quote_calls,
      brokerage_calls = p_brokerage_calls, token_calls = p_token_calls,
      updated_at = statement_timestamp(), completed_at = statement_timestamp()
  WHERE certification_id = p_certification_id AND owner_user_id = p_owner_user_id
    AND account_id = p_account_id AND credential_revision = p_revision
    AND status = 'RUNNING' AND lease_token_sha256 = p_lease_token_sha256;
  GET DIAGNOSTICS completed = ROW_COUNT;
  IF completed <> 1 THEN
    RAISE EXCEPTION 'mock certification lease changed' USING ERRCODE = '40001';
  END IF;
  UPDATE public.user_broker_credentials
  SET credential_state = 'CERTIFIED', updated_at = statement_timestamp()
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK'
    AND account_id = p_account_id AND revision = p_revision AND credential_state = 'CONNECTED';
  GET DIAGNOSTICS completed = ROW_COUNT;
  IF completed <> 1 THEN
    RAISE EXCEPTION 'mock certification credential changed' USING ERRCODE = '40001';
  END IF;
  INSERT INTO public.audit_logs(audit_log_id,user_id,action,target_type,target_id,payload_json)
  VALUES (
    'aud_mock_cert_pass_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id,'MOCK_CREDENTIAL_CERTIFIED','BROKER_CREDENTIAL',p_account_id,
    pg_catalog.jsonb_build_object('certificationId',p_certification_id,'receiptSha256',p_receipt_sha256,
      'sessionDate',p_session_date,'quoteCalls',p_quote_calls,'brokerageCalls',p_brokerage_calls,'tokenCalls',p_token_calls)
  );
  RETURN 'CERTIFIED';
END;
$complete_bound_mock_certification_v1$;
ALTER FUNCTION public.complete_bound_mock_certification_v1(text,text,bigint,text,text,text,date,integer,integer,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.complete_bound_mock_certification_v1(text,text,bigint,text,text,text,date,integer,integer,integer)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.complete_bound_mock_certification_v1(text,text,bigint,text,text,text,date,integer,integer,integer)
  TO decision_app;

CREATE FUNCTION public.finish_bound_mock_certification_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint, p_certification_id text,
  p_lease_token_sha256 text, p_status text, p_failure_code text,
  p_session_date date, p_quote_calls integer, p_brokerage_calls integer, p_token_calls integer
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $finish_bound_mock_certification_v1$
DECLARE completed integer;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_account_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1
     OR p_certification_id IS NULL OR p_certification_id !~ '^cert_[0-9a-f]{32}$'
     OR p_lease_token_sha256 IS NULL OR p_lease_token_sha256 !~ '^sha256:[0-9a-f]{64}$'
     OR p_status IS NULL OR p_status NOT IN ('FAILED','RECOVERY_REQUIRED')
     OR p_failure_code IS NULL OR p_failure_code NOT IN (
       'MARKET_CLOSED','QUOTE_INVALID','BUYABLE_UNAVAILABLE','PROVIDER_FAILED',
       'TEST_ORDER_RECOVERED','TEST_ORDER_UNCERTAIN','EXECUTION_FILLED','BALANCE_CHANGED'
     )
     OR p_session_date IS NULL OR p_quote_calls IS NULL OR p_quote_calls NOT BETWEEN 0 AND 1
     OR p_brokerage_calls IS NULL OR p_brokerage_calls NOT BETWEEN 0 AND 7
     OR p_token_calls IS NULL OR p_token_calls NOT BETWEEN 0 AND 1 THEN
    RAISE EXCEPTION 'mock certification failure receipt invalid' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'FINISH_MOCK_CERTIFICATION', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  UPDATE public.user_broker_credential_certification_attempts
  SET status = p_status, lease_token_sha256 = NULL, failure_code = p_failure_code,
      session_date = p_session_date, quote_calls = p_quote_calls,
      brokerage_calls = p_brokerage_calls, token_calls = p_token_calls,
      updated_at = statement_timestamp(), completed_at =
        CASE WHEN p_status = 'FAILED' THEN statement_timestamp() ELSE NULL END
  WHERE certification_id = p_certification_id AND owner_user_id = p_owner_user_id
    AND account_id = p_account_id AND credential_revision = p_revision
    AND status = 'RUNNING' AND lease_token_sha256 = p_lease_token_sha256;
  GET DIAGNOSTICS completed = ROW_COUNT;
  IF completed <> 1 THEN
    RAISE EXCEPTION 'mock certification lease changed' USING ERRCODE = '40001';
  END IF;
  INSERT INTO public.audit_logs(audit_log_id,user_id,action,target_type,target_id,payload_json)
  VALUES (
    'aud_mock_cert_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id,'MOCK_CREDENTIAL_CERTIFICATION_' || p_status,'BROKER_CREDENTIAL',p_account_id,
    pg_catalog.jsonb_build_object('certificationId',p_certification_id,'failureCode',p_failure_code,
      'sessionDate',p_session_date,'quoteCalls',p_quote_calls,'brokerageCalls',p_brokerage_calls,'tokenCalls',p_token_calls)
  );
  RETURN p_status;
END;
$finish_bound_mock_certification_v1$;
ALTER FUNCTION public.finish_bound_mock_certification_v1(text,text,bigint,text,text,text,text,date,integer,integer,integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.finish_bound_mock_certification_v1(text,text,bigint,text,text,text,text,date,integer,integer,integer)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.finish_bound_mock_certification_v1(text,text,bigint,text,text,text,text,date,integer,integer,integer)
  TO decision_app;

CREATE FUNCTION public.acknowledge_bound_mock_certification_recovery_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $acknowledge_bound_mock_certification_recovery_v1$
DECLARE attempt public.user_broker_credential_certification_attempts%ROWTYPE;
DECLARE credential public.user_broker_credentials%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_account_id IS NULL OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1 THEN
    RAISE EXCEPTION 'mock certification recovery actor denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'ACK_MOCK_CERTIFICATION_RECOVERY', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT * INTO credential FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF credential.owner_user_id IS NULL OR credential.account_id IS DISTINCT FROM p_account_id
     OR credential.revision IS DISTINCT FROM p_revision OR credential.credential_state <> 'CONNECTED' THEN
    RAISE EXCEPTION 'mock certification recovery credential changed' USING ERRCODE = '40001';
  END IF;
  SELECT * INTO attempt FROM public.user_broker_credential_certification_attempts
  WHERE owner_user_id = p_owner_user_id AND account_id = p_account_id
    AND credential_revision = p_revision AND status = 'RECOVERY_REQUIRED'
  ORDER BY started_at DESC LIMIT 1 FOR UPDATE;
  IF attempt.certification_id IS NULL THEN
    RAISE EXCEPTION 'mock certification recovery is not pending' USING ERRCODE = '40001';
  END IF;
  UPDATE public.user_broker_credential_certification_attempts
  SET status = 'FAILED', failure_code = 'TEST_ORDER_RECOVERED',
      lease_token_sha256 = NULL, updated_at = statement_timestamp(), completed_at = statement_timestamp()
  WHERE certification_id = attempt.certification_id;
  INSERT INTO public.audit_logs(audit_log_id,user_id,action,target_type,target_id,payload_json)
  VALUES (
    'aud_mock_cert_ack_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id,'MOCK_CREDENTIAL_CERTIFICATION_RECOVERY_ACKNOWLEDGED','BROKER_CREDENTIAL',p_account_id,
    pg_catalog.jsonb_build_object('certificationId',attempt.certification_id,'revision',p_revision)
  );
  RETURN 'FAILED';
END;
$acknowledge_bound_mock_certification_recovery_v1$;
ALTER FUNCTION public.acknowledge_bound_mock_certification_recovery_v1(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.acknowledge_bound_mock_certification_recovery_v1(text,text,bigint)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.acknowledge_bound_mock_certification_recovery_v1(text,text,bigint)
  TO decision_app;

CREATE FUNCTION public.read_bound_mock_broker_summary_v3(p_owner_user_id text)
RETURNS TABLE(
  account_id text, credential_state text, revision bigint, app_key_last4 text, account_no_last4 text,
  certification_status text, certification_failure_code text, certification_session_date date
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_bound_mock_broker_summary_v3$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'mock credential actor is invalid' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'READ_MOCK_CREDENTIAL_SUMMARY', 'OWNER', p_owner_user_id,
    'sha256:' || pg_catalog.encode(public.digest(p_owner_user_id, 'sha256'), 'hex')
  );
  RETURN QUERY
  SELECT item.account_id, item.credential_state, item.revision, item.app_key_last4, item.account_no_last4,
         COALESCE(attempt.status, CASE WHEN item.credential_state='CERTIFIED' THEN 'PASS' ELSE 'NOT_STARTED' END),
         attempt.failure_code, attempt.session_date
  FROM public.user_broker_credentials item
  LEFT JOIN LATERAL (
    SELECT history.status, history.failure_code, history.session_date
    FROM public.user_broker_credential_certification_attempts history
    WHERE history.owner_user_id = item.owner_user_id AND history.account_id = item.account_id
      AND history.credential_revision = item.revision
    ORDER BY history.started_at DESC LIMIT 1
  ) attempt ON true
  WHERE item.owner_user_id = p_owner_user_id AND item.brokerage_mode = 'KIS_MOCK'
    AND item.account_id IS NOT NULL;
END;
$read_bound_mock_broker_summary_v3$;
ALTER FUNCTION public.read_bound_mock_broker_summary_v3(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_bound_mock_broker_summary_v3(text)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.read_bound_mock_broker_summary_v3(text) TO decision_app;
DROP FUNCTION public.read_bound_mock_broker_summary_v2(text);
