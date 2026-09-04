-- 실제 산출물을 우선하는 owner-scoped latest run projection.

CREATE FUNCTION public.latest_dashboard_artifact_run(
  p_actor_user_id text, p_security_version bigint, p_view_kind text
)
RETURNS TABLE(run_id text, fixture_class text, as_of timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $latest_dashboard_artifact_run_v121$
BEGIN
  IF session_user <> 'decision_app' OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST') THEN
    RAISE EXCEPTION 'dashboard projection read denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
    AND actor.status='ACTIVE' AND actor.security_version=p_security_version) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.run_id, item.fixture_class, item.as_of
  FROM public.dashboard_artifact_views item
  WHERE item.view_kind=p_view_kind AND item.owner_user_id=p_actor_user_id
  ORDER BY (item.fixture_class='SYNTHETIC_FAKE_E2E'), item.as_of DESC, item.run_id
  LIMIT 1;
END
$latest_dashboard_artifact_run_v121$;

CREATE FUNCTION public.latest_dashboard_artifact_run_authorized(
  p_capability text, p_actor_user_id text, p_security_version bigint, p_view_kind text
)
RETURNS TABLE(run_id text, fixture_class text, as_of timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $latest_dashboard_artifact_run_authorized_v121$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE' AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_ARTIFACT','DASHBOARD_ARTIFACT','latest',
    public.actor_capability_payload_hash(p_view_kind,'latest')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.latest_dashboard_artifact_run(
    p_actor_user_id,p_security_version,p_view_kind
  );
END
$latest_dashboard_artifact_run_authorized_v121$;

ALTER FUNCTION public.latest_dashboard_artifact_run(text,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.latest_dashboard_artifact_run_authorized(text,text,bigint,text) OWNER TO flyway;

REVOKE ALL ON FUNCTION
  public.latest_dashboard_artifact_run(text,bigint,text),
  public.latest_dashboard_artifact_run_authorized(text,text,bigint,text)
FROM PUBLIC,decision_app;

GRANT EXECUTE ON FUNCTION
  public.latest_dashboard_artifact_run_authorized(text,text,bigint,text)
TO decision_app;
