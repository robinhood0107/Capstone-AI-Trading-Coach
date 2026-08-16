-- S5.6B immutable LightGBM model release, exact-31 batch와 역할별 capability 경계.
ALTER TABLE public.ingested_signals
  ADD COLUMN model_release_id text,
  ADD COLUMN signal_batch_id text;

DROP INDEX public.ingested_signals_s5_as_of_fixture_unique;
CREATE UNIQUE INDEX ingested_signals_s5_as_of_fixture_release_unique
  ON public.ingested_signals (
    producer, symbol, as_of, timeframe, fixture,
    COALESCE(model_release_id, ''), COALESCE(signal_batch_id, '')
  );

CREATE TABLE public.signal_universe_releases (
  universe_release_id text PRIMARY KEY CHECK (universe_release_id ~ '^sur-[0-9a-f]{12}$'),
  policy_version text NOT NULL CHECK (policy_version = 'top30-plus-132030-v1'),
  membership_sha256 text NOT NULL UNIQUE CHECK (membership_sha256 ~ '^[0-9a-f]{64}$'),
  member_count integer NOT NULL CHECK (member_count = 31),
  fixture boolean NOT NULL CHECK (fixture = false),
  provenance_class text NOT NULL CHECK (provenance_class = 'PRODUCTION'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.signal_model_releases (
  model_release_id text PRIMARY KEY CHECK (model_release_id ~ '^lgr-[0-9a-f]{12}$'),
  model_version text NOT NULL CHECK (model_version ~ '^lgbm-v1-[0-9a-f]{12}$'),
  model_report_id text NOT NULL CHECK (model_report_id ~ '^mrp-[0-9a-f]{12}$'),
  release_manifest_sha256 text NOT NULL UNIQUE CHECK (release_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  feature_manifest_sha256 text NOT NULL CHECK (feature_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  source_bundle_set_sha256 text NOT NULL CHECK (source_bundle_set_sha256 ~ '^[0-9a-f]{64}$'),
  training_dataset_sha256 text NOT NULL CHECK (training_dataset_sha256 ~ '^[0-9a-f]{64}$'),
  code_head text NOT NULL CHECK (code_head ~ '^[0-9a-f]{40}$'),
  code_tree text NOT NULL CHECK (code_tree ~ '^[0-9a-f]{40}$'),
  uv_lock_sha256 text NOT NULL CHECK (uv_lock_sha256 ~ '^[0-9a-f]{64}$'),
  temporal_quality text NOT NULL CHECK (temporal_quality = 'RECONSTRUCTED_FIXED_LAG'),
  qualification_status text NOT NULL CHECK (qualification_status = 'QUALIFIED'),
  lifecycle_status text NOT NULL CHECK (lifecycle_status IN ('STAGED','ACCEPTED','SUSPENDED')),
  fixture boolean NOT NULL CHECK (fixture = false),
  provenance_class text NOT NULL CHECK (provenance_class = 'PRODUCTION'),
  staged_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.signal_model_release_transitions (
  transition_id text PRIMARY KEY CHECK (transition_id ~ '^smrt-[0-9a-f]{24}$'),
  model_release_id text NOT NULL REFERENCES public.signal_model_releases(model_release_id),
  from_status text CHECK (from_status IS NULL OR from_status IN ('STAGED','ACCEPTED','SUSPENDED')),
  to_status text NOT NULL CHECK (to_status IN ('STAGED','ACCEPTED','SUSPENDED')),
  reason text NOT NULL CHECK (reason IN ('STAGED','MANUAL_ACTIVATION','MANUAL_ROLLBACK','ARTIFACT_DRIFT')),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  actor_role text NOT NULL CHECK (actor_role IN ('decision_signal_writer','decision_signal_scheduler','decision_signal_admin')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (model_release_id, to_status, reason, evidence_sha256)
);

CREATE TABLE public.signal_batches (
  signal_batch_id text PRIMARY KEY CHECK (signal_batch_id ~ '^sgb-[0-9a-f]{12}$'),
  model_release_id text NOT NULL REFERENCES public.signal_model_releases(model_release_id),
  universe_release_id text NOT NULL REFERENCES public.signal_universe_releases(universe_release_id),
  batch_manifest_sha256 text NOT NULL UNIQUE CHECK (batch_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  membership_sha256 text NOT NULL CHECK (membership_sha256 ~ '^[0-9a-f]{64}$'),
  session_date date NOT NULL,
  as_of timestamptz NOT NULL,
  timeframe text NOT NULL CHECK (timeframe = '1d'),
  row_count integer NOT NULL CHECK (row_count = 31),
  status text NOT NULL CHECK (status IN ('STAGED','FINALIZED')),
  fixture boolean NOT NULL CHECK (fixture = false),
  provenance_class text NOT NULL CHECK (provenance_class = 'PRODUCTION'),
  staged_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.signal_batch_members (
  signal_batch_id text NOT NULL REFERENCES public.signal_batches(signal_batch_id),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  status text NOT NULL CHECK (status = 'AVAILABLE'),
  signal text NOT NULL CHECK (signal IN ('BUY','HOLD','SELL')),
  confidence numeric NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  as_of timestamptz NOT NULL,
  model_version text NOT NULL,
  model_report_id text NOT NULL,
  logical_identity_sha256 text NOT NULL UNIQUE
    REFERENCES public.ingested_signals(logical_identity_sha256),
  PRIMARY KEY (signal_batch_id, symbol)
);

CREATE TABLE public.active_signal_model_release (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  model_release_id text NOT NULL REFERENCES public.signal_model_releases(model_release_id),
  generation bigint NOT NULL CHECK (generation > 0),
  activated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.active_signal_batch (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  signal_batch_id text NOT NULL REFERENCES public.signal_batches(signal_batch_id),
  generation bigint NOT NULL CHECK (generation > 0),
  published_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE public.signal_batch_publications (
  publication_id text PRIMARY KEY CHECK (publication_id ~ '^sbp-[0-9a-f]{24}$'),
  signal_batch_id text NOT NULL UNIQUE REFERENCES public.signal_batches(signal_batch_id),
  model_release_id text NOT NULL REFERENCES public.signal_model_releases(model_release_id),
  generation bigint NOT NULL UNIQUE CHECK (generation > 0),
  reason text NOT NULL CHECK (reason IN ('MANUAL_ACTIVATION','MANUAL_ROLLBACK','DAILY_PUBLISH')),
  actor_role text NOT NULL CHECK (actor_role IN ('decision_signal_scheduler','decision_signal_admin')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

DO $rls$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'signal_universe_releases', 'signal_model_releases',
    'signal_model_release_transitions', 'signal_batches', 'signal_batch_members',
    'active_signal_model_release', 'active_signal_batch', 'signal_batch_publications'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR ALL TO flyway USING (true) WITH CHECK (true)',
      table_name || '_flyway_function_policy', table_name
    );
    EXECUTE format(
      'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC, decision_app, decision_signal_writer, decision_signal_scheduler, decision_signal_admin',
      table_name
    );
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE public.%I TO flyway', table_name);
  END LOOP;
END
$rls$;

CREATE FUNCTION public.stage_signal_model_release(
  p_release_manifest_sha256 text,
  p_model_release_id text,
  p_model_version text,
  p_model_report_id text,
  p_feature_manifest_sha256 text,
  p_source_bundle_set_sha256 text,
  p_training_dataset_sha256 text,
  p_code_head text,
  p_code_tree text,
  p_uv_lock_sha256 text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_signal_model_release$
DECLARE
  existing_manifest text;
  transition_digest text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_signal_writer'
     OR p_release_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_model_release_id !~ '^lgr-[0-9a-f]{12}$'
     OR p_model_version !~ '^lgbm-v1-[0-9a-f]{12}$'
     OR p_model_report_id !~ '^mrp-[0-9a-f]{12}$'
     OR p_feature_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_source_bundle_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_training_dataset_sha256 !~ '^[0-9a-f]{64}$'
     OR p_code_head !~ '^[0-9a-f]{40}$' OR p_code_tree !~ '^[0-9a-f]{40}$'
     OR p_uv_lock_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S5 model release arguments are invalid' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_model_release_id, 0));
  SELECT release_manifest_sha256 INTO existing_manifest
  FROM public.signal_model_releases WHERE model_release_id = p_model_release_id FOR SHARE;
  IF FOUND THEN
    IF existing_manifest = p_release_manifest_sha256 THEN RETURN 'REPLAYED'; END IF;
    RAISE EXCEPTION 'S5 model release identity conflict' USING ERRCODE = '23505';
  END IF;
  INSERT INTO public.signal_model_releases(
    model_release_id, model_version, model_report_id, release_manifest_sha256,
    feature_manifest_sha256, source_bundle_set_sha256, training_dataset_sha256,
    code_head, code_tree, uv_lock_sha256, temporal_quality, qualification_status,
    lifecycle_status, fixture, provenance_class
  ) VALUES (
    p_model_release_id, p_model_version, p_model_report_id, p_release_manifest_sha256,
    p_feature_manifest_sha256, p_source_bundle_set_sha256, p_training_dataset_sha256,
    p_code_head, p_code_tree, p_uv_lock_sha256, 'RECONSTRUCTED_FIXED_LAG', 'QUALIFIED',
    'STAGED', false, 'PRODUCTION'
  );
  transition_digest := encode(digest(convert_to(
    concat_ws(E'\n', 's5-model-transition-v1', p_model_release_id, 'STAGED', p_release_manifest_sha256),
    'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.signal_model_release_transitions(
    transition_id, model_release_id, from_status, to_status, reason, evidence_sha256, actor_role
  ) VALUES (
    'smrt-' || substr(transition_digest, 1, 24), p_model_release_id, NULL, 'STAGED',
    'STAGED', p_release_manifest_sha256, session_user
  );
  RETURN 'INSERTED';
END
$stage_signal_model_release$;
ALTER FUNCTION public.stage_signal_model_release(text,text,text,text,text,text,text,text,text,text) OWNER TO flyway;

CREATE FUNCTION public.stage_signal_batch(
  p_batch_manifest_sha256 text,
  p_signal_batch_id text,
  p_model_release_id text,
  p_universe_release_id text,
  p_membership_sha256 text,
  p_session_date date,
  p_as_of timestamptz,
  p_members_canonical_text text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_signal_batch$
DECLARE
  members jsonb;
  member jsonb;
  existing_manifest text;
  identity_digest text;
  payload_text text;
  payload_digest text;
  generated_signal_id text;
  member_count integer;
  computed_membership_sha256 text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_signal_writer'
     OR p_batch_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_signal_batch_id !~ '^sgb-[0-9a-f]{12}$'
     OR p_model_release_id !~ '^lgr-[0-9a-f]{12}$'
     OR p_universe_release_id !~ '^sur-[0-9a-f]{12}$'
     OR p_membership_sha256 !~ '^[0-9a-f]{64}$'
     OR p_as_of IS NULL OR octet_length(p_members_canonical_text) NOT BETWEEN 2 AND 262144 THEN
    RAISE EXCEPTION 'S5 signal batch arguments are invalid' USING ERRCODE = '22023';
  END IF;
  members := p_members_canonical_text::jsonb;
  IF jsonb_typeof(members) <> 'array' OR jsonb_array_length(members) <> 31 THEN
    RAISE EXCEPTION 'S5 signal batch must contain exact 31 members' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.signal_model_releases
    WHERE model_release_id = p_model_release_id AND qualification_status = 'QUALIFIED'
      AND fixture = false AND provenance_class = 'PRODUCTION'
  ) THEN
    RAISE EXCEPTION 'S5 signal batch model release is unavailable' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_signal_batch_id, 0));
  SELECT batch_manifest_sha256 INTO existing_manifest
  FROM public.signal_batches WHERE signal_batch_id = p_signal_batch_id FOR SHARE;
  IF FOUND THEN
    IF existing_manifest = p_batch_manifest_sha256 THEN RETURN 'REPLAYED'; END IF;
    RAISE EXCEPTION 'S5 signal batch identity conflict' USING ERRCODE = '23505';
  END IF;
  INSERT INTO public.signal_universe_releases(
    universe_release_id, policy_version, membership_sha256, member_count, fixture, provenance_class
  ) VALUES (
    p_universe_release_id, 'top30-plus-132030-v1', p_membership_sha256, 31, false, 'PRODUCTION'
  ) ON CONFLICT (universe_release_id) DO NOTHING;
  IF NOT EXISTS (
    SELECT 1 FROM public.signal_universe_releases
    WHERE universe_release_id = p_universe_release_id AND membership_sha256 = p_membership_sha256
  ) THEN
    RAISE EXCEPTION 'S5 universe release identity conflict' USING ERRCODE = '23505';
  END IF;
  INSERT INTO public.signal_batches(
    signal_batch_id, model_release_id, universe_release_id, batch_manifest_sha256,
    membership_sha256, session_date, as_of, timeframe, row_count, status, fixture, provenance_class
  ) VALUES (
    p_signal_batch_id, p_model_release_id, p_universe_release_id, p_batch_manifest_sha256,
    p_membership_sha256, p_session_date, p_as_of, '1d', 31, 'STAGED', false, 'PRODUCTION'
  );
  FOR member IN SELECT value FROM jsonb_array_elements(members) LOOP
    IF jsonb_typeof(member) <> 'object'
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(member) key) <>
          ARRAY['asOf','confidence','modelReportId','modelVersion','signal','status','symbol']
       OR member->>'symbol' !~ '^[0-9]{6}$'
       OR member->>'status' <> 'AVAILABLE'
       OR member->>'signal' NOT IN ('BUY','HOLD','SELL')
       OR (member->>'asOf')::timestamptz <> p_as_of
       OR (member->>'confidence')::numeric < 0 OR (member->>'confidence')::numeric > 1
       OR NOT EXISTS (
         SELECT 1 FROM public.signal_model_releases release
         WHERE release.model_release_id = p_model_release_id
           AND release.model_version = member->>'modelVersion'
           AND release.model_report_id = member->>'modelReportId'
       ) THEN
      RAISE EXCEPTION 'S5 signal batch member is invalid' USING ERRCODE = '22023';
    END IF;
    payload_text := jsonb_build_object(
      'asOf', member->>'asOf', 'confidence', (member->>'confidence')::numeric,
      'modelReportId', member->>'modelReportId', 'modelVersion', member->>'modelVersion',
      'signal', member->>'signal', 'status', 'AVAILABLE', 'symbol', member->>'symbol'
    )::text;
    payload_digest := encode(digest(convert_to(payload_text, 'UTF8'), 'sha256'), 'hex');
    identity_digest := encode(digest(convert_to(concat_ws(E'\n',
      'signal-v2-production-release-identity-v1', p_model_release_id, p_signal_batch_id,
      member->>'symbol', p_session_date::text, '1d'), 'UTF8'), 'sha256'), 'hex');
    generated_signal_id := 'sigv2_' || substr(identity_digest, 1, 24);
    INSERT INTO public.ingested_signals(
      signal_id, producer, source_workspace, symbol, as_of, timeframe, confidence,
      predicted_return, feature_summary_json, payload_json, contract_version, status,
      reason, signal, evaluation_id, model_version, model_report_id, artifact_sha256,
      payload_sha256, provenance_sha256, logical_identity_sha256, fixture,
      provenance_class, payload_canonical_text, artifact_verified, session_date,
      model_release_id, signal_batch_id
    ) VALUES (
      generated_signal_id, 'LIGHTGBM', 'decision-platform', member->>'symbol', p_as_of, '1d',
      (member->>'confidence')::numeric, NULL, '[]'::jsonb, payload_text::jsonb,
      'signal-v2-runtime-v1', 'AVAILABLE', NULL, member->>'signal', p_signal_batch_id,
      member->>'modelVersion', member->>'modelReportId', p_batch_manifest_sha256,
      payload_digest, p_batch_manifest_sha256, identity_digest, false, 'PRODUCTION',
      payload_text, true, p_session_date, p_model_release_id, p_signal_batch_id
    );
    INSERT INTO public.signal_batch_members(
      signal_batch_id, symbol, status, signal, confidence, as_of, model_version,
      model_report_id, logical_identity_sha256
    ) VALUES (
      p_signal_batch_id, member->>'symbol', 'AVAILABLE', member->>'signal',
      (member->>'confidence')::numeric, p_as_of, member->>'modelVersion',
      member->>'modelReportId', identity_digest
    );
  END LOOP;
  SELECT count(*) INTO member_count FROM public.signal_batch_members
  WHERE signal_batch_id = p_signal_batch_id;
  SELECT encode(digest(
    convert_to('s5-inference-universe-v1', 'UTF8') || decode('00', 'hex') ||
      convert_to('[' || string_agg(to_jsonb(symbol)::text, ',' ORDER BY symbol) || ']', 'UTF8'),
    'sha256'), 'hex')
  INTO computed_membership_sha256
  FROM public.signal_batch_members WHERE signal_batch_id = p_signal_batch_id;
  IF member_count <> 31 OR NOT EXISTS (
    SELECT 1 FROM public.signal_batch_members
    WHERE signal_batch_id = p_signal_batch_id AND symbol = '132030'
  ) OR computed_membership_sha256 <> p_membership_sha256
     OR p_universe_release_id <> 'sur-' || substr(computed_membership_sha256, 1, 12) THEN
    RAISE EXCEPTION 'S5 signal batch is incomplete' USING ERRCODE = '22023';
  END IF;
  UPDATE public.signal_batches SET status = 'FINALIZED' WHERE signal_batch_id = p_signal_batch_id;
  RETURN 'INSERTED';
END
$stage_signal_batch$;
ALTER FUNCTION public.stage_signal_batch(text,text,text,text,text,date,timestamptz,text) OWNER TO flyway;

-- 배치가 가리키는 session은 다음 XKRX session 08:10 KST에만 publish 가능하다.
-- conflict calendar를 조용히 사용하지 않고 row가 없게 만들어 호출자가 fail-closed한다.
CREATE FUNCTION public.current_s5_signal_batch_clock()
RETURNS TABLE(session_date date, as_of timestamptz)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $current_s5_signal_batch_clock$
  SELECT prior_session.session_date,
    (next_session.session_date + time '08:10') AT TIME ZONE 'Asia/Seoul'
  FROM public.trading_sessions prior_session
  JOIN LATERAL (
    SELECT candidate.session_date
    FROM public.trading_sessions candidate
    WHERE candidate.exchange_mic = 'XKRX' AND candidate.is_open
      AND candidate.has_conflict = false
      AND candidate.session_date > prior_session.session_date
    ORDER BY candidate.session_date
    LIMIT 1
  ) next_session ON true
  WHERE current_user = 'flyway'
    AND session_user IN ('decision_signal_admin','decision_signal_scheduler')
    AND prior_session.exchange_mic = 'XKRX' AND prior_session.is_open
    AND prior_session.has_conflict = false
    AND (next_session.session_date + time '08:10') AT TIME ZONE 'Asia/Seoul'
      <= statement_timestamp()
  ORDER BY prior_session.session_date DESC
  LIMIT 1
$current_s5_signal_batch_clock$;
ALTER FUNCTION public.current_s5_signal_batch_clock() OWNER TO flyway;
GRANT SELECT ON TABLE public.trading_sessions TO flyway;

CREATE FUNCTION public.activate_signal_model_and_batch(
  p_model_release_id text,
  p_signal_batch_id text,
  p_expected_model_release_id text,
  p_expected_signal_batch_id text,
  p_reason text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_signal_model_and_batch$
DECLARE
  current_model text;
  current_batch text;
  next_generation bigint;
  transition_digest text;
  target_status text;
  target_batch_manifest text;
  publication_digest text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_signal_admin'
     OR p_reason NOT IN ('MANUAL_ACTIVATION','MANUAL_ROLLBACK') THEN
    RAISE EXCEPTION 'S5 activation actor or reason is invalid' USING ERRCODE = '42501';
  END IF;
  SELECT model_release_id, generation INTO current_model, next_generation
  FROM public.active_signal_model_release WHERE singleton FOR UPDATE;
  SELECT signal_batch_id INTO current_batch FROM public.active_signal_batch WHERE singleton FOR UPDATE;
  IF current_model IS DISTINCT FROM NULLIF(p_expected_model_release_id, '')
     OR current_batch IS DISTINCT FROM NULLIF(p_expected_signal_batch_id, '') THEN
    RAISE EXCEPTION 'S5 active pointer CAS conflict' USING ERRCODE = '40001';
  END IF;
  SELECT lifecycle_status INTO target_status
  FROM public.signal_model_releases
  WHERE model_release_id = p_model_release_id FOR UPDATE;
  IF (p_reason = 'MANUAL_ACTIVATION' AND target_status IS DISTINCT FROM 'STAGED')
     OR (p_reason = 'MANUAL_ROLLBACK' AND target_status IS DISTINCT FROM 'ACCEPTED') THEN
    RAISE EXCEPTION 'S5 activation lifecycle transition is invalid' USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.signal_batches batch
    JOIN public.signal_model_releases release USING (model_release_id)
    JOIN public.current_s5_signal_batch_clock() current_clock
      ON current_clock.session_date = batch.session_date AND current_clock.as_of = batch.as_of
    WHERE batch.signal_batch_id = p_signal_batch_id
      AND batch.model_release_id = p_model_release_id AND batch.status = 'FINALIZED'
      AND batch.row_count = 31 AND release.qualification_status = 'QUALIFIED'
      AND release.lifecycle_status IN ('STAGED','ACCEPTED')
      AND NOT EXISTS (
        SELECT 1 FROM public.signal_batch_publications publication
        WHERE publication.signal_batch_id = batch.signal_batch_id
      )
      AND (SELECT count(*) FROM public.signal_batch_members member
           WHERE member.signal_batch_id = batch.signal_batch_id AND member.status = 'AVAILABLE') = 31
  ) THEN
    RAISE EXCEPTION 'S5 activation release or batch is invalid' USING ERRCODE = '22023';
  END IF;
  SELECT batch_manifest_sha256 INTO target_batch_manifest
  FROM public.signal_batches WHERE signal_batch_id = p_signal_batch_id;
  next_generation := COALESCE(next_generation, 0) + 1;
  UPDATE public.signal_model_releases SET lifecycle_status = 'ACCEPTED'
  WHERE model_release_id = p_model_release_id AND lifecycle_status = 'STAGED';
  transition_digest := encode(digest(convert_to(concat_ws(E'\n',
    's5-model-transition-v1', p_model_release_id, target_status, 'ACCEPTED',
    p_signal_batch_id, p_reason),
    'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.signal_model_release_transitions(
    transition_id, model_release_id, from_status, to_status, reason, evidence_sha256, actor_role
  ) VALUES (
    'smrt-' || substr(transition_digest, 1, 24), p_model_release_id, target_status, 'ACCEPTED',
    p_reason, target_batch_manifest,
    session_user
  ) ON CONFLICT (model_release_id, to_status, reason, evidence_sha256) DO NOTHING;
  INSERT INTO public.active_signal_model_release(singleton, model_release_id, generation)
  VALUES (true, p_model_release_id, next_generation)
  ON CONFLICT (singleton) DO UPDATE SET model_release_id = EXCLUDED.model_release_id,
    generation = EXCLUDED.generation, activated_at = clock_timestamp();
  INSERT INTO public.active_signal_batch(singleton, signal_batch_id, generation)
  VALUES (true, p_signal_batch_id, next_generation)
  ON CONFLICT (singleton) DO UPDATE SET signal_batch_id = EXCLUDED.signal_batch_id,
    generation = EXCLUDED.generation, published_at = clock_timestamp();
  publication_digest := encode(digest(convert_to(concat_ws(E'\n',
    's5-batch-publication-v1', p_signal_batch_id, next_generation::text, p_reason),
    'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.signal_batch_publications(
    publication_id, signal_batch_id, model_release_id, generation, reason, actor_role
  ) VALUES (
    'sbp-' || substr(publication_digest, 1, 24), p_signal_batch_id, p_model_release_id,
    next_generation, p_reason, session_user
  );
  RETURN next_generation;
END
$activate_signal_model_and_batch$;
ALTER FUNCTION public.activate_signal_model_and_batch(text,text,text,text,text) OWNER TO flyway;

CREATE FUNCTION public.publish_active_signal_batch(
  p_signal_batch_id text,
  p_expected_signal_batch_id text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $publish_active_signal_batch$
DECLARE
  active_model text;
  current_batch text;
  next_generation bigint;
  publication_digest text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_signal_scheduler' THEN
    RAISE EXCEPTION 'S5 scheduler actor is invalid' USING ERRCODE = '42501';
  END IF;
  SELECT model_release_id, generation INTO active_model, next_generation
  FROM public.active_signal_model_release WHERE singleton FOR SHARE;
  SELECT signal_batch_id INTO current_batch FROM public.active_signal_batch WHERE singleton FOR UPDATE;
  IF active_model IS NULL OR current_batch IS DISTINCT FROM NULLIF(p_expected_signal_batch_id, '') THEN
    RAISE EXCEPTION 'S5 scheduler pointer CAS conflict' USING ERRCODE = '40001';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.signal_batches batch
    JOIN public.signal_model_releases release USING (model_release_id)
    JOIN public.current_s5_signal_batch_clock() current_clock
      ON current_clock.session_date = batch.session_date AND current_clock.as_of = batch.as_of
    WHERE batch.signal_batch_id = p_signal_batch_id AND batch.model_release_id = active_model
      AND batch.status = 'FINALIZED' AND batch.row_count = 31
      AND release.lifecycle_status = 'ACCEPTED'
      AND NOT EXISTS (
        SELECT 1 FROM public.signal_batch_publications publication
        WHERE publication.signal_batch_id = batch.signal_batch_id
      )
      AND (SELECT count(*) FROM public.signal_batch_members member
           WHERE member.signal_batch_id = batch.signal_batch_id AND member.status = 'AVAILABLE') = 31
  ) THEN
    RAISE EXCEPTION 'S5 scheduler batch is invalid' USING ERRCODE = '22023';
  END IF;
  next_generation := next_generation + 1;
  UPDATE public.active_signal_batch SET signal_batch_id = p_signal_batch_id,
    generation = next_generation, published_at = clock_timestamp() WHERE singleton;
  publication_digest := encode(digest(convert_to(concat_ws(E'\n',
    's5-batch-publication-v1', p_signal_batch_id, next_generation::text, 'DAILY_PUBLISH'),
    'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.signal_batch_publications(
    publication_id, signal_batch_id, model_release_id, generation, reason, actor_role
  ) VALUES (
    'sbp-' || substr(publication_digest, 1, 24), p_signal_batch_id, active_model,
    next_generation, 'DAILY_PUBLISH', session_user
  );
  RETURN next_generation;
END
$publish_active_signal_batch$;
ALTER FUNCTION public.publish_active_signal_batch(text,text) OWNER TO flyway;

CREATE FUNCTION public.suspend_signal_model_for_drift(
  p_model_release_id text,
  p_evidence_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $suspend_signal_model_for_drift$
DECLARE
  transition_digest text;
BEGIN
  IF current_user <> 'flyway' OR session_user NOT IN ('decision_signal_scheduler','decision_signal_admin')
     OR p_evidence_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'S5 drift suspension actor or evidence is invalid' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.active_signal_model_release
    WHERE singleton AND model_release_id = p_model_release_id
  ) THEN
    RAISE EXCEPTION 'S5 drift suspension target is not active' USING ERRCODE = '22023';
  END IF;
  UPDATE public.signal_model_releases SET lifecycle_status = 'SUSPENDED'
  WHERE model_release_id = p_model_release_id AND lifecycle_status = 'ACCEPTED';
  IF NOT FOUND THEN RETURN; END IF;
  transition_digest := encode(digest(convert_to(concat_ws(E'\n',
    's5-model-transition-v1', p_model_release_id, 'SUSPENDED', p_evidence_sha256),
    'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.signal_model_release_transitions(
    transition_id, model_release_id, from_status, to_status, reason, evidence_sha256, actor_role
  ) VALUES (
    'smrt-' || substr(transition_digest, 1, 24), p_model_release_id, 'ACCEPTED', 'SUSPENDED',
    'ARTIFACT_DRIFT', p_evidence_sha256, session_user
  );
END
$suspend_signal_model_for_drift$;
ALTER FUNCTION public.suspend_signal_model_for_drift(text,text) OWNER TO flyway;

-- V72 direct pointer는 future producer에 유지하되 LightGBM만 release-level activation을 강제한다.
CREATE OR REPLACE FUNCTION public.activate_signal_v2_production_pointer(p_logical_identity_sha256 text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_signal_v2_production_pointer$
DECLARE
  candidate public.ingested_signals%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'Signal v2 pointer actor is invalid' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO candidate FROM public.ingested_signals
  WHERE logical_identity_sha256 = p_logical_identity_sha256 FOR SHARE;
  IF NOT FOUND OR candidate.producer = 'LIGHTGBM' OR candidate.fixture
     OR candidate.provenance_class <> 'PRODUCTION' OR NOT candidate.artifact_verified
     OR candidate.status <> 'AVAILABLE' THEN
    RAISE EXCEPTION 'Signal v2 production pointer candidate is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.signal_v2_production_pointers(producer, timeframe, logical_identity_sha256)
  VALUES (candidate.producer, candidate.timeframe, candidate.logical_identity_sha256)
  ON CONFLICT (producer, timeframe) DO UPDATE
  SET logical_identity_sha256 = EXCLUDED.logical_identity_sha256, activated_at = clock_timestamp();
END
$activate_signal_v2_production_pointer$;
ALTER FUNCTION public.activate_signal_v2_production_pointer(text) OWNER TO flyway;

CREATE OR REPLACE FUNCTION public.read_production_signal_v2(p_symbol text)
RETURNS TABLE(
  producer text, source_workspace text, symbol text, session_date date, as_of timestamptz,
  timeframe text, status text, reason text, signal text, confidence numeric,
  predicted_return numeric, model_version text, model_report_id text
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_production_signal_v2$
  SELECT stored.producer, stored.source_workspace, stored.symbol, stored.session_date,
    stored.as_of, stored.timeframe, stored.status, stored.reason, stored.signal,
    stored.confidence, stored.predicted_return, stored.model_version, stored.model_report_id
  FROM public.signal_v2_production_pointers pointer
  JOIN public.ingested_signals stored ON stored.logical_identity_sha256 = pointer.logical_identity_sha256
  WHERE current_user = 'flyway' AND session_user = 'decision_app'
    AND p_symbol ~ '^[0-9A-Z._:-]{1,20}$' AND stored.symbol = p_symbol
    AND stored.producer <> 'LIGHTGBM' AND stored.fixture = false
    AND stored.provenance_class = 'PRODUCTION' AND stored.artifact_verified
  UNION ALL
  SELECT 'LIGHTGBM'::text, 'decision-platform'::text, member.symbol, batch.session_date,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN member.as_of ELSE NULL END,
    '1d'::text,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN 'AVAILABLE' ELSE 'ABSTAIN' END,
    CASE WHEN release.lifecycle_status = 'SUSPENDED' THEN 'ARTIFACT_DRIFT' ELSE NULL END,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN member.signal ELSE NULL END,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN member.confidence ELSE NULL END,
    NULL::numeric,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN member.model_version ELSE NULL END,
    CASE WHEN release.lifecycle_status = 'ACCEPTED' THEN member.model_report_id ELSE NULL END
  FROM public.active_signal_model_release active_model
  JOIN public.signal_model_releases release USING (model_release_id)
  JOIN public.active_signal_batch active_batch ON active_batch.singleton
  JOIN public.signal_batches batch ON batch.signal_batch_id = active_batch.signal_batch_id
    AND batch.model_release_id = active_model.model_release_id
  JOIN public.signal_batch_members member ON member.signal_batch_id = batch.signal_batch_id
  WHERE current_user = 'flyway' AND session_user = 'decision_app'
    AND active_model.singleton AND member.symbol = p_symbol
  ORDER BY producer
$read_production_signal_v2$;
ALTER FUNCTION public.read_production_signal_v2(text) OWNER TO flyway;

REVOKE ALL PRIVILEGES ON FUNCTION
  public.current_s5_signal_batch_clock(),
  public.stage_signal_model_release(text,text,text,text,text,text,text,text,text,text),
  public.stage_signal_batch(text,text,text,text,text,date,timestamptz,text),
  public.activate_signal_model_and_batch(text,text,text,text,text),
  public.publish_active_signal_batch(text,text),
  public.suspend_signal_model_for_drift(text,text)
FROM PUBLIC, decision_app, decision_signal_writer, decision_signal_scheduler, decision_signal_admin;

GRANT EXECUTE ON FUNCTION
  public.stage_signal_model_release(text,text,text,text,text,text,text,text,text,text),
  public.stage_signal_batch(text,text,text,text,text,date,timestamptz,text)
TO decision_signal_writer;
GRANT EXECUTE ON FUNCTION public.publish_active_signal_batch(text,text)
TO decision_signal_scheduler;
GRANT EXECUTE ON FUNCTION public.suspend_signal_model_for_drift(text,text)
TO decision_signal_scheduler, decision_signal_admin;
GRANT EXECUTE ON FUNCTION public.activate_signal_model_and_batch(text,text,text,text,text)
TO decision_signal_admin;
