-- V91은 사이저 입력 세 개를 상수로 두었다. 보유 평가액은 0, 원칙 자산 한도와 1회 최대주문
-- 한도는 MAX_BIGINT였다. 그래서 _variable_buy_quantity의 min() 다섯 항 중 셋이 아무것도
-- 제한하지 못했고, 사용자가 정한 원칙을 넘는 수량이 만들어졌다. RiskEngine이 뒤에서 BLOCK하니
-- 나쁜 주문이 나가지는 않았지만, 세션당 한 번뿐인 주문 기회가 그대로 버려졌다.
--
-- 실제 값을 넣어 원칙이 주문 크기 단계에서부터 구속하게 한다. 함께 instrumentCatalogSymbols를
-- 노출해, 보유 종목 분류가 전부 확인될 때만 host가 balance를 risk-complete로 선언할 수 있게 한다.

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v2(p_run_id text,p_claim_token_hash text)
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
DECLARE principle_rules jsonb;
DECLARE balance_row public.portfolio_balance_observations%ROWTYPE;
DECLARE open_position_value bigint;
DECLARE asset_weight_limit numeric;
DECLARE asset_remaining bigint;
DECLARE max_single_order bigint;
DECLARE catalog_symbols jsonb;
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
  -- V91까지 사이저 입력 세 개가 상수였다. 보유평가액 0과 원칙 한도 MAX_BIGINT 두 개 때문에
  -- _variable_buy_quantity의 min() 다섯 항 중 셋이 무력했고, 사용자가 정한 1회 최대주문 한도를
  -- 넘는 수량이 만들어져 RiskEngine이 BLOCK하면 그 세션의 단 한 번뿐인 주문이 낭비됐다.
  SELECT * INTO balance_row FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=claim_row.user_id AND item.source='KIS_MOCK'
    AND item.context_status='ACTIVE' AND item.completeness='COMPLETE'
    AND item.account_scope_hash LIKE substr(control_row.account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  SELECT COALESCE(sum(position.market_value_krw),0) INTO open_position_value
  FROM public.portfolio_position_observations position
  WHERE position.balance_observation_id=balance_row.observation_id;

  SELECT version.rules_json INTO principle_rules FROM public.principle_versions version
  WHERE version.principle_version_id=control_row.principle_version_id;

  -- 규칙이 없거나 꺼져 있으면 제한이 없다는 뜻이므로 MAX_BIGINT를 유지한다.
  SELECT COALESCE(min((rule->>'threshold')::bigint),9223372036854775807)
  INTO max_single_order
  FROM jsonb_array_elements(COALESCE(principle_rules,'[]'::jsonb)) rule
  WHERE rule->>'ruleId'='max_single_order_amount' AND (rule->>'enabled')::boolean;

  SELECT min((rule->>'threshold')::numeric) INTO asset_weight_limit
  FROM jsonb_array_elements(COALESCE(principle_rules,'[]'::jsonb)) rule
  WHERE rule->>'ruleId'='max_position_per_asset' AND (rule->>'enabled')::boolean;

  IF asset_weight_limit IS NULL OR balance_row.portfolio_equity_krw IS NULL THEN
    asset_remaining:=9223372036854775807;
  ELSE
    -- 선택 종목이 이미 차지한 평가액을 뺀 나머지가 이번 주문에 허용된 한도다.
    asset_remaining:=GREATEST(0,
      floor(asset_weight_limit*balance_row.portfolio_equity_krw)::bigint
      - COALESCE((
        SELECT position.market_value_krw FROM public.portfolio_position_observations position
        WHERE position.balance_observation_id=balance_row.observation_id
          AND position.symbol=checkpoint_row.selected_symbol
      ),0));
  END IF;

  -- 분류가 확인된 종목만 risk-complete 판정에 쓸 수 있다. host가 보유 종목 전부를 덮는지
  -- 확인하도록 카탈로그가 아는 종목 집합을 그대로 넘긴다.
  SELECT COALESCE(jsonb_agg(catalog.symbol ORDER BY catalog.symbol),'[]'::jsonb)
  INTO catalog_symbols
  FROM public.latest_instrument_catalog_observations catalog
  WHERE catalog.completeness='COMPLETE';

  RETURN (base||jsonb_build_object(
    'accountComplete',risk_projection IS NOT NULL,
    'accountDigestMatches',risk_digest IS NOT NULL AND risk_digest=control_row.expected_account_digest_v2,
    'averageFillPriceKrw',reservation_row.average_fill_price_krw,
    'exitReason',COALESCE(reservation_row.exit_reason,checkpoint_row.exit_reason),
    'expectedAccountDigest',control_row.expected_account_digest_v2,
    'expectedAccountProjection',control_row.expected_account_projection_v2,
    'filledQuantity',COALESCE(reservation_row.filled_quantity,0),
    'leavesQuantity',COALESCE(reservation_row.leaves_quantity,0),
    'openPositionMarketValueKrw',open_position_value,'pendingBuyNotionalKrw',CASE
      WHEN reservation_row.side='BUY' AND reservation_row.reconciliation_status='NOT_APPLICABLE'
        THEN COALESCE(reservation_row.estimated_amount_krw,0) ELSE 0 END,
    'policy',jsonb_build_object(
      'capitalLimitKrw',policy_row.capital_limit_krw,'maxOpenPositions',5,
      'policyId',policy_row.policy_id,'preset',policy_row.risk_profile,
      'stopLossBps',policy_row.stop_loss_bps,'takeProfitBps',policy_row.take_profit_bps,
      'version',policy_row.version
    ),
    'positions',positions_json,'principleAssetRemainingKrw',asset_remaining,
    'principleMaxSingleOrderKrw',max_single_order,
    'instrumentCatalogSymbols',catalog_symbols,
    'providerExecRefHash',reservation_row.provider_order_ref_hash,
    'quoteSnapshot',CASE WHEN checkpoint_row.quote_snapshot_json IS NULL THEN NULL
      ELSE checkpoint_row.quote_snapshot_json::jsonb END,
    'reservation',reservation_json,
    'unfilledTerminatedQuantity',COALESCE(reservation_row.unfilled_terminated_quantity,0)
  ))::text;
END
$p1_read_automation_runtime_state_v2$;
