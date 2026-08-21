-- S7 async는 V3 identity를 보존하고 claim/lease/fencing capability만 forward-only로 추가한다.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE event_outbox
  ADD COLUMN claim_token uuid,
  ADD COLUMN claimed_by text,
  ADD COLUMN lease_expires_at timestamptz,
  ADD COLUMN next_attempt_at timestamptz,
  ADD COLUMN failure_code text,
  ADD COLUMN error_class text;

UPDATE event_outbox
SET next_attempt_at = created_at
WHERE next_attempt_at IS NULL;

ALTER TABLE event_outbox
  ALTER COLUMN next_attempt_at SET NOT NULL,
  ALTER COLUMN next_attempt_at SET DEFAULT now(),
  ADD CONSTRAINT event_outbox_retry_cap_check CHECK (retry_count BETWEEN 0 AND 3),
  ADD CONSTRAINT event_outbox_claim_tuple_check CHECK (
    (claim_token IS NULL AND claimed_by IS NULL AND lease_expires_at IS NULL)
    OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL)
  ),
  ADD CONSTRAINT event_outbox_claimed_by_check CHECK (
    claimed_by IS NULL OR claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$'
  ),
  ADD CONSTRAINT event_outbox_failure_code_check CHECK (
    failure_code IS NULL OR (octet_length(failure_code) <= 128 AND failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$')
  ),
  ADD CONSTRAINT event_outbox_error_class_check CHECK (
    error_class IS NULL OR (octet_length(error_class) <= 128 AND error_class ~ '^[A-Z][A-Z0-9_]{2,63}$')
  ),
  ADD CONSTRAINT event_outbox_error_bound_check CHECK (
    last_error IS NULL OR octet_length(last_error) <= 128
  ),
  ADD CONSTRAINT event_outbox_payload_bound_check CHECK (
    octet_length(payload_json::text) <= 32768
  );

DROP INDEX idx_event_outbox_pending_created_at;
CREATE INDEX idx_event_outbox_claimable
  ON event_outbox(next_attempt_at, created_at, event_id)
  WHERE status IN ('PENDING', 'FAILED');

ALTER TABLE processed_event
  ADD COLUMN payload_hash_conflict boolean NOT NULL DEFAULT false;
UPDATE processed_event
SET payload_hash = 'sha256:' || repeat('0', 64)
WHERE payload_hash IS NULL;
ALTER TABLE processed_event
  ALTER COLUMN payload_hash SET NOT NULL,
  ADD CONSTRAINT processed_event_payload_hash_check CHECK (
    payload_hash ~ '^sha256:[0-9a-f]{64}$'
  );

ALTER TABLE async_job
  ADD COLUMN claim_token uuid,
  ADD COLUMN claimed_by text,
  ADD COLUMN lease_expires_at timestamptz,
  ADD COLUMN next_attempt_at timestamptz,
  ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
  ADD COLUMN error_code text,
  ADD COLUMN error_class text,
  ADD COLUMN heartbeat_at timestamptz,
  ADD COLUMN hard_deadline_at timestamptz;

UPDATE async_job
SET next_attempt_at = created_at
WHERE next_attempt_at IS NULL;

ALTER TABLE async_job
  ALTER COLUMN next_attempt_at SET NOT NULL,
  ALTER COLUMN next_attempt_at SET DEFAULT now(),
  ADD CONSTRAINT async_job_attempt_cap_check CHECK (attempt_count BETWEEN 0 AND 3),
  ADD CONSTRAINT async_job_claim_tuple_check CHECK (
    (claim_token IS NULL AND claimed_by IS NULL AND lease_expires_at IS NULL)
    OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL)
  ),
  ADD CONSTRAINT async_job_claimed_by_check CHECK (
    claimed_by IS NULL OR claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$'
  ),
  ADD CONSTRAINT async_job_error_code_check CHECK (
    error_code IS NULL OR (octet_length(error_code) <= 128 AND error_code ~ '^[A-Z][A-Z0-9_]{2,63}$')
  ),
  ADD CONSTRAINT async_job_error_class_check CHECK (
    error_class IS NULL OR (octet_length(error_class) <= 128 AND error_class ~ '^[A-Z][A-Z0-9_]{2,63}$')
  ),
  ADD CONSTRAINT async_job_error_bound_check CHECK (
    error_message IS NULL OR octet_length(error_message) <= 128
  ),
  ADD CONSTRAINT async_job_payload_bound_check CHECK (
    octet_length(payload_json::text) <= 32768 AND octet_length(result_json::text) <= 32768
  );

CREATE INDEX idx_async_job_claimable
  ON async_job(next_attempt_at, created_at, job_id)
  WHERE status IN ('REQUESTED', 'FAILED');

CREATE TABLE async_event_registry (
  event_type text PRIMARY KEY,
  outbox_schema_version text NOT NULL,
  kafka_schema_version integer NOT NULL,
  topic_name text NOT NULL UNIQUE,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (outbox_schema_version = '1.0.0'),
  CHECK (kafka_schema_version = 1),
  CHECK (topic_name = event_type),
  CHECK (event_type ~ '^[a-z0-9.-]+\.v1$')
);

INSERT INTO async_event_registry(event_type, outbox_schema_version, kafka_schema_version, topic_name)
VALUES
  ('artifact.ingest-requested.v1', '1.0.0', 1, 'artifact.ingest-requested.v1'),
  ('artifact.ingested.v1', '1.0.0', 1, 'artifact.ingested.v1'),
  ('signal.received.v1', '1.0.0', 1, 'signal.received.v1'),
  ('feature.updated.v1', '1.0.0', 1, 'feature.updated.v1'),
  ('lightgbm.signal-generated.v1', '1.0.0', 1, 'lightgbm.signal-generated.v1'),
  ('risk.context-updated.v1', '1.0.0', 1, 'risk.context-updated.v1'),
  ('risk.decision-created.v1', '1.0.0', 1, 'risk.decision-created.v1'),
  ('order.event-created.v1', '1.0.0', 1, 'order.event-created.v1'),
  ('rag.index-requested.v1', '1.0.0', 1, 'rag.index-requested.v1'),
  ('rag.index-completed.v1', '1.0.0', 1, 'rag.index-completed.v1'),
  ('model.eval-requested.v1', '1.0.0', 1, 'model.eval-requested.v1'),
  ('model.eval-completed.v1', '1.0.0', 1, 'model.eval-completed.v1');

CREATE TABLE event_outbox_transition_audit (
  transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id text NOT NULL,
  previous_status text NOT NULL,
  next_status text NOT NULL,
  failure_code text,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  CHECK (previous_status IN ('PENDING', 'PUBLISHED', 'FAILED', 'DLQ_REQUESTED')),
  CHECK (next_status IN ('PENDING', 'PUBLISHED', 'FAILED', 'DLQ_REQUESTED')),
  CHECK (previous_status <> next_status),
  CHECK (failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$')
);

CREATE TABLE async_job_transition_audit (
  transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id text NOT NULL,
  previous_status text NOT NULL,
  next_status text NOT NULL,
  attempt_count integer NOT NULL,
  failure_code text,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  CHECK (previous_status IN ('REQUESTED', 'RUNNING', 'COMPLETED', 'FAILED', 'NEEDS_REVIEW')),
  CHECK (next_status IN ('REQUESTED', 'RUNNING', 'COMPLETED', 'FAILED', 'NEEDS_REVIEW')),
  CHECK (previous_status <> next_status),
  CHECK (attempt_count BETWEEN 0 AND 3),
  CHECK (failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{2,63}$')
);

CREATE TABLE async_job_admin_read_audit (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_user_id text NOT NULL,
  target_owner_user_id text,
  job_id text NOT NULL,
  read_kind text NOT NULL CHECK (read_kind IN ('DETAIL', 'LIST')),
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE async_materialization_receipt (
  result_ref text PRIMARY KEY,
  event_id text NOT NULL,
  job_id text NOT NULL,
  job_type text NOT NULL,
  source_revision_id text,
  artifact_id text,
  run_id text,
  content_hash text,
  completed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, job_id),
  CHECK (result_ref ~ '^[A-Za-z][A-Za-z0-9_-]{7,127}$'),
  CHECK (job_type IN ('RAG_INDEX', 'ARTIFACT_INGEST', 'MODEL_EVAL')),
  CHECK (content_hash IS NULL OR content_hash ~ '^sha256:[0-9a-f]{64}$')
);

CREATE TABLE shedlock (
  name varchar(64) PRIMARY KEY,
  lock_until timestamptz NOT NULL,
  locked_at timestamptz NOT NULL,
  locked_by varchar(255) NOT NULL
);

-- definer owner는 migration role이며 runtime caller에게 base DML을 열지 않는다.
GRANT SELECT, UPDATE ON TABLE event_outbox, async_job TO flyway;
GRANT INSERT ON TABLE async_job TO flyway;
GRANT SELECT ON TABLE async_event_registry, users TO flyway;
GRANT INSERT ON TABLE event_outbox_transition_audit, async_job_transition_audit,
  async_job_admin_read_audit TO flyway;
GRANT SELECT, INSERT ON TABLE processed_event, async_materialization_receipt, event_outbox TO flyway;

CREATE FUNCTION guard_event_outbox_s7_update()
RETURNS trigger
LANGUAGE plpgsql
AS $guard_event_outbox_s7_update$
BEGIN
  IF NEW.event_id IS DISTINCT FROM OLD.event_id
     OR NEW.event_type IS DISTINCT FROM OLD.event_type
     OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
     OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
     OR NEW.partition_key IS DISTINCT FROM OLD.partition_key
     OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'event outbox immutable identity cannot change' USING ERRCODE = '42501';
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT (
      (OLD.status IN ('PENDING', 'FAILED') AND NEW.status IN ('PUBLISHED', 'FAILED', 'DLQ_REQUESTED'))
    ) THEN
      RAISE EXCEPTION 'invalid event outbox transition' USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.event_outbox_transition_audit(event_id, previous_status, next_status, failure_code)
    VALUES (OLD.event_id, OLD.status, NEW.status, NEW.failure_code);
  END IF;
  RETURN NEW;
END
$guard_event_outbox_s7_update$;

CREATE TRIGGER event_outbox_s7_update_guard
BEFORE UPDATE ON event_outbox
FOR EACH ROW EXECUTE FUNCTION guard_event_outbox_s7_update();

CREATE FUNCTION guard_async_job_s7_update()
RETURNS trigger
LANGUAGE plpgsql
AS $guard_async_job_s7_update$
BEGIN
  IF NEW.job_id IS DISTINCT FROM OLD.job_id
     OR NEW.job_type IS DISTINCT FROM OLD.job_type
     OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
     OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'async job immutable identity cannot change' USING ERRCODE = '42501';
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT (
      (OLD.status = 'REQUESTED' AND NEW.status IN ('RUNNING', 'FAILED'))
      OR (OLD.status = 'RUNNING' AND NEW.status IN ('COMPLETED', 'FAILED'))
      OR (OLD.status = 'FAILED' AND NEW.status IN ('RUNNING', 'NEEDS_REVIEW'))
    ) THEN
      RAISE EXCEPTION 'invalid async job transition' USING ERRCODE = '23514';
    END IF;
    INSERT INTO public.async_job_transition_audit(job_id, previous_status, next_status, attempt_count, failure_code)
    VALUES (OLD.job_id, OLD.status, NEW.status, NEW.attempt_count, NEW.error_code);
  END IF;
  RETURN NEW;
END
$guard_async_job_s7_update$;

CREATE TRIGGER async_job_s7_update_guard
BEFORE UPDATE ON async_job
FOR EACH ROW EXECUTE FUNCTION guard_async_job_s7_update();

CREATE FUNCTION reject_s7_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $reject_s7_append_only_mutation$
BEGIN
  RAISE EXCEPTION 'S7 audit is append-only' USING ERRCODE = '42501';
END
$reject_s7_append_only_mutation$;

CREATE TRIGGER event_outbox_transition_audit_append_only
BEFORE UPDATE OR DELETE ON event_outbox_transition_audit
FOR EACH ROW EXECUTE FUNCTION reject_s7_append_only_mutation();
CREATE TRIGGER async_job_transition_audit_append_only
BEFORE UPDATE OR DELETE ON async_job_transition_audit
FOR EACH ROW EXECUTE FUNCTION reject_s7_append_only_mutation();
CREATE TRIGGER async_job_admin_read_audit_append_only
BEFORE UPDATE OR DELETE ON async_job_admin_read_audit
FOR EACH ROW EXECUTE FUNCTION reject_s7_append_only_mutation();
CREATE TRIGGER async_materialization_receipt_append_only
BEFORE UPDATE OR DELETE ON async_materialization_receipt
FOR EACH ROW EXECUTE FUNCTION reject_s7_append_only_mutation();

CREATE FUNCTION claim_event_outbox(p_worker text, p_limit integer DEFAULT 100)
RETURNS TABLE(
  event_id text,
  event_type text,
  aggregate_type text,
  aggregate_id text,
  partition_key text,
  payload_json jsonb,
  outbox_schema_version text,
  kafka_schema_version integer,
  topic_name text,
  claim_token uuid,
  attempt_count integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $claim_event_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'outbox claim role denied' USING ERRCODE = '42501';
  END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'invalid outbox claim request' USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  WITH candidates AS (
    SELECT item.event_id
    FROM public.event_outbox item
    JOIN public.async_event_registry registry
      ON registry.event_type = item.event_type
     AND registry.outbox_schema_version = item.schema_version
     AND registry.enabled
    WHERE item.status IN ('PENDING', 'FAILED')
      AND item.retry_count < 3
      AND item.next_attempt_at <= statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at <= statement_timestamp())
    ORDER BY item.next_attempt_at, item.created_at, item.event_id
    FOR UPDATE OF item SKIP LOCKED
    LIMIT p_limit
  ), claimed AS (
    UPDATE public.event_outbox item
    SET claim_token = gen_random_uuid(),
        claimed_by = p_worker,
        lease_expires_at = statement_timestamp() + interval '30 seconds',
        updated_at = statement_timestamp()
    FROM candidates
    WHERE item.event_id = candidates.event_id
    RETURNING item.*
  )
  SELECT claimed.event_id, claimed.event_type, claimed.aggregate_type, claimed.aggregate_id,
         claimed.partition_key, claimed.payload_json, claimed.schema_version,
         registry.kafka_schema_version, registry.topic_name, claimed.claim_token,
         claimed.retry_count + 1
  FROM claimed
  JOIN public.async_event_registry registry ON registry.event_type = claimed.event_type
  ORDER BY claimed.next_attempt_at, claimed.created_at, claimed.event_id;
END
$claim_event_outbox$;

CREATE FUNCTION claim_db_async_outbox(p_worker text, p_limit integer DEFAULT 100)
RETURNS TABLE(
  event_id text,
  event_type text,
  aggregate_type text,
  aggregate_id text,
  partition_key text,
  payload_json jsonb,
  outbox_schema_version text,
  kafka_schema_version integer,
  topic_name text,
  claim_token uuid,
  attempt_count integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $claim_db_async_outbox$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'DB async outbox claim role denied' USING ERRCODE = '42501';
  END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'invalid DB async outbox claim request' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT item.event_id
    FROM public.event_outbox item
    JOIN public.async_event_registry registry
      ON registry.event_type = item.event_type
     AND registry.outbox_schema_version = item.schema_version
     AND registry.enabled
    WHERE item.event_type IN (
        'artifact.ingest-requested.v1',
        'rag.index-requested.v1',
        'model.eval-requested.v1'
      )
      AND item.status IN ('PENDING', 'FAILED')
      AND item.retry_count < 3
      AND item.next_attempt_at <= statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at <= statement_timestamp())
    ORDER BY item.next_attempt_at, item.created_at, item.event_id
    FOR UPDATE OF item SKIP LOCKED
    LIMIT p_limit
  ), claimed AS (
    UPDATE public.event_outbox item
    SET claim_token = gen_random_uuid(), claimed_by = p_worker,
        lease_expires_at = statement_timestamp() + interval '30 seconds',
        updated_at = statement_timestamp()
    FROM candidates
    WHERE item.event_id = candidates.event_id
    RETURNING item.*
  )
  SELECT claimed.event_id, claimed.event_type, claimed.aggregate_type, claimed.aggregate_id,
         claimed.partition_key, claimed.payload_json, claimed.schema_version,
         registry.kafka_schema_version, registry.topic_name, claimed.claim_token,
         claimed.retry_count + 1
  FROM claimed
  JOIN public.async_event_registry registry ON registry.event_type = claimed.event_type
  ORDER BY claimed.next_attempt_at, claimed.created_at, claimed.event_id;
END
$claim_db_async_outbox$;

CREATE FUNCTION complete_event_outbox(p_event_id text, p_claim_token uuid)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $complete_event_outbox$
DECLARE
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'outbox completion role denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.event_outbox
  SET status = 'PUBLISHED', published_at = statement_timestamp(), claim_token = NULL,
      claimed_by = NULL, lease_expires_at = NULL, failure_code = NULL,
      error_class = NULL, last_error = NULL, updated_at = statement_timestamp()
  WHERE event_id = p_event_id
    AND claim_token = p_claim_token
    AND status IN ('PENDING', 'FAILED')
    AND lease_expires_at > statement_timestamp();
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$complete_event_outbox$;

CREATE FUNCTION fail_event_outbox(
  p_event_id text,
  p_claim_token uuid,
  p_failure_code text,
  p_error_class text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fail_event_outbox$
DECLARE
  current_attempt integer;
  next_status text;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'outbox failure role denied' USING ERRCODE = '42501';
  END IF;
  IF p_failure_code !~ '^[A-Z][A-Z0-9_]{2,63}$' OR p_error_class !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid bounded outbox failure' USING ERRCODE = '22023';
  END IF;

  SELECT retry_count + 1 INTO current_attempt
  FROM public.event_outbox
  WHERE event_id = p_event_id AND claim_token = p_claim_token
    AND status IN ('PENDING', 'FAILED') AND lease_expires_at > statement_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN
    RETURN 'CONFLICT';
  END IF;
  next_status := CASE WHEN current_attempt >= 3 THEN 'DLQ_REQUESTED' ELSE 'FAILED' END;

  UPDATE public.event_outbox
  SET status = next_status, retry_count = current_attempt, claim_token = NULL, claimed_by = NULL,
      lease_expires_at = NULL, failure_code = p_failure_code, error_class = p_error_class,
      last_error = p_failure_code,
      next_attempt_at = statement_timestamp() + CASE current_attempt WHEN 1 THEN interval '1 second' ELSE interval '5 seconds' END,
      updated_at = statement_timestamp()
  WHERE event_id = p_event_id AND claim_token = p_claim_token;
  RETURN next_status;
END
$fail_event_outbox$;

CREATE FUNCTION quarantine_claimed_outbox(p_event_id text, p_claim_token uuid, p_failure_code text)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $quarantine_claimed_outbox$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'outbox quarantine role denied' USING ERRCODE = '42501';
  END IF;
  IF p_failure_code !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid quarantine failure' USING ERRCODE = '22023';
  END IF;
  UPDATE public.event_outbox
  SET status = 'DLQ_REQUESTED', claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL,
      failure_code = p_failure_code, error_class = 'CONTRACT_VIOLATION',
      last_error = p_failure_code, updated_at = statement_timestamp()
  WHERE event_id = p_event_id AND claim_token = p_claim_token
    AND status IN ('PENDING', 'FAILED') AND lease_expires_at > statement_timestamp();
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$quarantine_claimed_outbox$;

CREATE FUNCTION quarantine_unknown_outbox(p_limit integer DEFAULT 100)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $quarantine_unknown_outbox$
DECLARE
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'outbox quarantine role denied' USING ERRCODE = '42501';
  END IF;
  IF p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'invalid quarantine limit' USING ERRCODE = '22023';
  END IF;
  WITH candidates AS (
    SELECT item.event_id
    FROM public.event_outbox item
    LEFT JOIN public.async_event_registry registry
      ON registry.event_type = item.event_type
     AND registry.outbox_schema_version = item.schema_version
     AND registry.enabled
    WHERE item.status IN ('PENDING', 'FAILED') AND registry.event_type IS NULL
    ORDER BY item.created_at, item.event_id
    FOR UPDATE OF item SKIP LOCKED
    LIMIT p_limit
  )
  UPDATE public.event_outbox item
  SET status = 'DLQ_REQUESTED', failure_code = 'UNREGISTERED_EVENT', error_class = 'CONTRACT_VIOLATION',
      last_error = 'UNREGISTERED_EVENT', claim_token = NULL, claimed_by = NULL,
      lease_expires_at = NULL, updated_at = statement_timestamp()
  FROM candidates
  WHERE item.event_id = candidates.event_id;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed;
END
$quarantine_unknown_outbox$;

CREATE FUNCTION create_async_job(
  p_job_id text,
  p_job_type text,
  p_requested_by text,
  p_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $create_async_job$
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async job create role denied' USING ERRCODE = '42501';
  END IF;
  IF p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$'
     OR p_job_type NOT IN ('RAG_INDEX', 'ARTIFACT_INGEST', 'MODEL_EVAL')
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR octet_length(p_payload::text) > 32768 THEN
    RAISE EXCEPTION 'invalid async job request' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id = p_requested_by AND actor.status = 'ACTIVE'
  ) THEN
    RETURN false;
  END IF;
  INSERT INTO public.async_job(job_id, job_type, status, requested_by, payload_json, next_attempt_at)
  VALUES (p_job_id, p_job_type, 'REQUESTED', p_requested_by, p_payload, statement_timestamp());
  RETURN true;
EXCEPTION
  WHEN unique_violation THEN
    RETURN false;
END
$create_async_job$;

CREATE FUNCTION claim_async_jobs(p_worker text, p_limit integer DEFAULT 100)
RETURNS TABLE(job_id text, job_type text, payload_json jsonb, claim_token uuid, attempt_count integer, hard_deadline_at timestamptz)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $claim_async_jobs$
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async job claim role denied' USING ERRCODE = '42501';
  END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_limit < 1 OR p_limit > 100 THEN
    RAISE EXCEPTION 'invalid async claim request' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT item.job_id
    FROM public.async_job item
    WHERE item.status IN ('REQUESTED', 'FAILED') AND item.attempt_count < 3
      AND item.next_attempt_at <= statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at <= statement_timestamp())
    ORDER BY item.next_attempt_at, item.created_at, item.job_id
    FOR UPDATE SKIP LOCKED
    LIMIT p_limit
  ), claimed AS (
    UPDATE public.async_job item
    SET status = 'RUNNING', claim_token = gen_random_uuid(), claimed_by = p_worker,
        lease_expires_at = statement_timestamp() + interval '5 minutes',
        heartbeat_at = statement_timestamp(), hard_deadline_at = statement_timestamp() + interval '15 minutes',
        attempt_count = item.attempt_count + 1,
        started_at = COALESCE(item.started_at, statement_timestamp()), updated_at = statement_timestamp()
    FROM candidates
    WHERE item.job_id = candidates.job_id
    RETURNING item.*
  )
  SELECT claimed.job_id, claimed.job_type, claimed.payload_json, claimed.claim_token,
         claimed.attempt_count, claimed.hard_deadline_at
  FROM claimed
  ORDER BY claimed.next_attempt_at, claimed.created_at, claimed.job_id;
END
$claim_async_jobs$;

CREATE FUNCTION heartbeat_async_job(p_job_id text, p_claim_token uuid)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $heartbeat_async_job$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async heartbeat role denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.async_job
  SET heartbeat_at = statement_timestamp(),
      lease_expires_at = LEAST(statement_timestamp() + interval '5 minutes', hard_deadline_at),
      updated_at = statement_timestamp()
  WHERE job_id = p_job_id AND claim_token = p_claim_token AND status = 'RUNNING'
    AND lease_expires_at > statement_timestamp() AND hard_deadline_at > statement_timestamp();
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$heartbeat_async_job$;

CREATE FUNCTION claim_async_job_by_id(p_worker text, p_job_id text)
RETURNS TABLE(job_id text, job_type text, payload_json jsonb, claim_token uuid, attempt_count integer, hard_deadline_at timestamptz)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $claim_async_job_by_id$
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async job claim role denied' USING ERRCODE = '42501';
  END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$'
     OR p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$' THEN
    RAISE EXCEPTION 'invalid async claim request' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH candidate AS (
    SELECT item.job_id
    FROM public.async_job item
    WHERE item.job_id = p_job_id
      AND item.status IN ('REQUESTED', 'FAILED')
      AND item.attempt_count < 3
      AND item.next_attempt_at <= statement_timestamp()
      AND (item.lease_expires_at IS NULL OR item.lease_expires_at <= statement_timestamp())
    FOR UPDATE SKIP LOCKED
  ), claimed AS (
    UPDATE public.async_job item
    SET status = 'RUNNING', claim_token = gen_random_uuid(), claimed_by = p_worker,
        lease_expires_at = statement_timestamp() + interval '5 minutes',
        heartbeat_at = statement_timestamp(), hard_deadline_at = statement_timestamp() + interval '15 minutes',
        attempt_count = item.attempt_count + 1,
        started_at = COALESCE(item.started_at, statement_timestamp()), updated_at = statement_timestamp()
    FROM candidate
    WHERE item.job_id = candidate.job_id
    RETURNING item.*
  )
  SELECT claimed.job_id, claimed.job_type, claimed.payload_json, claimed.claim_token,
         claimed.attempt_count, claimed.hard_deadline_at
  FROM claimed;
END
$claim_async_job_by_id$;

CREATE FUNCTION complete_async_job(p_job_id text, p_claim_token uuid, p_result jsonb)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $complete_async_job$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async completion role denied' USING ERRCODE = '42501';
  END IF;
  IF p_result IS NULL OR jsonb_typeof(p_result) <> 'object' OR octet_length(p_result::text) > 32768 THEN
    RAISE EXCEPTION 'invalid async result' USING ERRCODE = '22023';
  END IF;
  UPDATE public.async_job
  SET status = 'COMPLETED', result_json = p_result, completed_at = statement_timestamp(),
      claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL,
      error_code = NULL, error_class = NULL, error_message = NULL, updated_at = statement_timestamp()
  WHERE job_id = p_job_id AND claim_token = p_claim_token AND status = 'RUNNING'
    AND lease_expires_at > statement_timestamp() AND hard_deadline_at > statement_timestamp();
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$complete_async_job$;

CREATE FUNCTION commit_async_work(
  p_event_id text,
  p_event_type text,
  p_consumer_name text,
  p_payload_hash text,
  p_job_id text,
  p_claim_token uuid,
  p_result_ref text,
  p_completion_event_id text,
  p_partition_key text
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $commit_async_work$
#variable_conflict use_column
DECLARE item public.async_job%ROWTYPE; existing_hash text; completion_type text;
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async work commit role denied' USING ERRCODE = '42501';
  END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_consumer_name !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$'
     OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_result_ref !~ '^[A-Za-z][A-Za-z0-9_-]{7,127}$'
     OR p_completion_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid async work commit' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.processed_event(event_id, consumer_name, payload_hash)
  VALUES (p_event_id, p_consumer_name, p_payload_hash)
  ON CONFLICT (event_id, consumer_name) DO NOTHING;
  IF NOT FOUND THEN
    SELECT payload_hash INTO existing_hash
    FROM public.processed_event
    WHERE event_id = p_event_id AND consumer_name = p_consumer_name
    FOR UPDATE;
    IF existing_hash IS DISTINCT FROM p_payload_hash THEN
      UPDATE public.processed_event SET payload_hash_conflict = true
      WHERE event_id = p_event_id AND consumer_name = p_consumer_name;
      RETURN 'PAYLOAD_CONFLICT';
    END IF;
    RETURN 'DUPLICATE';
  END IF;
  SELECT * INTO item
  FROM public.async_job
  WHERE job_id = p_job_id AND claim_token = p_claim_token AND status = 'RUNNING'
    AND lease_expires_at > statement_timestamp() AND hard_deadline_at > statement_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'async work claim conflict' USING ERRCODE = '40001';
  END IF;
  completion_type := CASE p_event_type
    WHEN 'rag.index-requested.v1' THEN 'rag.index-completed.v1'
    WHEN 'artifact.ingest-requested.v1' THEN 'artifact.ingested.v1'
    WHEN 'model.eval-requested.v1' THEN 'model.eval-completed.v1'
    ELSE NULL
  END;
  IF completion_type IS NULL
     OR (p_event_type = 'rag.index-requested.v1' AND item.job_type <> 'RAG_INDEX')
     OR (p_event_type = 'artifact.ingest-requested.v1' AND item.job_type <> 'ARTIFACT_INGEST')
     OR (p_event_type = 'model.eval-requested.v1' AND item.job_type <> 'MODEL_EVAL') THEN
    RAISE EXCEPTION 'async event and job type conflict' USING ERRCODE = '22023';
  END IF;
  IF (item.job_type = 'RAG_INDEX' AND (
        item.payload_json ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
        OR item.payload_json ->> 'sourceRevisionId' !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
        OR item.payload_json ->> 'importTicketId' !~ '^rti_[0-9a-f]{32}$'
      ))
     OR (item.job_type = 'ARTIFACT_INGEST' AND (
        item.payload_json ->> 'artifactId' !~ '^artifact_[A-Za-z0-9_-]{8,96}$'
        OR item.payload_json ->> 'contentHash' !~ '^sha256:[0-9a-f]{64}$'
      ))
     OR (item.job_type = 'MODEL_EVAL' AND (
        item.payload_json ->> 'runId' !~ '^(run|demo)_[A-Za-z0-9_-]{8,96}$'
        OR item.payload_json ->> 'contentHash' !~ '^sha256:[0-9a-f]{64}$'
      )) THEN
    RAISE EXCEPTION 'async work references are invalid' USING ERRCODE = '22023';
  END IF;
  IF item.job_type = 'RAG_INDEX' AND NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions source
    JOIN public.rag_v2_immutable_import_tickets ticket
      ON ticket.owner_user_id = source.owner_user_id
     AND ticket.ticket_hash = encode(public.digest(item.payload_json ->> 'importTicketId', 'sha256'), 'hex')
     AND ticket.state = 'CONSUMED'
     AND ticket.embedding_profile_id = 'bge_m3_local_1024_v1'
    WHERE source.source_revision_id = item.payload_json ->> 'sourceRevisionId'
      AND source.source_id = item.payload_json ->> 'sourceId'
      AND source.owner_user_id = item.requested_by
      AND source.source_scope = 'OWNER_PRIVATE'
      AND source.local_processing_allowed
      AND item.payload_json ->> 'ownerRef' = item.requested_by
      AND item.payload_json ->> 'profileId' = 'bge_m3_local_1024_v1'
  ) THEN
    RAISE EXCEPTION 'RAG async request lacks consumed local owner evidence' USING ERRCODE = '42501';
  END IF;
  INSERT INTO public.async_materialization_receipt(
    result_ref, event_id, job_id, job_type, source_revision_id, artifact_id, run_id, content_hash
  ) VALUES (
    p_result_ref, p_event_id, p_job_id, item.job_type,
    item.payload_json ->> 'sourceRevisionId', item.payload_json ->> 'artifactId',
    item.payload_json ->> 'runId', item.payload_json ->> 'contentHash'
  );
  UPDATE public.async_job
  SET status = 'COMPLETED', result_json = jsonb_build_object('resultRef', p_result_ref),
      completed_at = statement_timestamp(), claim_token = NULL, claimed_by = NULL,
      lease_expires_at = NULL, error_code = NULL, error_class = NULL,
      error_message = NULL, updated_at = statement_timestamp()
  WHERE job_id = p_job_id AND claim_token = p_claim_token;
  INSERT INTO public.event_outbox(
    event_id, event_type, aggregate_type, aggregate_id, partition_key, payload_json, schema_version
  ) VALUES (
    p_completion_event_id, completion_type, 'ASYNC_JOB', p_job_id, p_partition_key,
    jsonb_build_object('jobId', p_job_id, 'resultRef', p_result_ref), '1.0.0'
  );
  RETURN 'COMPLETED';
END
$commit_async_work$;

CREATE FUNCTION fail_async_job(p_job_id text, p_claim_token uuid, p_error_code text, p_error_class text)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fail_async_job$
DECLARE current_attempt integer; next_status text;
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async failure role denied' USING ERRCODE = '42501';
  END IF;
  IF p_error_code !~ '^[A-Z][A-Z0-9_]{2,63}$' OR p_error_class !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid async failure' USING ERRCODE = '22023';
  END IF;
  SELECT attempt_count INTO current_attempt
  FROM public.async_job
  WHERE job_id = p_job_id AND claim_token = p_claim_token AND status = 'RUNNING'
    AND lease_expires_at > statement_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN RETURN 'CONFLICT'; END IF;
  next_status := CASE WHEN current_attempt >= 3 THEN 'NEEDS_REVIEW' ELSE 'FAILED' END;
  UPDATE public.async_job
  SET status = next_status, claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL,
      error_code = p_error_code, error_class = p_error_class, error_message = p_error_code,
      next_attempt_at = statement_timestamp() + CASE current_attempt WHEN 1 THEN interval '1 second' ELSE interval '5 seconds' END,
      completed_at = CASE WHEN next_status = 'NEEDS_REVIEW' THEN statement_timestamp() ELSE completed_at END,
      updated_at = statement_timestamp()
  WHERE job_id = p_job_id AND claim_token = p_claim_token;
  RETURN next_status;
END
$fail_async_job$;

CREATE FUNCTION quarantine_async_job(p_job_id text, p_claim_token uuid, p_error_code text, p_error_class text)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $quarantine_async_job$
DECLARE changed integer;
BEGIN
  IF session_user <> 'decision_worker' THEN
    RAISE EXCEPTION 'async quarantine role denied' USING ERRCODE = '42501';
  END IF;
  IF p_error_code !~ '^[A-Z][A-Z0-9_]{2,63}$' OR p_error_class !~ '^[A-Z][A-Z0-9_]{2,63}$' THEN
    RAISE EXCEPTION 'invalid async quarantine' USING ERRCODE = '22023';
  END IF;
  UPDATE public.async_job
  SET status = 'NEEDS_REVIEW', claim_token = NULL, claimed_by = NULL, lease_expires_at = NULL,
      error_code = p_error_code, error_class = p_error_class, error_message = p_error_code,
      completed_at = statement_timestamp(), updated_at = statement_timestamp()
  WHERE job_id = p_job_id AND claim_token = p_claim_token AND status = 'RUNNING';
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$quarantine_async_job$;

CREATE FUNCTION read_async_job_status(p_actor_user_id text, p_security_version bigint, p_job_id text)
RETURNS TABLE(
  job_id text, job_type text, status text, requested_at timestamptz, started_at timestamptz,
  completed_at timestamptz, source_id text, artifact_id text, result_ref text,
  error_code text, error_class text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_async_job_status$
DECLARE actor_role text; actor_status text; actor_security_version bigint;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async status role denied' USING ERRCODE = '42501';
  END IF;
  SELECT role, users.status, security_version INTO actor_role, actor_status, actor_security_version
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status <> 'ACTIVE' OR actor_role <> 'ADMIN'
     OR actor_security_version <> p_security_version THEN
    RETURN;
  END IF;
  INSERT INTO public.async_job_admin_read_audit(actor_user_id, target_owner_user_id, job_id, read_kind)
  SELECT p_actor_user_id, item.requested_by, item.job_id, 'DETAIL'
  FROM public.async_job item
  WHERE item.job_id = p_job_id AND item.requested_by IS DISTINCT FROM p_actor_user_id;
  RETURN QUERY
  SELECT item.job_id, item.job_type, item.status, item.created_at, item.started_at, item.completed_at,
         item.payload_json ->> 'sourceId', item.payload_json ->> 'artifactId', item.result_json ->> 'resultRef',
         item.error_code, item.error_class
  FROM public.async_job item
  WHERE item.job_id = p_job_id
  LIMIT 1;
END
$read_async_job_status$;

CREATE FUNCTION list_async_job_status(
  p_actor_user_id text,
  p_security_version bigint,
  p_status text,
  p_job_type text,
  p_before_created_at timestamptz,
  p_before_job_id text,
  p_limit integer
)
RETURNS TABLE(
  job_id text, job_type text, status text, requested_at timestamptz, started_at timestamptz,
  completed_at timestamptz, source_id text, artifact_id text, result_ref text,
  error_code text, error_class text
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $list_async_job_status$
DECLARE actor_role text; actor_status text; actor_security_version bigint;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'async status role denied' USING ERRCODE = '42501';
  END IF;
  IF p_limit < 1 OR p_limit > 101
     OR (p_status IS NOT NULL AND p_status NOT IN ('REQUESTED', 'RUNNING', 'COMPLETED', 'FAILED', 'NEEDS_REVIEW'))
     OR (p_job_type IS NOT NULL AND p_job_type NOT IN ('RAG_INDEX', 'ARTIFACT_INGEST', 'MODEL_EVAL'))
     OR ((p_before_created_at IS NULL) <> (p_before_job_id IS NULL)) THEN
    RAISE EXCEPTION 'invalid async status query' USING ERRCODE = '22023';
  END IF;
  SELECT role, users.status, security_version INTO actor_role, actor_status, actor_security_version
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status <> 'ACTIVE' OR actor_role <> 'ADMIN'
     OR actor_security_version <> p_security_version THEN
    RETURN;
  END IF;
  DROP TABLE IF EXISTS pg_temp.s7_async_status_page;
  CREATE TEMP TABLE pg_temp.s7_async_status_page ON COMMIT DROP AS
    SELECT item.job_id, item.job_type, item.status, item.created_at, item.started_at, item.completed_at,
           item.payload_json ->> 'sourceId' AS source_id,
           item.payload_json ->> 'artifactId' AS artifact_id,
           item.result_json ->> 'resultRef' AS result_ref,
           item.error_code, item.error_class, item.requested_by
    FROM public.async_job item
    WHERE (p_status IS NULL OR item.status = p_status)
      AND (p_job_type IS NULL OR item.job_type = p_job_type)
      AND (
        p_before_created_at IS NULL
        OR (item.created_at, item.job_id) < (p_before_created_at, p_before_job_id)
      )
    ORDER BY item.created_at DESC, item.job_id DESC
    LIMIT p_limit;
  INSERT INTO public.async_job_admin_read_audit(actor_user_id, target_owner_user_id, job_id, read_kind)
  SELECT p_actor_user_id, page.requested_by, page.job_id, 'LIST'
  FROM pg_temp.s7_async_status_page page
  WHERE page.requested_by IS DISTINCT FROM p_actor_user_id;
  RETURN QUERY
  SELECT page.job_id, page.job_type, page.status, page.created_at, page.started_at, page.completed_at,
         page.source_id, page.artifact_id, page.result_ref, page.error_code, page.error_class
  FROM pg_temp.s7_async_status_page page
  ORDER BY page.created_at DESC, page.job_id DESC;
END
$list_async_job_status$;

-- 모든 definer 함수는 flyway 소유, fixed search_path, PUBLIC revoke 뒤 exact role만 실행한다.
ALTER FUNCTION guard_event_outbox_s7_update() OWNER TO flyway;
ALTER FUNCTION guard_async_job_s7_update() OWNER TO flyway;
ALTER FUNCTION reject_s7_append_only_mutation() OWNER TO flyway;
ALTER FUNCTION claim_event_outbox(text, integer) OWNER TO flyway;
ALTER FUNCTION claim_db_async_outbox(text, integer) OWNER TO flyway;
ALTER FUNCTION complete_event_outbox(text, uuid) OWNER TO flyway;
ALTER FUNCTION fail_event_outbox(text, uuid, text, text) OWNER TO flyway;
ALTER FUNCTION quarantine_claimed_outbox(text, uuid, text) OWNER TO flyway;
ALTER FUNCTION quarantine_unknown_outbox(integer) OWNER TO flyway;
ALTER FUNCTION create_async_job(text, text, text, jsonb) OWNER TO flyway;
ALTER FUNCTION claim_async_jobs(text, integer) OWNER TO flyway;
ALTER FUNCTION heartbeat_async_job(text, uuid) OWNER TO flyway;
ALTER FUNCTION claim_async_job_by_id(text, text) OWNER TO flyway;
ALTER FUNCTION complete_async_job(text, uuid, jsonb) OWNER TO flyway;
ALTER FUNCTION commit_async_work(text, text, text, text, text, uuid, text, text, text) OWNER TO flyway;
ALTER FUNCTION fail_async_job(text, uuid, text, text) OWNER TO flyway;
ALTER FUNCTION quarantine_async_job(text, uuid, text, text) OWNER TO flyway;
ALTER FUNCTION read_async_job_status(text, bigint, text) OWNER TO flyway;
ALTER FUNCTION list_async_job_status(text, bigint, text, text, timestamptz, text, integer) OWNER TO flyway;

REVOKE ALL ON TABLE async_event_registry, event_outbox_transition_audit,
  async_job_transition_audit, async_job_admin_read_audit, async_materialization_receipt,
  shedlock FROM PUBLIC, decision_app;
REVOKE ALL ON FUNCTION claim_event_outbox(text, integer), claim_db_async_outbox(text, integer),
  complete_event_outbox(text, uuid),
  fail_event_outbox(text, uuid, text, text), quarantine_claimed_outbox(text, uuid, text),
  quarantine_unknown_outbox(integer),
  create_async_job(text, text, text, jsonb),
  claim_async_jobs(text, integer), claim_async_job_by_id(text, text), heartbeat_async_job(text, uuid),
  complete_async_job(text, uuid, jsonb), fail_async_job(text, uuid, text, text),
  quarantine_async_job(text, uuid, text, text),
  commit_async_work(text, text, text, text, text, uuid, text, text, text),
  read_async_job_status(text, bigint, text),
  list_async_job_status(text, bigint, text, text, timestamptz, text, integer) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION claim_event_outbox(text, integer), claim_db_async_outbox(text, integer),
  complete_event_outbox(text, uuid),
  fail_event_outbox(text, uuid, text, text), quarantine_claimed_outbox(text, uuid, text),
  quarantine_unknown_outbox(integer),
  create_async_job(text, text, text, jsonb),
  read_async_job_status(text, bigint, text),
  list_async_job_status(text, bigint, text, text, timestamptz, text, integer) TO decision_app;
GRANT SELECT, INSERT, UPDATE ON TABLE shedlock TO decision_app;

DO $decision_worker_grants$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_worker') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_worker;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_worker;
    GRANT EXECUTE ON FUNCTION claim_async_jobs(text, integer), claim_async_job_by_id(text, text),
      heartbeat_async_job(text, uuid),
      complete_async_job(text, uuid, jsonb), fail_async_job(text, uuid, text, text),
      quarantine_async_job(text, uuid, text, text),
      commit_async_work(text, text, text, text, text, uuid, text, text, text)
      TO decision_worker;
    GRANT INSERT ON TABLE processed_event TO decision_worker;
    REVOKE CREATE ON SCHEMA public FROM decision_worker;
  END IF;
END
$decision_worker_grants$;

REVOKE UPDATE, DELETE, TRUNCATE ON TABLE event_outbox_transition_audit,
  async_job_transition_audit, async_job_admin_read_audit FROM decision_app;
REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;

-- Schema qualification is intentional: bootstrap reconstruction and Spring Flyway must
-- produce the same ACL even when a deployment customizes the migration role search_path.
GRANT EXECUTE ON FUNCTION public.claim_event_outbox(text, integer),
  public.claim_db_async_outbox(text, integer),
  public.complete_event_outbox(text, uuid),
  public.fail_event_outbox(text, uuid, text, text),
  public.quarantine_claimed_outbox(text, uuid, text),
  public.quarantine_unknown_outbox(integer),
  public.create_async_job(text, text, text, jsonb),
  public.read_async_job_status(text, bigint, text),
  public.list_async_job_status(text, bigint, text, text, timestamptz, text, integer)
  TO decision_app;
GRANT EXECUTE ON FUNCTION public.claim_async_jobs(text, integer),
  public.claim_async_job_by_id(text, text), public.heartbeat_async_job(text, uuid),
  public.complete_async_job(text, uuid, jsonb), public.fail_async_job(text, uuid, text, text),
  public.quarantine_async_job(text, uuid, text, text),
  public.commit_async_work(text, text, text, text, text, uuid, text, text, text)
  TO decision_worker;
