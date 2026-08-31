-- P1 1.1.0 owner policy, variable-quantity reservation, and structural account-lineage boundary.
-- KIS_MOCK v2 remains fail-closed until a COMPLETE online risk-balance projection is present.

CREATE FUNCTION public.p1_automation_policy_profile_v1(p_stop_loss_bps integer,p_take_profit_bps integer)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_policy_profile_v1$
  SELECT CASE (p_stop_loss_bps,p_take_profit_bps)
    WHEN (300,500) THEN 'CONSERVATIVE'
    WHEN (500,1000) THEN 'BALANCED'
    WHEN (800,1500) THEN 'AGGRESSIVE'
    ELSE 'CUSTOM'
  END
$p1_automation_policy_profile_v1$;

CREATE FUNCTION public.p1_automation_structural_projection_valid_v2(p_projection jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_structural_projection_valid_v2$
  SELECT jsonb_typeof(p_projection)='object'
    AND p_projection ?& ARRAY['accountId','cashKrw','positions','schemaVersion']
    AND p_projection-ARRAY['accountId','cashKrw','positions','schemaVersion']='{}'::jsonb
    AND p_projection->>'accountId'~'^acct_[0-9a-f]{32}$'
    AND p_projection->>'schemaVersion'='2'
    AND (p_projection->>'cashKrw')~'^(0|[1-9][0-9]{0,18})$'
    AND jsonb_typeof(p_projection->'positions')='array'
    AND jsonb_array_length(p_projection->'positions')<=1000
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_projection->'positions') item
      WHERE jsonb_typeof(item)<>'object'
        OR NOT item ?& ARRAY['quantity','symbol']
        OR item-ARRAY['quantity','symbol']<>'{}'::jsonb
        OR item->>'symbol'!~'^[0-9]{6}$'
        OR item->>'quantity'!~'^(0|[1-9][0-9]{0,18})$'
    )
    AND (SELECT count(*) FROM jsonb_array_elements(p_projection->'positions'))=(
      SELECT count(DISTINCT item->>'symbol') FROM jsonb_array_elements(p_projection->'positions') item
    )
    AND octet_length(p_projection::text) BETWEEN 2 AND 65536
$p1_automation_structural_projection_valid_v2$;

CREATE TABLE public.automation_policy_versions (
  policy_id text NOT NULL CHECK (policy_id~'^auto_pol_[0-9a-f]{32}$'),
  version integer NOT NULL CHECK (version>=1),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  capital_limit_krw bigint NOT NULL CHECK (
    capital_limit_krw BETWEEN 10000 AND 10000000000 AND capital_limit_krw%10000=0
  ),
  stop_loss_bps integer NOT NULL CHECK (stop_loss_bps BETWEEN 100 AND 1500),
  take_profit_bps integer NOT NULL CHECK (
    take_profit_bps BETWEEN 200 AND 3000 AND take_profit_bps>stop_loss_bps
  ),
  risk_profile text NOT NULL CHECK (risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE','CUSTOM')),
  principle_id text NOT NULL,
  principle_version_id text NOT NULL,
  principle_version integer NOT NULL CHECK (principle_version>=1),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (policy_id,version),
  UNIQUE (user_id,version),
  FOREIGN KEY (principle_version_id,principle_id,principle_version)
    REFERENCES public.principle_versions(principle_version_id,principle_id,version) ON DELETE RESTRICT,
  CHECK (risk_profile=public.p1_automation_policy_profile_v1(stop_loss_bps,take_profit_bps))
);
CREATE INDEX automation_policy_owner_current_idx
  ON public.automation_policy_versions(user_id,version DESC,policy_id);

CREATE TABLE public.automation_policy_idempotency (
  scope_hash text PRIMARY KEY CHECK (scope_hash~'^sha256:[0-9a-f]{64}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  request_hash text NOT NULL CHECK (request_hash~'^sha256:[0-9a-f]{64}$'),
  policy_id text NOT NULL,
  policy_version integer NOT NULL,
  result_json jsonb NOT NULL CHECK (
    jsonb_typeof(result_json)='object' AND octet_length(result_json::text) BETWEEN 2 AND 16384
  ),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  FOREIGN KEY (policy_id,policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT
);

-- V89/V90 FORCE RLS policies intentionally exclude a direct flyway session. PostgreSQL validates
-- newly added foreign keys by scanning the altered table, so the table owner must bypass RLS only
-- inside this atomic migration. ENABLE remains set throughout and FORCE is restored below.
ALTER TABLE public.automation_control NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_checkpoint NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations NO FORCE ROW LEVEL SECURITY;

ALTER TABLE public.automation_control
  ADD COLUMN policy_id text,
  ADD COLUMN policy_version integer,
  ADD COLUMN principle_version_id text,
  ADD COLUMN principle_version integer,
  ADD COLUMN team_b_integrity_receipt_sha256_v2 text,
  ADD COLUMN initial_account_digest_v2 text,
  ADD COLUMN expected_account_digest_v2 text,
  ADD COLUMN expected_account_projection_v2 jsonb,
  ADD CONSTRAINT automation_control_policy_v2_fkey FOREIGN KEY (policy_id,policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT,
  ADD CONSTRAINT automation_control_principle_v2_fkey
    FOREIGN KEY (principle_version_id,principle_id,principle_version)
    REFERENCES public.principle_versions(principle_version_id,principle_id,version) ON DELETE RESTRICT,
  ADD CONSTRAINT automation_control_v2_binding_check CHECK (
    (policy_id IS NULL AND policy_version IS NULL AND principle_version_id IS NULL
      AND principle_version IS NULL AND team_b_integrity_receipt_sha256_v2 IS NULL
      AND initial_account_digest_v2 IS NULL AND expected_account_digest_v2 IS NULL
      AND expected_account_projection_v2 IS NULL)
    OR
    (policy_id~'^auto_pol_[0-9a-f]{32}$' AND policy_version>=1
      AND principle_version_id~'^pvr_[A-Za-z0-9_-]{8,96}$' AND principle_version>=1
      AND team_b_integrity_receipt_sha256_v2~'^[0-9a-f]{64}$'
      AND initial_account_digest_v2~'^[0-9a-f]{64}$'
      AND expected_account_digest_v2~'^[0-9a-f]{64}$'
      AND public.p1_automation_structural_projection_valid_v2(expected_account_projection_v2))
  );

ALTER TABLE public.automation_runs
  ADD COLUMN policy_id text,
  ADD COLUMN policy_version integer,
  ADD COLUMN expected_account_digest_before text,
  DROP CONSTRAINT automation_runs_state_check,
  ADD CONSTRAINT automation_runs_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','BUY_CANDIDATE_SELECTED',
    'NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING','RISK_CHECKING','ORDER_SUBMITTING',
    'ORDER_SUBMITTED','PENDING_RECONCILIATION','CANCELLED_UNFILLED',
    'COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  )),
  ADD CONSTRAINT automation_runs_policy_v2_fkey FOREIGN KEY (policy_id,policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT,
  ADD CONSTRAINT automation_runs_expected_digest_v2_check CHECK (
    expected_account_digest_before IS NULL OR expected_account_digest_before~'^[0-9a-f]{64}$'
  );

ALTER TABLE public.automation_runtime_checkpoint
  ADD COLUMN quote_snapshot_json text,
  ADD COLUMN exit_reason text,
  DROP CONSTRAINT automation_runtime_checkpoint_state_check,
  ADD CONSTRAINT automation_runtime_checkpoint_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','BUY_CANDIDATE_SELECTED',
    'NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING','RISK_CHECKING','ORDER_SUBMITTING',
    'ORDER_SUBMITTED','PENDING_RECONCILIATION','CANCELLED_UNFILLED',
    'COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  )),
  ADD CONSTRAINT automation_checkpoint_quote_v2_check CHECK (
    quote_snapshot_json IS NULL OR (
      octet_length(quote_snapshot_json) BETWEEN 2 AND 4096
      AND jsonb_typeof(quote_snapshot_json::jsonb)='object'
    )
  ),
  ADD CONSTRAINT automation_checkpoint_exit_reason_v2_check CHECK (
    exit_reason IS NULL OR exit_reason IN ('STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT')
  );

ALTER TABLE public.automation_order_reservations
  DROP CONSTRAINT automation_order_reservations_quantity_check,
  ALTER COLUMN quantity TYPE bigint,
  ADD CONSTRAINT automation_order_reservations_quantity_check CHECK (quantity>0),
  ADD COLUMN estimated_amount_krw bigint,
  ADD COLUMN strategy_id text,
  ADD COLUMN policy_id text,
  ADD COLUMN policy_version integer,
  ADD COLUMN principle_version_id text,
  ADD COLUMN exact_intent_json text,
  ADD COLUMN quote_snapshot_json text,
  ADD COLUMN order_intent_sha256 text,
  ADD COLUMN filled_quantity bigint NOT NULL DEFAULT 0,
  ADD COLUMN leaves_quantity bigint,
  ADD COLUMN unfilled_terminated_quantity bigint NOT NULL DEFAULT 0,
  ADD COLUMN average_fill_price_krw bigint,
  ADD COLUMN reconciliation_status text NOT NULL DEFAULT 'NOT_APPLICABLE',
  ADD COLUMN exit_reason text,
  ADD CONSTRAINT automation_reservation_policy_v2_fkey FOREIGN KEY (policy_id,policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT,
  ADD CONSTRAINT automation_reservation_v2_shape_check CHECK (
    (policy_id IS NULL AND policy_version IS NULL AND principle_version_id IS NULL
      AND estimated_amount_krw IS NULL AND strategy_id IS NULL AND exact_intent_json IS NULL
      AND quote_snapshot_json IS NULL AND order_intent_sha256 IS NULL)
    OR
    (policy_id~'^auto_pol_[0-9a-f]{32}$' AND policy_version>=1
      AND principle_version_id~'^pvr_[A-Za-z0-9_-]{8,96}$'
      AND estimated_amount_krw>0 AND limit_price_krw<=9223372036854775807/quantity
      AND estimated_amount_krw=quantity*limit_price_krw
      AND strategy_id='strategy_rule_lstm_v1' AND order_intent_sha256~'^[0-9a-f]{64}$'
      AND (exact_intent_json IS NULL OR (
        octet_length(exact_intent_json) BETWEEN 2 AND 4096
        AND jsonb_typeof(exact_intent_json::jsonb)='object'))
      AND (quote_snapshot_json IS NULL OR (
        octet_length(quote_snapshot_json) BETWEEN 2 AND 4096
        AND jsonb_typeof(quote_snapshot_json::jsonb)='object')))
  ),
  ADD CONSTRAINT automation_reservation_fill_v2_check CHECK (
    filled_quantity>=0 AND unfilled_terminated_quantity>=0
      AND (leaves_quantity IS NULL OR leaves_quantity>=0)
      AND (average_fill_price_krw IS NULL OR average_fill_price_krw>0)
      AND reconciliation_status IN ('NOT_APPLICABLE','MATCHED','MISMATCH')
      AND (exit_reason IS NULL OR exit_reason IN ('STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT'))
  );
UPDATE public.automation_order_reservations SET leaves_quantity=quantity WHERE leaves_quantity IS NULL;
ALTER TABLE public.automation_order_reservations ALTER COLUMN leaves_quantity SET NOT NULL;
ALTER TABLE public.automation_order_reservations ADD CONSTRAINT automation_reservation_conservation_v2_check
  CHECK (filled_quantity+leaves_quantity+unfilled_terminated_quantity=quantity);

ALTER TABLE public.automation_positions
  DROP CONSTRAINT automation_positions_quantity_check,
  ALTER COLUMN quantity TYPE bigint,
  ADD CONSTRAINT automation_positions_quantity_check CHECK (quantity>=0),
  ADD COLUMN entry_order_id text,
  ADD COLUMN entry_ordered_quantity bigint,
  ADD COLUMN entry_filled_quantity bigint,
  ADD COLUMN entry_unfilled_quantity bigint,
  ADD COLUMN entry_average_fill_price_krw bigint,
  ADD COLUMN policy_id text,
  ADD COLUMN policy_version integer,
  ADD COLUMN stop_loss_bps integer,
  ADD COLUMN take_profit_bps integer,
  ADD COLUMN exit_filled_quantity bigint NOT NULL DEFAULT 0,
  ADD COLUMN exit_average_fill_price_krw bigint,
  ADD COLUMN exit_reason text,
  ADD CONSTRAINT automation_position_policy_v2_fkey FOREIGN KEY (policy_id,policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT,
  ADD CONSTRAINT automation_position_v2_shape_check CHECK (
    (policy_id IS NULL AND policy_version IS NULL AND entry_order_id IS NULL
      AND entry_ordered_quantity IS NULL AND entry_filled_quantity IS NULL
      AND entry_unfilled_quantity IS NULL AND entry_average_fill_price_krw IS NULL
      AND stop_loss_bps IS NULL AND take_profit_bps IS NULL)
    OR
    (policy_id~'^auto_pol_[0-9a-f]{32}$' AND policy_version>=1
      AND entry_order_id~'^ord_mock_[0-9a-f]{32}$'
      AND entry_ordered_quantity>0 AND entry_filled_quantity>0 AND entry_unfilled_quantity>=0
      AND entry_ordered_quantity=entry_filled_quantity+entry_unfilled_quantity
      AND entry_average_fill_price_krw>0
      AND stop_loss_bps BETWEEN 100 AND 1500 AND take_profit_bps BETWEEN 200 AND 3000
      AND take_profit_bps>stop_loss_bps
      AND ((status='CLOSED')=(quantity=0))
      AND exit_filled_quantity>=0 AND quantity+exit_filled_quantity=entry_filled_quantity
      AND (exit_average_fill_price_krw IS NULL OR exit_average_fill_price_krw>0)
      AND (exit_reason IS NULL OR exit_reason IN ('STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT')))
  );

ALTER TABLE public.automation_control FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_checkpoint FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_order_reservations FORCE ROW LEVEL SECURITY;

CREATE TABLE public.automation_account_lineage (
  lineage_id text PRIMARY KEY CHECK (lineage_id~'^auto_acl_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  run_id text REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  sequence integer NOT NULL CHECK (sequence>=1),
  reason text NOT NULL CHECK (reason IN ('ARM_BASELINE','BUY_FILL','SELL_FILL')),
  prior_digest text CHECK (prior_digest IS NULL OR prior_digest~'^[0-9a-f]{64}$'),
  next_digest text NOT NULL CHECK (next_digest~'^[0-9a-f]{64}$'),
  order_id text CHECK (order_id IS NULL OR order_id~'^ord_mock_[0-9a-f]{32}$'),
  filled_quantity bigint CHECK (filled_quantity IS NULL OR filled_quantity>0),
  average_fill_price_krw bigint CHECK (average_fill_price_krw IS NULL OR average_fill_price_krw>0),
  occurred_at timestamptz NOT NULL,
  UNIQUE (user_id,sequence),
  CHECK ((reason='ARM_BASELINE')=(prior_digest IS NULL AND order_id IS NULL))
);
CREATE TRIGGER automation_account_lineage_append_only
BEFORE UPDATE OR DELETE ON public.automation_account_lineage
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

ALTER TABLE public.automation_policy_versions OWNER TO flyway;
ALTER TABLE public.automation_policy_idempotency OWNER TO flyway;
ALTER TABLE public.automation_account_lineage OWNER TO flyway;
ALTER TABLE public.automation_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_policy_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_policy_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_policy_idempotency FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_account_lineage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_account_lineage FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_policy_owner_v91 ON public.automation_policy_versions TO PUBLIC
USING (
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
)
WITH CHECK (
  (current_user='flyway' AND session_user='decision_app'
    AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
);
CREATE POLICY automation_policy_idempotency_owner_v91 ON public.automation_policy_idempotency TO PUBLIC
USING (current_user='flyway' AND session_user='decision_app'
  AND user_id=pg_catalog.current_setting('app.actor_user_id',true) AND public.actor_rls_scope_is_open_v1())
WITH CHECK (current_user='flyway' AND session_user='decision_app'
  AND user_id=pg_catalog.current_setting('app.actor_user_id',true) AND public.actor_rls_scope_is_open_v1());
CREATE POLICY automation_account_lineage_owner_v91 ON public.automation_account_lineage TO PUBLIC
USING (
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
)
WITH CHECK (current_user='flyway' AND session_user='decision_automation_runtime'
  AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true));

CREATE FUNCTION public.p1_put_automation_policy_v1(
  p_user_id text,p_principle_id text,p_capital_limit_krw bigint,p_stop_loss_bps integer,
  p_take_profit_bps integer,p_expected_version integer,p_scope_hash text,p_request_hash text
)
RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_put_automation_policy_v1$
DECLARE prior public.automation_policy_idempotency%ROWTYPE;
DECLARE principle record;
DECLARE current_version integer;
DECLARE next_version integer;
DECLARE stable_policy_id text;
DECLARE projection jsonb;
BEGIN
  PERFORM public.assert_actor_rls_scope_exact_v1(
    p_user_id,'PUT_AUTOMATION_POLICY','AUTOMATION_POLICY',p_user_id,p_request_hash
  );
  IF p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_expected_version<0 OR p_capital_limit_krw NOT BETWEEN 10000 AND 10000000000
     OR p_capital_limit_krw%10000<>0 OR p_stop_loss_bps NOT BETWEEN 100 AND 1500
     OR p_take_profit_bps NOT BETWEEN 200 AND 3000 OR p_take_profit_bps<=p_stop_loss_bps THEN
    RAISE EXCEPTION 'automation policy input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-policy:'||p_user_id,91));
  PERFORM pg_advisory_xact_lock(hashtextextended(p_scope_hash,91));
  SELECT * INTO prior FROM public.automation_policy_idempotency WHERE scope_hash=p_scope_hash;
  IF FOUND THEN
    IF prior.user_id<>p_user_id OR prior.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation policy idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM public.automation_control WHERE user_id=p_user_id AND control_state<>'DISARMED') THEN
    RAISE EXCEPTION 'automation policy is immutable while armed' USING ERRCODE='40001';
  END IF;
  SELECT version INTO current_version FROM public.automation_policy_versions
  WHERE user_id=p_user_id ORDER BY version DESC LIMIT 1;
  IF current_version IS NULL THEN
    IF p_expected_version<>0 THEN RAISE EXCEPTION 'automation policy version conflict' USING ERRCODE='40001'; END IF;
    next_version:=1;
  ELSE
    IF current_version<>p_expected_version OR current_version=2147483647 THEN
      RAISE EXCEPTION 'automation policy version conflict' USING ERRCODE='40001';
    END IF;
    next_version:=current_version+1;
  END IF;
  SELECT item.principle_version_id,item.version INTO principle
  FROM public.principles owner
  JOIN public.principle_versions item
    ON item.principle_id=owner.principle_id AND item.version=owner.current_version
  WHERE owner.user_id=p_user_id AND owner.principle_id=p_principle_id AND owner.status='ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'automation principle unavailable' USING ERRCODE='P0002'; END IF;
  stable_policy_id:='auto_pol_'||substr(encode(public.digest(convert_to(p_user_id,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_policy_versions(
    policy_id,version,user_id,capital_limit_krw,stop_loss_bps,take_profit_bps,risk_profile,
    principle_id,principle_version_id,principle_version,created_at
  ) VALUES (
    stable_policy_id,next_version,p_user_id,p_capital_limit_krw,p_stop_loss_bps,p_take_profit_bps,
    public.p1_automation_policy_profile_v1(p_stop_loss_bps,p_take_profit_bps),p_principle_id,
    principle.principle_version_id,principle.version,statement_timestamp()
  );
  projection:=jsonb_build_object(
    'capitalLimitKrw',p_capital_limit_krw,'contractId','automation-policy.v1',
    'policyId',stable_policy_id,'principleId',p_principle_id,
    'principleVersion',principle.version,'principleVersionId',principle.principle_version_id,
    'riskProfile',public.p1_automation_policy_profile_v1(p_stop_loss_bps,p_take_profit_bps),
    'stopLossBps',p_stop_loss_bps,'takeProfitBps',p_take_profit_bps,
    'updatedAt',statement_timestamp(),'version',next_version
  );
  INSERT INTO public.automation_policy_idempotency(
    scope_hash,user_id,request_hash,policy_id,policy_version,result_json
  ) VALUES (p_scope_hash,p_user_id,p_request_hash,stable_policy_id,next_version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_put_automation_policy_v1$;

CREATE FUNCTION public.p1_automation_risk_balance_projection_v2(p_user_id text,p_account_id text)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_risk_balance_projection_v2$
DECLARE balance public.portfolio_balance_observations%ROWTYPE;
DECLARE projection jsonb;
BEGIN
  IF session_user NOT IN ('decision_app','decision_automation_runtime')
     OR (session_user='decision_app' AND pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id)
     OR (session_user='decision_automation_runtime'
       AND pg_catalog.current_setting('app.automation_owner_user_id',true)<>p_user_id)
     OR p_account_id!~'^acct_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'automation risk balance scope denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO balance FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=p_user_id AND item.source='KIS_MOCK' AND item.context_status='ACTIVE'
    AND item.completeness='COMPLETE' AND item.schema_version='2'
    AND item.source_version='kis-mock-online-complete-v2'
    AND item.account_scope_hash LIKE substr(p_account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  IF NOT FOUND OR balance.margin_requirement_krw<>0 THEN RETURN NULL; END IF;
  SELECT jsonb_build_object(
    'accountId',p_account_id,'cashKrw',balance.cash_krw::text,'schemaVersion',2,
    'positions',COALESCE(jsonb_agg(jsonb_build_object(
      'quantity',position.quantity::text,'symbol',position.symbol
    ) ORDER BY position.symbol) FILTER (WHERE position.symbol IS NOT NULL),'[]'::jsonb)
  ) INTO projection
  FROM public.portfolio_position_observations position
  WHERE position.balance_observation_id=balance.observation_id;
  IF NOT public.p1_automation_structural_projection_valid_v2(projection) THEN RETURN NULL; END IF;
  RETURN projection;
END
$p1_automation_risk_balance_projection_v2$;

-- decision_app은 principles와 orders에 SELECT 권한이 없다. v2 status가 두 테이블을 직접 읽지 않고
-- owner scope 안에서 boolean 사실만 받도록 SECURITY DEFINER projection을 둔다.
CREATE FUNCTION public.p1_automation_status_facts_v2(p_user_id text,p_account_id text)
RETURNS TABLE(
  principle_configured boolean,unresolved_reconciliation boolean,active_principle_id text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_status_facts_v2$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1()
     OR (p_account_id IS NOT NULL AND p_account_id!~'^acct_[0-9a-f]{32}$') THEN
    RAISE EXCEPTION 'automation status facts scope denied' USING ERRCODE='42501';
  END IF;
  SELECT principle.principle_id INTO active_principle_id FROM public.principles principle
  WHERE principle.user_id=p_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id DESC LIMIT 1;
  principle_configured:=active_principle_id IS NOT NULL;
  unresolved_reconciliation:=p_account_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=p_account_id
      AND (item.status IN (
        'SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED'
      ) OR item.reconciliation_status='MISMATCH')
  );
  RETURN NEXT;
END
$p1_automation_status_facts_v2$;

CREATE FUNCTION public.p1_arm_automation_v2(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text
)
RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
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
DECLARE target_session date;
DECLARE new_schedule_id text;
DECLARE projection jsonb;
DECLARE active_receipt text;
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
  IF COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true) THEN
    RAISE EXCEPTION 'automation kill switch active' USING ERRCODE='40001';
  END IF;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  IF NOT FOUND OR gate_row.certification_status<>'VALID' OR NOT gate_row.clean_release_binding
     OR NOT gate_row.real_team_b_pointer_active OR gate_row.team_b_integrity_receipt_sha256 IS NULL THEN
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
  SELECT session_date INTO target_session FROM public.trading_sessions
  WHERE exchange_mic='XKRX' AND is_open
    AND session_date>((statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date)
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
    legacy_digest,'VALID',false,statement_timestamp(),statement_timestamp(),p_policy_id,
    policy_row.version,policy_row.principle_version_id,policy_row.principle_version,active_receipt,
    risk_digest,risk_digest,risk_projection
  ) ON CONFLICT (user_id) DO UPDATE SET
    control_state='ARMED',version=excluded.version,brokerage_mode='KIS_MOCK',account_id=excluded.account_id,
    principle_id=excluded.principle_id,strategy_id=excluded.strategy_id,
    baseline_account_digest=excluded.baseline_account_digest,certification_status='VALID',
    kill_switch_active=false,updated_at=excluded.updated_at,policy_id=excluded.policy_id,
    policy_version=excluded.policy_version,principle_version_id=excluded.principle_version_id,
    principle_version=excluded.principle_version,
    team_b_integrity_receipt_sha256_v2=excluded.team_b_integrity_receipt_sha256_v2,
    initial_account_digest_v2=excluded.initial_account_digest_v2,
    expected_account_digest_v2=excluded.expected_account_digest_v2,
    expected_account_projection_v2=excluded.expected_account_projection_v2;
  new_schedule_id:='auto_sched_'||substr(encode(public.digest(
    convert_to(p_user_id||':'||target_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (
    new_schedule_id,p_user_id,target_session,next_version,'ARMED',
    (target_session+time '09:30') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id,session_date) DO UPDATE SET
    control_version=excluded.control_version,schedule_state='ARMED',run_at=excluded.run_at,updated_at=excluded.updated_at;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(p_user_id||':'||next_version::text||':ARM_BASELINE','UTF8'),'sha256'),'hex'),1,32),
    p_user_id,NULL,COALESCE((SELECT max(sequence)+1 FROM public.automation_account_lineage WHERE user_id=p_user_id),1),
    'ARM_BASELINE',NULL,risk_digest,NULL,NULL,NULL,statement_timestamp()
  );
  projection:=jsonb_build_object(
    'blocker',NULL,'brokerageMode','KIS_MOCK','certificationStatus','VALID',
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

CREATE FUNCTION public.p1_reserve_automation_order_v2(
  p_run_id text,p_claim_token_hash text,p_expected_version integer,p_reservation_id text,
  p_symbol text,p_side text,p_quantity bigint,p_limit_price_krw bigint,p_estimated_amount_krw bigint,
  p_order_intent_sha256 text,p_tick_identity_hash text,p_result_hash text,p_event_payload_hash text
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_reserve_automation_order_v2$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE next_version integer;
DECLARE sequence_value integer;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' OR p_reservation_id!~'^auto_res_[0-9a-f]{32}$'
     OR p_symbol!~'^[0-9]{6}$' OR p_side NOT IN ('BUY','SELL') OR p_quantity<=0
     OR p_limit_price_krw<=0 OR p_limit_price_krw>9223372036854775807/p_quantity
     OR p_estimated_amount_krw<>p_quantity*p_limit_price_krw
     OR p_order_intent_sha256!~'^[0-9a-f]{64}$' OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_result_hash!~'^sha256:[0-9a-f]{64}$' OR p_event_payload_hash!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation v2 reservation invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id FOR UPDATE;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR SHARE;
  IF checkpoint_row.state<>'ORDER_SIZING' OR checkpoint_row.checkpoint_version<>p_expected_version
     OR control_row.control_state<>'ARMED' OR control_row.policy_id IS NULL THEN
    RAISE EXCEPTION 'automation v2 reservation CAS conflict' USING ERRCODE='40001';
  END IF;
  INSERT INTO public.automation_order_reservations(
    reservation_id,run_id,user_id,session_date,symbol,side,quantity,limit_price_krw,
    logical_submit_count,order_id,provider_order_ref_hash,created_at,updated_at,
    estimated_amount_krw,strategy_id,policy_id,policy_version,principle_version_id,
    order_intent_sha256,filled_quantity,leaves_quantity,unfilled_terminated_quantity,
    average_fill_price_krw,reconciliation_status,exit_reason
  ) VALUES (
    p_reservation_id,p_run_id,claim_row.user_id,claim_row.session_date,p_symbol,p_side,p_quantity,
    p_limit_price_krw,0,NULL,NULL,statement_timestamp(),statement_timestamp(),p_estimated_amount_krw,
    control_row.strategy_id,control_row.policy_id,control_row.policy_version,
    control_row.principle_version_id,p_order_intent_sha256,0,p_quantity,0,NULL,'NOT_APPLICABLE',NULL
  );
  next_version:=p_expected_version+1;
  UPDATE public.automation_runtime_checkpoint SET state='RISK_CHECKING',selected_symbol=p_symbol,
    selected_side=p_side,checkpoint_version=next_version,updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND checkpoint_version=p_expected_version;
  UPDATE public.automation_runs SET state='RISK_CHECKING',selected_symbol=p_symbol,selected_side=p_side,
    policy_id=control_row.policy_id,policy_version=control_row.policy_version,
    expected_account_digest_before=control_row.expected_account_digest_v2,updated_at=statement_timestamp()
  WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(p_run_id||':'||sequence_value::text||':ORDER_RESERVED','UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,'ORDER_RESERVED',statement_timestamp(),p_event_payload_hash,0,0,true
  );
  INSERT INTO public.automation_processed_ticks(run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at)
  VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  RETURN next_version;
END
$p1_reserve_automation_order_v2$;

CREATE FUNCTION public.p1_bind_automation_decision_v2(
  p_run_id text,p_claim_token_hash text,p_expected_version integer,p_decision_id text,
  p_tick_identity_hash text,p_result_hash text,p_event_payload_hash text
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_bind_automation_decision_v2$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE decision_row public.decisions%ROWTYPE;
DECLARE artifact_row public.decision_artifacts%ROWTYPE;
DECLARE expected_intent jsonb;
DECLARE next_version integer;
DECLARE sequence_value integer;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_decision_id!~'^dec_[0-9a-f]{32}$'
     OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$' OR p_result_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_event_payload_hash!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation v2 decision input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id FOR UPDATE;
  SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id FOR SHARE;
  SELECT * INTO decision_row FROM public.decisions WHERE decision_id=p_decision_id AND user_id=claim_row.user_id;
  SELECT * INTO artifact_row FROM public.decision_artifacts WHERE decision_id=p_decision_id;
  IF checkpoint_row.state<>'RISK_CHECKING' OR checkpoint_row.checkpoint_version<>p_expected_version
     OR reservation_row.reservation_id IS NULL OR decision_row.decision_id IS NULL
     OR decision_row.outcome<>'ALLOW' OR NOT decision_row.can_submit_order
     OR decision_row.enforcement_action<>'NONE' OR decision_row.portfolio_source<>'KIS_MOCK'
     OR decision_row.principle_version_id<>reservation_row.principle_version_id
     OR decision_row.valid_until<=statement_timestamp() OR artifact_row.decision_id IS NULL THEN
    RAISE EXCEPTION 'automation v2 decision gate closed' USING ERRCODE='40001';
  END IF;
  -- Reuse the canonical numeric JSON authored before Risk evaluation. Rebuilding with
  -- ::text would turn numeric fields into JSON strings, and hashing jsonb::text would
  -- lose the exact canonical bytes bound by order_intent_sha256.
  expected_intent:=reservation_row.exact_intent_json::jsonb;
  IF artifact_row.snapshot_artifact_canonical_json::jsonb->'orderIntent'<>expected_intent
     OR encode(public.digest(convert_to(reservation_row.exact_intent_json,'UTF8'),'sha256'),'hex')
       <>reservation_row.order_intent_sha256 THEN
    RAISE EXCEPTION 'automation v2 exact order intent mismatch' USING ERRCODE='40001';
  END IF;
  next_version:=p_expected_version+1;
  UPDATE public.automation_runtime_checkpoint SET state='ORDER_SUBMITTING',decision_id=p_decision_id,
    checkpoint_version=next_version,updated_at=statement_timestamp() WHERE run_id=p_run_id;
  UPDATE public.automation_runs SET state='ORDER_SUBMITTING',updated_at=statement_timestamp() WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(p_run_id||':'||sequence_value::text||':RISK_RESULT_RECORDED','UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,'RISK_RESULT_RECORDED',statement_timestamp(),p_event_payload_hash,0,0,true
  );
  INSERT INTO public.automation_processed_ticks(run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at)
  VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  RETURN next_version;
END
$p1_bind_automation_decision_v2$;

CREATE FUNCTION public.p1_automation_transition_valid_v2(p_current text,p_next text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_transition_valid_v2$
  SELECT (p_current=p_next AND p_current IN ('ORDER_SUBMITTING','PENDING_RECONCILIATION'))
    OR (p_current,p_next) IN (
      ('SCHEDULED','PRECHECK'),('SCHEDULED','SKIPPED_NO_ACTION'),('SCHEDULED','SKIPPED_DATA_UNAVAILABLE'),
      ('SCHEDULED','SKIPPED_LATE_START'),('SCHEDULED','HALTED'),
      ('PRECHECK','RECONCILING_PREVIOUS'),('PRECHECK','EXIT_SELECTED'),
      ('PRECHECK','BUY_CANDIDATE_SELECTED'),('PRECHECK','SKIPPED_NO_ACTION'),
      ('PRECHECK','SKIPPED_DATA_UNAVAILABLE'),('PRECHECK','HALTED'),
      ('RECONCILING_PREVIOUS','PENDING_RECONCILIATION'),('RECONCILING_PREVIOUS','EXIT_SELECTED'),
      ('RECONCILING_PREVIOUS','BUY_CANDIDATE_SELECTED'),('RECONCILING_PREVIOUS','SKIPPED_NO_ACTION'),
      ('EXIT_SELECTED','ORDER_SIZING'),('BUY_CANDIDATE_SELECTED','NEWS_CHECKING'),
      ('NEWS_CHECKING','NEWS_VETOED'),('NEWS_CHECKING','ORDER_SIZING'),
      ('ORDER_SIZING','RISK_CHECKING'),('ORDER_SIZING','SKIPPED_NO_ACTION'),
      ('ORDER_SIZING','SKIPPED_DATA_UNAVAILABLE'),('ORDER_SIZING','SKIPPED_LATE_START'),
      ('ORDER_SIZING','HALTED'),('RISK_CHECKING','ORDER_SUBMITTING'),
      ('RISK_CHECKING','SKIPPED_NO_ACTION'),('RISK_CHECKING','HALTED'),
      ('ORDER_SUBMITTING','ORDER_SUBMITTED'),('ORDER_SUBMITTING','PENDING_RECONCILIATION'),
      ('ORDER_SUBMITTING','SKIPPED_NO_ACTION'),('ORDER_SUBMITTING','SKIPPED_DATA_UNAVAILABLE'),
      ('ORDER_SUBMITTING','SKIPPED_LATE_START'),('ORDER_SUBMITTING','HALTED'),
      ('ORDER_SUBMITTED','PENDING_RECONCILIATION'),
      ('ORDER_SUBMITTED','COMPLETED'),('ORDER_SUBMITTED','CANCELLED_UNFILLED'),
      ('ORDER_SUBMITTED','HALTED'),('PENDING_RECONCILIATION','COMPLETED'),
      ('PENDING_RECONCILIATION','CANCELLED_UNFILLED'),
      ('PENDING_RECONCILIATION','HALTED')
    )
$p1_automation_transition_valid_v2$;

CREATE FUNCTION public.p1_read_automation_runtime_state_v2(p_run_id text,p_claim_token_hash text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_runtime_state_v2$
DECLARE base jsonb;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE risk_projection jsonb;
DECLARE risk_digest text;
DECLARE positions_json jsonb;
DECLARE reservation_json jsonb;
BEGIN
  base:=public.p1_read_automation_runtime_state_v1(p_run_id,p_claim_token_hash)::jsonb;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id;
  IF control_row.policy_id IS NULL THEN
    RAISE EXCEPTION 'automation v2 policy unavailable' USING ERRCODE='40001';
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id;
  risk_projection:=public.p1_automation_risk_balance_projection_v2(claim_row.user_id,control_row.account_id);
  IF risk_projection IS NOT NULL THEN
    risk_digest:=encode(public.digest(convert_to(risk_projection::text,'UTF8'),'sha256'),'hex');
  END IF;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'accountId',position.account_id,'closedAt',position.closed_at,'createdAt',position.created_at,
    'entryAverageFillPriceKrw',position.entry_average_fill_price_krw,
    'entryNotionalKrw',position.entry_filled_quantity*position.entry_average_fill_price_krw,
    'entrySession',position.entry_session,'exitReason',position.exit_reason,
    'expirySession',position.expiry_session,'policyId',position.policy_id,
    'policyVersion',position.policy_version,'positionId',position.position_id,
    'quantity',position.quantity,'status',position.status,'stopLossBps',position.stop_loss_bps,
    'symbol',position.symbol,'takeProfitBps',position.take_profit_bps
  ) ORDER BY position.entry_session,position.symbol),'[]'::jsonb) INTO positions_json
  FROM public.automation_positions position
  WHERE position.user_id=claim_row.user_id AND position.policy_id IS NOT NULL;
  IF reservation_row.reservation_id IS NOT NULL THEN
    reservation_json:=jsonb_build_object(
      'exactIntent',reservation_row.exact_intent_json::jsonb,
      'limitPriceKrw',reservation_row.limit_price_krw,
      'logicalSubmitCount',reservation_row.logical_submit_count,
      'orderId',reservation_row.order_id,'providerOrderRefHash',reservation_row.provider_order_ref_hash,
      'quantity',reservation_row.quantity,'reservationId',reservation_row.reservation_id,
      'side',reservation_row.side,'symbol',reservation_row.symbol
    );
  END IF;
  RETURN (base||jsonb_build_object(
    'accountComplete',risk_projection IS NOT NULL,
    'accountDigestMatches',risk_digest IS NOT NULL AND risk_digest=control_row.expected_account_digest_v2,
    'averageFillPriceKrw',reservation_row.average_fill_price_krw,
    'exitReason',COALESCE(reservation_row.exit_reason,checkpoint_row.exit_reason),
    'expectedAccountDigest',control_row.expected_account_digest_v2,
    'expectedAccountProjection',control_row.expected_account_projection_v2,
    'filledQuantity',COALESCE(reservation_row.filled_quantity,0),
    'leavesQuantity',COALESCE(reservation_row.leaves_quantity,0),
    'openPositionMarketValueKrw',0,'pendingBuyNotionalKrw',CASE
      WHEN reservation_row.side='BUY' AND reservation_row.reconciliation_status='NOT_APPLICABLE'
        THEN COALESCE(reservation_row.estimated_amount_krw,0) ELSE 0 END,
    'policy',jsonb_build_object(
      'capitalLimitKrw',policy_row.capital_limit_krw,'maxOpenPositions',5,
      'policyId',policy_row.policy_id,'preset',policy_row.risk_profile,
      'stopLossBps',policy_row.stop_loss_bps,'takeProfitBps',policy_row.take_profit_bps,
      'version',policy_row.version
    ),
    'positions',positions_json,'principleAssetRemainingKrw',9223372036854775807,
    'principleMaxSingleOrderKrw',9223372036854775807,
    'providerExecRefHash',reservation_row.provider_order_ref_hash,
    'quoteSnapshot',CASE WHEN checkpoint_row.quote_snapshot_json IS NULL THEN NULL
      ELSE checkpoint_row.quote_snapshot_json::jsonb END,
    'reservation',reservation_json,
    'unfilledTerminatedQuantity',COALESCE(reservation_row.unfilled_terminated_quantity,0)
  ))::text;
END
$p1_read_automation_runtime_state_v2$;

CREATE FUNCTION public.p1_advance_automation_checkpoint_v2(
  p_run_id text,p_claim_token_hash text,p_tick_identity_hash text,p_expected_version integer,
  p_next_state text,p_selected_symbol text,p_selected_side text,p_decision_id text,
  p_vertex_call_count integer,p_provider_call_count integer,p_logical_submit_count integer,
  p_reservation_id text,p_quantity bigint,p_limit_price_krw bigint,p_exact_intent_json text,
  p_exact_intent_sha256 text,p_quote_snapshot_json text,p_policy_id text,p_policy_version integer,
  p_position_expiry_session date,p_filled_quantity bigint,p_leaves_quantity bigint,
  p_unfilled_terminated_quantity bigint,p_average_fill_price_krw bigint,p_exit_reason text,
  p_expected_account_digest text,p_order_id text,p_provider_order_ref_hash text,p_result_hash text,
  p_event_type text,p_event_payload_hash text
)
RETURNS TABLE(checkpoint_version integer,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_checkpoint_v2$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE checkpoint_row public.automation_runtime_checkpoint%ROWTYPE;
DECLARE prior_tick public.automation_processed_ticks%ROWTYPE;
DECLARE reservation_row public.automation_order_reservations%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE decision_row public.decisions%ROWTYPE;
DECLARE artifact_row public.decision_artifacts%ROWTYPE;
DECLARE position_row public.automation_positions%ROWTYPE;
DECLARE next_version integer;
DECLARE sequence_value integer;
DECLARE event_seed text;
DECLARE terminal boolean;
DECLARE estimated_amount bigint;
DECLARE remaining_quantity bigint;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_tick_identity_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_result_hash!~'^sha256:[0-9a-f]{64}$' OR p_event_payload_hash!~'^[0-9a-f]{64}$'
     OR p_expected_version<1 OR p_vertex_call_count NOT BETWEEN 0 AND 1
     OR p_provider_call_count NOT BETWEEN 0 AND 16 OR p_logical_submit_count NOT BETWEEN 0 AND 1
     OR p_filled_quantity<0 OR p_leaves_quantity<0 OR p_unfilled_terminated_quantity<0
     OR (p_average_fill_price_krw IS NULL)<>(p_filled_quantity=0)
     OR (p_exit_reason IS NOT NULL AND p_exit_reason NOT IN (
       'STOP_LOSS','MAX_HOLDING_SESSIONS','MODEL_SELL','TAKE_PROFIT'
     ))
     OR p_event_type NOT IN ('CONTROL_CHANGED','RUN_TRANSITIONED','BASELINE_CAPTURED','ACCOUNT_RECONCILED',
       'EXIT_SELECTED','BUY_SELECTED','NEWS_RESULT_RECORDED','RISK_RESULT_RECORDED','ORDER_RESERVED',
       'ORDER_OUTCOME_RECORDED','CANCEL_RECORDED','DRIFT_DETECTED','RUN_HALTED') THEN
    RAISE EXCEPTION 'automation v2 checkpoint input invalid' USING ERRCODE='22023';
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
  SELECT * INTO checkpoint_row FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id FOR UPDATE;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=claim_row.user_id FOR UPDATE;
  IF checkpoint_row.run_id IS NULL OR checkpoint_row.checkpoint_version<>p_expected_version
     OR NOT public.p1_automation_transition_valid_v2(checkpoint_row.state,p_next_state)
     OR p_vertex_call_count<checkpoint_row.vertex_call_count
     OR p_provider_call_count<checkpoint_row.provider_call_count
     OR p_logical_submit_count<checkpoint_row.logical_submit_count
     OR control_row.policy_id IS NULL OR control_row.policy_id IS DISTINCT FROM p_policy_id
     OR control_row.policy_version IS DISTINCT FROM p_policy_version
     OR control_row.expected_account_digest_v2 IS DISTINCT FROM p_expected_account_digest THEN
    RAISE EXCEPTION 'automation v2 checkpoint CAS conflict' USING ERRCODE='40001';
  END IF;
  IF control_row.control_state='HALTED' OR (
    control_row.control_state='DISARMED' AND checkpoint_row.state NOT IN (
      'RECONCILING_PREVIOUS','ORDER_SUBMITTED','PENDING_RECONCILIATION'
    )
  ) THEN RAISE EXCEPTION 'automation control disallows new work' USING ERRCODE='40001'; END IF;
  IF p_quote_snapshot_json IS NOT NULL THEN
    IF octet_length(p_quote_snapshot_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_quote_snapshot_json::jsonb)<>'object'
       OR p_quote_snapshot_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol THEN
      RAISE EXCEPTION 'automation quote snapshot invalid' USING ERRCODE='22023';
    END IF;
    IF checkpoint_row.quote_snapshot_json IS NOT NULL
       AND checkpoint_row.quote_snapshot_json::jsonb<>p_quote_snapshot_json::jsonb THEN
      RAISE EXCEPTION 'automation quote snapshot drift' USING ERRCODE='40001';
    END IF;
  END IF;
  IF p_reservation_id IS NOT NULL THEN
    IF p_reservation_id!~'^auto_res_[0-9a-f]{32}$' OR p_quantity<=0 OR p_limit_price_krw<=0
       OR p_limit_price_krw>9223372036854775807/p_quantity
       OR p_exact_intent_json IS NULL OR octet_length(p_exact_intent_json) NOT BETWEEN 2 AND 4096
       OR jsonb_typeof(p_exact_intent_json::jsonb)<>'object'
       OR (SELECT count(*) FROM jsonb_object_keys(p_exact_intent_json::jsonb))<>8
       OR NOT p_exact_intent_json::jsonb ?& ARRAY[
         'estimatedAmount','estimatedPrice','orderType','quantity','side','strategyId','symbol','timeframe'
       ]
       OR p_exact_intent_json::jsonb->>'symbol' IS DISTINCT FROM p_selected_symbol
       OR p_exact_intent_json::jsonb->>'side' IS DISTINCT FROM p_selected_side
       OR p_exact_intent_json::jsonb->>'orderType'<>'LIMIT'
       OR (p_exact_intent_json::jsonb->>'quantity')::bigint<>p_quantity
       OR (p_exact_intent_json::jsonb->>'estimatedPrice')::bigint<>p_limit_price_krw
       OR p_exact_intent_json::jsonb->>'strategyId'<>control_row.strategy_id
       OR p_exact_intent_json::jsonb->>'timeframe'<>'1d'
       OR p_exact_intent_sha256!~'^[0-9a-f]{64}$'
       OR encode(public.digest(convert_to(p_exact_intent_json,'UTF8'),'sha256'),'hex')<>p_exact_intent_sha256
       OR p_quote_snapshot_json IS NULL THEN
      RAISE EXCEPTION 'automation exact order intent invalid' USING ERRCODE='22023';
    END IF;
    estimated_amount:=(p_exact_intent_json::jsonb->>'estimatedAmount')::bigint;
    IF estimated_amount<>p_quantity*p_limit_price_krw
       OR p_filled_quantity+p_leaves_quantity+p_unfilled_terminated_quantity<>p_quantity THEN
      RAISE EXCEPTION 'automation order quantity conservation failed' USING ERRCODE='40001';
    END IF;
    SELECT * INTO reservation_row FROM public.automation_order_reservations WHERE run_id=p_run_id FOR UPDATE;
    IF FOUND THEN
      IF reservation_row.reservation_id<>p_reservation_id OR reservation_row.symbol<>p_selected_symbol
         OR reservation_row.side<>p_selected_side OR reservation_row.quantity<>p_quantity
         OR reservation_row.limit_price_krw<>p_limit_price_krw
         OR reservation_row.estimated_amount_krw<>estimated_amount
         OR reservation_row.order_intent_sha256<>p_exact_intent_sha256
         OR reservation_row.exact_intent_json::jsonb<>p_exact_intent_json::jsonb
         OR p_filled_quantity<reservation_row.filled_quantity
         OR p_unfilled_terminated_quantity<reservation_row.unfilled_terminated_quantity THEN
        RAISE EXCEPTION 'automation reservation drift' USING ERRCODE='40001';
      END IF;
      UPDATE public.automation_order_reservations SET
        logical_submit_count=p_logical_submit_count,
        order_id=COALESCE(automation_order_reservations.order_id,p_order_id),
        provider_order_ref_hash=COALESCE(automation_order_reservations.provider_order_ref_hash,p_provider_order_ref_hash),
        filled_quantity=p_filled_quantity,leaves_quantity=p_leaves_quantity,
        unfilled_terminated_quantity=p_unfilled_terminated_quantity,
        average_fill_price_krw=p_average_fill_price_krw,
        reconciliation_status=CASE
          WHEN p_next_state IN ('COMPLETED','CANCELLED_UNFILLED') THEN 'MATCHED'
          ELSE automation_order_reservations.reconciliation_status END,
        exit_reason=COALESCE(automation_order_reservations.exit_reason,p_exit_reason),
        updated_at=statement_timestamp()
      WHERE run_id=p_run_id;
    ELSE
      IF checkpoint_row.state<>'ORDER_SIZING' OR p_next_state<>'RISK_CHECKING' THEN
        RAISE EXCEPTION 'automation reservation must precede risk' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_order_reservations(
        reservation_id,run_id,user_id,session_date,symbol,side,quantity,limit_price_krw,
        logical_submit_count,order_id,provider_order_ref_hash,created_at,updated_at,
        estimated_amount_krw,strategy_id,policy_id,policy_version,principle_version_id,
        exact_intent_json,quote_snapshot_json,order_intent_sha256,filled_quantity,leaves_quantity,
        unfilled_terminated_quantity,average_fill_price_krw,reconciliation_status,exit_reason
      ) VALUES (
        p_reservation_id,p_run_id,claim_row.user_id,claim_row.session_date,p_selected_symbol,
        p_selected_side,p_quantity,p_limit_price_krw,p_logical_submit_count,p_order_id,
        p_provider_order_ref_hash,statement_timestamp(),statement_timestamp(),estimated_amount,
        control_row.strategy_id,control_row.policy_id,control_row.policy_version,
        control_row.principle_version_id,p_exact_intent_json,p_quote_snapshot_json,
        p_exact_intent_sha256,p_filled_quantity,p_leaves_quantity,p_unfilled_terminated_quantity,
        p_average_fill_price_krw,'NOT_APPLICABLE',p_exit_reason
      );
    END IF;
    IF p_decision_id IS NOT NULL THEN
      SELECT * INTO decision_row FROM public.decisions
      WHERE decision_id=p_decision_id AND user_id=claim_row.user_id;
      SELECT * INTO artifact_row FROM public.decision_artifacts WHERE decision_id=p_decision_id;
      IF decision_row.decision_id IS NULL OR decision_row.outcome<>'ALLOW'
         OR NOT decision_row.can_submit_order OR decision_row.enforcement_action<>'NONE'
         OR decision_row.portfolio_source<>'KIS_MOCK'
         OR decision_row.principle_version_id<>control_row.principle_version_id
         OR decision_row.valid_until<=statement_timestamp() OR artifact_row.decision_id IS NULL
         OR EXISTS (
           SELECT 1 FROM jsonb_object_keys(p_exact_intent_json::jsonb) key
           WHERE artifact_row.snapshot_artifact_canonical_json::jsonb->'orderIntent'->>key
             IS DISTINCT FROM p_exact_intent_json::jsonb->>key
         ) THEN
        RAISE EXCEPTION 'automation exact Decision intent mismatch' USING ERRCODE='40001';
      END IF;
    END IF;
  ELSIF p_quantity IS NOT NULL OR p_limit_price_krw IS NOT NULL OR p_exact_intent_json IS NOT NULL
     OR p_exact_intent_sha256 IS NOT NULL OR p_order_id IS NOT NULL OR p_provider_order_ref_hash IS NOT NULL
     OR p_filled_quantity<>0 OR p_leaves_quantity<>0 OR p_unfilled_terminated_quantity<>0
     OR p_average_fill_price_krw IS NOT NULL THEN
    RAISE EXCEPTION 'automation order state lacks reservation' USING ERRCODE='22023';
  END IF;
  IF p_next_state='EXIT_SELECTED' AND p_selected_side='SELL' THEN
    UPDATE public.automation_positions SET status='EXIT_PENDING',exit_reason=p_exit_reason
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='OPEN';
    IF NOT FOUND THEN RAISE EXCEPTION 'automation SELL lot unavailable' USING ERRCODE='40001'; END IF;
  END IF;
  IF p_next_state='COMPLETED' AND p_reservation_id IS NOT NULL AND p_filled_quantity>0 THEN
    SELECT * INTO policy_row FROM public.automation_policy_versions
    WHERE policy_id=control_row.policy_id AND version=control_row.policy_version;
    IF p_selected_side='BUY' THEN
      IF p_position_expiry_session IS NULL OR p_position_expiry_session<=claim_row.session_date
         OR p_order_id IS NULL THEN
        RAISE EXCEPTION 'automation completed BUY evidence invalid' USING ERRCODE='40001';
      END IF;
      INSERT INTO public.automation_positions(
        position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
        bot_owned,short_allowed,created_at,closed_at,entry_order_id,entry_ordered_quantity,
        entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,
        policy_id,policy_version,stop_loss_bps,take_profit_bps,exit_filled_quantity,
        exit_average_fill_price_krw,exit_reason
      ) VALUES (
        'auto_pos_'||substr(encode(public.digest(convert_to(
          p_run_id||':'||p_selected_symbol||':'||claim_row.session_date::text,'UTF8'),'sha256'),'hex'),1,32),
        claim_row.user_id,control_row.account_id,p_selected_symbol,p_filled_quantity,
        claim_row.session_date,p_position_expiry_session,'OPEN',true,false,statement_timestamp(),NULL,
        p_order_id,p_quantity,p_filled_quantity,p_unfilled_terminated_quantity,p_average_fill_price_krw,
        control_row.policy_id,control_row.policy_version,policy_row.stop_loss_bps,
        policy_row.take_profit_bps,0,NULL,NULL
      );
    ELSE
      SELECT * INTO position_row FROM public.automation_positions
      WHERE user_id=claim_row.user_id AND account_id=control_row.account_id
        AND symbol=p_selected_symbol AND status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
      IF NOT FOUND OR position_row.quantity<p_filled_quantity THEN
        RAISE EXCEPTION 'automation completed SELL lot unavailable' USING ERRCODE='40001';
      END IF;
      remaining_quantity:=position_row.quantity-p_filled_quantity;
      UPDATE public.automation_positions SET
        quantity=remaining_quantity,exit_filled_quantity=exit_filled_quantity+p_filled_quantity,
        exit_average_fill_price_krw=p_average_fill_price_krw,exit_reason=p_exit_reason,
        status=CASE WHEN remaining_quantity=0 THEN 'CLOSED' ELSE 'OPEN' END,
        closed_at=CASE WHEN remaining_quantity=0 THEN statement_timestamp() ELSE NULL END
      WHERE position_id=position_row.position_id;
    END IF;
  ELSIF p_selected_side='SELL' AND p_next_state IN (
    'NEWS_VETOED','CANCELLED_UNFILLED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ) THEN
    UPDATE public.automation_positions SET status='OPEN'
    WHERE user_id=claim_row.user_id AND symbol=p_selected_symbol AND status='EXIT_PENDING';
  END IF;
  next_version:=checkpoint_row.checkpoint_version+1;
  UPDATE public.automation_runtime_checkpoint SET
    checkpoint_version=next_version,state=p_next_state,selected_symbol=p_selected_symbol,
    selected_side=p_selected_side,decision_id=COALESCE(automation_runtime_checkpoint.decision_id,p_decision_id),
    vertex_call_count=p_vertex_call_count,provider_call_count=p_provider_call_count,
    logical_submit_count=p_logical_submit_count,
    quote_snapshot_json=COALESCE(automation_runtime_checkpoint.quote_snapshot_json,p_quote_snapshot_json),
    exit_reason=COALESCE(automation_runtime_checkpoint.exit_reason,p_exit_reason),updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND checkpoint_version=p_expected_version;
  UPDATE public.automation_runs SET
    state=p_next_state,selected_symbol=p_selected_symbol,selected_side=p_selected_side,
    physical_submit_count=p_logical_submit_count,vertex_call_count=p_vertex_call_count,
    provider_calls=p_provider_call_count,policy_id=control_row.policy_id,
    policy_version=control_row.policy_version,
    expected_account_digest_before=COALESCE(expected_account_digest_before,control_row.expected_account_digest_v2),
    updated_at=statement_timestamp() WHERE run_id=p_run_id;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value FROM public.automation_events WHERE run_id=p_run_id;
  event_seed:=p_run_id||':'||sequence_value::text||':'||p_event_type||':'||p_event_payload_hash;
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,provider_calls,order_submits,sanitized
  ) VALUES (
    'auto_evt_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    p_run_id,claim_row.user_id,sequence_value,p_event_type,statement_timestamp(),p_event_payload_hash,
    p_provider_call_count,p_logical_submit_count,true
  );
  INSERT INTO public.automation_processed_ticks(
    run_id,tick_identity_hash,result_hash,checkpoint_version,processed_at
  ) VALUES (p_run_id,p_tick_identity_hash,p_result_hash,next_version,statement_timestamp());
  terminal:=p_next_state IN ('NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED',
    'SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED');
  IF p_next_state='HALTED' AND control_row.control_state<>'HALTED' THEN
    UPDATE public.automation_control SET control_state='HALTED',version=version+1,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id;
  END IF;
  IF terminal THEN
    UPDATE public.automation_runtime_claim SET claim_state='RELEASED',released_at=statement_timestamp()
    WHERE run_id=p_run_id;
    UPDATE public.automation_runtime_schedule SET
      schedule_state=CASE WHEN p_next_state='HALTED' THEN 'HALTED' ELSE 'COMPLETED' END,
      updated_at=statement_timestamp() WHERE user_id=claim_row.user_id AND session_date=claim_row.session_date;
  END IF;
  checkpoint_version:=next_version;replayed:=false;RETURN NEXT;
END
$p1_advance_automation_checkpoint_v2$;

ALTER FUNCTION public.p1_automation_policy_profile_v1(integer,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_structural_projection_valid_v2(jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_put_automation_policy_v1(text,text,bigint,integer,integer,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_risk_balance_projection_v2(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_status_facts_v2(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_arm_automation_v2(text,text,text,integer,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_reserve_automation_order_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_bind_automation_decision_v2(text,text,integer,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_automation_transition_valid_v2(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_runtime_state_v2(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_advance_automation_checkpoint_v2(
  text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,
  text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text
) OWNER TO flyway;

REVOKE ALL ON TABLE public.automation_policy_versions,public.automation_policy_idempotency,
  public.automation_account_lineage FROM PUBLIC,decision_worker,decision_replay,decision_replay_authorizer,
  decision_automation_runtime;
GRANT SELECT ON TABLE public.automation_policy_versions,public.automation_account_lineage TO decision_app;
-- automation-run.v2는 orderQuantity/filledQuantity/leavesQuantity를 필수로 공개하므로 owner가 자기
-- reservation을 읽어야 한다. 쓰기는 계속 runtime 전용이고 RLS가 owner row로만 한정한다.
CREATE POLICY automation_order_reservations_owner_read_v91
ON public.automation_order_reservations FOR SELECT TO PUBLIC
USING (
  session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
  AND public.actor_rls_scope_is_open_v1()
);
GRANT SELECT ON TABLE public.automation_order_reservations TO decision_app;
REVOKE ALL ON FUNCTION public.p1_automation_policy_profile_v1(integer,integer),
  public.p1_automation_status_facts_v2(text,text),
  public.p1_automation_structural_projection_valid_v2(jsonb),
  public.p1_put_automation_policy_v1(text,text,bigint,integer,integer,integer,text,text),
  public.p1_automation_risk_balance_projection_v2(text,text),
  public.p1_arm_automation_v2(text,text,text,integer,integer,text,text),
  public.p1_reserve_automation_order_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text,text,text,text),
  public.p1_bind_automation_decision_v2(text,text,integer,text,text,text,text),
  public.p1_automation_transition_valid_v2(text,text),
  public.p1_read_automation_runtime_state_v2(text,text),
  public.p1_advance_automation_checkpoint_v2(
    text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,
    text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text
  )
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION
  public.p1_put_automation_policy_v1(text,text,bigint,integer,integer,integer,text,text),
  public.p1_arm_automation_v2(text,text,text,integer,integer,text,text),
  public.p1_automation_risk_balance_projection_v2(text,text),
  public.p1_automation_status_facts_v2(text,text)
  TO decision_app;
-- automation_control_v2_binding_check가 이 검증 함수를 호출하므로, SECURITY INVOKER인 V89
-- arm/disarm이 decision_app으로 automation_control을 쓸 때 EXECUTE 권한이 반드시 필요하다.
GRANT EXECUTE ON FUNCTION public.p1_automation_structural_projection_valid_v2(jsonb) TO decision_app;
GRANT EXECUTE ON FUNCTION
  public.p1_automation_risk_balance_projection_v2(text,text),
  public.p1_reserve_automation_order_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text,text,text,text),
  public.p1_bind_automation_decision_v2(text,text,integer,text,text,text,text),
  public.p1_read_automation_runtime_state_v2(text,text),
  public.p1_advance_automation_checkpoint_v2(
    text,text,text,integer,text,text,text,text,integer,integer,integer,text,bigint,bigint,text,text,
    text,text,integer,date,bigint,bigint,bigint,bigint,text,text,text,text,text,text,text
  )
  TO decision_automation_runtime;
REVOKE CREATE ON SCHEMA public FROM decision_automation_runtime;
