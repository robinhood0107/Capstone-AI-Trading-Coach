-- Adopted holdings need no bot entry order; their actual Mock exit is the authority.
SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.p1_automation_realized_performance_v2(p_user_id text)
 RETURNS TABLE(closed_position_count bigint, realized_pnl_krw bigint, realized_gross_krw bigint, winning_position_count bigint, losing_position_count bigint)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation realized performance scope denied' USING ERRCODE='42501';
  END IF;
  SELECT count(*),COALESCE(sum(item.realized_pnl_krw),0),COALESCE(sum(
      (item.exit_average_fill_price_krw-item.entry_average_fill_price_krw)*item.exit_filled_quantity
    ),0),count(*) FILTER (WHERE item.realized_pnl_krw>0),
    count(*) FILTER (WHERE item.realized_pnl_krw<0)
  INTO closed_position_count,realized_pnl_krw,realized_gross_krw,
       winning_position_count,losing_position_count
  FROM public.automation_positions item
  WHERE item.user_id=p_user_id AND item.status='CLOSED' AND item.realized_pnl_krw IS NOT NULL
    AND item.account_id=(SELECT control.account_id FROM public.automation_control control
      WHERE control.user_id=p_user_id AND control.brokerage_mode='KIS_MOCK')
    AND EXISTS (SELECT 1 FROM public.automation_runs run
      JOIN public.automation_order_reservations reservation USING(run_id)
      JOIN public.orders exit_order ON exit_order.order_id=reservation.order_id
      WHERE run.user_id=p_user_id AND run.account_id=item.account_id
        AND run.selected_symbol=item.symbol AND run.selected_side='SELL'
        AND run.state='COMPLETED' AND run.physical_submit_count=1
        AND exit_order.brokerage_mode='KIS_MOCK' AND exit_order.provider_order_ref_hash IS NOT NULL
        AND item.closed_at BETWEEN run.started_at AND run.updated_at);
  RETURN NEXT;
END
$function$;
