-- P1 Automation V3 market-data foundation. Provider/account/order authority is not added here.

ALTER TABLE public.market_data_manifests
  DROP CONSTRAINT market_data_manifest_kind_check,
  DROP CONSTRAINT market_data_contract_id_check;

ALTER TABLE public.market_data_manifests
  ADD CONSTRAINT market_data_manifest_kind_check
    CHECK (manifest_kind IN ('SEED', 'DAILY', 'AUTOMATION_BOOTSTRAP')),
  ADD CONSTRAINT market_data_contract_id_check CHECK (
    (manifest_kind = 'SEED' AND contract_id = 'market-data-seed.v1')
    OR (manifest_kind = 'DAILY' AND contract_id = 'market-data-daily-shard.v1')
    OR (
      manifest_kind = 'AUTOMATION_BOOTSTRAP'
      AND contract_id = 'p1-automation-market-bootstrap.v1'
    )
  );

CREATE FUNCTION public.p1_read_automation_atr_bars_v1(
  p_symbol text,
  p_as_of_session date,
  p_limit integer
) RETURNS TABLE(
  symbol text,
  session_date date,
  open_price bigint,
  high_price bigint,
  low_price bigint,
  close_price bigint,
  volume bigint,
  temporal_quality text,
  source_receipt_sha256 text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $p1_read_automation_atr_bars_v1$
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_symbol!~'^[0-9]{6}$'
     OR p_as_of_session IS NULL
     OR p_limit NOT BETWEEN 1 AND 101 THEN
    RAISE EXCEPTION 'automation ATR history request is invalid' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.market_data_operational_universe AS universe
    WHERE universe.symbol=p_symbol
  ) THEN
    RAISE EXCEPTION 'automation ATR symbol is outside current exact-31' USING ERRCODE='55000';
  END IF;

  RETURN QUERY
  SELECT bounded.symbol, bounded.session_date, bounded.open_price, bounded.high_price,
         bounded.low_price, bounded.close_price, bounded.volume,
         bounded.temporal_quality, bounded.source_receipt_sha256
  FROM (
    SELECT bars.symbol, bars.session_date, bars.open_price, bars.high_price,
           bars.low_price, bars.close_price, bars.volume,
           bars.temporal_quality, bars.source_receipt_sha256
    FROM public.market_data_operational_bars AS bars
    WHERE bars.symbol=p_symbol AND bars.session_date<p_as_of_session
    ORDER BY bars.session_date DESC
    LIMIT p_limit
  ) AS bounded
  ORDER BY bounded.session_date;
END
$p1_read_automation_atr_bars_v1$;

CREATE FUNCTION public.p1_read_automation_market_history_status_v1()
RETURNS TABLE(
  manifest_count bigint,
  bar_count bigint,
  current_universe_count bigint,
  latest_session date,
  history_status text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $p1_read_automation_market_history_status_v1$
DECLARE
  manifests bigint;
  bars bigint;
  universe_count bigint;
  latest date;
  insufficient_count bigint;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation market history actor is invalid' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO manifests FROM public.market_data_manifests;
  SELECT count(*),max(session_date) INTO bars,latest FROM public.market_data_bars;
  SELECT count(*) INTO universe_count FROM public.market_data_operational_universe;
  SELECT count(*) INTO insufficient_count
  FROM public.market_data_operational_universe AS universe
  WHERE (
    SELECT count(*) FROM public.market_data_operational_bars AS history
    WHERE history.symbol=universe.symbol
  )<23;

  RETURN QUERY SELECT manifests,bars,universe_count,latest,
    CASE
      WHEN manifests=0 OR bars=0 OR universe_count=0 THEN 'EMPTY'
      WHEN universe_count<>31 OR insufficient_count>0 THEN 'PARTIAL'
      ELSE 'READY'
    END;
END
$p1_read_automation_market_history_status_v1$;

ALTER FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_market_history_status_v1() OWNER TO flyway;

REVOKE ALL ON FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer)
  FROM PUBLIC,decision_app,decision_worker,decision_automation_runtime;
REVOKE ALL ON FUNCTION public.p1_read_automation_market_history_status_v1()
  FROM PUBLIC,decision_app,decision_worker,decision_automation_runtime;

GRANT EXECUTE ON FUNCTION public.p1_read_automation_atr_bars_v1(text,date,integer)
  TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_market_history_status_v1()
  TO decision_automation_runtime;

REVOKE SELECT ON TABLE public.market_data_bars,public.market_data_research_bars,
  public.market_data_operational_bars,public.market_data_operational_universe
  FROM decision_automation_runtime;
REVOKE CREATE ON SCHEMA public FROM decision_automation_runtime;
