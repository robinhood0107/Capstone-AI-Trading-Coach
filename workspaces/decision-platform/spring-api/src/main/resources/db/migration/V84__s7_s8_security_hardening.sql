-- Final S7/S8 security hardening is forward-only: runtime roles receive capabilities, never base-table DML.
ALTER TABLE public.event_outbox
  ADD COLUMN transport_payload_hash text,
  ADD CONSTRAINT event_outbox_transport_payload_hash_check CHECK (
    transport_payload_hash IS NULL OR transport_payload_hash ~ '^sha256:[0-9a-f]{64}$'
  );

CREATE OR REPLACE FUNCTION public.create_async_job(
  p_job_id text,p_job_type text,p_requested_by text,p_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $create_async_job$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'async job create role denied' USING ERRCODE='42501'; END IF;
  IF p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$'
     OR p_job_type NOT IN ('RAG_INDEX','ARTIFACT_INGEST','MODEL_EVAL')
     OR p_requested_by !~ '^usr_[A-Za-z0-9_-]{8,64}$'
     OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR octet_length(p_payload::text)>32768
     OR p_payload->>'jobId' IS DISTINCT FROM p_job_id
     OR p_payload->>'ownerRef' IS DISTINCT FROM p_requested_by
     OR EXISTS (SELECT 1 FROM jsonb_each(p_payload) item
                WHERE jsonb_typeof(item.value)<>'string' OR octet_length(item.value#>>'{}')>2048)
     OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                WHERE key NOT IN ('jobId','ownerRef','sourceId','sourceRevisionId','importTicketId','profileId',
                                  'artifactId','runId','contentHash')) THEN
    RAISE EXCEPTION 'invalid async job request' USING ERRCODE='22023';
  END IF;
  IF (p_job_type='RAG_INDEX' AND (
        coalesce(p_payload->>'sourceId','') !~ '^src_[A-Za-z0-9_-]{3,96}$'
        OR coalesce(p_payload->>'sourceRevisionId','') !~ '^srv_[A-Za-z0-9_-]{3,96}$'
        OR coalesce(p_payload->>'importTicketId','') !~ '^rti_[0-9a-f]{32}$'
        OR coalesce(p_payload->>'profileId','') NOT IN ('bge_m3_local_1024_v1','voyage_context_4_1024_v1')
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','sourceId','sourceRevisionId','importTicketId','profileId'))
      ))
     OR (p_job_type='ARTIFACT_INGEST' AND (
        coalesce(p_payload->>'artifactId','') !~ '^artifact_[A-Za-z0-9_-]{8,96}$'
        OR coalesce(p_payload->>'contentHash','') !~ '^sha256:[0-9a-f]{64}$'
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','artifactId','contentHash'))
      ))
     OR (p_job_type='MODEL_EVAL' AND (
        coalesce(p_payload->>'runId','') !~ '^(run|demo)_[A-Za-z0-9_-]{8,96}$'
        OR coalesce(p_payload->>'contentHash','') !~ '^sha256:[0-9a-f]{64}$'
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','runId','contentHash'))
      )) THEN
    RAISE EXCEPTION 'async job schema mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor
                 WHERE actor.user_id=p_requested_by AND actor.status='ACTIVE') THEN RETURN false; END IF;
  INSERT INTO public.async_job(job_id,job_type,status,requested_by,payload_json,next_attempt_at)
  VALUES (p_job_id,p_job_type,'REQUESTED',p_requested_by,p_payload,statement_timestamp());
  RETURN true;
EXCEPTION WHEN unique_violation THEN RETURN false;
END
$create_async_job$;

CREATE FUNCTION public.append_async_request_outbox(
  p_event_id text,p_event_type text,p_job_id text,p_partition_key text,p_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $append_async_request_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async outbox append role denied' USING ERRCODE='42501';
  END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$'
     OR p_event_type NOT IN ('rag.index-requested.v1','artifact.ingest-requested.v1','model.eval-requested.v1')
     OR p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object'
     OR octet_length(p_payload::text) > 32768
     OR p_payload ->> 'jobId' IS DISTINCT FROM p_job_id
     OR coalesce(p_payload ->> 'ownerRef','') !~ '^usr_[A-Za-z0-9_-]{8,64}$'
     OR EXISTS (
       SELECT 1 FROM jsonb_each(p_payload) item
       WHERE jsonb_typeof(item.value) <> 'string' OR octet_length(item.value #>> '{}') > 2048
     )
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_payload) key
       WHERE key NOT IN ('jobId','ownerRef','sourceId','sourceRevisionId','importTicketId','profileId',
                         'artifactId','runId','contentHash')
     ) THEN
    RAISE EXCEPTION 'invalid async outbox payload' USING ERRCODE='22023';
  END IF;
  IF (p_event_type='rag.index-requested.v1' AND (
        coalesce(p_payload ->> 'sourceId','') !~ '^src_[A-Za-z0-9_-]{3,96}$'
        OR coalesce(p_payload ->> 'sourceRevisionId','') !~ '^srv_[A-Za-z0-9_-]{3,96}$'
        OR coalesce(p_payload ->> 'importTicketId','') !~ '^rti_[0-9a-f]{32}$'
        OR coalesce(p_payload ->> 'profileId','') NOT IN ('bge_m3_local_1024_v1','voyage_context_4_1024_v1')
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','sourceId','sourceRevisionId','importTicketId','profileId'))
      ))
     OR (p_event_type='artifact.ingest-requested.v1' AND (
        coalesce(p_payload ->> 'artifactId','') !~ '^artifact_[A-Za-z0-9_-]{8,96}$'
        OR coalesce(p_payload ->> 'contentHash','') !~ '^sha256:[0-9a-f]{64}$'
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','artifactId','contentHash'))
      ))
     OR (p_event_type='model.eval-requested.v1' AND (
        coalesce(p_payload ->> 'runId','') !~ '^(run|demo)_[A-Za-z0-9_-]{8,96}$'
        OR coalesce(p_payload ->> 'contentHash','') !~ '^sha256:[0-9a-f]{64}$'
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) key
                   WHERE key NOT IN ('jobId','ownerRef','runId','contentHash'))
      )) THEN
    RAISE EXCEPTION 'async outbox event schema mismatch' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.async_job job
    WHERE job.job_id=p_job_id AND job.status='REQUESTED'
      AND job.requested_by=p_payload->>'ownerRef' AND job.payload_json=p_payload
      AND job.job_type=CASE p_event_type
        WHEN 'rag.index-requested.v1' THEN 'RAG_INDEX'
        WHEN 'artifact.ingest-requested.v1' THEN 'ARTIFACT_INGEST'
        WHEN 'model.eval-requested.v1' THEN 'MODEL_EVAL'
      END
  ) THEN
    RAISE EXCEPTION 'async outbox is not bound to job' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version
  ) VALUES (p_event_id,p_event_type,'ASYNC_JOB',p_job_id,p_partition_key,p_payload,'1.0.0');
  RETURN true;
END
$append_async_request_outbox$;

CREATE FUNCTION public.append_decision_created_outbox(
  p_event_id text,p_decision_id text,p_payload jsonb,p_created_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $append_decision_created_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'decision outbox append role denied' USING ERRCODE='42501'; END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_decision_id !~ '^[A-Za-z0-9_-]{3,128}$'
     OR p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object' OR octet_length(p_payload::text) > 4096
     OR p_payload ->> 'decisionId' IS DISTINCT FROM p_decision_id
     OR p_payload ->> 'evaluationId' !~ '^[A-Za-z0-9_-]{3,128}$'
     OR p_payload ->> 'outcome' NOT IN ('ALLOW','WARN','BLOCK','HOLD')
     OR p_payload ->> 'principleVersionId' !~ '^[A-Za-z0-9_-]{3,128}$'
     OR p_payload ->> 'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_payload ->> 'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p_payload) key)
        <> ARRAY['decisionId','evaluationId','outcome','principleVersionId','semanticInputHash','snapshotArtifactHash'] THEN
    RAISE EXCEPTION 'invalid decision outbox payload' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,status,retry_count,created_at,updated_at
  ) VALUES (p_event_id,'risk.decision-created.v1','DECISION',p_decision_id,p_decision_id,p_payload,'1.0.0','PENDING',0,p_created_at,p_created_at);
  RETURN true;
END
$append_decision_created_outbox$;

CREATE FUNCTION public.append_kill_switch_outbox(
  p_event_id text,p_active boolean,p_changed_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $append_kill_switch_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'kill switch outbox append role denied' USING ERRCODE='42501'; END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_changed_at IS NULL THEN
    RAISE EXCEPTION 'invalid kill switch outbox payload' USING ERRCODE='22023';
  END IF;
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    p_event_id,'kill-switch.changed','KILL_SWITCH','GLOBAL','GLOBAL',
    jsonb_build_object('active',p_active,'changedAt',p_changed_at::text),'1.0.0','PENDING',0,p_changed_at,p_changed_at
  );
  RETURN true;
END
$append_kill_switch_outbox$;

CREATE FUNCTION public.bind_claimed_outbox_payload_hash(p_event_id text,p_claim_token uuid,p_payload_hash text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $bind_claimed_outbox_payload_hash$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'outbox hash bind role denied' USING ERRCODE='42501'; END IF;
  IF p_payload_hash !~ '^sha256:[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid outbox payload hash' USING ERRCODE='22023'; END IF;
  UPDATE public.event_outbox SET transport_payload_hash=p_payload_hash,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND status IN ('PENDING','FAILED')
    AND lease_expires_at>statement_timestamp()
    AND (transport_payload_hash IS NULL OR transport_payload_hash=p_payload_hash);
  GET DIAGNOSTICS changed=ROW_COUNT;
  RETURN changed=1;
END
$bind_claimed_outbox_payload_hash$;

CREATE FUNCTION public.claim_async_job_by_event(
  p_worker text,p_event_id text,p_event_type text,p_job_id text,p_payload_hash text,p_partition_key text
)
RETURNS TABLE(job_id text,job_type text,payload_json jsonb,claim_token uuid,attempt_count integer,hard_deadline_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $claim_async_job_by_event$
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'Kafka async claim role denied' USING ERRCODE='42501'; END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$' OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid Kafka async claim' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.event_outbox source
    WHERE source.event_id=p_event_id AND source.event_type=p_event_type
      AND source.aggregate_type='ASYNC_JOB' AND source.aggregate_id=p_job_id
      AND source.partition_key=p_partition_key AND source.schema_version='1.0.0'
      AND source.transport_payload_hash=p_payload_hash
      AND source.status IN ('PENDING','FAILED','PUBLISHED')
  ) THEN
    RAISE EXCEPTION 'Kafka event is not bound to outbox' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  WITH candidate AS (
    SELECT item.job_id FROM public.async_job item
    WHERE item.job_id=p_job_id AND item.status IN ('REQUESTED','FAILED')
      AND item.attempt_count<3 AND item.next_attempt_at<=statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at<=statement_timestamp())
    FOR UPDATE SKIP LOCKED
  ), claimed AS (
    UPDATE public.async_job item SET status='RUNNING',claim_token=gen_random_uuid(),claimed_by=p_worker,
      lease_expires_at=statement_timestamp()+interval '5 minutes',heartbeat_at=statement_timestamp(),
      hard_deadline_at=statement_timestamp()+interval '15 minutes',attempt_count=item.attempt_count+1,
      started_at=coalesce(item.started_at,statement_timestamp()),updated_at=statement_timestamp()
    FROM candidate WHERE item.job_id=candidate.job_id RETURNING item.*
  ) SELECT claimed.job_id,claimed.job_type,claimed.payload_json,claimed.claim_token,
      claimed.attempt_count,claimed.hard_deadline_at FROM claimed;
END
$claim_async_job_by_event$;

CREATE OR REPLACE FUNCTION public.commit_async_work(
  p_event_id text,p_event_type text,p_consumer_name text,p_payload_hash text,p_job_id text,p_claim_token uuid,
  p_result_ref text,p_completion_event_id text,p_partition_key text
)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $commit_async_work$
#variable_conflict use_column
DECLARE item public.async_job%ROWTYPE; existing_hash text; completion_type text; existing_status text;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'async work commit role denied' USING ERRCODE='42501'; END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_consumer_name !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$'
     OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$' OR p_result_ref !~ '^[A-Za-z][A-Za-z0-9_-]{7,127}$'
     OR p_completion_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid async work commit' USING ERRCODE='22023';
  END IF;
  SELECT payload_hash INTO existing_hash FROM public.processed_event
  WHERE event_id=p_event_id AND consumer_name=p_consumer_name FOR UPDATE;
  IF FOUND THEN
    IF existing_hash IS DISTINCT FROM p_payload_hash THEN
      UPDATE public.processed_event SET payload_hash_conflict=true
      WHERE event_id=p_event_id AND consumer_name=p_consumer_name;
      RETURN 'PAYLOAD_CONFLICT';
    END IF;
    SELECT status INTO existing_status FROM public.async_job WHERE job_id=p_job_id;
    IF existing_status='COMPLETED' AND EXISTS (
      SELECT 1 FROM public.async_materialization_receipt WHERE event_id=p_event_id AND job_id=p_job_id
    ) THEN RETURN 'DUPLICATE'; END IF;
    RAISE EXCEPTION 'processed event exists without completed claim' USING ERRCODE='40001';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.event_outbox source
    WHERE source.event_id=p_event_id AND source.event_type=p_event_type
      AND source.aggregate_type='ASYNC_JOB' AND source.aggregate_id=p_job_id
      AND source.partition_key=p_partition_key AND source.schema_version='1.0.0'
      AND coalesce(source.transport_payload_hash,
        'sha256:'||encode(public.digest(source.payload_json::text,'sha256'),'hex'))=p_payload_hash
      AND source.status IN ('PENDING','FAILED','PUBLISHED')
  ) THEN RAISE EXCEPTION 'async event/outbox binding conflict' USING ERRCODE='42501'; END IF;
  SELECT * INTO item FROM public.async_job
  WHERE job_id=p_job_id AND claim_token=p_claim_token AND status='RUNNING'
    AND lease_expires_at>statement_timestamp() AND hard_deadline_at>statement_timestamp() FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'async work claim conflict' USING ERRCODE='40001'; END IF;
  completion_type:=CASE p_event_type WHEN 'rag.index-requested.v1' THEN 'rag.index-completed.v1'
    WHEN 'artifact.ingest-requested.v1' THEN 'artifact.ingested.v1'
    WHEN 'model.eval-requested.v1' THEN 'model.eval-completed.v1' ELSE NULL END;
  IF completion_type IS NULL
     OR (p_event_type='rag.index-requested.v1' AND item.job_type<>'RAG_INDEX')
     OR (p_event_type='artifact.ingest-requested.v1' AND item.job_type<>'ARTIFACT_INGEST')
     OR (p_event_type='model.eval-requested.v1' AND item.job_type<>'MODEL_EVAL') THEN
    RAISE EXCEPTION 'async event and job type conflict' USING ERRCODE='22023';
  END IF;
  IF (item.job_type='RAG_INDEX' AND (
      coalesce(item.payload_json->>'sourceId','') !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
      OR coalesce(item.payload_json->>'sourceRevisionId','') !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
      OR coalesce(item.payload_json->>'importTicketId','') !~ '^rti_[0-9a-f]{32}$'))
     OR (item.job_type='ARTIFACT_INGEST' AND (coalesce(item.payload_json->>'artifactId','') !~ '^artifact_[A-Za-z0-9_-]{8,96}$'
      OR coalesce(item.payload_json->>'contentHash','') !~ '^sha256:[0-9a-f]{64}$'))
     OR (item.job_type='MODEL_EVAL' AND (coalesce(item.payload_json->>'runId','') !~ '^(run|demo)_[A-Za-z0-9_-]{8,96}$'
      OR coalesce(item.payload_json->>'contentHash','') !~ '^sha256:[0-9a-f]{64}$')) THEN
    RAISE EXCEPTION 'async work references are invalid' USING ERRCODE='22023';
  END IF;
  IF item.job_type='RAG_INDEX' AND NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_source_revisions source
    JOIN public.rag_v2_immutable_import_tickets ticket ON ticket.owner_user_id=source.owner_user_id
      AND ticket.ticket_hash=encode(public.digest(item.payload_json->>'importTicketId','sha256'),'hex')
      AND ticket.state='CONSUMED' AND ticket.embedding_profile_id='bge_m3_local_1024_v1'
    WHERE source.source_revision_id=item.payload_json->>'sourceRevisionId'
      AND source.source_id=item.payload_json->>'sourceId' AND source.owner_user_id=item.requested_by
      AND source.source_scope='OWNER_PRIVATE' AND source.local_processing_allowed
      AND item.payload_json->>'ownerRef'=item.requested_by
      AND item.payload_json->>'profileId'='bge_m3_local_1024_v1'
  ) THEN RAISE EXCEPTION 'RAG async request lacks consumed local owner evidence' USING ERRCODE='42501'; END IF;
  INSERT INTO public.processed_event(event_id,consumer_name,payload_hash)
  VALUES (p_event_id,p_consumer_name,p_payload_hash);
  INSERT INTO public.async_materialization_receipt(
    result_ref,event_id,job_id,job_type,source_revision_id,artifact_id,run_id,content_hash
  ) VALUES (p_result_ref,p_event_id,p_job_id,item.job_type,item.payload_json->>'sourceRevisionId',
    item.payload_json->>'artifactId',item.payload_json->>'runId',item.payload_json->>'contentHash');
  UPDATE public.async_job SET status='COMPLETED',result_json=jsonb_build_object('resultRef',p_result_ref),
    completed_at=statement_timestamp(),claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
    error_code=NULL,error_class=NULL,error_message=NULL,updated_at=statement_timestamp()
  WHERE job_id=p_job_id AND claim_token=p_claim_token;
  INSERT INTO public.event_outbox(event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version)
  VALUES (p_completion_event_id,completion_type,'ASYNC_JOB',p_job_id,p_partition_key,
    jsonb_build_object('jobId',p_job_id,'resultRef',p_result_ref),'1.0.0');
  RETURN 'COMPLETED';
END
$commit_async_work$;

CREATE OR REPLACE FUNCTION public.fail_async_job(p_job_id text,p_claim_token uuid,p_error_code text,p_error_class text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $fail_async_job$
DECLARE current_attempt integer; next_status text;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'async failure role denied' USING ERRCODE='42501'; END IF;
  IF p_error_code !~ '^[A-Z][A-Z0-9_]{2,63}$' OR p_error_class !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid async failure' USING ERRCODE='22023';
  END IF;
  SELECT attempt_count INTO current_attempt FROM public.async_job
  WHERE job_id=p_job_id AND claim_token=p_claim_token AND status='RUNNING'
    AND lease_expires_at>statement_timestamp() AND hard_deadline_at>statement_timestamp() FOR UPDATE;
  IF NOT FOUND THEN RETURN 'CONFLICT'; END IF;
  next_status:=CASE WHEN current_attempt>=3 THEN 'NEEDS_REVIEW' ELSE 'FAILED' END;
  UPDATE public.async_job SET status=next_status,claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
    error_code=p_error_code,error_class=p_error_class,error_message=p_error_code,
    next_attempt_at=statement_timestamp()+CASE current_attempt WHEN 1 THEN interval '1 second' ELSE interval '5 seconds' END,
    completed_at=CASE WHEN next_status='NEEDS_REVIEW' THEN statement_timestamp() ELSE completed_at END,
    updated_at=statement_timestamp()
  WHERE job_id=p_job_id AND claim_token=p_claim_token;
  RETURN next_status;
END
$fail_async_job$;

CREATE OR REPLACE FUNCTION public.quarantine_async_job(p_job_id text,p_claim_token uuid,p_error_code text,p_error_class text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $quarantine_async_job$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'async quarantine role denied' USING ERRCODE='42501'; END IF;
  IF p_error_code !~ '^[A-Z][A-Z0-9_]{2,63}$' OR p_error_class !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid async quarantine' USING ERRCODE='22023';
  END IF;
  UPDATE public.async_job SET status='NEEDS_REVIEW',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,
    error_code=p_error_code,error_class=p_error_class,error_message=p_error_code,updated_at=statement_timestamp()
  WHERE job_id=p_job_id AND claim_token=p_claim_token AND status='RUNNING'
    AND lease_expires_at>statement_timestamp() AND hard_deadline_at>statement_timestamp();
  GET DIAGNOSTICS changed=ROW_COUNT; RETURN changed=1;
END
$quarantine_async_job$;

CREATE OR REPLACE FUNCTION public.fail_dlq_outbox(p_event_id text,p_claim_token uuid)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $fail_dlq_outbox$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'DLQ failure role denied' USING ERRCODE='42501'; END IF;
  UPDATE public.event_outbox SET retry_count=retry_count+1,claim_token=NULL,claimed_by=NULL,
    lease_expires_at=NULL,next_attempt_at=statement_timestamp()+
      CASE retry_count WHEN 0 THEN interval '1 second' ELSE interval '5 seconds' END,updated_at=statement_timestamp()
  WHERE event_id=p_event_id AND claim_token=p_claim_token AND status='DLQ_REQUESTED'
    AND retry_count<3 AND lease_expires_at>statement_timestamp();
  GET DIAGNOSTICS changed=ROW_COUNT; RETURN changed=1;
END
$fail_dlq_outbox$;

-- A terminal DLQ delivery never contains the original payload. The storage event ID remains an internal fence.
DROP FUNCTION public.claim_dlq_outbox(text,integer);
CREATE FUNCTION public.claim_dlq_outbox(p_worker text,p_limit integer DEFAULT 100)
RETURNS TABLE(
  storage_event_id text,event_id text,event_type text,aggregate_type text,aggregate_id text,
  partition_key text,payload_json jsonb,occurred_at timestamptz,outbox_schema_version text,
  kafka_schema_version integer,topic_name text,claim_token uuid,attempt_count integer
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $claim_dlq_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN RAISE EXCEPTION 'DLQ outbox claim role denied' USING ERRCODE='42501'; END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_limit<1 OR p_limit>100 THEN
    RAISE EXCEPTION 'invalid DLQ claim request' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT item.event_id FROM public.event_outbox item JOIN public.async_event_registry registry
      ON registry.event_type=item.event_type AND registry.outbox_schema_version=item.schema_version
    WHERE item.status='DLQ_REQUESTED' AND item.retry_count<3 AND item.next_attempt_at<=statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at<=statement_timestamp())
    ORDER BY item.next_attempt_at,item.created_at,item.event_id FOR UPDATE OF item SKIP LOCKED LIMIT p_limit
  ), claimed AS (
    UPDATE public.event_outbox item SET claim_token=gen_random_uuid(),claimed_by=p_worker,
      lease_expires_at=statement_timestamp()+interval '30 seconds',updated_at=statement_timestamp()
    FROM candidates WHERE item.event_id=candidates.event_id RETURNING item.*
  )
  SELECT claimed.event_id,
    'evt_dlq_'||substr(encode(public.digest(claimed.event_id,'sha256'),'hex'),1,32),
    claimed.event_type,claimed.aggregate_type,claimed.aggregate_id,claimed.partition_key,
    jsonb_build_object(
      'eventId',claimed.event_id,'eventType',claimed.event_type,
      'payloadHash',coalesce(claimed.transport_payload_hash,claimed.payload_json->>'payloadHash',
        'sha256:'||encode(public.digest(claimed.payload_json::text,'sha256'),'hex')),
      'failureCode',coalesce(claimed.failure_code,'ASYNC_TERMINAL_FAILURE'),
      'sourceTopic',claimed.event_type,'attempt',least(3,claimed.retry_count+1)
    ),claimed.created_at,claimed.schema_version,registry.kafka_schema_version,
    regexp_replace(registry.topic_name,'\.v1$','.dlq.v1'),claimed.claim_token,least(3,claimed.retry_count+1)
  FROM claimed JOIN public.async_event_registry registry ON registry.event_type=claimed.event_type
  ORDER BY claimed.next_attempt_at,claimed.created_at,claimed.event_id;
END
$claim_dlq_outbox$;

CREATE OR REPLACE FUNCTION public.replay_async_work(
  p_actor_user_id text,p_security_version bigint,p_replay_batch_id text,p_target_kind text,p_target_ids text[],
  p_expected_count integer,p_reason_code text,p_packet_hash text,p_execute boolean
)
RETURNS TABLE(target_id text,source_job_id text,source_event_id text,new_job_id text,new_event_id text,outcome text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $replay_async_work$
DECLARE actor_role text; actor_status text; actor_security_version bigint; actual integer; resolved record;
  generated_job_id text; generated_event_id text;
BEGIN
  IF session_user <> 'decision_replay' THEN RAISE EXCEPTION 'async replay role denied' USING ERRCODE='42501'; END IF;
  IF p_replay_batch_id !~ '^replay_[0-9a-f]{32}$' OR p_target_kind NOT IN ('EVENT','JOB')
     OR p_target_ids IS NULL OR cardinality(p_target_ids) NOT BETWEEN 1 AND 100
     OR cardinality(p_target_ids)<>(SELECT count(DISTINCT value) FROM unnest(p_target_ids) value)
     OR p_expected_count NOT BETWEEN 1 AND 100 OR p_reason_code !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_packet_hash !~ '^sha256:[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid async replay request' USING ERRCODE='22023'; END IF;
  SELECT role,status,security_version INTO actor_role,actor_status,actor_security_version
    FROM public.users WHERE user_id=p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status<>'ACTIVE' OR actor_role<>'ADMIN' OR actor_security_version<>p_security_version THEN RETURN; END IF;
  DROP TABLE IF EXISTS pg_temp.s7_replay_targets;
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
    new_job_id:=generated_job_id;new_event_id:=generated_event_id;outcome:=CASE WHEN p_execute THEN 'EXECUTED' ELSE 'DRY_RUN' END;
    RETURN NEXT;
  END LOOP;
END
$replay_async_work$;

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
  projection:=p_projection_text::jsonb;
  IF p_owner_user_id<>'usr_demo_user'
     OR p_artifact_id<>'artifact_s8_0ed32aac66088e495ae853bb'
     OR p_run_id<>'demo_s8_fake_e2e_0001'
     OR p_file_hash<>'sha256:0ed32aac66088e495ae853bbac98a35b2c4a22420138bdd58dcdbbb0d9d8ad02'
     OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST') OR p_projection_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_projection_text IS NULL OR octet_length(p_projection_text) NOT BETWEEN 2 AND 524288
     OR jsonb_typeof(projection)<>'object' OR p_projection_hash<>('sha256:'||encode(public.digest(p_projection_text,'sha256'),'hex'))
     OR p_fresh_until<p_as_of OR projection->'success'<>'true'::jsonb OR projection->'error'<>'null'::jsonb
     OR projection#>>'{data,evidenceMode}'<>'SYNTHETIC_DEMO'
     OR projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb
     OR projection#>>'{data,viewState}'<>'READY'
     OR (projection#>>'{data,asOf}')::timestamptz<>p_as_of
     OR (projection#>>'{data,freshUntil}')::timestamptz<>p_fresh_until
     OR projection#>>'{data,view,runId}'<>p_run_id
     OR p_as_of<>'2026-08-22T00:00:00Z'::timestamptz
     OR p_fresh_until<>'2026-09-21T00:00:00Z'::timestamptz
     OR (p_view_kind='MODEL_EVALUATION' AND (
       p_file_name<>'model-evaluation.json'
       OR p_projection_hash<>'sha256:984ff60fba0c795a77b47b4d9180a1f1f7b76a5c3d1fcbbb30bb3510cf322696'
     ))
     OR (p_view_kind='BACKTEST' AND (
       p_file_name<>'backtest.json'
       OR projection#>>'{data,view,fixtureClass}'<>'SYNTHETIC_FAKE_E2E'
       OR p_projection_hash<>'sha256:0e754cd9ea9ede03de7b54274fcb0540e1aff7dcd4621ba698b506ab817f5d45'
     )) THEN
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

CREATE OR REPLACE FUNCTION public.materialize_dashboard_artifact_receipt()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $materialize_dashboard_artifact_receipt$
DECLARE staged record; job public.async_job%ROWTYPE;
BEGIN
  IF NEW.job_type<>'ARTIFACT_INGEST' OR NEW.artifact_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO job FROM public.async_job WHERE job_id=NEW.job_id;
  IF NOT FOUND OR job.status<>'RUNNING' OR job.requested_by IS NULL
     OR job.payload_json->>'artifactId' IS DISTINCT FROM NEW.artifact_id
     OR job.payload_json->>'contentHash' IS DISTINCT FROM NEW.content_hash THEN
    RAISE EXCEPTION 'dashboard receipt is not bound to async job' USING ERRCODE='42501';
  END IF;
  FOR staged IN SELECT * FROM public.dashboard_artifact_staging
    WHERE artifact_id=NEW.artifact_id AND owner_user_id=job.requested_by AND file_hash=NEW.content_hash ORDER BY view_kind LOOP
    INSERT INTO public.dashboard_artifact_views(artifact_id,view_kind,owner_user_id,run_id,fixture_class,evidence_mode,
      projection_json,projection_hash,as_of,fresh_until)
    VALUES (staged.artifact_id,staged.view_kind,staged.owner_user_id,staged.run_id,staged.fixture_class,staged.evidence_mode,
      staged.projection_text::jsonb,staged.projection_hash,staged.as_of,staged.fresh_until) ON CONFLICT DO NOTHING;
    INSERT INTO public.artifact_ingest_projection(artifact_id,owner_user_id,file_name,producer,run_id,file_hash,
      schema_version,status,last_ingested_at,duplicate)
    VALUES (staged.artifact_id,staged.owner_user_id,staged.file_name,'decision-platform',staged.run_id,staged.file_hash,
      staged.schema_version,'INGESTED',statement_timestamp(),false) ON CONFLICT DO NOTHING;
  END LOOP;
  RETURN NEW;
END
$materialize_dashboard_artifact_receipt$;

ALTER FUNCTION public.append_async_request_outbox(text,text,text,text,jsonb) OWNER TO flyway;
ALTER FUNCTION public.create_async_job(text,text,text,jsonb) OWNER TO flyway;
ALTER FUNCTION public.append_decision_created_outbox(text,text,jsonb,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.append_kill_switch_outbox(text,boolean,timestamptz) OWNER TO flyway;
ALTER FUNCTION public.bind_claimed_outbox_payload_hash(text,uuid,text) OWNER TO flyway;
ALTER FUNCTION public.claim_async_job_by_event(text,text,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.claim_dlq_outbox(text,integer) OWNER TO flyway;
ALTER FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) OWNER TO flyway;
ALTER FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz) OWNER TO flyway;

GRANT UPDATE (payload_hash_conflict) ON TABLE public.processed_event TO flyway;
REVOKE INSERT ON TABLE public.event_outbox FROM decision_app;
REVOKE INSERT ON TABLE public.processed_event FROM decision_worker;
REVOKE ALL ON FUNCTION public.append_async_request_outbox(text,text,text,text,jsonb),
  public.create_async_job(text,text,text,jsonb),
  public.append_decision_created_outbox(text,text,jsonb,timestamptz),
  public.append_kill_switch_outbox(text,boolean,timestamptz),
  public.bind_claimed_outbox_payload_hash(text,uuid,text),
  public.claim_async_job_by_event(text,text,text,text,text,text),
  public.claim_dlq_outbox(text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.append_async_request_outbox(text,text,text,text,jsonb),
  public.create_async_job(text,text,text,jsonb),
  public.append_decision_created_outbox(text,text,jsonb,timestamptz),
  public.append_kill_switch_outbox(text,boolean,timestamptz),
  public.bind_claimed_outbox_payload_hash(text,uuid,text),
  public.claim_dlq_outbox(text,integer) TO decision_app;
GRANT EXECUTE ON FUNCTION public.claim_async_job_by_event(text,text,text,text,text,text) TO decision_worker;
REVOKE EXECUTE ON FUNCTION public.claim_async_jobs(text,integer),
  public.complete_async_job(text,uuid,jsonb),
  public.quarantine_async_job(text,uuid,text,text) FROM decision_worker;
REVOKE EXECUTE ON FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) FROM decision_app;
REVOKE EXECUTE ON FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz) FROM decision_app;

DO $roles$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_replay') THEN
    GRANT EXECUTE ON FUNCTION public.replay_async_work(text,bigint,text,text,text[],integer,text,text,boolean) TO decision_replay;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='decision_demo') THEN
    GRANT EXECUTE ON FUNCTION public.stage_synthetic_dashboard_view(text,text,text,text,text,text,text,text,timestamptz,timestamptz) TO decision_demo;
  END IF;
END
$roles$;
