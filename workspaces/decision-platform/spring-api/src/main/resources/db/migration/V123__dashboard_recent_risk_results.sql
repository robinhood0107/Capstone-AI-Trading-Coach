CREATE FUNCTION public.latest_dashboard_risk_result(
  p_actor_user_id text, p_security_version bigint
)
RETURNS TABLE(decision_id text, outcome text, symbol text, evaluation_as_of timestamptz, valid_until timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $latest_dashboard_risk_result_v123$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'dashboard risk read denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
      AND actor.security_version=p_security_version
  ) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.decision_id,item.outcome,item.symbol,item.evaluation_as_of,item.valid_until
  FROM public.decisions item
  WHERE item.user_id=p_actor_user_id
  ORDER BY item.evaluation_as_of DESC,item.decision_id
  LIMIT 1;
END
$latest_dashboard_risk_result_v123$;

CREATE FUNCTION public.recent_dashboard_risk_results(
  p_actor_user_id text, p_security_version bigint
)
RETURNS TABLE(decision_id text, outcome text, symbol text, evaluation_as_of timestamptz, valid_until timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $recent_dashboard_risk_results_v123$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'dashboard risk read denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
      AND actor.security_version=p_security_version
  ) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.decision_id,item.outcome,item.symbol,item.evaluation_as_of,item.valid_until
  FROM public.decisions item
  WHERE item.user_id=p_actor_user_id
  ORDER BY item.evaluation_as_of DESC,item.decision_id
  LIMIT 20;
END
$recent_dashboard_risk_results_v123$;

CREATE FUNCTION public.latest_dashboard_risk_result_authorized(
  p_capability text, p_actor_user_id text, p_security_version bigint
)
RETURNS TABLE(decision_id text, outcome text, symbol text, evaluation_as_of timestamptz, valid_until timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $latest_dashboard_risk_result_authorized_v123$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
    AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_RISK','RISK_DECISION','latest',
    public.actor_capability_payload_hash('LATEST_RISK','latest')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.latest_dashboard_risk_result(
    p_actor_user_id,p_security_version
  );
END
$latest_dashboard_risk_result_authorized_v123$;

CREATE FUNCTION public.recent_dashboard_risk_results_authorized(
  p_capability text, p_actor_user_id text, p_security_version bigint
)
RETURNS TABLE(decision_id text, outcome text, symbol text, evaluation_as_of timestamptz, valid_until timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $recent_dashboard_risk_results_authorized_v123$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
    AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_RISK','RISK_DECISION','recent',
    public.actor_capability_payload_hash('RECENT_RISK','recent')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.recent_dashboard_risk_results(
    p_actor_user_id,p_security_version
  );
END
$recent_dashboard_risk_results_authorized_v123$;

ALTER FUNCTION public.latest_dashboard_risk_result(text,bigint) OWNER TO flyway;
ALTER FUNCTION public.recent_dashboard_risk_results(text,bigint) OWNER TO flyway;
ALTER FUNCTION public.latest_dashboard_risk_result_authorized(text,text,bigint) OWNER TO flyway;
ALTER FUNCTION public.recent_dashboard_risk_results_authorized(text,text,bigint) OWNER TO flyway;

REVOKE ALL ON FUNCTION
  public.latest_dashboard_risk_result(text,bigint),
  public.recent_dashboard_risk_results(text,bigint),
  public.latest_dashboard_risk_result_authorized(text,text,bigint),
  public.recent_dashboard_risk_results_authorized(text,text,bigint)
FROM PUBLIC,decision_app;

GRANT EXECUTE ON FUNCTION
  public.latest_dashboard_risk_result_authorized(text,text,bigint),
  public.recent_dashboard_risk_results_authorized(text,text,bigint)
TO decision_app;
