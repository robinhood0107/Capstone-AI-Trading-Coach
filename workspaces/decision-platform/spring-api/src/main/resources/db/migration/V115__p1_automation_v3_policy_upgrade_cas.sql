-- `expectedVersion=0` means "create the first V3 policy", even when immutable
-- V1/V2 history already exists for the owner.  Internally the append-only
-- version sequence still advances from the latest historical row.

CREATE OR REPLACE FUNCTION public.p1_put_automation_policy_v2(
  p_user_id text,p_principle_id text,p_capital_limit_krw bigint,p_stop_loss_bps integer,
  p_take_profit_bps integer,p_max_holding_sessions integer,p_atr_period integer,
  p_atr_multiplier_milli integer,p_model_sell_enabled boolean,p_expected_version integer,
  p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_put_automation_policy_v2$
DECLARE base_result record;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE projection jsonb;
DECLARE profile text;
DECLARE current_v3_version integer;
DECLARE latest_historical_version integer;
DECLARE effective_expected_version integer;
BEGIN
  IF p_max_holding_sessions NOT BETWEEN 0 AND 1260 OR p_atr_period NOT BETWEEN 5 AND 100
     OR p_atr_multiplier_milli NOT BETWEEN 1000 AND 10000
     OR p_atr_multiplier_milli%100<>0 OR p_model_sell_enabled IS NULL THEN
    RAISE EXCEPTION 'automation v3 policy input invalid' USING ERRCODE='22023';
  END IF;
  SELECT version INTO current_v3_version FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND max_holding_sessions IS NOT NULL
  ORDER BY version DESC LIMIT 1;
  SELECT version INTO latest_historical_version FROM public.automation_policy_versions
  WHERE user_id=p_user_id ORDER BY version DESC LIMIT 1;
  IF current_v3_version IS NULL AND p_expected_version<>0 THEN
    RAISE EXCEPTION 'automation v3 policy version conflict' USING ERRCODE='40001';
  END IF;
  effective_expected_version:=CASE
    WHEN current_v3_version IS NULL THEN COALESCE(latest_historical_version,0)
    ELSE p_expected_version
  END;
  SELECT * INTO base_result FROM public.p1_put_automation_policy_v1(
    p_user_id,p_principle_id,p_capital_limit_krw,p_stop_loss_bps,p_take_profit_bps,
    effective_expected_version,p_scope_hash,p_request_hash
  );
  IF base_result.replayed THEN
    result_json:=base_result.result_json;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  profile:=public.p1_automation_policy_profile_v2(
    p_stop_loss_bps,p_take_profit_bps,p_max_holding_sessions,p_atr_period,
    p_atr_multiplier_milli,p_model_sell_enabled
  );
  UPDATE public.automation_policy_versions SET
    max_holding_sessions=p_max_holding_sessions,atr_period=p_atr_period,
    atr_multiplier_milli=p_atr_multiplier_milli,model_sell_enabled=p_model_sell_enabled,
    risk_profile=profile
  WHERE policy_id=base_result.result_json::jsonb->>'policyId'
    AND version=(base_result.result_json::jsonb->>'version')::integer
  RETURNING * INTO policy_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation v3 policy write unavailable' USING ERRCODE='40001';
  END IF;
  projection:=jsonb_build_object(
    'atrMultiplierMilli',policy_row.atr_multiplier_milli,'atrPeriod',policy_row.atr_period,
    'buyCutoffTimeKst','09:40','cancelTimeKst','15:20',
    'capitalLimitKrw',policy_row.capital_limit_krw,'contractId','automation-policy.v2',
    'createdAt',policy_row.created_at,'evaluationTimeKst','09:30',
    'maxHoldingSessions',policy_row.max_holding_sessions,'maxNewOrdersPerSession',1,
    'maxOpenPositions',5,'modelSellEnabled',policy_row.model_sell_enabled,
    'policyId',policy_row.policy_id,'presetId',lower(policy_row.risk_profile),
    'stopLossBps',policy_row.stop_loss_bps,'takeProfitBps',policy_row.take_profit_bps,
    'updatedAt',policy_row.created_at,'version',policy_row.version
  );
  UPDATE public.automation_policy_idempotency SET result_json=projection
  WHERE scope_hash=p_scope_hash AND user_id=p_user_id;
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_put_automation_policy_v2$;

ALTER FUNCTION public.p1_put_automation_policy_v2(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_put_automation_policy_v2(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text
) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_put_automation_policy_v2(
  text,text,bigint,integer,integer,integer,integer,integer,boolean,integer,text,text
) TO decision_app;

