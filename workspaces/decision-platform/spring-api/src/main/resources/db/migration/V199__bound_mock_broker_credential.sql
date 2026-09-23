-- V184 reserved encrypted storage but had no account binding or runtime reader.
-- This forward migration makes the MOCK credential's owner+account scope explicit.
-- Full-product startup requires the dedicated brokerage KEK and this bound path.

ALTER TABLE public.user_broker_credentials
  ADD COLUMN account_id text,
  ADD COLUMN credential_state text NOT NULL DEFAULT 'STORED',
  ADD COLUMN revision bigint NOT NULL DEFAULT 1,
  ADD CONSTRAINT user_broker_account_id_shape_v199 CHECK (
    account_id IS NULL OR account_id ~ '^acct_[0-9a-f]{32}$'
  ),
  ADD CONSTRAINT user_broker_state_v199 CHECK (credential_state IN ('STORED','CONNECTED','CERTIFIED','DISCONNECTING')),
  ADD CONSTRAINT user_broker_revision_v199 CHECK (revision > 0);
CREATE UNIQUE INDEX user_broker_credentials_account_id_v199
  ON public.user_broker_credentials(account_id) WHERE account_id IS NOT NULL;

-- V184 functions were never called by an application. Remove their unbound
-- write/read/delete entrypoints; an old row remains encrypted but cannot be used.
DROP FUNCTION public.put_user_broker_credential_v1(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
);
DROP FUNCTION public.delete_user_broker_credential_v1(text, text);
DROP FUNCTION public.read_user_broker_credential_summary_v1(text);
DROP FUNCTION public.read_user_broker_credential_v1(text, text);

CREATE FUNCTION public.put_bound_mock_broker_credential_v2(
  p_owner_user_id text, p_account_id text, p_kek_version text,
  p_wrap_nonce bytea, p_wrapped_dek bytea, p_wrap_tag bytea,
  p_secret_nonce bytea, p_secret_ciphertext bytea, p_secret_tag bytea,
  p_app_key_last4 text, p_account_no_last4 text
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $put_bound_mock_broker_credential_v2$
DECLARE previous public.user_broker_credentials%ROWTYPE;
DECLARE control_state_now text;
DECLARE new_revision bigint;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_account_id !~ '^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'mock credential actor is invalid' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'PUT_MOCK_CREDENTIAL', 'OWNER', p_owner_user_id,
    'sha256:' || pg_catalog.encode(public.digest(p_owner_user_id, 'sha256'), 'hex')
  );
  -- Order submission must take the same lock before the public order gate opens.
  PERFORM pg_catalog.pg_advisory_xact_lock(199, pg_catalog.hashtext(p_owner_user_id));
  SELECT control_state INTO control_state_now FROM public.automation_control
  WHERE user_id = p_owner_user_id FOR UPDATE;
  IF control_state_now = 'ARMED' THEN
    RAISE EXCEPTION 'mock credential cannot change while armed' USING ERRCODE = '40001';
  END IF;
  SELECT * INTO previous FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK' FOR UPDATE;
  IF previous.owner_user_id IS NOT NULL AND previous.account_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id = p_owner_user_id AND item.account_id = previous.account_id
      AND item.status IN ('SUBMITTED','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
  ) THEN
    RAISE EXCEPTION 'mock credential has pending reconciliation' USING ERRCODE = '40001';
  END IF;
  IF previous.owner_user_id IS NOT NULL AND previous.account_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.automation_portfolio_order_executions_v1 item
    JOIN public.automation_portfolio_session_snapshots_v1 snapshot ON snapshot.run_id = item.run_id
    WHERE snapshot.user_id = p_owner_user_id
      AND item.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION')
  ) THEN
    RAISE EXCEPTION 'mock credential has pending execution' USING ERRCODE = '40001';
  END IF;
  INSERT INTO public.user_broker_credentials(
    owner_user_id, brokerage_mode, account_id, credential_state, revision,
    kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    secret_nonce, secret_ciphertext, secret_tag,
    app_key_last4, account_no_last4, created_at, updated_at
  ) VALUES (
    p_owner_user_id, 'KIS_MOCK', p_account_id, 'STORED', 1,
    p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_secret_nonce, p_secret_ciphertext, p_secret_tag,
    p_app_key_last4, p_account_no_last4, statement_timestamp(), statement_timestamp()
  ) ON CONFLICT (owner_user_id, brokerage_mode) DO UPDATE SET
    account_id = excluded.account_id,
    credential_state = 'STORED',
    revision = public.user_broker_credentials.revision + 1,
    kek_version = excluded.kek_version,
    wrap_nonce = excluded.wrap_nonce,
    wrapped_dek = excluded.wrapped_dek,
    wrap_tag = excluded.wrap_tag,
    secret_nonce = excluded.secret_nonce,
    secret_ciphertext = excluded.secret_ciphertext,
    secret_tag = excluded.secret_tag,
    app_key_last4 = excluded.app_key_last4,
    account_no_last4 = excluded.account_no_last4,
    updated_at = statement_timestamp();
  SELECT revision INTO new_revision FROM public.user_broker_credentials
  WHERE owner_user_id = p_owner_user_id AND brokerage_mode = 'KIS_MOCK';
  INSERT INTO public.audit_logs(audit_log_id, user_id, action, target_type, target_id, payload_json)
  VALUES (
    'aud_mock_credential_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_owner_user_id, 'MOCK_CREDENTIAL_STORED', 'BROKER_CREDENTIAL', p_account_id,
    pg_catalog.jsonb_build_object('state', 'STORED', 'revision', new_revision)
  );
END;
$put_bound_mock_broker_credential_v2$;
ALTER FUNCTION public.put_bound_mock_broker_credential_v2(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.put_bound_mock_broker_credential_v2(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
) FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.put_bound_mock_broker_credential_v2(
  text, text, text, bytea, bytea, bytea, bytea, bytea, bytea, text, text
) TO decision_app;

CREATE FUNCTION public.read_bound_mock_broker_summary_v2(p_owner_user_id text)
RETURNS TABLE(account_id text, credential_state text, revision bigint, app_key_last4 text, account_no_last4 text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_bound_mock_broker_summary_v2$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id THEN
    RAISE EXCEPTION 'mock credential actor is invalid' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'READ_MOCK_CREDENTIAL_SUMMARY', 'OWNER', p_owner_user_id,
    'sha256:' || pg_catalog.encode(public.digest(p_owner_user_id, 'sha256'), 'hex')
  );
  RETURN QUERY SELECT item.account_id, item.credential_state, item.revision,
    item.app_key_last4, item.account_no_last4
  FROM public.user_broker_credentials item
  WHERE item.owner_user_id = p_owner_user_id AND item.brokerage_mode = 'KIS_MOCK'
    AND item.account_id IS NOT NULL;
END;
$read_bound_mock_broker_summary_v2$;
ALTER FUNCTION public.read_bound_mock_broker_summary_v2(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_bound_mock_broker_summary_v2(text)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.read_bound_mock_broker_summary_v2(text) TO decision_app;

GRANT SELECT ON TABLE public.orders, public.automation_control,
  public.automation_portfolio_order_executions_v1,
  public.automation_portfolio_session_snapshots_v1 TO flyway;
