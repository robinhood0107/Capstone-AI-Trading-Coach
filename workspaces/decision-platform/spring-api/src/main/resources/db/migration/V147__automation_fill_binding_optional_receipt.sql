-- Project verified automation completion into the existing order/fill ledger atomically.
SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.p1_sync_completed_automation_order_v1(p_run text,p_claim_hash text,p_exec_hash text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE reservation public.automation_order_reservations%ROWTYPE;
DECLARE stored public.orders%ROWTYPE;
DECLARE observation_id text;
DECLARE final_status text;
DECLARE event_seq integer;
DECLARE payload jsonb;
BEGIN
 IF session_user<>'decision_automation_runtime' OR p_exec_hash IS NULL OR p_exec_hash!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'automation fill projection scope denied' USING ERRCODE='42501'; END IF;
 PERFORM set_config('app.automation_claim_scan','1',true);
 SELECT * INTO claim FROM public.automation_runtime_claim WHERE run_id=p_run AND claim_token_hash=p_claim_hash;
 PERFORM set_config('app.automation_claim_scan','0',true);
 IF claim.run_id IS NULL THEN RAISE EXCEPTION 'automation fill claim unavailable' USING ERRCODE='42501'; END IF;
 PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
 SELECT a.* INTO reservation FROM public.automation_order_reservations a
 JOIN public.automation_runs r USING(run_id)
 WHERE a.run_id=p_run AND a.user_id=claim.user_id AND r.state='COMPLETED'
  AND r.physical_submit_count=1 AND a.logical_submit_count=1
  AND a.filled_quantity>0 AND a.leaves_quantity=0 AND a.average_fill_price_krw>0;
 IF reservation.order_id IS NULL THEN RAISE EXCEPTION 'automation fill evidence incomplete' USING ERRCODE='40001'; END IF;
 SELECT * INTO stored FROM public.orders WHERE order_id=reservation.order_id FOR UPDATE;
 IF stored.user_id<>claim.user_id OR stored.brokerage_mode<>'KIS_MOCK'
    OR stored.quantity<>reservation.quantity OR stored.symbol<>reservation.symbol
    OR stored.side<>reservation.side OR stored.provider_order_ref_hash IS NULL
    OR (reservation.provider_order_ref_hash IS NOT NULL AND stored.provider_order_ref_hash IS DISTINCT FROM reservation.provider_order_ref_hash)
    OR (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(stored.order_intent_json)) IS DISTINCT FROM
       (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(reservation.exact_intent_json::jsonb))
    OR NOT EXISTS(SELECT 1 FROM public.automation_runs r JOIN public.automation_runtime_checkpoint cp USING(run_id)
      WHERE r.run_id=p_run AND r.account_id=stored.account_id AND cp.decision_id=stored.decision_id) THEN
  RAISE EXCEPTION 'automation fill order binding mismatch' USING ERRCODE='40001'; END IF;
 final_status:=CASE WHEN reservation.filled_quantity=stored.quantity THEN 'FILLED' ELSE 'CANCELLED' END;
 IF stored.filled_quantity=reservation.filled_quantity AND stored.status=final_status
    AND stored.reconciliation_status='MATCHED' THEN RETURN; END IF;
 IF stored.filled_quantity<>0 THEN RAISE EXCEPTION 'automation fill requires existing partial reconciliation' USING ERRCODE='40001'; END IF;
 observation_id:='ofo_'||md5(stored.order_id||':'||p_exec_hash);
 payload:=jsonb_build_object('providerExecRefHash',p_exec_hash,'fillQuantity',reservation.filled_quantity,
  'fillPriceKrw',reservation.average_fill_price_krw,'cumulativeQuantity',reservation.filled_quantity,
  'leavesQuantity',stored.quantity-reservation.filled_quantity,'sourceVersion','kis-mock-automation-execution-v1');
 INSERT INTO public.order_fill_observations VALUES(observation_id,stored.order_id,p_exec_hash,
  CASE WHEN final_status='FILLED' THEN 'FILL' ELSE 'PARTIAL_FILL' END,
  reservation.filled_quantity,reservation.average_fill_price_krw,reservation.filled_quantity,
  stored.quantity-reservation.filled_quantity,reservation.average_fill_price_krw,
  reservation.updated_at,statement_timestamp(),'1','kis-mock-automation-execution-v1',p_run,'COMPLETE',
  encode(public.digest(convert_to(payload::text,'UTF8'),'sha256'),'hex'));
 INSERT INTO public.order_fill_application_receipts VALUES('ofr_'||md5(observation_id),observation_id,
  stored.order_id,'APPLIED',NULL,statement_timestamp());
 SELECT COALESCE(max(e.event_seq),0)+1 INTO event_seq FROM public.order_events e WHERE order_id=stored.order_id;
 INSERT INTO public.order_events VALUES('oev_'||md5(observation_id),stored.order_id,
  CASE WHEN final_status='FILLED' THEN 'MOCK_ORDER_FILLED' ELSE 'MOCK_ORDER_PARTIALLY_FILLED' END,
  CASE WHEN final_status='FILLED' THEN 'FILLED' ELSE 'PARTIALLY_FILLED' END,payload,statement_timestamp(),event_seq);
 IF final_status='CANCELLED' THEN
  INSERT INTO public.order_events VALUES('oev_'||md5(observation_id||':cancel'),stored.order_id,
   'MOCK_ORDER_CANCELLED','CANCELLED',payload||jsonb_build_object('leavesQuantity',0),statement_timestamp(),event_seq+1);
 END IF;
 UPDATE public.orders SET status=final_status,filled_quantity=reservation.filled_quantity,leaves_quantity=0,
  unfilled_terminated_quantity=quantity-reservation.filled_quantity,
  average_fill_price_krw=reservation.average_fill_price_krw,reconciliation_status='MATCHED',
  reconciled_at=statement_timestamp(),updated_at=statement_timestamp() WHERE order_id=stored.order_id;
END $f$;
ALTER FUNCTION public.p1_sync_completed_automation_order_v1(text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_sync_completed_automation_order_v1(text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_sync_completed_automation_order_v1(text,text,text) TO decision_automation_runtime;
