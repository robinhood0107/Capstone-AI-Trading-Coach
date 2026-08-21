-- Retire S6.7 runtime capabilities while preserving immutable V78 audit rows.

REVOKE ALL PRIVILEGES ON FUNCTION public.append_cross_market_risk_snapshot_v2(
  uuid, text, text, text, timestamptz, timestamptz, text, text, text, text,
  text, numeric, numeric, text, text, text, timestamptz, text, text, text,
  text, text
) FROM PUBLIC, decision_app, decision_market_writer;

REVOKE ALL PRIVILEGES ON FUNCTION public.read_cross_market_decision_input_v2(
  text, text, timestamptz
) FROM PUBLIC, decision_app, decision_market_writer;

DROP FUNCTION public.append_cross_market_risk_snapshot_v2(
  uuid, text, text, text, timestamptz, timestamptz, text, text, text, text,
  text, numeric, numeric, text, text, text, timestamptz, text, text, text,
  text, text
);

DROP FUNCTION public.read_cross_market_decision_input_v2(text, text, timestamptz);

REVOKE ALL PRIVILEGES ON TABLE public.cross_market_risk_snapshots_v2
FROM PUBLIC, decision_app, decision_market_writer;

COMMENT ON TABLE public.cross_market_risk_snapshots_v2 IS
  'HISTORICAL_ONLY: S6.7 retired by V79; immutable V78 audit rows retained';
