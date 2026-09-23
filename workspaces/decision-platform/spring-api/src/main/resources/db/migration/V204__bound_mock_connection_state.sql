-- A provider read-only balance probe succeeds before this function is called.
-- Rotation and connection state share the V199 advisory owner lock, so a late
-- proof for an old account cannot mark a newly stored credential connected.
ALTER TABLE public.user_broker_credentials ADD COLUMN connect_last_attempt_at timestamptz;

CREATE FUNCTION public.begin_bound_mock_connection_attempt_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $begin_bound_mock_connection_attempt_v1$
DECLARE current_row public.user_broker_credentials%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_owner_user_id IS NULL OR p_account_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1 THEN
    RAISE EXCEPTION 'mock connection actor denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'BEGIN_MOCK_CONNECTION_ATTEMPT', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT * INTO current_row FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF current_row.owner_user_id IS NULL OR current_row.account_id IS DISTINCT FROM p_account_id
     OR current_row.revision IS DISTINCT FROM p_revision
     OR current_row.credential_state NOT IN ('STORED','CONNECTED','CERTIFIED')
     OR (current_row.connect_last_attempt_at IS NOT NULL
         AND current_row.connect_last_attempt_at > statement_timestamp() - interval '60 seconds') THEN
    RAISE EXCEPTION 'mock connection attempt unavailable' USING ERRCODE = '40001';
  END IF;
  UPDATE public.user_broker_credentials
  SET connect_last_attempt_at = statement_timestamp()
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK';
END;
$begin_bound_mock_connection_attempt_v1$;
ALTER FUNCTION public.begin_bound_mock_connection_attempt_v1(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.begin_bound_mock_connection_attempt_v1(text,text,bigint)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.begin_bound_mock_connection_attempt_v1(text,text,bigint) TO decision_app;

CREATE FUNCTION public.mark_bound_mock_broker_connected_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $mark_bound_mock_broker_connected_v1$
DECLARE current_row public.user_broker_credentials%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_owner_user_id IS NULL OR p_account_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1 THEN
    RAISE EXCEPTION 'mock connection actor denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'MARK_MOCK_CREDENTIAL_CONNECTED', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT * INTO current_row FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF current_row.owner_user_id IS NULL OR current_row.account_id IS DISTINCT FROM p_account_id
     OR current_row.revision IS DISTINCT FROM p_revision
     OR current_row.credential_state NOT IN ('STORED','CONNECTED','CERTIFIED')
     OR current_row.connect_last_attempt_at IS NULL
     OR current_row.connect_last_attempt_at < statement_timestamp() - interval '90 seconds' THEN
    RAISE EXCEPTION 'mock connection credential changed' USING ERRCODE = '40001';
  END IF;
  IF current_row.credential_state = 'STORED' THEN
    UPDATE public.user_broker_credentials SET credential_state = 'CONNECTED', updated_at = statement_timestamp()
    WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK';
    INSERT INTO public.audit_logs(audit_log_id, user_id, action, target_type, target_id, payload_json)
    VALUES (
      'aud_mock_connect_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      p_owner_user_id, 'MOCK_CREDENTIAL_CONNECTED', 'BROKER_CREDENTIAL', p_account_id,
      pg_catalog.jsonb_build_object('state', 'CONNECTED', 'revision', p_revision)
    );
    RETURN 'CONNECTED';
  END IF;
  RETURN current_row.credential_state;
END;
$mark_bound_mock_broker_connected_v1$;
ALTER FUNCTION public.mark_bound_mock_broker_connected_v1(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.mark_bound_mock_broker_connected_v1(text,text,bigint)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.mark_bound_mock_broker_connected_v1(text,text,bigint) TO decision_app;
