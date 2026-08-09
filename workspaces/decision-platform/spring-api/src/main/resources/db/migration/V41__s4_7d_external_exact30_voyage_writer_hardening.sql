-- V41은 이미 적용됐을 수 있는 V37 external exact-30 Voyage writer를 additive하게 harden한다.
-- raw artifact/provider transport/activation authority는 추가하지 않으며, 기존 V37 checksum을 바꾸지 않는다.

ALTER TABLE public.rag_v2_immutable_external_exact30_source_allowlist
  ADD COLUMN IF NOT EXISTS canonical_text_sha256 text;

-- V37의 allowlist는 RLS를 강제하므로 migration 동안에만 flyway update policy를 열고 즉시 닫는다.
DO $rag_v2_external_exact30_v41_allowlist_update_policy$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'rag_v2_immutable_external_exact30_source_allowlist'
      AND policyname = 'rag_v2_immutable_external_exact30_allowlist_v41_flyway_update'
  ) THEN
    EXECUTE '
      CREATE POLICY rag_v2_immutable_external_exact30_allowlist_v41_flyway_update
        ON public.rag_v2_immutable_external_exact30_source_allowlist
        FOR UPDATE TO flyway USING (true) WITH CHECK (true)
    ';
  END IF;
END;
$rag_v2_external_exact30_v41_allowlist_update_policy$;

WITH expected_source_text_hashes(source_id, canonical_text_sha256) AS (
  VALUES
    ('src_project_backtest_overfitting_001', 'f44c738239aea178dc1ca1c2cd666a1e4f1f86ff044320f09168f8bf5678630e'),
    ('src_project_bsm_continuous_hedge_assumptions_001', '212e3d14edf3379aa5198036f461bf7c7509efcd6f698ce6b7ae1f5d559f8e57'),
    ('src_project_bsm_risk_neutral_001', 'fd2fa03b9e9b96a7b447de1ae2c63289f191eceacac135adc1c90396576a2210'),
    ('src_project_bsm_time_to_expiry_001', '4ae13e78291c30360787ce92c33e192d25c31e0bead74984f26e4fd51c57a716'),
    ('src_project_delta_hedge_residual_cost_001', '8ab4de19c0d927f025cbda5a006b21e11c2e612cc98f840dfbee337490226fc5'),
    ('src_project_ecos_pit_availability_001', '534c836a28102c9fa754f5823d6772cb229a791c04756b595f7460dbf8be37ab'),
    ('src_project_expected_payoff_measure_discount_001', '9912c77e4a45392c4005c18f034c3efc7d2978afe1c29383729095e213563ead'),
    ('src_project_finance_diffusion_not_ddpm_001', 'e8eaf3d9f58a478f7be3573ff1701d8744943702974ca412134abb93557ef1dd'),
    ('src_project_gold_futures_etf_132030_001', '8d4f1f3534c7c1dfc0c4213012b51f76bf4d7851fd088353449836e1424991ad'),
    ('src_project_hmm_latent_state_boundary_001', '23cb511559dd2e8a30907d71d5cf9ee7b5cd1d69a26e2a00b2db985fba1ada42'),
    ('src_project_kis_adjusted_price_001', 'af9764e74dea6eec236a272ff7d2175bbeddfe69952f866335f96cd3b32a6daf'),
    ('src_project_kis_current_price_snapshot_001', '0d94dd496dcb136097b685ec57c4dbb6c2ccfc418820547ca7e0c89be9a74c57'),
    ('src_project_kis_discovery_write_boundary_001', 'bd987a9de5835938240b97200f4ae14a219fd0085aebfe71264a8d806b79933b'),
    ('src_project_kis_market_calendar_001', '024cfddcf3b5f28f98f5971c5e29a1883438238e6c0be4b4ec8250387343718a'),
    ('src_project_kis_rate_limit_token_001', '5b6cf53650253e7338e1a8121e8e067d6a95f8d436e0663f1fdbf593e00be582'),
    ('src_project_krx_etf_etn_structure_001', '18101f21818d859c2f6d9961abc8be34f6ed0c917e48dd07147e981e1149dc2b'),
    ('src_project_krx_etn_risk_indicator_001', '1ed84505d945be0f5836ae919c0202cfda123b0b4372a213e6d004495d619139'),
    ('src_project_krx_last_trading_settlement_001', 'b88e1d2fc126f8479bd4cb90c4cb9004880b14ab27b3156740a7048784053486'),
    ('src_project_krx_service_coverage_001', '8f9912bb9bd978cc05661f73f1d07975f79f57ba27626f8a1ad601f7433bc6d3'),
    ('src_project_mean_reversion_stationarity_001', '0baa1eca0f9c403a4afafc55d7759a8a3ff4b5324f43d7b15d3aac8358d7cb74'),
    ('src_project_monte_carlo_not_stress_probability_001', '9cb59a15d3af111b0c5443f7080c9ba606a133c4626c6a54ef7b6167b15351d9'),
    ('src_project_naver_news_discovery_boundary_001', '98216149d1a55ab84580594e901b9742262f9a7855fe1577a15bedc705972e38'),
    ('src_project_notional_not_exposure_001', '43416e11720e0932c1f731c11692200488aaf11118d9902e761544552b59ed5b'),
    ('src_project_opendart_corporation_code_001', '39f5259232a0bbecceea3bfe207efc68640845b4382119273e7f1f8fcaaf74e6'),
    ('src_project_opendart_financial_statement_scope_001', '306f7380c7b5274d25652a21c34aed1e0379e3464327f4da81d84fa5503377ff'),
    ('src_project_opendart_status_quota_001', '5d57c8d584a9960a2454069f622fbd9193ea361980b0c40874c9bdc1433bf3b6'),
    ('src_project_sharpe_drawdown_partial_metrics_001', 'a233ee7ed8231318138de0dfd7bd76f279211a2e364bac2e154054f8aad44161'),
    ('src_project_threshold_cvar_not_exact_es_001', 'bb58748ea87ae212e988e0b3876c9683dd3d07dd2cee8302969b5e1ead8419ae'),
    ('src_project_valuation_delta_not_guard_delta_001', 'ed627373f17a5a38265ed1a8f4bf95698c249b358f1899ef99b76aedd6321106'),
    ('src_project_var_es_coherence_001', '071cc3eaf661355a821e1eb85289bd88674961fbf4161b72db821fea26c33834')
)
UPDATE public.rag_v2_immutable_external_exact30_source_allowlist AS allowed
SET canonical_text_sha256 = expected.canonical_text_sha256
FROM expected_source_text_hashes AS expected
WHERE allowed.source_id = expected.source_id;

ALTER TABLE public.rag_v2_immutable_external_exact30_source_allowlist
  ALTER COLUMN canonical_text_sha256 SET NOT NULL;

DO $rag_v2_external_exact30_v41_allowlist_check$
BEGIN
  IF (SELECT count(*) FROM public.rag_v2_immutable_external_exact30_source_allowlist) <> 30
     OR EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_external_exact30_source_allowlist
       WHERE canonical_text_sha256 !~ '^[0-9a-f]{64}$'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage V41 allowlist drifted'
      USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'public.rag_v2_immutable_external_exact30_source_allowlist'::regclass
      AND conname = 'rag_v2_immutable_external_exact30_allowlist_v41_canonical_text_hash_check'
  ) THEN
    EXECUTE '
      ALTER TABLE public.rag_v2_immutable_external_exact30_source_allowlist
      ADD CONSTRAINT rag_v2_immutable_external_exact30_allowlist_v41_canonical_text_hash_check
      CHECK (canonical_text_sha256 ~ ''^[0-9a-f]{64}$'')
    ';
  END IF;
END;
$rag_v2_external_exact30_v41_allowlist_check$;

DROP POLICY IF EXISTS rag_v2_immutable_external_exact30_allowlist_v41_flyway_update
  ON public.rag_v2_immutable_external_exact30_source_allowlist;

CREATE OR REPLACE FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(
  p_source_id text,
  p_canonical_https_url text,
  p_raw_content_sha256 text,
  p_source_card_sha256 text,
  p_canonical_text_sha256 text
)
RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_external_exact30_voyage_source_is_approved$
  SELECT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_external_exact30_source_allowlist AS allowed
    WHERE allowed.source_id = p_source_id
      AND allowed.canonical_https_url = p_canonical_https_url
      AND allowed.raw_content_sha256 = p_raw_content_sha256
      AND allowed.source_card_sha256 = p_source_card_sha256
      AND allowed.canonical_text_sha256 = p_canonical_text_sha256
  )
$rag_v2_immutable_external_exact30_voyage_source_is_approved$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(text, text, text, text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rag_v2_immutable_external_exact30_voyage_source_revision_id(
  p_source_id text,
  p_raw_content_sha256 text,
  p_source_card_sha256 text
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_external_exact30_voyage_source_revision_id$
BEGIN
  IF p_source_id !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_raw_content_sha256 !~ '^[0-9a-f]{64}$'
     OR p_source_card_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source identity arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN 'srv_exact30_external_' || substr(encode(public.digest(
    convert_to('s4_7c_external_v1', 'UTF8') || decode('00', 'hex') ||
    convert_to(p_source_id, 'UTF8') || decode('00', 'hex') ||
    convert_to(p_source_card_sha256, 'UTF8') || decode('00', 'hex') ||
    convert_to(p_raw_content_sha256, 'UTF8'),
    'sha256'
  ), 'hex'), 1, 32);
END;
$rag_v2_immutable_external_exact30_voyage_source_revision_id$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_source_revision_id(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_source_revision_id(text, text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rag_v2_immutable_external_exact30_voyage_document_id(
  p_source_id text,
  p_source_revision_id text
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_external_exact30_voyage_document_id$
BEGIN
  IF p_source_id !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_source_revision_id !~ '^srv_exact30_external_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage document identity arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN 'doc_exact30_external_' || substr(encode(public.digest(
    convert_to('s4_7c_external_v1', 'UTF8') || decode('00', 'hex') ||
    convert_to(p_source_id, 'UTF8') || decode('00', 'hex') ||
    convert_to(p_source_revision_id, 'UTF8'),
    'sha256'
  ), 'hex'), 1, 32);
END;
$rag_v2_immutable_external_exact30_voyage_document_id$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_document_id(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_document_id(text, text) FROM PUBLIC;

-- V37의 writer payload validation이 우회되더라도 source row 자체가 approved canonical body와
-- deterministic source/document identity를 벗어나면 insert 직전에 실패한다.
CREATE OR REPLACE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_source_identity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_v2_immutable_external_exact30_voyage_source_identity$
BEGIN
  IF NEW.owner_user_id IS NULL
     AND NEW.source_scope = 'EXACT30'
     AND NEW.external_processing_eligible
     AND NOT NEW.machine_fetch_allowed
     AND NEW.local_processing_allowed
     AND NEW.external_embedding_allowed
     AND NEW.external_generation_allowed THEN
    IF public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
         NEW.source_id,
         NEW.canonical_https_url,
         NEW.raw_content_sha256,
         NEW.exact30_source_card_sha256,
         NEW.canonical_text_sha256
       ) IS NOT TRUE
       OR NEW.source_revision_id <> public.rag_v2_immutable_external_exact30_voyage_source_revision_id(
         NEW.source_id,
         NEW.raw_content_sha256,
         NEW.exact30_source_card_sha256
       )
       OR NEW.document_id <> public.rag_v2_immutable_external_exact30_voyage_document_id(
         NEW.source_id,
         NEW.source_revision_id
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source identity is invalid'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard_rag_v2_immutable_external_exact30_voyage_source_identity$;
ALTER FUNCTION guard_rag_v2_immutable_external_exact30_voyage_source_identity() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_external_exact30_voyage_source_identity() FROM PUBLIC;

DROP TRIGGER IF EXISTS rag_v2_immutable_external_exact30_voyage_source_identity_guard
  ON public.rag_v2_immutable_source_revisions;
CREATE TRIGGER rag_v2_immutable_external_exact30_voyage_source_identity_guard
  BEFORE INSERT OR UPDATE ON public.rag_v2_immutable_source_revisions
  FOR EACH ROW
  EXECUTE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_source_identity();

-- Membership trigger는 V37 stage function의 direct invocation에도 source별 하나의 revision/document와
-- UTF-8 source ID global order를 보장한다. local-BGE/OA/owner generations에는 적용하지 않는다.
CREATE OR REPLACE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_membership()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_v2_immutable_external_exact30_voyage_membership$
DECLARE
  generation_profile_id text;
  staged_source public.rag_v2_immutable_source_revisions%ROWTYPE;
BEGIN
  IF NEW.owner_user_id IS NOT NULL OR NEW.component_scope <> 'EXACT30' THEN
    RETURN NEW;
  END IF;
  SELECT generation.embedding_profile_id
  INTO generation_profile_id
  FROM public.rag_v2_immutable_component_generations AS generation
  WHERE generation.component_generation_id = NEW.component_generation_id
    AND generation.component_scope = 'EXACT30'
    AND generation.owner_user_id IS NULL;
  IF generation_profile_id IS DISTINCT FROM 'voyage_context_4_1024_v1' THEN
    RETURN NEW;
  END IF;
  SELECT * INTO staged_source
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = NEW.source_revision_id;
  IF NOT FOUND
     OR staged_source.owner_user_id IS NOT NULL
     OR staged_source.source_scope <> 'EXACT30'
     OR NOT staged_source.external_processing_eligible
     OR staged_source.machine_fetch_allowed
     OR NOT staged_source.local_processing_allowed
     OR NOT staged_source.external_embedding_allowed
     OR NOT staged_source.external_generation_allowed
     OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
       staged_source.source_id,
       staged_source.canonical_https_url,
       staged_source.raw_content_sha256,
       staged_source.exact30_source_card_sha256,
       staged_source.canonical_text_sha256
     ) IS NOT TRUE
     OR staged_source.source_revision_id <> public.rag_v2_immutable_external_exact30_voyage_source_revision_id(
       staged_source.source_id,
       staged_source.raw_content_sha256,
       staged_source.exact30_source_card_sha256
     )
     OR staged_source.document_id <> public.rag_v2_immutable_external_exact30_voyage_document_id(
       staged_source.source_id,
       staged_source.source_revision_id
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage membership source is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       JOIN public.rag_v2_immutable_source_revisions AS existing_source
         ON existing_source.source_revision_id = membership.source_revision_id
       WHERE membership.component_generation_id = NEW.component_generation_id
         AND membership.component_scope = 'EXACT30'
         AND membership.owner_user_id IS NULL
         AND existing_source.source_id = staged_source.source_id
         AND existing_source.source_revision_id <> NEW.source_revision_id
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage duplicate source is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       WHERE membership.component_generation_id = NEW.component_generation_id
         AND membership.component_scope = 'EXACT30'
         AND membership.owner_user_id IS NULL
         AND membership.source_revision_id = NEW.source_revision_id
     )
     AND EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_external_exact30_source_allowlist AS required_source
       WHERE pg_catalog.convert_to(required_source.source_id, 'UTF8')
             < pg_catalog.convert_to(staged_source.source_id, 'UTF8')
         AND NOT EXISTS (
           SELECT 1
           FROM public.rag_v2_immutable_generation_memberships AS membership
           JOIN public.rag_v2_immutable_source_revisions AS existing_source
             ON existing_source.source_revision_id = membership.source_revision_id
           WHERE membership.component_generation_id = NEW.component_generation_id
             AND membership.component_scope = 'EXACT30'
             AND membership.owner_user_id IS NULL
             AND existing_source.source_id = required_source.source_id
         )
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage canonical source order is invalid'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$guard_rag_v2_immutable_external_exact30_voyage_membership$;
ALTER FUNCTION guard_rag_v2_immutable_external_exact30_voyage_membership() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_external_exact30_voyage_membership() FROM PUBLIC;

DROP TRIGGER IF EXISTS rag_v2_immutable_external_exact30_voyage_membership_guard
  ON public.rag_v2_immutable_generation_memberships;
CREATE TRIGGER rag_v2_immutable_external_exact30_voyage_membership_guard
  BEFORE INSERT ON public.rag_v2_immutable_generation_memberships
  FOR EACH ROW
  EXECUTE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_membership();

CREATE OR REPLACE FUNCTION rag_v2_immutable_external_exact30_voyage_source_member_digest(
  p_component_generation_id text,
  p_source_revision_id text
)
RETURNS text
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_external_exact30_voyage_source_member_digest$
DECLARE
  source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  encoded_chunks text;
  joined_canonical_text text;
BEGIN
  SELECT * INTO source_record
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = p_source_revision_id
    AND source.source_scope = 'EXACT30'
    AND source.owner_user_id IS NULL;
  IF NOT FOUND
     OR NOT source_record.external_processing_eligible
     OR source_record.machine_fetch_allowed
     OR NOT source_record.local_processing_allowed
     OR NOT source_record.external_embedding_allowed
     OR NOT source_record.external_generation_allowed
     OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
       source_record.source_id,
       source_record.canonical_https_url,
       source_record.raw_content_sha256,
       source_record.exact30_source_card_sha256,
       source_record.canonical_text_sha256
     ) IS NOT TRUE
     OR source_record.source_revision_id <> public.rag_v2_immutable_external_exact30_voyage_source_revision_id(
       source_record.source_id,
       source_record.raw_content_sha256,
       source_record.exact30_source_card_sha256
     )
     OR source_record.document_id <> public.rag_v2_immutable_external_exact30_voyage_document_id(
       source_record.source_id,
       source_record.source_revision_id
     ) THEN
    RETURN NULL;
  END IF;
  SELECT
    string_agg(
      '{"canonicalTextSha256":' || pg_catalog.to_json(chunk.canonical_text_sha256)::text ||
      ',"chunkId":' || pg_catalog.to_json(chunk.chunk_id)::text ||
      ',"chunkOrdinal":' || chunk.chunk_ordinal::text ||
      ',"contextSetHash":' || pg_catalog.to_json(embedding.context_set_hash)::text ||
      ',"embeddingInputHash":' || pg_catalog.to_json(embedding.embedding_input_hash)::text || '}',
      ',' ORDER BY chunk.chunk_ordinal
    ),
    string_agg(chunk.canonical_text, E'\n\n' ORDER BY chunk.chunk_ordinal)
  INTO encoded_chunks, joined_canonical_text
  FROM public.rag_v2_immutable_generation_memberships AS membership
  JOIN public.rag_v2_immutable_chunks AS chunk
    ON chunk.chunk_id = membership.chunk_id
   AND chunk.source_revision_id = membership.source_revision_id
   AND chunk.source_scope = membership.component_scope
   AND chunk.owner_user_id IS NULL
  JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
   AND embedding.component_scope = membership.component_scope
   AND embedding.owner_user_id IS NULL
   AND embedding.embedding_profile_id = 'voyage_context_4_1024_v1'
  WHERE membership.component_generation_id = p_component_generation_id
    AND membership.component_scope = 'EXACT30'
    AND membership.source_revision_id = p_source_revision_id;
  IF encoded_chunks IS NULL
     OR joined_canonical_text IS NULL
     OR encode(public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex')
        <> source_record.canonical_text_sha256 THEN
    RETURN NULL;
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"canonicalTextSha256":' || pg_catalog.to_json(encode(
      public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex'
    ))::text || ',"chunks":[' || encoded_chunks || '],"documentId":' ||
    pg_catalog.to_json(source_record.document_id)::text || ',"rawContentSha256":' ||
    pg_catalog.to_json(source_record.raw_content_sha256)::text || ',"sourceCardSha256":' ||
    pg_catalog.to_json(source_record.exact30_source_card_sha256)::text || ',"sourceId":' ||
    pg_catalog.to_json(source_record.source_id)::text || ',"sourceRevisionId":' ||
    pg_catalog.to_json(source_record.source_revision_id)::text || ',"sourceRevisionSha256":' ||
    pg_catalog.to_json(source_record.source_revision_sha256)::text || '}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_external_exact30_voyage_source_member_digest$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_source_member_digest(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_source_member_digest(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(
  p_component_generation_id text
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid$
DECLARE
  generation_record public.rag_v2_immutable_component_generations%ROWTYPE;
  manifest_record public.rag_v2_immutable_external_exact30_voyage_component_manifests%ROWTYPE;
  observed_member_digests text[];
  observed_source_ids text[];
  expected_source_ids text[];
  observed_source_total integer;
  observed_chunk_total integer;
  expected_manifest_hash text;
  expected_generation_hash text;
BEGIN
  SELECT * INTO generation_record
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = p_component_generation_id;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  SELECT * INTO manifest_record
  FROM public.rag_v2_immutable_external_exact30_voyage_component_manifests
  WHERE component_generation_id = p_component_generation_id;
  IF NOT FOUND
     OR generation_record.owner_user_id IS NOT NULL
     OR generation_record.component_scope <> 'EXACT30'
     OR generation_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR generation_record.expected_source_count <> 30
     OR generation_record.expected_chunk_count < 30
     OR manifest_record.component_scope <> 'EXACT30'
     OR manifest_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR manifest_record.manifest_hash <> generation_record.manifest_hash
     OR manifest_record.generation_hash <> generation_record.generation_hash THEN
    RETURN false;
  END IF;
  SELECT
    array_agg(
      public.rag_v2_immutable_external_exact30_voyage_source_member_digest(
        p_component_generation_id, selected_source.source_revision_id
      ) ORDER BY pg_catalog.convert_to(selected_source.source_id, 'UTF8')
    ),
    array_agg(selected_source.source_id ORDER BY pg_catalog.convert_to(selected_source.source_id, 'UTF8')),
    count(*)::integer,
    coalesce(sum(selected_source.chunk_count), 0)::integer
  INTO observed_member_digests, observed_source_ids, observed_source_total, observed_chunk_total
  FROM (
    SELECT source.source_revision_id, source.source_id, count(membership.chunk_id)::integer AS chunk_count
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE membership.component_generation_id = p_component_generation_id
      AND membership.component_scope = 'EXACT30'
      AND source.source_scope = 'EXACT30'
      AND source.owner_user_id IS NULL
      AND source.external_processing_eligible
      AND NOT source.machine_fetch_allowed
      AND source.local_processing_allowed
      AND source.external_embedding_allowed
      AND source.external_generation_allowed
      AND public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
        source.source_id,
        source.canonical_https_url,
        source.raw_content_sha256,
        source.exact30_source_card_sha256,
        source.canonical_text_sha256
      ) IS TRUE
      AND source.source_revision_id = public.rag_v2_immutable_external_exact30_voyage_source_revision_id(
        source.source_id,
        source.raw_content_sha256,
        source.exact30_source_card_sha256
      )
      AND source.document_id = public.rag_v2_immutable_external_exact30_voyage_document_id(
        source.source_id,
        source.source_revision_id
      )
    GROUP BY source.source_revision_id, source.source_id
  ) AS selected_source;
  SELECT array_agg(allowed.source_id ORDER BY pg_catalog.convert_to(allowed.source_id, 'UTF8'))
  INTO expected_source_ids
  FROM public.rag_v2_immutable_external_exact30_source_allowlist AS allowed;
  IF observed_member_digests IS NULL
     OR observed_source_ids IS NULL
     OR expected_source_ids IS NULL
     OR array_position(observed_member_digests, NULL) IS NOT NULL
     OR observed_source_total <> 30
     OR observed_chunk_total <> generation_record.expected_chunk_count
     OR observed_source_ids IS DISTINCT FROM expected_source_ids
     OR observed_member_digests IS DISTINCT FROM manifest_record.member_digests THEN
    RETURN false;
  END IF;
  expected_manifest_hash := public.rag_v2_immutable_external_exact30_voyage_manifest_hash(
    manifest_record.member_digests
  );
  expected_generation_hash := public.rag_v2_immutable_external_exact30_voyage_generation_hash(
    generation_record.expected_source_count,
    generation_record.expected_chunk_count,
    expected_manifest_hash
  );
  RETURN expected_manifest_hash = generation_record.manifest_hash
    AND expected_generation_hash = generation_record.generation_hash
    AND p_component_generation_id = 'rgr_' || substr(expected_generation_hash, 1, 32);
END;
$rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(text) FROM PUBLIC;
