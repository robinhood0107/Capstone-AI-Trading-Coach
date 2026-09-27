-- Preserve account lineage, keep offline replay history out of live KIS holdings, and make
-- stop/re-arm state transitions agree across control, schedule, position and order projections.
SET LOCAL row_security = on;

-- Flyway performs an evidence-gated data repair below. Restore FORCE RLS before commit.
ALTER TABLE public.user_broker_credentials NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_position_observations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_account_lineage NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_schedule NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.orders NO FORCE ROW LEVEL SECURITY;

ALTER TABLE public.automation_positions
  ADD COLUMN source_account_id text CHECK (
    source_account_id IS NULL OR source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'
  ),
  ADD COLUMN data_quality_state text NOT NULL DEFAULT 'VERIFIED'
    CHECK (data_quality_state IN ('VERIFIED','HISTORICAL_PAPER','QUARANTINED_UNVERIFIED'));

ALTER TABLE public.automation_runs
  ADD COLUMN source_account_id text CHECK (
    source_account_id IS NULL OR source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'
  );

ALTER TABLE public.orders
  ADD COLUMN source_account_id text CHECK (
    source_account_id IS NULL OR source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'
  ),
  ADD COLUMN source_account_scope_hash text CHECK (
    source_account_scope_hash IS NULL OR source_account_scope_hash ~ '^[0-9a-f]{64}$'
  );

-- HALTED_MISMATCH is a quarantine state, not a fabricated sell/close. It may retain the old
-- closed_at and quantity/PnL fields while ordinary active and realized-PnL queries exclude it.
ALTER TABLE public.automation_positions
  DROP CONSTRAINT automation_positions_check1,
  ADD CONSTRAINT automation_positions_closed_at_v218_check CHECK (
    (status <> 'CLOSED' OR closed_at IS NOT NULL)
    AND (status = 'HALTED_MISMATCH' OR status = 'CLOSED' OR closed_at IS NULL)
  ),
  DROP CONSTRAINT automation_position_v2_shape_check,
  ADD CONSTRAINT automation_position_v2_shape_v218_check CHECK (
    (
      policy_id IS NULL AND policy_version IS NULL AND entry_order_id IS NULL
      AND entry_ordered_quantity IS NULL AND entry_filled_quantity IS NULL
      AND entry_unfilled_quantity IS NULL AND entry_average_fill_price_krw IS NULL
      AND stop_loss_bps IS NULL AND take_profit_bps IS NULL
    ) OR (
      policy_id ~ '^auto_pol_[0-9a-f]{32}$' AND policy_version >= 1
      AND entry_order_id ~ '^ord_mock_[0-9a-f]{32}$'
      AND entry_ordered_quantity > 0 AND entry_filled_quantity > 0
      AND entry_unfilled_quantity >= 0
      AND entry_ordered_quantity = entry_filled_quantity + entry_unfilled_quantity
      AND entry_average_fill_price_krw > 0
      AND stop_loss_bps BETWEEN 100 AND 1500
      AND take_profit_bps BETWEEN 200 AND 3000
      AND take_profit_bps > stop_loss_bps
      AND (((status = 'CLOSED') = (quantity = 0)) OR status = 'HALTED_MISMATCH')
      AND exit_filled_quantity >= 0
      AND quantity + exit_filled_quantity = entry_filled_quantity
      AND (exit_average_fill_price_krw IS NULL OR exit_average_fill_price_krw > 0)
      AND (exit_reason IS NULL OR exit_reason IN (
        'STOP_LOSS','ATR_TRAILING','MODEL_SELL','TAKE_PROFIT','MAX_HOLDING_SESSIONS'
      ))
    )
  );

CREATE TABLE public.full_owner_account_identity_v218 (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  identity_fingerprint text NOT NULL CHECK (identity_fingerprint ~ '^[0-9a-f]{64}$'),
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (owner_user_id,identity_fingerprint),
  UNIQUE (owner_user_id,account_id)
);
ALTER TABLE public.full_owner_account_identity_v218 OWNER TO flyway;
GRANT SELECT,INSERT,UPDATE ON public.full_owner_account_identity_v218 TO flyway;
ALTER TABLE public.full_owner_account_identity_v218 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_account_identity_v218 FORCE ROW LEVEL SECURITY;
CREATE POLICY full_owner_account_identity_v218_scope
  ON public.full_owner_account_identity_v218 TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND EXISTS (
      SELECT 1 FROM public.actor_rls_scope_v1 scope
      WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
        AND scope.actor_user_id=owner_user_id
        AND (
          (scope.operation='PUT_MOCK_CREDENTIAL' AND scope.target_kind='OWNER'
           AND scope.target_id=owner_user_id)
          OR (scope.operation='READ_AUTOMATION_STATUS' AND scope.target_kind='AUTOMATION'
              AND scope.target_id=owner_user_id)
          OR (scope.operation='DISCONNECT_MOCK_CREDENTIAL'
              AND scope.target_kind='BROKER_CREDENTIAL')
        )
        AND scope.expires_at>statement_timestamp()
    )
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND EXISTS (
      SELECT 1 FROM public.actor_rls_scope_v1 scope
      WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
        AND scope.actor_user_id=owner_user_id
        AND (
          (scope.operation='PUT_MOCK_CREDENTIAL' AND scope.target_kind='OWNER'
           AND scope.target_id=owner_user_id)
          OR (scope.operation='READ_AUTOMATION_STATUS' AND scope.target_kind='AUTOMATION'
              AND scope.target_id=owner_user_id)
          OR (scope.operation='DISCONNECT_MOCK_CREDENTIAL'
              AND scope.target_kind='BROKER_CREDENTIAL')
        )
        AND scope.expires_at>statement_timestamp()
    )
  );

CREATE FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(p_owner_user_id text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_owner_mock_credential_state_for_automation_v218$
DECLARE credential_state_value text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.actor_rls_scope_v1 scope
       WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
         AND scope.actor_user_id=p_owner_user_id
         AND scope.operation='READ_AUTOMATION_STATUS'
         AND scope.target_kind='AUTOMATION' AND scope.target_id=p_owner_user_id
         AND scope.expires_at>statement_timestamp()
     ) THEN
    RAISE EXCEPTION 'automation credential state scope denied' USING ERRCODE='42501';
  END IF;
  SELECT credential.credential_state INTO credential_state_value
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=p_owner_user_id
    AND credential.brokerage_mode='KIS_MOCK';
  RETURN credential_state_value;
END
$p1_read_owner_mock_credential_state_for_automation_v218$;
ALTER FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text)
  TO decision_app;

-- Credential ciphertext is read only inside the server to prove full account identity before
-- reusing an internal account ID. The account number never leaves this encrypted-row envelope.
CREATE POLICY user_broker_credentials_identity_scope_v218
  ON public.user_broker_credentials FOR SELECT TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND EXISTS (
      SELECT 1 FROM public.actor_rls_scope_v1 scope
      WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
        AND scope.actor_user_id=owner_user_id
        AND (
          (scope.operation='PUT_MOCK_CREDENTIAL' AND scope.target_kind='OWNER'
           AND scope.target_id=owner_user_id)
          OR (scope.operation='READ_AUTOMATION_STATUS' AND scope.target_kind='AUTOMATION'
              AND scope.target_id=owner_user_id)
          OR (scope.operation='DISCONNECT_MOCK_CREDENTIAL'
              AND scope.target_kind='BROKER_CREDENTIAL'
              AND scope.target_id=user_broker_credentials.account_id)
        )
        AND scope.expires_at>statement_timestamp()
    )
  );

CREATE FUNCTION public.p1_read_mock_credential_identity_envelope_v218(p_owner_user_id text)
RETURNS TABLE(
  account_id text,credential_state text,revision bigint,kek_version text,
  wrap_nonce bytea,wrapped_dek bytea,wrap_tag bytea,secret_nonce bytea,
  secret_ciphertext bytea,secret_tag bytea,app_key_last4 text,account_no_last4 text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_mock_credential_identity_envelope_v218$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.actor_rls_scope_v1 scope
       WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
         AND scope.actor_user_id=p_owner_user_id
         AND (
           (scope.operation='PUT_MOCK_CREDENTIAL' AND scope.target_kind='OWNER'
            AND scope.target_id=p_owner_user_id)
           OR (scope.operation='DISCONNECT_MOCK_CREDENTIAL'
               AND scope.target_kind='BROKER_CREDENTIAL')
         )
         AND scope.expires_at>statement_timestamp()
     ) THEN
    RAISE EXCEPTION 'mock credential identity envelope scope denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT credential.account_id,credential.credential_state,credential.revision,credential.kek_version,
         credential.wrap_nonce,credential.wrapped_dek,credential.wrap_tag,
         credential.secret_nonce,credential.secret_ciphertext,credential.secret_tag,
         credential.app_key_last4,credential.account_no_last4
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=p_owner_user_id AND credential.brokerage_mode='KIS_MOCK';
END
$p1_read_mock_credential_identity_envelope_v218$;
ALTER FUNCTION public.p1_read_mock_credential_identity_envelope_v218(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_mock_credential_identity_envelope_v218(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_mock_credential_identity_envelope_v218(text) TO decision_app;

CREATE FUNCTION public.p1_resolve_or_bind_mock_account_identity_v218(
  p_owner_user_id text,p_identity_fingerprint text,p_verified_current_account_id text,
  p_new_account_id text
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_resolve_or_bind_mock_account_identity_v218$
DECLARE existing_account text;
DECLARE resolved_account text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id
     OR p_identity_fingerprint !~ '^[0-9a-f]{64}$'
     OR p_new_account_id !~ '^acct_[0-9a-f]{32}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.actor_rls_scope_v1 scope
       WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
         AND scope.actor_user_id=p_owner_user_id AND scope.operation='PUT_MOCK_CREDENTIAL'
         AND scope.target_kind='OWNER' AND scope.target_id=p_owner_user_id
         AND scope.expires_at>statement_timestamp()
     ) THEN
    RAISE EXCEPTION 'mock account identity scope denied' USING ERRCODE='42501';
  END IF;
  SELECT identity.account_id INTO existing_account
  FROM public.full_owner_account_identity_v218 identity
  WHERE identity.owner_user_id=p_owner_user_id
    AND identity.identity_fingerprint=p_identity_fingerprint;
  IF existing_account IS NOT NULL THEN RETURN existing_account; END IF;

  IF p_verified_current_account_id IS NOT NULL THEN
    IF p_verified_current_account_id !~ '^acct_[0-9a-f]{32}$'
       OR NOT EXISTS (
         SELECT 1 FROM public.user_broker_credentials credential
         WHERE credential.owner_user_id=p_owner_user_id
           AND credential.brokerage_mode='KIS_MOCK'
           AND credential.account_id=p_verified_current_account_id
       ) THEN
      RAISE EXCEPTION 'mock account identity candidate is not owner-bound' USING ERRCODE='42501';
    END IF;
    resolved_account:=p_verified_current_account_id;
  ELSE
    resolved_account:=p_new_account_id;
  END IF;

  INSERT INTO public.full_owner_account_identity_v218(
    owner_user_id,identity_fingerprint,account_id
  ) VALUES (p_owner_user_id,p_identity_fingerprint,resolved_account);
  RETURN resolved_account;
END
$p1_resolve_or_bind_mock_account_identity_v218$;
ALTER FUNCTION public.p1_resolve_or_bind_mock_account_identity_v218(text,text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_resolve_or_bind_mock_account_identity_v218(text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_resolve_or_bind_mock_account_identity_v218(text,text,text,text)
  TO decision_app;

CREATE FUNCTION public.p1_register_mock_account_identity_before_disconnect_v218(
  p_owner_user_id text,p_account_id text,p_identity_fingerprint text
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_register_mock_account_identity_before_disconnect_v218$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id
     OR p_account_id !~ '^acct_[0-9a-f]{32}$'
     OR p_identity_fingerprint !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.actor_rls_scope_v1 scope
       WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
         AND scope.actor_user_id=p_owner_user_id AND scope.operation='DISCONNECT_MOCK_CREDENTIAL'
         AND scope.target_kind='BROKER_CREDENTIAL' AND scope.target_id=p_account_id
         AND scope.expires_at>statement_timestamp()
     )
     OR NOT EXISTS (
       SELECT 1 FROM public.user_broker_credentials credential
       WHERE credential.owner_user_id=p_owner_user_id AND credential.brokerage_mode='KIS_MOCK'
         AND credential.account_id=p_account_id
     ) THEN
    RAISE EXCEPTION 'mock account disconnect identity scope denied' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.full_owner_account_identity_v218(
    owner_user_id,identity_fingerprint,account_id
  ) VALUES (p_owner_user_id,p_identity_fingerprint,p_account_id)
  ON CONFLICT (owner_user_id,identity_fingerprint) DO UPDATE
  SET updated_at=statement_timestamp()
  WHERE public.full_owner_account_identity_v218.account_id=excluded.account_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'mock account identity mapping conflict' USING ERRCODE='23505';
  END IF;
  RETURN true;
END
$p1_register_mock_account_identity_before_disconnect_v218$;
ALTER FUNCTION public.p1_register_mock_account_identity_before_disconnect_v218(text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_register_mock_account_identity_before_disconnect_v218(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_register_mock_account_identity_before_disconnect_v218(text,text,text)
  TO decision_app;

-- Retire the older last-four-only chooser. Every current save path now uses the full keyed identity.
REVOKE ALL ON FUNCTION public.resolve_bound_mock_account_reuse_v1(text,text)
  FROM PUBLIC,decision_app;

CREATE TABLE public.full_owner_account_aliases_v218 (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  canonical_account_id text NOT NULL CHECK (canonical_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  source_balance_observation_id text NOT NULL
    REFERENCES public.portfolio_balance_observations(observation_id) ON DELETE RESTRICT,
  canonical_balance_observation_id text NOT NULL
    REFERENCES public.portfolio_balance_observations(observation_id) ON DELETE RESTRICT,
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  linked_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (owner_user_id,source_account_id),
  CHECK (source_account_id <> canonical_account_id),
  CHECK (source_balance_observation_id <> canonical_balance_observation_id)
);
ALTER TABLE public.full_owner_account_aliases_v218 OWNER TO flyway;
GRANT SELECT,INSERT ON public.full_owner_account_aliases_v218 TO flyway;

CREATE TABLE public.automation_position_history_events_v218 (
  position_id text PRIMARY KEY REFERENCES public.automation_positions(position_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  original_status text NOT NULL CHECK (original_status IN ('OPEN','EXIT_PENDING','CLOSED')),
  reason_code text NOT NULL CHECK (reason_code IN (
    'DETERMINISTIC_INTERNAL_PAPER_REPLAY','TEAM_A_ACCEPTANCE_PAPER_FIXTURE'
  )),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.automation_position_history_events_v218 OWNER TO flyway;
GRANT SELECT,INSERT ON public.automation_position_history_events_v218 TO flyway;
CREATE TRIGGER automation_position_history_events_append_only_v218
  BEFORE UPDATE OR DELETE ON public.automation_position_history_events_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TABLE public.automation_paper_history_run_links_v218 (
  run_id text PRIMARY KEY REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  source_code text NOT NULL CHECK (source_code IN ('OFFLINE_HISTORY_REPLAY','TEAM_A_ACCEPTANCE_PAPER')),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  linked_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.automation_paper_history_run_links_v218 OWNER TO flyway;
REVOKE ALL ON public.automation_paper_history_run_links_v218
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT SELECT,INSERT ON public.automation_paper_history_run_links_v218 TO flyway;
CREATE TRIGGER automation_paper_history_run_links_append_only_v218
  BEFORE UPDATE OR DELETE ON public.automation_paper_history_run_links_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TABLE public.automation_paper_account_rekey_events_v218 (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  paper_account_id text NOT NULL CHECK (paper_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  source_code text NOT NULL CHECK (source_code='TEAM_A_ACCEPTANCE_PAPER_FIXTURE'),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  rekeyed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (owner_user_id,source_account_id),
  CHECK (source_account_id<>paper_account_id)
);
ALTER TABLE public.automation_paper_account_rekey_events_v218 OWNER TO flyway;
REVOKE ALL ON public.automation_paper_account_rekey_events_v218
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT SELECT,INSERT ON public.automation_paper_account_rekey_events_v218 TO flyway;
CREATE TRIGGER automation_paper_account_rekey_events_append_only_v218
  BEFORE UPDATE OR DELETE ON public.automation_paper_account_rekey_events_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TABLE public.automation_order_integrity_events_v218 (
  order_id text PRIMARY KEY REFERENCES public.orders(order_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  reason_code text NOT NULL CHECK (reason_code='LEGACY_UNLINKED_ORDER_NO_RECONCILIATION'),
  disposition_code text NOT NULL CHECK (disposition_code='USER_REQUESTED_QUARANTINE_BLOCK_START'),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.automation_order_integrity_events_v218 OWNER TO flyway;
GRANT SELECT,INSERT ON public.automation_order_integrity_events_v218 TO flyway;
CREATE TRIGGER automation_order_integrity_events_append_only_v218
  BEFORE UPDATE OR DELETE ON public.automation_order_integrity_events_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TABLE public.full_owner_account_link_events_v218 (
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  source_account_id text NOT NULL CHECK (source_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  canonical_account_id text NOT NULL CHECK (canonical_account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  linked_position_count integer NOT NULL CHECK (linked_position_count >= 0),
  linked_run_count integer NOT NULL CHECK (linked_run_count >= 0),
  linked_order_count integer NOT NULL CHECK (linked_order_count >= 0),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (owner_user_id,source_account_id),
  CHECK (source_account_id <> canonical_account_id)
);
ALTER TABLE public.full_owner_account_link_events_v218 OWNER TO flyway;
GRANT SELECT,INSERT ON public.full_owner_account_link_events_v218 TO flyway;
CREATE TRIGGER full_owner_account_link_events_append_only_v218
  BEFORE UPDATE OR DELETE ON public.full_owner_account_link_events_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

-- acct_aaaa was historically used by both the owner's KIS history and the Team A paper
-- acceptance fixture. Move only the deterministic NEWS_VETOED/005930 fixture to a dedicated
-- paper ID before linking the remaining evidence-backed KIS history to the current credential.
DO $v218_rekey_team_a_paper_account_id$
DECLARE
  demo_owner text;
  source_account text:='acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
  paper_account text:='acct_f1a5e315b7c8462b9338f0cf4c5a1d20';
BEGIN
  SELECT user_id INTO demo_owner FROM public.users WHERE username='demo-user' AND status='ACTIVE';
  IF demo_owner IS NULL OR NOT EXISTS (
    SELECT 1 FROM public.paper_accounts paper
    WHERE paper.user_id=demo_owner AND paper.account_id=source_account
      AND paper.name='Team A acceptance paper' AND paper.owner_scope_hash=repeat('a',64)
  ) THEN RETURN; END IF;

  IF EXISTS (
    SELECT 1 FROM public.paper_accounts WHERE account_id=paper_account
    UNION ALL SELECT 1 FROM public.automation_control WHERE account_id=paper_account
    UNION ALL SELECT 1 FROM public.user_broker_credentials WHERE account_id=paper_account
    UNION ALL SELECT 1 FROM public.automation_positions WHERE account_id=paper_account
    UNION ALL SELECT 1 FROM public.automation_runs WHERE account_id=paper_account
    UNION ALL SELECT 1 FROM public.orders WHERE account_id=paper_account
  ) THEN
    RAISE EXCEPTION 'Team A paper account target ID is already in use' USING ERRCODE='23505';
  END IF;
  IF EXISTS (SELECT 1 FROM public.paper_positions WHERE account_id=source_account)
     OR EXISTS (SELECT 1 FROM public.paper_order_events WHERE account_id=source_account) THEN
    RAISE EXCEPTION 'Team A acceptance paper ledger needs explicit account rekey' USING ERRCODE='P1H02';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions position
    WHERE position.user_id=demo_owner AND position.account_id=source_account
      AND position.position_id='auto_pos_team_a_closed_0001'
      AND (position.symbol<>'005930' OR position.status<>'CLOSED'
           OR position.entry_session<>DATE '2026-08-18' OR position.entry_order_id IS NOT NULL)
  ) THEN
    RAISE EXCEPTION 'Team A acceptance paper fixture shape drift' USING ERRCODE='P1H02';
  END IF;

  INSERT INTO public.automation_paper_account_rekey_events_v218(
    owner_user_id,source_account_id,paper_account_id,source_code,evidence_sha256
  ) VALUES (
    demo_owner,source_account,paper_account,'TEAM_A_ACCEPTANCE_PAPER_FIXTURE',
    encode(public.digest(convert_to(
      'team-a-acceptance-paper-rekey-v1:'||demo_owner||':'||source_account||':'||paper_account||
      ':scope-a64', 'UTF8'),'sha256'),'hex')
  ) ON CONFLICT (owner_user_id,source_account_id) DO NOTHING;

  INSERT INTO public.automation_position_history_events_v218(
    position_id,owner_user_id,source_account_id,original_status,reason_code,evidence_sha256
  )
  SELECT position.position_id,demo_owner,source_account,position.status,
         'TEAM_A_ACCEPTANCE_PAPER_FIXTURE',
         encode(public.digest(convert_to(
           'team-a-acceptance-paper-position-v1:'||position.position_id||':'||
           position.symbol||':'||position.quantity||':'||position.entry_session::text||':'||
           position.status, 'UTF8'),'sha256'),'hex')
  FROM public.automation_positions position
  WHERE position.user_id=demo_owner AND position.account_id=source_account
    AND position.position_id='auto_pos_team_a_closed_0001'
  ON CONFLICT (position_id) DO NOTHING;

  UPDATE public.automation_positions position
  SET source_account_id=COALESCE(position.source_account_id,source_account),
      account_id=paper_account,data_quality_state='HISTORICAL_PAPER'
  WHERE position.user_id=demo_owner AND position.account_id=source_account
    AND position.position_id='auto_pos_team_a_closed_0001'
    AND position.symbol='005930' AND position.status='CLOSED'
    AND position.entry_session=DATE '2026-08-18' AND position.entry_order_id IS NULL;

  UPDATE public.automation_runs run
  SET account_id=paper_account
  WHERE run.user_id=demo_owner AND run.run_id='auto_run_team_a_news_veto_0001'
    AND run.brokerage_mode='INTERNAL_PAPER'
    AND (run.account_id IS NULL OR run.account_id=source_account);

  UPDATE public.paper_accounts paper
  SET account_id=paper_account,updated_at=statement_timestamp()
  WHERE paper.user_id=demo_owner AND paper.account_id=source_account
    AND paper.name='Team A acceptance paper' AND paper.owner_scope_hash=repeat('a',64);
END
$v218_rekey_team_a_paper_account_id$;

-- Link only a unique old account whose newest complete KIS snapshot is byte-for-byte equivalent
-- in cash, equity, position count and symbol/quantity set to the current credential's snapshot.
-- The old account's active bot positions must equal that complete balance and its old order/run
-- ledger must have no unresolved provider work. IDs and order/run history stay intact; source IDs
-- are retained in the new provenance columns.
DO $v218_exact_account_history_link$
DECLARE
  demo_owner text;
  canonical_account text;
  control_state text;
  current_snapshot record;
  candidate record;
  candidate_count integer:=0;
  source_account text;
  source_observation text;
  source_artifact text;
  link_evidence text;
  linked_positions integer;
  linked_runs integer;
  linked_orders integer;
BEGIN
  SELECT user_id INTO demo_owner FROM public.users WHERE username='demo-user' AND status='ACTIVE';
  IF demo_owner IS NULL THEN RETURN; END IF;

  SELECT credential.account_id
    INTO canonical_account
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=demo_owner AND credential.brokerage_mode='KIS_MOCK'
    AND credential.credential_state IN ('CONNECTED','CERTIFIED');
  IF canonical_account IS NULL THEN RETURN; END IF;

  SELECT control.control_state INTO control_state
  FROM public.automation_control control WHERE control.user_id=demo_owner;
  IF control_state IS DISTINCT FROM 'DISARMED' THEN RETURN; END IF;

  SELECT balance.observation_id,balance.artifact_hash,balance.cash_krw,
         balance.portfolio_equity_krw,balance.position_count,
         COALESCE((
           SELECT jsonb_agg(jsonb_build_array(position.symbol,position.quantity) ORDER BY position.symbol)
           FROM public.portfolio_position_observations position
           WHERE position.balance_observation_id=balance.observation_id
         ),'[]'::jsonb) AS position_set
    INTO current_snapshot
  FROM public.portfolio_balance_observations balance
  WHERE balance.owner_user_id=demo_owner AND balance.source='KIS_MOCK'
    AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
    AND balance.source_version='kis-mock-online-complete-v2'
    AND balance.account_scope_hash=substr(canonical_account,6)||repeat('0',32)
  ORDER BY balance.observed_at DESC,balance.received_at DESC,balance.observation_id DESC
  LIMIT 1;
  IF current_snapshot.observation_id IS NULL THEN RETURN; END IF;

  FOR candidate IN
    SELECT balance.observation_id,balance.artifact_hash,
           'acct_'||left(balance.account_scope_hash,32) AS source_account_id,
           COALESCE((
             SELECT jsonb_agg(jsonb_build_array(position.symbol,position.quantity) ORDER BY position.symbol)
             FROM public.portfolio_position_observations position
             WHERE position.balance_observation_id=balance.observation_id
           ),'[]'::jsonb) AS position_set
    FROM public.portfolio_balance_observations balance
    WHERE balance.owner_user_id=demo_owner AND balance.source='KIS_MOCK'
      AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
      AND balance.source_version='kis-mock-online-complete-v2'
      AND balance.account_scope_hash ~ '^[0-9a-f]{64}$'
      AND 'acct_'||left(balance.account_scope_hash,32)<>canonical_account
      AND balance.cash_krw=current_snapshot.cash_krw
      AND balance.portfolio_equity_krw=current_snapshot.portfolio_equity_krw
      AND balance.position_count=current_snapshot.position_count
      AND NOT EXISTS (
        SELECT 1 FROM public.portfolio_balance_observations newer
        WHERE newer.owner_user_id=balance.owner_user_id
          AND newer.account_scope_hash=balance.account_scope_hash
          AND newer.source='KIS_MOCK' AND newer.context_status='ACTIVE'
          AND newer.completeness='COMPLETE'
          AND newer.source_version='kis-mock-online-complete-v2'
          AND (newer.observed_at,newer.received_at,newer.observation_id)>
              (balance.observed_at,balance.received_at,balance.observation_id)
      )
  LOOP
    IF candidate.position_set IS DISTINCT FROM current_snapshot.position_set THEN CONTINUE; END IF;
    IF candidate.position_set IS DISTINCT FROM COALESCE((
      SELECT jsonb_agg(jsonb_build_array(position.symbol,position.quantity) ORDER BY position.symbol)
      FROM public.automation_positions position
      WHERE position.user_id=demo_owner AND position.account_id=candidate.source_account_id
        AND position.status IN ('OPEN','EXIT_PENDING')
        AND position.data_quality_state='VERIFIED'
    ),'[]'::jsonb) THEN CONTINUE; END IF;
    IF EXISTS (
      SELECT 1 FROM public.orders order_row
      WHERE order_row.user_id=demo_owner AND order_row.account_id=candidate.source_account_id
        AND (order_row.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED',
                                  'PARTIALLY_FILLED','CANCEL_REQUESTED')
             OR order_row.reconciliation_status='MISMATCH')
    ) THEN CONTINUE; END IF;
    IF NOT public.p1_automation_open_work_clear_v3(demo_owner,candidate.source_account_id) THEN
      CONTINUE;
    END IF;
    candidate_count:=candidate_count+1;
    source_account:=candidate.source_account_id;
    source_observation:=candidate.observation_id;
    source_artifact:=candidate.artifact_hash;
  END LOOP;

  IF candidate_count<>1 THEN RETURN; END IF;
  IF EXISTS (
    SELECT 1 FROM public.automation_positions source_position
    JOIN public.automation_positions current_position
      ON current_position.user_id=source_position.user_id
     AND current_position.account_id=canonical_account
     AND current_position.symbol=source_position.symbol
     AND current_position.status IN ('OPEN','EXIT_PENDING')
    WHERE source_position.user_id=demo_owner AND source_position.account_id=source_account
      AND source_position.status IN ('OPEN','EXIT_PENDING')
  ) THEN RETURN; END IF;

  link_evidence:=encode(public.digest(convert_to(
    demo_owner||':'||source_account||':'||canonical_account||':'||
    source_observation||':'||current_snapshot.observation_id||':'||
    source_artifact||':'||current_snapshot.artifact_hash,
    'UTF8'),'sha256'),'hex');

  INSERT INTO public.full_owner_account_aliases_v218(
    owner_user_id,source_account_id,canonical_account_id,source_balance_observation_id,
    canonical_balance_observation_id,evidence_sha256
  ) VALUES (
    demo_owner,source_account,canonical_account,source_observation,
    current_snapshot.observation_id,link_evidence
  ) ON CONFLICT (owner_user_id,source_account_id) DO NOTHING;

  UPDATE public.automation_positions position
  SET source_account_id=COALESCE(position.source_account_id,position.account_id),
      account_id=canonical_account
  WHERE position.user_id=demo_owner AND position.account_id=source_account;
  GET DIAGNOSTICS linked_positions=ROW_COUNT;

  UPDATE public.automation_runs run
  SET source_account_id=COALESCE(run.source_account_id,run.account_id),
      account_id=canonical_account
  WHERE run.user_id=demo_owner AND run.account_id=source_account;
  GET DIAGNOSTICS linked_runs=ROW_COUNT;

  UPDATE public.orders order_row
  SET source_account_id=COALESCE(order_row.source_account_id,order_row.account_id),
      source_account_scope_hash=COALESCE(order_row.source_account_scope_hash,order_row.account_scope_hash),
      account_id=canonical_account,
      account_scope_hash=substr(canonical_account,6)||repeat('0',32)
  WHERE order_row.user_id=demo_owner AND order_row.account_id=source_account;
  GET DIAGNOSTICS linked_orders=ROW_COUNT;

  INSERT INTO public.full_owner_account_link_events_v218(
    owner_user_id,source_account_id,canonical_account_id,evidence_sha256,
    linked_position_count,linked_run_count,linked_order_count
  ) VALUES (
    demo_owner,source_account,canonical_account,link_evidence,
    linked_positions,linked_runs,linked_orders
  ) ON CONFLICT (owner_user_id,source_account_id) DO NOTHING;
END
$v218_exact_account_history_link$;

-- The exact replay account and namespaced IDs identify deterministic INTERNAL_PAPER history.
-- Its seed intentionally creates simulated reservations without brokerage orders. Preserve every
-- OPEN/CLOSED value and PnL, link its runs back to that paper account, and keep it separate from
-- current KIS holdings. Only rows that match their simulated run/reservation evidence qualify.
DO $v218_link_offline_history_replay$
DECLARE
  demo_owner text;
  replay_account text:='acct_dddddddddddddddddddddddddddddddd';
  team_a_paper_account text:='acct_f1a5e315b7c8462b9338f0cf4c5a1d20';
BEGIN
  SELECT user_id INTO demo_owner FROM public.users WHERE username='demo-user' AND status='ACTIVE';
  IF demo_owner IS NULL THEN RETURN; END IF;

  INSERT INTO public.automation_paper_history_run_links_v218(
    run_id,owner_user_id,account_id,source_code,evidence_sha256
  )
  SELECT run.run_id,demo_owner,replay_account,'OFFLINE_HISTORY_REPLAY',
         encode(public.digest(convert_to(
           'p1-history-replay-v1:run:'||run.run_id||':'||run.session_date::text||':'||
           run.brokerage_mode||':'||run.state,
           'UTF8'),'sha256'),'hex')
  FROM public.automation_runs run
  WHERE run.user_id=demo_owner AND run.run_id LIKE 'auto_run_replay%'
    AND run.brokerage_mode='INTERNAL_PAPER' AND run.account_id IS NULL
    AND EXISTS (
      SELECT 1 FROM public.paper_accounts paper
      WHERE paper.user_id=demo_owner AND paper.account_id=replay_account
        AND paper.name='Offline history replay' AND paper.owner_scope_hash=repeat('d',64)
    )
  ON CONFLICT (run_id) DO NOTHING;

  UPDATE public.automation_runs run
  SET account_id=replay_account
  FROM public.automation_paper_history_run_links_v218 linked
  WHERE linked.run_id=run.run_id AND linked.owner_user_id=run.user_id
    AND run.user_id=demo_owner AND run.account_id IS NULL;

  UPDATE public.automation_runs run
  SET account_id=team_a_paper_account
  WHERE run.user_id=demo_owner AND run.run_id='auto_run_team_a_news_veto_0001'
    AND run.brokerage_mode='INTERNAL_PAPER' AND run.account_id IS NULL
    AND EXISTS (
      SELECT 1 FROM public.paper_accounts paper
      WHERE paper.user_id=demo_owner AND paper.account_id=team_a_paper_account
        AND paper.name='Team A acceptance paper' AND paper.owner_scope_hash=repeat('a',64)
    );

  INSERT INTO public.automation_paper_history_run_links_v218(
    run_id,owner_user_id,account_id,source_code,evidence_sha256
  )
  SELECT run.run_id,demo_owner,team_a_paper_account,'TEAM_A_ACCEPTANCE_PAPER',
         encode(public.digest(convert_to(
           'team-a-acceptance-paper-run-v1:'||run.run_id||':'||run.session_date::text||':'||
           run.brokerage_mode||':'||run.state,'UTF8'),'sha256'),'hex')
  FROM public.automation_runs run
  JOIN public.paper_accounts paper ON paper.user_id=run.user_id
    AND paper.account_id=team_a_paper_account AND paper.name='Team A acceptance paper'
    AND paper.owner_scope_hash=repeat('a',64)
  WHERE run.user_id=demo_owner AND run.run_id='auto_run_team_a_news_veto_0001'
    AND run.brokerage_mode='INTERNAL_PAPER' AND run.account_id=team_a_paper_account
  ON CONFLICT (run_id) DO NOTHING;

  INSERT INTO public.automation_position_history_events_v218(
    position_id,owner_user_id,source_account_id,original_status,reason_code,evidence_sha256
  )
  SELECT position.position_id,demo_owner,replay_account,position.status,
         'DETERMINISTIC_INTERNAL_PAPER_REPLAY',
         encode(public.digest(convert_to(
           'p1-history-replay-v1:position:'||position.position_id||':'||position.symbol||':'||
           position.quantity||':'||position.entry_session::text||':'||position.status||':'||
           COALESCE(position.entry_filled_quantity::text,'NULL')||':'||
           COALESCE(position.entry_average_fill_price_krw::text,'NULL'),
           'UTF8'),'sha256'),'hex')
  FROM public.automation_positions position
  WHERE position.user_id=demo_owner AND position.account_id=replay_account
    AND position.position_id LIKE 'auto_pos_replay%'
    AND position.entry_order_id LIKE 'ord_mock_%'
    AND position.status IN ('OPEN','EXIT_PENDING','CLOSED')
    AND EXISTS (
      SELECT 1 FROM public.paper_accounts paper
      WHERE paper.user_id=demo_owner AND paper.account_id=replay_account
        AND paper.name='Offline history replay' AND paper.owner_scope_hash=repeat('d',64)
    )
    AND EXISTS (
      SELECT 1
      FROM public.automation_runs run
      JOIN public.automation_order_reservations reservation ON reservation.run_id=run.run_id
      WHERE run.user_id=position.user_id AND run.run_id LIKE 'auto_run_replay%'
        AND run.brokerage_mode='INTERNAL_PAPER' AND run.session_date=position.entry_session
        AND reservation.user_id=position.user_id AND reservation.session_date=run.session_date
        AND reservation.symbol=position.symbol AND reservation.reconciliation_status='MATCHED'
        AND reservation.order_id IS NULL
        AND reservation.filled_quantity=position.entry_filled_quantity
        AND reservation.average_fill_price_krw=position.entry_average_fill_price_krw
    )
  ON CONFLICT (position_id) DO NOTHING;

  INSERT INTO public.automation_position_history_events_v218(
    position_id,owner_user_id,source_account_id,original_status,reason_code,evidence_sha256
  )
  SELECT position.position_id,demo_owner,COALESCE(position.source_account_id,position.account_id),
         position.status,'TEAM_A_ACCEPTANCE_PAPER_FIXTURE',
         encode(public.digest(convert_to(
           'team-a-acceptance-paper-position-v1:'||position.position_id||':'||
           position.symbol||':'||position.quantity||':'||position.entry_session::text||':'||
           position.status, 'UTF8'),'sha256'),'hex')
  FROM public.automation_positions position
  JOIN public.paper_accounts paper ON paper.user_id=position.user_id
    AND paper.account_id=position.account_id AND paper.name='Team A acceptance paper'
    AND paper.owner_scope_hash=repeat('a',64)
  WHERE position.user_id=demo_owner AND position.position_id='auto_pos_team_a_closed_0001'
    AND position.symbol='005930' AND position.status='CLOSED'
    AND position.entry_session=DATE '2026-08-18' AND position.entry_order_id IS NULL
  ON CONFLICT (position_id) DO NOTHING;

  UPDATE public.automation_positions position
  SET data_quality_state='HISTORICAL_PAPER'
  WHERE position.user_id=demo_owner AND position.account_id=replay_account
    AND position.position_id LIKE 'auto_pos_replay%'
    AND EXISTS (
      SELECT 1 FROM public.automation_position_history_events_v218 event
      WHERE event.position_id=position.position_id AND event.owner_user_id=demo_owner
        AND event.reason_code='DETERMINISTIC_INTERNAL_PAPER_REPLAY'
    );

  UPDATE public.automation_positions position
  SET data_quality_state='HISTORICAL_PAPER'
  WHERE position.user_id=demo_owner AND position.position_id='auto_pos_team_a_closed_0001'
    AND EXISTS (
      SELECT 1 FROM public.automation_position_history_events_v218 event
      WHERE event.position_id=position.position_id AND event.owner_user_id=demo_owner
        AND event.reason_code='TEAM_A_ACCEPTANCE_PAPER_FIXTURE'
    );
END
$v218_link_offline_history_replay$;

-- The user explicitly chose to keep this old one-share SUBMITTED row quarantined and to block
-- automation until it is verified or cancelled. Preserve the order as-is; this record is the
-- reason it remains an account-history blocker.
DO $v218_quarantine_unreconciled_legacy_order$
DECLARE
  demo_owner text;
  bound_account text;
  candidate_count integer;
  candidate_order_id text;
  candidate_account_id text;
  evidence text;
BEGIN
  SELECT user_id INTO demo_owner FROM public.users WHERE username='demo-user' AND status='ACTIVE';
  IF demo_owner IS NULL THEN RETURN; END IF;
  SELECT credential.account_id INTO bound_account
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=demo_owner AND credential.brokerage_mode='KIS_MOCK'
    AND credential.credential_state IN ('CONNECTED','CERTIFIED');
  IF bound_account IS NULL THEN RETURN; END IF;

  SELECT count(*),min(order_row.order_id),min(order_row.account_id)
    INTO candidate_count,candidate_order_id,candidate_account_id
  FROM public.orders order_row
  WHERE order_row.user_id=demo_owner AND order_row.brokerage_mode='KIS_MOCK'
    AND order_row.account_id<>bound_account
    AND order_row.symbol='005930' AND order_row.side='BUY' AND order_row.quantity=1
    AND order_row.status='SUBMITTED' AND order_row.filled_quantity=0
    AND order_row.leaves_quantity=1
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_order_reservations reservation
      WHERE reservation.order_id=order_row.order_id
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_runs run
      WHERE run.user_id=demo_owner AND run.account_id=order_row.account_id
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.portfolio_balance_observations balance
      WHERE balance.owner_user_id=demo_owner AND balance.source='KIS_MOCK'
        AND balance.account_scope_hash=substr(order_row.account_id,6)||repeat('0',32)
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.full_owner_account_aliases_v218 alias
      WHERE alias.owner_user_id=demo_owner AND alias.source_account_id=order_row.account_id
        AND alias.canonical_account_id=bound_account
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_order_integrity_events_v218 prior
      WHERE prior.order_id=order_row.order_id
    );

  IF candidate_count<>1 THEN RETURN; END IF;
  evidence:=encode(public.digest(convert_to(
    'user-requested-order-quarantine:'||candidate_order_id||':'||candidate_account_id||
    ':SUBMITTED:005930:BUY:1:0:1',
    'UTF8'),'sha256'),'hex');
  INSERT INTO public.automation_order_integrity_events_v218(
    order_id,owner_user_id,source_account_id,reason_code,disposition_code,evidence_sha256
  ) VALUES (
    candidate_order_id,demo_owner,candidate_account_id,
    'LEGACY_UNLINKED_ORDER_NO_RECONCILIATION',
    'USER_REQUESTED_QUARANTINE_BLOCK_START',evidence
  ) ON CONFLICT (order_id) DO NOTHING;
END
$v218_quarantine_unreconciled_legacy_order$;

-- Clear stale schedules where the owning automation control is stopped or the version no longer
-- matches. A schedule from v20 must never remain visibly ARMED after control v21 was disarmed.
WITH stale AS (
  UPDATE public.automation_runtime_schedule schedule
  SET schedule_state='DISARMED',updated_at=statement_timestamp()
  FROM public.automation_control control
  WHERE control.user_id=schedule.user_id AND schedule.schedule_state='ARMED'
    AND (control.control_state<>'ARMED' OR control.version<>schedule.control_version)
  RETURNING schedule.user_id,schedule.session_date,schedule.schedule_id,schedule.control_version
)
INSERT INTO public.automation_runtime_events(
  event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
)
SELECT
  'auto_rte_'||substr(encode(public.digest(convert_to(
    stale.user_id||':'||stale.session_date::text||':'||stale.schedule_id||':V218_DISARM',
    'UTF8'),'sha256'),'hex'),1,32),
  stale.user_id,stale.session_date,NULL,'SCHEDULE_DISARMED',
  encode(public.digest(convert_to(
    'stale-schedule-disarmed:'||stale.schedule_id||':'||stale.control_version::text,
    'UTF8'),'sha256'),'hex'),
  true,statement_timestamp()
FROM stale
ON CONFLICT (event_id) DO NOTHING;

-- RLS-protected status/read helpers: active records on a different account are never silently
-- omitted from the 1/N count, and unverified histories cannot enter the start path.
CREATE POLICY full_owner_account_aliases_v218_reader
  ON public.full_owner_account_aliases_v218 FOR SELECT TO PUBLIC
  USING (
    current_user='flyway' AND (
      (session_user='decision_app'
        AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
        AND public.actor_rls_scope_is_open_v1())
      OR (session_user='decision_automation_runtime'
        AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
    )
  );
ALTER TABLE public.full_owner_account_aliases_v218 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.full_owner_account_aliases_v218 FORCE ROW LEVEL SECURITY;

CREATE FUNCTION public.p1_full_owner_account_history_integrity_v218(
  p_owner_user_id text,p_bound_account_id text
) RETURNS TABLE(
  unlinked_open_position_count integer,
  unresolved_unlinked_order_count integer,
  unresolved_unlinked_run_count integer,
  quarantined_position_count integer,
  historical_paper_open_position_count integer,
  historical_paper_closed_position_count integer,
  historical_paper_run_count integer
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_full_owner_account_history_integrity_v218$
BEGIN
  IF current_user<>'flyway' OR p_bound_account_id !~ '^acct_[A-Za-z0-9_-]{8,96}$'
     OR NOT (
       (session_user='decision_app'
        AND pg_catalog.current_setting('app.actor_user_id',true)=p_owner_user_id
        AND public.actor_rls_scope_is_open_v1())
       OR (session_user='decision_automation_runtime'
        AND pg_catalog.current_setting('app.automation_owner_user_id',true)=p_owner_user_id)
     ) THEN
    RAISE EXCEPTION 'account history integrity owner scope denied' USING ERRCODE='42501';
  END IF;

  SELECT count(*)::integer INTO unlinked_open_position_count
  FROM public.automation_positions position
  WHERE position.user_id=p_owner_user_id AND position.status IN ('OPEN','EXIT_PENDING')
    AND position.data_quality_state='VERIFIED'
    AND position.account_id<>p_bound_account_id
    AND NOT EXISTS (
      SELECT 1 FROM public.full_owner_account_aliases_v218 alias
      WHERE alias.owner_user_id=position.user_id
        AND alias.source_account_id=position.account_id
        AND alias.canonical_account_id=p_bound_account_id
    );

  SELECT count(*)::integer INTO unresolved_unlinked_order_count
  FROM public.orders order_row
  WHERE order_row.user_id=p_owner_user_id AND order_row.brokerage_mode='KIS_MOCK'
    AND order_row.account_id<>p_bound_account_id
    AND (order_row.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED',
                              'PARTIALLY_FILLED','CANCEL_REQUESTED')
         OR order_row.reconciliation_status='MISMATCH')
    AND NOT EXISTS (
      SELECT 1 FROM public.full_owner_account_aliases_v218 alias
      WHERE alias.owner_user_id=order_row.user_id
        AND alias.source_account_id=order_row.account_id
        AND alias.canonical_account_id=p_bound_account_id
    );

  SELECT count(*)::integer INTO unresolved_unlinked_run_count
  FROM public.automation_runs run
  WHERE run.user_id=p_owner_user_id AND run.account_id IS NOT NULL
    AND run.account_id<>p_bound_account_id AND run.physical_submit_count>0
    AND (
      run.state='PENDING_RECONCILIATION'
      OR (
        NOT EXISTS (
          SELECT 1 FROM public.automation_order_reservations reservation
          WHERE reservation.run_id=run.run_id
        )
        AND NOT EXISTS (
          SELECT 1 FROM public.automation_account_lineage lineage
          WHERE lineage.run_id=run.run_id AND lineage.reason='RECOVERY_BASELINE'
        )
      )
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.full_owner_account_aliases_v218 alias
      WHERE alias.owner_user_id=run.user_id AND alias.source_account_id=run.account_id
        AND alias.canonical_account_id=p_bound_account_id
    );

  SELECT count(*)::integer INTO quarantined_position_count
  FROM public.automation_positions position
  WHERE position.user_id=p_owner_user_id
    AND position.data_quality_state='QUARANTINED_UNVERIFIED'
    AND position.status='HALTED_MISMATCH';

  SELECT count(*) FILTER (WHERE position.status IN ('OPEN','EXIT_PENDING'))::integer,
         count(*) FILTER (WHERE position.status='CLOSED')::integer
    INTO historical_paper_open_position_count,historical_paper_closed_position_count
  FROM public.automation_positions position
  WHERE position.user_id=p_owner_user_id
    AND position.data_quality_state='HISTORICAL_PAPER'
    AND position.account_id<>p_bound_account_id;

  SELECT count(*)::integer INTO historical_paper_run_count
  FROM public.automation_paper_history_run_links_v218 linked
  WHERE linked.owner_user_id=p_owner_user_id AND linked.account_id<>p_bound_account_id;
  RETURN NEXT;
END
$p1_full_owner_account_history_integrity_v218$;
ALTER FUNCTION public.p1_full_owner_account_history_integrity_v218(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_full_owner_account_history_integrity_v218(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_full_owner_account_history_integrity_v218(text,text)
  TO decision_app,decision_automation_runtime;

CREATE FUNCTION public.p1_guard_full_account_history_schedule_claim_v218()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $p1_guard_full_account_history_schedule_claim_v218$
DECLARE integrity record;
DECLARE control_account text;
BEGIN
  IF NEW.schedule_state<>'CLAIMED' OR session_user<>'decision_automation_runtime' THEN
    RETURN NEW;
  END IF;
  IF pg_catalog.current_setting('app.automation_owner_user_id',true) IS DISTINCT FROM NEW.user_id THEN
    RAISE EXCEPTION 'automation schedule owner scope denied' USING ERRCODE='42501';
  END IF;
  SELECT control.account_id INTO control_account
  FROM public.automation_control control WHERE control.user_id=NEW.user_id;
  IF control_account IS NULL THEN RAISE EXCEPTION 'automation account unavailable' USING ERRCODE='40001'; END IF;
  SELECT * INTO integrity
  FROM public.p1_full_owner_account_history_integrity_v218(NEW.user_id,control_account);
  IF integrity.unlinked_open_position_count+integrity.unresolved_unlinked_order_count+
     integrity.unresolved_unlinked_run_count>0 THEN
    RAISE EXCEPTION 'ACCOUNT_HISTORY_UNLINKED' USING ERRCODE='P1H01';
  END IF;
  RETURN NEW;
END
$p1_guard_full_account_history_schedule_claim_v218$;
ALTER FUNCTION public.p1_guard_full_account_history_schedule_claim_v218() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_guard_full_account_history_schedule_claim_v218() FROM PUBLIC;
CREATE TRIGGER automation_schedule_account_history_guard_v218
  BEFORE UPDATE OF schedule_state ON public.automation_runtime_schedule
  FOR EACH ROW EXECUTE FUNCTION public.p1_guard_full_account_history_schedule_claim_v218();

CREATE OR REPLACE FUNCTION public.p1_arm_automation_full_v1(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text,
  p_provider_capability_ready boolean,p_operator_provider_ready boolean
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_full_v1$
DECLARE integrity record;
BEGIN
  IF session_user<>'decision_app'
     OR nullif(pg_catalog.current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id
     OR NOT public.p1_full_owner_connection_readiness_v1(p_user_id,p_account_id) THEN
    RAISE EXCEPTION 'FULL KIS read-only connection proof required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO integrity
  FROM public.p1_full_owner_account_history_integrity_v218(p_user_id,p_account_id);
  IF integrity.unlinked_open_position_count+integrity.unresolved_unlinked_order_count+
     integrity.unresolved_unlinked_run_count>0 THEN
    RAISE EXCEPTION 'ACCOUNT_HISTORY_UNLINKED' USING ERRCODE='P1H01';
  END IF;
  PERFORM pg_catalog.set_config('app.full_connected_credential_arm','on',true);
  RETURN QUERY SELECT * FROM public.p1_arm_automation_v3(
    p_user_id,p_account_id,p_policy_id,p_expected_policy_version,p_expected_control_version,
    p_scope_hash,p_request_hash,p_provider_capability_ready,p_operator_provider_ready
  );
END
$p1_arm_automation_full_v1$;
ALTER FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text,text,boolean,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text,text,boolean,boolean)
  FROM PUBLIC,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_full_v1(text,text,text,integer,integer,text,text,boolean,boolean)
  TO decision_app;

-- A stop action must clear every future ARMED schedule, including a stale-version row that
-- survived an earlier stop. The control version remains monotonic and the schedule event is durable.
CREATE TABLE public.automation_schedule_disarm_events_v218 (
  event_id text PRIMARY KEY CHECK (event_id ~ '^auto_dse_[0-9a-f]{32}$'),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  schedule_id text NOT NULL CHECK (schedule_id ~ '^auto_sched_[0-9a-f]{32}$'),
  schedule_control_version integer NOT NULL CHECK (schedule_control_version>=1),
  resulting_control_version integer NOT NULL CHECK (resulting_control_version>=1),
  request_hash text NOT NULL CHECK (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.automation_schedule_disarm_events_v218 OWNER TO flyway;
REVOKE ALL ON public.automation_schedule_disarm_events_v218
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT SELECT,INSERT ON public.automation_schedule_disarm_events_v218 TO flyway;
CREATE TRIGGER automation_schedule_disarm_events_append_only_v218
  BEFORE UPDATE OR DELETE ON public.automation_schedule_disarm_events_v218
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE OR REPLACE FUNCTION public.p1_disarm_automation_v1(
  p_user_id text,p_expected_version integer,p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_disarm_automation_v1$
DECLARE current_control public.automation_control%ROWTYPE;
DECLARE prior_idempotency public.automation_control_idempotency%ROWTYPE;
DECLARE next_version integer;
DECLARE kill_switch_active boolean;
DECLARE projection jsonb;
BEGIN
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_user_id,'DISARM_AUTOMATION','AUTOMATION',p_user_id,p_request_hash
  );
  IF p_scope_hash !~ '^sha256:[0-9a-f]{64}$' OR p_request_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_expected_version IS NULL OR p_expected_version<1 THEN
    RAISE EXCEPTION 'automation disarm input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,89));
  PERFORM pg_advisory_xact_lock(hashtextextended(p_scope_hash,89));
  SELECT * INTO prior_idempotency FROM public.automation_control_idempotency
  WHERE scope_hash=p_scope_hash FOR SHARE;
  IF FOUND THEN
    IF prior_idempotency.user_id<>p_user_id OR prior_idempotency.operation<>'DISARM'
       OR prior_idempotency.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior_idempotency.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO current_control FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation control unavailable' USING ERRCODE='P0002'; END IF;
  IF current_control.version<>p_expected_version THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  next_version:=CASE
    WHEN current_control.control_state='ARMED' AND current_control.version<2147483647
      THEN current_control.version+1
    ELSE current_control.version
  END;
  IF current_control.control_state='ARMED' THEN
    UPDATE public.automation_control SET control_state='DISARMED',version=next_version,
      updated_at=statement_timestamp() WHERE user_id=p_user_id;
    current_control.control_state:='DISARMED';current_control.version:=next_version;
  END IF;

  WITH stopped AS (
    UPDATE public.automation_runtime_schedule schedule
    SET schedule_state='DISARMED',updated_at=statement_timestamp()
    WHERE schedule.user_id=p_user_id AND schedule.schedule_state='ARMED'
    RETURNING schedule.session_date,schedule.schedule_id,schedule.control_version
  )
  INSERT INTO public.automation_schedule_disarm_events_v218(
    event_id,owner_user_id,session_date,schedule_id,schedule_control_version,
    resulting_control_version,request_hash
  )
  SELECT
    'auto_dse_'||substr(encode(public.digest(convert_to(
      p_user_id||':'||stopped.session_date::text||':'||stopped.schedule_id||':'||p_request_hash,
      'UTF8'),'sha256'),'hex'),1,32),
    p_user_id,stopped.session_date,stopped.schedule_id,stopped.control_version,
    current_control.version,p_request_hash
  FROM stopped
  ON CONFLICT (event_id) DO NOTHING;

  kill_switch_active:=public.p1_automation_kill_switch_active_v1(p_user_id);
  projection:=jsonb_build_object(
    'brokerageMode',current_control.brokerage_mode,
    'certificationStatus',current_control.certification_status,
    'contractId','automation-control.v1','controlState',current_control.control_state,
    'killSwitchActive',COALESCE(kill_switch_active,true),'principleId',current_control.principle_id,
    'projectionState',current_control.control_state,'strategyId',current_control.strategy_id,
    'version',current_control.version
  );
  INSERT INTO public.automation_control_idempotency(
    scope_hash,user_id,operation,request_hash,control_version,result_json
  ) VALUES (p_scope_hash,p_user_id,'DISARM',p_request_hash,current_control.version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_disarm_automation_v1$;
ALTER FUNCTION public.p1_disarm_automation_v1(text,integer,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_disarm_automation_v1(text,integer,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_disarm_automation_v1(text,integer,text,text) TO decision_app;

ALTER TABLE public.automation_runtime_schedule FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_account_lineage FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_position_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_balance_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_broker_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control FORCE ROW LEVEL SECURITY;
