-- Canonical snapshot decimals are serialized as strings. Accept only bounded decimal strings;
-- a malformed or unavailable value remains NULL instead of becoming a fabricated zero.
CREATE OR REPLACE FUNCTION public.read_decision_input_metrics_owner_v1(p_decision_id text)
RETURNS TABLE(metric text,value numeric,unit text,availability text,observed_at text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $read_decision_input_metrics_owner_v1$
BEGIN
  IF session_user<>'decision_app' OR p_decision_id !~ '^dec_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'decision inputs denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT item->>'metric',
    CASE WHEN jsonb_typeof(item->'value')='number'
                OR (jsonb_typeof(item->'value')='string'
                    AND length(item->>'value')<=40
                    AND item->>'value' ~ '^-?[0-9]+(\.[0-9]+)?$')
         THEN (item->>'value')::numeric ELSE NULL END,
    item->>'unit',item->>'availability',item->>'observedAt'
  FROM public.read_decision_owner_projection() owner_row
  JOIN public.decision_artifacts artifact
    ON artifact.decision_id=owner_row.decision_id
   AND artifact.snapshot_artifact_hash=owner_row.snapshot_artifact_hash
  CROSS JOIN LATERAL jsonb_array_elements(artifact.snapshot_artifact_canonical_json::jsonb->'metrics') item
  WHERE owner_row.decision_id=p_decision_id
    AND item->>'metric' ~ '^[a-z][a-z0-9_]{1,63}$'
  ORDER BY item->>'metric'
  LIMIT 64;
END
$read_decision_input_metrics_owner_v1$;
