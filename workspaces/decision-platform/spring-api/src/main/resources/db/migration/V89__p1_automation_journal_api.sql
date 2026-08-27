-- P1 Owner automation control and owner-scoped Journal API storage boundary.
-- Every decision_app statement requires a consumed v2 actor capability in the same transaction.

CREATE FUNCTION public.p1_journal_tags_valid(p_tags text[])
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog
AS $p1_journal_tags_valid$
  SELECT cardinality(p_tags) <= 20
     AND cardinality(p_tags) = (SELECT count(DISTINCT tag) FROM unnest(p_tags) tag)
     AND NOT EXISTS (SELECT 1 FROM unnest(p_tags) tag WHERE char_length(tag) NOT BETWEEN 1 AND 32)
$p1_journal_tags_valid$;
ALTER FUNCTION public.p1_journal_tags_valid(text[]) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_journal_tags_valid(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_journal_tags_valid(text[]) TO decision_app;

ALTER TABLE public.journals
  ADD COLUMN owner_scope text,
  ADD COLUMN backtest_run_id text,
  ADD COLUMN automation_run_id text,
  ADD COLUMN version integer NOT NULL DEFAULT 1;

UPDATE public.journals
SET owner_scope = encode(public.digest(user_id,'sha256'),'hex')
WHERE owner_scope IS NULL;

ALTER TABLE public.journals
  ALTER COLUMN owner_scope SET NOT NULL,
  ADD CONSTRAINT journals_id_v1_check CHECK (journal_id ~ '^jnl_[A-Za-z0-9_-]{8,96}$'),
  ADD CONSTRAINT journals_owner_scope_v1_check
    CHECK (owner_scope ~ '^[0-9a-f]{64}$' AND owner_scope<>repeat('0',64)),
  ADD CONSTRAINT journals_title_v1_check CHECK (char_length(title) BETWEEN 1 AND 120),
  ADD CONSTRAINT journals_content_v1_check CHECK (char_length(body) BETWEEN 1 AND 8192),
  ADD CONSTRAINT journals_tags_v1_check CHECK (public.p1_journal_tags_valid(tags)),
  ADD CONSTRAINT journals_version_v1_check CHECK (version >= 1),
  ADD CONSTRAINT journals_backtest_link_v1_check
    CHECK (backtest_run_id IS NULL OR backtest_run_id ~ '^run_[A-Za-z0-9_-]{8,96}$'),
  ADD CONSTRAINT journals_automation_link_v1_check
    CHECK (automation_run_id IS NULL OR automation_run_id ~ '^auto_run_[A-Za-z0-9_-]{8,96}$'),
  ADD CONSTRAINT journals_rag_link_v1_check
    CHECK (rag_answer_id IS NULL OR rag_answer_id ~ '^rag_[A-Za-z0-9_-]{8,96}$');

CREATE TABLE public.automation_control (
  user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE RESTRICT,
  control_state text NOT NULL CHECK (control_state IN ('DISARMED','ARMED','HALTED')),
  version integer NOT NULL CHECK (version >= 1),
  brokerage_mode text NOT NULL CHECK (brokerage_mode IN ('KIS_MOCK','INTERNAL_PAPER')),
  account_id text NOT NULL CHECK (account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  principle_id text NOT NULL CHECK (principle_id ~ '^prc_[A-Za-z0-9_-]{8,96}$'),
  strategy_id text NOT NULL CHECK (strategy_id ~ '^strategy_[A-Za-z0-9_-]{8,96}$'),
  baseline_account_digest text NOT NULL CHECK (
    baseline_account_digest ~ '^[0-9a-f]{64}$' AND baseline_account_digest<>repeat('0',64)
  ),
  certification_status text NOT NULL CHECK (
    certification_status IN ('NOT_REQUIRED_INTERNAL_PAPER','REQUIRED','VALID','EXPIRED','INVALID')
  ),
  kill_switch_active boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.automation_activation_gate (
  user_id text PRIMARY KEY REFERENCES public.users(user_id) ON DELETE RESTRICT,
  certification_status text NOT NULL CHECK (certification_status IN ('REQUIRED','VALID','EXPIRED','INVALID')),
  clean_release_binding boolean NOT NULL DEFAULT false,
  real_team_b_pointer_active boolean NOT NULL DEFAULT false,
  release_binding_sha256 text CHECK (release_binding_sha256 IS NULL OR release_binding_sha256 ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.automation_runs (
  run_id text PRIMARY KEY CHECK (run_id ~ '^auto_run_[A-Za-z0-9_-]{8,96}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  state text NOT NULL CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','BUY_CANDIDATE_SELECTED',
    'NEWS_CHECKING','NEWS_VETOED','RISK_CHECKING','ORDER_SUBMITTING','ORDER_SUBMITTED',
    'PENDING_RECONCILIATION','CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION',
    'SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED'
  )),
  brokerage_mode text NOT NULL CHECK (brokerage_mode IN ('KIS_MOCK','INTERNAL_PAPER')),
  selected_symbol text CHECK (selected_symbol IS NULL OR selected_symbol ~ '^[0-9]{6}$'),
  selected_side text CHECK (selected_side IS NULL OR selected_side IN ('BUY','SELL')),
  physical_submit_count integer NOT NULL CHECK (physical_submit_count BETWEEN 0 AND 1),
  vertex_call_count integer NOT NULL CHECK (vertex_call_count BETWEEN 0 AND 1),
  provider_calls integer NOT NULL CHECK (provider_calls BETWEEN 0 AND 16),
  started_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE INDEX automation_runs_owner_updated_idx ON public.automation_runs(user_id,updated_at DESC,run_id DESC);

CREATE TABLE public.automation_positions (
  position_id text PRIMARY KEY CHECK (position_id ~ '^auto_pos_[A-Za-z0-9_-]{8,96}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[A-Za-z0-9_-]{8,96}$'),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  quantity integer NOT NULL CHECK (quantity = 1),
  entry_session date NOT NULL,
  expiry_session date NOT NULL CHECK (expiry_session > entry_session),
  status text NOT NULL CHECK (status IN ('OPEN','EXIT_PENDING','CLOSED','HALTED_MISMATCH')),
  bot_owned boolean NOT NULL CHECK (bot_owned),
  short_allowed boolean NOT NULL CHECK (NOT short_allowed),
  created_at timestamptz NOT NULL,
  closed_at timestamptz,
  CHECK ((status='CLOSED') = (closed_at IS NOT NULL))
);
CREATE UNIQUE INDEX automation_positions_one_open_lot_idx
  ON public.automation_positions(user_id,account_id,symbol) WHERE status IN ('OPEN','EXIT_PENDING');

CREATE TABLE public.automation_events (
  event_id text PRIMARY KEY CHECK (event_id ~ '^auto_evt_[A-Za-z0-9_-]{8,96}$'),
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  sequence integer NOT NULL CHECK (sequence >= 1),
  event_type text NOT NULL CHECK (event_type IN (
    'CONTROL_CHANGED','RUN_TRANSITIONED','BASELINE_CAPTURED','ACCOUNT_RECONCILED',
    'EXIT_SELECTED','BUY_SELECTED','NEWS_RESULT_RECORDED','RISK_RESULT_RECORDED',
    'ORDER_RESERVED','ORDER_OUTCOME_RECORDED','CANCEL_RECORDED','DRIFT_DETECTED','RUN_HALTED'
  )),
  occurred_at timestamptz NOT NULL,
  payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$' AND payload_hash<>repeat('0',64)),
  provider_calls integer NOT NULL CHECK (provider_calls BETWEEN 0 AND 16),
  order_submits integer NOT NULL CHECK (order_submits BETWEEN 0 AND 1),
  sanitized boolean NOT NULL CHECK (sanitized),
  UNIQUE (run_id,sequence)
);

CREATE TABLE public.automation_control_idempotency (
  scope_hash text PRIMARY KEY CHECK (scope_hash ~ '^sha256:[0-9a-f]{64}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  operation text NOT NULL CHECK (operation IN ('ARM','DISARM')),
  request_hash text NOT NULL CHECK (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  control_version integer NOT NULL CHECK (control_version >= 1),
  result_json jsonb NOT NULL CHECK (
    jsonb_typeof(result_json)='object' AND octet_length(result_json::text) BETWEEN 2 AND 16384
  ),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.journal_idempotency (
  scope_hash text PRIMARY KEY CHECK (scope_hash ~ '^sha256:[0-9a-f]{64}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  operation text NOT NULL CHECK (operation IN ('CREATE','PATCH','DELETE')),
  request_hash text NOT NULL CHECK (request_hash ~ '^sha256:[0-9a-f]{64}$'),
  journal_id text NOT NULL CHECK (journal_id ~ '^jnl_[A-Za-z0-9_-]{8,96}$'),
  result_json jsonb NOT NULL CHECK (
    jsonb_typeof(result_json)='object' AND octet_length(result_json::text) BETWEEN 2 AND 65536
  ),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TRIGGER automation_events_append_only
BEFORE UPDATE OR DELETE ON public.automation_events
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

ALTER TABLE public.journals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.journals FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_activation_gate ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_activation_gate FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control_idempotency FORCE ROW LEVEL SECURITY;
ALTER TABLE public.journal_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.journal_idempotency FORCE ROW LEVEL SECURITY;

CREATE POLICY journals_owner_v89 ON public.journals TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_control_owner_v89 ON public.automation_control TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_activation_gate_owner_v89 ON public.automation_activation_gate FOR SELECT TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_runs_owner_v89 ON public.automation_runs TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_positions_owner_v89 ON public.automation_positions TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_events_owner_v89 ON public.automation_events TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY automation_idempotency_owner_v89 ON public.automation_control_idempotency TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));
CREATE POLICY journal_idempotency_owner_v89 ON public.journal_idempotency TO PUBLIC
USING ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()))
WITH CHECK ((current_user='flyway' AND session_user='decision_app' AND public.actor_rls_scope_is_open_v1()) OR
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()));

CREATE FUNCTION public.p1_journal_links_owned(
  p_user_id text,p_decision_id text,p_backtest_run_id text,p_rag_answer_id text,
  p_order_id text,p_automation_run_id text
)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_journal_links_owned$
  SELECT session_user='decision_app'
     AND current_setting('app.actor_user_id',true)=p_user_id
     AND public.actor_rls_scope_is_open_v1()
     AND (p_decision_id IS NULL OR EXISTS (
       SELECT 1 FROM public.decisions item WHERE item.user_id=p_user_id AND item.decision_id=p_decision_id
     ))
     AND (p_backtest_run_id IS NULL OR EXISTS (
       SELECT 1 FROM public.dashboard_artifact_views item
       WHERE item.owner_user_id=p_user_id AND item.view_kind='BACKTEST' AND item.run_id=p_backtest_run_id
     ))
     AND (p_rag_answer_id IS NULL OR EXISTS (
       SELECT 1 FROM public.rag_answer_history item
       WHERE item.owner_user_id=p_user_id AND item.answer_id=p_rag_answer_id
       UNION ALL
       SELECT 1 FROM public.rag_v2_answer_history item
       WHERE item.owner_user_id=p_user_id AND item.answer_id=p_rag_answer_id
     ))
     AND (p_order_id IS NULL OR EXISTS (
       SELECT 1 FROM public.orders item WHERE item.user_id=p_user_id AND item.order_id=p_order_id
     ))
     AND (p_automation_run_id IS NULL OR EXISTS (
       SELECT 1 FROM public.automation_runs item
       WHERE item.user_id=p_user_id AND item.run_id=p_automation_run_id
     ))
$p1_journal_links_owned$;
ALTER FUNCTION public.p1_journal_links_owned(text,text,text,text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_journal_links_owned(text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_journal_links_owned(text,text,text,text,text,text) TO decision_app;

CREATE FUNCTION public.p1_automation_account_digest_v1(
  p_user_id text,p_brokerage_mode text,p_account_id text
)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_account_digest_v1$
DECLARE account_payload jsonb;
DECLARE balance_row public.portfolio_balance_observations%ROWTYPE;
DECLARE candidate_count integer;
BEGIN
  IF session_user<>'decision_app'
     OR current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1()
     OR p_account_id!~'^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'automation account scope denied' USING ERRCODE='42501';
  END IF;
  IF p_brokerage_mode='INTERNAL_PAPER' THEN
    SELECT jsonb_build_object(
      'accountId',account.account_id,
      'cashKrw',account.cash_balance,
      'positions',COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
          'averagePriceKrw',position.average_price,
          'marketValueKrw',position.market_value,
          'quantity',position.quantity,
          'symbol',position.symbol
        ) ORDER BY position.symbol)
        FROM public.paper_positions position WHERE position.account_id=account.account_id
      ),'[]'::jsonb)
    ) INTO account_payload
    FROM public.paper_accounts account
    WHERE account.account_id=p_account_id AND account.user_id=p_user_id AND account.status='ACTIVE'
    FOR SHARE OF account;
  ELSIF p_brokerage_mode='KIS_MOCK' THEN
    SELECT count(DISTINCT account_scope_hash) INTO candidate_count FROM (
      SELECT account_scope_hash
      FROM public.portfolio_balance_observations
      WHERE owner_user_id=p_user_id AND source='KIS_MOCK' AND context_status='ACTIVE'
        AND account_scope_hash LIKE substr(p_account_id,6)||'%'
    ) candidate;
    IF candidate_count<>1 THEN
      RAISE EXCEPTION 'automation account unavailable' USING ERRCODE='P0002';
    END IF;
    SELECT * INTO balance_row
    FROM public.portfolio_balance_observations
    WHERE owner_user_id=p_user_id AND source='KIS_MOCK' AND context_status='ACTIVE'
      AND account_scope_hash LIKE substr(p_account_id,6)||'%'
    ORDER BY observed_at DESC,received_at DESC,observation_id
    LIMIT 1 FOR SHARE;
    IF balance_row.completeness<>'COMPLETE' THEN
      RAISE EXCEPTION 'automation account evidence incomplete' USING ERRCODE='40001';
    END IF;
    SELECT jsonb_build_object(
      'accountId',p_account_id,
      'cashKrw',balance_row.cash_krw,
      'marginRequirementKrw',balance_row.margin_requirement_krw,
      'portfolioEquityKrw',balance_row.portfolio_equity_krw,
      'positions',COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
          'marketValueKrw',position.market_value_krw,
          'quantity',position.quantity,
          'symbol',position.symbol
        ) ORDER BY position.symbol)
        FROM public.portfolio_position_observations position
        WHERE position.balance_observation_id=balance_row.observation_id
      ),'[]'::jsonb)
    ) INTO account_payload;
  ELSE
    RAISE EXCEPTION 'automation brokerage mode invalid' USING ERRCODE='22023';
  END IF;
  IF account_payload IS NULL THEN
    RAISE EXCEPTION 'automation account unavailable' USING ERRCODE='P0002';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=p_account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
  ) THEN
    RAISE EXCEPTION 'automation account has unresolved state' USING ERRCODE='40001';
  END IF;
  RETURN encode(public.digest(convert_to(account_payload::text,'UTF8'),'sha256'),'hex');
END
$p1_automation_account_digest_v1$;
ALTER FUNCTION public.p1_automation_account_digest_v1(text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_account_digest_v1(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_account_digest_v1(text,text,text) TO decision_app;

CREATE FUNCTION public.p1_automation_principle_active_v1(p_user_id text,p_principle_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_principle_active_v1$
  SELECT session_user='decision_app'
     AND pg_catalog.current_setting('app.actor_user_id',true)=p_user_id
     AND public.actor_rls_scope_is_open_v1()
     AND EXISTS (
       SELECT 1 FROM public.principles item
       WHERE item.user_id=p_user_id AND item.principle_id=p_principle_id AND item.status='ACTIVE'
     )
$p1_automation_principle_active_v1$;

CREATE FUNCTION public.p1_real_team_b_pointer_active_v1()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_real_team_b_pointer_active_v1$
  SELECT session_user='decision_app' AND public.actor_rls_scope_is_open_v1()
     AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
     AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1
$p1_real_team_b_pointer_active_v1$;

CREATE FUNCTION public.p1_automation_kill_switch_active_v1(p_user_id text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_kill_switch_active_v1$
DECLARE gate_active boolean;
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation kill switch scope denied' USING ERRCODE='42501';
  END IF;
  SELECT active INTO gate_active FROM public.risk_kill_switch
  WHERE kill_switch_id='GLOBAL' FOR SHARE;
  RETURN COALESCE(gate_active,true);
END
$p1_automation_kill_switch_active_v1$;

ALTER FUNCTION public.p1_automation_principle_active_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_real_team_b_pointer_active_v1() OWNER TO flyway;
ALTER FUNCTION public.p1_automation_kill_switch_active_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_principle_active_v1(text,text),
  public.p1_real_team_b_pointer_active_v1(),public.p1_automation_kill_switch_active_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_principle_active_v1(text,text),
  public.p1_real_team_b_pointer_active_v1(),public.p1_automation_kill_switch_active_v1(text) TO decision_app;

CREATE FUNCTION public.p1_arm_automation_v1(
  p_user_id text,p_brokerage_mode text,p_account_id text,p_principle_id text,p_strategy_id text,
  p_expected_version integer,p_scope_hash text,p_request_hash text
)
RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
AS $p1_arm_automation_v1$
DECLARE current_control public.automation_control%ROWTYPE;
DECLARE prior_idempotency public.automation_control_idempotency%ROWTYPE;
DECLARE activation_gate public.automation_activation_gate%ROWTYPE;
DECLARE current_version integer;
DECLARE next_version integer;
DECLARE baseline_digest text;
DECLARE previous_digest text;
DECLARE certification_status text;
DECLARE kill_switch_active boolean;
DECLARE projection jsonb;
BEGIN
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_user_id,'ARM_AUTOMATION','AUTOMATION',p_user_id,p_request_hash
  );
  IF p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_expected_version IS NULL OR p_expected_version<1 THEN
    RAISE EXCEPTION 'automation arm input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,89));
  PERFORM pg_advisory_xact_lock(hashtextextended(p_scope_hash,89));
  SELECT * INTO prior_idempotency FROM public.automation_control_idempotency
  WHERE scope_hash=p_scope_hash FOR SHARE;
  IF FOUND THEN
    IF prior_idempotency.user_id<>p_user_id OR prior_idempotency.operation<>'ARM'
       OR prior_idempotency.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior_idempotency.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO current_control FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  current_version:=CASE WHEN FOUND THEN current_control.version ELSE 1 END;
  IF current_version<>p_expected_version OR (FOUND AND current_control.control_state<>'DISARMED')
     OR current_version=2147483647 THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  IF NOT public.p1_automation_principle_active_v1(p_user_id,p_principle_id) THEN
    RAISE EXCEPTION 'automation principle unavailable' USING ERRCODE='P0002';
  END IF;
  kill_switch_active:=public.p1_automation_kill_switch_active_v1(p_user_id);
  IF COALESCE(kill_switch_active,true) THEN
    RAISE EXCEPTION 'automation kill switch active' USING ERRCODE='40001';
  END IF;
  IF p_brokerage_mode='KIS_MOCK' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('automation-activation:'||p_user_id,89));
    SELECT * INTO activation_gate FROM public.automation_activation_gate
    WHERE user_id=p_user_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'automation activation gate missing' USING ERRCODE='40001';
    ELSIF activation_gate.certification_status<>'VALID' THEN
      RAISE EXCEPTION 'automation certification gate closed' USING ERRCODE='40001';
    ELSIF NOT activation_gate.clean_release_binding OR activation_gate.release_binding_sha256 IS NULL THEN
      RAISE EXCEPTION 'automation release binding gate closed' USING ERRCODE='40001';
    ELSIF NOT activation_gate.real_team_b_pointer_active THEN
      RAISE EXCEPTION 'automation pointer projection gate closed' USING ERRCODE='40001';
    ELSIF NOT public.p1_real_team_b_pointer_active_v1() THEN
      RAISE EXCEPTION 'automation real pointer gate closed' USING ERRCODE='40001';
    END IF;
    certification_status:='VALID';
  ELSIF p_brokerage_mode='INTERNAL_PAPER' THEN
    certification_status:='NOT_REQUIRED_INTERNAL_PAPER';
  ELSE
    RAISE EXCEPTION 'automation brokerage mode invalid' USING ERRCODE='22023';
  END IF;
  IF current_control.user_id IS NOT NULL THEN
    previous_digest:=public.p1_automation_account_digest_v1(
      p_user_id,current_control.brokerage_mode,current_control.account_id
    );
    IF previous_digest<>current_control.baseline_account_digest THEN
      RAISE EXCEPTION 'automation baseline drift detected' USING ERRCODE='40001';
    END IF;
  END IF;
  baseline_digest:=public.p1_automation_account_digest_v1(p_user_id,p_brokerage_mode,p_account_id);
  next_version:=current_version+1;
  INSERT INTO public.automation_control(
    user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
    baseline_account_digest,certification_status,kill_switch_active,created_at,updated_at
  ) VALUES (
    p_user_id,'ARMED',next_version,p_brokerage_mode,p_account_id,p_principle_id,p_strategy_id,
    baseline_digest,certification_status,false,statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id) DO UPDATE SET
    control_state='ARMED',version=excluded.version,brokerage_mode=excluded.brokerage_mode,
    account_id=excluded.account_id,principle_id=excluded.principle_id,strategy_id=excluded.strategy_id,
    baseline_account_digest=excluded.baseline_account_digest,
    certification_status=excluded.certification_status,kill_switch_active=false,
    updated_at=excluded.updated_at;
  projection:=jsonb_build_object(
    'brokerageMode',p_brokerage_mode,'certificationStatus',certification_status,
    'contractId','automation-control.v1','controlState','ARMED','killSwitchActive',false,
    'principleId',p_principle_id,'projectionState','ARMED','strategyId',p_strategy_id,
    'version',next_version
  );
  INSERT INTO public.automation_control_idempotency(
    scope_hash,user_id,operation,request_hash,control_version,result_json
  ) VALUES (p_scope_hash,p_user_id,'ARM',p_request_hash,next_version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_arm_automation_v1$;

CREATE FUNCTION public.p1_disarm_automation_v1(
  p_user_id text,p_expected_version integer,p_scope_hash text,p_request_hash text
)
RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog
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
  IF p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$'
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
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation control unavailable' USING ERRCODE='P0002';
  END IF;
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

ALTER FUNCTION public.p1_arm_automation_v1(text,text,text,text,text,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_disarm_automation_v1(text,integer,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_arm_automation_v1(text,text,text,text,text,integer,text,text),
  public.p1_disarm_automation_v1(text,integer,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_arm_automation_v1(text,text,text,text,text,integer,text,text),
  public.p1_disarm_automation_v1(text,integer,text,text) TO decision_app;

REVOKE ALL ON TABLE public.journals,public.automation_control,public.automation_activation_gate,
  public.automation_runs,public.automation_positions,public.automation_events,
  public.automation_control_idempotency,public.journal_idempotency
  FROM PUBLIC,decision_worker,decision_replay;
GRANT SELECT,INSERT,UPDATE ON TABLE public.journals,public.automation_control,
  public.automation_runs,public.automation_positions,public.automation_control_idempotency,
  public.journal_idempotency TO decision_app;
GRANT SELECT,INSERT ON TABLE public.automation_events TO decision_app;
GRANT SELECT ON TABLE public.automation_activation_gate TO decision_app;
