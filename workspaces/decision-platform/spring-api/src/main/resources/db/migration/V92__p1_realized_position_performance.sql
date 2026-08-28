-- V91은 청산 수량과 청산 평균체결가까지만 저장한다. lot 단위 실현손익을 남길 자리가 없어
-- 성과를 계산할 근거가 없었다. 왕복 비용을 반영한 실현손익만 추가한다.

ALTER TABLE public.automation_positions NO FORCE ROW LEVEL SECURITY;

ALTER TABLE public.automation_positions
  ADD COLUMN realized_pnl_krw bigint,
  ADD CONSTRAINT automation_position_realized_shape_v92_check CHECK (
    realized_pnl_krw IS NULL
    OR (
      exit_filled_quantity > 0
      AND exit_average_fill_price_krw IS NOT NULL
      AND entry_average_fill_price_krw IS NOT NULL
    )
  );

ALTER TABLE public.automation_positions FORCE ROW LEVEL SECURITY;

-- owner scope 안에서 실현 성과만 돌려준다. 집계는 SECURITY DEFINER로 고정해 소유자 외 row가
-- 섞이지 않게 한다.
CREATE FUNCTION public.p1_automation_realized_performance_v2(p_user_id text)
RETURNS TABLE(
  closed_position_count bigint,
  realized_pnl_krw bigint,
  realized_gross_krw bigint,
  winning_position_count bigint,
  losing_position_count bigint
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_realized_performance_v2$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation realized performance scope denied' USING ERRCODE='42501';
  END IF;
  SELECT
    count(*),
    COALESCE(sum(item.realized_pnl_krw),0),
    COALESCE(sum(
      (item.exit_average_fill_price_krw - item.entry_average_fill_price_krw)
        * item.exit_filled_quantity
    ),0),
    count(*) FILTER (WHERE item.realized_pnl_krw>0),
    count(*) FILTER (WHERE item.realized_pnl_krw<0)
  INTO closed_position_count, realized_pnl_krw, realized_gross_krw,
       winning_position_count, losing_position_count
  FROM public.automation_positions item
  WHERE item.user_id=p_user_id AND item.status='CLOSED'
    AND item.realized_pnl_krw IS NOT NULL;
  RETURN NEXT;
END
$p1_automation_realized_performance_v2$;

ALTER FUNCTION public.p1_automation_realized_performance_v2(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_realized_performance_v2(text)
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,
  decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_automation_realized_performance_v2(text) TO decision_app;
