-- Reconcile owner initiated KIS_MOCK activity without turning an explainable
-- account delta into a permanent automation stop.
-- Keep full broker positions for account identity, while owner-authorized decisions
-- exclude non-bot holdings from automation-only exposure limits.

ALTER TABLE public.automation_account_lineage
  DROP CONSTRAINT automation_account_lineage_reason_check;
ALTER TABLE public.automation_account_lineage
  ADD CONSTRAINT automation_account_lineage_reason_check CHECK (
    reason IN (
      'ARM_BASELINE','BUY_FILL','SELL_FILL','RECOVERY_BASELINE','EXTERNAL_RECONCILIATION'
    )
  );

CREATE TABLE public.automation_account_sync_events_v231 (
  event_id text PRIMARY KEY CHECK (event_id ~ '^auto_sync_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  run_id text REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  event_type text NOT NULL CHECK (event_type IN ('ACCOUNT_RECONCILED','HALTED_RECOVERED')),
  prior_digest text NOT NULL CHECK (prior_digest ~ '^[0-9a-f]{64}$'),
  next_digest text NOT NULL CHECK (next_digest ~ '^[0-9a-f]{64}$'),
  order_snapshot_sha256 text NOT NULL CHECK (order_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  quarantined_symbols jsonb NOT NULL CHECK (
    jsonb_typeof(quarantined_symbols)='array'
    AND octet_length(quarantined_symbols::text)<=8192
  ),
  occurred_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
ALTER TABLE public.automation_account_sync_events_v231 OWNER TO flyway;
ALTER TABLE public.automation_account_sync_events_v231 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_account_sync_events_v231 FORCE ROW LEVEL SECURITY;
CREATE POLICY automation_account_sync_events_runtime_v231
  ON public.automation_account_sync_events_v231 TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=current_setting('app.automation_owner_user_id',true)
  )
  WITH CHECK (
    current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=current_setting('app.automation_owner_user_id',true)
  );
CREATE TRIGGER automation_account_sync_events_append_only_v231
  BEFORE UPDATE OR DELETE ON public.automation_account_sync_events_v231
  FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

-- Keep one automation-risk equity baseline for each owner/account/session. Account rebases
-- update the expected broker projection, but must never reset the session loss denominator.
CREATE TABLE public.automation_risk_session_baselines_v231 (
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id ~ '^acct_[0-9a-f]{32}$'),
  session_date date NOT NULL,
  baseline_equity_krw bigint NOT NULL CHECK (baseline_equity_krw > 0),
  baseline_cash_snapshot_krw bigint NOT NULL CHECK (baseline_cash_snapshot_krw >= 0),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(user_id,account_id,session_date)
);
ALTER TABLE public.automation_risk_session_baselines_v231 OWNER TO flyway;
REVOKE ALL ON public.automation_risk_session_baselines_v231 FROM PUBLIC,decision_app,decision_automation_runtime;

CREATE FUNCTION public.p1_get_or_initialize_automation_risk_baseline_v231(
  p_run_id text,p_claim_token_hash text,p_observed_equity_krw bigint,p_observed_cash_krw bigint,
  p_recover_halted boolean,p_as_of timestamptz
)
RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_get_or_initialize_automation_risk_baseline_v231$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE account_value text;
DECLARE control_state_value text;
DECLARE baseline_value bigint;
DECLARE release_valid boolean := false;
DECLARE halted_recovery_valid boolean := false;
DECLARE baseline_exists boolean := false;
DECLARE local_now timestamp without time zone;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id IS NULL
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$' OR p_claim_token_hash IS NULL
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_observed_equity_krw IS NULL OR p_observed_equity_krw<=0
     OR p_observed_cash_krw IS NULL OR p_observed_cash_krw<0
     OR p_recover_halted IS NULL OR p_as_of IS NULL THEN
    RAISE EXCEPTION 'automation risk baseline scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation risk baseline claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT control.account_id,control.control_state INTO account_value,control_state_value
  FROM public.automation_control control
  WHERE control.user_id=claim_row.user_id;
  IF NOT FOUND OR account_value IS NULL THEN
    RAISE EXCEPTION 'automation risk baseline account unavailable' USING ERRCODE='42501';
  END IF;
  SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
  IF NOT FOUND OR run_row.user_id<>claim_row.user_id THEN
    RAISE EXCEPTION 'automation risk baseline run unavailable' USING ERRCODE='42501';
  END IF;
  IF claim_row.claim_state<>'ACTIVE' THEN
    release_valid:=public.p1_automation_released_continuation_claim_valid_v231(
      p_run_id,p_claim_token_hash,claim_row.user_id,run_row.principle_id,p_as_of
    );
    IF p_recover_halted AND claim_row.claim_state='RELEASED'
       AND control_state_value='HALTED' AND run_row.state='HALTED'
       AND run_row.physical_submit_count=0 AND run_row.provider_calls=0
       AND run_row.selected_symbol IS NULL AND run_row.selected_side IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM public.automation_order_reservations WHERE run_id=p_run_id
       )
       AND NOT EXISTS (
         SELECT 1 FROM public.orders item
         WHERE item.user_id=claim_row.user_id AND item.account_id=account_value
           AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
             OR item.reconciliation_status='MISMATCH')
       )
       AND EXISTS (
         SELECT 1 FROM public.automation_events event
         WHERE event.run_id=p_run_id AND event.event_type='DRIFT_DETECTED'
           AND event.payload_hash='09ed4e6112ac2721795539344195397292b9248845ffb8b409a58166bd5c3f31'
       ) THEN
      halted_recovery_valid:=true;
    END IF;
    IF NOT release_valid AND NOT halted_recovery_valid THEN
      RAISE EXCEPTION 'automation risk baseline claim unavailable' USING ERRCODE='42501';
    END IF;
  END IF;
  SELECT baseline_equity_krw INTO baseline_value
  FROM public.automation_risk_session_baselines_v231
  WHERE user_id=claim_row.user_id AND account_id=account_value
    AND session_date=claim_row.session_date;
  baseline_exists:=FOUND;
  IF NOT baseline_exists THEN
    IF claim_row.claim_state='ACTIVE' THEN
      local_now:=p_as_of AT TIME ZONE 'Asia/Seoul';
      IF local_now::date<>claim_row.session_date OR local_now::time>time '09:45'
         OR run_row.state NOT IN ('SCHEDULED','PRECHECK')
         OR run_row.physical_submit_count<>0 OR run_row.selected_symbol IS NOT NULL
         OR run_row.selected_side IS NOT NULL
         OR EXISTS (SELECT 1 FROM public.automation_order_reservations WHERE run_id=p_run_id) THEN
        RAISE EXCEPTION 'automation risk baseline late initialization denied' USING ERRCODE='40001';
      END IF;
    ELSIF NOT halted_recovery_valid THEN
      -- A completed same-session continuation must keep its earlier loss baseline;
      -- never seed one from the later balance after the row has gone missing.
      RAISE EXCEPTION 'automation risk baseline missing for continuation' USING ERRCODE='40001';
    END IF;
  END IF;
  INSERT INTO public.automation_risk_session_baselines_v231(
    user_id,account_id,session_date,baseline_equity_krw,baseline_cash_snapshot_krw
  ) VALUES (
    claim_row.user_id,account_value,claim_row.session_date,p_observed_equity_krw,p_observed_cash_krw
  )
  ON CONFLICT (user_id,account_id,session_date) DO NOTHING;
  SELECT baseline_equity_krw INTO baseline_value
  FROM public.automation_risk_session_baselines_v231
  WHERE user_id=claim_row.user_id AND account_id=account_value
    AND session_date=claim_row.session_date;
  IF baseline_value IS NULL OR baseline_value<=0 THEN
    RAISE EXCEPTION 'automation risk baseline unavailable' USING ERRCODE='40001';
  END IF;
  RETURN baseline_value;
END
$p1_get_or_initialize_automation_risk_baseline_v231$;

CREATE FUNCTION public.sync_automation_risk_baseline_cash_snapshot_v231()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog
AS $sync_automation_risk_baseline_cash_snapshot_v231$
BEGIN
  IF session_user='decision_app' AND current_user='flyway' THEN
    IF current_setting('app.actor_user_id',true) IS DISTINCT FROM NEW.user_id
       OR NOT public.actor_rls_scope_is_open_v1() THEN
      RAISE EXCEPTION 'automation risk cash snapshot owner scope denied' USING ERRCODE='42501';
    END IF;
    -- Arming captures a new account projection before the session baseline exists.
    -- Do not rewrite the prior session's cash snapshot from that control update.
    RETURN NEW;
  END IF;
  IF session_user<>'decision_automation_runtime' OR current_user<>'flyway'
     OR current_setting('app.automation_owner_user_id',true) IS DISTINCT FROM NEW.user_id THEN
    RAISE EXCEPTION 'automation risk cash snapshot scope denied' USING ERRCODE='42501';
  END IF;
  IF NEW.expected_account_projection_v2 IS NOT NULL THEN
    UPDATE public.automation_risk_session_baselines_v231 baseline
    SET baseline_cash_snapshot_krw=(NEW.expected_account_projection_v2->>'cashKrw')::bigint
    WHERE baseline.user_id=NEW.user_id AND baseline.account_id=NEW.account_id
      AND baseline.session_date=(
        SELECT max(current_baseline.session_date)
        FROM public.automation_risk_session_baselines_v231 current_baseline
        WHERE current_baseline.user_id=NEW.user_id AND current_baseline.account_id=NEW.account_id
      );
  END IF;
  RETURN NEW;
END
$sync_automation_risk_baseline_cash_snapshot_v231$;
ALTER FUNCTION public.sync_automation_risk_baseline_cash_snapshot_v231() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.sync_automation_risk_baseline_cash_snapshot_v231()
  FROM PUBLIC,decision_app,decision_automation_runtime;
CREATE TRIGGER automation_risk_baseline_cash_snapshot_v231
  AFTER UPDATE OF expected_account_projection_v2 ON public.automation_control
  FOR EACH ROW
  WHEN (OLD.expected_account_projection_v2 IS DISTINCT FROM NEW.expected_account_projection_v2)
  EXECUTE FUNCTION public.sync_automation_risk_baseline_cash_snapshot_v231();

CREATE FUNCTION public.p1_automation_released_continuation_claim_valid_v231(
  p_run_id text,p_claim_token_hash text,p_user_id text,p_principle_id text,p_as_of timestamptz
)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_released_continuation_claim_valid_v231$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE checkpoint_state text;
DECLARE local_now timestamp without time zone;
BEGIN
  IF session_user NOT IN ('decision_automation_runtime','decision_app')
     OR p_run_id IS NULL OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash IS NULL OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_user_id IS NULL OR p_principle_id IS NULL OR p_as_of IS NULL THEN
    RETURN false;
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim claim
  WHERE claim.run_id=p_run_id AND claim.claim_token_hash=p_claim_token_hash
    AND claim.user_id=p_user_id;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND OR claim_row.claim_state<>'RELEASED' THEN RETURN false; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO run_row FROM public.automation_runs run
  WHERE run.run_id=p_run_id AND run.user_id=p_user_id AND run.principle_id=p_principle_id;
  IF NOT FOUND THEN RETURN false; END IF;
  SELECT checkpoint.state INTO checkpoint_state
  FROM public.automation_runtime_checkpoint checkpoint WHERE checkpoint.run_id=p_run_id;
  local_now:=p_as_of AT TIME ZONE 'Asia/Seoul';
  RETURN checkpoint_state IN ('COMPLETED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION')
    AND run_row.state=checkpoint_state
    AND claim_row.session_date=local_now::date
    AND local_now::time<=time '15:20';
END
$p1_automation_released_continuation_claim_valid_v231$;
ALTER FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz)
  TO decision_automation_runtime;

CREATE FUNCTION public.p1_list_recoverable_automation_account_halts_v1()
RETURNS TABLE(
  user_id text,run_id text,session_date date,control_version integer,account_id text,
  principle_id text,strategy_id text,baseline_account_digest text,claim_token_hash text,
  expected_account_projection jsonb,expected_account_digest text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_list_recoverable_automation_account_halts_v1$
DECLARE owner_value text;
DECLARE owner_values text[];
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation account recovery scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT COALESCE(array_agg(owners.user_id ORDER BY owners.user_id),'{}'::text[])
  INTO owner_values
  FROM (
    SELECT control.user_id FROM public.automation_control control
    WHERE control.control_state='HALTED' AND control.brokerage_mode='KIS_MOCK'
    ORDER BY control.user_id LIMIT 100
  ) owners;
  PERFORM set_config('app.automation_claim_scan','0',true);
  FOREACH owner_value IN ARRAY owner_values LOOP
    PERFORM set_config('app.automation_owner_user_id',owner_value,true);
    RETURN QUERY
    SELECT control.user_id,run.run_id,run.session_date,control.version,control.account_id,
      control.principle_id,control.strategy_id,control.baseline_account_digest,
      claim.claim_token_hash,control.expected_account_projection_v2,
      control.expected_account_digest_v2
    FROM public.automation_control control
    JOIN public.automation_runtime_schedule schedule
      ON schedule.user_id=control.user_id AND schedule.schedule_state='HALTED'
    JOIN public.automation_runtime_claim claim
      ON claim.user_id=schedule.user_id AND claim.session_date=schedule.session_date
     AND claim.claim_state='RELEASED'
    JOIN public.automation_runs run ON run.run_id=claim.run_id
    JOIN public.automation_runtime_checkpoint checkpoint ON checkpoint.run_id=run.run_id
    WHERE control.user_id=owner_value AND control.control_state='HALTED'
      AND control.brokerage_mode='KIS_MOCK' AND NOT control.kill_switch_active
      AND NOT public.owner_stop_active(owner_value)
      AND NOT COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true)
      AND run.state='HALTED' AND checkpoint.state='HALTED'
      AND run.physical_submit_count=0 AND run.provider_calls=0
      AND run.selected_symbol IS NULL AND run.selected_side IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM public.automation_order_reservations reservation
        WHERE reservation.run_id=run.run_id
      )
      AND EXISTS (
        SELECT 1 FROM public.automation_events event
        WHERE event.run_id=run.run_id AND event.event_type='DRIFT_DETECTED'
          AND event.payload_hash='09ed4e6112ac2721795539344195397292b9248845ffb8b409a58166bd5c3f31'
      )
    ORDER BY schedule.session_date DESC LIMIT 1;
  END LOOP;
  PERFORM set_config('app.automation_owner_user_id','',true);
END
$p1_list_recoverable_automation_account_halts_v1$;

CREATE FUNCTION public.p1_automation_account_lineage_digest_v1(p_account_projection jsonb)
RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog
AS $p1_automation_account_lineage_digest_v1$
DECLARE risk_projection jsonb;
DECLARE lineage_json_text text;
BEGIN
  IF p_account_projection IS NULL
     OR jsonb_typeof(p_account_projection)<>'object'
     OR p_account_projection->>'accountId' IS NULL
     OR p_account_projection->>'cashKrw' IS NULL
     OR (p_account_projection->>'cashKrw') !~ '^(0|[1-9][0-9]{0,18})$'
     OR jsonb_typeof(p_account_projection->'positions') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'automation account lineage projection invalid' USING ERRCODE='22023';
  END IF;
  SELECT jsonb_build_object(
    'accountId',p_account_projection->>'accountId',
    'cashKrw',(p_account_projection->>'cashKrw')::bigint::text,
    'schemaVersion',2,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'quantity',(item->>'quantity')::bigint::text,'symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_account_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO risk_projection;
  IF NOT public.p1_automation_structural_projection_valid_v2(risk_projection) THEN
    RAISE EXCEPTION 'automation account lineage structure invalid' USING ERRCODE='22023';
  END IF;
  -- Match AccountLineageSnapshot.projection() + canonical_json_bytes(): lexical key
  -- order, compact JSON and one trailing newline. jsonb::text uses another key order.
  SELECT '{"accountId":'||to_json(p_account_projection->>'accountId')::text||
    ',"cashKrw":'||(p_account_projection->>'cashKrw')::bigint::text||
    ',"positions":['||COALESCE(string_agg(
      '{"quantity":'||(item->>'quantity')::bigint::text||
      ',"symbol":'||to_json(item->>'symbol')::text||'}',',' ORDER BY item->>'symbol'
    ),'')||']}' INTO lineage_json_text
  FROM jsonb_array_elements(p_account_projection->'positions') item;
  RETURN encode(public.digest(convert_to(lineage_json_text||E'\n','UTF8'),'sha256'),'hex');
END
$p1_automation_account_lineage_digest_v1$;

CREATE FUNCTION public.p1_reconcile_automation_account_v1(
  p_run_id text,p_claim_token_hash text,p_expected_control_version integer,
  p_expected_account_digest text,p_account_projection jsonb,p_lineage_digest text,
  p_order_snapshot_sha256 text,
  p_open_order_count integer,p_next_session date,p_recover_halted boolean
)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_reconcile_automation_account_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE prior_digest_value text;
DECLARE observed_lineage_digest text;
DECLARE next_risk_projection jsonb;
DECLARE next_digest_value text;
DECLARE risk_changed boolean;
DECLARE mismatched_symbols jsonb;
DECLARE sequence_value integer;
DECLARE next_version integer;
DECLARE event_type_value text;
DECLARE schedule_state_value text;
DECLARE risk_cash_delta bigint;
DECLARE risk_baseline_row public.automation_risk_session_baselines_v231%ROWTYPE;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id IS NULL OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash IS NULL OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_expected_control_version IS NULL OR p_expected_control_version<1
     OR p_expected_account_digest IS NULL OR p_expected_account_digest!~'^[0-9a-f]{64}$'
     OR p_account_projection IS NULL
     OR p_lineage_digest IS NULL OR p_lineage_digest!~'^[0-9a-f]{64}$'
     OR p_order_snapshot_sha256 IS NULL OR p_order_snapshot_sha256!~'^[0-9a-f]{64}$'
     OR p_open_order_count IS NULL OR p_open_order_count<0
     OR p_next_session IS NULL OR p_recover_halted IS NULL
     OR jsonb_typeof(p_account_projection)<>'object'
     OR p_account_projection-ARRAY[
       'accountId','cashKrw','marginRequirementKrw','portfolioEquityKrw',
       'positions','positionsComplete'
     ]<>'{}'::jsonb
     OR p_account_projection->>'accountId' IS NULL
     OR p_account_projection->>'positionsComplete' IS DISTINCT FROM 'true'
     OR p_account_projection->>'marginRequirementKrw' IS DISTINCT FROM '0'
     OR p_account_projection->>'cashKrw' IS NULL
     OR (p_account_projection->>'cashKrw') !~ '^(0|[1-9][0-9]{0,18})$'
     OR p_account_projection->>'portfolioEquityKrw' IS NULL
     OR (p_account_projection->>'portfolioEquityKrw') !~ '^(0|[1-9][0-9]{0,18})$'
     OR jsonb_typeof(p_account_projection->'positions') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'automation account reconciliation input invalid' USING ERRCODE='22023';
  END IF;
  IF p_open_order_count<>0 THEN
    RAISE EXCEPTION 'automation account has unresolved KIS orders' USING ERRCODE='40001';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation account reconciliation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation account reconciliation run unavailable' USING ERRCODE='42501';
  END IF;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.account_id<>p_account_projection->>'accountId'
     OR control_row.brokerage_mode<>'KIS_MOCK'
     OR control_row.version<>p_expected_control_version
     OR control_row.expected_account_digest_v2 IS DISTINCT FROM p_expected_account_digest
     OR NOT public.p1_automation_structural_projection_valid_v2(
       jsonb_build_object(
         'accountId',p_account_projection->>'accountId',
         'cashKrw',(p_account_projection->>'cashKrw')::bigint::text,
         'schemaVersion',2,
         'positions',COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
             'quantity',(item->>'quantity')::bigint::text,'symbol',item->>'symbol'
           ) ORDER BY item->>'symbol')
           FROM jsonb_array_elements(p_account_projection->'positions') item
         ),'[]'::jsonb)
       )
     ) THEN
    RAISE EXCEPTION 'automation account reconciliation identity invalid' USING ERRCODE='22023';
  END IF;
  IF p_recover_halted THEN
    IF control_row.control_state<>'HALTED' OR claim_row.claim_state<>'RELEASED'
       OR run_row.state<>'HALTED' OR run_row.physical_submit_count<>0
       OR run_row.provider_calls<>0 OR run_row.selected_symbol IS NOT NULL
       OR run_row.selected_side IS NOT NULL
       OR EXISTS (SELECT 1 FROM public.automation_order_reservations WHERE run_id=p_run_id)
       OR NOT EXISTS (
         SELECT 1 FROM public.automation_events event
         WHERE event.run_id=p_run_id AND event.event_type='DRIFT_DETECTED'
           AND event.payload_hash='09ed4e6112ac2721795539344195397292b9248845ffb8b409a58166bd5c3f31'
       )
       OR p_next_session<=claim_row.session_date
       OR NOT EXISTS (
         SELECT 1 FROM public.trading_sessions session
         WHERE session.exchange_mic='XKRX' AND session.is_open
           AND session.session_date=p_next_session
       ) THEN
      RAISE EXCEPTION 'automation halted account recovery gate closed' USING ERRCODE='40001';
    END IF;
  ELSIF control_row.control_state<>'ARMED'
     OR NOT (
       claim_row.claim_state='ACTIVE'
       OR public.p1_automation_released_continuation_claim_valid_v231(
         p_run_id,p_claim_token_hash,claim_row.user_id,run_row.principle_id,statement_timestamp()
       )
     )
     OR run_row.state NOT IN (
       'SCHEDULED','PRECHECK','ORDER_SIZING','ORDER_SUBMITTING',
       'COMPLETED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION'
     ) THEN
    RAISE EXCEPTION 'automation account reconciliation gate closed' USING ERRCODE='40001';
  END IF;
  IF control_row.kill_switch_active OR public.owner_stop_active(claim_row.user_id)
     OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true) THEN
    RAISE EXCEPTION 'automation account reconciliation stop active' USING ERRCODE='40001';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=claim_row.user_id AND item.account_id=control_row.account_id
      AND (item.status IN (
        'SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED'
      ) OR item.reconciliation_status='MISMATCH')
  ) THEN
    RAISE EXCEPTION 'automation account has unresolved local orders' USING ERRCODE='40001';
  END IF;

  SELECT COALESCE(jsonb_agg(symbol ORDER BY symbol),'[]'::jsonb) INTO mismatched_symbols
  FROM (
    SELECT managed.symbol FROM (
      SELECT position.symbol,sum(position.quantity)::bigint AS quantity
      FROM public.automation_positions position
      WHERE position.user_id=claim_row.user_id AND position.account_id=control_row.account_id
        AND position.bot_owned AND position.policy_id IS NOT NULL
        AND position.status IN ('OPEN','EXIT_PENDING')
      GROUP BY position.symbol
    ) managed
    WHERE managed.quantity<>COALESCE((
      SELECT sum((item->>'quantity')::bigint)
      FROM jsonb_array_elements(p_account_projection->'positions') item
      WHERE item->>'symbol'=managed.symbol
    ),0)
  ) mismatch;
  UPDATE public.automation_positions position SET status='HALTED_MISMATCH'
  WHERE position.user_id=claim_row.user_id AND position.account_id=control_row.account_id
    AND position.bot_owned AND position.policy_id IS NOT NULL
    AND position.status IN ('OPEN','EXIT_PENDING')
    AND position.symbol IN (SELECT jsonb_array_elements_text(mismatched_symbols));

  SELECT COALESCE(
    (SELECT lineage.next_digest FROM public.automation_account_lineage lineage
     WHERE lineage.user_id=claim_row.user_id ORDER BY lineage.sequence DESC LIMIT 1),
    control_row.initial_account_digest_v2,control_row.expected_account_digest_v2
  ) INTO prior_digest_value;
  observed_lineage_digest:=public.p1_automation_account_lineage_digest_v1(p_account_projection);
  IF observed_lineage_digest<>p_lineage_digest THEN
    RAISE EXCEPTION 'automation account lineage digest mismatch' USING ERRCODE='40001';
  END IF;
  SELECT jsonb_build_object(
    'accountId',p_account_projection->>'accountId',
    'cashKrw',(p_account_projection->>'cashKrw')::bigint::text,
    'schemaVersion',2,
    'positions',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'quantity',(item->>'quantity')::bigint::text,'symbol',item->>'symbol'
      ) ORDER BY item->>'symbol')
      FROM jsonb_array_elements(p_account_projection->'positions') item
    ),'[]'::jsonb)
  ) INTO next_risk_projection;
  next_digest_value:=encode(public.digest(convert_to(next_risk_projection::text,'UTF8'),'sha256'),'hex');
  IF NOT public.p1_automation_structural_projection_valid_v2(next_risk_projection) THEN
    RAISE EXCEPTION 'automation account reconciliation projection invalid' USING ERRCODE='22023';
  END IF;
  risk_changed:=p_expected_account_digest<>next_digest_value;
  SELECT * INTO risk_baseline_row
  FROM public.automation_risk_session_baselines_v231
  WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
    AND session_date=claim_row.session_date FOR UPDATE;
  IF FOUND THEN
    risk_cash_delta:=(p_account_projection->>'cashKrw')::bigint-
      risk_baseline_row.baseline_cash_snapshot_krw;
    IF risk_cash_delta<>0 THEN
      -- External manual buys/sells change real cash and buying power. Shift only
      -- the loss denominator by the cash flow since the last successful account
      -- sync, without replacing the strategy's cumulative P&L baseline.
      UPDATE public.automation_risk_session_baselines_v231
      SET baseline_equity_krw=baseline_equity_krw+risk_cash_delta
      WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
        AND session_date=claim_row.session_date
        AND baseline_equity_krw+risk_cash_delta>0;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'automation risk baseline external cash delta invalid' USING ERRCODE='40001';
      END IF;
    END IF;
  END IF;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value
  FROM public.automation_account_lineage WHERE user_id=claim_row.user_id;
  IF prior_digest_value<>p_lineage_digest OR p_recover_halted THEN
    INSERT INTO public.automation_account_lineage(
      lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
      filled_quantity,average_fill_price_krw,occurred_at
    ) VALUES (
      'auto_acl_'||substr(encode(public.digest(convert_to(
        claim_row.user_id||':'||sequence_value::text||':EXTERNAL_RECONCILIATION:'||p_run_id,
        'UTF8'),'sha256'),'hex'),1,32),
      claim_row.user_id,p_run_id,sequence_value,'EXTERNAL_RECONCILIATION',
      prior_digest_value,p_lineage_digest,NULL,NULL,NULL,statement_timestamp()
    );
  END IF;
  event_type_value:=CASE WHEN p_recover_halted THEN 'HALTED_RECOVERED' ELSE 'ACCOUNT_RECONCILED' END;
  IF risk_changed OR p_recover_halted OR jsonb_array_length(mismatched_symbols)>0 THEN
    INSERT INTO public.automation_account_sync_events_v231(
      event_id,user_id,account_id,run_id,event_type,prior_digest,next_digest,
      order_snapshot_sha256,quarantined_symbols,occurred_at
      ) VALUES (
      'auto_sync_'||substr(encode(public.digest(convert_to(
        claim_row.user_id||':'||p_run_id||':'||event_type_value||':'||next_digest_value||':'||p_order_snapshot_sha256,
        'UTF8'),'sha256'),'hex'),1,32),
      claim_row.user_id,control_row.account_id,p_run_id,event_type_value,
      p_expected_account_digest,next_digest_value,p_order_snapshot_sha256,mismatched_symbols,statement_timestamp()
    ) ON CONFLICT (event_id) DO NOTHING;
  END IF;
  UPDATE public.automation_control SET
    expected_account_projection_v2=next_risk_projection,
    expected_account_digest_v2=next_digest_value,
    control_state=CASE WHEN p_recover_halted THEN 'ARMED' ELSE control_state END,
    version=CASE WHEN p_recover_halted THEN version+1 ELSE version END,
    updated_at=statement_timestamp()
  WHERE user_id=claim_row.user_id;
  IF p_recover_halted THEN
    IF control_row.version=2147483647 THEN
      RAISE EXCEPTION 'automation control version exhausted' USING ERRCODE='40001';
    END IF;
    next_version:=control_row.version+1;
    UPDATE public.automation_runtime_schedule
    SET schedule_state='COMPLETED',control_version=next_version,updated_at=statement_timestamp()
    WHERE user_id=claim_row.user_id AND session_date=claim_row.session_date
      AND schedule_state='HALTED';
    IF NOT FOUND THEN RAISE EXCEPTION 'automation halted schedule missing' USING ERRCODE='40001'; END IF;
    INSERT INTO public.automation_runtime_schedule(
      schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
    ) VALUES (
      'auto_sched_'||substr(encode(public.digest(convert_to(
        claim_row.user_id||':'||p_next_session::text,'UTF8'),'sha256'),'hex'),1,32),
      claim_row.user_id,p_next_session,next_version,'ARMED',
      (p_next_session+time '08:55') AT TIME ZONE 'Asia/Seoul',
      statement_timestamp(),statement_timestamp()
    ) ON CONFLICT (user_id,session_date) DO UPDATE SET
      control_version=excluded.control_version,
      schedule_state=CASE WHEN automation_runtime_schedule.schedule_state IN ('HALTED','COMPLETED','DISARMED')
        THEN 'ARMED' ELSE automation_runtime_schedule.schedule_state END,
      run_at=excluded.run_at,updated_at=statement_timestamp();
  END IF;
  RETURN jsonb_build_object(
    'controlState',CASE WHEN p_recover_halted THEN 'ARMED' ELSE control_row.control_state END,
    'controlVersion',CASE WHEN p_recover_halted THEN control_row.version+1 ELSE control_row.version END,
    'quarantinedSymbols',mismatched_symbols,
    'reconciled',true,
    'expectedDigest',next_digest_value
  );
END
$p1_reconcile_automation_account_v1$;

ALTER FUNCTION public.p1_list_recoverable_automation_account_halts_v1() OWNER TO flyway;
ALTER FUNCTION public.p1_automation_account_lineage_digest_v1(jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_reconcile_automation_account_v1(text,text,integer,text,jsonb,text,text,integer,date,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_list_recoverable_automation_account_halts_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p1_automation_account_lineage_digest_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p1_reconcile_automation_account_v1(text,text,integer,text,jsonb,text,text,integer,date,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_list_recoverable_automation_account_halts_v1() TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_automation_account_lineage_digest_v1(jsonb) TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_reconcile_automation_account_v1(text,text,integer,text,jsonb,text,text,integer,date,boolean) TO decision_automation_runtime;

-- Balance observations remain the complete broker account for reconciliation and dashboards.
-- The decision risk snapshot receives only positions outside active bot ownership as exclusions.
CREATE POLICY portfolio_balance_automation_risk_reader_v231
  ON public.portfolio_balance_observations FOR SELECT TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND public.actor_rls_scope_is_open_v1()
    AND owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
  );
CREATE POLICY portfolio_position_automation_risk_reader_v231
  ON public.portfolio_position_observations FOR SELECT TO PUBLIC
  USING (
    current_user='flyway' AND session_user='decision_app'
    AND public.actor_rls_scope_is_open_v1()
    AND EXISTS (
      SELECT 1 FROM public.portfolio_balance_observations balance
      WHERE balance.observation_id=portfolio_position_observations.balance_observation_id
        AND balance.owner_user_id=pg_catalog.current_setting('app.actor_user_id',true)
    )
  );

CREATE FUNCTION public.read_automation_risk_exclusions_authorized_v231(
  p_capability text,p_actor_user_id text,p_principle_id text,p_run_id text,p_claim_hash text
)
RETURNS TABLE(symbol text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $read_automation_risk_exclusions_authorized_v231$
DECLARE account_value text;
BEGIN
  IF session_user<>'decision_app' OR p_actor_user_id IS NULL
     OR p_principle_id IS NULL OR p_run_id IS NULL OR p_claim_hash IS NULL
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_hash!~'^sha256:[0-9a-f]{64}$'
     OR NOT public.consume_current_actor_capability_v2(
       p_capability,p_actor_user_id,'READ_ACTIVE_PRINCIPLE','PRINCIPLE',p_principle_id,
       'sha256:'||encode(public.digest(p_principle_id,'sha256'),'hex')
     ) THEN
    RAISE EXCEPTION 'automation risk exclusion scope denied' USING ERRCODE='42501';
  END IF;
  SELECT control.account_id INTO account_value
  FROM public.automation_runs run
  JOIN public.automation_runtime_claim claim ON claim.run_id=run.run_id AND claim.user_id=run.user_id
  JOIN public.automation_control control ON control.user_id=run.user_id
  WHERE run.run_id=p_run_id AND run.user_id=p_actor_user_id
    AND run.principle_id=p_principle_id
    AND (
      claim.claim_state='ACTIVE'
      OR public.p1_automation_released_continuation_claim_valid_v231(
        p_run_id,p_claim_hash,p_actor_user_id,p_principle_id,statement_timestamp()
      )
    )
    AND claim.claim_token_hash=p_claim_hash;
  IF NOT FOUND OR account_value IS NULL THEN
    RAISE EXCEPTION 'automation risk exclusion claim unavailable' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT DISTINCT holding.symbol
  FROM public.portfolio_balance_observations balance
  JOIN public.portfolio_position_observations holding
    ON holding.balance_observation_id=balance.observation_id
  WHERE balance.owner_user_id=p_actor_user_id AND balance.source='KIS_MOCK'
    AND balance.context_status='ACTIVE' AND balance.completeness='COMPLETE'
    AND balance.account_scope_hash LIKE substr(account_value,6)||'%'
    AND NOT EXISTS (
      SELECT 1 FROM public.automation_positions managed
      WHERE managed.user_id=p_actor_user_id AND managed.account_id=account_value
        AND managed.symbol=holding.symbol AND managed.bot_owned AND managed.policy_id IS NOT NULL
        AND managed.status IN ('OPEN','EXIT_PENDING')
    )
  ORDER BY holding.symbol;
END
$read_automation_risk_exclusions_authorized_v231$;

CREATE FUNCTION public.p1_read_automation_asset_weight_limit_v1(p_run_id text,p_claim_token_hash text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_asset_weight_limit_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE principle_rules jsonb;
DECLARE limit_value text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id IS NULL
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$' OR p_claim_token_hash IS NULL
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation asset weight limit scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation asset weight claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  IF NOT FOUND OR control_row.principle_version_id IS NULL THEN
    RAISE EXCEPTION 'automation asset weight policy unavailable' USING ERRCODE='40001';
  END IF;
  SELECT version.rules_json INTO principle_rules FROM public.principle_versions version
  WHERE version.principle_version_id=control_row.principle_version_id
    AND version.principle_id=control_row.principle_id;
  SELECT min((rule->>'threshold')::numeric)::text INTO limit_value
  FROM jsonb_array_elements(COALESCE(principle_rules,'[]'::jsonb)) rule
  WHERE rule->>'ruleId'='max_position_per_asset' AND (rule->>'enabled')::boolean;
  RETURN limit_value;
END
$p1_read_automation_asset_weight_limit_v1$;

  -- The account-wide risk view remains authoritative for dashboards. Automation reads its
  -- separately versioned risk observations through an actor-scope checked function.
CREATE OR REPLACE VIEW public.latest_deterministic_risk_observations
WITH (security_barrier=true)
AS
SELECT DISTINCT ON (risk.owner_user_id,risk.owner_scope_hash,risk.portfolio_source)
  risk.observation_id,risk.owner_user_id,risk.owner_scope_hash,risk.portfolio_source,
  risk.daily_loss_rate,risk.max_drawdown,risk.annualized_volatility,risk.completeness,
  risk.observed_at,risk.received_at,risk.schema_version,risk.source_version,risk.source_ref,
  risk.artifact_hash
FROM public.deterministic_risk_observations risk
WHERE risk.owner_user_id=current_setting('app.actor_user_id',true)
  AND risk.source_version<>'p1-automation-risk-v1'
ORDER BY risk.owner_user_id,risk.owner_scope_hash,risk.portfolio_source,
  risk.observed_at DESC,risk.received_at DESC,risk.observation_id;

CREATE FUNCTION public.read_automation_risk_snapshot_authorized_v231(
  p_actor_user_id text,p_owner_scope_hash text,p_portfolio_source text
)
RETURNS TABLE(
  daily_loss_rate numeric,max_drawdown numeric,annualized_volatility numeric,
  completeness text,observed_at timestamptz,received_at timestamptz,
  source_version text,source_ref text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $read_automation_risk_snapshot_authorized_v231$
BEGIN
  IF session_user<>'decision_app' OR p_actor_user_id IS NULL
     OR p_actor_user_id!~'^[0-9A-Za-z._:-]{1,128}$'
     OR p_owner_scope_hash IS NULL OR p_owner_scope_hash!~'^[0-9a-f]{64}$'
     OR p_portfolio_source IS DISTINCT FROM 'KIS_MOCK'
     OR NOT public.assert_actor_rls_scope_exact_v1(
       p_actor_user_id,'READ_STORED_SOURCE','OWNER',p_actor_user_id,
       public.actor_capability_payload_hash(p_actor_user_id,NULL,NULL)
     ) THEN
    RAISE EXCEPTION 'automation risk snapshot scope denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT risk.daily_loss_rate,risk.max_drawdown,risk.annualized_volatility,
    risk.completeness,risk.observed_at,risk.received_at,risk.source_version,risk.source_ref
  FROM public.deterministic_risk_observations risk
  WHERE risk.owner_user_id=p_actor_user_id AND risk.owner_scope_hash=p_owner_scope_hash
    AND risk.portfolio_source=p_portfolio_source
    AND risk.source_version='p1-automation-risk-v1'
  ORDER BY risk.observed_at DESC,risk.received_at DESC,risk.observation_id
  LIMIT 1;
END
$read_automation_risk_snapshot_authorized_v231$;

ALTER FUNCTION public.read_automation_risk_exclusions_authorized_v231(text,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_asset_weight_limit_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_get_or_initialize_automation_risk_baseline_v231(text,text,bigint,bigint,boolean,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.read_automation_risk_snapshot_authorized_v231(text,text,text) OWNER TO flyway;
ALTER VIEW public.latest_deterministic_risk_observations OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_automation_risk_exclusions_authorized_v231(text,text,text,text,text)
  FROM PUBLIC,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_read_automation_asset_weight_limit_v1(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.p1_get_or_initialize_automation_risk_baseline_v231(text,text,bigint,bigint,boolean,timestamptz)
  FROM PUBLIC,decision_app;
REVOKE ALL ON FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_automation_released_continuation_claim_valid_v231(text,text,text,text,timestamptz)
  TO decision_automation_runtime;
REVOKE ALL ON FUNCTION public.read_automation_risk_snapshot_authorized_v231(text,text,text)
  FROM PUBLIC,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.read_automation_risk_exclusions_authorized_v231(text,text,text,text,text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_asset_weight_limit_v1(text,text)
  TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_get_or_initialize_automation_risk_baseline_v231(text,text,bigint,bigint,boolean,timestamptz)
  TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.read_automation_risk_snapshot_authorized_v231(text,text,text)
  TO decision_app;
