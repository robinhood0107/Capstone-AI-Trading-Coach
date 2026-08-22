-- S7/P1 final security closure: one-use actor/replay capabilities, event-bound DB work,
-- broker-offset poison quotas, pre-parse bounds, and removal of broad application DML.

CREATE TABLE actor_request_capability (
  token_hash text PRIMARY KEY,
  actor_user_id text NOT NULL,
  actor_role text NOT NULL,
  actor_security_version bigint NOT NULL,
  issued_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  CHECK (token_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (actor_role IN ('USER', 'ADMIN')),
  CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '30 seconds'),
  CHECK (consumed_at IS NULL OR consumed_at >= issued_at)
);

CREATE INDEX actor_request_capability_expires_idx
  ON actor_request_capability(expires_at);

CREATE TABLE async_replay_authorization (
  packet_hash text PRIMARY KEY,
  actor_user_id text NOT NULL,
  actor_security_version bigint NOT NULL,
  replay_batch_id text NOT NULL UNIQUE,
  target_kind text NOT NULL,
  target_ids text[] NOT NULL,
  expected_count integer NOT NULL,
  reason_code text NOT NULL,
  execute_authorized boolean NOT NULL,
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  CHECK (packet_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (replay_batch_id ~ '^replay_[0-9a-f]{32}$'),
  CHECK (target_kind IN ('EVENT', 'JOB')),
  CHECK (cardinality(target_ids) BETWEEN 1 AND 100),
  CHECK (expected_count BETWEEN 1 AND 100),
  CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '5 minutes'),
  CHECK (consumed_at IS NULL OR consumed_at >= issued_at)
);

CREATE TABLE kafka_poison_receipt (
  source_topic text NOT NULL,
  source_partition integer NOT NULL,
  source_offset bigint NOT NULL,
  event_id text NOT NULL,
  payload_hash text NOT NULL,
  failure_code text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY (source_topic, source_partition, source_offset),
  CHECK (source_partition BETWEEN 0 AND 1023),
  CHECK (source_offset >= 0),
  CHECK (event_id ~ '^evt_[A-Za-z0-9_-]{8,96}$'),
  CHECK (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
  CHECK (failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$')
);

CREATE FUNCTION issue_actor_request_capability(p_actor_user_id text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $issue_actor_request_capability$
DECLARE token text; actor record;
BEGIN
  IF session_user <> 'decision_identity' THEN
    RAISE EXCEPTION 'actor capability issuer role denied' USING ERRCODE = '42501';
  END IF;
  SELECT role, status, security_version INTO actor
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor.status <> 'ACTIVE' OR actor.role NOT IN ('USER', 'ADMIN') THEN
    RETURN NULL;
  END IF;
  DELETE FROM public.actor_request_capability expired
  WHERE expired.token_hash IN (
    SELECT candidate.token_hash
    FROM public.actor_request_capability candidate
    WHERE candidate.expires_at <= statement_timestamp()
    ORDER BY candidate.expires_at
    LIMIT 100
  );
  token := 'cap_' || replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '');
  INSERT INTO public.actor_request_capability(
    token_hash, actor_user_id, actor_role, actor_security_version, expires_at
  ) VALUES (
    'sha256:' || encode(public.digest(token, 'sha256'), 'hex'),
    p_actor_user_id, actor.role, actor.security_version, statement_timestamp() + interval '15 seconds'
  );
  RETURN token;
END
$issue_actor_request_capability$;

CREATE FUNCTION consume_actor_request_capability(
  p_token text, p_actor_user_id text, p_security_version bigint, p_required_role text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_actor_request_capability$
DECLARE changed integer;
BEGIN
  IF (session_user <> 'decision_app' AND NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
      )) OR p_token !~ '^cap_[0-9a-f]{64}$'
     OR p_required_role IS NOT NULL AND p_required_role <> 'ADMIN' THEN
    RAISE EXCEPTION 'actor capability denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.actor_request_capability capability
  SET consumed_at = statement_timestamp()
  WHERE capability.token_hash = 'sha256:' || encode(public.digest(p_token, 'sha256'), 'hex')
    AND capability.actor_user_id = p_actor_user_id
    AND capability.actor_security_version = p_security_version
    AND (p_required_role IS NULL OR capability.actor_role = p_required_role)
    AND capability.consumed_at IS NULL
    AND capability.expires_at > statement_timestamp()
    AND EXISTS (
      SELECT 1 FROM public.users actor
      WHERE actor.user_id = capability.actor_user_id
        AND actor.status = 'ACTIVE'
        AND actor.role = capability.actor_role
        AND actor.security_version = capability.actor_security_version
    );
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$consume_actor_request_capability$;

CREATE FUNCTION read_demo_credentials()
RETURNS TABLE(user_id text, username text, password_hash text, role text, status text, security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_demo_credentials$
BEGIN
  IF session_user <> 'decision_app' AND NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
  ) THEN RAISE EXCEPTION 'credential reader role denied' USING ERRCODE='42501'; END IF;
  RETURN QUERY SELECT item.user_id,item.username,item.password_hash,item.role,item.status,item.security_version
  FROM public.users item WHERE item.user_id IN ('usr_demo_user','usr_demo_admin') ORDER BY item.user_id;
END
$read_demo_credentials$;

CREATE FUNCTION read_user_actor(p_user_id text)
RETURNS TABLE(user_id text, username text, role text, status text, security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_user_actor$
BEGIN
  IF (session_user <> 'decision_app' AND NOT EXISTS (
       SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
     )) OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$' THEN
    RAISE EXCEPTION 'actor reader denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY SELECT item.user_id,item.username,item.role,item.status,item.security_version
  FROM public.users item WHERE item.user_id=p_user_id;
END
$read_user_actor$;

CREATE FUNCTION create_async_request_authorized(
  p_capability text,p_event_id text,p_event_type text,p_partition_key text,
  p_job_id text,p_job_type text,p_requested_by text,p_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $create_async_request_authorized$
DECLARE security_version bigint;
BEGIN
  SELECT actor.security_version INTO security_version FROM public.users actor WHERE actor.user_id=p_requested_by;
  IF NOT FOUND OR NOT public.consume_actor_request_capability(p_capability,p_requested_by,security_version,NULL) THEN
    RAISE EXCEPTION 'async actor capability denied' USING ERRCODE='42501';
  END IF;
  IF NOT public.create_async_job(p_job_id,p_job_type,p_requested_by,p_payload)
     OR NOT public.append_async_request_outbox(p_event_id,p_event_type,p_job_id,p_partition_key,p_payload) THEN
    RAISE EXCEPTION 'async request creation conflict' USING ERRCODE='40001';
  END IF;
  RETURN true;
END
$create_async_request_authorized$;

CREATE FUNCTION read_async_job_status_authorized(p_capability text,p_actor_user_id text,p_security_version bigint,p_job_id text)
RETURNS TABLE(job_id text,job_type text,status text,requested_at timestamptz,started_at timestamptz,
  completed_at timestamptz,source_id text,artifact_id text,result_ref text,error_code text,error_class text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_async_job_status_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,'ADMIN') THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_async_job_status(p_actor_user_id,p_security_version,p_job_id);
END
$read_async_job_status_authorized$;

CREATE FUNCTION list_async_job_status_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_status text,p_job_type text,
  p_before_created_at timestamptz,p_before_job_id text,p_limit integer
)
RETURNS TABLE(job_id text,job_type text,status text,requested_at timestamptz,started_at timestamptz,
  completed_at timestamptz,source_id text,artifact_id text,result_ref text,error_code text,error_class text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_async_job_status_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,'ADMIN') THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.list_async_job_status(p_actor_user_id,p_security_version,p_status,p_job_type,
    p_before_created_at,p_before_job_id,p_limit);
END
$list_async_job_status_authorized$;

CREATE FUNCTION read_stream_metric_status_authorized(p_capability text,p_actor_user_id text,p_security_version bigint)
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
AS $read_stream_metric_status_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,'ADMIN') THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_stream_metric_status(p_actor_user_id,p_security_version);
END
$read_stream_metric_status_authorized$;

CREATE FUNCTION read_dashboard_artifact_view_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_view_kind text,p_run_id text
)
RETURNS TABLE(projection_json jsonb,evidence_mode text,fixture_class text,as_of timestamptz,fresh_until timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_artifact_view_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,NULL) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_artifact_view(p_actor_user_id,p_security_version,p_view_kind,p_run_id);
END
$read_dashboard_artifact_view_authorized$;

CREATE FUNCTION read_dashboard_risk_view_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_decision_id text
)
RETURNS TABLE(decision_id text,outcome text,evaluation_as_of timestamptz,valid_until timestamptz,
  reasons jsonb,principles jsonb,risk_items jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_risk_view_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,NULL) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_risk_view(p_actor_user_id,p_security_version,p_decision_id);
END
$read_dashboard_risk_view_authorized$;

CREATE FUNCTION read_dashboard_rag_sources_authorized(
  p_capability text,p_actor_user_id text,p_security_version bigint,p_answer_id text
)
RETURNS TABLE(answer_id text,created_at timestamptz,expires_at timestamptz,sources jsonb)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_dashboard_rag_sources_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,NULL) THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.read_dashboard_rag_sources(p_actor_user_id,p_security_version,p_answer_id);
END
$read_dashboard_rag_sources_authorized$;

CREATE FUNCTION list_artifact_ingest_status_authorized(p_capability text,p_actor_user_id text,p_security_version bigint)
RETURNS TABLE(artifact_id text,file_name text,producer text,run_id text,file_hash text,schema_version text,
  status text,last_ingested_at timestamptz,duplicate boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_artifact_ingest_status_authorized$
BEGIN
  IF NOT public.consume_actor_request_capability(p_capability,p_actor_user_id,p_security_version,'ADMIN') THEN RETURN; END IF;
  RETURN QUERY SELECT * FROM public.list_artifact_ingest_status(p_actor_user_id,p_security_version);
END
$list_artifact_ingest_status_authorized$;

CREATE FUNCTION consume_current_actor_capability(p_capability text,p_actor_user_id text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $consume_current_actor_capability$
DECLARE current_security_version bigint;
BEGIN
  IF session_user <> 'decision_app' AND NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
  ) THEN
    RAISE EXCEPTION 'principle capability role denied' USING ERRCODE='42501';
  END IF;
  SELECT actor.security_version INTO current_security_version
  FROM public.users actor WHERE actor.user_id=p_actor_user_id;
  IF NOT FOUND THEN RETURN false; END IF;
  RETURN public.consume_actor_request_capability(
    p_capability,p_actor_user_id,current_security_version,NULL
  );
END
$consume_current_actor_capability$;

CREATE FUNCTION insert_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_preset_id text,p_title text,
  p_mode text,p_status text,p_version integer,p_created_at timestamptz,p_updated_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_authorized$
DECLARE changed integer;
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN
    RAISE EXCEPTION 'principle actor capability denied' USING ERRCODE='42501';
  END IF;
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
$insert_principle_authorized$;

CREATE FUNCTION insert_principle_version_authorized(
  p_capability text,p_actor_user_id text,p_version_id text,p_principle_id text,p_version integer,
  p_preset_id text,p_title text,p_mode text,p_status text,p_rules_json jsonb,
  p_changed_fields text[],p_created_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_version_authorized$
DECLARE changed integer;
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN
    RAISE EXCEPTION 'principle version capability denied' USING ERRCODE='42501';
  END IF;
  IF p_version_id !~ '^pvr_[0-9a-f]{32}$' OR p_version<1
     OR NOT EXISTS (SELECT 1 FROM public.principles item
       WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id)
     OR p_changed_fields IS NULL OR cardinality(p_changed_fields) NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'invalid principle version insert' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.principle_versions(
    principle_version_id,principle_id,version,preset_id,title,mode,status,
    rules_json,changed_fields,created_by,created_at
  ) VALUES (
    p_version_id,p_principle_id,p_version,p_preset_id,p_title,p_mode,p_status,
    p_rules_json,p_changed_fields,p_actor_user_id,p_created_at
  );
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$insert_principle_version_authorized$;

CREATE FUNCTION insert_principle_audit_authorized(
  p_capability text,p_actor_user_id text,p_request_id text,p_action text,
  p_principle_id text,p_new_version integer,p_changed_fields text[],p_created_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $insert_principle_audit_authorized$
DECLARE actor_role text; changed integer;
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN
    RAISE EXCEPTION 'principle audit capability denied' USING ERRCODE='42501';
  END IF;
  IF p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_action NOT IN ('PRINCIPLE_CREATED','PRINCIPLE_UPDATED','PRINCIPLE_ARCHIVED','PRINCIPLE_REACTIVATED')
     OR p_new_version<1 OR p_changed_fields IS NULL OR cardinality(p_changed_fields) NOT BETWEEN 1 AND 5
     OR NOT EXISTS (SELECT 1 FROM public.principles item
       WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id) THEN
    RAISE EXCEPTION 'invalid principle audit insert' USING ERRCODE='22023';
  END IF;
  SELECT actor.role INTO actor_role FROM public.users actor WHERE actor.user_id=p_actor_user_id;
  INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at)
  VALUES ('aud_'||replace(gen_random_uuid()::text,'-',''),p_actor_user_id,actor_role,p_action,'PRINCIPLE',
    p_principle_id,p_request_id,jsonb_build_object('principleId',p_principle_id,
      'newVersion',p_new_version,'changedFields',to_jsonb(p_changed_fields)),p_created_at);
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$insert_principle_audit_authorized$;

CREATE FUNCTION read_owned_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text
)
RETURNS TABLE(principle_id text,user_id text,preset_id text,title text,mode text,status text,
  current_version integer,created_at timestamptz,updated_at timestamptz,rules_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_owned_principle_authorized$
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN RETURN; END IF;
  RETURN QUERY SELECT item.principle_id,item.user_id,item.preset_id,item.title,item.mode,item.status,
    item.current_version,item.created_at,item.updated_at,version.rules_json::text
  FROM public.principles item JOIN public.principle_versions version
    ON version.principle_id=item.principle_id AND version.version=item.current_version
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id;
END
$read_owned_principle_authorized$;

CREATE FUNCTION list_owned_principles_authorized(
  p_capability text,p_actor_user_id text,p_limit integer,p_sort text,
  p_after_updated_at timestamptz,p_after_principle_id text
)
RETURNS TABLE(principle_id text,preset_id text,title text,mode text,status text,
  current_version integer,created_at timestamptz,updated_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_owned_principles_authorized$
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN RETURN; END IF;
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
$list_owned_principles_authorized$;

CREATE FUNCTION update_owned_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_expected_version integer,
  p_title text,p_mode text,p_status text,p_updated_at timestamptz
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $update_owned_principle_authorized$
DECLARE next_version integer;
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN
    RAISE EXCEPTION 'principle update capability denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.principles item SET title=p_title,mode=p_mode,status=p_status,
    current_version=item.current_version+1,updated_at=p_updated_at
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id
    AND item.current_version=p_expected_version AND item.current_version<2147483647
  RETURNING item.current_version INTO next_version;
  RETURN next_version;
END
$update_owned_principle_authorized$;

CREATE FUNCTION list_owned_principle_versions_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_limit integer,
  p_sort text,p_after_version integer
)
RETURNS TABLE(principle_id text,version integer,preset_id text,title text,mode text,status text,
  rules_json text,changed_fields text[],created_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $list_owned_principle_versions_authorized$
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN RETURN; END IF;
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
$list_owned_principle_versions_authorized$;

CREATE FUNCTION read_active_owned_principle_snapshot_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text
)
RETURNS TABLE(principle_id text,principle_version_id text,version integer,mode text,status text,rules_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $read_active_owned_principle_snapshot_authorized$
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.principle_id,version_item.principle_version_id,version_item.version,
    version_item.mode,version_item.status,version_item.rules_json::text
  FROM public.principles item
  JOIN public.principle_versions version_item
    ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id
    AND item.status='ACTIVE' AND version_item.status='ACTIVE';
END
$read_active_owned_principle_snapshot_authorized$;

CREATE FUNCTION lock_active_owned_principle_authorized(
  p_capability text,p_actor_user_id text,p_principle_id text,p_principle_version integer,
  p_principle_version_id text,p_mode text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $lock_active_owned_principle_authorized$
DECLARE locked_id text;
BEGIN
  IF NOT public.consume_current_actor_capability(p_capability,p_actor_user_id) THEN
    RAISE EXCEPTION 'principle lock capability denied' USING ERRCODE='42501';
  END IF;
  SELECT item.principle_id INTO locked_id
  FROM public.principles item
  JOIN public.principle_versions version_item
    ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
  WHERE item.principle_id=p_principle_id AND item.user_id=p_actor_user_id
    AND item.status='ACTIVE' AND item.current_version=p_principle_version
    AND version_item.principle_version_id=p_principle_version_id
    AND version_item.status='ACTIVE' AND version_item.mode=p_mode
  FOR SHARE OF item;
  RETURN locked_id IS NOT NULL;
END
$lock_active_owned_principle_authorized$;

CREATE FUNCTION resolve_completed_async_event(
  p_event_id text,p_event_type text,p_job_id text,p_payload_hash text,p_partition_key text
)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $resolve_completed_async_event$
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'async duplicate role denied' USING ERRCODE='42501'; END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.event_outbox source
    JOIN public.async_job job ON job.job_id=source.aggregate_id
    JOIN public.processed_event processed ON processed.event_id=source.event_id
      AND processed.consumer_name='python-async-worker-v1' AND processed.payload_hash=p_payload_hash
      AND NOT processed.payload_hash_conflict
    JOIN public.async_materialization_receipt receipt ON receipt.event_id=source.event_id AND receipt.job_id=job.job_id
    WHERE source.event_id=p_event_id AND source.event_type=p_event_type
      AND source.aggregate_type='ASYNC_JOB' AND source.aggregate_id=p_job_id
      AND source.partition_key=p_partition_key AND source.schema_version='1.0.0'
      AND coalesce(source.transport_payload_hash,
        'sha256:'||encode(public.digest(source.payload_json::text,'sha256'),'hex'))=p_payload_hash
      AND job.status='COMPLETED'
  );
END
$resolve_completed_async_event$;

DROP FUNCTION public.record_kafka_poison(text,text,text,text,text,integer,text,text);
CREATE FUNCTION record_kafka_poison(
  p_event_id text,p_event_type text,p_payload_hash text,p_source_topic text,p_source_partition integer,
  p_source_offset bigint,p_attempt integer,p_failure_code text,p_partition_key text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $record_kafka_poison$
DECLARE dlq_event_id text; inserted integer;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'Kafka poison role denied' USING ERRCODE='42501'; END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_source_topic<>p_event_type OR p_source_partition NOT BETWEEN 0 AND 1023 OR p_source_offset<0
     OR p_attempt NOT BETWEEN 1 AND 3 OR p_failure_code !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM public.async_event_registry registry
       WHERE registry.event_type=p_event_type AND registry.topic_name=p_source_topic AND registry.enabled) THEN
    RAISE EXCEPTION 'invalid Kafka poison record' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('s7-kafka-poison-admission',0));
  IF (SELECT count(*) FROM public.kafka_poison_receipt
      WHERE recorded_at>=statement_timestamp()-interval '1 hour')>=1000
     OR (SELECT count(*) FROM public.kafka_poison_receipt
      WHERE source_topic=p_source_topic AND source_partition=p_source_partition
        AND recorded_at>=statement_timestamp()-interval '1 hour')>=100
     OR (SELECT count(*) FROM public.event_outbox WHERE status='DLQ_REQUESTED')>=1000 THEN
    RAISE EXCEPTION 'Kafka poison admission exhausted' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.kafka_poison_receipt(source_topic,source_partition,source_offset,event_id,payload_hash,failure_code)
  VALUES (p_source_topic,p_source_partition,p_source_offset,p_event_id,p_payload_hash,p_failure_code)
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS inserted=ROW_COUNT;
  IF inserted=0 THEN RETURN false; END IF;
  dlq_event_id:='evt_dlq_'||substr(encode(public.digest(
    p_source_topic||'|'||p_source_partition::text||'|'||p_source_offset::text,'sha256'),'hex'),1,32);
  INSERT INTO public.event_outbox(event_id,event_type,aggregate_type,aggregate_id,partition_key,
    payload_json,schema_version,status,failure_code,error_class,last_error)
  VALUES (dlq_event_id,p_event_type,'ASYNC_DLQ',p_event_id,p_partition_key,
    jsonb_build_object('eventId',p_event_id,'eventType',p_event_type,'payloadHash',p_payload_hash,
      'failureCode',p_failure_code,'sourceTopic',p_source_topic,'attempt',p_attempt),
    '1.0.0','DLQ_REQUESTED',p_failure_code,'CONTRACT_VIOLATION',p_failure_code);
  RETURN true;
END
$record_kafka_poison$;

CREATE FUNCTION authorize_async_replay(
  p_packet_hash text,p_actor_user_id text,p_security_version bigint,p_replay_batch_id text,
  p_target_kind text,p_target_ids text[],p_expected_count integer,p_reason_code text,
  p_execute_authorized boolean,p_issued_at timestamptz,p_expires_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authorize_async_replay$
BEGIN
  IF session_user <> 'decision_replay_authorizer' OR p_packet_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_replay_batch_id !~ '^replay_[0-9a-f]{32}$' OR p_target_kind NOT IN ('EVENT','JOB')
     OR cardinality(p_target_ids) NOT BETWEEN 1 AND 100
     OR cardinality(p_target_ids)<>(SELECT count(DISTINCT value) FROM unnest(p_target_ids) value)
     OR p_expected_count<>cardinality(p_target_ids) OR p_reason_code !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_issued_at>statement_timestamp()+interval '5 seconds'
     OR p_expires_at<=statement_timestamp() OR p_expires_at>p_issued_at+interval '5 minutes'
     OR NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
       AND actor.status='ACTIVE' AND actor.role='ADMIN' AND actor.security_version=p_security_version) THEN
    RAISE EXCEPTION 'invalid replay authorization' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.async_replay_authorization(packet_hash,actor_user_id,actor_security_version,replay_batch_id,
    target_kind,target_ids,expected_count,reason_code,execute_authorized,issued_at,expires_at)
  VALUES (p_packet_hash,p_actor_user_id,p_security_version,p_replay_batch_id,p_target_kind,p_target_ids,
    p_expected_count,p_reason_code,p_execute_authorized,p_issued_at,p_expires_at)
  ON CONFLICT DO NOTHING;
  IF FOUND THEN RETURN true; END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.async_replay_authorization authz
    WHERE authz.packet_hash=p_packet_hash AND authz.actor_user_id=p_actor_user_id
      AND authz.actor_security_version=p_security_version AND authz.replay_batch_id=p_replay_batch_id
      AND authz.target_kind=p_target_kind AND authz.target_ids=p_target_ids
      AND authz.expected_count=p_expected_count AND authz.reason_code=p_reason_code
      AND authz.execute_authorized=p_execute_authorized AND authz.issued_at=p_issued_at
      AND authz.expires_at=p_expires_at AND authz.consumed_at IS NULL
      AND authz.expires_at>statement_timestamp()
  );
END
$authorize_async_replay$;

CREATE OR REPLACE FUNCTION public.replay_async_work(
  p_actor_user_id text,p_security_version bigint,p_replay_batch_id text,p_target_kind text,p_target_ids text[],
  p_expected_count integer,p_reason_code text,p_packet_hash text,p_execute boolean
)
RETURNS TABLE(target_id text,source_job_id text,source_event_id text,new_job_id text,new_event_id text,outcome text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $replay_async_work$
DECLARE actual integer; resolved record; generated_job_id text; generated_event_id text; authorized integer;
BEGIN
  IF session_user <> 'decision_replay' THEN RAISE EXCEPTION 'async replay role denied' USING ERRCODE='42501'; END IF;
  UPDATE public.async_replay_authorization AS authz SET consumed_at=statement_timestamp()
  WHERE authz.packet_hash=p_packet_hash AND authz.actor_user_id=p_actor_user_id
    AND authz.actor_security_version=p_security_version AND authz.replay_batch_id=p_replay_batch_id
    AND authz.target_kind=p_target_kind AND authz.target_ids=p_target_ids
    AND authz.expected_count=p_expected_count AND authz.reason_code=p_reason_code
    AND authz.execute_authorized=p_execute AND authz.consumed_at IS NULL
    AND authz.issued_at<=statement_timestamp() AND authz.expires_at>statement_timestamp()
    AND EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
      AND actor.status='ACTIVE' AND actor.role='ADMIN' AND actor.security_version=p_security_version);
  GET DIAGNOSTICS authorized=ROW_COUNT;
  IF authorized<>1 THEN RAISE EXCEPTION 'replay authorization unavailable' USING ERRCODE='42501'; END IF;
  CREATE TEMP TABLE pg_temp.s7_replay_targets ON COMMIT DROP AS
    SELECT requested.target_id,job.job_id source_job_id,event.event_id source_event_id,job.job_type,
      job.requested_by,job.payload_json,event.event_type,event.aggregate_type,event.partition_key,event.schema_version
    FROM unnest(p_target_ids) requested(target_id)
    JOIN public.async_job job ON ((p_target_kind='JOB' AND job.job_id=requested.target_id) OR
      (p_target_kind='EVENT' AND EXISTS (SELECT 1 FROM public.event_outbox lookup
        WHERE lookup.event_id=requested.target_id AND lookup.aggregate_id=job.job_id)))
    JOIN LATERAL (SELECT candidate.* FROM public.event_outbox candidate WHERE candidate.aggregate_id=job.job_id
      AND candidate.event_type IN ('rag.index-requested.v1','artifact.ingest-requested.v1','model.eval-requested.v1')
      AND (p_target_kind='JOB' OR candidate.event_id=requested.target_id)
      ORDER BY candidate.created_at,candidate.event_id LIMIT 1) event ON true
    WHERE job.status IN ('FAILED','NEEDS_REVIEW') OR event.status IN ('FAILED','DLQ_REQUESTED');
  SELECT count(*) INTO actual FROM pg_temp.s7_replay_targets;
  INSERT INTO public.async_replay_audit(replay_batch_id,actor_user_id,actor_security_version,target_kind,
    expected_count,actual_count,dry_run,outcome,reason_code,packet_hash)
  VALUES (p_replay_batch_id,p_actor_user_id,p_security_version,p_target_kind,p_expected_count,actual,NOT p_execute,
    CASE WHEN actual<>p_expected_count THEN 'COUNT_MISMATCH' WHEN p_execute THEN 'EXECUTED' ELSE 'DRY_RUN' END,
    p_reason_code,p_packet_hash);
  IF actual<>p_expected_count THEN
    RETURN QUERY SELECT requested.target_id,NULL::text,NULL::text,NULL::text,NULL::text,'COUNT_MISMATCH'::text
    FROM unnest(p_target_ids) requested(target_id) ORDER BY requested.target_id; RETURN;
  END IF;
  FOR resolved IN SELECT * FROM pg_temp.s7_replay_targets ORDER BY target_id LOOP
    generated_job_id:=CASE WHEN p_execute THEN 'job_'||replace(gen_random_uuid()::text,'-','') ELSE NULL END;
    generated_event_id:=CASE WHEN p_execute THEN 'evt_'||replace(gen_random_uuid()::text,'-','') ELSE NULL END;
    IF p_execute THEN
      resolved.payload_json:=jsonb_set(resolved.payload_json,'{jobId}',to_jsonb(generated_job_id),true)
        ||jsonb_build_object('replayOf',resolved.source_event_id);
      INSERT INTO public.async_job(job_id,job_type,status,requested_by,payload_json,next_attempt_at)
      VALUES (generated_job_id,resolved.job_type,'REQUESTED',resolved.requested_by,resolved.payload_json,statement_timestamp());
      INSERT INTO public.event_outbox(event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,next_attempt_at)
      VALUES (generated_event_id,resolved.event_type,resolved.aggregate_type,generated_job_id,resolved.partition_key,
        resolved.payload_json,resolved.schema_version,statement_timestamp());
    END IF;
    INSERT INTO public.async_replay_item_audit(replay_batch_id,target_id,source_job_id,source_event_id,new_job_id,new_event_id)
    VALUES (p_replay_batch_id,resolved.target_id,resolved.source_job_id,resolved.source_event_id,generated_job_id,generated_event_id);
    target_id:=resolved.target_id;source_job_id:=resolved.source_job_id;source_event_id:=resolved.source_event_id;
    new_job_id:=generated_job_id;new_event_id:=generated_event_id;
    outcome:=CASE WHEN p_execute THEN 'EXECUTED' ELSE 'DRY_RUN' END;RETURN NEXT;
  END LOOP;
END
$replay_async_work$;

CREATE FUNCTION json_text_depth_within(p_value text,p_max_depth integer)
RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog
AS $json_text_depth_within$
DECLARE position integer; current_char text; in_string boolean:=false; escaped boolean:=false; depth integer:=0;
BEGIN
  IF p_max_depth NOT BETWEEN 1 AND 16 THEN RETURN false; END IF;
  FOR position IN 1..char_length(p_value) LOOP
    current_char:=substr(p_value,position,1);
    IF in_string THEN
      IF escaped THEN escaped:=false;
      ELSIF current_char='\\' THEN escaped:=true;
      ELSIF current_char='"' THEN in_string:=false;
      END IF;
    ELSIF current_char='"' THEN in_string:=true;
    ELSIF current_char IN ('{','[') THEN depth:=depth+1;IF depth>p_max_depth THEN RETURN false;END IF;
    ELSIF current_char IN ('}',']') THEN depth:=depth-1;IF depth<0 THEN RETURN false;END IF;
    END IF;
  END LOOP;
  RETURN NOT in_string AND NOT escaped AND depth=0;
END
$json_text_depth_within$;

CREATE OR REPLACE FUNCTION public.stage_synthetic_dashboard_view(
  p_artifact_id text,p_owner_user_id text,p_run_id text,p_file_name text,p_file_hash text,
  p_view_kind text,p_projection_text text,p_projection_hash text,p_as_of timestamptz,p_fresh_until timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $stage_synthetic_dashboard_view$
DECLARE changed integer; existing public.dashboard_artifact_staging%ROWTYPE; projection jsonb;
BEGIN
  IF session_user <> 'decision_demo' THEN RAISE EXCEPTION 'dashboard staging role denied' USING ERRCODE='42501'; END IF;
  IF p_projection_text IS NULL OR octet_length(p_projection_text) NOT BETWEEN 2 AND 524288
     OR NOT public.json_text_depth_within(p_projection_text,8) THEN
    RAISE EXCEPTION 'invalid synthetic dashboard projection' USING ERRCODE='22023';
  END IF;
  projection:=p_projection_text::jsonb;
  IF p_owner_user_id<>'usr_demo_user' OR p_artifact_id<>'artifact_s8_0ed32aac66088e495ae853bb'
     OR p_run_id<>'demo_s8_fake_e2e_0001'
     OR p_file_hash<>'sha256:0ed32aac66088e495ae853bbac98a35b2c4a22420138bdd58dcdbbb0d9d8ad02'
     OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST') OR p_projection_hash !~ '^sha256:[0-9a-f]{64}$'
     OR jsonb_typeof(projection)<>'object' OR p_projection_hash<>('sha256:'||encode(public.digest(p_projection_text,'sha256'),'hex'))
     OR p_fresh_until<p_as_of OR projection->'success'<>'true'::jsonb OR projection->'error'<>'null'::jsonb
     OR projection#>>'{data,evidenceMode}'<>'SYNTHETIC_DEMO' OR projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb
     OR projection#>>'{data,viewState}'<>'READY' OR (projection#>>'{data,asOf}')::timestamptz<>p_as_of
     OR (projection#>>'{data,freshUntil}')::timestamptz<>p_fresh_until OR projection#>>'{data,view,runId}'<>p_run_id
     OR p_as_of<>'2026-08-22T00:00:00Z'::timestamptz OR p_fresh_until<>'2026-09-21T00:00:00Z'::timestamptz
     OR (p_view_kind='MODEL_EVALUATION' AND (p_file_name<>'model-evaluation.json'
       OR p_projection_hash<>'sha256:984ff60fba0c795a77b47b4d9180a1f1f7b76a5c3d1fcbbb30bb3510cf322696'))
     OR (p_view_kind='BACKTEST' AND (p_file_name<>'backtest.json'
       OR projection#>>'{data,view,fixtureClass}'<>'SYNTHETIC_FAKE_E2E'
       OR p_projection_hash<>'sha256:0e754cd9ea9ede03de7b54274fcb0540e1aff7dcd4621ba698b506ab817f5d45')) THEN
    RAISE EXCEPTION 'invalid synthetic dashboard projection' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE user_id=p_owner_user_id AND status='ACTIVE') THEN RETURN false; END IF;
  SELECT * INTO existing FROM public.dashboard_artifact_staging WHERE artifact_id=p_artifact_id AND view_kind=p_view_kind;
  IF FOUND THEN
    IF existing.owner_user_id=p_owner_user_id AND existing.run_id=p_run_id AND existing.file_name=p_file_name
       AND existing.file_hash=p_file_hash AND existing.projection_text=p_projection_text
       AND existing.projection_hash=p_projection_hash AND existing.as_of=p_as_of AND existing.fresh_until=p_fresh_until THEN RETURN false; END IF;
    RAISE EXCEPTION 'synthetic dashboard identity conflict' USING ERRCODE='23505';
  END IF;
  INSERT INTO public.dashboard_artifact_staging(artifact_id,view_kind,owner_user_id,run_id,file_name,file_hash,
    schema_version,fixture_class,evidence_mode,projection_text,projection_hash,as_of,fresh_until)
  VALUES (p_artifact_id,p_view_kind,p_owner_user_id,p_run_id,p_file_name,p_file_hash,'1.0.0','SYNTHETIC_FAKE_E2E',
    'SYNTHETIC_DEMO',p_projection_text,p_projection_hash,p_as_of,p_fresh_until);
  GET DIAGNOSTICS changed=ROW_COUNT;RETURN changed=1;
END
$stage_synthetic_dashboard_view$;

ALTER FUNCTION issue_actor_request_capability(text) OWNER TO flyway;
ALTER FUNCTION consume_actor_request_capability(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION read_demo_credentials() OWNER TO flyway;
ALTER FUNCTION read_user_actor(text) OWNER TO flyway;
ALTER FUNCTION create_async_request_authorized(text,text,text,text,text,text,text,jsonb) OWNER TO flyway;
ALTER FUNCTION read_async_job_status_authorized(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer) OWNER TO flyway;
ALTER FUNCTION read_stream_metric_status_authorized(text,text,bigint) OWNER TO flyway;
ALTER FUNCTION read_dashboard_artifact_view_authorized(text,text,bigint,text,text) OWNER TO flyway;
ALTER FUNCTION read_dashboard_risk_view_authorized(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION read_dashboard_rag_sources_authorized(text,text,bigint,text) OWNER TO flyway;
ALTER FUNCTION list_artifact_ingest_status_authorized(text,text,bigint) OWNER TO flyway;
ALTER FUNCTION consume_current_actor_capability(text,text) OWNER TO flyway;
ALTER FUNCTION insert_principle_authorized(text,text,text,text,text,text,text,integer,timestamptz,timestamptz) OWNER TO flyway;
ALTER FUNCTION insert_principle_version_authorized(text,text,text,text,integer,text,text,text,text,jsonb,text[],timestamptz) OWNER TO flyway;
ALTER FUNCTION insert_principle_audit_authorized(text,text,text,text,text,integer,text[],timestamptz) OWNER TO flyway;
ALTER FUNCTION read_owned_principle_authorized(text,text,text) OWNER TO flyway;
ALTER FUNCTION list_owned_principles_authorized(text,text,integer,text,timestamptz,text) OWNER TO flyway;
ALTER FUNCTION update_owned_principle_authorized(text,text,text,integer,text,text,text,timestamptz) OWNER TO flyway;
ALTER FUNCTION list_owned_principle_versions_authorized(text,text,text,integer,text,integer) OWNER TO flyway;
ALTER FUNCTION read_active_owned_principle_snapshot_authorized(text,text,text) OWNER TO flyway;
ALTER FUNCTION lock_active_owned_principle_authorized(text,text,text,integer,text,text) OWNER TO flyway;
ALTER FUNCTION resolve_completed_async_event(text,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION record_kafka_poison(text,text,text,text,integer,bigint,integer,text,text) OWNER TO flyway;
ALTER FUNCTION authorize_async_replay(text,text,bigint,text,text,text[],integer,text,boolean,timestamptz,timestamptz) OWNER TO flyway;
ALTER FUNCTION json_text_depth_within(text,integer) OWNER TO flyway;

REVOKE ALL ON TABLE actor_request_capability,async_replay_authorization,kafka_poison_receipt FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_demo;
GRANT SELECT,INSERT,UPDATE ON TABLE actor_request_capability,async_replay_authorization,kafka_poison_receipt TO flyway;
GRANT DELETE ON TABLE actor_request_capability TO flyway;
GRANT SELECT ON TABLE users,principle_presets,principles,principle_versions TO flyway;
GRANT INSERT ON TABLE principles,principle_versions,audit_logs TO flyway;
GRANT UPDATE (title,mode,status,current_version,updated_at) ON TABLE principles TO flyway;
REVOKE ALL ON FUNCTION issue_actor_request_capability(text),consume_actor_request_capability(text,text,bigint,text),
  read_demo_credentials(),read_user_actor(text),
  create_async_request_authorized(text,text,text,text,text,text,text,jsonb),
  read_async_job_status_authorized(text,text,bigint,text),
  list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer),
  read_stream_metric_status_authorized(text,text,bigint),
  read_dashboard_artifact_view_authorized(text,text,bigint,text,text),
  read_dashboard_risk_view_authorized(text,text,bigint,text),
  read_dashboard_rag_sources_authorized(text,text,bigint,text),
  list_artifact_ingest_status_authorized(text,text,bigint),
  consume_current_actor_capability(text,text),
  insert_principle_authorized(text,text,text,text,text,text,text,integer,timestamptz,timestamptz),
  insert_principle_version_authorized(text,text,text,text,integer,text,text,text,text,jsonb,text[],timestamptz),
  insert_principle_audit_authorized(text,text,text,text,text,integer,text[],timestamptz),
  read_owned_principle_authorized(text,text,text),
  list_owned_principles_authorized(text,text,integer,text,timestamptz,text),
  update_owned_principle_authorized(text,text,text,integer,text,text,text,timestamptz),
  list_owned_principle_versions_authorized(text,text,text,integer,text,integer),
  read_active_owned_principle_snapshot_authorized(text,text,text),
  lock_active_owned_principle_authorized(text,text,text,integer,text,text),
  resolve_completed_async_event(text,text,text,text,text),
  record_kafka_poison(text,text,text,text,integer,bigint,integer,text,text),
  authorize_async_replay(text,text,bigint,text,text,text[],integer,text,boolean,timestamptz,timestamptz)
FROM PUBLIC;

REVOKE EXECUTE ON FUNCTION create_async_job(text,text,text,jsonb),append_async_request_outbox(text,text,text,text,jsonb),
  read_async_job_status(text,bigint,text),list_async_job_status(text,bigint,text,text,timestamptz,text,integer),
  read_stream_metric_status(text,bigint),read_dashboard_artifact_view(text,bigint,text,text),
  read_dashboard_risk_view(text,bigint,text),read_dashboard_rag_sources(text,bigint,text),
  list_artifact_ingest_status(text,bigint) FROM decision_app;
REVOKE EXECUTE ON FUNCTION claim_async_job_by_id(text,text) FROM decision_worker;

GRANT EXECUTE ON FUNCTION read_demo_credentials(),read_user_actor(text),
  create_async_request_authorized(text,text,text,text,text,text,text,jsonb),
  read_async_job_status_authorized(text,text,bigint,text),
  list_async_job_status_authorized(text,text,bigint,text,text,timestamptz,text,integer),
  read_stream_metric_status_authorized(text,text,bigint),
  read_dashboard_artifact_view_authorized(text,text,bigint,text,text),
  read_dashboard_risk_view_authorized(text,text,bigint,text),
  read_dashboard_rag_sources_authorized(text,text,bigint,text),
  list_artifact_ingest_status_authorized(text,text,bigint) TO decision_app;
GRANT EXECUTE ON FUNCTION
  insert_principle_authorized(text,text,text,text,text,text,text,integer,timestamptz,timestamptz),
  insert_principle_version_authorized(text,text,text,text,integer,text,text,text,text,jsonb,text[],timestamptz),
  insert_principle_audit_authorized(text,text,text,text,text,integer,text[],timestamptz),
  read_owned_principle_authorized(text,text,text),
  list_owned_principles_authorized(text,text,integer,text,timestamptz,text),
  update_owned_principle_authorized(text,text,text,integer,text,text,text,timestamptz),
  list_owned_principle_versions_authorized(text,text,text,integer,text,integer),
  read_active_owned_principle_snapshot_authorized(text,text,text),
  lock_active_owned_principle_authorized(text,text,text,integer,text,text)
TO decision_app;
GRANT EXECUTE ON FUNCTION claim_async_job_by_event(text,text,text,text,text,text),
  resolve_completed_async_event(text,text,text,text,text),
  record_kafka_poison(text,text,text,text,integer,bigint,integer,text,text) TO decision_worker;

DO $role_grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_identity') THEN
    GRANT EXECUTE ON FUNCTION issue_actor_request_capability(text) TO decision_identity;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_replay_authorizer') THEN
    GRANT EXECUTE ON FUNCTION authorize_async_replay(text,text,bigint,text,text,text[],integer,text,boolean,timestamptz,timestamptz)
    TO decision_replay_authorizer;
  END IF;
END
$role_grants$;

REVOKE SELECT ON TABLE users FROM decision_app;
REVOKE SELECT,INSERT,UPDATE,DELETE ON TABLE principles,principle_versions FROM decision_app;
