-- Missing optional v3 metadata must not erase durable call accounting.
SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.p1_read_automation_v3_metadata_v1(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE result jsonb;
DECLARE checkpoint_calls integer;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation v3 metadata actor invalid' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  SELECT provider_call_count INTO checkpoint_calls
  FROM public.automation_runtime_checkpoint WHERE run_id=p_run_id;
  checkpoint_calls:=COALESCE(checkpoint_calls,0);
  SELECT jsonb_build_object(
    'candidateSetSha256',usage.candidate_set_sha256,
    'evidenceSetSha256',usage.evidence_set_sha256,
    'groundingQueryCount',COALESCE(usage.grounding_query_count,0),
    'providerCallCount',greatest(COALESCE(usage.provider_call_count,0),checkpoint_calls),
    'screeningProviderCallCount',COALESCE(usage.screening_provider_call_count,0),
    'screenings',COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'evidence',COALESCE((SELECT jsonb_agg(jsonb_build_object(
          'ageWarning',evidence.age_warning,'boundedQuote',evidence.bounded_quote,
          'citationId',evidence.citation_id,'quoteSha256',evidence.quote_sha256,
          'sourceEventDate',evidence.source_event_date,'sourceId',evidence.source_id,
          'sourceType',evidence.source_type,'symbol',evidence.symbol,
          'uriSha256',evidence.uri_sha256,'verified',evidence.verified
        ) ORDER BY evidence.citation_id) FROM public.automation_candidate_evidence evidence
          WHERE evidence.run_id=screening.run_id AND evidence.symbol=screening.symbol),'[]'::jsonb),
        'isEtfEtn',screening.is_etf_etn,'lowerLimitKrw',screening.lower_limit_krw,
        'priceKrw',screening.quote_price_krw,'reason',screening.reason,
        'scoreBps',screening.score_bps,'status',screening.status,'symbol',screening.symbol,
        'upperLimitKrw',screening.upper_limit_krw,'verdict',screening.verdict
      ) ORDER BY screening.symbol)
      FROM public.automation_candidate_screenings screening
      WHERE screening.run_id=p_run_id
    ),'[]'::jsonb)
  ) INTO result
  FROM (SELECT * FROM public.automation_v3_usage WHERE run_id=p_run_id) usage;
  RETURN COALESCE(result,jsonb_build_object(
    'candidateSetSha256',NULL,'evidenceSetSha256',NULL,'groundingQueryCount',0,
    'providerCallCount',checkpoint_calls,'screeningProviderCallCount',0,'screenings','[]'::jsonb
  ))::text;
END
$function$;
