-- Automation V3 evidence-first candidate screening.  Legacy NEWS_CHECKING
-- checkpoints remain valid and no live provider transport is enabled here.

ALTER TABLE public.automation_runs
  DROP CONSTRAINT automation_runs_state_check,
  ADD CONSTRAINT automation_runs_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','NEWS_SCREENING',
    'AI_JUDGING','BUY_CANDIDATE_SELECTED','NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING',
    'RISK_CHECKING','ORDER_SUBMITTING','ORDER_SUBMITTED','PENDING_RECONCILIATION',
    'CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ));

ALTER TABLE public.automation_runtime_checkpoint
  DROP CONSTRAINT automation_runtime_checkpoint_state_check,
  ADD CONSTRAINT automation_runtime_checkpoint_state_check CHECK (state IN (
    'SCHEDULED','PRECHECK','RECONCILING_PREVIOUS','EXIT_SELECTED','NEWS_SCREENING',
    'AI_JUDGING','BUY_CANDIDATE_SELECTED','NEWS_CHECKING','NEWS_VETOED','ORDER_SIZING',
    'RISK_CHECKING','ORDER_SUBMITTING','ORDER_SUBMITTED','PENDING_RECONCILIATION',
    'CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE',
    'SKIPPED_LATE_START','HALTED'
  ));

CREATE OR REPLACE FUNCTION public.p1_automation_transition_valid_v2(p_current text,p_next text)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog
AS $p1_automation_transition_valid_v2$
  SELECT (p_current,p_next) IN (
    ('AI_JUDGING','BUY_CANDIDATE_SELECTED'),('AI_JUDGING','HALTED'),
    ('AI_JUDGING','SKIPPED_DATA_UNAVAILABLE'),('AI_JUDGING','SKIPPED_NO_ACTION'),
    ('BUY_CANDIDATE_SELECTED','HALTED'),('BUY_CANDIDATE_SELECTED','NEWS_CHECKING'),
    ('BUY_CANDIDATE_SELECTED','ORDER_SIZING'),
    ('EXIT_SELECTED','HALTED'),('EXIT_SELECTED','ORDER_SIZING'),
    ('NEWS_SCREENING','AI_JUDGING'),('NEWS_SCREENING','BUY_CANDIDATE_SELECTED'),
    ('NEWS_SCREENING','HALTED'),('NEWS_SCREENING','SKIPPED_DATA_UNAVAILABLE'),
    ('NEWS_SCREENING','SKIPPED_NO_ACTION'),
    ('NEWS_CHECKING','HALTED'),('NEWS_CHECKING','NEWS_VETOED'),
    ('NEWS_CHECKING','ORDER_SIZING'),('NEWS_CHECKING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SIZING','HALTED'),('ORDER_SIZING','RISK_CHECKING'),
    ('ORDER_SIZING','SKIPPED_DATA_UNAVAILABLE'),('ORDER_SIZING','SKIPPED_LATE_START'),
    ('ORDER_SIZING','SKIPPED_NO_ACTION'),('ORDER_SUBMITTED','CANCELLED_UNFILLED'),
    ('ORDER_SUBMITTED','COMPLETED'),('ORDER_SUBMITTED','HALTED'),
    ('ORDER_SUBMITTED','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','HALTED'),
    ('ORDER_SUBMITTING','ORDER_SUBMITTED'),('ORDER_SUBMITTING','ORDER_SUBMITTING'),
    ('ORDER_SUBMITTING','PENDING_RECONCILIATION'),('ORDER_SUBMITTING','SKIPPED_DATA_UNAVAILABLE'),
    ('ORDER_SUBMITTING','SKIPPED_LATE_START'),('PENDING_RECONCILIATION','AI_JUDGING'),
    ('PENDING_RECONCILIATION','BUY_CANDIDATE_SELECTED'),
    ('PENDING_RECONCILIATION','CANCELLED_UNFILLED'),('PENDING_RECONCILIATION','COMPLETED'),
    ('PENDING_RECONCILIATION','EXIT_SELECTED'),('PENDING_RECONCILIATION','HALTED'),
    ('PENDING_RECONCILIATION','NEWS_SCREENING'),
    ('PENDING_RECONCILIATION','PENDING_RECONCILIATION'),
    ('PENDING_RECONCILIATION','SKIPPED_DATA_UNAVAILABLE'),
    ('PENDING_RECONCILIATION','SKIPPED_NO_ACTION'),('PRECHECK','AI_JUDGING'),
    ('PRECHECK','BUY_CANDIDATE_SELECTED'),('PRECHECK','EXIT_SELECTED'),
    ('PRECHECK','HALTED'),('PRECHECK','NEWS_SCREENING'),
    ('PRECHECK','RECONCILING_PREVIOUS'),('PRECHECK','SKIPPED_DATA_UNAVAILABLE'),
    ('PRECHECK','SKIPPED_NO_ACTION'),('RECONCILING_PREVIOUS','AI_JUDGING'),
    ('RECONCILING_PREVIOUS','BUY_CANDIDATE_SELECTED'),
    ('RECONCILING_PREVIOUS','EXIT_SELECTED'),('RECONCILING_PREVIOUS','HALTED'),
    ('RECONCILING_PREVIOUS','NEWS_SCREENING'),
    ('RECONCILING_PREVIOUS','PENDING_RECONCILIATION'),
    ('RECONCILING_PREVIOUS','SKIPPED_DATA_UNAVAILABLE'),
    ('RECONCILING_PREVIOUS','SKIPPED_NO_ACTION'),('RISK_CHECKING','HALTED'),
    ('RISK_CHECKING','ORDER_SUBMITTING'),('RISK_CHECKING','SKIPPED_NO_ACTION'),
    ('SCHEDULED','HALTED'),('SCHEDULED','PRECHECK'),('SCHEDULED','SCHEDULED'),
    ('SCHEDULED','SKIPPED_DATA_UNAVAILABLE'),('SCHEDULED','SKIPPED_LATE_START'),
    ('SCHEDULED','SKIPPED_NO_ACTION')
  )
$p1_automation_transition_valid_v2$;

CREATE TABLE public.automation_v3_usage (
  run_id text PRIMARY KEY REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  provider_call_count integer NOT NULL CHECK (provider_call_count BETWEEN 0 AND 64),
  screening_provider_call_count integer NOT NULL CHECK (screening_provider_call_count BETWEEN 0 AND 1),
  grounding_query_count integer NOT NULL CHECK (grounding_query_count BETWEEN 0 AND 32),
  candidate_set_sha256 text CHECK (candidate_set_sha256 IS NULL OR candidate_set_sha256~'^[0-9a-f]{64}$'),
  evidence_set_sha256 text CHECK (evidence_set_sha256 IS NULL OR evidence_set_sha256~'^[0-9a-f]{64}$'),
  updated_at timestamptz NOT NULL,
  CHECK (grounding_query_count=0 OR screening_provider_call_count=1)
);

CREATE TABLE public.automation_candidate_screenings (
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  symbol text NOT NULL CHECK (symbol~'^[0-9]{6}$'),
  status text NOT NULL CHECK (status IN ('AVAILABLE','ABSTAIN')),
  verdict text NOT NULL CHECK (verdict IN ('VETO_BUY','NO_VETO')),
  score_bps integer NOT NULL CHECK (score_bps BETWEEN 0 AND 10000),
  reason text NOT NULL CHECK (octet_length(reason) BETWEEN 1 AND 512),
  prompt_injection_detected boolean NOT NULL,
  input_sha256 text NOT NULL CHECK (input_sha256~'^[0-9a-f]{64}$'),
  output_sha256 text NOT NULL CHECK (output_sha256~'^[0-9a-f]{64}$'),
  provider_call_count integer NOT NULL CHECK (provider_call_count BETWEEN 0 AND 1),
  quote_price_krw bigint NOT NULL CHECK (quote_price_krw>0),
  lower_limit_krw bigint NOT NULL CHECK (lower_limit_krw>0),
  upper_limit_krw bigint NOT NULL CHECK (upper_limit_krw>=quote_price_krw),
  is_etf_etn boolean NOT NULL,
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,symbol),
  CHECK (lower_limit_krw<=quote_price_krw),
  CHECK (NOT prompt_injection_detected OR (status='ABSTAIN' AND verdict='NO_VETO'))
);

CREATE TABLE public.automation_candidate_evidence (
  run_id text NOT NULL,
  symbol text NOT NULL,
  citation_id text NOT NULL CHECK (citation_id~'^cit_[A-Za-z0-9._:-]{1,96}$'),
  source_id text NOT NULL CHECK (source_id~'^[A-Za-z0-9._:-]{1,128}$'),
  source_type text NOT NULL CHECK (source_type IN ('OFFICIAL_PRIMARY','REGISTERED_INDEPENDENT')),
  source_event_date date,
  age_warning boolean NOT NULL,
  uri_sha256 text NOT NULL CHECK (uri_sha256~'^[0-9a-f]{64}$'),
  bounded_quote text NOT NULL CHECK (char_length(bounded_quote) BETWEEN 1 AND 240),
  quote_sha256 text NOT NULL CHECK (quote_sha256~'^[0-9a-f]{64}$'),
  verified boolean NOT NULL CHECK (verified),
  recorded_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,symbol,citation_id),
  FOREIGN KEY (run_id,symbol) REFERENCES public.automation_candidate_screenings(run_id,symbol)
    ON DELETE RESTRICT
);

-- A committed reservation exists before any SCREEN/JUDGE provider call.  A
-- process death leaves RESERVED and retry=0 fails closed instead of duplicating
-- a physical call.  Only sanitized JUDGE output may be retained for replay.
CREATE TABLE public.automation_ai_provider_operations (
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  phase text NOT NULL CHECK (phase IN ('SCREEN','JUDGE')),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  input_sha256 text NOT NULL CHECK (input_sha256~'^[0-9a-f]{64}$'),
  status text NOT NULL CHECK (status IN ('RESERVED','COMPLETED','FAILED_UNKNOWN')),
  physical_call_cap integer NOT NULL CHECK (physical_call_cap BETWEEN 1 AND 2),
  provider_call_count integer CHECK (provider_call_count BETWEEN 0 AND 2),
  grounding_query_count integer CHECK (grounding_query_count BETWEEN 0 AND 32),
  sanitized_result_json text,
  output_sha256 text CHECK (output_sha256 IS NULL OR output_sha256~'^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,phase),
  CHECK (
    (status='RESERVED' AND provider_call_count IS NULL AND grounding_query_count IS NULL
      AND sanitized_result_json IS NULL AND output_sha256 IS NULL)
    OR (status='FAILED_UNKNOWN' AND sanitized_result_json IS NULL AND output_sha256 IS NULL)
    OR (status='COMPLETED' AND provider_call_count IS NOT NULL
      AND grounding_query_count IS NOT NULL AND output_sha256 IS NOT NULL
      AND provider_call_count<=physical_call_cap
      AND (grounding_query_count=0 OR (phase='SCREEN' AND provider_call_count=1))
      AND ((phase='SCREEN' AND sanitized_result_json IS NULL)
        OR (phase='JUDGE' AND octet_length(sanitized_result_json) BETWEEN 2 AND 16384)))
  )
);

CREATE TRIGGER automation_candidate_screenings_append_only
BEFORE UPDATE OR DELETE ON public.automation_candidate_screenings
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();
CREATE TRIGGER automation_candidate_evidence_append_only
BEFORE UPDATE OR DELETE ON public.automation_candidate_evidence
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

ALTER TABLE public.automation_ai_judgements
  ADD COLUMN evidence_set_sha256 text,
  ADD COLUMN grounding_call_count integer NOT NULL DEFAULT 0,
  ADD COLUMN grounding_query_count integer NOT NULL DEFAULT 0,
  ADD COLUMN evidence_count integer NOT NULL DEFAULT 0,
  ADD CONSTRAINT automation_ai_judgements_evidence_v112_check CHECK (
    (evidence_set_sha256 IS NULL OR evidence_set_sha256~'^[0-9a-f]{64}$')
    AND grounding_call_count BETWEEN 0 AND 1
    AND grounding_query_count BETWEEN 0 AND 32
    AND (grounding_query_count=0 OR grounding_call_count=1)
    AND evidence_count BETWEEN 0 AND 155
  );

ALTER TABLE public.automation_v3_usage OWNER TO flyway;
ALTER TABLE public.automation_candidate_screenings OWNER TO flyway;
ALTER TABLE public.automation_candidate_evidence OWNER TO flyway;
ALTER TABLE public.automation_ai_provider_operations OWNER TO flyway;
ALTER TABLE public.automation_v3_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_v3_usage FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_candidate_screenings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_candidate_screenings FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_candidate_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_candidate_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_ai_provider_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.automation_ai_provider_operations FORCE ROW LEVEL SECURITY;

CREATE POLICY automation_ai_provider_operation_definer_v112
ON public.automation_ai_provider_operations TO PUBLIC
USING (current_user='flyway')
WITH CHECK (current_user='flyway');

REVOKE ALL ON TABLE public.automation_ai_provider_operations
  FROM PUBLIC,decision_app,decision_worker,decision_replay,
       decision_replay_authorizer,decision_automation_runtime;

CREATE POLICY automation_v3_usage_scope_v112 ON public.automation_v3_usage TO PUBLIC
USING (
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
)
WITH CHECK (
  (session_user='decision_app'
    AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
);

CREATE POLICY automation_candidate_screening_scope_v112 ON public.automation_candidate_screenings TO PUBLIC
USING (
  (session_user='decision_app' AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1())
  OR (current_user='flyway' AND session_user='decision_automation_runtime'
    AND user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
)
WITH CHECK (
  session_user='decision_app'
  AND user_id=pg_catalog.current_setting('app.actor_user_id',true)
  AND public.actor_rls_scope_is_open_v1()
);

CREATE POLICY automation_candidate_evidence_scope_v112 ON public.automation_candidate_evidence TO PUBLIC
USING (EXISTS (
  SELECT 1 FROM public.automation_candidate_screenings screening
  WHERE screening.run_id=automation_candidate_evidence.run_id
    AND screening.symbol=automation_candidate_evidence.symbol
    AND (
      (session_user='decision_app'
        AND screening.user_id=pg_catalog.current_setting('app.actor_user_id',true)
        AND public.actor_rls_scope_is_open_v1())
      OR (current_user='flyway' AND session_user='decision_automation_runtime'
        AND screening.user_id=pg_catalog.current_setting('app.automation_owner_user_id',true))
    )
))
WITH CHECK (EXISTS (
  SELECT 1 FROM public.automation_candidate_screenings screening
  WHERE screening.run_id=automation_candidate_evidence.run_id
    AND screening.symbol=automation_candidate_evidence.symbol
    AND session_user='decision_app'
    AND screening.user_id=pg_catalog.current_setting('app.actor_user_id',true)
    AND public.actor_rls_scope_is_open_v1()
));

GRANT SELECT,INSERT,UPDATE ON public.automation_v3_usage TO decision_app;
GRANT SELECT,INSERT ON public.automation_candidate_screenings TO decision_app;
GRANT SELECT,INSERT ON public.automation_candidate_evidence TO decision_app;

CREATE FUNCTION public.p1_reserve_automation_ai_provider_v1(
  p_user_id text,p_run_id text,p_phase text,p_input_sha256 text,p_physical_call_cap integer
) RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_reserve_automation_ai_provider_v1$
DECLARE operation_row public.automation_ai_provider_operations%ROWTYPE;
DECLARE inserted_count integer;
BEGIN
  IF session_user<>'decision_app'
     OR p_user_id<>pg_catalog.current_setting('app.actor_user_id',true)
     OR NOT public.actor_rls_scope_is_open_v1()
     OR p_phase NOT IN ('SCREEN','JUDGE')
     OR p_input_sha256!~'^[0-9a-f]{64}$'
     OR (p_phase='SCREEN' AND p_physical_call_cap<>1)
     OR (p_phase='JUDGE' AND p_physical_call_cap<>2)
     OR NOT EXISTS (
       SELECT 1 FROM public.automation_runs run
       WHERE run.run_id=p_run_id AND run.user_id=p_user_id
         AND run.state=CASE p_phase WHEN 'SCREEN' THEN 'NEWS_SCREENING' ELSE 'AI_JUDGING' END
     ) THEN
    RAISE EXCEPTION 'automation provider reservation denied' USING ERRCODE='42501';
  END IF;
  INSERT INTO public.automation_ai_provider_operations(
    run_id,phase,user_id,input_sha256,status,physical_call_cap,created_at,updated_at
  ) VALUES (
    p_run_id,p_phase,p_user_id,p_input_sha256,'RESERVED',p_physical_call_cap,
    statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (run_id,phase) DO NOTHING;
  GET DIAGNOSTICS inserted_count=ROW_COUNT;
  SELECT * INTO operation_row FROM public.automation_ai_provider_operations
  WHERE run_id=p_run_id AND phase=p_phase;
  IF NOT FOUND OR operation_row.user_id<>p_user_id
     OR operation_row.input_sha256<>p_input_sha256
     OR operation_row.physical_call_cap<>p_physical_call_cap THEN
    RAISE EXCEPTION 'automation provider reservation conflict' USING ERRCODE='40001';
  END IF;
  RETURN jsonb_build_object(
    'created',inserted_count=1,'groundingQueryCount',operation_row.grounding_query_count,
    'providerCallCount',operation_row.provider_call_count,
    'resultJson',operation_row.sanitized_result_json,'status',operation_row.status
  )::text;
END
$p1_reserve_automation_ai_provider_v1$;

CREATE FUNCTION public.p1_complete_automation_ai_provider_v1(
  p_user_id text,p_run_id text,p_phase text,p_input_sha256 text,
  p_provider_call_count integer,p_grounding_query_count integer,
  p_sanitized_result_json text,p_output_sha256 text
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_complete_automation_ai_provider_v1$
DECLARE operation_row public.automation_ai_provider_operations%ROWTYPE;
BEGIN
  IF session_user<>'decision_app'
     OR p_user_id<>pg_catalog.current_setting('app.actor_user_id',true)
     OR NOT public.actor_rls_scope_is_open_v1()
     OR p_phase NOT IN ('SCREEN','JUDGE')
     OR p_input_sha256!~'^[0-9a-f]{64}$'
     OR p_output_sha256!~'^[0-9a-f]{64}$'
     OR p_provider_call_count NOT BETWEEN 0 AND 2
     OR p_grounding_query_count NOT BETWEEN 0 AND 32
     OR (p_grounding_query_count>0 AND (p_phase<>'SCREEN' OR p_provider_call_count<>1))
     OR (p_phase='SCREEN' AND p_sanitized_result_json IS NOT NULL)
     OR (p_phase='JUDGE' AND (p_sanitized_result_json IS NULL
       OR octet_length(p_sanitized_result_json) NOT BETWEEN 2 AND 16384)) THEN
    RAISE EXCEPTION 'automation provider completion invalid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO operation_row FROM public.automation_ai_provider_operations
  WHERE run_id=p_run_id AND phase=p_phase FOR UPDATE;
  IF NOT FOUND OR operation_row.user_id<>p_user_id
     OR operation_row.input_sha256<>p_input_sha256
     OR p_provider_call_count>operation_row.physical_call_cap
     OR operation_row.status<>'RESERVED' THEN
    RAISE EXCEPTION 'automation provider completion conflict' USING ERRCODE='40001';
  END IF;
  UPDATE public.automation_ai_provider_operations SET
    status='COMPLETED',provider_call_count=p_provider_call_count,
    grounding_query_count=p_grounding_query_count,
    sanitized_result_json=p_sanitized_result_json,output_sha256=p_output_sha256,
    updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND phase=p_phase;
END
$p1_complete_automation_ai_provider_v1$;

CREATE FUNCTION public.p1_fail_automation_ai_provider_v1(
  p_user_id text,p_run_id text,p_phase text,p_input_sha256 text
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_fail_automation_ai_provider_v1$
BEGIN
  IF session_user<>'decision_app'
     OR p_user_id<>pg_catalog.current_setting('app.actor_user_id',true)
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation provider failure update denied' USING ERRCODE='42501';
  END IF;
  UPDATE public.automation_ai_provider_operations SET
    status='FAILED_UNKNOWN',provider_call_count=physical_call_cap,
    grounding_query_count=0,updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND phase=p_phase AND user_id=p_user_id
    AND input_sha256=p_input_sha256 AND status='RESERVED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'automation provider failure update conflict' USING ERRCODE='40001';
  END IF;
END
$p1_fail_automation_ai_provider_v1$;

ALTER FUNCTION public.p1_reserve_automation_ai_provider_v1(text,text,text,text,integer) OWNER TO flyway;
ALTER FUNCTION public.p1_complete_automation_ai_provider_v1(text,text,text,text,integer,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_fail_automation_ai_provider_v1(text,text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_reserve_automation_ai_provider_v1(text,text,text,text,integer)
  FROM PUBLIC,decision_app;
REVOKE ALL ON FUNCTION public.p1_complete_automation_ai_provider_v1(text,text,text,text,integer,integer,text,text)
  FROM PUBLIC,decision_app;
REVOKE ALL ON FUNCTION public.p1_fail_automation_ai_provider_v1(text,text,text,text)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_reserve_automation_ai_provider_v1(text,text,text,text,integer)
  TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_complete_automation_ai_provider_v1(text,text,text,text,integer,integer,text,text)
  TO decision_app;
GRANT EXECUTE ON FUNCTION public.p1_fail_automation_ai_provider_v1(text,text,text,text)
  TO decision_app;

CREATE FUNCTION public.p1_read_automation_v3_metadata_v1(p_run_id text,p_claim_token_hash text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_v3_metadata_v1$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE result jsonb;
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
  SELECT jsonb_build_object(
    'candidateSetSha256',usage.candidate_set_sha256,
    'evidenceSetSha256',usage.evidence_set_sha256,
    'groundingQueryCount',COALESCE(usage.grounding_query_count,0),
    'providerCallCount',COALESCE(usage.provider_call_count,0),
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
    'providerCallCount',0,'screeningProviderCallCount',0,'screenings','[]'::jsonb
  ))::text;
END
$p1_read_automation_v3_metadata_v1$;

ALTER FUNCTION public.p1_read_automation_v3_metadata_v1(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_v3_metadata_v1(text,text)
  FROM PUBLIC,decision_app,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_v3_metadata_v1(text,text)
  TO decision_automation_runtime;

CREATE FUNCTION public.p1_read_after_hours_replay_bars_v1(p_manifest_sha256 text)
RETURNS TABLE(
  symbol text,session_date date,open_price bigint,high_price bigint,
  low_price bigint,close_price bigint,volume bigint,temporal_quality text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_after_hours_replay_bars_v1$
BEGIN
  IF session_user<>'decision_replay'
     OR pg_catalog.current_setting('app.after_hours_replay_isolated',true)<>'1'
     OR p_manifest_sha256!~'^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.market_data_manifests manifest
       WHERE manifest.manifest_sha256=p_manifest_sha256 AND manifest.status='ACCEPTED'
     ) THEN
    RAISE EXCEPTION 'after-hours replay input unavailable' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT bars.symbol,bars.session_date,bars.open_price,bars.high_price,
         bars.low_price,bars.close_price,bars.volume,bars.temporal_quality
  FROM public.market_data_bars bars
  WHERE bars.manifest_sha256=p_manifest_sha256
  ORDER BY bars.symbol,bars.session_date;
END
$p1_read_after_hours_replay_bars_v1$;

ALTER FUNCTION public.p1_read_after_hours_replay_bars_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_after_hours_replay_bars_v1(text)
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_after_hours_replay_bars_v1(text) TO decision_replay;
