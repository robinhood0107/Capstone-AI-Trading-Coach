-- 기존 COMPLETE는 보존하되 현재 producer/horizon 완결성과 구분한다.
SET LOCAL row_security=on;
CREATE OR REPLACE FUNCTION public.p1_read_daily_inference_context_v1(p_target_session date)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_daily_inference_context_v1$
DECLARE model_row record;
DECLARE source_row record;
DECLARE symbols_json jsonb;
DECLARE existing_batch public.p1_return_daily_signal_batch%ROWTYPE;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_target_session IS NULL THEN
    RAISE EXCEPTION 'daily inference context denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO model_row FROM public.current_p1_return_model_pointer LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO existing_batch FROM public.p1_return_daily_signal_batch
  WHERE bundle_sha256=model_row.bundle_sha256 AND target_session=p_target_session
    AND status='COMPLETE';
  IF FOUND THEN
    RETURN jsonb_build_object(
      'batchSha256',existing_batch.batch_sha256,'outcome','REPLAYED',
      'sourceSession',existing_batch.source_session,'targetSession',existing_batch.target_session,
      'currentContractComplete',(
        existing_batch.model_sha256=model_row.model_sha256
        AND existing_batch.market_manifest_sha256=(SELECT manifest_sha256 FROM public.market_data_manifests
          WHERE session_date=existing_batch.source_session AND status='ACCEPTED' ORDER BY generation DESC LIMIT 1)
        AND (SELECT count(*) FROM public.p1_return_daily_signal_projection WHERE batch_sha256=existing_batch.batch_sha256)=62
        AND NOT EXISTS (
          SELECT seed.symbol,producer.name FROM public.p1_return_model_seed_signal seed
          CROSS JOIN (VALUES ('LSTM'),('RULE_BASELINE')) producer(name)
          WHERE seed.bundle_sha256=model_row.bundle_sha256 AND seed.producer='LSTM'
          EXCEPT SELECT signal.symbol,signal.producer FROM public.p1_return_daily_signal_projection signal
          WHERE signal.batch_sha256=existing_batch.batch_sha256
        )
        AND (SELECT count(*) FROM public.p1_ridge_daily_forecasts WHERE batch_sha256=existing_batch.batch_sha256)=31
        AND NOT EXISTS (
          SELECT seed.symbol FROM public.p1_return_model_seed_signal seed
          WHERE seed.bundle_sha256=model_row.bundle_sha256 AND seed.producer='LSTM'
          EXCEPT SELECT ridge.symbol FROM public.p1_ridge_daily_forecasts ridge WHERE ridge.batch_sha256=existing_batch.batch_sha256
        )
        AND NOT EXISTS (
          SELECT 1 FROM public.p1_ridge_daily_forecasts ridge
          WHERE ridge.batch_sha256=existing_batch.batch_sha256 AND (
            ridge.model_json->>'sourceSession'<>existing_batch.source_session::text
            OR jsonb_typeof(ridge.forecasts)<>'array'
            OR (SELECT array_agg((f->>'horizonSessions')::integer ORDER BY (f->>'horizonSessions')::integer)
                FROM jsonb_array_elements(ridge.forecasts) f) IS DISTINCT FROM ARRAY[1,5,20]
          )
        )
      )
    )::text;
  END IF;
  SELECT manifest.manifest_sha256,manifest.session_date INTO source_row
  FROM public.market_data_manifests manifest
  WHERE manifest.status='ACCEPTED' AND manifest.session_date<p_target_session
  ORDER BY manifest.session_date DESC,manifest.generation DESC LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT jsonb_agg(seed.symbol ORDER BY seed.symbol) INTO symbols_json
  FROM public.p1_return_model_seed_signal seed
  WHERE seed.bundle_sha256=model_row.bundle_sha256 AND seed.producer='LSTM';
  IF jsonb_array_length(COALESCE(symbols_json,'[]'::jsonb))<>31 THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'artifactId',model_row.artifact_id,'bundleSha256',model_row.bundle_sha256,
    'marketManifestSha256',source_row.manifest_sha256,'modelSha256',model_row.model_sha256,
    'outcome','MATERIALIZE','sourceSession',source_row.session_date,
    'symbols',symbols_json,'targetSession',p_target_session
  )::text;
END
$p1_read_daily_inference_context_v1$;
