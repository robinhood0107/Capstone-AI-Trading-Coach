-- Account identifiers can exist in both ledgers; use actual Mock order provenance.
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
    AND EXISTS (SELECT 1 FROM public.orders entry_order
      WHERE entry_order.order_id=item.entry_order_id AND entry_order.user_id=p_user_id
        AND entry_order.account_id=item.account_id AND entry_order.brokerage_mode='KIS_MOCK'
        AND entry_order.provider_order_ref_hash IS NOT NULL);
  RETURN NEXT;
END
$function$;
