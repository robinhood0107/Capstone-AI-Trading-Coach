-- S1.6 canonical calendar/event 저장소와 OpenDART charged-attempt 원장을 한 migration으로 소유한다.
-- login role은 operator bootstrap이 먼저 만들며, 이 migration은 객체와 exact privilege만 다룬다.

CREATE TABLE opendart_quota_usage (
  usage_date date PRIMARY KEY,
  effective_limit integer NOT NULL,
  daily_budget integer NOT NULL,
  physical_attempts integer NOT NULL DEFAULT 0,
  exhausted_at timestamptz,
  exhausted_reason text,
  last_grant_token text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (effective_limit > 0),
  CHECK (daily_budget > 0 AND daily_budget <= 17500),
  CHECK (daily_budget <= effective_limit),
  CHECK ((daily_budget::bigint * 8) <= (effective_limit::bigint * 7)),
  CHECK (physical_attempts >= 0),
  CHECK ((exhausted_at IS NULL) = (exhausted_reason IS NULL)),
  CHECK (exhausted_reason IS NULL OR char_length(exhausted_reason) BETWEEN 1 AND 64),
  CHECK (last_grant_token IS NULL OR char_length(last_grant_token) BETWEEN 1 AND 128)
);

CREATE TABLE calendar_source_health (
  source_id text PRIMARY KEY,
  last_success_at timestamptz,
  last_failure_at timestamptz,
  failure_count integer NOT NULL DEFAULT 0,
  stale_after interval NOT NULL,
  network_ready boolean NOT NULL DEFAULT false,
  status_code text NOT NULL,
  error_code text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (char_length(source_id) BETWEEN 1 AND 128),
  CHECK (failure_count >= 0),
  CHECK (stale_after > interval '0 seconds'),
  CHECK (status_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
  CHECK (error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$')
);

CREATE TABLE calendar_observations (
  observation_id text PRIMARY KEY,
  source_id text NOT NULL,
  origin_group text NOT NULL,
  capability text NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  observed_at timestamptz NOT NULL,
  ingested_at timestamptz NOT NULL,
  sanitized_payload jsonb NOT NULL,
  sanitized_payload_hash text NOT NULL,
  adapter_version text NOT NULL,
  mapping_version text NOT NULL,
  registry_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (effective_to IS NULL OR effective_to >= effective_from),
  CHECK (ingested_at >= observed_at),
  CHECK (char_length(sanitized_payload_hash) = 64),
  CHECK (jsonb_typeof(sanitized_payload) = 'object'),
  UNIQUE (source_id, sanitized_payload_hash, mapping_version)
);

CREATE TABLE trading_sessions (
  exchange_mic text NOT NULL,
  session_date date NOT NULL,
  is_open boolean NOT NULL,
  open_at timestamptz,
  close_at timestamptz,
  timezone text NOT NULL,
  reason text,
  chosen_source_id text,
  degraded boolean NOT NULL,
  fallback_reason text,
  as_of timestamptz NOT NULL,
  confidence_bps integer NOT NULL,
  has_conflict boolean NOT NULL,
  canonical_hash text NOT NULL,
  canonical_rule_version text NOT NULL,
  confidence_rule_version text NOT NULL DEFAULT 's1.6-confidence-v1',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (exchange_mic, session_date),
  CHECK (exchange_mic ~ '^[A-Z0-9]{4}$'),
  CHECK (timezone = 'Asia/Seoul'),
  CHECK (open_at IS NULL OR close_at IS NULL OR close_at > open_at),
  CHECK (confidence_bps BETWEEN 0 AND 9900),
  CHECK (char_length(canonical_hash) = 64)
);

CREATE TABLE calendar_events (
  event_id text PRIMARY KEY,
  event_series_key text NOT NULL,
  revision_no integer NOT NULL,
  revised_from_event_id text REFERENCES calendar_events(event_id),
  source_id text NOT NULL,
  source_event_key text NOT NULL,
  source_revision text,
  event_type text NOT NULL,
  symbol text,
  exchange_mic text,
  event_date date NOT NULL,
  detail jsonb NOT NULL,
  status text NOT NULL,
  confidence_bps integer NOT NULL,
  has_conflict boolean NOT NULL,
  canonical_hash text NOT NULL,
  canonical_rule_version text NOT NULL DEFAULT 's1.6-event-v1',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (revision_no > 0),
  CHECK (jsonb_typeof(detail) = 'object'),
  CHECK (status IN ('ACTIVE', 'CLOSED', 'SCHEDULED', 'CANCELLED', 'CORRECTED')),
  CHECK (confidence_bps BETWEEN 0 AND 9900),
  CHECK (char_length(canonical_hash) = 64),
  UNIQUE (event_series_key, revision_no),
  UNIQUE (event_series_key, canonical_hash),
  UNIQUE (revised_from_event_id),
  UNIQUE NULLS NOT DISTINCT (source_id, source_event_key, source_revision)
);

CREATE TABLE calendar_event_sources (
  event_source_id text PRIMARY KEY,
  event_id text REFERENCES calendar_events(event_id),
  exchange_mic text,
  session_date date,
  observation_id text NOT NULL REFERENCES calendar_observations(observation_id),
  source_choice text NOT NULL,
  resolution_reason text NOT NULL,
  opaque_source_ref text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (event_id IS NOT NULL AND exchange_mic IS NULL AND session_date IS NULL)
    OR (event_id IS NULL AND exchange_mic IS NOT NULL AND session_date IS NOT NULL)
  ),
  CHECK (char_length(opaque_source_ref) = 64),
  FOREIGN KEY (exchange_mic, session_date) REFERENCES trading_sessions(exchange_mic, session_date),
  UNIQUE NULLS NOT DISTINCT (event_id, exchange_mic, session_date, observation_id)
);

CREATE TABLE calendar_conflicts (
  conflict_id text PRIMARY KEY,
  canonical_key text NOT NULL,
  field_name text NOT NULL,
  competing_values jsonb NOT NULL,
  chosen_value jsonb NOT NULL,
  chosen_source_id text NOT NULL,
  resolution_rule text NOT NULL,
  resolution_reason text NOT NULL,
  unresolved boolean NOT NULL,
  conflict_hash text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(competing_values) = 'array'),
  CHECK (char_length(conflict_hash) = 64)
);

CREATE TABLE calendar_collection_cursors (
  source_id text NOT NULL,
  operation text NOT NULL,
  subject text NOT NULL,
  window_from date NOT NULL,
  window_to date NOT NULL,
  mapping_version text NOT NULL,
  next_page integer NOT NULL DEFAULT 1,
  continuation text,
  completed boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, operation, subject, window_from, window_to, mapping_version),
  CHECK (window_to >= window_from),
  CHECK (next_page > 0),
  CHECK (continuation IS NULL OR char_length(continuation) BETWEEN 1 AND 512)
);

CREATE TABLE disclosure_risk_state_transitions (
  transition_id text PRIMARY KEY,
  corp_code text NOT NULL,
  state_type text NOT NULL,
  state_key text NOT NULL,
  transition_type text NOT NULL,
  revision_no integer NOT NULL,
  revised_from_transition_id text REFERENCES disclosure_risk_state_transitions(transition_id),
  source_id text NOT NULL,
  source_event_key text NOT NULL,
  source_revision text,
  effective_at timestamptz NOT NULL,
  observed_at timestamptz NOT NULL,
  canonical_event_id text NOT NULL REFERENCES calendar_events(event_id),
  mapping_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (corp_code ~ '^[0-9]{8}$'),
  CHECK (transition_type IN ('OPEN', 'CLOSE')),
  CHECK (revision_no > 0),
  UNIQUE (state_key, revision_no),
  UNIQUE (revised_from_transition_id),
  UNIQUE NULLS NOT DISTINCT (source_id, source_event_key, source_revision)
);

CREATE INDEX calendar_observations_source_effective_idx
  ON calendar_observations (source_id, effective_from, effective_to);
CREATE INDEX trading_sessions_as_of_idx ON trading_sessions (as_of);
CREATE INDEX calendar_events_symbol_date_idx ON calendar_events (symbol, event_date);
CREATE INDEX calendar_conflicts_canonical_key_idx ON calendar_conflicts (canonical_key, created_at);
CREATE INDEX disclosure_state_key_effective_idx
  ON disclosure_risk_state_transitions (state_key, revision_no DESC, observed_at DESC);

CREATE VIEW current_calendar_events AS
SELECT DISTINCT ON (event_series_key)
  event_id,
  event_series_key,
  revision_no,
  revised_from_event_id,
  event_type,
  symbol,
  exchange_mic,
  event_date,
  detail,
  status,
  confidence_bps,
  has_conflict,
  canonical_hash,
  canonical_rule_version,
  created_at
FROM calendar_events
ORDER BY event_series_key, revision_no DESC, created_at DESC, event_id DESC;

CREATE VIEW active_disclosure_risk_states AS
SELECT
  transition_id,
  corp_code,
  state_type,
  state_key,
  revision_no,
  effective_at,
  observed_at,
  canonical_event_id,
  mapping_version
FROM (
  SELECT DISTINCT ON (state_key)
    transition_id,
    corp_code,
    state_type,
    state_key,
    transition_type,
    revision_no,
    effective_at,
    observed_at,
    canonical_event_id,
    mapping_version
  FROM disclosure_risk_state_transitions
  ORDER BY state_key, revision_no DESC, observed_at DESC, transition_id DESC
) latest
WHERE transition_type = 'OPEN';

-- V4 seed를 새 canonical로 이관한 뒤 기존 table을 같은 이름의 read-only projection으로 교체한다.
INSERT INTO trading_sessions (
  exchange_mic,
  session_date,
  is_open,
  open_at,
  close_at,
  timezone,
  reason,
  chosen_source_id,
  degraded,
  fallback_reason,
  as_of,
  confidence_bps,
  has_conflict,
  canonical_hash,
  canonical_rule_version,
  confidence_rule_version,
  created_at,
  updated_at
)
SELECT
  'XKRX',
  calendar_date,
  is_trading_day,
  CASE WHEN is_trading_day THEN (calendar_date + time '09:00') AT TIME ZONE 'Asia/Seoul' END,
  CASE WHEN is_trading_day THEN (calendar_date + time '15:30') AT TIME ZONE 'Asia/Seoul' END,
  'Asia/Seoul',
  holiday_name,
  source,
  true,
  'V4_FIXTURE_COMPATIBILITY',
  created_at,
  3000,
  false,
  md5(market || ':' || calendar_date::text || ':' || is_trading_day::text)
    || md5(market || ':' || calendar_date::text || ':compat'),
  'V4_COMPAT_MIGRATION',
  's1.6-confidence-v1',
  created_at,
  created_at
FROM market_calendar;

DROP TABLE market_calendar;

CREATE VIEW market_calendar AS
SELECT
  'KRX'::text AS market,
  session_date AS calendar_date,
  is_open AS is_trading_day,
  reason AS holiday_name,
  COALESCE(chosen_source_id, 'S1.6_CANONICAL') AS source,
  created_at
FROM trading_sessions
WHERE exchange_mic = 'XKRX';

REVOKE ALL PRIVILEGES ON TABLE
  opendart_quota_usage,
  calendar_source_health,
  calendar_observations,
  trading_sessions,
  calendar_events,
  calendar_event_sources,
  calendar_conflicts,
  calendar_collection_cursors,
  disclosure_risk_state_transitions,
  current_calendar_events,
  active_disclosure_risk_states,
  market_calendar
FROM PUBLIC;

DO $calendar_roles$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_collector') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_collector;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_collector;
    REVOKE CREATE ON SCHEMA public FROM decision_collector;
    GRANT USAGE ON SCHEMA public TO decision_collector;

    GRANT SELECT, INSERT, UPDATE ON TABLE
      opendart_quota_usage,
      calendar_source_health,
      trading_sessions,
      calendar_collection_cursors
    TO decision_collector;
    GRANT SELECT, INSERT ON TABLE
      calendar_observations,
      calendar_events,
      calendar_event_sources,
      calendar_conflicts,
      disclosure_risk_state_transitions
    TO decision_collector;
    GRANT SELECT ON TABLE
      current_calendar_events,
      active_disclosure_risk_states,
      market_calendar
    TO decision_collector;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_collector;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      opendart_quota_usage,
      calendar_source_health,
      calendar_observations,
      calendar_events,
      calendar_event_sources,
      calendar_conflicts,
      calendar_collection_cursors,
      disclosure_risk_state_transitions
    FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE
      trading_sessions,
      current_calendar_events,
      active_disclosure_risk_states,
      market_calendar
    FROM decision_app;
    GRANT SELECT ON TABLE
      trading_sessions,
      current_calendar_events,
      active_disclosure_risk_states,
      market_calendar
    TO decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$calendar_roles$;
