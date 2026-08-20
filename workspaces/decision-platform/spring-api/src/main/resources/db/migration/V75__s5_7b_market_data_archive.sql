-- S5.7B neutral market-data archive. No provider, Signal, RiskDecision, or order authority.

CREATE TABLE market_data_manifests (
    manifest_sha256 text PRIMARY KEY,
    manifest_kind text NOT NULL,
    contract_id text NOT NULL,
    session_date date NOT NULL,
    as_of timestamptz NOT NULL,
    generation integer NOT NULL,
    source_manifest_sha256 text NOT NULL,
    previous_manifest_sha256 text,
    supersedes_sha256 text,
    archive_sha256 text NOT NULL,
    receipt_set_sha256 text,
    calendar_revision text NOT NULL,
    calendar_sha256 text NOT NULL,
    temporal_quality text NOT NULL,
    entitlement_expires_at date,
    status text NOT NULL DEFAULT 'ACCEPTED',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT market_data_manifest_sha_check
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_source_manifest_sha_check
        CHECK (source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_previous_manifest_sha_check
        CHECK (previous_manifest_sha256 IS NULL OR previous_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_supersedes_sha_check
        CHECK (supersedes_sha256 IS NULL OR supersedes_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_archive_sha_check
        CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_receipt_set_sha_check
        CHECK (receipt_set_sha256 IS NULL OR receipt_set_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_calendar_sha_check
        CHECK (calendar_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_manifest_kind_check
        CHECK (manifest_kind IN ('SEED', 'DAILY')),
    CONSTRAINT market_data_contract_id_check
        CHECK (
            (manifest_kind = 'SEED' AND contract_id = 'market-data-seed.v1')
            OR (manifest_kind = 'DAILY' AND contract_id = 'market-data-daily-shard.v1')
        ),
    CONSTRAINT market_data_generation_check CHECK (generation >= 1),
    CONSTRAINT market_data_temporal_quality_check
        CHECK (temporal_quality IN (
            'PROVIDER_VINTAGE',
            'PROVIDER_AS_OF_NO_VINTAGE',
            'RECONSTRUCTED_FIXED_LAG',
            'COLLECTION_ONLY'
        )),
    CONSTRAINT market_data_status_check CHECK (status = 'ACCEPTED'),
    CONSTRAINT market_data_generation_supersedes_check CHECK (
        (generation = 1 AND supersedes_sha256 IS NULL)
        OR (generation > 1 AND supersedes_sha256 IS NOT NULL)
    ),
    CONSTRAINT market_data_manifest_generation_unique UNIQUE (manifest_sha256, generation),
    CONSTRAINT market_data_session_generation_unique UNIQUE (session_date, generation),
    CONSTRAINT market_data_supersedes_fk
        FOREIGN KEY (supersedes_sha256) REFERENCES market_data_manifests(manifest_sha256)
);

CREATE OR REPLACE FUNCTION enforce_market_data_manifest_append()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    existing market_data_manifests%ROWTYPE;
    prior market_data_manifests%ROWTYPE;
BEGIN
    SELECT * INTO existing
    FROM market_data_manifests
    WHERE manifest_sha256 = NEW.manifest_sha256;
    IF FOUND THEN
        IF existing.session_date = NEW.session_date
           AND existing.generation = NEW.generation
           AND existing.archive_sha256 = NEW.archive_sha256 THEN
            RETURN NULL;
        END IF;
        RAISE EXCEPTION 'NEEDS_HUMAN: manifest sha identity conflicts with stored bytes';
    END IF;

    SELECT * INTO existing
    FROM market_data_manifests
    WHERE session_date = NEW.session_date
      AND generation = NEW.generation;
    IF FOUND THEN
        RAISE EXCEPTION 'NEEDS_HUMAN: same session generation has a different manifest sha';
    END IF;

    IF NEW.generation > 1 THEN
        SELECT * INTO prior
        FROM market_data_manifests
        WHERE manifest_sha256 = NEW.supersedes_sha256
          AND session_date = NEW.session_date
          AND generation = NEW.generation - 1
          AND status = 'ACCEPTED';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'NEEDS_HUMAN: correction does not supersede the accepted prior generation';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER market_data_manifest_append_guard
BEFORE INSERT ON market_data_manifests
FOR EACH ROW EXECUTE FUNCTION enforce_market_data_manifest_append();

CREATE TABLE market_data_bars (
    manifest_sha256 text NOT NULL,
    generation integer NOT NULL,
    symbol text NOT NULL,
    session_date date NOT NULL,
    open_price bigint NOT NULL,
    high_price bigint NOT NULL,
    low_price bigint NOT NULL,
    close_price bigint NOT NULL,
    volume bigint NOT NULL,
    currency text NOT NULL,
    temporal_quality text NOT NULL,
    source_receipt_sha256 text NOT NULL,
    PRIMARY KEY (symbol, session_date, generation),
    CONSTRAINT market_data_bars_manifest_fk
        FOREIGN KEY (manifest_sha256, generation)
        REFERENCES market_data_manifests(manifest_sha256, generation),
    CONSTRAINT market_data_bars_symbol_check CHECK (symbol ~ '^[0-9A-Z]{6}$'),
    CONSTRAINT market_data_bars_price_check CHECK (
        open_price > 0 AND high_price >= open_price AND high_price >= close_price
        AND low_price > 0 AND low_price <= open_price AND low_price <= close_price
        AND close_price > 0 AND volume >= 0
    ),
    CONSTRAINT market_data_bars_currency_check CHECK (currency = 'KRW'),
    CONSTRAINT market_data_bars_quality_check CHECK (temporal_quality IN (
        'PROVIDER_VINTAGE', 'PROVIDER_AS_OF_NO_VINTAGE',
        'RECONSTRUCTED_FIXED_LAG', 'COLLECTION_ONLY'
    )),
    CONSTRAINT market_data_bars_receipt_check
        CHECK (source_receipt_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX market_data_bars_history_idx
ON market_data_bars(symbol, session_date DESC, generation DESC);

CREATE TABLE market_data_indices (
    manifest_sha256 text NOT NULL,
    generation integer NOT NULL,
    index_id text NOT NULL,
    session_date date NOT NULL,
    close_value double precision NOT NULL,
    temporal_quality text NOT NULL,
    source_receipt_sha256 text NOT NULL,
    PRIMARY KEY (index_id, session_date, generation),
    CONSTRAINT market_data_indices_manifest_fk
        FOREIGN KEY (manifest_sha256, generation)
        REFERENCES market_data_manifests(manifest_sha256, generation),
    CONSTRAINT market_data_indices_id_check CHECK (index_id IN ('KOSPI', 'KOSDAQ')),
    CONSTRAINT market_data_indices_value_check
        CHECK (close_value > 0 AND close_value < 'Infinity'::double precision),
    CONSTRAINT market_data_indices_quality_check CHECK (temporal_quality IN (
        'PROVIDER_VINTAGE', 'PROVIDER_AS_OF_NO_VINTAGE',
        'RECONSTRUCTED_FIXED_LAG', 'COLLECTION_ONLY'
    )),
    CONSTRAINT market_data_indices_receipt_check
        CHECK (source_receipt_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE market_data_macro (
    manifest_sha256 text NOT NULL,
    generation integer NOT NULL,
    series_id text NOT NULL,
    observation_date date NOT NULL,
    available_at timestamptz NOT NULL,
    value_text text NOT NULL,
    temporal_quality text NOT NULL,
    source_receipt_sha256 text NOT NULL,
    entitlement_expires_at date,
    PRIMARY KEY (series_id, observation_date, available_at, generation),
    CONSTRAINT market_data_macro_manifest_fk
        FOREIGN KEY (manifest_sha256, generation)
        REFERENCES market_data_manifests(manifest_sha256, generation),
    CONSTRAINT market_data_macro_series_check
        CHECK (series_id IN ('722Y001/0101000/D', '731Y001/0000001/D')),
    CONSTRAINT market_data_macro_value_check CHECK (char_length(value_text) BETWEEN 1 AND 64),
    CONSTRAINT market_data_macro_quality_check CHECK (temporal_quality IN (
        'PROVIDER_VINTAGE', 'PROVIDER_AS_OF_NO_VINTAGE',
        'RECONSTRUCTED_FIXED_LAG', 'COLLECTION_ONLY'
    )),
    CONSTRAINT market_data_macro_receipt_check
        CHECK (source_receipt_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE market_data_universes (
    manifest_sha256 text NOT NULL,
    generation integer NOT NULL,
    membership_month text NOT NULL,
    selection_session date NOT NULL,
    effective_from_session date NOT NULL,
    instrument_id text NOT NULL,
    symbol text NOT NULL,
    market text NOT NULL,
    rank integer NOT NULL,
    is_fixed_member boolean NOT NULL,
    temporal_quality text NOT NULL,
    source_receipt_sha256 text NOT NULL,
    PRIMARY KEY (membership_month, symbol, generation),
    CONSTRAINT market_data_universes_manifest_fk
        FOREIGN KEY (manifest_sha256, generation)
        REFERENCES market_data_manifests(manifest_sha256, generation),
    CONSTRAINT market_data_universes_month_check
        CHECK (membership_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT market_data_universes_identity_check
        CHECK (instrument_id ~ '^[0-9A-Z]{12}$' OR instrument_id = 'XKRX:ETF:132030'),
    CONSTRAINT market_data_universes_symbol_check CHECK (symbol ~ '^[0-9A-Z]{6}$'),
    CONSTRAINT market_data_universes_market_check CHECK (market IN ('KOSPI', 'KOSDAQ')),
    CONSTRAINT market_data_universes_rank_check CHECK (rank BETWEEN 1 AND 31),
    CONSTRAINT market_data_universes_fixed_check CHECK (
        (symbol = '132030' AND rank = 31 AND is_fixed_member)
        OR (symbol <> '132030' AND rank <= 30 AND NOT is_fixed_member)
    ),
    CONSTRAINT market_data_universes_quality_check CHECK (temporal_quality IN (
        'PROVIDER_VINTAGE', 'PROVIDER_AS_OF_NO_VINTAGE',
        'RECONSTRUCTED_FIXED_LAG', 'COLLECTION_ONLY'
    )),
    CONSTRAINT market_data_universes_receipt_check
        CHECK (source_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT market_data_universes_rank_unique
        UNIQUE (membership_month, rank, generation)
);

CREATE VIEW market_data_operational_universe
WITH (security_barrier = true)
AS
SELECT membership_month, symbol, market, rank, is_fixed_member, source_receipt_sha256
FROM market_data_universes
WHERE membership_month = (SELECT max(membership_month) FROM market_data_universes)
  AND generation = (
      SELECT max(generation)
      FROM market_data_universes
      WHERE membership_month = (SELECT max(membership_month) FROM market_data_universes)
  )
ORDER BY rank;

CREATE VIEW market_data_operational_bars
WITH (security_barrier = true)
AS
SELECT symbol, session_date, open_price, high_price, low_price, close_price, volume,
       currency, temporal_quality, source_receipt_sha256
FROM (
    SELECT latest.*,
           row_number() OVER (
               PARTITION BY latest.symbol ORDER BY latest.session_date DESC
           ) AS history_rank
    FROM (
        SELECT DISTINCT ON (b.symbol, b.session_date) b.*
        FROM market_data_bars b
        JOIN market_data_operational_universe u ON u.symbol = b.symbol
        ORDER BY b.symbol, b.session_date, b.generation DESC
    ) latest
) bounded
WHERE history_rank <= 253;

CREATE VIEW market_data_research_bars
WITH (security_barrier = true)
AS
SELECT symbol, session_date, open_price, high_price, low_price, close_price, volume,
       currency, temporal_quality, source_receipt_sha256
FROM (
    SELECT latest.*,
           row_number() OVER (
               PARTITION BY latest.symbol ORDER BY latest.session_date DESC
           ) AS history_rank
    FROM (
        SELECT DISTINCT ON (symbol, session_date) *
        FROM market_data_bars
        ORDER BY symbol, session_date, generation DESC
    ) latest
) bounded
WHERE history_rank <= 1260;

CREATE VIEW market_data_research_indices
WITH (security_barrier = true)
AS
SELECT index_id, session_date, close_value, temporal_quality, source_receipt_sha256
FROM (
    SELECT latest.*,
           row_number() OVER (
               PARTITION BY latest.index_id ORDER BY latest.session_date DESC
           ) AS history_rank
    FROM (
        SELECT DISTINCT ON (index_id, session_date) *
        FROM market_data_indices
        ORDER BY index_id, session_date, generation DESC
    ) latest
) bounded
WHERE history_rank <= 1260;

CREATE OR REPLACE FUNCTION prune_market_data_macro(
    p_as_of date,
    p_apply boolean DEFAULT false
)
RETURNS TABLE(candidate_rows bigint, deleted_rows bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    candidate_count bigint;
    removed_count bigint := 0;
BEGIN
    IF NOT pg_has_role(session_user, 'decision_market_retention_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'market-data retention authority is required';
    END IF;
    SELECT count(*) INTO candidate_count
    FROM market_data_macro
    WHERE observation_date < p_as_of - 365
       OR entitlement_expires_at < p_as_of;
    IF p_apply THEN
        DELETE FROM market_data_macro
        WHERE observation_date < p_as_of - 365
           OR entitlement_expires_at < p_as_of;
        GET DIAGNOSTICS removed_count = ROW_COUNT;
    END IF;
    RETURN QUERY SELECT candidate_count, removed_count;
END
$function$;

REVOKE ALL PRIVILEGES ON TABLE
    market_data_manifests,
    market_data_bars,
    market_data_indices,
    market_data_macro,
    market_data_universes,
    market_data_operational_universe,
    market_data_operational_bars,
    market_data_research_bars,
    market_data_research_indices
FROM PUBLIC, decision_app, decision_market_writer;

REVOKE ALL PRIVILEGES ON FUNCTION
    enforce_market_data_manifest_append(),
    prune_market_data_macro(date, boolean)
FROM PUBLIC, decision_app, decision_market_writer;

DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
        GRANT INSERT ON TABLE
            market_data_manifests,
            market_data_bars,
            market_data_indices,
            market_data_macro,
            market_data_universes
        TO decision_market_writer;
        REVOKE SELECT, UPDATE, DELETE, TRUNCATE ON TABLE
            market_data_manifests,
            market_data_bars,
            market_data_indices,
            market_data_macro,
            market_data_universes
        FROM decision_market_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_operational_reader') THEN
        GRANT SELECT ON TABLE
            market_data_operational_universe,
            market_data_operational_bars
        TO decision_market_operational_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_research_reader') THEN
        GRANT SELECT ON TABLE
            market_data_research_bars,
            market_data_research_indices,
            market_data_macro,
            market_data_universes
        TO decision_market_research_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_retention_admin') THEN
        GRANT EXECUTE ON FUNCTION prune_market_data_macro(date, boolean)
        TO decision_market_retention_admin;
    END IF;
END
$grants$;

DO $schema_grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer') THEN
        REVOKE CREATE ON SCHEMA public FROM decision_market_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_operational_reader') THEN
        REVOKE CREATE ON SCHEMA public FROM decision_market_operational_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_research_reader') THEN
        REVOKE CREATE ON SCHEMA public FROM decision_market_research_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_retention_admin') THEN
        REVOKE CREATE ON SCHEMA public FROM decision_market_retention_admin;
    END IF;
END
$schema_grants$;
