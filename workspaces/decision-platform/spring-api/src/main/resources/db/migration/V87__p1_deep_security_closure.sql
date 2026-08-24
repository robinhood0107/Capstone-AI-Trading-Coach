-- P1 deep-security closure. B86 remains byte-stable; all trust-role changes are forward-only.

-- Capabilities live for at most 30 seconds, so no pre-V87 capability may cross this boundary.
DELETE FROM public.actor_request_capability;

ALTER TABLE public.actor_request_capability
  ADD COLUMN operation text NOT NULL,
  ADD COLUMN target_kind text NOT NULL,
  ADD COLUMN target_id text NOT NULL,
  ADD COLUMN payload_hash text NOT NULL,
  ADD COLUMN request_id text NOT NULL,
  ADD COLUMN transaction_id text NOT NULL,
  ADD COLUMN nonce text NOT NULL,
  ADD COLUMN signature text NOT NULL,
  ADD CONSTRAINT actor_request_capability_operation_format
    CHECK (operation ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  ADD CONSTRAINT actor_request_capability_target_kind_format
    CHECK (target_kind ~ '^[A-Z][A-Z0-9_]{2,31}$'),
  ADD CONSTRAINT actor_request_capability_target_id_format
    CHECK (char_length(target_id) BETWEEN 1 AND 160),
  ADD CONSTRAINT actor_request_capability_payload_hash_format
    CHECK (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
  ADD CONSTRAINT actor_request_capability_request_id_format
    CHECK (request_id ~ '^req_[0-9a-f]{32}$'),
  ADD CONSTRAINT actor_request_capability_transaction_id_format
    CHECK (transaction_id ~ '^txn_[0-9a-f]{32}$'),
  ADD CONSTRAINT actor_request_capability_nonce_format
    CHECK (nonce ~ '^[0-9a-f]{32}$'),
  ADD CONSTRAINT actor_request_capability_signature_format
    CHECK (signature ~ '^ed25519:[A-Za-z0-9_-]{86}$');

CREATE UNIQUE INDEX actor_request_capability_nonce_idx
  ON public.actor_request_capability(nonce);
CREATE UNIQUE INDEX actor_request_capability_request_transaction_idx
  ON public.actor_request_capability(request_id, transaction_id);

CREATE OR REPLACE FUNCTION public.issue_actor_request_capability(p_actor_user_id text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $issue_actor_request_capability$
BEGIN
  RAISE EXCEPTION 'legacy actor capability issuance is retired' USING ERRCODE='42501';
END
$issue_actor_request_capability$;

CREATE FUNCTION public.register_actor_request_capability_v2(
  p_token text,
  p_actor_user_id text,
  p_actor_role text,
  p_actor_security_version bigint,
  p_operation text,
  p_target_kind text,
  p_target_id text,
  p_payload_hash text,
  p_request_id text,
  p_transaction_id text,
  p_nonce text,
  p_issued_at timestamptz,
  p_expires_at timestamptz,
  p_signature text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $register_actor_request_capability_v2$
DECLARE actor record; changed integer;
BEGIN
  IF session_user <> 'decision_identity'
     OR char_length(p_token) NOT BETWEEN 120 AND 1024
     OR p_token !~ '^cap2_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}$'
     OR p_actor_role NOT IN ('USER','ADMIN')
     OR p_actor_security_version < 1
     OR p_operation !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_target_kind !~ '^[A-Z][A-Z0-9_]{2,31}$'
     OR char_length(p_target_id) NOT BETWEEN 1 AND 160
     OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_request_id !~ '^req_[0-9a-f]{32}$'
     OR p_transaction_id !~ '^txn_[0-9a-f]{32}$'
     OR p_nonce !~ '^[0-9a-f]{32}$'
     OR p_signature !~ '^ed25519:[A-Za-z0-9_-]{86}$'
     OR p_issued_at < statement_timestamp() - interval '5 seconds'
     OR p_issued_at > statement_timestamp() + interval '1 second'
     OR p_expires_at <= p_issued_at
     OR p_expires_at > p_issued_at + interval '30 seconds' THEN
    RAISE EXCEPTION 'actor capability registration denied' USING ERRCODE='42501';
  END IF;
  SELECT role,status,security_version INTO actor
  FROM public.users WHERE user_id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor.status <> 'ACTIVE' OR actor.role <> p_actor_role
     OR actor.security_version <> p_actor_security_version THEN
    RETURN false;
  END IF;
  DELETE FROM public.actor_request_capability expired
  WHERE expired.expires_at <= statement_timestamp();
  INSERT INTO public.actor_request_capability(
    token_hash,actor_user_id,actor_role,actor_security_version,issued_at,expires_at,
    operation,target_kind,target_id,payload_hash,request_id,transaction_id,nonce,signature
  ) VALUES (
    'sha256:' || encode(public.digest(p_token,'sha256'),'hex'),
    p_actor_user_id,p_actor_role,p_actor_security_version,p_issued_at,p_expires_at,
    p_operation,p_target_kind,p_target_id,p_payload_hash,p_request_id,p_transaction_id,p_nonce,p_signature
  );
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$register_actor_request_capability_v2$;

CREATE FUNCTION public.consume_actor_request_capability_v2(
  p_token text,
  p_actor_user_id text,
  p_security_version bigint,
  p_required_role text,
  p_operation text,
  p_target_kind text,
  p_target_id text,
  p_payload_hash text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_actor_request_capability_v2$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_app'
     OR char_length(p_token) NOT BETWEEN 120 AND 1024
     OR p_token !~ '^cap2_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}$'
     OR p_required_role NOT IN ('USER','ADMIN')
     OR p_operation !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_target_kind !~ '^[A-Z][A-Z0-9_]{2,31}$'
     OR char_length(p_target_id) NOT BETWEEN 1 AND 160
     OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'actor capability denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.actor_request_capability capability
  SET consumed_at=statement_timestamp()
  WHERE capability.token_hash='sha256:' || encode(public.digest(p_token,'sha256'),'hex')
    AND capability.actor_user_id=p_actor_user_id
    AND capability.actor_security_version=p_security_version
    AND capability.actor_role=p_required_role
    AND capability.operation=p_operation
    AND capability.target_kind=p_target_kind
    AND capability.target_id=p_target_id
    AND capability.payload_hash=p_payload_hash
    AND capability.consumed_at IS NULL
    AND capability.expires_at>statement_timestamp()
    AND EXISTS (
      SELECT 1 FROM public.users actor
      WHERE actor.user_id=capability.actor_user_id
        AND actor.status='ACTIVE'
        AND actor.role=capability.actor_role
        AND actor.security_version=capability.actor_security_version
    );
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$consume_actor_request_capability_v2$;

CREATE OR REPLACE FUNCTION public.consume_actor_request_capability(
  p_token text,p_actor_user_id text,p_security_version bigint,p_required_role text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_actor_request_capability$
BEGIN
  RAISE EXCEPTION 'legacy actor capability consumption is retired' USING ERRCODE='42501';
END
$consume_actor_request_capability$;

ALTER FUNCTION public.register_actor_request_capability_v2(
  text,text,text,bigint,text,text,text,text,text,text,text,timestamptz,timestamptz,text
) OWNER TO flyway;
ALTER FUNCTION public.consume_actor_request_capability_v2(
  text,text,bigint,text,text,text,text,text
) OWNER TO flyway;

REVOKE ALL ON FUNCTION public.issue_actor_request_capability(text),
  public.consume_actor_request_capability(text,text,bigint,text),
  public.register_actor_request_capability_v2(text,text,text,bigint,text,text,text,text,text,text,text,timestamptz,timestamptz,text),
  public.consume_actor_request_capability_v2(text,text,bigint,text,text,text,text,text)
FROM PUBLIC,decision_app,decision_worker,decision_replay;

-- Ed25519 verification is performed with the public key before this identity-only DB registration.
CREATE FUNCTION public.read_actor_capability_subject(p_actor_user_id text)
RETURNS TABLE(actor_role text,actor_security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_actor_capability_subject$
BEGIN
  IF session_user <> 'decision_identity'
     OR p_actor_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$' THEN
    RAISE EXCEPTION 'actor capability subject denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY SELECT actor.role,actor.security_version
  FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
    AND actor.role IN ('USER','ADMIN');
END
$read_actor_capability_subject$;
ALTER FUNCTION public.read_actor_capability_subject(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_actor_capability_subject(text) FROM PUBLIC,decision_app;

DO $p1_v87_roles$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_identity') THEN
    GRANT EXECUTE ON FUNCTION public.register_actor_request_capability_v2(
      text,text,text,bigint,text,text,text,text,text,text,text,timestamptz,timestamptz,text
    ) TO decision_identity;
    GRANT EXECUTE ON FUNCTION public.read_actor_capability_subject(text) TO decision_identity;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_outbox_publisher') THEN
    GRANT USAGE ON SCHEMA public TO decision_outbox_publisher;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_poison_recorder') THEN
    GRANT USAGE ON SCHEMA public TO decision_poison_recorder;
  END IF;
END
$p1_v87_roles$;

REVOKE EXECUTE ON FUNCTION public.activate_signal_v2_production_pointer(text) FROM decision_app;
REVOKE ALL PRIVILEGES ON TABLE public.actor_request_capability
FROM PUBLIC,decision_app,decision_worker,decision_replay;
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.actor_request_capability TO flyway;

CREATE OR REPLACE FUNCTION public.read_async_job_status_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_job_id text
)
RETURNS TABLE(job_id text,job_type text,status text,requested_at timestamptz,started_at timestamptz,
  completed_at timestamptz,source_id text,artifact_id text,result_ref text,error_code text,error_class text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_async_job_status_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,'ADMIN','READ_ASYNC_JOB','ASYNC_JOB',p_job_id,
    'sha256:' || encode(public.digest(p_job_id,'sha256'),'hex')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_async_job_status(
    p_actor_user_id,p_security_version,p_job_id
  );
END
$read_async_job_status_authorized$;

ALTER FUNCTION public.read_async_job_status_authorized(text,text,bigint,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_async_job_status_authorized(text,text,bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_async_job_status_authorized(text,text,bigint,text) TO decision_app;

CREATE FUNCTION public.actor_capability_payload_hash(VARIADIC p_values text[])
RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog
AS $actor_capability_payload_hash$
  SELECT 'sha256:' || encode(public.digest(coalesce(string_agg(
    CASE WHEN item.value IS NULL THEN E'-:\n'
         ELSE octet_length(item.value)::text || ':' || item.value || E'\n' END,
    '' ORDER BY item.ordinality
  ),''),'sha256'),'hex')
  FROM unnest(p_values) WITH ORDINALITY AS item(value,ordinality)
$actor_capability_payload_hash$;
ALTER FUNCTION public.actor_capability_payload_hash(text[]) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.actor_capability_payload_hash(text[]) FROM PUBLIC;

CREATE FUNCTION public.create_async_request_authorized(
  p_capability text,p_event_id text,p_event_type text,p_partition_key text,
  p_job_id text,p_job_type text,p_requested_by text,p_payload text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $create_async_request_authorized_v87$
DECLARE actor record; payload_json jsonb;
BEGIN
  SELECT subject.role,subject.security_version INTO actor
  FROM public.users subject WHERE subject.user_id=p_requested_by AND subject.status='ACTIVE';
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_requested_by,actor.security_version,actor.role,
    'CREATE_ASYNC_REQUEST','ASYNC_JOB',p_job_id,
    public.actor_capability_payload_hash(
      p_event_id,p_event_type,p_partition_key,p_job_id,p_job_type,p_requested_by,p_payload
    )
  ) THEN
    RAISE EXCEPTION 'async actor capability denied' USING ERRCODE='42501';
  END IF;
  IF octet_length(p_payload)>32768 THEN
    RAISE EXCEPTION 'invalid async payload' USING ERRCODE='22023';
  END IF;
  payload_json := p_payload::jsonb;
  IF NOT public.create_async_job(p_job_id,p_job_type,p_requested_by,payload_json)
     OR NOT public.append_async_request_outbox(
       p_event_id,p_event_type,p_job_id,p_partition_key,payload_json
     ) THEN
    RAISE EXCEPTION 'async request creation conflict' USING ERRCODE='40001';
  END IF;
  RETURN true;
END
$create_async_request_authorized_v87$;

CREATE OR REPLACE FUNCTION public.list_async_job_status_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_status text,p_job_type text,
  p_before_created_at timestamptz,p_before_job_id text,p_limit integer
)
RETURNS TABLE(job_id text,job_type text,status text,requested_at timestamptz,started_at timestamptz,
  completed_at timestamptz,source_id text,artifact_id text,result_ref text,error_code text,error_class text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_async_job_status_authorized_v87$
BEGIN
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,'ADMIN',
    'LIST_ASYNC_JOBS','ASYNC_JOB_LIST','async-jobs',
    public.actor_capability_payload_hash(
      p_status,p_job_type,
      CASE WHEN p_before_created_at IS NULL THEN NULL
           ELSE (extract(epoch FROM p_before_created_at)*1000)::bigint::text END,
      p_before_job_id,p_limit::text
    )
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.list_async_job_status(
    p_actor_user_id,p_security_version,p_status,p_job_type,p_before_created_at,p_before_job_id,p_limit
  );
END
$list_async_job_status_authorized_v87$;

CREATE OR REPLACE FUNCTION public.read_stream_metric_status_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint
)
RETURNS TABLE(
  last_updated_at timestamptz,pipeline_health text,stale_signal_ratio numeric,
  allow_count bigint,warn_count bigint,hold_count bigint,block_count bigint,
  failed_job_count bigint,dlq_event_count bigint,
  decision_status text,decision_observed_at timestamptz,
  signal_status text,signal_observed_at timestamptz,
  failed_status text,failed_observed_at timestamptz,
  dlq_status text,dlq_observed_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_stream_metric_status_authorized_v87$
BEGIN
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,'ADMIN',
    'READ_STREAM_METRICS','STREAM_METRICS','stream-metrics',
    public.actor_capability_payload_hash(VARIADIC ARRAY[]::text[])
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_stream_metric_status(p_actor_user_id,p_security_version);
END
$read_stream_metric_status_authorized_v87$;

ALTER FUNCTION public.create_async_request_authorized(text,text,text,text,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer) OWNER TO flyway;
ALTER FUNCTION public.read_stream_metric_status_authorized(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION
  public.create_async_request_authorized(text,text,text,text,text,text,text,jsonb),
  public.create_async_request_authorized(text,text,text,text,text,text,text,text),
  public.list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer),
  public.read_stream_metric_status_authorized(text,text,bigint)
FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION
  public.create_async_request_authorized(text,text,text,text,text,text,text,text),
  public.list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer),
  public.read_stream_metric_status_authorized(text,text,bigint)
TO decision_app;

CREATE OR REPLACE FUNCTION public.read_dashboard_artifact_view_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_view_kind text,p_run_id text
)
RETURNS TABLE(projection_json jsonb,evidence_mode text,fixture_class text,as_of timestamptz,fresh_until timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_artifact_view_authorized_v87$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE' AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_ARTIFACT','DASHBOARD_ARTIFACT',p_run_id,
    public.actor_capability_payload_hash(p_view_kind,p_run_id)
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_artifact_view(
    p_actor_user_id,p_security_version,p_view_kind,p_run_id
  );
END
$read_dashboard_artifact_view_authorized_v87$;

CREATE OR REPLACE FUNCTION public.read_dashboard_risk_view_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_decision_id text
)
RETURNS TABLE(decision_id text,outcome text,evaluation_as_of timestamptz,valid_until timestamptz,
  reasons jsonb,principles jsonb,risk_items jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_risk_view_authorized_v87$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE' AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_RISK','RISK_DECISION',p_decision_id,
    'sha256:' || encode(public.digest(p_decision_id,'sha256'),'hex')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_risk_view(
    p_actor_user_id,p_security_version,p_decision_id
  );
END
$read_dashboard_risk_view_authorized_v87$;

CREATE OR REPLACE FUNCTION public.read_dashboard_rag_sources_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_answer_id text
)
RETURNS TABLE(answer_id text,created_at timestamptz,expires_at timestamptz,sources jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_rag_sources_authorized_v87$
DECLARE actor_role text;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE' AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_RAG','RAG_ANSWER',p_answer_id,
    'sha256:' || encode(public.digest(p_answer_id,'sha256'),'hex')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_rag_sources(
    p_actor_user_id,p_security_version,p_answer_id
  );
END
$read_dashboard_rag_sources_authorized_v87$;

CREATE OR REPLACE FUNCTION public.list_artifact_ingest_status_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint
)
RETURNS TABLE(artifact_id text,file_name text,producer text,run_id text,file_hash text,schema_version text,
  status text,last_ingested_at timestamptz,duplicate boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_artifact_ingest_status_authorized_v87$
BEGIN
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,'ADMIN',
    'LIST_ARTIFACT_STATUS','ARTIFACT_STATUS_LIST','artifact-statuses',
    public.actor_capability_payload_hash(VARIADIC ARRAY[]::text[])
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.list_artifact_ingest_status(p_actor_user_id,p_security_version);
END
$list_artifact_ingest_status_authorized_v87$;

ALTER FUNCTION public.read_dashboard_artifact_view_authorized(text,text,bigint,text,text) OWNER TO flyway;
ALTER FUNCTION public.read_dashboard_risk_view_authorized(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.read_dashboard_rag_sources_authorized(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.list_artifact_ingest_status_authorized(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION
  public.read_dashboard_artifact_view_authorized(text,text,bigint,text,text),
  public.read_dashboard_risk_view_authorized(text,text,bigint,text),
  public.read_dashboard_rag_sources_authorized(text,text,bigint,text),
  public.list_artifact_ingest_status_authorized(text,text,bigint)
FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION
  public.read_dashboard_artifact_view_authorized(text,text,bigint,text,text),
  public.read_dashboard_risk_view_authorized(text,text,bigint,text),
  public.read_dashboard_rag_sources_authorized(text,text,bigint,text),
  public.list_artifact_ingest_status_authorized(text,text,bigint)
TO decision_app;

-- Owner operations accept the current active USER or ADMIN role, while the signed claim remains role-bound.
CREATE FUNCTION public.consume_current_actor_capability_v2(
  p_capability text,p_actor_user_id text,p_operation text,p_target_kind text,
  p_target_id text,p_payload_hash text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_current_actor_capability_v2$
DECLARE actor record;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'actor capability role denied' USING ERRCODE='42501';
  END IF;
  SELECT subject.role,subject.security_version INTO actor
  FROM public.users subject
  WHERE subject.user_id=p_actor_user_id AND subject.status='ACTIVE'
    AND subject.role IN ('USER','ADMIN');
  IF NOT FOUND THEN RETURN false; END IF;
  RETURN public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,actor.security_version,actor.role,
    p_operation,p_target_kind,p_target_id,p_payload_hash
  );
END
$consume_current_actor_capability_v2$;
ALTER FUNCTION public.consume_current_actor_capability_v2(text,text,text,text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.consume_current_actor_capability_v2(text,text,text,text,text,text)
FROM PUBLIC,decision_app;

CREATE OR REPLACE FUNCTION public.insert_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_preset_id text,p_title text,
  p_mode text,p_status text,p_version integer,p_created_at timestamptz,p_updated_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_authorized_v87$
DECLARE changed integer;
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'INSERT_PRINCIPLE','PRINCIPLE',p_principle_id,
    public.actor_capability_payload_hash(
      p_actor_user_id,p_principle_id,p_preset_id,p_title,p_mode,p_status,p_version::text
    )
  ) THEN RAISE EXCEPTION 'principle actor capability denied' USING ERRCODE='42501'; END IF;
  IF p_principle_id !~ '^prc_[0-9a-f]{32}$' OR p_preset_id !~ '^[a-z][a-z0-9_-]{2,63}$'
     OR char_length(p_title) NOT BETWEEN 1 AND 120 OR p_mode NOT IN ('GUIDE','STRICT')
     OR p_status NOT IN ('ACTIVE','ARCHIVED') OR p_version<>1 OR p_created_at<>p_updated_at
     OR NOT EXISTS (SELECT 1 FROM public.principle_presets preset
       WHERE preset.preset_id=p_preset_id AND preset.is_active) THEN
    RAISE EXCEPTION 'invalid principle insert' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.principles(principle_id,user_id,preset_id,title,mode,status,current_version,created_at,updated_at)
  VALUES (p_principle_id,p_actor_user_id,p_preset_id,p_title,p_mode,p_status,p_version,p_created_at,p_updated_at);
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$insert_principle_authorized_v87$;

CREATE FUNCTION public.insert_principle_version_authorized_v2(
  p_capability text,p_actor_user_id text,p_version_id text,p_principle_id text,p_version integer,
  p_preset_id text,p_title text,p_mode text,p_status text,p_rules_json text,
  p_changed_fields_json text,p_created_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_version_authorized_v2$
DECLARE changed integer; rules_json jsonb; changed_fields text[];
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'INSERT_PRINCIPLE_VERSION','PRINCIPLE_VERSION',p_version_id,
    public.actor_capability_payload_hash(
      p_actor_user_id,p_version_id,p_principle_id,p_version::text,p_preset_id,p_title,
      p_mode,p_status,p_rules_json,p_changed_fields_json
    )
  ) THEN RAISE EXCEPTION 'principle version capability denied' USING ERRCODE='42501'; END IF;
  IF octet_length(p_rules_json)>65536 OR octet_length(p_changed_fields_json)>2048 THEN
    RAISE EXCEPTION 'invalid principle version payload' USING ERRCODE='22023';
  END IF;
  BEGIN
    rules_json:=p_rules_json::jsonb;
    SELECT array_agg(value ORDER BY ordinality) INTO changed_fields
    FROM jsonb_array_elements_text(p_changed_fields_json::jsonb) WITH ORDINALITY;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'invalid principle version payload' USING ERRCODE='22023';
  END;
  IF p_version_id !~ '^pvr_[0-9a-f]{32}$' OR p_version<1
     OR NOT EXISTS (SELECT 1 FROM public.principles item
       WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id)
     OR changed_fields IS NULL OR cardinality(changed_fields) NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'invalid principle version insert' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.principle_versions(
    principle_version_id,principle_id,version,preset_id,title,mode,status,
    rules_json,changed_fields,created_by,created_at
  ) VALUES (
    p_version_id,p_principle_id,p_version,p_preset_id,p_title,p_mode,p_status,
    rules_json,changed_fields,p_actor_user_id,p_created_at
  );
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$insert_principle_version_authorized_v2$;

CREATE FUNCTION public.insert_principle_audit_authorized_v2(
  p_capability text,p_actor_user_id text,p_request_id text,p_action text,
  p_principle_id text,p_new_version integer,p_changed_fields_json text,p_created_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_audit_authorized_v2$
DECLARE actor_role text; changed integer; changed_fields text[];
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'INSERT_PRINCIPLE_AUDIT','PRINCIPLE',p_principle_id,
    public.actor_capability_payload_hash(
      p_actor_user_id,p_request_id,p_action,p_principle_id,p_new_version::text,p_changed_fields_json
    )
  ) THEN RAISE EXCEPTION 'principle audit capability denied' USING ERRCODE='42501'; END IF;
  IF octet_length(p_changed_fields_json)>2048 THEN
    RAISE EXCEPTION 'invalid principle audit payload' USING ERRCODE='22023';
  END IF;
  BEGIN
    SELECT array_agg(value ORDER BY ordinality) INTO changed_fields
    FROM jsonb_array_elements_text(p_changed_fields_json::jsonb) WITH ORDINALITY;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'invalid principle audit payload' USING ERRCODE='22023';
  END;
  IF p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_action NOT IN ('PRINCIPLE_CREATED','PRINCIPLE_UPDATED','PRINCIPLE_ARCHIVED','PRINCIPLE_REACTIVATED')
     OR p_new_version<1 OR changed_fields IS NULL OR cardinality(changed_fields) NOT BETWEEN 1 AND 5
     OR NOT EXISTS (SELECT 1 FROM public.principles item
       WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id) THEN
    RAISE EXCEPTION 'invalid principle audit insert' USING ERRCODE='22023';
  END IF;
  SELECT actor.role INTO actor_role FROM public.users actor WHERE actor.user_id=p_actor_user_id;
  INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at)
  VALUES ('aud_'||replace(gen_random_uuid()::text,'-',''),p_actor_user_id,actor_role,p_action,'PRINCIPLE',
    p_principle_id,p_request_id,jsonb_build_object('principleId',p_principle_id,
      'newVersion',p_new_version,'changedFields',to_jsonb(changed_fields)),p_created_at);
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$insert_principle_audit_authorized_v2$;

CREATE OR REPLACE FUNCTION public.read_owned_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text
)
RETURNS TABLE(principle_id text,user_id text,preset_id text,title text,mode text,status text,
  current_version integer,created_at timestamptz,updated_at timestamptz,rules_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_owned_principle_authorized_v87$
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'READ_PRINCIPLE','PRINCIPLE',p_principle_id,
    'sha256:'||encode(public.digest(p_principle_id,'sha256'),'hex')
  ) THEN RETURN; END IF;
  RETURN QUERY SELECT item.principle_id,item.user_id,item.preset_id,item.title,item.mode,item.status,
    item.current_version,item.created_at,item.updated_at,version.rules_json::text
  FROM public.principles item JOIN public.principle_versions version
    ON version.principle_id=item.principle_id AND version.version=item.current_version
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id;
END
$read_owned_principle_authorized_v87$;

CREATE OR REPLACE FUNCTION public.list_owned_principles_authorized(
  p_capability text,p_actor_user_id text,p_limit integer,p_sort text,
  p_after_updated_at timestamptz,p_after_principle_id text
)
RETURNS TABLE(principle_id text,preset_id text,title text,mode text,status text,
  current_version integer,created_at timestamptz,updated_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_owned_principles_authorized_v87$
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'LIST_PRINCIPLES','PRINCIPLE_LIST','principles',
    public.actor_capability_payload_hash(
      p_actor_user_id,p_limit::text,p_sort,
      CASE WHEN p_after_updated_at IS NULL THEN NULL
           ELSE floor(extract(epoch FROM p_after_updated_at)*1000)::bigint::text END,
      p_after_principle_id
    )
  ) THEN RETURN; END IF;
  IF p_limit NOT BETWEEN 1 AND 101 OR p_sort NOT IN ('UPDATED_AT_ASC','UPDATED_AT_DESC')
     OR ((p_after_updated_at IS NULL)<>(p_after_principle_id IS NULL)) THEN
    RAISE EXCEPTION 'invalid principle list query' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT item.principle_id,item.preset_id,item.title,item.mode,item.status,
    item.current_version,item.created_at,item.updated_at
  FROM public.principles item
  WHERE item.user_id=p_actor_user_id AND (
    p_after_updated_at IS NULL OR
    (p_sort='UPDATED_AT_ASC' AND (item.updated_at,item.principle_id)>(p_after_updated_at,p_after_principle_id)) OR
    (p_sort='UPDATED_AT_DESC' AND (item.updated_at,item.principle_id)<(p_after_updated_at,p_after_principle_id))
  )
  ORDER BY
    CASE WHEN p_sort='UPDATED_AT_ASC' THEN item.updated_at END ASC,
    CASE WHEN p_sort='UPDATED_AT_ASC' THEN item.principle_id END ASC,
    CASE WHEN p_sort='UPDATED_AT_DESC' THEN item.updated_at END DESC,
    CASE WHEN p_sort='UPDATED_AT_DESC' THEN item.principle_id END DESC
  LIMIT p_limit;
END
$list_owned_principles_authorized_v87$;

CREATE OR REPLACE FUNCTION public.update_owned_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_expected_version integer,
  p_title text,p_mode text,p_status text,p_updated_at timestamptz
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $update_owned_principle_authorized_v87$
DECLARE next_version integer;
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'UPDATE_PRINCIPLE','PRINCIPLE',p_principle_id,
    public.actor_capability_payload_hash(
      p_actor_user_id,p_principle_id,p_expected_version::text,p_title,p_mode,p_status
    )
  ) THEN RAISE EXCEPTION 'principle update capability denied' USING ERRCODE='42501'; END IF;
  UPDATE public.principles item SET title=p_title,mode=p_mode,status=p_status,
    current_version=item.current_version+1,updated_at=p_updated_at
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id
    AND item.current_version=p_expected_version AND item.current_version<2147483647
  RETURNING item.current_version INTO next_version;
  RETURN next_version;
END
$update_owned_principle_authorized_v87$;

CREATE OR REPLACE FUNCTION public.list_owned_principle_versions_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_limit integer,
  p_sort text,p_after_version integer
)
RETURNS TABLE(principle_id text,version integer,preset_id text,title text,mode text,status text,
  rules_json text,changed_fields text[],created_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_owned_principle_versions_authorized_v87$
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'LIST_PRINCIPLE_VERSIONS','PRINCIPLE',p_principle_id,
    public.actor_capability_payload_hash(
      p_actor_user_id,p_principle_id,p_limit::text,p_sort,p_after_version::text
    )
  ) THEN RETURN; END IF;
  IF p_limit NOT BETWEEN 1 AND 101 OR p_sort NOT IN ('VERSION_ASC','VERSION_DESC') THEN
    RAISE EXCEPTION 'invalid principle history query' USING ERRCODE='22023';
  END IF;
  RETURN QUERY SELECT version.principle_id,version.version,version.preset_id,version.title,
    version.mode,version.status,version.rules_json::text,version.changed_fields,version.created_at
  FROM public.principle_versions version JOIN public.principles item
    ON item.principle_id=version.principle_id AND item.user_id=p_actor_user_id
  WHERE version.principle_id=p_principle_id AND (
    p_after_version IS NULL OR (p_sort='VERSION_ASC' AND version.version>p_after_version)
    OR (p_sort='VERSION_DESC' AND version.version<p_after_version)
  )
  ORDER BY CASE WHEN p_sort='VERSION_ASC' THEN version.version END ASC,
    CASE WHEN p_sort='VERSION_DESC' THEN version.version END DESC
  LIMIT p_limit;
END
$list_owned_principle_versions_authorized_v87$;

CREATE OR REPLACE FUNCTION public.read_active_owned_principle_snapshot_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text
)
RETURNS TABLE(principle_id text,principle_version_id text,version integer,mode text,status text,rules_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_active_owned_principle_snapshot_authorized_v87$
BEGIN
  IF NOT public.consume_current_actor_capability_v2(
    p_capability,p_actor_user_id,'READ_ACTIVE_PRINCIPLE','PRINCIPLE',p_principle_id,
    'sha256:'||encode(public.digest(p_principle_id,'sha256'),'hex')
  ) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.principle_id,version_item.principle_version_id,version_item.version,
    version_item.mode,version_item.status,version_item.rules_json::text
  FROM public.principles item
  JOIN public.principle_versions version_item
    ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id
    AND item.status='ACTIVE' AND version_item.status='ACTIVE';
END
$read_active_owned_principle_snapshot_authorized_v87$;

ALTER FUNCTION public.insert_principle_authorized(text,text,text,text,text,text,text,integer,timestamptz,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.insert_principle_version_authorized_v2(text,text,text,text,integer,text,text,text,text,text,text,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.insert_principle_audit_authorized_v2(text,text,text,text,text,integer,text,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.read_owned_principle_authorized(text,text,text) OWNER TO flyway;
ALTER FUNCTION public.list_owned_principles_authorized(text,text,integer,text,timestamptz,text) OWNER TO flyway;
ALTER FUNCTION public.update_owned_principle_authorized(text,text,text,integer,text,text,text,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.list_owned_principle_versions_authorized(text,text,text,integer,text,integer) OWNER TO flyway;
ALTER FUNCTION public.read_active_owned_principle_snapshot_authorized(text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION
  public.insert_principle_version_authorized(text,text,text,text,integer,text,text,text,text,jsonb,text[],timestamptz),
  public.insert_principle_audit_authorized(text,text,text,text,text,integer,text[],timestamptz)
FROM decision_app;
GRANT EXECUTE ON FUNCTION
  public.insert_principle_version_authorized_v2(text,text,text,text,integer,text,text,text,text,text,text,timestamptz),
  public.insert_principle_audit_authorized_v2(text,text,text,text,text,integer,text,timestamptz)
TO decision_app;

CREATE OR REPLACE FUNCTION public.transition_kill_switch_authorized(
  p_capability text,p_actor_user_id text,p_actor_security_version bigint,
  p_requested_active boolean,p_observed_generation bigint,p_request_id text
)
RETURNS TABLE(
  active boolean,reason_class text,changed_at timestamptz,changed boolean,
  previous_active boolean,generation bigint,invalidated_decision_count integer
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $transition_kill_switch_authorized_v87$
#variable_conflict use_column
DECLARE
  actor_role text;
  current_gate public.risk_kill_switch%ROWTYPE;
  next_reason text;
  transition_time timestamptz;
  invalidated_count integer := 0;
BEGIN
  IF session_user <> 'decision_app'
     OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_observed_generation <= 0 THEN
    RAISE EXCEPTION 'kill switch capability request denied' USING ERRCODE='42501';
  END IF;
  SELECT actor.role INTO actor_role
  FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
    AND actor.security_version=p_actor_security_version AND actor.role IN ('USER','ADMIN')
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch current actor denied' USING ERRCODE='42501';
  END IF;
  IF NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_actor_security_version,
    CASE WHEN p_requested_active THEN actor_role ELSE 'ADMIN' END,
    'TRANSITION_KILL_SWITCH','KILL_SWITCH','GLOBAL',
    public.actor_capability_payload_hash(
      p_actor_user_id,p_actor_security_version::text,p_requested_active::text,
      p_observed_generation::text,p_request_id
    )
  ) THEN
    RAISE EXCEPTION 'kill switch actor capability denied' USING ERRCODE='42501';
  END IF;

  SELECT * INTO current_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE='P5501';
  END IF;
  IF current_gate.generation<>p_observed_generation THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE='40001';
  END IF;
  IF current_gate.active=p_requested_active THEN
    RETURN QUERY SELECT current_gate.active,current_gate.reason_class,current_gate.changed_at,false,
      current_gate.active,current_gate.generation,0;
    RETURN;
  END IF;

  next_reason:=CASE
    WHEN NOT p_requested_active THEN 'ADMIN_RESUME'
    WHEN actor_role='ADMIN' THEN 'OPERATOR_MANUAL_STOP'
    ELSE 'USER_MANUAL_STOP'
  END;
  transition_time:=greatest(statement_timestamp(),current_gate.changed_at);
  UPDATE public.risk_kill_switch gate
  SET active=p_requested_active,
      reason_class=next_reason,
      generation=current_gate.generation+1,
      changed_by=p_actor_user_id,
      changed_by_role=actor_role,
      changed_at=transition_time,
      request_id=p_request_id
  WHERE gate.kill_switch_id='GLOBAL' AND gate.generation=p_observed_generation;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE='40001';
  END IF;
  IF p_requested_active THEN
    invalidated_count:=public.invalidate_unused_decisions_for_kill_switch(
      current_gate.generation+1,transition_time,p_request_id
    );
  END IF;

  INSERT INTO public.risk_kill_switch_transitions(
    transition_id,generation,previous_active,next_active,reason_class,changed_by,
    changed_by_role,changed_at,request_id,invalidated_decision_count
  ) VALUES (
    'kst_'||replace(gen_random_uuid()::text,'-',''),current_gate.generation+1,
    current_gate.active,p_requested_active,next_reason,p_actor_user_id,actor_role,
    transition_time,p_request_id,invalidated_count
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),p_actor_user_id,actor_role,
    'KILL_SWITCH_CHANGED','KILL_SWITCH','GLOBAL',p_request_id,
    jsonb_build_object(
      'generation',current_gate.generation+1,
      'previousActive',current_gate.active,
      'nextActive',p_requested_active,
      'reasonClass',next_reason,
      'changedBy',p_actor_user_id,
      'changedByRole',actor_role,
      'correlationId',p_request_id,
      'invalidatedDecisionCount',invalidated_count
    ),transition_time
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'kill-switch.changed','KILL_SWITCH',
    'GLOBAL','GLOBAL',jsonb_build_object('active',p_requested_active,'changedAt',transition_time::text),
    '1.0.0','PENDING',0,transition_time,transition_time
  );
  RETURN QUERY SELECT p_requested_active,next_reason,transition_time,true,current_gate.active,
    current_gate.generation+1,invalidated_count;
END
$transition_kill_switch_authorized_v87$;
ALTER FUNCTION public.transition_kill_switch_authorized(text,text,bigint,boolean,bigint,text) OWNER TO flyway;

CREATE FUNCTION public.persist_decision_bundle_authorized_v2(
  p_capability text,p_bundle_text text
)
RETURNS TABLE(outcome text,result_canonical_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $persist_decision_bundle_authorized_v2$
#variable_conflict use_column
DECLARE
  p_bundle jsonb;
  actor_user_id text;
  actor_role text;
  actor_security_version bigint;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user<>'decision_app' OR octet_length(p_bundle_text) NOT BETWEEN 2 AND 4194304 THEN
    RAISE EXCEPTION 'decision bundle request denied' USING ERRCODE='42501';
  END IF;
  BEGIN
    p_bundle:=p_bundle_text::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF jsonb_typeof(p_bundle)<>'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value='null'::jsonb)
     OR p_bundle-ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ]<>'{}'::jsonb
     OR NOT p_bundle?&ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE='22023';
  END IF;
  BEGIN
    actor_user_id:=p_bundle->>'actorUserId';
    actor_role:=p_bundle->>'actorRole';
    created_at:=(p_bundle->>'createdAt')::timestamptz;
    result_text:=p_bundle->>'resultCanonicalJson';
    snapshot_text:=p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'decisionId' !~ '^dec_[0-9a-f]{32}$'
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions')<>'object'
     OR jsonb_typeof(p_bundle->'violations')<>'array'
     OR jsonb_array_length(p_bundle->'violations')>14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb)<>'object'
     OR jsonb_typeof(snapshot_text::jsonb)<>'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE='22023';
  END IF;
  SELECT actor.security_version INTO actor_security_version
  FROM public.users actor
  WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  FOR SHARE;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,actor_user_id,actor_security_version,actor_role,
    'PERSIST_DECISION_BUNDLE','DECISION',p_bundle->>'decisionId',
    public.actor_capability_payload_hash(p_bundle_text)
  ) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE='42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE='P5501';
  END IF;
  IF gate_active THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE='55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND item.current_version=(p_bundle->>'principleVersion')::integer
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE='40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal:=ordinal+1;
    IF jsonb_typeof(violation)<>'object'
       OR violation-ARRAY['ruleId','severity','observedValue','thresholdValue','message']<>'{}'::jsonb
       OR NOT violation?&ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal:=array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );
  reference_payload:=jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$persist_decision_bundle_authorized_v2$;
ALTER FUNCTION public.persist_decision_bundle_authorized_v2(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.persist_decision_bundle_authorized(text,jsonb) FROM decision_app;
GRANT EXECUTE ON FUNCTION public.persist_decision_bundle_authorized_v2(text,text) TO decision_app;

REVOKE ALL ON FUNCTION
  public.consume_current_actor_capability(text,text),
  public.lock_active_owned_principle_authorized(text,text,text,integer,text,text)
FROM PUBLIC,decision_app,decision_worker,decision_replay;

CREATE TABLE public.p1_kafka_poison_receipt (
  source_topic text NOT NULL,
  source_partition integer NOT NULL,
  source_offset bigint NOT NULL,
  event_id text NOT NULL,
  event_type text NOT NULL,
  payload_hash text NOT NULL,
  attempt integer NOT NULL,
  failure_code text NOT NULL,
  job_id text,
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(source_topic,source_partition,source_offset),
  CHECK(source_partition BETWEEN 0 AND 2),
  CHECK(source_offset >= 0),
  CHECK(source_topic=event_type),
  CHECK(attempt BETWEEN 1 AND 3)
);
ALTER TABLE public.p1_kafka_poison_receipt OWNER TO flyway;
CREATE TRIGGER p1_kafka_poison_receipt_append_only
BEFORE UPDATE OR DELETE ON public.p1_kafka_poison_receipt
FOR EACH ROW EXECUTE FUNCTION public.reject_s7_append_only_mutation();
REVOKE ALL ON TABLE public.p1_kafka_poison_receipt FROM PUBLIC,decision_app,decision_worker,decision_replay;

CREATE FUNCTION public.p1_claim_kafka_outbox(p_worker text,p_limit integer)
RETURNS TABLE(
  event_id text,event_type text,aggregate_type text,aggregate_id text,partition_key text,
  payload_json jsonb,occurred_at timestamptz,outbox_schema_version text,
  kafka_schema_version integer,topic_name text,claim_token uuid,attempt_count integer
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_claim_kafka_outbox$
BEGIN
  IF session_user<>'decision_outbox_publisher' THEN
    RAISE EXCEPTION 'Kafka outbox publisher role denied' USING ERRCODE='42501';
  END IF;
  IF p_worker<>'p1-kafka-outbox-publisher' OR p_limit<>100 THEN
    RAISE EXCEPTION 'Kafka outbox claim bounds denied' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT item.event_id
    FROM public.event_outbox item
    JOIN public.async_event_registry registry
      ON registry.event_type=item.event_type
     AND registry.outbox_schema_version=item.schema_version
     AND registry.enabled
    WHERE item.status IN ('PENDING','FAILED')
      AND item.retry_count<3
      AND item.next_attempt_at<=statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at<=statement_timestamp())
    ORDER BY item.next_attempt_at,item.created_at,item.event_id
    FOR UPDATE OF item SKIP LOCKED LIMIT p_limit
  ), claimed AS (
    UPDATE public.event_outbox item
    SET claim_token=gen_random_uuid(),claimed_by=p_worker,
        lease_expires_at=statement_timestamp()+interval '30 seconds',updated_at=statement_timestamp()
    FROM candidates WHERE item.event_id=candidates.event_id RETURNING item.*
  )
  SELECT claimed.event_id,claimed.event_type,claimed.aggregate_type,claimed.aggregate_id,
    claimed.partition_key,claimed.payload_json,claimed.created_at,claimed.schema_version,
    registry.kafka_schema_version,registry.topic_name,claimed.claim_token,claimed.retry_count+1
  FROM claimed JOIN public.async_event_registry registry ON registry.event_type=claimed.event_type
  ORDER BY claimed.next_attempt_at,claimed.created_at,claimed.event_id;
END
$p1_claim_kafka_outbox$;

CREATE FUNCTION public.p1_claim_kafka_dlq_outbox(p_worker text,p_limit integer)
RETURNS TABLE(
  event_id text,event_type text,aggregate_type text,aggregate_id text,partition_key text,
  payload_json jsonb,occurred_at timestamptz,outbox_schema_version text,
  kafka_schema_version integer,topic_name text,claim_token uuid,attempt_count integer
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_claim_kafka_dlq_outbox$
BEGIN
  IF session_user<>'decision_outbox_publisher' THEN
    RAISE EXCEPTION 'Kafka DLQ publisher role denied' USING ERRCODE='42501';
  END IF;
  IF p_worker<>'p1-kafka-outbox-publisher' OR p_limit<>100 THEN
    RAISE EXCEPTION 'Kafka DLQ claim bounds denied' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT item.event_id
    FROM public.event_outbox item
    JOIN public.async_event_registry registry
      ON registry.event_type=item.event_type
     AND registry.outbox_schema_version=item.schema_version
     AND registry.enabled
    WHERE item.status='DLQ_REQUESTED' AND item.retry_count<3
      AND item.next_attempt_at<=statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at<=statement_timestamp())
    ORDER BY item.next_attempt_at,item.created_at,item.event_id
    FOR UPDATE OF item SKIP LOCKED LIMIT p_limit
  ), claimed AS (
    UPDATE public.event_outbox item
    SET claim_token=gen_random_uuid(),claimed_by=p_worker,
        lease_expires_at=statement_timestamp()+interval '30 seconds',updated_at=statement_timestamp()
    FROM candidates WHERE item.event_id=candidates.event_id RETURNING item.*
  )
  SELECT claimed.event_id,claimed.event_type,claimed.aggregate_type,claimed.aggregate_id,
    claimed.partition_key,claimed.payload_json,claimed.created_at,claimed.schema_version,
    registry.kafka_schema_version,regexp_replace(registry.topic_name,'\.v1$','.dlq.v1'),
    claimed.claim_token,claimed.retry_count+1
  FROM claimed JOIN public.async_event_registry registry ON registry.event_type=claimed.event_type
  ORDER BY claimed.next_attempt_at,claimed.created_at,claimed.event_id;
END
$p1_claim_kafka_dlq_outbox$;

CREATE FUNCTION public.p1_bind_kafka_outbox_payload_hash(
  p_event_id text,p_claim_token uuid,p_payload_hash text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_bind_kafka_outbox_payload_hash$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' THEN
    RAISE EXCEPTION 'Kafka outbox hash role denied' USING ERRCODE='42501';
  END IF;
  IF p_payload_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Kafka outbox hash invalid' USING ERRCODE='22023';
  END IF;
  UPDATE public.event_outbox SET transport_payload_hash=p_payload_hash,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status IN ('PENDING','FAILED') AND lease_expires_at>statement_timestamp()
    AND (transport_payload_hash IS NULL OR transport_payload_hash=p_payload_hash);
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_bind_kafka_outbox_payload_hash$;

CREATE FUNCTION public.p1_complete_kafka_outbox(p_event_id text,p_claim_token uuid)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_complete_kafka_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' THEN
    RAISE EXCEPTION 'Kafka outbox completion role denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.event_outbox
  SET status='PUBLISHED',published_at=statement_timestamp(),claim_token=NULL,claimed_by=NULL,
      lease_expires_at=NULL,failure_code=NULL,error_class=NULL,last_error=NULL,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status IN ('PENDING','FAILED') AND lease_expires_at>statement_timestamp();
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_complete_kafka_outbox$;

CREATE FUNCTION public.p1_complete_kafka_dlq_outbox(p_event_id text,p_claim_token uuid)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_complete_kafka_dlq_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' THEN
    RAISE EXCEPTION 'Kafka DLQ completion role denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.event_outbox
  SET status='PUBLISHED',published_at=statement_timestamp(),claim_token=NULL,claimed_by=NULL,
      lease_expires_at=NULL,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status='DLQ_REQUESTED' AND lease_expires_at>statement_timestamp();
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_complete_kafka_dlq_outbox$;

CREATE FUNCTION public.p1_fail_kafka_outbox(p_event_id text,p_claim_token uuid,p_failure_code text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_fail_kafka_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' OR p_failure_code<>'KAFKA_PUBLISH_FAILED' THEN
    RAISE EXCEPTION 'Kafka outbox failure denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.event_outbox
  SET status=CASE WHEN retry_count+1>=3 THEN 'DLQ_REQUESTED' ELSE 'FAILED' END,
      retry_count=retry_count+1,next_attempt_at=statement_timestamp()+CASE retry_count WHEN 0 THEN interval '1 second' ELSE interval '5 seconds' END,
      claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,failure_code=p_failure_code,
      error_class='RETRYABLE_TRANSIENT',last_error=p_failure_code,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status IN ('PENDING','FAILED') AND retry_count<3;
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_fail_kafka_outbox$;

CREATE FUNCTION public.p1_fail_kafka_dlq_outbox(p_event_id text,p_claim_token uuid,p_failure_code text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_fail_kafka_dlq_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' OR p_failure_code<>'KAFKA_PUBLISH_FAILED' THEN
    RAISE EXCEPTION 'Kafka DLQ failure denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.event_outbox
  SET retry_count=retry_count+1,
      next_attempt_at=statement_timestamp()+CASE retry_count WHEN 0 THEN interval '1 second' ELSE interval '5 seconds' END,
      claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,failure_code=p_failure_code,
      error_class='RETRYABLE_TRANSIENT',last_error=p_failure_code,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status='DLQ_REQUESTED' AND retry_count<3;
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_fail_kafka_dlq_outbox$;

CREATE FUNCTION public.p1_quarantine_kafka_outbox(
  p_event_id text,p_claim_token uuid,p_failure_code text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_quarantine_kafka_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher'
     OR p_failure_code!~'^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'Kafka outbox quarantine denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.event_outbox
  SET status='DLQ_REQUESTED',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
      failure_code=p_failure_code,error_class='CONTRACT_VIOLATION',last_error=p_failure_code,
      updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND claimed_by='p1-kafka-outbox-publisher'
    AND status IN ('PENDING','FAILED') AND lease_expires_at>statement_timestamp();
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$p1_quarantine_kafka_outbox$;

CREATE FUNCTION public.p1_quarantine_unknown_kafka_outbox(p_limit integer)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_quarantine_unknown_kafka_outbox$
DECLARE changed integer;
BEGIN
  IF session_user<>'decision_outbox_publisher' OR p_limit<>100 THEN
    RAISE EXCEPTION 'Kafka unknown quarantine denied' USING ERRCODE='42501';
  END IF;
  WITH candidates AS (
    SELECT item.event_id FROM public.event_outbox item
    LEFT JOIN public.async_event_registry registry
      ON registry.event_type=item.event_type
     AND registry.outbox_schema_version=item.schema_version
     AND registry.enabled
    WHERE item.status IN ('PENDING','FAILED') AND registry.event_type IS NULL
    ORDER BY item.created_at,item.event_id FOR UPDATE OF item SKIP LOCKED LIMIT p_limit
  )
  UPDATE public.event_outbox item
  SET status='DLQ_REQUESTED',failure_code='UNREGISTERED_EVENT',error_class='CONTRACT_VIOLATION',
      last_error='UNREGISTERED_EVENT',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
      updated_at=statement_timestamp()
  FROM candidates WHERE item.event_id=candidates.event_id;
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed;
END
$p1_quarantine_unknown_kafka_outbox$;

CREATE FUNCTION public.p1_record_kafka_poison_receipt(
  p_event_id text,p_event_type text,p_payload_hash text,p_source_topic text,
  p_source_partition integer,p_source_offset bigint,p_attempt integer,p_failure_code text,
  p_partition_key text,p_job_id text,p_claim_token text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_record_kafka_poison_receipt$
DECLARE
  dlq_event_id text;
  changed integer;
BEGIN
  IF session_user<>'decision_poison_recorder' THEN
    RAISE EXCEPTION 'Kafka poison recorder role denied' USING ERRCODE='42501';
  END IF;
  IF p_event_id!~'^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_payload_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_source_topic<>p_event_type
     OR p_source_partition NOT BETWEEN 0 AND 2 OR p_source_offset<0
     OR p_attempt NOT BETWEEN 1 AND 3
     OR p_failure_code!~'^[A-Z][A-Z0-9_]{2,63}$'
     OR p_partition_key!~'^hmac-sha256:[0-9a-f]{64}$'
     OR ((p_job_id IS NULL)<>(p_claim_token IS NULL))
     OR (p_job_id IS NOT NULL AND (p_job_id!~'^job_[A-Za-z0-9_-]{8,96}$'
       OR p_claim_token!~'^[0-9a-f-]{36}$'))
     OR NOT EXISTS(
       SELECT 1 FROM public.async_event_registry registry
       WHERE registry.event_type=p_event_type AND registry.topic_name=p_source_topic AND registry.enabled
     ) THEN
    RAISE EXCEPTION 'Kafka poison receipt invalid' USING ERRCODE='22023';
  END IF;

  IF EXISTS(
    SELECT 1 FROM public.p1_kafka_poison_receipt receipt
    WHERE receipt.source_topic=p_source_topic AND receipt.source_partition=p_source_partition
      AND receipt.source_offset=p_source_offset AND receipt.event_id=p_event_id
      AND receipt.payload_hash=p_payload_hash AND receipt.failure_code=p_failure_code
  ) THEN
    RETURN true;
  END IF;

  IF p_job_id IS NOT NULL THEN
    UPDATE public.async_job
    SET status='NEEDS_REVIEW',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
        error_code=p_failure_code,error_class='CONTRACT_VIOLATION',error_message=p_failure_code,
        completed_at=statement_timestamp(),updated_at=statement_timestamp()
    WHERE job_id=p_job_id AND claim_token=p_claim_token::uuid AND status='RUNNING';
    GET DIAGNOSTICS changed=ROW_COUNT;
    IF changed<>1 THEN RETURN false; END IF;
  END IF;

  INSERT INTO public.p1_kafka_poison_receipt(
    source_topic,source_partition,source_offset,event_id,event_type,payload_hash,
    attempt,failure_code,job_id
  ) VALUES (
    p_source_topic,p_source_partition,p_source_offset,p_event_id,p_event_type,p_payload_hash,
    p_attempt,p_failure_code,p_job_id
  ) ON CONFLICT DO NOTHING;
  IF NOT FOUND THEN RETURN false; END IF;

  dlq_event_id:='evt_dlq_'||encode(public.digest(
    p_source_topic||'|'||p_source_partition::text||'|'||p_source_offset::text,'sha256'
  ),'hex')::text;
  dlq_event_id:=left(dlq_event_id,40);
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,failure_code,error_class,last_error
  ) VALUES (
    dlq_event_id,p_event_type,'ASYNC_DLQ',p_event_id,p_partition_key,
    jsonb_build_object('eventId',p_event_id,'eventType',p_event_type,'payloadHash',p_payload_hash,
      'failureCode',p_failure_code,'sourceTopic',p_source_topic,'sourcePartition',p_source_partition,
      'sourceOffset',p_source_offset,'attempt',p_attempt),
    '1.0.0','DLQ_REQUESTED',p_failure_code,'CONTRACT_VIOLATION',p_failure_code
  ) ON CONFLICT(event_id) DO NOTHING;
  RETURN true;
END
$p1_record_kafka_poison_receipt$;

ALTER FUNCTION public.p1_claim_kafka_outbox(text,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_claim_kafka_dlq_outbox(text,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_bind_kafka_outbox_payload_hash(text,uuid,text) OWNER TO flyway;
ALTER FUNCTION public.p1_complete_kafka_outbox(text,uuid) OWNER TO flyway;
ALTER FUNCTION public.p1_complete_kafka_dlq_outbox(text,uuid) OWNER TO flyway;
ALTER FUNCTION public.p1_fail_kafka_outbox(text,uuid,text) OWNER TO flyway;
ALTER FUNCTION public.p1_fail_kafka_dlq_outbox(text,uuid,text) OWNER TO flyway;
ALTER FUNCTION public.p1_quarantine_kafka_outbox(text,uuid,text) OWNER TO flyway;
ALTER FUNCTION public.p1_quarantine_unknown_kafka_outbox(integer) OWNER TO flyway;
ALTER FUNCTION public.p1_record_kafka_poison_receipt(
  text,text,text,text,integer,bigint,integer,text,text,text,text
) OWNER TO flyway;
REVOKE ALL ON FUNCTION
  public.p1_claim_kafka_outbox(text,integer),
  public.p1_claim_kafka_dlq_outbox(text,integer),
  public.p1_bind_kafka_outbox_payload_hash(text,uuid,text),
  public.p1_complete_kafka_outbox(text,uuid),
  public.p1_complete_kafka_dlq_outbox(text,uuid),
  public.p1_fail_kafka_outbox(text,uuid,text),
  public.p1_fail_kafka_dlq_outbox(text,uuid,text),
  public.p1_quarantine_kafka_outbox(text,uuid,text),
  public.p1_quarantine_unknown_kafka_outbox(integer),
  public.p1_record_kafka_poison_receipt(text,text,text,text,integer,bigint,integer,text,text,text,text)
FROM PUBLIC,decision_app,decision_worker,decision_replay;
GRANT EXECUTE ON FUNCTION
  public.p1_claim_kafka_outbox(text,integer),
  public.p1_claim_kafka_dlq_outbox(text,integer),
  public.p1_bind_kafka_outbox_payload_hash(text,uuid,text),
  public.p1_complete_kafka_outbox(text,uuid),
  public.p1_complete_kafka_dlq_outbox(text,uuid),
  public.p1_fail_kafka_outbox(text,uuid,text),
  public.p1_fail_kafka_dlq_outbox(text,uuid,text),
  public.p1_quarantine_kafka_outbox(text,uuid,text),
  public.p1_quarantine_unknown_kafka_outbox(integer)
TO decision_outbox_publisher;
GRANT EXECUTE ON FUNCTION public.p1_record_kafka_poison_receipt(
  text,text,text,text,integer,bigint,integer,text,text,text,text
) TO decision_poison_recorder;

-- Signed provider authority has one durable claim store; Redis and output roots are evidence only.
CREATE TABLE public.p1_provider_approval_claim (
  packet_hash text PRIMARY KEY CHECK (packet_hash ~ '^sha256:[0-9a-f]{64}$'),
  approval_id_hash text NOT NULL CHECK (approval_id_hash ~ '^sha256:[0-9a-f]{64}$'),
  nonce_hash text NOT NULL UNIQUE CHECK (nonce_hash ~ '^sha256:[0-9a-f]{64}$'),
  operation_set_hash text NOT NULL CHECK (operation_set_hash ~ '^sha256:[0-9a-f]{64}$'),
  physical_call_cap integer NOT NULL CHECK (physical_call_cap BETWEEN 1 AND 112),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE FUNCTION public.consume_p1_provider_approval(
  p_packet_hash text,p_approval_id_hash text,p_nonce_hash text,p_operation_set_hash text,
  p_physical_call_cap integer,p_expires_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_p1_provider_approval$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_replay'
     OR p_packet_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_approval_id_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_nonce_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_operation_set_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_physical_call_cap NOT BETWEEN 1 AND 112
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes' THEN
    RAISE EXCEPTION 'P1 provider approval claim denied' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.p1_provider_approval_claim(
    packet_hash,approval_id_hash,nonce_hash,operation_set_hash,physical_call_cap,expires_at
  ) VALUES (
    p_packet_hash,p_approval_id_hash,p_nonce_hash,p_operation_set_hash,p_physical_call_cap,p_expires_at
  ) ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$consume_p1_provider_approval$;

ALTER TABLE public.p1_provider_approval_claim OWNER TO flyway;
ALTER FUNCTION public.consume_p1_provider_approval(text,text,text,text,integer,timestamptz) OWNER TO flyway;
REVOKE ALL ON TABLE public.p1_provider_approval_claim FROM PUBLIC,decision_app,decision_worker,decision_identity;
REVOKE ALL ON FUNCTION public.consume_p1_provider_approval(text,text,text,text,integer,timestamptz)
  FROM PUBLIC,decision_app,decision_worker,decision_identity;
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.p1_provider_approval_claim TO flyway;
GRANT EXECUTE ON FUNCTION public.consume_p1_provider_approval(text,text,text,text,integer,timestamptz)
  TO decision_replay;


-- Refresh is a one-shot DB claim bound to the current actor security version.
CREATE FUNCTION public.consume_s4_9_mcp_refresh_token(
  p_token_sha256 text
)
RETURNS TABLE(
  client_id text,owner_user_id text,security_version bigint,resource_uri text,scopes text[]
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $consume_s4_9_mcp_refresh_token$
BEGIN
  IF session_user<>'decision_app' OR p_token_sha256!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S4.9 refresh token claim denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  UPDATE public.s4_9_mcp_oauth_refresh_tokens token
  SET rotated_at=statement_timestamp()
  FROM public.users actor,public.s4_9_mcp_oauth_clients client
  WHERE token.token_sha256=p_token_sha256
    AND token.owner_user_id=actor.user_id
    AND token.client_id=client.client_id
    AND token.rotated_at IS NULL AND token.revoked_at IS NULL
    AND token.expires_at>statement_timestamp()
    AND actor.status='ACTIVE' AND actor.security_version=token.security_version
    AND client.status='ACTIVE'
  RETURNING token.client_id,token.owner_user_id,token.security_version,token.resource_uri,token.scopes;
END
$consume_s4_9_mcp_refresh_token$;

ALTER FUNCTION public.consume_s4_9_mcp_refresh_token(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.consume_s4_9_mcp_refresh_token(text)
  FROM PUBLIC,decision_worker,decision_replay,decision_identity;
GRANT EXECUTE ON FUNCTION public.consume_s4_9_mcp_refresh_token(text) TO decision_app;


-- New refresh tokens preserve the consumed claim security version and cannot cross credential rotation.
CREATE OR REPLACE FUNCTION public.rotate_s4_9_mcp_refresh_token_hash(
  p_token_sha256 text,p_client_id text,p_owner_user_id text,p_security_version bigint,
  p_resource_uri text,p_scopes text[],p_expires_at timestamptz
)
RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $rotate_s4_9_mcp_refresh_token_hash$
DECLARE family_id text;
DECLARE previous_hash text;
BEGIN
  IF session_user<>'decision_app'
     OR p_token_sha256!~'^[0-9a-f]{64}$'
     OR p_owner_user_id!~'^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_security_version<=0
     OR p_expires_at<=transaction_timestamp()
     OR p_expires_at>transaction_timestamp()+interval '7 days 1 minute'
     OR NOT p_scopes<@ARRAY[
       'mcp:rag.public','mcp:rag.owner','mcp:web.read','mcp:answer.validate','mcp:history.write'
     ]::text[]
     OR NOT EXISTS(
       SELECT 1 FROM public.users actor
       WHERE actor.user_id=p_owner_user_id AND actor.status='ACTIVE'
         AND actor.security_version=p_security_version
     ) THEN
    RAISE EXCEPTION 'S4.9 refresh token hash is invalid' USING ERRCODE='22023';
  END IF;
  IF EXISTS(
    SELECT 1 FROM public.s4_9_mcp_oauth_refresh_tokens
    WHERE token_sha256=p_token_sha256
  ) THEN
    RETURN;
  END IF;
  family_id:='mrf_'||substr(encode(public.digest(
    p_client_id||':'||p_owner_user_id,'sha256'
  ),'hex'),1,32);
  SELECT token_sha256 INTO previous_hash
  FROM public.s4_9_mcp_oauth_refresh_tokens
  WHERE token_family_id=family_id AND rotated_at IS NULL AND revoked_at IS NULL
  FOR UPDATE;
  IF FOUND THEN
    UPDATE public.s4_9_mcp_oauth_refresh_tokens
    SET rotated_at=transaction_timestamp()
    WHERE token_sha256=previous_hash;
  END IF;
  INSERT INTO public.s4_9_mcp_oauth_refresh_tokens(
    token_sha256,token_family_id,client_id,owner_user_id,security_version,
    resource_uri,scopes,previous_token_sha256,expires_at
  ) VALUES (
    p_token_sha256,family_id,p_client_id,p_owner_user_id,p_security_version,
    p_resource_uri,p_scopes,previous_hash,p_expires_at
  );
END
$rotate_s4_9_mcp_refresh_token_hash$;
