-- Internal encrypted reader. The browser and public REST responses never see
-- these bytes. Only an exact authenticated owner+account actor capability can
-- retrieve them for the existing KIS_MOCK transport.
CREATE FUNCTION public.read_bound_mock_broker_envelope_v3(
  p_owner_user_id text, p_account_id text
) RETURNS TABLE(
  credential_state text, revision bigint, kek_version text,
  wrap_nonce bytea, wrapped_dek bytea, wrap_tag bytea,
  secret_nonce bytea, secret_ciphertext bytea, secret_tag bytea,
  app_key_last4 text, account_no_last4 text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_bound_mock_broker_envelope_v3$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id IS NULL OR p_account_id IS NULL
     OR p_account_id !~ '^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'mock credential reader denied' USING ERRCODE = '42501';
  END IF;
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_owner_user_id, 'READ_MOCK_CREDENTIAL_ENVELOPE', 'BROKER_CREDENTIAL', p_account_id,
    'sha256:' || pg_catalog.encode(public.digest(p_account_id, 'sha256'), 'hex')
  );
  RETURN QUERY SELECT item.credential_state, item.revision, item.kek_version,
    item.wrap_nonce, item.wrapped_dek, item.wrap_tag,
    item.secret_nonce, item.secret_ciphertext, item.secret_tag,
    item.app_key_last4, item.account_no_last4
  FROM public.user_broker_credentials item
  WHERE item.owner_user_id = p_owner_user_id AND item.brokerage_mode = 'KIS_MOCK'
    AND item.account_id = p_account_id AND item.credential_state IN ('STORED','CONNECTED','CERTIFIED','DISCONNECTING');
END;
$read_bound_mock_broker_envelope_v3$;
ALTER FUNCTION public.read_bound_mock_broker_envelope_v3(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_bound_mock_broker_envelope_v3(text,text)
  FROM PUBLIC, decision_worker, decision_auth, decision_identity, decision_replay,
  decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.read_bound_mock_broker_envelope_v3(text,text) TO decision_app;
