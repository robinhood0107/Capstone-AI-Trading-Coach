--핵심 도메인 테이블은 Spring/Flyway가 리뷰 가능한 SQL로 소유해 JPA ddl-auto 드리프트를 막는다.
CREATE TABLE users (
  user_id text PRIMARY KEY,
  username text NOT NULL UNIQUE,
  role text NOT NULL CHECK (role IN ('USER', 'ADMIN')),
  password_hash text NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'LOCKED', 'DISABLED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_sessions (
  session_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  refresh_token_hash text NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE principle_presets (
  preset_id text PRIMARY KEY,
  name text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('GUIDE', 'STRICT')),
  rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE principles (
  principle_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  preset_id text REFERENCES principle_presets(preset_id),
  name text NOT NULL,
  mode text NOT NULL CHECK (mode IN ('GUIDE', 'STRICT')),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')),
  current_version integer NOT NULL DEFAULT 0 CHECK (current_version >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE principle_versions (
  principle_version_id text PRIMARY KEY,
  principle_id text NOT NULL REFERENCES principles(principle_id) ON DELETE CASCADE,
  version integer NOT NULL CHECK (version > 0),
  rules_json jsonb NOT NULL DEFAULT '[]'::jsonb,
  summary text,
  created_by text REFERENCES users(user_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT principle_versions_principle_id_version_unique UNIQUE (principle_id, version)
);

CREATE TABLE decisions (
  decision_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id),
  account_id text NOT NULL,
  principle_version_id text NOT NULL REFERENCES principle_versions(principle_version_id),
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('BUY', 'SELL')),
  decision text NOT NULL CHECK (decision IN ('ALLOW', 'WARN', 'HOLD', 'BLOCK')),
  mode text NOT NULL DEFAULT 'GUIDE' CHECK (mode IN ('GUIDE', 'STRICT')),
  reason_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  signal_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz NOT NULL,
  CONSTRAINT decisions_valid_until_after_created_at_check CHECK (valid_until > created_at)
);
CREATE INDEX idx_decisions_account_created_at ON decisions(account_id, created_at DESC);

CREATE TABLE decision_violations (
  violation_id text PRIMARY KEY,
  decision_id text NOT NULL REFERENCES decisions(decision_id) ON DELETE CASCADE,
  rule_id text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('INFO', 'WARN', 'BLOCK')),
  metric text,
  observed_value numeric(10,6),
  threshold_value numeric(10,6),
  message text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE risk_snapshots (
  risk_snapshot_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id),
  account_id text NOT NULL,
  symbol text,
  as_of timestamptz NOT NULL,
  var95 numeric(10,6),
  cvar95 numeric(10,6),
  max_drawdown numeric(10,6),
  exposure_amount bigint,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
  order_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id),
  account_id text NOT NULL,
  decision_id text NOT NULL REFERENCES decisions(decision_id),
  idempotency_key text NOT NULL,
  symbol text NOT NULL,
  side text NOT NULL CHECK (side IN ('BUY', 'SELL')),
  order_type text NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT')),
  quantity bigint NOT NULL CHECK (quantity > 0),
  limit_price bigint,
  status text NOT NULL CHECK (status IN ('REQUESTED', 'SUBMITTED', 'FILLED', 'CANCELED', 'REJECTED', 'HELD')),
  order_intent_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  submitted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT orders_decision_id_unique UNIQUE (decision_id),
  CONSTRAINT orders_idempotency_key_unique UNIQUE (idempotency_key)
);

CREATE TABLE order_events (
  order_event_id text PRIMARY KEY,
  order_id text NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  event_type text NOT NULL,
  event_status text,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE journals (
  journal_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  decision_id text REFERENCES decisions(decision_id),
  order_id text REFERENCES orders(order_id),
  rag_answer_id text,
  title text NOT NULL,
  body text NOT NULL,
  tags text[] NOT NULL DEFAULT '{}',
  source_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);
CREATE INDEX idx_journals_user_created_at ON journals(user_id, created_at DESC);

CREATE TABLE audit_logs (
  audit_log_id text PRIMARY KEY,
  user_id text REFERENCES users(user_id),
  actor_role text,
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text,
  request_id text,
  ip_hash text,
  --원문 credential/API 응답 대신 참조 ID와 hash 중심 payload만 남겨 감사 추적과 보안 경계를 함께 지킨다.
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE data_quality_reports (
  report_id text PRIMARY KEY,
  run_id text NOT NULL,
  producer text NOT NULL,
  symbol text,
  as_of timestamptz NOT NULL,
  quality_score numeric(10,6),
  report_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE financial_engineering_reports (
  report_id text PRIMARY KEY,
  user_id text REFERENCES users(user_id),
  report_type text NOT NULL,
  symbol text,
  input_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE paper_accounts (
  account_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  name text NOT NULL,
  cash_balance bigint NOT NULL DEFAULT 0,
  currency text NOT NULL DEFAULT 'KRW',
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE paper_positions (
  position_id text PRIMARY KEY,
  account_id text NOT NULL REFERENCES paper_accounts(account_id) ON DELETE CASCADE,
  symbol text NOT NULL,
  quantity bigint NOT NULL DEFAULT 0,
  average_price bigint NOT NULL DEFAULT 0,
  market_value bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (account_id, symbol)
);

CREATE TABLE paper_order_events (
  paper_order_event_id text PRIMARY KEY,
  account_id text NOT NULL REFERENCES paper_accounts(account_id) ON DELETE CASCADE,
  order_id text REFERENCES orders(order_id),
  event_type text NOT NULL,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE portfolio_positions (
  portfolio_position_id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES users(user_id),
  account_id text NOT NULL,
  symbol text NOT NULL,
  quantity bigint NOT NULL DEFAULT 0,
  average_price bigint NOT NULL DEFAULT 0,
  market_value bigint NOT NULL DEFAULT 0,
  source text NOT NULL CHECK (source IN ('KIS_MOCK', 'PAPER', 'MANUAL')),
  as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (account_id, symbol, source)
);

CREATE TABLE ingested_signals (
  signal_id text PRIMARY KEY,
  producer text NOT NULL,
  source_workspace text NOT NULL,
  symbol text NOT NULL,
  as_of timestamptz NOT NULL,
  timeframe text NOT NULL,
  confidence numeric(10,6),
  predicted_return numeric(10,6),
  feature_summary_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ingested_signals_producer_symbol_as_of_timeframe_unique UNIQUE (producer, symbol, as_of, timeframe)
);
CREATE INDEX idx_ingested_signals_symbol_as_of ON ingested_signals(symbol, as_of DESC);

CREATE TABLE universe_assets (
  universe_asset_id text PRIMARY KEY,
  universe_id text NOT NULL,
  symbol text NOT NULL,
  asset_name text NOT NULL,
  market text NOT NULL,
  asset_type text NOT NULL,
  included boolean NOT NULL DEFAULT true,
  reason text,
  effective_from date NOT NULL,
  effective_to date,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (universe_id, symbol, effective_from)
);
