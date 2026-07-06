--비동기 작업은 Kafka 도입 전에도 같은 DB 트랜잭션과 polling으로 재처리 가능해야 한다.
CREATE TABLE event_outbox (
  event_id text PRIMARY KEY,
  event_type text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  partition_key text NOT NULL,
  --외부 원문·계좌 token·API key를 싣지 않고 참조 ID/hash만 전달하기 위해 reference-only JSONB로 둔다.
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  schema_version text NOT NULL DEFAULT '1.0.0',
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED', 'DLQ_REQUESTED')),
  retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  locked_at timestamptz,
  published_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_event_outbox_pending_created_at ON event_outbox(created_at) WHERE status = 'PENDING';

CREATE TABLE processed_event (
  event_id text NOT NULL,
  consumer_name text NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(),
  payload_hash text,
  CONSTRAINT processed_event_event_id_consumer_name_unique UNIQUE (event_id, consumer_name)
);

CREATE TABLE async_job (
  job_id text PRIMARY KEY,
  job_type text NOT NULL,
  status text NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED', 'RUNNING', 'COMPLETED', 'FAILED', 'NEEDS_REVIEW')),
  requested_by text REFERENCES users(user_id),
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE artifact_ingest_state (
  run_id text NOT NULL,
  file_hash text NOT NULL,
  schema_version text NOT NULL,
  file_name text NOT NULL,
  status text NOT NULL DEFAULT 'DISCOVERED' CHECK (status IN ('DISCOVERED', 'VALIDATED', 'INGESTED', 'FAILED', 'SKIPPED')),
  row_count bigint,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT artifact_ingest_state_run_id_file_hash_schema_version_unique UNIQUE (run_id, file_hash, schema_version)
);

CREATE TABLE stream_metric_snapshot (
  snapshot_id text PRIMARY KEY,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  outbox_pending_count bigint NOT NULL DEFAULT 0,
  failed_job_count bigint NOT NULL DEFAULT 0,
  stale_signal_ratio numeric(10,6),
  decision_distribution_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (window_end > window_start)
);
