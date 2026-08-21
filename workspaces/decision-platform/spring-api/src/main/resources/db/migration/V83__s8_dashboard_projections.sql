-- S8 stores only sanitized, bounded projections; raw artifacts remain outside PostgreSQL.
CREATE TABLE public.dashboard_artifact_staging (
  artifact_id text NOT NULL,
  view_kind text NOT NULL CHECK (view_kind IN ('MODEL_EVALUATION','BACKTEST')),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  run_id text NOT NULL,
  file_name text NOT NULL,
  file_hash text NOT NULL,
  schema_version text NOT NULL,
  fixture_class text NOT NULL CHECK (fixture_class = 'SYNTHETIC_FAKE_E2E'),
  evidence_mode text NOT NULL CHECK (evidence_mode = 'SYNTHETIC_DEMO'),
  projection_text text NOT NULL,
  projection_hash text NOT NULL,
  as_of timestamptz NOT NULL,
  fresh_until timestamptz NOT NULL,
  staged_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (artifact_id, view_kind),
  CHECK (artifact_id ~ '^artifact_[A-Za-z0-9_-]{8,96}$'),
  CHECK (run_id ~ '^demo_[A-Za-z0-9_-]{8,96}$'),
  CHECK (file_name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  CHECK (file_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (schema_version = '1.0.0'),
  CHECK (projection_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (octet_length(projection_text) BETWEEN 2 AND 524288),
  CHECK (jsonb_typeof(projection_text::jsonb) = 'object'),
  CHECK (fresh_until >= as_of)
);

CREATE TABLE public.dashboard_artifact_views (
  artifact_id text NOT NULL,
  view_kind text NOT NULL CHECK (view_kind IN ('MODEL_EVALUATION','BACKTEST')),
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  run_id text NOT NULL,
  fixture_class text NOT NULL CHECK (fixture_class IN ('SYNTHETIC_FAKE_E2E','REAL_ARTIFACT')),
  evidence_mode text NOT NULL CHECK (evidence_mode IN ('SYNTHETIC_DEMO','REAL_ARTIFACT')),
  projection_json jsonb NOT NULL,
  projection_hash text NOT NULL,
  as_of timestamptz NOT NULL,
  fresh_until timestamptz NOT NULL,
  published_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (view_kind, run_id),
  UNIQUE (artifact_id, view_kind),
  CHECK (jsonb_typeof(projection_json) = 'object' AND octet_length(projection_json::text) <= 524288),
  CHECK (projection_hash ~ '^sha256:[0-9a-f]{64}$' AND fresh_until >= as_of)
);

CREATE TABLE public.artifact_ingest_projection (
  artifact_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  file_name text NOT NULL,
  producer text NOT NULL CHECK (producer IN ('decision-platform','return-engine')),
  run_id text NOT NULL,
  file_hash text NOT NULL,
  schema_version text NOT NULL,
  status text NOT NULL CHECK (status IN ('DISCOVERED','VALIDATED','INGESTED','FAILED','SKIPPED')),
  last_ingested_at timestamptz,
  duplicate boolean NOT NULL DEFAULT false,
  CHECK (artifact_id ~ '^artifact_[A-Za-z0-9_-]{8,96}$'),
  CHECK (run_id ~ '^(run|demo)_[A-Za-z0-9_-]{8,96}$'),
  CHECK (file_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (schema_version ~ '^[1-9][0-9]*\.[0-9]+\.[0-9]+$')
);

CREATE TABLE public.artifact_ingest_admin_read_audit (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_user_id text NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TRIGGER dashboard_artifact_staging_append_only BEFORE UPDATE OR DELETE ON public.dashboard_artifact_staging
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();
CREATE TRIGGER dashboard_artifact_views_append_only BEFORE UPDATE OR DELETE ON public.dashboard_artifact_views
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();
CREATE TRIGGER artifact_ingest_projection_append_only BEFORE UPDATE OR DELETE ON public.artifact_ingest_projection
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();
CREATE TRIGGER artifact_ingest_admin_read_audit_append_only BEFORE UPDATE OR DELETE ON public.artifact_ingest_admin_read_audit
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE FUNCTION public.stage_synthetic_dashboard_view(
  p_artifact_id text,p_owner_user_id text,p_run_id text,p_file_name text,p_file_hash text,
  p_view_kind text,p_projection_text text,p_projection_hash text,p_as_of timestamptz,p_fresh_until timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $stage_synthetic_dashboard_view$
DECLARE changed integer; existing public.dashboard_artifact_staging%ROWTYPE;
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'dashboard staging role denied' USING ERRCODE='42501'; END IF;
  IF p_artifact_id !~ '^artifact_[A-Za-z0-9_-]{8,96}$' OR p_run_id !~ '^demo_[A-Za-z0-9_-]{8,96}$'
     OR p_file_name !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     OR p_file_hash !~ '^sha256:[0-9a-f]{64}$' OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST')
     OR p_projection_hash !~ '^sha256:[0-9a-f]{64}$' OR p_projection_text IS NULL
     OR octet_length(p_projection_text) NOT BETWEEN 2 AND 524288 OR jsonb_typeof(p_projection_text::jsonb) <> 'object'
     OR p_projection_hash <> ('sha256:' || encode(public.digest(p_projection_text,'sha256'),'hex'))
     OR p_fresh_until < p_as_of THEN
    RAISE EXCEPTION 'invalid synthetic dashboard projection' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE user_id=p_owner_user_id AND status='ACTIVE') THEN RETURN false; END IF;
  SELECT * INTO existing FROM public.dashboard_artifact_staging
    WHERE artifact_id=p_artifact_id AND view_kind=p_view_kind;
  IF FOUND THEN
    IF existing.owner_user_id=p_owner_user_id AND existing.run_id=p_run_id AND existing.file_name=p_file_name
       AND existing.file_hash=p_file_hash AND existing.projection_text=p_projection_text
       AND existing.projection_hash=p_projection_hash AND existing.as_of=p_as_of
       AND existing.fresh_until=p_fresh_until THEN RETURN false; END IF;
    RAISE EXCEPTION 'synthetic dashboard identity conflict' USING ERRCODE='23505';
  END IF;
  INSERT INTO public.dashboard_artifact_staging(
    artifact_id,view_kind,owner_user_id,run_id,file_name,file_hash,schema_version,fixture_class,
    evidence_mode,projection_text,projection_hash,as_of,fresh_until
  ) VALUES (
    p_artifact_id,p_view_kind,p_owner_user_id,p_run_id,p_file_name,p_file_hash,'1.0.0',
    'SYNTHETIC_FAKE_E2E','SYNTHETIC_DEMO',p_projection_text,p_projection_hash,p_as_of,p_fresh_until
  );
  GET DIAGNOSTICS changed=ROW_COUNT; RETURN changed=1;
END
$stage_synthetic_dashboard_view$;

CREATE FUNCTION public.materialize_dashboard_artifact_receipt()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $materialize_dashboard_artifact_receipt$
DECLARE staged record;
BEGIN
  IF NEW.job_type <> 'ARTIFACT_INGEST' OR NEW.artifact_id IS NULL THEN RETURN NEW; END IF;
  FOR staged IN SELECT * FROM public.dashboard_artifact_staging WHERE artifact_id=NEW.artifact_id ORDER BY view_kind LOOP
    INSERT INTO public.dashboard_artifact_views(
      artifact_id,view_kind,owner_user_id,run_id,fixture_class,evidence_mode,projection_json,
      projection_hash,as_of,fresh_until
    ) VALUES (
      staged.artifact_id,staged.view_kind,staged.owner_user_id,staged.run_id,staged.fixture_class,
      staged.evidence_mode,staged.projection_text::jsonb,staged.projection_hash,staged.as_of,staged.fresh_until
    ) ON CONFLICT DO NOTHING;
    INSERT INTO public.artifact_ingest_projection(
      artifact_id,owner_user_id,file_name,producer,run_id,file_hash,schema_version,status,last_ingested_at,duplicate
    ) VALUES (
      staged.artifact_id,staged.owner_user_id,staged.file_name,'decision-platform',staged.run_id,
      staged.file_hash,staged.schema_version,'INGESTED',statement_timestamp(),false
    ) ON CONFLICT DO NOTHING;
  END LOOP;
  RETURN NEW;
END
$materialize_dashboard_artifact_receipt$;

CREATE TRIGGER async_receipt_materializes_dashboard
AFTER INSERT ON public.async_materialization_receipt
FOR EACH ROW EXECUTE FUNCTION public.materialize_dashboard_artifact_receipt();

CREATE FUNCTION public.read_dashboard_artifact_view(
  p_actor_user_id text,p_security_version bigint,p_view_kind text,p_run_id text
)
RETURNS TABLE(projection_json jsonb,evidence_mode text,fixture_class text,as_of timestamptz,fresh_until timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_artifact_view$
BEGIN
  IF session_user <> 'decision_app' OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST') THEN
    RAISE EXCEPTION 'dashboard projection read denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
    AND actor.status='ACTIVE' AND actor.security_version=p_security_version) THEN RETURN; END IF;
  RETURN QUERY SELECT item.projection_json,item.evidence_mode,item.fixture_class,item.as_of,item.fresh_until
  FROM public.dashboard_artifact_views item
  WHERE item.view_kind=p_view_kind AND item.run_id=p_run_id AND item.owner_user_id=p_actor_user_id LIMIT 1;
END
$read_dashboard_artifact_view$;

CREATE FUNCTION public.read_dashboard_risk_view(p_actor_user_id text,p_security_version bigint,p_decision_id text)
RETURNS TABLE(decision_id text,outcome text,evaluation_as_of timestamptz,valid_until timestamptz,
  reasons jsonb,principles jsonb,risk_items jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_risk_view$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'dashboard risk read denied' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
    AND actor.status='ACTIVE' AND actor.security_version=p_security_version) THEN RETURN; END IF;
  PERFORM set_config('app.actor_user_id',p_actor_user_id,true);
  RETURN QUERY SELECT decision.decision_id,decision.outcome,decision.evaluation_as_of,decision.valid_until,
    coalesce(
      (SELECT jsonb_agg(v.message ORDER BY v.ordinal) FROM public.decision_violations v
        WHERE v.decision_id=decision.decision_id),
      (SELECT jsonb_agg(left(issue->>'message',256)) FROM jsonb_array_elements(
        coalesce(decision.result_json#>'{riskDecision,issues}','[]'::jsonb)) issue),
      '[]'::jsonb
    ),
    jsonb_build_array(left(principle.title,256)),
    coalesce(
      (SELECT jsonb_agg(jsonb_build_object(
        'code',left('RISK_' || upper(regexp_replace(coalesce(v.public_code,v.rule_id),'[^A-Za-z0-9]+','_','g')),64),
        'severity',v.severity,'summary',left(v.message,256)) ORDER BY v.ordinal)
        FROM public.decision_violations v WHERE v.decision_id=decision.decision_id),
      (SELECT jsonb_agg(jsonb_build_object(
        'code',left('RISK_' || upper(regexp_replace(issue->>'code','[^A-Za-z0-9]+','_','g')),64),
        'severity','WARN','summary',left(issue->>'message',256))) FROM jsonb_array_elements(
        coalesce(decision.result_json#>'{riskDecision,issues}','[]'::jsonb)) issue),
      '[]'::jsonb
    )
  FROM public.decisions decision JOIN public.principles principle ON principle.principle_id=decision.principle_id
  WHERE decision.decision_id=p_decision_id AND decision.user_id=p_actor_user_id LIMIT 1;
END
$read_dashboard_risk_view$;

CREATE FUNCTION public.read_dashboard_rag_sources(p_actor_user_id text,p_security_version bigint,p_answer_id text)
RETURNS TABLE(answer_id text,created_at timestamptz,expires_at timestamptz,sources jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_rag_sources$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'dashboard RAG read denied' USING ERRCODE='42501'; END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
    AND actor.status='ACTIVE' AND actor.security_version=p_security_version) THEN RETURN; END IF;
  PERFORM set_config('app.actor_user_id',p_actor_user_id,true);
  RETURN QUERY SELECT history.answer_id,history.created_at,history.expires_at,
    coalesce(jsonb_agg(jsonb_build_object(
      'sourceId',coalesce(citation.source_id,'src_local_' || substr(encode(public.digest(citation.document_id,'sha256'),'hex'),1,16)),
      'title',left(coalesce(citation.title,citation.sanitized_display_name),160),
      'classification',CASE WHEN citation.citation_kind='LOCAL_DOCUMENT' THEN 'INTERNAL_PAPER' ELSE 'OFFICIAL' END,
      'summary',left('저장된 근거: ' || coalesce(citation.title,citation.sanitized_display_name),512)
    ) ORDER BY citation.ordinal) FILTER (WHERE citation.answer_id IS NOT NULL),'[]'::jsonb)
  FROM public.rag_v2_answer_history history LEFT JOIN public.rag_v2_answer_citations citation
    ON citation.answer_id=history.answer_id AND citation.owner_user_id=history.owner_user_id
  WHERE history.answer_id=p_answer_id AND history.owner_user_id=p_actor_user_id
  GROUP BY history.answer_id,history.created_at,history.expires_at;
END
$read_dashboard_rag_sources$;

CREATE FUNCTION public.list_artifact_ingest_status(p_actor_user_id text,p_security_version bigint)
RETURNS TABLE(artifact_id text,file_name text,producer text,run_id text,file_hash text,schema_version text,
  status text,last_ingested_at timestamptz,duplicate boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_artifact_ingest_status$
DECLARE actor_role text; actor_status text; actor_version bigint;
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'artifact status read denied' USING ERRCODE='42501'; END IF;
  SELECT role,users.status,security_version INTO actor_role,actor_status,actor_version
    FROM public.users WHERE user_id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_role<>'ADMIN' OR actor_status<>'ACTIVE' OR actor_version<>p_security_version THEN RETURN; END IF;
  INSERT INTO public.artifact_ingest_admin_read_audit(actor_user_id) VALUES (p_actor_user_id);
  RETURN QUERY SELECT item.artifact_id,item.file_name,item.producer,item.run_id,item.file_hash,item.schema_version,
    item.status,item.last_ingested_at,item.duplicate
  FROM public.artifact_ingest_projection item ORDER BY item.last_ingested_at DESC,item.artifact_id LIMIT 100;
END
$list_artifact_ingest_status$;

ALTER FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.materialize_dashboard_artifact_receipt() OWNER TO flyway;
ALTER FUNCTION public.read_dashboard_artifact_view(text,bigint,text,text) OWNER TO flyway;
ALTER FUNCTION public.read_dashboard_risk_view(text,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.read_dashboard_rag_sources(text,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.list_artifact_ingest_status(text,bigint) OWNER TO flyway;

REVOKE ALL ON TABLE public.dashboard_artifact_staging,public.dashboard_artifact_views,
  public.artifact_ingest_projection,public.artifact_ingest_admin_read_audit FROM PUBLIC,decision_app,decision_worker;
GRANT SELECT,INSERT ON TABLE public.dashboard_artifact_staging,public.dashboard_artifact_views,
  public.artifact_ingest_projection,public.artifact_ingest_admin_read_audit TO flyway;
REVOKE ALL ON FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz),
  public.materialize_dashboard_artifact_receipt(),public.read_dashboard_artifact_view(text,bigint,text,text),
  public.read_dashboard_risk_view(text,bigint,text),public.read_dashboard_rag_sources(text,bigint,text),
  public.list_artifact_ingest_status(text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz),
  public.read_dashboard_artifact_view(text,bigint,text,text),public.read_dashboard_risk_view(text,bigint,text),
  public.read_dashboard_rag_sources(text,bigint,text),public.list_artifact_ingest_status(text,bigint) TO decision_app;
