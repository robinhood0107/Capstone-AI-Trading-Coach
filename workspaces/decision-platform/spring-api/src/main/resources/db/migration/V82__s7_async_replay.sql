-- S7.4 replay is an append-only internal operator capability, never a public REST route.
CREATE TABLE public.async_replay_audit (
  replay_batch_id text PRIMARY KEY,
  actor_user_id text NOT NULL,
  actor_security_version bigint NOT NULL CHECK (actor_security_version > 0),
  target_kind text NOT NULL CHECK (target_kind IN ('EVENT', 'JOB')),
  expected_count integer NOT NULL CHECK (expected_count BETWEEN 1 AND 100),
  actual_count integer NOT NULL CHECK (actual_count BETWEEN 0 AND 100),
  dry_run boolean NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('DRY_RUN', 'EXECUTED', 'COUNT_MISMATCH')),
  reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
  packet_hash text NOT NULL CHECK (packet_hash ~ '^sha256:[0-9a-f]{64}$'),
  occurred_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE public.async_replay_item_audit (
  replay_batch_id text NOT NULL REFERENCES public.async_replay_audit(replay_batch_id) ON DELETE RESTRICT,
  target_id text NOT NULL,
  source_job_id text NOT NULL,
  source_event_id text NOT NULL,
  new_job_id text,
  new_event_id text,
  PRIMARY KEY (replay_batch_id, target_id),
  CHECK ((new_job_id IS NULL) = (new_event_id IS NULL))
);

CREATE TRIGGER async_replay_audit_append_only
BEFORE UPDATE OR DELETE ON public.async_replay_audit
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER async_replay_item_audit_append_only
BEFORE UPDATE OR DELETE ON public.async_replay_item_audit
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE FUNCTION public.replay_async_work(
  p_actor_user_id text,
  p_security_version bigint,
  p_replay_batch_id text,
  p_target_kind text,
  p_target_ids text[],
  p_expected_count integer,
  p_reason_code text,
  p_packet_hash text,
  p_execute boolean
)
RETURNS TABLE(target_id text, source_job_id text, source_event_id text, new_job_id text, new_event_id text, outcome text)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $replay_async_work$
DECLARE
  actor_role text; actor_status text; actor_security_version bigint;
  actual integer; resolved record; generated_job_id text; generated_event_id text;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async replay role denied' USING ERRCODE = '42501';
  END IF;
  IF p_replay_batch_id !~ '^replay_[0-9a-f]{32}$'
     OR p_target_kind NOT IN ('EVENT', 'JOB')
     OR p_target_ids IS NULL OR cardinality(p_target_ids) NOT BETWEEN 1 AND 100
     OR cardinality(p_target_ids) <> (SELECT count(DISTINCT value) FROM unnest(p_target_ids) value)
     OR p_expected_count NOT BETWEEN 1 AND 100
     OR p_reason_code !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_packet_hash !~ '^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid async replay request' USING ERRCODE = '22023';
  END IF;
  SELECT role, users.status, security_version INTO actor_role, actor_status, actor_security_version
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status <> 'ACTIVE' OR actor_role <> 'ADMIN'
     OR actor_security_version <> p_security_version THEN
    RETURN;
  END IF;
  DROP TABLE IF EXISTS pg_temp.s7_replay_targets;
  CREATE TEMP TABLE pg_temp.s7_replay_targets ON COMMIT DROP AS
    SELECT requested.target_id, job.job_id AS source_job_id, event.event_id AS source_event_id,
           job.job_type, job.requested_by, job.payload_json, event.event_type,
           event.aggregate_type, event.partition_key, event.schema_version
    FROM unnest(p_target_ids) requested(target_id)
    JOIN public.async_job job ON (
      (p_target_kind = 'JOB' AND job.job_id = requested.target_id)
      OR (p_target_kind = 'EVENT' AND EXISTS (
        SELECT 1 FROM public.event_outbox lookup
        WHERE lookup.event_id = requested.target_id AND lookup.aggregate_id = job.job_id
      ))
    )
    JOIN LATERAL (
      SELECT candidate.* FROM public.event_outbox candidate
      WHERE candidate.aggregate_id = job.job_id
        AND candidate.event_type IN ('rag.index-requested.v1','artifact.ingest-requested.v1','model.eval-requested.v1')
        AND (p_target_kind = 'JOB' OR candidate.event_id = requested.target_id)
      ORDER BY candidate.created_at, candidate.event_id LIMIT 1
    ) event ON true
    WHERE job.status IN ('FAILED', 'NEEDS_REVIEW') OR event.status IN ('FAILED', 'DLQ_REQUESTED');
  SELECT count(*) INTO actual FROM pg_temp.s7_replay_targets;
  INSERT INTO public.async_replay_audit(
    replay_batch_id,actor_user_id,actor_security_version,target_kind,expected_count,actual_count,
    dry_run,outcome,reason_code,packet_hash
  ) VALUES (
    p_replay_batch_id,p_actor_user_id,p_security_version,p_target_kind,p_expected_count,actual,
    NOT p_execute,CASE WHEN actual <> p_expected_count THEN 'COUNT_MISMATCH'
      WHEN p_execute THEN 'EXECUTED' ELSE 'DRY_RUN' END,p_reason_code,p_packet_hash
  );
  IF actual <> p_expected_count THEN
    RETURN QUERY SELECT requested.target_id, NULL::text, NULL::text, NULL::text, NULL::text, 'COUNT_MISMATCH'::text
      FROM unnest(p_target_ids) requested(target_id) ORDER BY requested.target_id;
    RETURN;
  END IF;
  FOR resolved IN SELECT * FROM pg_temp.s7_replay_targets ORDER BY target_id LOOP
    generated_job_id := CASE WHEN p_execute THEN 'job_' || replace(gen_random_uuid()::text, '-', '') ELSE NULL END;
    generated_event_id := CASE WHEN p_execute THEN 'evt_' || replace(gen_random_uuid()::text, '-', '') ELSE NULL END;
    IF p_execute THEN
      resolved.payload_json := jsonb_set(resolved.payload_json, '{jobId}', to_jsonb(generated_job_id), true)
        || jsonb_build_object('replayOf', resolved.source_event_id);
      INSERT INTO public.async_job(job_id,job_type,status,requested_by,payload_json,next_attempt_at)
      VALUES (generated_job_id,resolved.job_type,'REQUESTED',resolved.requested_by,resolved.payload_json,statement_timestamp());
      INSERT INTO public.event_outbox(
        event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,next_attempt_at
      ) VALUES (
        generated_event_id,resolved.event_type,resolved.aggregate_type,generated_job_id,resolved.partition_key,
        resolved.payload_json,resolved.schema_version,statement_timestamp()
      );
    END IF;
    INSERT INTO public.async_replay_item_audit(
      replay_batch_id,target_id,source_job_id,source_event_id,new_job_id,new_event_id
    ) VALUES (
      p_replay_batch_id,resolved.target_id,resolved.source_job_id,resolved.source_event_id,
      generated_job_id,generated_event_id
    );
    target_id := resolved.target_id; source_job_id := resolved.source_job_id;
    source_event_id := resolved.source_event_id; new_job_id := generated_job_id;
    new_event_id := generated_event_id; outcome := CASE WHEN p_execute THEN 'EXECUTED' ELSE 'DRY_RUN' END;
    RETURN NEXT;
  END LOOP;
END
$replay_async_work$;

ALTER FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) OWNER TO flyway;
REVOKE ALL ON TABLE public.async_replay_audit, public.async_replay_item_audit FROM PUBLIC, decision_app;
GRANT SELECT, INSERT ON TABLE public.async_replay_audit, public.async_replay_item_audit TO flyway;
REVOKE ALL ON FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) TO decision_app;
