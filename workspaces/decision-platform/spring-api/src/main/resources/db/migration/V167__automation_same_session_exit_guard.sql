-- 포트폴리오 계획 입력에 진입 세션을 실어 "오늘 산 것을 오늘 되파는" 경로를 닫는다.
-- 단일 엔진 청산은 파이썬에서 사유마다 대칭 가드를 걸었지만, 목표비중 초과(REDUCE)
-- 경로에는 entry_session 이 애초에 전달되지 않아 가드를 걸 재료가 없었다.
-- V163 함수 본문을 그대로 두고 positions JSON 에 entrySession 과 최상위 sessionDate 만 더한다.
-- 권한 검사, claim 상태 조건, 편입 baseline 차감, 다른 run 예약 제외는 원본 그대로다.
-- forward-only.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_read_automation_portfolio_sources_v1(p_run_id text,p_claim_token_hash text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $sources$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE run public.automation_runs%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE base public.automation_policy_versions%ROWTYPE;
DECLARE capital public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE balance public.portfolio_balance_observations%ROWTYPE;
DECLARE realized bigint:=0;
DECLARE bot_value bigint:=0;
DECLARE reserved bigint:=0;
DECLARE positions jsonb:='[]'::jsonb;
DECLARE order_count integer:=0;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation portfolio source access denied' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'));
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO run FROM public.automation_runs WHERE run_id=p_run_id;
  SELECT * INTO control FROM public.automation_control WHERE user_id=claim.user_id;
  SELECT * INTO base FROM public.automation_policy_versions item
  WHERE item.policy_id=run.policy_id AND item.version=run.policy_version AND item.user_id=claim.user_id;
  SELECT * INTO capital FROM public.automation_capital_policy_versions_v1 item
  WHERE item.user_id=claim.user_id AND item.effective_from_session<=claim.session_date
  ORDER BY item.version DESC LIMIT 1;
  SELECT * INTO balance FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=claim.user_id AND item.source='KIS_MOCK'
    AND item.context_status='ACTIVE' AND item.completeness='COMPLETE'
    AND item.account_scope_hash LIKE substr(control.account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  IF run.run_id IS NULL OR control.account_id IS NULL OR base.policy_id IS NULL
     OR capital.user_id IS NULL OR balance.observation_id IS NULL THEN
    RAISE EXCEPTION 'automation portfolio sources unavailable' USING ERRCODE='40001'; END IF;
  SELECT COALESCE(sum(CASE WHEN adopted.position_id IS NOT NULL
      THEN COALESCE(position.realized_pnl_krw,0)-adopted.realized_pnl_baseline_krw
      WHEN position.created_at>=capital.transition_started_at THEN COALESCE(position.realized_pnl_krw,0)
      ELSE 0 END),0) INTO realized
  FROM public.automation_positions position
  LEFT JOIN public.automation_position_adoption_receipts_v1 adopted ON adopted.position_id=position.position_id
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id;
  SELECT COALESCE(sum(observation.market_value_krw),0),
    COALESCE(jsonb_agg(jsonb_build_object('positionId',position.position_id,'symbol',position.symbol,
      'quantity',position.quantity,'marketValueKrw',observation.market_value_krw,
      'priceKrw',CASE WHEN position.quantity>0 THEN observation.market_value_krw/position.quantity ELSE NULL END,
      'entrySession',position.entry_session,
      'status',position.status) ORDER BY position.symbol),'[]'::jsonb)
  INTO bot_value,positions
  FROM public.automation_positions position
  JOIN public.portfolio_position_observations observation
    ON observation.balance_observation_id=balance.observation_id AND observation.symbol=position.symbol
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id
    AND position.status IN ('OPEN','EXIT_PENDING');
  SELECT COALESCE(sum(execution.reserved_buy_cash_krw),0) INTO reserved
  FROM public.automation_portfolio_order_executions_v1 execution
  JOIN public.automation_portfolio_session_snapshots_v1 snapshot USING(run_id)
  WHERE snapshot.user_id=claim.user_id AND execution.side='BUY'
    AND execution.run_id<>p_run_id
    AND execution.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION');
  SELECT count(*) INTO order_count FROM public.orders item
  WHERE item.user_id=claim.user_id AND item.account_id=control.account_id
    AND (item.submitted_at AT TIME ZONE 'Asia/Seoul')::date=claim.session_date;
  RETURN jsonb_build_object('automationPolicyId',base.policy_id,'automationPolicyVersion',base.version,
    'capitalPolicyVersion',capital.version,'reinvestRealizedPnl',capital.reinvest_realized_pnl,
    'cashBufferBps',capital.cash_buffer_bps,'rebalanceDeviationBps',capital.rebalance_deviation_bps,
    'minimumAdjustmentKrw',capital.minimum_adjustment_krw,
    'maxOrdersPerSession',capital.max_orders_per_session,'principleVersionId',run.principle_version_id,
    'principleVersion',run.principle_version,'configuredCapitalKrw',base.capital_limit_krw,
    -- 포트폴리오 계획이 자기 기본값을 따로 갖지 않도록 사용자 원칙의 상한을 싣는다.
    -- base 는 원본 표라 저장 전 버전은 NULL 이다. effective view 와 같은 기본값을 쓴다.
    'maxOpenPositions',COALESCE(base.max_open_positions,10),
    'realizedPnlSinceTransitionKrw',realized,'balanceObservationId',balance.observation_id,
    'brokerCashKrw',balance.cash_krw,'botPositionMarketValueKrw',bot_value,
    'reservedBuyCashKrw',reserved,'ordersAlreadySubmitted',order_count,
    'sessionDate',claim.session_date,'positions',positions)::text;
END $sources$;

ALTER FUNCTION public.p1_read_automation_portfolio_sources_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_portfolio_sources_v1(text,text)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_portfolio_sources_v1(text,text)
  TO decision_automation_runtime;

-- 세션 주문 상한을 3 -> 5 로 올린다. 상한 10 슬롯을 빈 상태에서 채우는 데 3건이면
-- 4 거래일이 걸려 자본이 오래 놀았다. ordinal 원장과 KIS 호출 예산(12 -> 20)이
-- 함께 이 수를 지탱한다. 기존 행은 그대로 유효하다.
-- 제약명은 생성 당시 자동 부여된 것을 그대로 쓴다(테이블명 63자 절단).
ALTER TABLE public.automation_capital_policy_versions_v1
  DROP CONSTRAINT IF EXISTS automation_capital_policy_versions_max_orders_per_session_check;
ALTER TABLE public.automation_capital_policy_versions_v1
  ADD CONSTRAINT automation_capital_policy_versions_v1_max_orders_per_session_check
  CHECK (max_orders_per_session BETWEEN 1 AND 5);

-- ordinal 도 같은 상한을 따른다.
ALTER TABLE public.automation_portfolio_order_executions_v1
  DROP CONSTRAINT IF EXISTS automation_portfolio_order_executions_v1_ordinal_check;
ALTER TABLE public.automation_portfolio_order_executions_v1
  ADD CONSTRAINT automation_portfolio_order_executions_v1_ordinal_check
  CHECK (ordinal BETWEEN 1 AND 5);
