-- Disconnect stops new full-product brokerage sessions immediately. Existing
-- unresolved orders retain their encrypted credential for cancellation and
-- reconciliation; only a later confirmed clear state can remove the row.
CREATE FUNCTION public.guard_mock_credential_rotation_pending_v1()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $guard_mock_credential_rotation_pending_v1$
DECLARE owner_id text := OLD.owner_user_id;
DECLARE bound_account_id text := OLD.account_id;
DECLARE replaces_credential boolean;
BEGIN
  IF OLD.brokerage_mode <> 'KIS_MOCK' OR bound_account_id IS NULL THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
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
  END IF;
  IF replaces_credential AND EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id = owner_id AND item.account_id = bound_account_id
      AND item.status = 'PENDING_RECONCILIATION'
  ) THEN
    RAISE EXCEPTION 'mock credential has pending provider reconciliation' USING ERRCODE = '40001';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$guard_mock_credential_rotation_pending_v1$;
ALTER FUNCTION public.guard_mock_credential_rotation_pending_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.guard_mock_credential_rotation_pending_v1()
  FROM PUBLIC, decision_app, decision_worker, decision_auth, decision_identity;
CREATE TRIGGER user_broker_credentials_pending_reconciliation_v205
  BEFORE UPDATE OR DELETE ON public.user_broker_credentials
  FOR EACH ROW EXECUTE FUNCTION public.guard_mock_credential_rotation_pending_v1();

CREATE FUNCTION public.disconnect_bound_mock_broker_credential_v1(
  p_owner_user_id text, p_account_id text, p_revision bigint
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $disconnect_bound_mock_broker_credential_v1$
DECLARE current_row public.user_broker_credentials%ROWTYPE;
DECLARE pending_recovery boolean;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_owner_user_id IS NULL OR p_account_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_revision IS NULL OR p_revision < 1 THEN
    RAISE EXCEPTION 'mock disconnect actor denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'DISCONNECT_MOCK_CREDENTIAL', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT * INTO current_row FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF current_row.owner_user_id IS NULL OR current_row.account_id IS DISTINCT FROM p_account_id
     OR current_row.revision IS DISTINCT FROM p_revision THEN
    RAISE EXCEPTION 'mock disconnect credential changed' USING ERRCODE = '40001';
  END IF;
  IF current_row.credential_state <> 'DISCONNECTING' THEN
    UPDATE public.user_broker_credentials
    SET credential_state = 'DISCONNECTING', updated_at = statement_timestamp()
    WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK';
    INSERT INTO public.audit_logs(audit_log_id, user_id, action, target_type, target_id, payload_json)
    VALUES (
      'aud_mock_disconnect_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      p_owner_user_id, 'MOCK_CREDENTIAL_DISCONNECTING', 'BROKER_CREDENTIAL', p_account_id,
      pg_catalog.jsonb_build_object('state', 'DISCONNECTING', 'revision', p_revision)
    );
  END IF;

  -- Same pending predicates as V199 rotation. Both paths take the same owner
  -- advisory lock; neither may destroy recovery material while an execution
  -- remains unconfirmed. Broader owner-wide automation check is intentional.
  pending_recovery := EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id = p_owner_user_id AND item.account_id = p_account_id
      AND item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) OR EXISTS (
    SELECT 1 FROM public.automation_portfolio_order_executions_v1 item
    JOIN public.automation_portfolio_session_snapshots_v1 snapshot ON snapshot.run_id = item.run_id
    WHERE snapshot.user_id = p_owner_user_id
      AND item.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION')
  );
  IF pending_recovery THEN RETURN 'DISCONNECTING'; END IF;

  DELETE FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK'
    AND account_id = p_account_id AND revision = p_revision;
  INSERT INTO public.audit_logs(audit_log_id, user_id, action, target_type, target_id, payload_json)
  VALUES (
    'aud_mock_removed_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id, 'MOCK_CREDENTIAL_REMOVED', 'BROKER_CREDENTIAL', p_account_id,
    pg_catalog.jsonb_build_object('state', 'REMOVED', 'revision', p_revision)
  );
  RETURN 'REMOVED';
END;
$disconnect_bound_mock_broker_credential_v1$;
ALTER FUNCTION public.disconnect_bound_mock_broker_credential_v1(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.disconnect_bound_mock_broker_credential_v1(text,text,bigint)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.disconnect_bound_mock_broker_credential_v1(text,text,bigint) TO decision_app;
