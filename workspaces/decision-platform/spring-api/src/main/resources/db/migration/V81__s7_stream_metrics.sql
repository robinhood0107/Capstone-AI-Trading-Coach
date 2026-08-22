-- S7.3 stores DB-only operational metrics in append-only time buckets.
ALTER TABLE public.stream_metric_snapshot
  ADD COLUMN snapshot_kind text NOT NULL DEFAULT 'LEGACY_FULL',
  ADD COLUMN component_status text NOT NULL DEFAULT 'UNAVAILABLE',
  ADD COLUMN observed_at timestamptz,
  ADD COLUMN dlq_event_count bigint NOT NULL DEFAULT 0,
  ADD CONSTRAINT stream_metric_snapshot_kind_check CHECK (
    snapshot_kind IN ('LEGACY_FULL', 'DECISION_DISTRIBUTION', 'SIGNAL_FRESHNESS', 'FAILED_JOBS', 'DLQ_EVENTS')
  ),
  ADD CONSTRAINT stream_metric_component_status_check CHECK (
    component_status IN ('OK', 'EMPTY', 'UNAVAILABLE')
  ),
  ADD CONSTRAINT stream_metric_count_bounds_check CHECK (
    outbox_pending_count >= 0 AND failed_job_count >= 0 AND dlq_event_count >= 0
  ),
  ADD CONSTRAINT stream_metric_observed_at_check CHECK (
    snapshot_kind = 'LEGACY_FULL' OR observed_at IS NOT NULL
  ),
  ADD CONSTRAINT stream_metric_signal_ratio_check CHECK (
    stale_signal_ratio IS NULL OR stale_signal_ratio BETWEEN 0 AND 1
  );

CREATE UNIQUE INDEX stream_metric_snapshot_bucket_unique
  ON public.stream_metric_snapshot(snapshot_kind, window_start)
  WHERE snapshot_kind <> 'LEGACY_FULL';

CREATE TABLE public.stream_metric_admin_read_audit (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_user_id text NOT NULL,
  read_kind text NOT NULL CHECK (read_kind = 'LATEST'),
  occurred_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE FUNCTION public.reject_stream_metric_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $reject_stream_metric_mutation$
BEGIN
  RAISE EXCEPTION 'stream metric snapshots and read audit are append-only' USING ERRCODE = '42501';
END
$reject_stream_metric_mutation$;

CREATE TRIGGER stream_metric_snapshot_append_only
BEFORE UPDATE OR DELETE ON public.stream_metric_snapshot
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER stream_metric_admin_read_audit_append_only
BEFORE UPDATE OR DELETE ON public.stream_metric_admin_read_audit
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE FUNCTION public.aggregate_decision_distribution()
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $aggregate_decision_distribution$
DECLARE
  bucket_start timestamptz := date_trunc('minute', statement_timestamp());
  distribution jsonb;
  total_count bigint;
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'stream metric writer role denied' USING ERRCODE = '42501';
  END IF;
  SELECT jsonb_build_object(
           'ALLOW', count(*) FILTER (WHERE outcome = 'ALLOW'),
           'WARN', count(*) FILTER (WHERE outcome = 'WARN'),
           'HOLD', count(*) FILTER (WHERE outcome = 'HOLD'),
           'BLOCK', count(*) FILTER (WHERE outcome = 'BLOCK')
         ), count(*)
    INTO distribution, total_count
  FROM public.decisions
  WHERE created_at >= (
    (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date::timestamp AT TIME ZONE 'Asia/Seoul'
  )
    AND created_at < statement_timestamp();
  INSERT INTO public.stream_metric_snapshot(
    snapshot_id, snapshot_kind, component_status, observed_at,
    window_start, window_end, decision_distribution_json
  ) VALUES (
    'metric_decision_' || to_char(bucket_start AT TIME ZONE 'UTC', 'YYYYMMDDHH24MI'),
    'DECISION_DISTRIBUTION', CASE WHEN total_count = 0 THEN 'EMPTY' ELSE 'OK' END,
    statement_timestamp(), bucket_start, bucket_start + interval '1 minute', distribution
  ) ON CONFLICT (snapshot_kind, window_start) WHERE snapshot_kind <> 'LEGACY_FULL' DO NOTHING;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$aggregate_decision_distribution$;

CREATE FUNCTION public.aggregate_signal_freshness()
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $aggregate_signal_freshness$
DECLARE
  bucket_start timestamptz := date_trunc('hour', statement_timestamp())
    + floor(extract(minute FROM statement_timestamp()) / 5) * interval '5 minutes';
  completed_close timestamptz;
  source_count bigint;
  stale_count bigint;
  ratio numeric(10,6);
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'stream metric writer role denied' USING ERRCODE = '42501';
  END IF;
  SELECT max(close_at) INTO completed_close
  FROM public.trading_sessions
  WHERE exchange_mic = 'XKRX' AND is_open AND close_at <= statement_timestamp();
  WITH latest AS (
    SELECT DISTINCT ON (producer, symbol, timeframe)
      producer, symbol, timeframe, as_of, status
    FROM public.ingested_signals
    WHERE fixture = false AND provenance_class = 'PRODUCTION'
    ORDER BY producer, symbol, timeframe, session_date DESC, created_at DESC, signal_id DESC
  )
  SELECT count(*), count(*) FILTER (
    WHERE status <> 'AVAILABLE' OR as_of IS NULL
       OR (timeframe = '60m' AND as_of < statement_timestamp() - interval '90 minutes')
       OR (timeframe = '1d' AND (completed_close IS NULL OR as_of < completed_close))
       OR timeframe NOT IN ('1d', '60m')
       OR as_of > statement_timestamp()
  ) INTO source_count, stale_count
  FROM latest;
  ratio := CASE WHEN source_count = 0 THEN NULL ELSE stale_count::numeric / source_count END;
  INSERT INTO public.stream_metric_snapshot(
    snapshot_id, snapshot_kind, component_status, observed_at,
    window_start, window_end, stale_signal_ratio
  ) VALUES (
    'metric_signal_' || to_char(bucket_start AT TIME ZONE 'UTC', 'YYYYMMDDHH24MI'),
    'SIGNAL_FRESHNESS', CASE WHEN source_count = 0 THEN 'UNAVAILABLE' ELSE 'OK' END,
    statement_timestamp(), bucket_start, bucket_start + interval '5 minutes', ratio
  ) ON CONFLICT (snapshot_kind, window_start) WHERE snapshot_kind <> 'LEGACY_FULL' DO NOTHING;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$aggregate_signal_freshness$;

CREATE FUNCTION public.aggregate_failed_jobs()
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $aggregate_failed_jobs$
DECLARE
  bucket_start timestamptz := date_trunc('minute', statement_timestamp());
  metric_count bigint;
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'stream metric writer role denied' USING ERRCODE = '42501';
  END IF;
  SELECT count(*) INTO metric_count FROM public.async_job WHERE status IN ('FAILED', 'NEEDS_REVIEW');
  INSERT INTO public.stream_metric_snapshot(
    snapshot_id, snapshot_kind, component_status, observed_at,
    window_start, window_end, failed_job_count
  ) VALUES (
    'metric_failed_' || to_char(bucket_start AT TIME ZONE 'UTC', 'YYYYMMDDHH24MI'),
    'FAILED_JOBS', 'OK', statement_timestamp(), bucket_start, bucket_start + interval '1 minute', metric_count
  ) ON CONFLICT (snapshot_kind, window_start) WHERE snapshot_kind <> 'LEGACY_FULL' DO NOTHING;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$aggregate_failed_jobs$;

CREATE FUNCTION public.aggregate_dlq_events()
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $aggregate_dlq_events$
DECLARE
  bucket_start timestamptz := date_trunc('hour', statement_timestamp())
    + floor(extract(minute FROM statement_timestamp()) / 5) * interval '5 minutes';
  metric_count bigint;
  changed integer;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'stream metric writer role denied' USING ERRCODE = '42501';
  END IF;
  SELECT count(*) INTO metric_count FROM public.event_outbox WHERE status = 'DLQ_REQUESTED';
  INSERT INTO public.stream_metric_snapshot(
    snapshot_id, snapshot_kind, component_status, observed_at,
    window_start, window_end, dlq_event_count
  ) VALUES (
    'metric_dlq_' || to_char(bucket_start AT TIME ZONE 'UTC', 'YYYYMMDDHH24MI'),
    'DLQ_EVENTS', 'OK', statement_timestamp(), bucket_start, bucket_start + interval '5 minutes', metric_count
  ) ON CONFLICT (snapshot_kind, window_start) WHERE snapshot_kind <> 'LEGACY_FULL' DO NOTHING;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END
$aggregate_dlq_events$;

CREATE FUNCTION public.read_stream_metric_status(p_actor_user_id text, p_security_version bigint)
RETURNS TABLE(
  last_updated_at timestamptz, pipeline_health text, stale_signal_ratio numeric,
  allow_count bigint, warn_count bigint, hold_count bigint, block_count bigint,
  failed_job_count bigint, dlq_event_count bigint,
  decision_status text, decision_observed_at timestamptz,
  signal_status text, signal_observed_at timestamptz,
  failed_status text, failed_observed_at timestamptz,
  dlq_status text, dlq_observed_at timestamptz
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_stream_metric_status$
DECLARE
  actor_role text; actor_status text; actor_security_version bigint;
BEGIN
  IF session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'stream metric reader role denied' USING ERRCODE = '42501';
  END IF;
  SELECT role, users.status, security_version INTO actor_role, actor_status, actor_security_version
  FROM public.users WHERE user_id = p_actor_user_id FOR SHARE;
  IF NOT FOUND OR actor_status <> 'ACTIVE' OR actor_role <> 'ADMIN'
     OR actor_security_version <> p_security_version THEN
    RETURN;
  END IF;
  INSERT INTO public.stream_metric_admin_read_audit(actor_user_id, read_kind)
  VALUES (p_actor_user_id, 'LATEST');
  RETURN QUERY
  WITH latest AS (
    SELECT DISTINCT ON (snapshot_kind) *
    FROM public.stream_metric_snapshot
    WHERE snapshot_kind <> 'LEGACY_FULL'
    ORDER BY snapshot_kind, observed_at DESC, snapshot_id DESC
  ), values_by_kind AS (
    SELECT
      max(item.observed_at) AS updated_at,
      max(item.stale_signal_ratio) FILTER (WHERE item.snapshot_kind = 'SIGNAL_FRESHNESS') AS signal_ratio,
      max((item.decision_distribution_json ->> 'ALLOW')::bigint)
        FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS allows,
      max((item.decision_distribution_json ->> 'WARN')::bigint)
        FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS warns,
      max((item.decision_distribution_json ->> 'HOLD')::bigint)
        FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS holds,
      max((item.decision_distribution_json ->> 'BLOCK')::bigint)
        FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS blocks,
      max(item.failed_job_count) FILTER (WHERE item.snapshot_kind = 'FAILED_JOBS') AS faileds,
      max(item.dlq_event_count) FILTER (WHERE item.snapshot_kind = 'DLQ_EVENTS') AS dlqs,
      max(item.component_status) FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS decision_state,
      max(item.observed_at) FILTER (WHERE item.snapshot_kind = 'DECISION_DISTRIBUTION') AS decision_at,
      max(item.component_status) FILTER (WHERE item.snapshot_kind = 'SIGNAL_FRESHNESS') AS signal_state,
      max(item.observed_at) FILTER (WHERE item.snapshot_kind = 'SIGNAL_FRESHNESS') AS signal_at,
      max(item.component_status) FILTER (WHERE item.snapshot_kind = 'FAILED_JOBS') AS failed_state,
      max(item.observed_at) FILTER (WHERE item.snapshot_kind = 'FAILED_JOBS') AS failed_at,
      max(item.component_status) FILTER (WHERE item.snapshot_kind = 'DLQ_EVENTS') AS dlq_state,
      max(item.observed_at) FILTER (WHERE item.snapshot_kind = 'DLQ_EVENTS') AS dlq_at
    FROM latest item
  )
  SELECT updated_at,
    CASE
      WHEN decision_state IS NULL OR signal_state IS NULL OR failed_state IS NULL OR dlq_state IS NULL
        OR signal_state = 'UNAVAILABLE' THEN 'UNAVAILABLE'
      WHEN coalesce(signal_ratio, 0) > 0 OR coalesce(faileds, 0) > 0 OR coalesce(dlqs, 0) > 0 THEN 'DEGRADED'
      ELSE 'OK'
    END,
    signal_ratio, coalesce(allows, 0), coalesce(warns, 0), coalesce(holds, 0), coalesce(blocks, 0),
    coalesce(faileds, 0), coalesce(dlqs, 0),
    coalesce(decision_state, 'UNAVAILABLE'), decision_at,
    coalesce(signal_state, 'UNAVAILABLE'), signal_at,
    coalesce(failed_state, 'UNAVAILABLE'), failed_at,
    coalesce(dlq_state, 'UNAVAILABLE'), dlq_at
  FROM values_by_kind;
END
$read_stream_metric_status$;

ALTER FUNCTION public.reject_stream_metric_mutation() OWNER TO flyway;
ALTER FUNCTION public.aggregate_decision_distribution() OWNER TO flyway;
ALTER FUNCTION public.aggregate_signal_freshness() OWNER TO flyway;
ALTER FUNCTION public.aggregate_failed_jobs() OWNER TO flyway;
ALTER FUNCTION public.aggregate_dlq_events() OWNER TO flyway;
ALTER FUNCTION public.read_stream_metric_status(text, bigint) OWNER TO flyway;

REVOKE ALL ON TABLE public.stream_metric_snapshot, public.stream_metric_admin_read_audit
  FROM PUBLIC, decision_app;
GRANT SELECT, INSERT ON TABLE public.stream_metric_snapshot, public.stream_metric_admin_read_audit TO flyway;
REVOKE ALL ON FUNCTION public.reject_stream_metric_mutation(),
  public.aggregate_decision_distribution(), public.aggregate_signal_freshness(),
  public.aggregate_failed_jobs(), public.aggregate_dlq_events(),
  public.read_stream_metric_status(text, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.aggregate_decision_distribution(), public.aggregate_signal_freshness(),
  public.aggregate_failed_jobs(), public.aggregate_dlq_events(),
  public.read_stream_metric_status(text, bigint) TO decision_app;
