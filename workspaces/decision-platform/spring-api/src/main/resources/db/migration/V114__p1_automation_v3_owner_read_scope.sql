-- Keep the runtime-only automation checkpoint table private while allowing an
-- authenticated owner to read Automation V3 run projections.  The V106
-- runtime RLS policy referenced automation_runtime_checkpoint directly; PostgreSQL
-- therefore required decision_app to have SELECT on that private table even
-- when the owner policy was the branch that should authorize the read.

CREATE FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1(p_run_id text)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_ai_judgement_runtime_scope_v1$
  SELECT CASE
    WHEN session_user<>'decision_automation_runtime' THEN false
    ELSE EXISTS (
      SELECT 1 FROM public.automation_runtime_checkpoint checkpoint
      WHERE checkpoint.run_id=p_run_id
        AND checkpoint.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true)
    )
  END
$p1_automation_ai_judgement_runtime_scope_v1$;

ALTER FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1(text)
  TO decision_app,decision_automation_runtime;

DROP POLICY automation_ai_judgements_scope_v106 ON public.automation_ai_judgements;
CREATE POLICY automation_ai_judgements_runtime_v114 ON public.automation_ai_judgements TO PUBLIC
USING (public.p1_automation_ai_judgement_runtime_scope_v1(run_id))
WITH CHECK (public.p1_automation_ai_judgement_runtime_scope_v1(run_id));

