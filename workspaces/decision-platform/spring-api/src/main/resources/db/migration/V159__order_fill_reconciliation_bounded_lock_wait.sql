-- 동시 reconciliation은 advisory xact lock으로 직렬화되지만 decision_app의 500ms 기본
-- lock_timeout이 전체 suite 부하에서 먼저 끝났다. 이 operation만 statement 2s 안의 1.5s로 좁혀 기다린다.
CREATE FUNCTION public.acquire_order_fill_reconciliation_lock_authorized_v3(
  p_capability text,p_payload_json text
) RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $lock$
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app' THEN
    RAISE EXCEPTION 'order fill reconciliation lock v3 denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('lock_timeout','1500ms',true);
  RETURN public.acquire_order_fill_reconciliation_lock_authorized_v2(p_capability,p_payload_json);
END $lock$;
ALTER FUNCTION public.acquire_order_fill_reconciliation_lock_authorized_v3(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.acquire_order_fill_reconciliation_lock_authorized_v3(text,text) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.acquire_order_fill_reconciliation_lock_authorized_v3(text,text) TO decision_app;
