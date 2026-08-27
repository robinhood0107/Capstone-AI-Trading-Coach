-- P1 KIS_MOCK persistent automation runtime boundary.
-- Runtime rows contain only bounded projections and one-way hashes; provider/account/order payloads stay out.

ALTER TABLE public.automation_activation_gate
  ADD COLUMN gate_version integer NOT NULL DEFAULT 1 CHECK (gate_version >= 1),
  ADD COLUMN certification_receipt_sha256 text CHECK (
    certification_receipt_sha256 IS NULL OR certification_receipt_sha256 ~ '^[0-9a-f]{64}$'
  ),
  ADD COLUMN certification_session_date date,
  ADD COLUMN strategy_eligible_from_session_date date,
  ADD COLUMN source_binding_sha256 text CHECK (
    source_binding_sha256 IS NULL OR source_binding_sha256 ~ '^[0-9a-f]{64}$'
  ),
  ADD COLUMN team_b_integrity_receipt_sha256 text CHECK (
    team_b_integrity_receipt_sha256 IS NULL OR team_b_integrity_receipt_sha256 ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT automation_activation_certification_delay_v90_check CHECK (
    certification_session_date IS NULL OR strategy_eligible_from_session_date IS NULL
    OR strategy_eligible_from_session_date > certification_session_date
  );

-- Test and baseline migrations may be executed by a database owner; normalize the production owner
-- before SECURITY DEFINER runtime functions touch current V89/source tables.
ALTER TABLE public.automation_control OWNER TO flyway;
ALTER TABLE public.automation_activation_gate OWNER TO flyway;
ALTER TABLE public.automation_runs OWNER TO flyway;
ALTER TABLE public.automation_positions OWNER TO flyway;
ALTER TABLE public.automation_events OWNER TO flyway;
ALTER TABLE public.orders OWNER TO flyway;
ALTER TABLE public.portfolio_balance_observations OWNER TO flyway;
ALTER TABLE public.portfolio_position_observations OWNER TO flyway;
ALTER TABLE public.principles OWNER TO flyway;
ALTER TABLE public.risk_kill_switch OWNER TO flyway;
ALTER TABLE public.p1_return_artifact_bundle OWNER TO flyway;
ALTER TABLE public.p1_return_signal_projection OWNER TO flyway;
ALTER VIEW public.current_p1_return_signal_pointer OWNER TO flyway;
ALTER TABLE public.market_data_manifests OWNER TO flyway;

CREATE TABLE public.automation_runtime_schedule (
  schedule_id text PRIMARY KEY CHECK (schedule_id ~ '^auto_sched_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  control_version integer NOT NULL CHECK (control_version >= 1),
  schedule_state text NOT NULL CHECK (
    schedule_state IN ('ARMED','CLAIMED','DISARMED','COMPLETED','HALTED')
  ),
  run_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  UNIQUE (user_id,session_date)
);

CREATE TABLE public.automation_runtime_claim (
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  run_id text NOT NULL UNIQUE REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  claim_token_hash text NOT NULL CHECK (claim_token_hash ~ '^sha256:[0-9a-f]{64}$'),
  claim_state text NOT NULL CHECK (claim_state IN ('ACTIVE','RELEASED')),
  claimed_at timestamptz NOT NULL,
  released_at timestamptz,
  PRIMARY KEY (user_id,session_date),
  CHECK ((claim_state='RELEASED') = (released_at IS NOT NULL))
);

CREATE TABLE public.automation_runtime_checkpoint (
  run_id text PRIMARY KEY REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  checkpoint_version integer NOT NULL CHECK (checkpoint_version >= 1),
  state text NOT NULL CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','BUY_CANDIDATE_SELECTED',
    'NEWS_CHECKING','NEWS_VETOED','RISK_CHECKING','ORDER_SUBMITTING','ORDER_SUBMITTED',
    'PENDING_RECONCILIATION','CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION',
    'SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED'
  )),
  selected_symbol text CHECK (selected_symbol IS NULL OR selected_symbol ~ '^[0-9]{6}$'),
  selected_side text CHECK (selected_side IS NULL OR selected_side IN ('BUY','SELL')),
  decision_id text REFERENCES public.decisions(decision_id) ON DELETE RESTRICT
    CHECK (decision_id IS NULL OR decision_id ~ '^dec_[0-9a-f]{32}$'),
  vertex_call_count integer NOT NULL CHECK (vertex_call_count BETWEEN 0 AND 1),
  provider_call_count integer NOT NULL CHECK (provider_call_count BETWEEN 0 AND 16),
  logical_submit_count integer NOT NULL CHECK (logical_submit_count BETWEEN 0 AND 1),
  updated_at timestamptz NOT NULL,
  UNIQUE (user_id,session_date)
);

CREATE TABLE public.automation_processed_ticks (
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  tick_identity_hash text NOT NULL CHECK (tick_identity_hash ~ '^sha256:[0-9a-f]{64}$'),
  result_hash text NOT NULL CHECK (result_hash ~ '^sha256:[0-9a-f]{64}$'),
  checkpoint_version integer NOT NULL CHECK (checkpoint_version >= 1),
  processed_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,tick_identity_hash)
);

CREATE TABLE public.automation_order_reservations (
  reservation_id text PRIMARY KEY CHECK (reservation_id ~ '^auto_res_[0-9a-f]{32}$'),
  run_id text NOT NULL UNIQUE REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  side text NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity integer NOT NULL CHECK (quantity = 1),
  limit_price_krw bigint NOT NULL CHECK (limit_price_krw > 0),
  logical_submit_count integer NOT NULL CHECK (logical_submit_count BETWEEN 0 AND 1),
  order_id text REFERENCES public.orders(order_id) ON DELETE RESTRICT
    CHECK (order_id IS NULL OR order_id ~ '^ord_mock_[0-9a-f]{32}$'),
  provider_order_ref_hash text CHECK (
    provider_order_ref_hash IS NULL OR provider_order_ref_hash ~ '^[0-9a-f]{64}$'
  ),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  UNIQUE (user_id,session_date)
);

CREATE TABLE public.automation_runtime_events (
  event_id text PRIMARY KEY CHECK (event_id ~ '^auto_rte_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  run_id text REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  event_type text NOT NULL CHECK (event_type IN (
    'ACTIVATION_GATE_AUTHORED','SCHEDULE_ARMED','SCHEDULE_DISARMED','SESSION_CLAIMED',
    'CHECKPOINT_TRANSITIONED','SESSION_RELEASED'
  )),
  payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
  sanitized boolean NOT NULL CHECK (sanitized),
  occurred_at timestamptz NOT NULL
);

ALTER TABLE public.automation_runtime_schedule OWNER TO flyway;
ALTER TABLE public.automation_runtime_claim OWNER TO flyway;
ALTER TABLE public.automation_runtime_checkpoint OWNER TO flyway;
ALTER TABLE public.automation_processed_ticks OWNER TO flyway;
ALTER TABLE public.automation_order_reservations OWNER TO flyway;
ALTER TABLE public.automation_runtime_events OWNER TO flyway;

CREATE TRIGGER automation_runtime_events_append_only
BEFORE UPDATE OR DELETE ON public.automation_runtime_events
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER automation_processed_ticks_append_only
BEFORE UPDATE OR DELETE ON public.automation_processed_ticks
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

ALTER TABLE public.automation_runtime_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_schedule FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_claim ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_claim FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_checkpoint FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_processed_ticks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_processed_ticks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_events FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_runtime_schedule_scope_v90 ON public.automation_runtime_schedule TO PUBLIC
USING (
  current_user='flyway' AND session_user IN ('decision_automation_runtime','decision_replay_authorizer')
  AND (
    user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
    OR pg_catalog.current_setting('app.automation_claim_scan',true)='1'
  )
)
WITH CHECK (
  current_user='flyway' AND session_user IN ('decision_automation_runtime','decision_replay_authorizer')
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_runtime_claim_scope_v90 ON public.automation_runtime_claim TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND (
    user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
    OR pg_catalog.current_setting('app.automation_claim_scan',true)='1'
  )
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_runtime_checkpoint_scope_v90 ON public.automation_runtime_checkpoint TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_processed_ticks_scope_v90 ON public.automation_processed_ticks TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND EXISTS (
    SELECT 1 FROM public.automation_runtime_checkpoint checkpoint
    WHERE checkpoint.run_id=automation_processed_ticks.run_id
      AND checkpoint.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND EXISTS (
    SELECT 1 FROM public.automation_runtime_checkpoint checkpoint
    WHERE checkpoint.run_id=automation_processed_ticks.run_id
      AND checkpoint.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
);
CREATE POLICY automation_order_reservations_scope_v90 ON public.automation_order_reservations TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_runtime_events_scope_v90 ON public.automation_runtime_events TO PUBLIC
USING (
  current_user='flyway' AND session_user IN ('decision_automation_runtime','decision_replay_authorizer')
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user IN ('decision_automation_runtime','decision_replay_authorizer')
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);

-- V89 owner tables remain inaccessible directly. These policies are usable only by the bounded
-- SECURITY DEFINER functions below because the runtime role receives no table privilege.
CREATE POLICY automation_control_runtime_v90 ON public.automation_control TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_activation_runtime_v90 ON public.automation_activation_gate TO PUBLIC
USING (
  current_user='flyway' AND session_user IN ('decision_automation_runtime','decision_replay_authorizer')
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_replay_authorizer'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_runs_runtime_v90 ON public.automation_runs TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_events_runtime_v90 ON public.automation_events TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY automation_positions_runtime_v90 ON public.automation_positions TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
)
WITH CHECK (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY orders_automation_runtime_v90 ON public.orders FOR SELECT TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY portfolio_balance_automation_runtime_v90 ON public.portfolio_balance_observations FOR SELECT TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY portfolio_position_automation_runtime_v90 ON public.portfolio_position_observations FOR SELECT TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND EXISTS (
    SELECT 1 FROM public.portfolio_balance_observations balance
    WHERE balance.observation_id=portfolio_position_observations.balance_observation_id
      AND balance.owner_user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
  )
);
CREATE POLICY principles_automation_runtime_v90 ON public.principles FOR SELECT TO PUBLIC
USING (
  current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
);
CREATE POLICY risk_kill_switch_automation_runtime_v90 ON public.risk_kill_switch FOR SELECT TO PUBLIC
USING (current_user='flyway' AND session_user='decision_automation_runtime');

CREATE FUNCTION public.p1_automation_transition_valid_v1(p_current text,p_next text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog
AS $p1_automation_transition_valid_v1$
  SELECT p_current=p_next AND p_current IN ('ORDER_SUBMITTING','PENDING_RECONCILIATION') OR (p_current,p_next) IN (
    ('SCHEDULED','PRECHECK'),('SCHEDULED','SKIPPED_NO_ACTION'),
    ('SCHEDULED','SKIPPED_DATA_UNAVAILABLE'),('SCHEDULED','SKIPPED_LATE_START'),
    ('SCHEDULED','HALTED'),('PRECHECK','RECONCILING_PREVIOUS'),('PRECHECK','EXIT_SELECTED'),
    ('PRECHECK','BUY_CANDIDATE_SELECTED'),('PRECHECK','SKIPPED_NO_ACTION'),
    ('PRECHECK','SKIPPED_DATA_UNAVAILABLE'),('PRECHECK','HALTED'),
    ('RECONCILING_PREVIOUS','PENDING_RECONCILIATION'),('RECONCILING_PREVIOUS','EXIT_SELECTED'),
    ('RECONCILING_PREVIOUS','BUY_CANDIDATE_SELECTED'),('RECONCILING_PREVIOUS','SKIPPED_NO_ACTION'),
    ('EXIT_SELECTED','RISK_CHECKING'),('BUY_CANDIDATE_SELECTED','NEWS_CHECKING'),
    ('NEWS_CHECKING','NEWS_VETOED'),('NEWS_CHECKING','RISK_CHECKING'),
    ('RISK_CHECKING','ORDER_SUBMITTING'),('RISK_CHECKING','SKIPPED_NO_ACTION'),
    ('RISK_CHECKING','HALTED'),('ORDER_SUBMITTING','ORDER_SUBMITTED'),
    ('ORDER_SUBMITTING','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','SKIPPED_NO_ACTION'),
    ('ORDER_SUBMITTING','SKIPPED_DATA_UNAVAILABLE'),('ORDER_SUBMITTING','SKIPPED_LATE_START'),
    ('ORDER_SUBMITTING','HALTED'),('ORDER_SUBMITTED','COMPLETED'),
    ('ORDER_SUBMITTED','PENDING_RECONCILIATION'),('ORDER_SUBMITTED','CANCELLED_UNFILLED'),
    ('ORDER_SUBMITTED','HALTED'),('PENDING_RECONCILIATION','COMPLETED'),
    ('PENDING_RECONCILIATION','CANCELLED_UNFILLED'),('PENDING_RECONCILIATION','HALTED')
  )
$p1_automation_transition_valid_v1$;

CREATE FUNCTION public.p1_automation_runtime_account_digest_v1(
  p_user_id text,p_account_id text
)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_runtime_account_digest_v1$
DECLARE balance_row public.portfolio_balance_observations%ROWTYPE;
DECLARE account_projection jsonb;
DECLARE candidate_count integer;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR current_setting('app.automation_owner_user_id',true)<>p_user_id
     OR p_account_id!~'^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'automation runtime account scope denied' USING ERRCODE='42501';
  END IF;
  SELECT count(DISTINCT account_scope_hash) INTO candidate_count
  FROM public.portfolio_balance_observations
  WHERE owner_user_id=p_user_id AND source='KIS_MOCK' AND context_status='ACTIVE'
    AND account_scope_hash LIKE substr(p_account_id,6)||'%';
  IF candidate_count<>1 THEN RETURN NULL; END IF;
  SELECT * INTO balance_row
  FROM public.portfolio_balance_observations
  WHERE owner_user_id=p_user_id AND source='KIS_MOCK' AND context_status='ACTIVE'
    AND account_scope_hash LIKE substr(p_account_id,6)||'%'
  ORDER BY observed_at DESC,received_at DESC,observation_id
  LIMIT 1;
  IF NOT FOUND OR balance_row.completeness<>'COMPLETE' THEN RETURN NULL; END IF;
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
  ) INTO account_projection;
  RETURN encode(public.digest(convert_to(account_projection::text,'UTF8'),'sha256'),'hex');
END
$p1_automation_runtime_account_digest_v1$;

CREATE FUNCTION public.p1_author_automation_activation_gate_v2(
  p_user_id text,p_certification_receipt_sha256 text,p_certification_session date,
  p_strategy_eligible_from_session date,p_release_binding_sha256 text,
  p_source_binding_sha256 text,p_team_b_integrity_receipt_sha256 text
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_author_automation_activation_gate_v2$
DECLARE next_version integer;
DECLARE event_seed text;
DECLARE active_team_b_receipt_sha256 text;
BEGIN
  IF session_user<>'decision_replay_authorizer'
     OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_certification_receipt_sha256!~'^[0-9a-f]{64}$'
     OR p_release_binding_sha256!~'^[0-9a-f]{64}$'
     OR p_source_binding_sha256!~'^[0-9a-f]{64}$'
     OR p_team_b_integrity_receipt_sha256!~'^[0-9a-f]{64}$'
     OR p_strategy_eligible_from_session<=p_certification_session THEN
    RAISE EXCEPTION 'automation activation gate input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-activation:'||p_user_id,90));
  IF (SELECT count(*) FROM public.current_p1_return_signal_pointer)<>31
     OR (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)<>1 THEN
    RAISE EXCEPTION 'automation real Team B pointer unavailable' USING ERRCODE='40001';
  END IF;
  SELECT bundle.packet_sha256 INTO active_team_b_receipt_sha256
  FROM public.p1_return_artifact_bundle bundle
  WHERE bundle.bundle_sha256=(
    SELECT min(pointer.bundle_sha256) FROM public.current_p1_return_signal_pointer pointer
  ) AND bundle.real_team_b AND bundle.mock_runtime_eligible;
  IF active_team_b_receipt_sha256 IS DISTINCT FROM p_team_b_integrity_receipt_sha256 THEN
    RAISE EXCEPTION 'automation Team B integrity receipt mismatch' USING ERRCODE='40001';
  END IF;
  SELECT COALESCE(gate_version,0)+1 INTO next_version
  FROM public.automation_activation_gate WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN next_version:=1; END IF;
  INSERT INTO public.automation_activation_gate(
    user_id,certification_status,clean_release_binding,real_team_b_pointer_active,
    release_binding_sha256,updated_at,gate_version,certification_receipt_sha256,
    certification_session_date,strategy_eligible_from_session_date,source_binding_sha256,
    team_b_integrity_receipt_sha256
  ) VALUES (
    p_user_id,'VALID',true,true,p_release_binding_sha256,statement_timestamp(),next_version,
    p_certification_receipt_sha256,p_certification_session,p_strategy_eligible_from_session,
    p_source_binding_sha256,p_team_b_integrity_receipt_sha256
  ) ON CONFLICT (user_id) DO UPDATE SET
    certification_status='VALID',clean_release_binding=true,real_team_b_pointer_active=true,
    release_binding_sha256=excluded.release_binding_sha256,updated_at=excluded.updated_at,
    gate_version=excluded.gate_version,
    certification_receipt_sha256=excluded.certification_receipt_sha256,
    certification_session_date=excluded.certification_session_date,
    strategy_eligible_from_session_date=excluded.strategy_eligible_from_session_date,
    source_binding_sha256=excluded.source_binding_sha256,
    team_b_integrity_receipt_sha256=excluded.team_b_integrity_receipt_sha256;
  event_seed:=p_user_id||':'||next_version::text||':ACTIVATION_GATE_AUTHORED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_user_id,p_strategy_eligible_from_session,NULL,'ACTIVATION_GATE_AUTHORED',
    encode(public.digest(convert_to(p_certification_receipt_sha256||p_release_binding_sha256||
      p_source_binding_sha256||p_team_b_integrity_receipt_sha256,'UTF8'),'sha256'),'hex'),
    true,statement_timestamp()
  );
  RETURN next_version;
END
$p1_author_automation_activation_gate_v2$;

CREATE FUNCTION public.p1_current_team_b_integrity_receipt_v1()
RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_current_team_b_integrity_receipt_v1$
  SELECT CASE
    WHEN session_user<>'decision_replay_authorizer'
      OR (SELECT count(*) FROM public.current_p1_return_signal_pointer)<>31
      OR (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)<>1
      THEN NULL
    ELSE (
      SELECT bundle.packet_sha256
      FROM public.p1_return_artifact_bundle bundle
      WHERE bundle.bundle_sha256=(
        SELECT min(pointer.bundle_sha256) FROM public.current_p1_return_signal_pointer pointer
      ) AND bundle.real_team_b AND bundle.mock_runtime_eligible
    )
  END
$p1_current_team_b_integrity_receipt_v1$;

CREATE FUNCTION public.p1_automation_runtime_readiness_v1(
  p_user_id text,p_target_session date
)
RETURNS TABLE(
  control_configured boolean,certification_valid boolean,release_source_bound boolean,
  real_team_b_ready boolean,principle_current boolean,kill_switch_inactive boolean,
  account_baseline_matches boolean,unresolved_state_clear boolean,target_available boolean,
  current_control_version integer,all_ready boolean
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_automation_runtime_readiness_v1$
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
  control_configured:=control_row.user_id IS NOT NULL AND control_row.control_state='DISARMED'
    AND control_row.brokerage_mode='KIS_MOCK' AND control_row.baseline_account_digest~'^[0-9a-f]{64}$';
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
  IF control_configured THEN
    observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=control_row.baseline_account_digest;
  unresolved_state_clear:=control_row.user_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=control_row.account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
  );
  target_available:=NOT EXISTS (
    SELECT 1 FROM public.automation_runtime_schedule schedule
    WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
      AND schedule.schedule_state IN ('ARMED','CLAIMED')
  );
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND certification_valid AND release_source_bound
    AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$p1_automation_runtime_readiness_v1$;

CREATE FUNCTION public.p1_start_automation_runtime_v1(
  p_user_id text,p_target_session date,p_expected_control_version integer
)
RETURNS TABLE(schedule_id text,control_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_start_automation_runtime_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE existing public.automation_runtime_schedule%ROWTYPE;
DECLARE readiness record;
DECLARE next_version integer;
DECLARE new_schedule_id text;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_expected_control_version<1 THEN
    RAISE EXCEPTION 'automation start scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation control unavailable' USING ERRCODE='P0002'; END IF;
  SELECT * INTO existing FROM public.automation_runtime_schedule
  WHERE user_id=p_user_id AND session_date=p_target_session FOR UPDATE;
  IF FOUND AND existing.schedule_state IN ('ARMED','CLAIMED')
     AND control_row.control_state='ARMED' THEN
    schedule_id:=existing.schedule_id;control_version:=control_row.version;replayed:=true;
    RETURN NEXT;RETURN;
  END IF;
  IF control_row.version<>p_expected_control_version OR control_row.control_state<>'DISARMED' THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  SELECT * INTO readiness FROM public.p1_automation_runtime_readiness_v1(p_user_id,p_target_session);
  IF NOT readiness.all_ready THEN
    RAISE EXCEPTION 'automation readiness gate closed' USING ERRCODE='40001';
  END IF;
  IF control_row.version=2147483647 THEN
    RAISE EXCEPTION 'automation control version exhausted' USING ERRCODE='40001';
  END IF;
  next_version:=control_row.version+1;
  UPDATE public.automation_control SET control_state='ARMED',version=next_version,
    updated_at=statement_timestamp() WHERE user_id=p_user_id;
  new_schedule_id:='auto_sched_'||substr(encode(public.digest(
    convert_to(p_user_id||':'||p_target_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (
    new_schedule_id,p_user_id,p_target_session,next_version,'ARMED',
    (p_target_session+time '08:55') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id,session_date) DO UPDATE SET
    control_version=excluded.control_version,schedule_state='ARMED',run_at=excluded.run_at,
    updated_at=excluded.updated_at;
  event_seed:=p_user_id||':'||p_target_session::text||':SCHEDULE_ARMED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_user_id,p_target_session,NULL,'SCHEDULE_ARMED',
    encode(public.digest(convert_to(new_schedule_id||':'||next_version::text,'UTF8'),'sha256'),'hex'),
    true,statement_timestamp()
  ) ON CONFLICT (event_id) DO NOTHING;
  schedule_id:=new_schedule_id;control_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_start_automation_runtime_v1$;

CREATE FUNCTION public.p1_stop_automation_runtime_v1(
  p_user_id text,p_expected_control_version integer
)
RETURNS TABLE(control_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_stop_automation_runtime_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE next_version integer;
DECLARE target_session date;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_expected_control_version<1 THEN
    RAISE EXCEPTION 'automation stop scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation control unavailable' USING ERRCODE='P0002'; END IF;
  IF control_row.version<>p_expected_control_version THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  IF control_row.control_state='DISARMED' THEN
    control_version:=control_row.version;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  IF control_row.control_state='HALTED' OR control_row.version=2147483647 THEN
    RAISE EXCEPTION 'automation control cannot be stopped automatically' USING ERRCODE='40001';
  END IF;
  next_version:=control_row.version+1;
  UPDATE public.automation_control SET control_state='DISARMED',version=next_version,
    updated_at=statement_timestamp() WHERE user_id=p_user_id;
  UPDATE public.automation_runtime_schedule SET schedule_state='DISARMED',updated_at=statement_timestamp()
  WHERE user_id=p_user_id AND schedule_state='ARMED';
  SELECT COALESCE(max(session_date),current_date) INTO target_session
  FROM public.automation_runtime_schedule WHERE user_id=p_user_id;
  event_seed:=p_user_id||':'||next_version::text||':SCHEDULE_DISARMED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_user_id,target_session,NULL,'SCHEDULE_DISARMED',
    encode(public.digest(convert_to(next_version::text,'UTF8'),'sha256'),'hex'),true,statement_timestamp()
  ) ON CONFLICT (event_id) DO NOTHING;
  control_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_stop_automation_runtime_v1$;

CREATE FUNCTION public.p1_roll_automation_schedule_v1(
  p_user_id text,p_completed_session date,p_next_session date,p_expected_control_version integer
)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_roll_automation_schedule_v1$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE prior_schedule public.automation_runtime_schedule%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE next_schedule_id text;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_next_session<=p_completed_session
     OR p_expected_control_version<1 THEN
    RAISE EXCEPTION 'automation roll input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  SELECT * INTO prior_schedule FROM public.automation_runtime_schedule
  WHERE user_id=p_user_id AND session_date=p_completed_session FOR UPDATE;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  IF control_row.user_id IS NULL OR control_row.control_state<>'ARMED'
     OR control_row.version<>p_expected_control_version
     OR prior_schedule.schedule_state<>'COMPLETED'
     OR gate_row.certification_status<>'VALID'
     OR gate_row.strategy_eligible_from_session_date IS NULL
     OR p_next_session<gate_row.strategy_eligible_from_session_date THEN
    RAISE EXCEPTION 'automation roll gate closed' USING ERRCODE='40001';
  END IF;
  next_schedule_id:='auto_sched_'||substr(encode(public.digest(
    convert_to(p_user_id||':'||p_next_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (
    next_schedule_id,p_user_id,p_next_session,control_row.version,'ARMED',
    (p_next_session+time '08:55') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id,session_date) DO NOTHING;
  IF NOT FOUND THEN
    SELECT schedule_id INTO next_schedule_id FROM public.automation_runtime_schedule
    WHERE user_id=p_user_id AND session_date=p_next_session AND schedule_state='ARMED';
    IF NOT FOUND THEN RAISE EXCEPTION 'automation roll conflict' USING ERRCODE='40001'; END IF;
  END IF;
  event_seed:=p_user_id||':'||p_next_session::text||':SCHEDULE_ARMED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_user_id,p_next_session,NULL,'SCHEDULE_ARMED',
    encode(public.digest(convert_to(next_schedule_id||':'||control_row.version::text,'UTF8'),'sha256'),'hex'),
    true,statement_timestamp()
  ) ON CONFLICT (event_id) DO NOTHING;
  RETURN next_schedule_id;
END
$p1_roll_automation_schedule_v1$;

CREATE FUNCTION public.p1_claim_automation_session_v1(
  p_session_date date,p_claim_token_hash text
)
RETURNS TABLE(
  user_id text,run_id text,control_version integer,account_id text,principle_id text,
  strategy_id text,baseline_account_digest text,replayed boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_claim_automation_session_v1$
DECLARE schedule_row public.automation_runtime_schedule%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE new_run_id text;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_session_date IS NULL
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation claim input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT schedule.* INTO schedule_row
  FROM public.automation_runtime_schedule schedule
  JOIN public.automation_runtime_claim claim USING (user_id,session_date)
  WHERE schedule.session_date=p_session_date AND schedule.schedule_state='CLAIMED'
    AND claim.claim_state='ACTIVE' AND claim.claim_token_hash=p_claim_token_hash
  ORDER BY schedule.user_id LIMIT 1 FOR UPDATE OF schedule;
  IF FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    PERFORM set_config('app.automation_owner_user_id',schedule_row.user_id,true);
    SELECT * INTO claim_row FROM public.automation_runtime_claim
      WHERE automation_runtime_claim.user_id=schedule_row.user_id
        AND automation_runtime_claim.session_date=p_session_date;
    SELECT * INTO control_row FROM public.automation_control WHERE automation_control.user_id=schedule_row.user_id;
    user_id:=schedule_row.user_id;run_id:=claim_row.run_id;control_version:=control_row.version;
    account_id:=control_row.account_id;principle_id:=control_row.principle_id;
    strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
    replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO schedule_row FROM public.automation_runtime_schedule
  WHERE session_date=p_session_date AND schedule_state='ARMED'
  ORDER BY user_id LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);RETURN;
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',schedule_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control
    WHERE automation_control.user_id=schedule_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.control_state<>'ARMED' OR control_row.version<>schedule_row.control_version
     OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RAISE EXCEPTION 'automation schedule control drift' USING ERRCODE='40001';
  END IF;
  new_run_id:='auto_run_'||substr(encode(public.digest(
    convert_to(schedule_row.user_id||':'||p_session_date::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runs(
    run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
    physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at
  ) VALUES (
    new_run_id,schedule_row.user_id,p_session_date,'SCHEDULED','KIS_MOCK',NULL,NULL,
    0,0,0,statement_timestamp(),statement_timestamp()
  );
  INSERT INTO public.automation_runtime_claim(
    user_id,session_date,run_id,claim_token_hash,claim_state,claimed_at,released_at
  ) VALUES (schedule_row.user_id,p_session_date,new_run_id,p_claim_token_hash,'ACTIVE',statement_timestamp(),NULL);
  INSERT INTO public.automation_runtime_checkpoint(
    run_id,user_id,session_date,checkpoint_version,state,selected_symbol,selected_side,decision_id,
    vertex_call_count,provider_call_count,logical_submit_count,updated_at
  ) VALUES (new_run_id,schedule_row.user_id,p_session_date,1,'SCHEDULED',NULL,NULL,NULL,0,0,0,statement_timestamp());
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,
    provider_calls,order_submits,sanitized
  ) VALUES
    (
      'auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':1:BASELINE_CAPTURED','UTF8'),'sha256'),'hex'),1,32),
      new_run_id,schedule_row.user_id,1,'BASELINE_CAPTURED',statement_timestamp(),
      encode(public.digest(convert_to(control_row.baseline_account_digest,'UTF8'),'sha256'),'hex'),0,0,true
    ),
    (
      'auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':2:RUN_TRANSITIONED','UTF8'),'sha256'),'hex'),1,32),
      new_run_id,schedule_row.user_id,2,'RUN_TRANSITIONED',statement_timestamp(),
      encode(public.digest(convert_to('SCHEDULED','UTF8'),'sha256'),'hex'),0,0,true
    );
  UPDATE public.automation_runtime_schedule SET schedule_state='CLAIMED',updated_at=statement_timestamp()
  WHERE schedule_id=schedule_row.schedule_id;
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
$p1_claim_automation_session_v1$;

CREATE FUNCTION public.p1_read_automation_runtime_state_v1(
  p_run_id text,p_claim_token_hash text
)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_read_automation_runtime_state_v1$
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
  no_open_order:=NOT EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=claim_row.user_id AND item.account_id=control_row.account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
  );
  daily_ready:=EXISTS (
    SELECT 1 FROM public.market_data_manifests manifest
    WHERE manifest.manifest_kind='DAILY' AND manifest.status='ACCEPTED'
      AND manifest.session_date<=claim_row.session_date
      AND manifest.as_of<=((claim_row.session_date+time '09:20') AT TIME ZONE 'Asia/Seoul')
  );
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'positionId',position.position_id,'accountId',position.account_id,'symbol',position.symbol,
    'entrySession',position.entry_session,'expirySession',position.expiry_session,
    'createdAt',position.created_at,'status',position.status,'closedAt',position.closed_at
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions position
  WHERE position.user_id=claim_row.user_id;
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
    'killSwitchActive',COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true),
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
$p1_read_automation_runtime_state_v1$;

CREATE FUNCTION public.p1_advance_automation_checkpoint_v1(
  p_run_id text,p_claim_token_hash text,p_tick_identity_hash text,p_expected_version integer,
  p_next_state text,p_selected_symbol text,p_selected_side text,p_decision_id text,p_vertex_call_count integer,
  p_provider_call_count integer,p_logical_submit_count integer,p_reservation_id text,
  p_limit_price_krw bigint,p_position_expiry_session date,p_order_id text,
  p_provider_order_ref_hash text,p_result_hash text,
  p_event_type text,p_event_payload_hash text
)
RETURNS TABLE(checkpoint_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_advance_automation_checkpoint_v1$
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE prior_tick public.automation_processed_ticks%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE next_version integer;
DECLARE sequence_value integer;
DECLARE event_seed text;
DECLARE terminal boolean;
DECLARE control_state text;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE effective_provider_order_ref_hash text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_result_hash!~'^sha256:[0-9a-f]{64}$' OR p_event_payload_hash!~'^[0-9a-f]{64}$'
     OR p_expected_version<1 OR p_vertex_call_count NOT BETWEEN 0 AND 1
     OR p_provider_call_count NOT BETWEEN 0 AND 16 OR p_logical_submit_count NOT BETWEEN 0 AND 1
     OR p_event_type NOT IN ('CONTROL_CHANGED','RUN_TRANSITIONED','BASELINE_CAPTURED','ACCOUNT_RECONCILED',
       'EXIT_SELECTED','BUY_SELECTED','NEWS_RESULT_RECORDED','RISK_RESULT_RECORDED','ORDER_RESERVED',
       'ORDER_OUTCOME_RECORDED','CANCEL_RECORDED','DRIFT_DETECTED','RUN_HALTED') THEN
    RAISE EXCEPTION 'automation checkpoint input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO prior_tick FROM public.automation_processed_ticks
  WHERE run_id=p_run_id AND tick_identity_hash=p_tick_identity_hash;
  IF FOUND THEN
    IF prior_tick.result_hash<>p_result_hash THEN
      RAISE EXCEPTION 'automation tick identity conflict' USING ERRCODE='23505';
    END IF;
    checkpoint_version:=prior_tick.checkpoint_version;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint
  WHERE run_id=p_run_id FOR UPDATE;
  IF NOT FOUND OR checkpoint_row.checkpoint_version<>p_expected_version
     OR NOT public.p1_automation_transition_valid_v1(checkpoint_row.state,p_next_state)
     OR p_vertex_call_count<checkpoint_row.vertex_call_count
     OR p_provider_call_count<checkpoint_row.provider_call_count
     OR p_logical_submit_count<checkpoint_row.logical_submit_count THEN
    RAISE EXCEPTION 'automation checkpoint CAS conflict' USING ERRCODE='40001';
  END IF;
  IF p_decision_id IS NOT NULL AND p_decision_id!~'^dec_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'automation decision identity invalid' USING ERRCODE='22023';
  END IF;
  IF checkpoint_row.decision_id IS NOT NULL AND p_decision_id IS DISTINCT FROM checkpoint_row.decision_id THEN
    RAISE EXCEPTION 'automation decision identity drift' USING ERRCODE='40001';
  END IF;
  SELECT control.control_state INTO control_state FROM public.automation_control control
  WHERE control.user_id=claim_row.user_id FOR SHARE;
  IF control_state='HALTED' OR (
    control_state='DISARMED' AND checkpoint_row.state NOT IN (
      'RECONCILING_PREVIOUS','ORDER_SUBMITTED','PENDING_RECONCILIATION'
    )
  ) THEN
    RAISE EXCEPTION 'automation control disallows new work' USING ERRCODE='40001';
  END IF;
  IF p_reservation_id IS NOT NULL THEN
    IF p_reservation_id!~'^auto_res_[0-9a-f]{32}$' OR p_selected_symbol!~'^[0-9]{6}$'
       OR p_selected_side NOT IN ('BUY','SELL') OR p_limit_price_krw<=0 THEN
      RAISE EXCEPTION 'automation reservation invalid' USING ERRCODE='22023';
    END IF;
    effective_provider_order_ref_hash:=p_provider_order_ref_hash;
    IF p_order_id IS NOT NULL THEN
      SELECT item.provider_order_ref_hash INTO effective_provider_order_ref_hash
      FROM public.orders item
      WHERE item.order_id=p_order_id AND item.user_id=claim_row.user_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'automation order identity unavailable' USING ERRCODE='40001';
      END IF;
      IF p_provider_order_ref_hash IS NOT NULL
         AND p_provider_order_ref_hash IS DISTINCT FROM effective_provider_order_ref_hash THEN
        RAISE EXCEPTION 'automation order reference hash drift' USING ERRCODE='40001';
      END IF;
    END IF;
    SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id FOR UPDATE;
    IF FOUND AND (reservation_row.reservation_id<>p_reservation_id
      OR reservation_row.symbol<>p_selected_symbol OR reservation_row.side<>p_selected_side
      OR reservation_row.limit_price_krw<>p_limit_price_krw
      OR p_logical_submit_count<reservation_row.logical_submit_count) THEN
      RAISE EXCEPTION 'automation reservation drift' USING ERRCODE='40001';
    END IF;
    INSERT INTO public.automation_order_reservations(
      reservation_id,run_id,user_id,session_date,symbol,side,quantity,limit_price_krw,
      logical_submit_count,order_id,provider_order_ref_hash,created_at,updated_at
    ) VALUES (
      p_reservation_id,p_run_id,claim_row.user_id,claim_row.session_date,p_selected_symbol,
      p_selected_side,1,p_limit_price_krw,p_logical_submit_count,p_order_id,
      effective_provider_order_ref_hash,statement_timestamp(),statement_timestamp()
    ) ON CONFLICT (run_id) DO UPDATE SET
      logical_submit_count=excluded.logical_submit_count,
      order_id=COALESCE(automation_order_reservations.order_id,excluded.order_id),
      provider_order_ref_hash=COALESCE(
        automation_order_reservations.provider_order_ref_hash,excluded.provider_order_ref_hash
      ),updated_at=excluded.updated_at;
  ELSIF p_logical_submit_count<>0 OR p_order_id IS NOT NULL OR p_provider_order_ref_hash IS NOT NULL THEN
    RAISE EXCEPTION 'automation submit lacks reservation' USING ERRCODE='22023';
  END IF;
  next_version:=checkpoint_row.checkpoint_version+1;
  UPDATE public.automation_runtime_checkpoint AS checkpoint SET
    checkpoint_version=next_version,state=p_next_state,selected_symbol=p_selected_symbol,
    selected_side=p_selected_side,decision_id=COALESCE(checkpoint.decision_id,p_decision_id),
    vertex_call_count=p_vertex_call_count,
    provider_call_count=p_provider_call_count,logical_submit_count=p_logical_submit_count,
    updated_at=statement_timestamp()
  WHERE checkpoint.run_id=p_run_id AND checkpoint.checkpoint_version=p_expected_version;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation checkpoint CAS lost' USING ERRCODE='40001'; END IF;
  UPDATE public.automation_runs SET state=p_next_state,selected_symbol=p_selected_symbol,
    selected_side=p_selected_side,physical_submit_count=p_logical_submit_count,
    vertex_call_count=p_vertex_call_count,provider_calls=p_provider_call_count,
    updated_at=statement_timestamp() WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  event_seed:=p_run_id||':'||sequence_value::text||':'||p_event_type||':'||p_event_payload_hash;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,
    provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,p_event_type,statement_timestamp(),p_event_payload_hash,
    p_provider_call_count,p_logical_submit_count,true
  );
  INSERT INTO public.automation_processed_ticks(
    run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at
  ) VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(p_run_id||':'||next_version::text,'UTF8'),'sha256'),'hex'),1,32),
    claim_row.user_id,claim_row.session_date,p_run_id,'CHECKPOINT_TRANSITIONED',
    p_event_payload_hash,true,statement_timestamp()
  );
  terminal:=p_next_state IN ('NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION',
    'SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED');
  IF p_next_state='COMPLETED' THEN
    SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id;
    IF FOUND THEN
      SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
      IF reservation_row.side='BUY' THEN
        IF p_position_expiry_session IS NULL OR p_position_expiry_session<=claim_row.session_date THEN
          RAISE EXCEPTION 'automation BUY expiry invalid' USING ERRCODE='22023';
        END IF;
        INSERT INTO public.automation_positions(
          position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
          bot_owned,short_allowed,created_at,closed_at
        ) VALUES (
          'auto_pos_'||substr(encode(public.digest(convert_to(
            p_run_id||':'||reservation_row.symbol||':'||claim_row.session_date::text,
            'UTF8'),'sha256'),'hex'),1,32),
          claim_row.user_id,control_row.account_id,reservation_row.symbol,1,claim_row.session_date,
          p_position_expiry_session,'OPEN',true,false,statement_timestamp(),NULL
        );
      ELSE
        UPDATE public.automation_positions SET status='CLOSED',closed_at=statement_timestamp()
        WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
          AND symbol=reservation_row.symbol AND status IN ('OPEN','EXIT_PENDING');
        IF NOT FOUND THEN
          RAISE EXCEPTION 'automation SELL lot unavailable' USING ERRCODE='40001';
        END IF;
      END IF;
    ELSIF p_position_expiry_session IS NOT NULL THEN
      RAISE EXCEPTION 'automation position lacks reservation' USING ERRCODE='22023';
    END IF;
  ELSIF p_position_expiry_session IS NOT NULL THEN
    RAISE EXCEPTION 'automation expiry only applies to completed BUY' USING ERRCODE='22023';
  END IF;
  IF terminal THEN
    UPDATE public.automation_runtime_claim SET claim_state='RELEASED',released_at=statement_timestamp()
    WHERE run_id=p_run_id;
    UPDATE public.automation_runtime_schedule SET
      schedule_state=CASE WHEN p_next_state='HALTED' THEN 'HALTED' ELSE 'COMPLETED' END,
      updated_at=statement_timestamp()
    WHERE user_id=claim_row.user_id AND session_date=claim_row.session_date;
    INSERT INTO public.automation_runtime_events(
      event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
    ) VALUES (
      'auto_rte_'||substr(encode(public.digest(convert_to(p_run_id||':SESSION_RELEASED','UTF8'),'sha256'),'hex'),1,32),
      claim_row.user_id,claim_row.session_date,p_run_id,'SESSION_RELEASED',
      substr(p_result_hash,8),true,statement_timestamp()
    );
  END IF;
  checkpoint_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_advance_automation_checkpoint_v1$;

ALTER FUNCTION public.p1_automation_transition_valid_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_runtime_account_digest_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_runtime_state_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_author_automation_activation_gate_v2(text,text,date,date,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_current_team_b_integrity_receipt_v1() OWNER TO flyway;
ALTER FUNCTION public.p1_automation_runtime_readiness_v1(text,date) OWNER TO flyway;
ALTER FUNCTION public.p1_start_automation_runtime_v1(text,date,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_stop_automation_runtime_v1(text,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_roll_automation_schedule_v1(text,date,date,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_claim_automation_session_v1(date,text) OWNER TO flyway;
ALTER FUNCTION public.p1_advance_automation_checkpoint_v1(
  text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,date,text,text,text,text,text
) OWNER TO flyway;

REVOKE ALL ON TABLE public.automation_runtime_schedule,public.automation_runtime_claim,
  public.automation_runtime_checkpoint,public.automation_processed_ticks,
  public.automation_order_reservations,public.automation_runtime_events
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,
    decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_automation_transition_valid_v1(text,text),
  public.p1_automation_runtime_account_digest_v1(text,text),
  public.p1_read_automation_runtime_state_v1(text,text),
  public.p1_author_automation_activation_gate_v2(text,text,date,date,text,text,text),
  public.p1_current_team_b_integrity_receipt_v1(),
  public.p1_automation_runtime_readiness_v1(text,date),
  public.p1_start_automation_runtime_v1(text,date,integer),
  public.p1_stop_automation_runtime_v1(text,integer),
  public.p1_roll_automation_schedule_v1(text,date,date,integer),
  public.p1_claim_automation_session_v1(date,text),
  public.p1_advance_automation_checkpoint_v1(
    text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,date,text,text,text,text,text
  ) FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,
    decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_author_automation_activation_gate_v2(
  text,text,date,date,text,text,text
), public.p1_current_team_b_integrity_receipt_v1() TO decision_replay_authorizer;
GRANT EXECUTE ON FUNCTION public.p1_automation_runtime_readiness_v1(text,date),
  public.p1_read_automation_runtime_state_v1(text,text),
  public.p1_start_automation_runtime_v1(text,date,integer),
  public.p1_stop_automation_runtime_v1(text,integer),
  public.p1_roll_automation_schedule_v1(text,date,date,integer),
  public.p1_claim_automation_session_v1(date,text),
  public.p1_advance_automation_checkpoint_v1(
    text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,date,text,text,text,text,text
  ) TO decision_automation_runtime;
REVOKE CREATE ON SCHEMA public FROM decision_automation_runtime;
