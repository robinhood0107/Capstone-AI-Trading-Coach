-- V37은 S4.7B/V36 local-BGE graph를 바꾸지 않고 S4.7C external-safe exact-30만 Voyage
-- contextual vector space에 stage한다. raw artifact=0이며 provider transport/activation authority는
-- 이 migration이 만들지 않는다.

CREATE TABLE rag_v2_immutable_external_exact30_source_allowlist (
  source_id text PRIMARY KEY,
  canonical_https_url text NOT NULL,
  raw_content_sha256 text NOT NULL,
  source_card_sha256 text NOT NULL,
  CONSTRAINT rag_v2_immutable_external_exact30_allowlist_source_id_check
    CHECK (source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT rag_v2_immutable_external_exact30_allowlist_url_check
    CHECK (public.rag_v2_immutable_public_https_url_is_valid(canonical_https_url)),
  CONSTRAINT rag_v2_immutable_external_exact30_allowlist_hash_check
    CHECK (
      raw_content_sha256 ~ '^[0-9a-f]{64}$'
      AND source_card_sha256 ~ '^[0-9a-f]{64}$'
    )
);
ALTER TABLE rag_v2_immutable_external_exact30_source_allowlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_external_exact30_source_allowlist FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_external_exact30_allowlist_flyway_read
  ON rag_v2_immutable_external_exact30_source_allowlist
  FOR SELECT TO flyway USING (true);
CREATE POLICY rag_v2_immutable_external_exact30_allowlist_flyway_write
  ON rag_v2_immutable_external_exact30_source_allowlist
  FOR INSERT TO flyway WITH CHECK (true);

-- The external card hash differs from S4.7B solely because the consent-bearing front matter differs.
-- The card body remains project-authored/sanitized and is not copied into this allowlist.
INSERT INTO rag_v2_immutable_external_exact30_source_allowlist (
  source_id, canonical_https_url, raw_content_sha256, source_card_sha256
) VALUES
  ('src_project_backtest_overfitting_001', 'https://doi.org/10.1111/1468-0262.00152', '71fd6c755fee1ad56c9795c98fd8ca86e05eff6b598a3f2dc9bbea2a76b6a288', 'fe1d823dde615713d044af774883376a5281ee141a2e1d0f6d64442892111637'),
  ('src_project_bsm_continuous_hedge_assumptions_001', 'https://doi.org/10.1086/260062', '26be6bb3d2de4a4bb92d50589849d6e5b737bcef8afae3f01bab3ce078d21e63', 'd8cdd94a70f76cc14f86880f2e0bde1c54ae101f89dbcad818ab1da538478817'),
  ('src_project_bsm_risk_neutral_001', 'https://doi.org/10.1086/260062', '4458a89a12c2878c354045633e4274b0e76cfb761cc977911764492eb1d77a21', 'e5bd1c4817a26d2745f0cb411e4cef747ac823ba4ea0264fd9f8064fec539be7'),
  ('src_project_bsm_time_to_expiry_001', 'https://doi.org/10.2307/3003143', '8d85efb1f6b6a9cb03848733f24a2202453118a05c7e91099cc0f686d1257623', '6d817db0f2b2302e9ecb43ae9ec48abe6ab8232e7216903c236b4540abc69a0b'),
  ('src_project_delta_hedge_residual_cost_001', 'https://doi.org/10.1111/j.1540-6261.1985.tb02383.x', 'ab18d838d14d40851be63becb18e69e1cf60a36b5e0e1632264a514fb2bd71a4', '428d1e46fdc6cc34537fe719bd4f2ad1609c7e5ccf5b0ec85722f98333f9170a'),
  ('src_project_ecos_pit_availability_001', 'https://ecos.bok.or.kr/api/', '617685e273926fa0b3ffe2d78c8dc98159552c4c509a2d8abbd718d5eea63c53', '50e12c2901bc08b3b9e8d1ca6f8710467dd6be64be71369074ea0cd0cbc37cf6'),
  ('src_project_expected_payoff_measure_discount_001', 'https://doi.org/10.2307/3003143', 'a355c8886cfc6efbc814af4b9d33ed2c89672f9234680e608882ab78f7e3454d', 'cde096f637c65175bba8053f8ba12cb85691db90d48c697252923cbbb88ea45b'),
  ('src_project_finance_diffusion_not_ddpm_001', 'https://proceedings.neurips.cc/paper/2020/hash/4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html', 'ed30781af1b381c6c6e949ee738005b9eb1ff37a083a0f3c99daa6d42b14d767', 'fbfb886df3c1dccd42b412fc73b0dd367b4f072f7034d1b101b612f1023f1c3d'),
  ('src_project_gold_futures_etf_132030_001', 'https://www.samsungfund.com/etf/product/view.do?id=2ETF24', 'fd5cd415deabd1ab24f286dc13a9d7b9a8794e445e623d07e79c138dc2840af5', '7714935fef291b7b2a5ec05cc35a7dea02e2ff7396b7894a2edfc9e6ddfe95b5'),
  ('src_project_hmm_latent_state_boundary_001', 'https://doi.org/10.1109/5.18626', '282004435071adb3fa0a7c2e08110bb1e7656ae7d850d746f668d6b3e3b3f705', 'b4f785bc82314078036a86284f277d8b643b4a10130c8c0401feb5b4872a247b'),
  ('src_project_kis_adjusted_price_001', 'https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py', '6fabc47a9437a0bdaa7658f4bbcabb5d3c3be211ef538e47f434b29e985aa09b', '2a0fe14f7ccfdf316d6f346090163963618321ab20466b67259da9e17220216a'),
  ('src_project_kis_current_price_snapshot_001', 'https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/inquire_price/inquire_price.py', '55249a9a6109aa90faf0ebe238bd459d7e25e6514b67ec641c8a2ba5a9924f35', '2b97286d3bf2fb192193d6393e6a4da9bdb88d252762c56f38f8dcedf06cc687'),
  ('src_project_kis_discovery_write_boundary_001', 'https://apiportal.koreainvestment.com/about-open-api', '3137be113762703bbf5632ee8bdc317c182391ed04476a12a5b7b93d481db952', 'b97f68b06290ded7304a96cc9de3b3c339f0baa0e09dc2d323d5037beeac1067'),
  ('src_project_kis_market_calendar_001', 'https://github.com/koreainvestment/open-trading-api/blob/b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/chk_holiday/chk_holiday.py', '23e4ae630d6675c1f7fdb2109c4426bfb1f1b4c226ac35c786c8bd959366c406', '7dfef37c786006419f5572ad088561f3c9c47695282a98b5f13da73a4a071225'),
  ('src_project_kis_rate_limit_token_001', 'https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989', '9fc0b4dba613e891c8d5a4d395351d6293496a0b441c9fdcde431bff824c6a74', '3bbf8771e429a0d40c32ecd8eed72a7c9524b62207be6be4bd4602ea2244921e'),
  ('src_project_krx_etf_etn_structure_001', 'https://open.krx.co.kr/contents/OPN/01/01030100/OPN01030100.jsp', 'e64eb2ab933e28c67c99ec6b09446b4c894225dfb357457ec91335287a5738ff', 'ee3794ca746a5887f5e7912a8f4a33c559e97cfb8803215df5bcdd78d747f272'),
  ('src_project_krx_etn_risk_indicator_001', 'https://open.krx.co.kr/contents/OPN/01/01030302/OPN01030302.jsp', '1c763d8a6a69bafd0f086fcbb3d1731d6af730bc9e104bb3532f04dbb23d9fc4', '07fe21fdf8e2b499c3c31196b25fd3f174371cc53024ace63360911f668a2ce9'),
  ('src_project_krx_last_trading_settlement_001', 'https://global.krx.co.kr/contents/GLB/02/0201/0201040202/GLB0201040202.jsp', '74e9806b45dde247b769e5003d2aedef477736b5583a51fc3a8ffb9c9eca9143', '8fef22e0b8d19baa98eb9c8529ca0b8701a383940142b69c09a4086af44aeaf9'),
  ('src_project_krx_service_coverage_001', 'https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd', 'f76330e9446e14b3febde9f72ffdb0162d1a534b528681b2652ce0fdb98a36ff', 'acbf5e61350ba9e41cbbb44a6af2422c100094de94b9b3b98092b0f516f9b3c5'),
  ('src_project_mean_reversion_stationarity_001', 'https://doi.org/10.1080/01621459.1979.10482531', '08928cda0376bd2428ccaa9e467cb52512c68c35d6dda0bc60ece829165899e5', '165e338e81311c3c9e6421745ad0ae99bf22c5cf6fdb7495508639c36594d71f'),
  ('src_project_monte_carlo_not_stress_probability_001', 'https://doi.org/10.1016/0304-405X(77)90005-8', '0292a9e7a12f208ecb8e8c5ef6dc7888e66f9244ff0dd42bc5a53fa92d375841', '636669ef076d5fc2661cc8910b93be8c5089ed90eff3be13bf8cc74f365ed44b'),
  ('src_project_naver_news_discovery_boundary_001', 'https://developers.naver.com/docs/serviceapi/search/news/news.md', '05cf1f4e5ae7e1daf3d7f1b78155bf504cec98a1c9ecbe49825bdec36ef47758', '2372f3fce64caea6d3f67b1d2beacefe0435d50b3ea1c6db63395f1f95b211f5'),
  ('src_project_notional_not_exposure_001', 'https://data.bis.org/topics/OTC_DER', 'e3065d9e6e4793aa86041a9590d8fb3b32eccaa5d1fd61c47ca474a9c8264720', '6ef73e46ee5c752ea70a278587efb34e387edb2a2574c9bc6a186fff6beb3156'),
  ('src_project_opendart_corporation_code_001', 'https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018', '2e2b953ceb93db04a165478ec5f084e72f69e96be741f3918052fae652dd1186', '39a12056d8c532ec28fec446ee0ce27bd75caea6ef2a61d7d9fc256b646ed554'),
  ('src_project_opendart_financial_statement_scope_001', 'https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016', '10d709ff4c8461f01b85239d11ad5adb3efdfda91b561268c7160ef643dc0ded', '9439b6781ba1c38a98965f1dd7429f21d7375cdbec66535ce81c80c61b727e5a'),
  ('src_project_opendart_status_quota_001', 'https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052', 'f0900ebafae2875d681b7d5dc0c03124ad5202c0cb7be6dc46a13e03017ee7aa', 'f25a5ac8beb4db0c881b04c5363818e8d16214e602e5acb4e55b8de6a0704904'),
  ('src_project_sharpe_drawdown_partial_metrics_001', 'https://doi.org/10.1111/joes.12520', 'a0eedc605692a151c99c0d9e0cbd4e81d77bf0a6bc14e0e20586bf4b38fa62f4', '9a5d8f18d4e431c8d9f2418a45c88235cc02d23dad7664cc9b1b096f07c37161'),
  ('src_project_threshold_cvar_not_exact_es_001', 'https://doi.org/10.1111/1468-0300.00091', 'ebb0772e904a94e2b0145425ce7e11764177c3d9e850829d05ec6501c34af7b3', '467cfc786a9eb8e85321921f81e4757c93fea3c1107b408c0cdf3b1ae4cbbad8'),
  ('src_project_valuation_delta_not_guard_delta_001', 'https://doi.org/10.1086/260062', '2bdb21b397aa564469afdd559e2516ae56fcf3ca7a2f3a83f2e499b050a0eb8f', 'ee125d8cc2710fa99d276b5ee08fabe871e434c0b01cca520b148e6a69cdaae8'),
  ('src_project_var_es_coherence_001', 'https://doi.org/10.1111/1467-9965.00068', '36cb0539f3ad78ab26143268c394b8cd03805bbec6142419d43709ce602e3206', '1f608e0a780144349bd83e9db211a6526a495f66b044df94b1b7fe5ba12855ca')
ON CONFLICT (source_id) DO NOTHING;

DO $rag_v2_immutable_external_exact30_allowlist_cardinality$
BEGIN
  IF (SELECT count(*) FROM rag_v2_immutable_external_exact30_source_allowlist) <> 30 THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 allowlist cardinality drifted'
      USING ERRCODE = '23514';
  END IF;
END;
$rag_v2_immutable_external_exact30_allowlist_cardinality$;

CREATE TABLE rag_v2_immutable_external_exact30_voyage_component_manifests (
  component_generation_id text PRIMARY KEY,
  component_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  member_digests text[] NOT NULL,
  manifest_hash text NOT NULL,
  generation_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_external_exact30_voyage_manifest_generation_fkey
    FOREIGN KEY (component_generation_id, component_scope)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, component_scope)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_external_exact30_voyage_manifest_scope_check
    CHECK (component_scope = 'EXACT30'),
  CONSTRAINT rag_v2_immutable_external_exact30_voyage_manifest_profile_check
    CHECK (embedding_profile_id = 'voyage_context_4_1024_v1'),
  CONSTRAINT rag_v2_immutable_external_exact30_voyage_manifest_hash_check
    CHECK (manifest_hash ~ '^[0-9a-f]{64}$' AND generation_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_external_exact30_voyage_manifest_members_check
    CHECK (
      cardinality(member_digests) = 30
      AND array_ndims(member_digests) = 1
      AND array_position(member_digests, NULL) IS NULL
    )
);
ALTER TABLE rag_v2_immutable_external_exact30_voyage_component_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_external_exact30_voyage_component_manifests FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_external_exact30_voyage_manifest_flyway_write
  ON rag_v2_immutable_external_exact30_voyage_component_manifests
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE FUNCTION rag_v2_immutable_external_exact30_voyage_manifest_hash(
  p_member_digests text[]
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_external_exact30_voyage_manifest_hash$
DECLARE
  encoded_members text;
BEGIN
  IF cardinality(p_member_digests) <> 30
     OR array_ndims(p_member_digests) <> 1
     OR array_position(p_member_digests, NULL) IS NOT NULL
     OR EXISTS (
       SELECT 1 FROM unnest(p_member_digests) AS member_digest
       WHERE member_digest !~ '^[0-9a-f]{64}$'
     )
     OR cardinality(p_member_digests) <> (
       SELECT count(DISTINCT member_digest) FROM unnest(p_member_digests) AS member_digest
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage member manifest is invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT string_agg(pg_catalog.to_json(member_digest)::text, ',' ORDER BY ordinal)
  INTO encoded_members
  FROM unnest(p_member_digests) WITH ORDINALITY AS digest_row(member_digest, ordinal);
  RETURN encode(public.digest(convert_to(
    '{"componentScope":"EXACT30","embeddingProfileId":"voyage_context_4_1024_v1","members":[' ||
    encoded_members || '],"schemaVersion":1}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_external_exact30_voyage_manifest_hash$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_manifest_hash(text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_manifest_hash(text[]) FROM PUBLIC;

CREATE FUNCTION rag_v2_immutable_external_exact30_voyage_generation_hash(
  p_expected_source_count integer,
  p_expected_chunk_count integer,
  p_manifest_hash text
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_external_exact30_voyage_generation_hash$
BEGIN
  IF p_expected_source_count <> 30
     OR p_expected_chunk_count < p_expected_source_count
     OR p_manifest_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage generation hash arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"componentScope":"EXACT30","embeddingProfileId":"voyage_context_4_1024_v1","expectedChunkCount":' ||
    p_expected_chunk_count::text || ',"expectedSourceCount":30,"manifestHash":' ||
    pg_catalog.to_json(p_manifest_hash)::text || ',"schemaVersion":1}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_external_exact30_voyage_generation_hash$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_generation_hash(integer, integer, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_generation_hash(integer, integer, text) FROM PUBLIC;

CREATE FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(
  p_source_id text,
  p_canonical_https_url text,
  p_raw_content_sha256 text,
  p_source_card_sha256 text
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
  )
$rag_v2_immutable_external_exact30_voyage_source_is_approved$;
ALTER FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_external_exact30_voyage_source_is_approved(text, text, text, text) FROM PUBLIC;

CREATE FUNCTION rag_v2_immutable_external_exact30_voyage_source_member_digest(
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
       source_record.exact30_source_card_sha256
     ) IS NOT TRUE THEN
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
  IF encoded_chunks IS NULL OR joined_canonical_text IS NULL THEN
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

CREATE FUNCTION rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(
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
    count(*)::integer,
    coalesce(sum(selected_source.chunk_count), 0)::integer
  INTO observed_member_digests, observed_source_total, observed_chunk_total
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
        source.exact30_source_card_sha256
      ) IS TRUE
    GROUP BY source.source_revision_id, source.source_id
  ) AS selected_source;
  IF observed_member_digests IS NULL
     OR array_position(observed_member_digests, NULL) IS NOT NULL
     OR observed_source_total <> generation_record.expected_source_count
     OR observed_chunk_total <> generation_record.expected_chunk_count
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

CREATE FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(p_payload jsonb)
RETURNS TABLE (
  component_generation_id text,
  materialization_run_id text,
  state text,
  source_reused boolean,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_rag_v2_immutable_external_exact30_voyage_document$
#variable_conflict use_column
DECLARE
  payload_scope text;
  payload_generation_id text;
  payload_run_id text;
  payload_generation_hash text;
  payload_manifest_hash text;
  payload_expected_source_count integer;
  payload_expected_chunk_count integer;
  payload_member_digests text[];
  payload_source_member_digest text;
  computed_manifest_hash text;
  computed_generation_hash text;
  payload_source jsonb;
  payload_document_id text;
  payload_source_id text;
  payload_source_revision_id text;
  payload_source_revision_sha256 text;
  payload_raw_content_sha256 text;
  payload_normalized_document_ir_sha256 text;
  payload_canonical_text_sha256 text;
  payload_canonical_text text;
  payload_mime_type text;
  payload_parser_version text;
  payload_tokenizer_version text;
  payload_document_ir jsonb;
  payload_source_locator jsonb;
  payload_canonical_https_url text;
  payload_source_card_sha256 text;
  payload_machine_fetch_allowed boolean;
  payload_local_processing_allowed boolean;
  payload_external_embedding_allowed boolean;
  payload_external_generation_allowed boolean;
  payload_external_processing_eligible boolean;
  payload_citation_title text;
  payload_topics text[];
  payload_chunk jsonb;
  payload_embedding jsonb;
  payload_chunk_id text;
  payload_chunk_ordinal integer;
  payload_chunk_text text;
  payload_chunk_sha256 text;
  payload_chunk_locator jsonb;
  payload_chunk_heading_path text[];
  payload_chunk_token_count integer;
  payload_chunk_contains_table boolean;
  payload_embedding_input_hash text;
  payload_context_set_hash text;
  payload_embedding_vector vector(1024);
  cached_embedding_vector vector(1024);
  existing_embedding_input_hash text;
  existing_context_set_hash text;
  expected_source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  expected_generation_record public.rag_v2_immutable_component_generations%ROWTYPE;
  expected_manifest_record public.rag_v2_immutable_external_exact30_voyage_component_manifests%ROWTYPE;
  existing_run_state text;
  observed_chunk_count integer := 0;
  observed_embedding_count integer := 0;
  observed_source_text text := '';
  first_chunk_locator jsonb;
  expected_context_set_hash text;
  observed_source_total integer := 0;
  observed_chunk_total integer := 0;
  existing_membership_count integer := 0;
  existing_component_membership_count integer := 0;
  reused_embedding_count integer := 0;
  source_was_reused boolean := false;
  embedding_was_reused boolean := false;
  complete_state text := 'STAGING';
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR octet_length(p_payload::text) NOT BETWEEN 2 AND 16777216
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_payload) AS root_key
       WHERE root_key NOT IN (
         'componentGenerationId', 'componentScope', 'embeddingProfileId', 'expectedChunkCount',
         'expectedSourceCount', 'generationHash', 'manifestHash', 'materializationRunId',
         'memberDigests', 'schemaVersion', 'source'
       )
     )
     OR NOT (p_payload ?& ARRAY[
       'componentGenerationId', 'componentScope', 'embeddingProfileId', 'expectedChunkCount',
       'expectedSourceCount', 'generationHash', 'manifestHash', 'materializationRunId',
       'memberDigests', 'schemaVersion', 'source'
     ])
     OR jsonb_typeof(p_payload -> 'schemaVersion') <> 'number'
     OR p_payload ->> 'schemaVersion' <> '1'
     OR jsonb_typeof(p_payload -> 'componentGenerationId') <> 'string'
     OR jsonb_typeof(p_payload -> 'componentScope') <> 'string'
     OR jsonb_typeof(p_payload -> 'embeddingProfileId') <> 'string'
     OR jsonb_typeof(p_payload -> 'generationHash') <> 'string'
     OR jsonb_typeof(p_payload -> 'manifestHash') <> 'string'
     OR jsonb_typeof(p_payload -> 'materializationRunId') <> 'string'
     OR p_payload ->> 'embeddingProfileId' <> 'voyage_context_4_1024_v1'
     OR jsonb_typeof(p_payload -> 'expectedSourceCount') <> 'number'
     OR jsonb_typeof(p_payload -> 'expectedChunkCount') <> 'number'
     OR (p_payload -> 'expectedSourceCount')::text !~ '^(0|[1-9][0-9]*)$'
     OR (p_payload -> 'expectedChunkCount')::text !~ '^(0|[1-9][0-9]*)$'
     OR jsonb_typeof(p_payload -> 'memberDigests') <> 'array'
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_payload -> 'memberDigests') AS digest_item(value)
       WHERE jsonb_typeof(digest_item.value) <> 'string'
     )
     OR jsonb_typeof(p_payload -> 'source') <> 'object' THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage staging arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_scope := p_payload ->> 'componentScope';
  payload_generation_id := p_payload ->> 'componentGenerationId';
  payload_run_id := p_payload ->> 'materializationRunId';
  payload_generation_hash := p_payload ->> 'generationHash';
  payload_manifest_hash := p_payload ->> 'manifestHash';
  payload_expected_source_count := (p_payload ->> 'expectedSourceCount')::integer;
  payload_expected_chunk_count := (p_payload ->> 'expectedChunkCount')::integer;
  SELECT coalesce(array_agg(digest_item.value ORDER BY digest_item.ordinality), ARRAY[]::text[])
  INTO payload_member_digests
  FROM jsonb_array_elements_text(p_payload -> 'memberDigests') WITH ORDINALITY AS digest_item(value, ordinality);
  payload_source := p_payload -> 'source';
  IF payload_scope <> 'EXACT30'
     OR payload_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR payload_run_id !~ '^rgr_run_[0-9a-f]{32}$'
     OR payload_generation_hash !~ '^[0-9a-f]{64}$'
     OR payload_manifest_hash !~ '^[0-9a-f]{64}$'
     OR payload_expected_source_count <> 30
     OR payload_expected_chunk_count < payload_expected_source_count
     OR payload_expected_chunk_count > 100000
     OR cardinality(payload_member_digests) <> 30
     OR array_position(payload_member_digests, NULL) IS NOT NULL
     OR EXISTS (
       SELECT 1 FROM unnest(payload_member_digests) AS member_digest
       WHERE member_digest !~ '^[0-9a-f]{64}$'
     )
     OR cardinality(payload_member_digests) <> (
       SELECT count(DISTINCT member_digest) FROM unnest(payload_member_digests) AS member_digest
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component identity is invalid'
      USING ERRCODE = '22023';
  END IF;
  computed_manifest_hash := public.rag_v2_immutable_external_exact30_voyage_manifest_hash(
    payload_member_digests
  );
  computed_generation_hash := public.rag_v2_immutable_external_exact30_voyage_generation_hash(
    payload_expected_source_count, payload_expected_chunk_count, computed_manifest_hash
  );
  IF payload_manifest_hash <> computed_manifest_hash
     OR payload_generation_hash <> computed_generation_hash
     OR payload_generation_id <> 'rgr_' || substr(computed_generation_hash, 1, 32)
     OR payload_run_id <> 'rgr_run_' || substr(
       encode(digest(
         'rag-v2-external-exact30-voyage-run|' || payload_generation_id || '|' || payload_manifest_hash,
         'sha256'
       ), 'hex'),
       1,
       32
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component hash binding is invalid'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
       SELECT 1 FROM jsonb_object_keys(payload_source) AS source_key
       WHERE source_key NOT IN (
         'accessEvidenceSha256', 'canonicalHttpsUrl', 'canonicalText', 'canonicalTextSha256',
         'chunks', 'citationTitle', 'documentId', 'documentIr', 'embeddings',
         'externalEmbeddingAllowed', 'externalGenerationAllowed', 'externalProcessingEligible',
         'licenseEvidenceSha256', 'localProcessingAllowed', 'machineFetchAllowed', 'mimeType',
         'oaSourceCard', 'oaTrackId', 'parserVersion', 'rawContentSha256', 'retrievalTopics',
         'sourceCardSha256', 'sourceId', 'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256',
         'tokenizerVersion'
       )
     )
     OR NOT (payload_source ?& ARRAY[
       'accessEvidenceSha256', 'canonicalHttpsUrl', 'canonicalText', 'canonicalTextSha256',
       'chunks', 'citationTitle', 'documentId', 'documentIr', 'embeddings',
       'externalEmbeddingAllowed', 'externalGenerationAllowed', 'externalProcessingEligible',
       'licenseEvidenceSha256', 'localProcessingAllowed', 'machineFetchAllowed', 'mimeType',
       'oaSourceCard', 'oaTrackId', 'parserVersion', 'rawContentSha256', 'retrievalTopics',
       'sourceCardSha256', 'sourceId', 'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256',
       'tokenizerVersion'
     ])
     OR jsonb_typeof(payload_source -> 'documentIr') <> 'object'
     OR jsonb_typeof(payload_source -> 'documentId') <> 'string'
     OR jsonb_typeof(payload_source -> 'sourceId') <> 'string'
     OR jsonb_typeof(payload_source -> 'sourceRevisionId') <> 'string'
     OR jsonb_typeof(payload_source -> 'sourceRevisionSha256') <> 'string'
     OR jsonb_typeof(payload_source -> 'rawContentSha256') <> 'string'
     OR jsonb_typeof(payload_source -> 'canonicalTextSha256') <> 'string'
     OR jsonb_typeof(payload_source -> 'canonicalText') <> 'string'
     OR jsonb_typeof(payload_source -> 'mimeType') <> 'string'
     OR jsonb_typeof(payload_source -> 'parserVersion') <> 'string'
     OR jsonb_typeof(payload_source -> 'tokenizerVersion') <> 'string'
     OR jsonb_typeof(payload_source -> 'canonicalHttpsUrl') <> 'string'
     OR jsonb_typeof(payload_source -> 'citationTitle') <> 'string'
     OR jsonb_typeof(payload_source -> 'chunks') <> 'array'
     OR jsonb_typeof(payload_source -> 'embeddings') <> 'array'
     OR jsonb_typeof(payload_source -> 'retrievalTopics') <> 'array'
     OR jsonb_typeof(payload_source -> 'sourceLocator') <> 'object'
     OR jsonb_typeof(payload_source -> 'machineFetchAllowed') <> 'boolean'
     OR jsonb_typeof(payload_source -> 'localProcessingAllowed') <> 'boolean'
     OR jsonb_typeof(payload_source -> 'externalEmbeddingAllowed') <> 'boolean'
     OR jsonb_typeof(payload_source -> 'externalGenerationAllowed') <> 'boolean'
     OR jsonb_typeof(payload_source -> 'externalProcessingEligible') <> 'boolean'
     OR jsonb_typeof(payload_source -> 'sourceCardSha256') <> 'string'
     OR jsonb_typeof(payload_source -> 'oaTrackId') <> 'null'
     OR jsonb_typeof(payload_source -> 'oaSourceCard') <> 'null'
     OR jsonb_typeof(payload_source -> 'licenseEvidenceSha256') <> 'null'
     OR jsonb_typeof(payload_source -> 'accessEvidenceSha256') <> 'null'
     OR jsonb_array_length(payload_source -> 'chunks') NOT BETWEEN 1 AND 50000
     OR jsonb_array_length(payload_source -> 'embeddings') <> jsonb_array_length(payload_source -> 'chunks') THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source payload is invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_document_id := payload_source ->> 'documentId';
  payload_source_id := payload_source ->> 'sourceId';
  payload_source_revision_id := payload_source ->> 'sourceRevisionId';
  payload_source_revision_sha256 := payload_source ->> 'sourceRevisionSha256';
  payload_raw_content_sha256 := payload_source ->> 'rawContentSha256';
  payload_normalized_document_ir_sha256 := payload_source -> 'documentIr' ->> 'normalizedContentSha256';
  payload_canonical_text_sha256 := payload_source ->> 'canonicalTextSha256';
  payload_canonical_text := payload_source ->> 'canonicalText';
  payload_mime_type := payload_source ->> 'mimeType';
  payload_parser_version := payload_source ->> 'parserVersion';
  payload_tokenizer_version := payload_source ->> 'tokenizerVersion';
  payload_document_ir := payload_source -> 'documentIr';
  payload_source_locator := payload_source -> 'sourceLocator';
  payload_canonical_https_url := payload_source ->> 'canonicalHttpsUrl';
  payload_source_card_sha256 := payload_source ->> 'sourceCardSha256';
  payload_machine_fetch_allowed := (payload_source ->> 'machineFetchAllowed')::boolean;
  payload_local_processing_allowed := (payload_source ->> 'localProcessingAllowed')::boolean;
  payload_external_embedding_allowed := (payload_source ->> 'externalEmbeddingAllowed')::boolean;
  payload_external_generation_allowed := (payload_source ->> 'externalGenerationAllowed')::boolean;
  payload_external_processing_eligible := (payload_source ->> 'externalProcessingEligible')::boolean;
  payload_citation_title := payload_source ->> 'citationTitle';
  SELECT coalesce(array_agg(topic.value ORDER BY topic.ordinality), ARRAY[]::text[])
  INTO payload_topics
  FROM jsonb_array_elements_text(payload_source -> 'retrievalTopics') WITH ORDINALITY AS topic(value, ordinality);
  IF payload_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR payload_source_id !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_id !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_raw_content_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_normalized_document_ir_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text IS NULL
     OR octet_length(payload_canonical_text) NOT BETWEEN 1 AND 16777216
     OR payload_canonical_text_sha256 <> encode(digest(payload_canonical_text, 'sha256'), 'hex')
     OR payload_mime_type IS NULL
     OR payload_parser_version IS NULL
     OR payload_tokenizer_version IS NULL
     OR char_length(payload_mime_type) NOT BETWEEN 3 AND 128
     OR char_length(payload_parser_version) NOT BETWEEN 1 AND 128
     OR char_length(payload_tokenizer_version) NOT BETWEEN 1 AND 128
     OR payload_citation_title IS NULL
     OR char_length(payload_citation_title) NOT BETWEEN 1 AND 500
     OR btrim(payload_citation_title) = ''
     OR payload_citation_title ~ '[[:cntrl:]]'
     OR public.rag_v2_immutable_retrieval_topics_are_valid(payload_topics) IS NOT TRUE
     OR public.rag_v2_immutable_locator_is_valid(payload_source_locator) IS NOT TRUE
     OR public.rag_v2_immutable_public_https_url_is_valid(payload_canonical_https_url) IS NOT TRUE
     OR payload_document_ir ->> 'sourceId' IS DISTINCT FROM payload_source_id
     OR payload_document_ir ->> 'sourceRevisionId' IS DISTINCT FROM payload_source_revision_id
     OR payload_document_ir ->> 'mimeType' IS DISTINCT FROM payload_mime_type
     OR payload_document_ir ->> 'rawContentSha256' IS DISTINCT FROM payload_raw_content_sha256
     OR payload_document_ir ->> 'normalizedContentSha256' IS DISTINCT FROM payload_normalized_document_ir_sha256
     OR payload_document_ir -> 'parserEvidence' ->> 'parserVersion' IS DISTINCT FROM payload_parser_version
     OR public.rag_v2_immutable_document_ir_structure_is_valid(payload_document_ir) IS NOT TRUE
     OR payload_machine_fetch_allowed
     OR NOT payload_local_processing_allowed
     OR NOT payload_external_embedding_allowed
     OR NOT payload_external_generation_allowed
     OR NOT payload_external_processing_eligible
     OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
       payload_source_id,
       payload_canonical_https_url,
       payload_raw_content_sha256,
       payload_source_card_sha256
     ) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source metadata is invalid'
      USING ERRCODE = '22023';
  END IF;

  -- 전체 component lock과 source identity lock을 분리해 resume 때 서로 다른 generation이 같은
  -- S4.7C source/chunk cache를 동시에 새로 쓰는 race를 닫는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-external-exact30-voyage|' || payload_generation_id, 0)
  );
  SELECT * INTO expected_generation_record
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = payload_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_component_generations (
      component_generation_id, owner_user_id, component_scope, embedding_profile_id, state,
      evaluation_status, expected_source_count, expected_chunk_count, actual_source_count,
      actual_chunk_count, generation_hash, manifest_hash
    ) VALUES (
      payload_generation_id, NULL, 'EXACT30', 'voyage_context_4_1024_v1', 'STAGING',
      'PENDING', 30, payload_expected_chunk_count, 0, 0,
      payload_generation_hash, payload_manifest_hash
    );
  ELSIF expected_generation_record.owner_user_id IS NOT NULL
     OR expected_generation_record.component_scope <> 'EXACT30'
     OR expected_generation_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR expected_generation_record.state <> 'STAGING'
     OR expected_generation_record.evaluation_status <> 'PENDING'
     OR expected_generation_record.expected_source_count <> 30
     OR expected_generation_record.expected_chunk_count <> payload_expected_chunk_count
     OR expected_generation_record.generation_hash <> payload_generation_hash
     OR expected_generation_record.manifest_hash <> payload_manifest_hash THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component conflicts'
      USING ERRCODE = '23505';
  END IF;

  SELECT * INTO expected_manifest_record
  FROM public.rag_v2_immutable_external_exact30_voyage_component_manifests
  WHERE component_generation_id = payload_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_external_exact30_voyage_component_manifests (
      component_generation_id, component_scope, embedding_profile_id, member_digests,
      manifest_hash, generation_hash
    ) VALUES (
      payload_generation_id, 'EXACT30', 'voyage_context_4_1024_v1', payload_member_digests,
      payload_manifest_hash, payload_generation_hash
    );
  ELSIF expected_manifest_record.component_scope <> 'EXACT30'
     OR expected_manifest_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR expected_manifest_record.member_digests IS DISTINCT FROM payload_member_digests
     OR expected_manifest_record.manifest_hash <> payload_manifest_hash
     OR expected_manifest_record.generation_hash <> payload_generation_hash THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage member manifest conflicts'
      USING ERRCODE = '23505';
  END IF;

  SELECT run.state INTO existing_run_state
  FROM public.rag_v2_immutable_materialization_runs AS run
  WHERE run.materialization_run_id = payload_run_id
    AND run.owner_user_id IS NULL
    AND run.component_generation_id = payload_generation_id
    AND run.component_scope = 'EXACT30'
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_materialization_runs (
      materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state
    ) VALUES (payload_run_id, NULL, payload_generation_id, 'EXACT30', NULL, 'OPEN');
  ELSIF existing_run_state NOT IN ('OPEN', 'STAGED') THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage run is not resumable'
      USING ERRCODE = '23505';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-external-exact30-voyage-source|' || payload_source_revision_id,
      0
    )
  );
  SELECT * INTO expected_source_record
  FROM public.rag_v2_immutable_source_revisions
  WHERE source_revision_id = payload_source_revision_id
  FOR UPDATE;
  IF FOUND THEN
    source_was_reused := true;
    IF expected_source_record.owner_user_id IS NOT NULL
       OR expected_source_record.document_id <> payload_document_id
       OR expected_source_record.source_id <> payload_source_id
       OR expected_source_record.source_scope <> 'EXACT30'
       OR expected_source_record.source_revision_sha256 <> payload_source_revision_sha256
       OR expected_source_record.raw_content_sha256 <> payload_raw_content_sha256
       OR expected_source_record.normalized_document_ir_sha256 <> payload_normalized_document_ir_sha256
       OR expected_source_record.canonical_text_sha256 <> payload_canonical_text_sha256
       OR expected_source_record.document_ir <> payload_document_ir
       OR expected_source_record.canonical_text <> payload_canonical_text
       OR expected_source_record.source_locator <> payload_source_locator
       OR expected_source_record.canonical_https_url <> payload_canonical_https_url
       OR expected_source_record.exact30_source_card_sha256 <> payload_source_card_sha256
       OR expected_source_record.license_evidence_sha256 IS NOT NULL
       OR expected_source_record.access_evidence_sha256 IS NOT NULL
       OR expected_source_record.mime_type <> payload_mime_type
       OR expected_source_record.machine_fetch_allowed
       OR NOT expected_source_record.local_processing_allowed
       OR NOT expected_source_record.external_embedding_allowed
       OR NOT expected_source_record.external_generation_allowed
       OR NOT expected_source_record.external_processing_eligible
       OR expected_source_record.parser_version <> payload_parser_version
       OR expected_source_record.tokenizer_version <> payload_tokenizer_version
       OR expected_source_record.retrieval_topics <> payload_topics
       OR expected_source_record.citation_title <> payload_citation_title
       OR expected_source_record.oa_track_id IS NOT NULL
       OR expected_source_record.reserve_source THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source revision conflicts'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    INSERT INTO public.rag_v2_immutable_source_revisions (
      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256,
      canonical_text_sha256, document_ir, canonical_text, sanitized_display_name, source_locator,
      canonical_https_url, exact30_source_card_sha256, license_evidence_sha256, access_evidence_sha256,
      mime_type, machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version,
      retrieval_topics, citation_title
    ) VALUES (
      payload_source_revision_id, payload_document_id, payload_source_id, NULL, 'EXACT30', NULL,
      false, payload_source_revision_sha256, payload_raw_content_sha256, payload_normalized_document_ir_sha256,
      payload_canonical_text_sha256, payload_document_ir, payload_canonical_text, NULL, payload_source_locator,
      payload_canonical_https_url, payload_source_card_sha256, NULL, NULL,
      payload_mime_type, false, true, true, true, true, payload_parser_version, payload_tokenizer_version,
      payload_topics, payload_citation_title
    );
  END IF;

  -- chunk/embedding payload는 source-local deterministic order를 강제한다. one source의 context hash가
  -- 달라지면 same document group이 아닌 것으로 간주해 provider result를 stage하지 않는다.
  IF EXISTS (
       SELECT 1
       FROM jsonb_array_elements(payload_source -> 'chunks') AS entry(value)
       GROUP BY entry.value ->> 'chunkId'
       HAVING count(*) <> 1
     )
     OR EXISTS (
       SELECT 1
       FROM jsonb_array_elements(payload_source -> 'embeddings') AS entry(value)
       GROUP BY entry.value ->> 'chunkId'
       HAVING count(*) <> 1
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage chunk identity is duplicated'
      USING ERRCODE = '22023';
  END IF;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    observed_chunk_count := observed_chunk_count + 1;
    IF jsonb_typeof(payload_chunk) <> 'object'
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(payload_chunk) AS chunk_key
         WHERE chunk_key NOT IN (
           'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
           'containsTable', 'headingPath', 'locator', 'tokenCount'
         )
       )
       OR NOT (payload_chunk ?& ARRAY[
         'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
         'containsTable', 'headingPath', 'locator', 'tokenCount'
       ])
       OR jsonb_typeof(payload_chunk -> 'headingPath') <> 'array'
       OR jsonb_typeof(payload_chunk -> 'locator') <> 'object'
       OR jsonb_typeof(payload_chunk -> 'chunkOrdinal') <> 'number'
       OR jsonb_typeof(payload_chunk -> 'containsTable') <> 'boolean'
       OR jsonb_typeof(payload_chunk -> 'tokenCount') <> 'number'
       OR (payload_chunk -> 'chunkOrdinal')::text !~ '^(0|[1-9][0-9]*)$'
       OR (payload_chunk -> 'tokenCount')::text !~ '^(0|[1-9][0-9]*)$'
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(payload_chunk -> 'headingPath') AS heading(value)
         WHERE jsonb_typeof(heading.value) <> 'string'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage chunk is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_chunk_id := payload_chunk ->> 'chunkId';
    payload_chunk_ordinal := (payload_chunk ->> 'chunkOrdinal')::integer;
    payload_chunk_text := payload_chunk ->> 'canonicalText';
    payload_chunk_sha256 := payload_chunk ->> 'canonicalTextSha256';
    payload_chunk_locator := payload_chunk -> 'locator';
    payload_chunk_token_count := (payload_chunk ->> 'tokenCount')::integer;
    payload_chunk_contains_table := (payload_chunk ->> 'containsTable')::boolean;
    SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
    INTO payload_chunk_heading_path
    FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);
    IF payload_chunk_id !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR payload_chunk_ordinal <> observed_chunk_count
       OR payload_chunk_text IS NULL
       OR payload_chunk_sha256 !~ '^[0-9a-f]{64}$'
       OR payload_chunk_sha256 <> encode(digest(payload_chunk_text, 'sha256'), 'hex')
       OR payload_chunk_token_count NOT BETWEEN 1 AND 600
       OR cardinality(payload_chunk_heading_path) > 12
       OR public.rag_v2_immutable_locator_is_valid(payload_chunk_locator) IS NOT TRUE THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage chunk contract is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF first_chunk_locator IS NULL THEN
      first_chunk_locator := payload_chunk_locator;
    END IF;
    IF observed_chunk_count = 1 THEN
      observed_source_text := payload_chunk_text;
    ELSE
      observed_source_text := observed_source_text || E'\n\n' || payload_chunk_text;
    END IF;
  END LOOP;
  IF first_chunk_locator IS NULL
     OR payload_source_locator <> first_chunk_locator
     OR payload_canonical_text <> observed_source_text THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source projection is invalid'
      USING ERRCODE = '22023';
  END IF;

  IF source_was_reused THEN
    IF EXISTS (
         SELECT 1
         FROM jsonb_array_elements(payload_source -> 'chunks') AS supplied(value)
         LEFT JOIN public.rag_v2_immutable_chunks AS stored
           ON stored.chunk_id = supplied.value ->> 'chunkId'
          AND stored.source_revision_id = payload_source_revision_id
          AND stored.owner_user_id IS NULL
          AND stored.source_scope = 'EXACT30'
         WHERE stored.chunk_id IS NULL
            OR stored.chunk_ordinal IS DISTINCT FROM (supplied.value ->> 'chunkOrdinal')::integer
            OR to_jsonb(stored.heading_path) IS DISTINCT FROM supplied.value -> 'headingPath'
            OR stored.locator IS DISTINCT FROM supplied.value -> 'locator'
            OR stored.canonical_text IS DISTINCT FROM supplied.value ->> 'canonicalText'
            OR stored.canonical_text_sha256 IS DISTINCT FROM supplied.value ->> 'canonicalTextSha256'
            OR stored.token_count IS DISTINCT FROM (supplied.value ->> 'tokenCount')::integer
            OR stored.contains_table IS DISTINCT FROM (supplied.value ->> 'containsTable')::boolean
       )
       OR (
         SELECT count(*)::integer
         FROM public.rag_v2_immutable_chunks AS stored
         WHERE stored.source_revision_id = payload_source_revision_id
           AND stored.owner_user_id IS NULL
           AND stored.source_scope = 'EXACT30'
       ) <> observed_chunk_count THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source cache conflicts'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    FOR payload_chunk IN
      SELECT value
      FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
      ORDER BY ordinality
    LOOP
      SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
      INTO payload_chunk_heading_path
      FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);
      INSERT INTO public.rag_v2_immutable_chunks (
        chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal, heading_path,
        locator, canonical_text, canonical_text_sha256, token_count, contains_table
      ) VALUES (
        payload_chunk ->> 'chunkId', payload_source_revision_id, NULL, 'EXACT30',
        (payload_chunk ->> 'chunkOrdinal')::integer, payload_chunk_heading_path,
        payload_chunk -> 'locator', payload_chunk ->> 'canonicalText', payload_chunk ->> 'canonicalTextSha256',
        (payload_chunk ->> 'tokenCount')::integer, (payload_chunk ->> 'containsTable')::boolean
      );
    END LOOP;
  END IF;

  SELECT count(*)::integer
  INTO existing_membership_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id = payload_generation_id
    AND membership.source_revision_id = payload_source_revision_id
    AND membership.owner_user_id IS NULL
    AND membership.component_scope = 'EXACT30';
  SELECT count(*)::integer
  INTO existing_component_membership_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id = payload_generation_id
    AND membership.component_scope = 'EXACT30';
  IF source_was_reused AND existing_membership_count > 0 THEN
    -- 이미 같은 generation에 완결된 source가 있으면 caller가 다른 vector를 덮어쓰지 못하게
    -- persisted hash projection만 재검증하고 idempotent receipt로 끝낸다.
    payload_source_member_digest := public.rag_v2_immutable_external_exact30_voyage_source_member_digest(
      payload_generation_id, payload_source_revision_id
    );
    IF payload_source_member_digest IS NULL
       OR array_position(payload_member_digests, payload_source_member_digest) IS NULL
       OR existing_membership_count <> observed_chunk_count
       OR (
         SELECT count(*)::integer
         FROM public.rag_v2_immutable_generation_embeddings AS embedding
         JOIN public.rag_v2_immutable_generation_memberships AS membership
           ON membership.component_generation_id = embedding.component_generation_id
          AND membership.chunk_id = embedding.chunk_id
          AND membership.component_scope = embedding.component_scope
         WHERE embedding.component_generation_id = payload_generation_id
           AND embedding.component_scope = 'EXACT30'
           AND membership.source_revision_id = payload_source_revision_id
       ) <> existing_membership_count
       OR EXISTS (
         SELECT 1
         FROM jsonb_array_elements(payload_source -> 'embeddings') AS supplied(value)
         LEFT JOIN public.rag_v2_immutable_generation_embeddings AS stored
           ON stored.component_generation_id = payload_generation_id
          AND stored.chunk_id = supplied.value ->> 'chunkId'
          AND stored.component_scope = 'EXACT30'
          AND stored.owner_user_id IS NULL
         WHERE stored.chunk_id IS NULL
            OR stored.embedding_input_hash IS DISTINCT FROM supplied.value ->> 'embeddingInputHash'
            OR stored.context_set_hash IS DISTINCT FROM supplied.value ->> 'contextSetHash'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source resume conflicts'
        USING ERRCODE = '23505';
    END IF;
    SELECT count(DISTINCT membership.source_revision_id)::integer, count(*)::integer
    INTO observed_source_total, observed_chunk_total
    FROM public.rag_v2_immutable_generation_memberships AS membership
    WHERE membership.component_generation_id = payload_generation_id
      AND membership.component_scope = 'EXACT30';
    RETURN QUERY SELECT payload_generation_id, payload_run_id,
      CASE
        WHEN observed_source_total = payload_expected_source_count
          AND observed_chunk_total = payload_expected_chunk_count THEN 'STAGED'
        ELSE 'STAGING'
      END,
      true, observed_source_total, observed_chunk_total;
    RETURN;
  END IF;

  SELECT entry.value ->> 'contextSetHash'
  INTO expected_context_set_hash
  FROM jsonb_array_elements(payload_source -> 'embeddings') AS entry(value)
  WHERE entry.value ->> 'chunkId' = ((payload_source -> 'chunks') -> 0 ->> 'chunkId');
  IF expected_context_set_hash IS NULL
     OR expected_context_set_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage context group is invalid'
      USING ERRCODE = '22023';
  END IF;

  -- generation membership가 vector보다 먼저 존재해야 FK와 source-to-vector binding이 동시에 닫힌다.
  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    INSERT INTO public.rag_v2_immutable_generation_memberships (
      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
    ) VALUES (
      payload_generation_id, payload_chunk ->> 'chunkId', payload_source_revision_id, NULL, 'EXACT30',
      existing_component_membership_count + (payload_chunk ->> 'chunkOrdinal')::integer
    );
  END LOOP;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    observed_embedding_count := observed_embedding_count + 1;
    payload_chunk_id := payload_chunk ->> 'chunkId';
    SELECT entry.value INTO payload_embedding
    FROM jsonb_array_elements(payload_source -> 'embeddings') AS entry(value)
    WHERE entry.value ->> 'chunkId' = payload_chunk_id;
    IF payload_embedding IS NULL
       OR jsonb_typeof(payload_embedding) <> 'object'
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(payload_embedding) AS embedding_key
         WHERE embedding_key NOT IN ('chunkId', 'contextSetHash', 'embedding', 'embeddingInputHash')
       )
       OR NOT (payload_embedding ?& ARRAY['chunkId', 'contextSetHash', 'embedding', 'embeddingInputHash'])
       OR jsonb_typeof(payload_embedding -> 'chunkId') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'contextSetHash') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'embeddingInputHash') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'embedding') <> 'array'
       OR jsonb_array_length(payload_embedding -> 'embedding') <> 1024
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(payload_embedding -> 'embedding') AS coordinate(value)
         WHERE jsonb_typeof(coordinate.value) <> 'number'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage embedding is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_embedding_input_hash := payload_embedding ->> 'embeddingInputHash';
    payload_context_set_hash := payload_embedding ->> 'contextSetHash';
    IF payload_embedding ->> 'chunkId' <> payload_chunk_id
       OR payload_embedding_input_hash !~ '^[0-9a-f]{64}$'
       OR payload_context_set_hash !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage embedding identity is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF payload_context_set_hash IS DISTINCT FROM expected_context_set_hash THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage context group drifted'
        USING ERRCODE = '23514';
    END IF;
    payload_embedding_vector := ((payload_embedding -> 'embedding')::text)::vector;
    IF vector_dims(payload_embedding_vector) <> 1024
       OR vector_norm(payload_embedding_vector)::text IN ('NaN', 'Infinity', '-Infinity')
       OR abs(vector_norm(payload_embedding_vector)::double precision - 1.0) > 0.00001 THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage vector is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_generation_memberships AS membership
      WHERE membership.component_generation_id = payload_generation_id
        AND membership.chunk_id = payload_chunk_id
        AND membership.source_revision_id = payload_source_revision_id
        AND membership.component_scope = 'EXACT30'
        AND membership.owner_user_id IS NULL
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage membership is invalid'
        USING ERRCODE = '22023';
    END IF;

    SELECT embedding.embedding_input_hash, embedding.context_set_hash
    INTO existing_embedding_input_hash, existing_context_set_hash
    FROM public.rag_v2_immutable_generation_embeddings AS embedding
    WHERE embedding.component_generation_id = payload_generation_id
      AND embedding.chunk_id = payload_chunk_id
      AND embedding.component_scope = 'EXACT30'
      AND embedding.owner_user_id IS NULL
    FOR SHARE;
    IF FOUND THEN
      IF existing_embedding_input_hash <> payload_embedding_input_hash
         OR existing_context_set_hash <> payload_context_set_hash THEN
        RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage embedding resume conflicts'
          USING ERRCODE = '23505';
      END IF;
    ELSE
      PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
          'rag-v2-immutable-external-exact30-voyage-cache|' || payload_chunk_id || '|' ||
          payload_embedding_input_hash || '|' || payload_context_set_hash,
          0
        )
      );
      embedding_was_reused := false;
      SELECT cache.embedding INTO cached_embedding_vector
      FROM public.rag_v2_immutable_embedding_cache AS cache
      WHERE cache.owner_user_id IS NULL
        AND cache.chunk_id = payload_chunk_id
        AND cache.source_scope = 'EXACT30'
        AND cache.embedding_profile_id = 'voyage_context_4_1024_v1'
        AND cache.embedding_input_hash = payload_embedding_input_hash
        AND cache.context_set_hash = payload_context_set_hash
      FOR SHARE;
      embedding_was_reused := FOUND;
      IF embedding_was_reused THEN
        payload_embedding_vector := cached_embedding_vector;
        reused_embedding_count := reused_embedding_count + 1;
      END IF;
      INSERT INTO public.rag_v2_immutable_generation_embeddings (
        component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
        embedding_input_hash, context_set_hash, embedding
      ) VALUES (
        payload_generation_id, payload_chunk_id, NULL, 'EXACT30', 'voyage_context_4_1024_v1',
        payload_embedding_input_hash, payload_context_set_hash, payload_embedding_vector
      );
      IF NOT embedding_was_reused THEN
        INSERT INTO public.rag_v2_immutable_embedding_cache (
          cache_id, owner_user_id, source_revision_id, chunk_id, source_scope, embedding_profile_id,
          embedding_input_hash, context_set_hash, embedding
        ) VALUES (
          'rgr_cache_' || substr(encode(digest(
            'rag-v2-immutable-external-exact30-voyage-cache|' || payload_generation_id || '|' || payload_chunk_id,
            'sha256'
          ), 'hex'), 1, 32),
          NULL, payload_source_revision_id, payload_chunk_id, 'EXACT30', 'voyage_context_4_1024_v1',
          payload_embedding_input_hash, payload_context_set_hash, payload_embedding_vector
        );
      END IF;
    END IF;

    INSERT INTO public.rag_v2_immutable_embedding_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope, component_generation_id,
      chunk_id, embedding_profile_id, embedding_input_hash, context_set_hash, reuse_state
    ) VALUES (
      'rgr_emb_' || substr(encode(digest(
        'rag-v2-immutable-external-exact30-voyage-embedding-receipt|' || payload_run_id || '|' || payload_chunk_id,
        'sha256'
      ), 'hex'), 1, 32),
      payload_run_id, NULL, 'EXACT30', payload_generation_id, payload_chunk_id,
      'voyage_context_4_1024_v1', payload_embedding_input_hash, payload_context_set_hash,
      CASE WHEN embedding_was_reused THEN 'REUSED' ELSE 'NEW' END
    ) ON CONFLICT (receipt_id) DO NOTHING;
  END LOOP;

  -- Membership must exist before its embedding because the generation-embedding FK closes accidental
  -- vector insertion for a chunk not bound to this exact immutable source revision.
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_generation_embeddings AS embedding
    LEFT JOIN public.rag_v2_immutable_generation_memberships AS membership
      ON membership.component_generation_id = embedding.component_generation_id
     AND membership.chunk_id = embedding.chunk_id
     AND membership.component_scope = embedding.component_scope
    WHERE embedding.component_generation_id = payload_generation_id
      AND embedding.component_scope = 'EXACT30'
      AND membership.chunk_id IS NULL
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage membership is missing'
      USING ERRCODE = '23514';
  END IF;

  IF observed_embedding_count <> observed_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage embedding count is invalid'
      USING ERRCODE = '22023';
  END IF;
  payload_source_member_digest := public.rag_v2_immutable_external_exact30_voyage_source_member_digest(
    payload_generation_id, payload_source_revision_id
  );
  IF payload_source_member_digest IS NULL
     OR array_position(payload_member_digests, payload_source_member_digest) IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage source is outside the bound member manifest'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_v2_immutable_source_receipts (
    receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
    raw_content_sha256, canonical_text_sha256, reuse_state
  ) VALUES (
    'rgr_src_' || substr(encode(digest(
      'rag-v2-immutable-external-exact30-voyage-source-receipt|' || payload_run_id || '|' || payload_source_revision_id,
      'sha256'
    ), 'hex'), 1, 32),
    payload_run_id, NULL, 'EXACT30', payload_source_revision_id,
    payload_raw_content_sha256, payload_canonical_text_sha256,
    CASE WHEN source_was_reused THEN 'REUSED' ELSE 'NEW' END
  ) ON CONFLICT (receipt_id) DO NOTHING;
  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    INSERT INTO public.rag_v2_immutable_chunk_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
      chunk_id, canonical_text_sha256, reuse_state
    ) VALUES (
      'rgr_chk_' || substr(encode(digest(
        'rag-v2-immutable-external-exact30-voyage-chunk-receipt|' || payload_run_id || '|' ||
        (payload_chunk ->> 'chunkId'),
        'sha256'
      ), 'hex'), 1, 32),
      payload_run_id, NULL, 'EXACT30', payload_source_revision_id,
      payload_chunk ->> 'chunkId', payload_chunk ->> 'canonicalTextSha256',
      CASE WHEN source_was_reused THEN 'REUSED' ELSE 'NEW' END
    ) ON CONFLICT (receipt_id) DO NOTHING;
  END LOOP;

  UPDATE public.rag_v2_immutable_materialization_runs AS run
  SET source_reused_count = run.source_reused_count + CASE WHEN source_was_reused THEN 1 ELSE 0 END,
      chunk_reused_count = run.chunk_reused_count + CASE WHEN source_was_reused THEN observed_chunk_count ELSE 0 END,
      embedding_reused_count = run.embedding_reused_count + reused_embedding_count
  WHERE run.materialization_run_id = payload_run_id
    AND run.owner_user_id IS NULL
    AND run.component_scope = 'EXACT30'
    AND run.state = 'OPEN';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage reuse receipt transition failed'
      USING ERRCODE = '23514';
  END IF;

  SELECT count(DISTINCT membership.source_revision_id)::integer, count(*)::integer
  INTO observed_source_total, observed_chunk_total
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id = payload_generation_id
    AND membership.component_scope = 'EXACT30';
  IF observed_source_total > payload_expected_source_count
     OR observed_chunk_total > payload_expected_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component exceeded exact membership'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.rag_v2_immutable_component_generations
  SET actual_source_count = observed_source_total,
      actual_chunk_count = observed_chunk_total
  WHERE component_generation_id = payload_generation_id
    AND owner_user_id IS NULL
    AND component_scope = 'EXACT30'
    AND embedding_profile_id = 'voyage_context_4_1024_v1'
    AND state = 'STAGING'
    AND evaluation_status = 'PENDING';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component transition failed'
      USING ERRCODE = '23514';
  END IF;
  IF observed_source_total = payload_expected_source_count
     AND observed_chunk_total = payload_expected_chunk_count THEN
    IF public.rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(
      payload_generation_id
    ) IS NOT TRUE THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component hash projection is invalid'
        USING ERRCODE = '23514';
    END IF;
    complete_state := 'STAGED';
    UPDATE public.rag_v2_immutable_materialization_runs
    SET state = 'STAGED'
    WHERE materialization_run_id = payload_run_id
      AND owner_user_id IS NULL
      AND component_scope = 'EXACT30'
      AND state = 'OPEN';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage run transition failed'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN QUERY SELECT payload_generation_id, payload_run_id, complete_state,
    source_was_reused, observed_source_total, observed_chunk_total;
END;
$stage_rag_v2_immutable_external_exact30_voyage_document$;
ALTER FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb) FROM PUBLIC;

-- 이 migration은 stage까지만 허용한다. 추후 별도 physical provider evidence writer가 state를
-- 평가/활성화하려 해도 persisted external-safe manifest의 재계산을 반드시 통과해야 한다.
CREATE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition$
BEGIN
  IF NEW.component_scope = 'EXACT30'
     AND NEW.embedding_profile_id = 'voyage_context_4_1024_v1'
     AND NEW.state IN ('EVALUATED', 'ACTIVE')
     AND EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_external_exact30_voyage_component_manifests AS manifest
       WHERE manifest.component_generation_id = NEW.component_generation_id
     )
     AND public.rag_v2_immutable_external_exact30_voyage_component_hashes_are_valid(
       NEW.component_generation_id
     ) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 external exact-30 Voyage component hash transition is invalid'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition$;
ALTER FUNCTION guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_external_exact30_voyage_component_hash_transition_guard
  BEFORE UPDATE OF state
  ON rag_v2_immutable_component_generations
  FOR EACH ROW
  EXECUTE FUNCTION guard_rag_v2_immutable_external_exact30_voyage_component_hash_transition();

DO $rag_v2_external_exact30_voyage_staging_writer_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_source_revisions,
      rag_v2_immutable_chunks,
      rag_v2_immutable_component_generations,
      rag_v2_immutable_generation_memberships,
      rag_v2_immutable_generation_embeddings,
      rag_v2_immutable_embedding_cache,
      rag_v2_immutable_materialization_runs,
      rag_v2_immutable_source_receipts,
      rag_v2_immutable_chunk_receipts,
      rag_v2_immutable_embedding_receipts,
      rag_v2_immutable_external_exact30_source_allowlist,
      rag_v2_immutable_external_exact30_voyage_component_manifests
    FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)
      TO decision_rag_writer;
  END IF;
END;
$rag_v2_external_exact30_voyage_staging_writer_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb) FROM PUBLIC;
