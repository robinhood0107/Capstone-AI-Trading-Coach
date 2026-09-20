-- 체결 품질을 바꾸려면 근거가 있어야 하는데 지금은 아무 기록도 없다.
--
-- 현재 체결 모델은 마지막 체결가 ±1 tick 한 방이고 재호가가 없다(`_limit_price`).
-- 스프레드가 실제로 얼마나 넓은지, 그 지정가가 매도호가 뒤에 서는 빈도가 얼마인지,
-- 주문이 체결되기까지 얼마나 걸리는지 **아무도 모른다.** 일봉 백테스트로는 체결 확률을
-- 모델링할 수 없으므로 근거는 모의계좌 실측으로만 만들 수 있다.
--
-- 새 표를 만들지 않는다. `automation_portfolio_order_executions_v1` 이 이미 열 개 중 여섯을
-- 갖고 있다 - symbol, limit_price_krw(제출 지정가), quantity, applied_filled_quantity,
-- applied_fill_notional_krw(→ 평균체결가 = 명목/수량), state(최종 결과). 체결 시각은
-- `order_fill_observations.observed_at` 에 델타마다 한 행씩 있고, V173 이 그 표에 진짜
-- PARTIAL_FILL 행을 처음으로 채우게 된다. 부족한 것은 숫자 넷과 시각 둘뿐이다.
--
-- **전부 nullable 이고 all-or-nothing 이다.** ETF/ETN 은 모의에 전용 호가 TR 이 없고
-- (FHPST02400200 미지원) 조회 실패도 있다. 그 경우 여섯 칸이 모두 NULL 인 채 주문은 그대로
-- 나간다 - **원장이 거래를 막을 수 없어야 한다.**
--
-- submitted_at 은 created_at(계획 시각, 수 분 전일 수 있음)과 별개다. 제출 순간의
-- 스프레드만 의미가 있다.
SET LOCAL row_security = on;

ALTER TABLE public.automation_portfolio_order_executions_v1
  ADD COLUMN IF NOT EXISTS submitted_at timestamptz,
  ADD COLUMN IF NOT EXISTS book_observed_at timestamptz,
  ADD COLUMN IF NOT EXISTS book_best_ask_krw bigint,
  ADD COLUMN IF NOT EXISTS book_best_ask_quantity bigint,
  ADD COLUMN IF NOT EXISTS book_best_bid_krw bigint,
  ADD COLUMN IF NOT EXISTS book_best_bid_quantity bigint,
  ADD COLUMN IF NOT EXISTS book_source_tr_id text;

ALTER TABLE public.automation_portfolio_order_executions_v1
  DROP CONSTRAINT IF EXISTS automation_portfolio_execution_book_shape_check;
ALTER TABLE public.automation_portfolio_order_executions_v1
  ADD CONSTRAINT automation_portfolio_execution_book_shape_check CHECK (
    (book_observed_at IS NULL AND book_best_ask_krw IS NULL AND book_best_ask_quantity IS NULL
      AND book_best_bid_krw IS NULL AND book_best_bid_quantity IS NULL
      AND book_source_tr_id IS NULL)
    OR (book_observed_at IS NOT NULL AND book_best_ask_krw > 0 AND book_best_bid_krw > 0
      AND book_best_ask_krw > book_best_bid_krw
      AND book_best_ask_quantity >= 0 AND book_best_bid_quantity >= 0
      AND book_source_tr_id ~ '^[A-Z0-9]{1,16}$')
  );

-- 기록 함수는 finish 에 접어 넣지 않는다. 그 본문은 줄 단위로 고정돼 있고
-- (`test_the_replaced_finish_function_kept_every_other_settlement_guard`) 진단 기록 때문에
-- 그 고정을 흔들 이유가 없다. 형제 함수와 같은 claim/역할 전문을 쓴다.
CREATE OR REPLACE FUNCTION public.p1_record_automation_portfolio_book_v1(
  p_run_id text, p_claim_token_hash text, p_ordinal integer, p_book jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog
AS $book$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE touched integer;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_run_id IS NULL
     OR p_claim_token_hash !~ '^sha256:[0-9a-f]{64}$' OR p_ordinal NOT BETWEEN 1 AND 5
     OR jsonb_typeof(p_book)<>'object' THEN
    RAISE EXCEPTION 'automation portfolio book input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF claim.run_id IS NULL THEN
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);

  -- 이미 채워져 있으면 덮지 않는다. 재생(replay)에서 제출 순간의 호가가 바뀌면 안 된다.
  UPDATE public.automation_portfolio_order_executions_v1 SET
    submitted_at=COALESCE(submitted_at,statement_timestamp()),
    book_observed_at=statement_timestamp(),
    book_best_ask_krw=(p_book->>'bestAskKrw')::bigint,
    book_best_ask_quantity=(p_book->>'bestAskQuantity')::bigint,
    book_best_bid_krw=(p_book->>'bestBidKrw')::bigint,
    book_best_bid_quantity=(p_book->>'bestBidQuantity')::bigint,
    book_source_tr_id=p_book->>'trId',
    updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal AND book_observed_at IS NULL;
  GET DIAGNOSTICS touched=ROW_COUNT;
  RETURN CASE WHEN touched>0 THEN 'UPDATED' ELSE 'NO_OP' END;
END
$book$;

ALTER FUNCTION public.p1_record_automation_portfolio_book_v1(text,text,integer,jsonb)
  OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_record_automation_portfolio_book_v1(text,text,integer,jsonb)
  FROM PUBLIC, decision_app;
GRANT EXECUTE ON FUNCTION public.p1_record_automation_portfolio_book_v1(text,text,integer,jsonb)
  TO decision_automation_runtime;

-- 3중 조인을 매번 손으로 쓰지 않게 한다. 이 뷰가 marketable-limit 판단의 원장이다.
CREATE OR REPLACE VIEW public.automation_portfolio_execution_quality_v1 AS
SELECT
  execution.run_id,
  execution.ordinal,
  execution.symbol,
  execution.side,
  execution.quantity,
  execution.limit_price_krw,
  execution.submitted_at,
  execution.book_observed_at,
  execution.book_best_ask_krw,
  execution.book_best_ask_quantity,
  execution.book_best_bid_krw,
  execution.book_best_bid_quantity,
  execution.book_best_ask_krw - execution.book_best_bid_krw AS spread_krw,
  -- 제출 지정가가 최우선 매도호가에 얼마나 못 미쳤는가. 양수면 호가창 뒤에 선 것이다.
  execution.book_best_ask_krw - execution.limit_price_krw AS ask_gap_krw,
  execution.applied_filled_quantity,
  CASE WHEN execution.applied_filled_quantity > 0
       THEN (execution.applied_fill_notional_krw / execution.applied_filled_quantity)::bigint
  END AS average_fill_price_krw,
  execution.state AS final_state,
  execution.updated_at,
  (SELECT min(fill.observed_at) FROM public.order_fill_observations fill
   WHERE fill.order_id=execution.order_id) AS first_fill_at,
  (SELECT max(fill.observed_at) FROM public.order_fill_observations fill
   WHERE fill.order_id=execution.order_id) AS last_fill_at
FROM public.automation_portfolio_order_executions_v1 execution;

ALTER VIEW public.automation_portfolio_execution_quality_v1 OWNER TO flyway;
REVOKE ALL ON public.automation_portfolio_execution_quality_v1 FROM PUBLIC;
GRANT SELECT ON public.automation_portfolio_execution_quality_v1 TO decision_app;
