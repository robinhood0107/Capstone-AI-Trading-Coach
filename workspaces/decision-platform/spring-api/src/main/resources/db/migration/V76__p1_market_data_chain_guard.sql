-- P1.V0 verifies the neutral Market Data predecessor chain without adding provider authority.

CREATE OR REPLACE FUNCTION current_market_data_manifest_head(candidate_session_date DATE)
RETURNS TABLE (manifest_sha256 CHARACTER(64), session_date DATE)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT manifests.manifest_sha256, manifests.session_date
    FROM market_data_manifests AS manifests
    WHERE manifests.status = 'ACCEPTED'
      AND manifests.session_date < candidate_session_date
    ORDER BY manifests.session_date DESC,
             manifests.generation DESC,
             manifests.created_at DESC,
             manifests.manifest_sha256 DESC
    LIMIT 1
$$;

REVOKE ALL ON FUNCTION current_market_data_manifest_head(DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION current_market_data_manifest_head(DATE) TO decision_market_writer;

CREATE OR REPLACE FUNCTION enforce_market_data_daily_chain()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    prior_manifest_sha256 text;
BEGIN
    IF NEW.manifest_kind <> 'DAILY' THEN
        RETURN NEW;
    END IF;

    -- Serialize the small append-only chain independently from application process locks.
    PERFORM pg_advisory_xact_lock(hashtextextended('market-data-daily-chain-v1', 0));

    SELECT manifest_sha256 INTO prior_manifest_sha256
    FROM market_data_manifests
    WHERE status = 'ACCEPTED'
      AND session_date < NEW.session_date
    ORDER BY session_date DESC, generation DESC, created_at DESC, manifest_sha256 DESC
    LIMIT 1;

    IF prior_manifest_sha256 IS NULL
       OR NEW.previous_manifest_sha256 IS DISTINCT FROM prior_manifest_sha256 THEN
        RAISE EXCEPTION 'NEEDS_HUMAN: previous accepted market-data manifest is not the DB head';
    END IF;

    IF NEW.generation = 1 AND EXISTS (
        SELECT 1
        FROM market_data_manifests
        WHERE status = 'ACCEPTED'
          AND manifest_kind = 'DAILY'
          AND session_date > NEW.session_date
    ) THEN
        RAISE EXCEPTION 'NEEDS_HUMAN: daily market-data sessions must append forward';
    END IF;

    RETURN NEW;
END
$function$;

CREATE TRIGGER market_data_manifest_chain_guard
BEFORE INSERT ON market_data_manifests
FOR EACH ROW EXECUTE FUNCTION enforce_market_data_daily_chain();

REVOKE ALL ON FUNCTION enforce_market_data_daily_chain() FROM PUBLIC;
