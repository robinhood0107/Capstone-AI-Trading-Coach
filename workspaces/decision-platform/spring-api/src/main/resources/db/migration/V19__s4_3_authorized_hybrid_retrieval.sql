-- S4.3 retrieval은 raw table SELECT가 아니라 owner/session에 묶인 짧은 수명의 opaque claim과
-- exact/lexical/dense별 독립 SECURITY DEFINER projection만 사용한다.
CREATE TABLE rag_source_card_verifications (
  source_revision_id text PRIMARY KEY
    REFERENCES rag_source_revisions(source_revision_id) ON DELETE RESTRICT,
  card_id text NOT NULL UNIQUE,
  status text NOT NULL,
  card_metadata_hash text NOT NULL,
  verified_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_source_card_verifications_card_id_check
    CHECK (card_id ~ '^card_[a-z0-9][a-z0-9_]{1,127}_[0-9]{3}$'),
  CONSTRAINT rag_source_card_verifications_status_check
    CHECK (status = 'VERIFIED'),
  CONSTRAINT rag_source_card_verifications_hash_check
    CHECK (card_metadata_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE rag_source_public_topics (
  source_id text NOT NULL REFERENCES rag_sources(source_id) ON DELETE RESTRICT,
  public_topic text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_source_public_topics_topic_check
    CHECK (
      public_topic IN (
        'API',
        'DATA',
        'FINANCIAL_ENGINEERING',
        'METHODOLOGY',
        'PRODUCT_RISK',
        'RISK'
      )
    ),
  CONSTRAINT rag_source_public_topics_pkey PRIMARY KEY (source_id, public_topic)
);
CREATE INDEX rag_source_public_topics_topic_source_idx
  ON rag_source_public_topics (public_topic, source_id);

CREATE TABLE rag_source_exact_identifiers (
  source_id text NOT NULL REFERENCES rag_sources(source_id) ON DELETE RESTRICT,
  identifier text NOT NULL,
  identifier_kind text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_source_exact_identifiers_kind_check
    CHECK (identifier_kind IN ('KIS_TR_ID', 'SYMBOL')),
  CONSTRAINT rag_source_exact_identifiers_value_check
    CHECK (
      (identifier_kind = 'KIS_TR_ID' AND identifier ~ '^[A-Z][A-Z0-9]{7,15}$')
      OR
      (identifier_kind = 'SYMBOL' AND identifier ~ '^[0-9]{6}$')
    ),
  CONSTRAINT rag_source_exact_identifiers_pkey
    PRIMARY KEY (source_id, identifier, identifier_kind),
  CONSTRAINT rag_source_exact_identifiers_unique UNIQUE (identifier, identifier_kind)
);
CREATE INDEX rag_source_exact_identifiers_lookup_idx
  ON rag_source_exact_identifiers (identifier, identifier_kind, source_id);

-- 기존 V18 active candidate는 exact S4.7B identity와 immutable metadata hash가 맞을 때만
-- VERIFIED sidecar로 승격한다. 다른 registry/version/source는 자동 backfill하지 않는다.
WITH approved_cards(source_id, card_id) AS (
  VALUES
    ('src_project_backtest_overfitting_001', 'card_backtest_overfitting_001'),
    ('src_project_bsm_continuous_hedge_assumptions_001', 'card_bsm_continuous_hedge_assumptions_001'),
    ('src_project_bsm_risk_neutral_001', 'card_bsm_risk_neutral_001'),
    ('src_project_bsm_time_to_expiry_001', 'card_bsm_time_to_expiry_001'),
    ('src_project_delta_hedge_residual_cost_001', 'card_delta_hedge_residual_cost_001'),
    ('src_project_ecos_pit_availability_001', 'card_ecos_pit_availability_001'),
    ('src_project_expected_payoff_measure_discount_001', 'card_expected_payoff_measure_discount_001'),
    ('src_project_finance_diffusion_not_ddpm_001', 'card_finance_diffusion_not_ddpm_001'),
    ('src_project_gold_futures_etf_132030_001', 'card_gold_futures_etf_132030_001'),
    ('src_project_hmm_latent_state_boundary_001', 'card_hmm_latent_state_boundary_001'),
    ('src_project_kis_adjusted_price_001', 'card_kis_adjusted_price_001'),
    ('src_project_kis_current_price_snapshot_001', 'card_kis_current_price_snapshot_001'),
    ('src_project_kis_discovery_write_boundary_001', 'card_kis_discovery_write_boundary_001'),
    ('src_project_kis_market_calendar_001', 'card_kis_market_calendar_001'),
    ('src_project_kis_rate_limit_token_001', 'card_kis_rate_limit_token_001'),
    ('src_project_krx_etf_etn_structure_001', 'card_krx_etf_etn_structure_001'),
    ('src_project_krx_etn_risk_indicator_001', 'card_krx_etn_risk_indicator_001'),
    ('src_project_krx_last_trading_settlement_001', 'card_krx_last_trading_settlement_001'),
    ('src_project_krx_service_coverage_001', 'card_krx_service_coverage_001'),
    ('src_project_mean_reversion_stationarity_001', 'card_mean_reversion_stationarity_001'),
    ('src_project_monte_carlo_not_stress_probability_001', 'card_monte_carlo_not_stress_probability_001'),
    ('src_project_naver_news_discovery_boundary_001', 'card_naver_news_discovery_boundary_001'),
    ('src_project_notional_not_exposure_001', 'card_notional_not_exposure_001'),
    ('src_project_opendart_corporation_code_001', 'card_opendart_corporation_code_001'),
    ('src_project_opendart_financial_statement_scope_001', 'card_opendart_financial_statement_scope_001'),
    ('src_project_opendart_status_quota_001', 'card_opendart_status_quota_001'),
    ('src_project_sharpe_drawdown_partial_metrics_001', 'card_sharpe_drawdown_partial_metrics_001'),
    ('src_project_threshold_cvar_not_exact_es_001', 'card_threshold_cvar_not_exact_es_001'),
    ('src_project_valuation_delta_not_guard_delta_001', 'card_valuation_delta_not_guard_delta_001'),
    ('src_project_var_es_coherence_001', 'card_var_es_coherence_001')
)
INSERT INTO rag_source_card_verifications (
  source_revision_id,
  card_id,
  status,
  card_metadata_hash,
  verified_at
)
SELECT
  revision.source_revision_id,
  approved.card_id,
  'VERIFIED',
  revision.metadata_hash,
  CASE
    WHEN approved.source_id IN (
      'src_project_ecos_pit_availability_001',
      'src_project_gold_futures_etf_132030_001',
      'src_project_kis_adjusted_price_001',
      'src_project_krx_service_coverage_001',
      'src_project_opendart_status_quota_001'
    )
    THEN timestamptz '2026-07-30T05:07:41Z'
    ELSE timestamptz '2026-07-31T00:00:00Z'
  END
FROM approved_cards AS approved
JOIN rag_sources AS source
  ON source.source_id = approved.source_id
 AND source.source_type = 'PROJECT_SOURCE_CARD'
 AND source.retired_at IS NULL
JOIN rag_source_revisions AS revision
  ON revision.source_id = source.source_id
 AND revision.registry_version = 's4-7b-source-card-v2'
 AND revision.tier = 'PROJECT'
 AND revision.access_level = 'PUBLIC'
 AND revision.initial_processing = 'PROJECT_AUTHORED_CARD'
 AND NOT revision.external_processing_allowed
ON CONFLICT (source_revision_id) DO NOTHING;

WITH approved_topics(source_id, public_topic) AS (
  VALUES
  ('src_project_backtest_overfitting_001', 'METHODOLOGY'),
  ('src_project_bsm_continuous_hedge_assumptions_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_bsm_continuous_hedge_assumptions_001', 'METHODOLOGY'),
  ('src_project_bsm_risk_neutral_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_bsm_risk_neutral_001', 'RISK'),
  ('src_project_bsm_time_to_expiry_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_bsm_time_to_expiry_001', 'METHODOLOGY'),
  ('src_project_delta_hedge_residual_cost_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_delta_hedge_residual_cost_001', 'RISK'),
  ('src_project_ecos_pit_availability_001', 'API'),
  ('src_project_ecos_pit_availability_001', 'DATA'),
  ('src_project_expected_payoff_measure_discount_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_expected_payoff_measure_discount_001', 'METHODOLOGY'),
  ('src_project_finance_diffusion_not_ddpm_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_finance_diffusion_not_ddpm_001', 'METHODOLOGY'),
  ('src_project_gold_futures_etf_132030_001', 'DATA'),
  ('src_project_gold_futures_etf_132030_001', 'PRODUCT_RISK'),
  ('src_project_hmm_latent_state_boundary_001', 'METHODOLOGY'),
  ('src_project_kis_adjusted_price_001', 'API'),
  ('src_project_kis_adjusted_price_001', 'DATA'),
  ('src_project_kis_current_price_snapshot_001', 'API'),
  ('src_project_kis_current_price_snapshot_001', 'DATA'),
  ('src_project_kis_discovery_write_boundary_001', 'API'),
  ('src_project_kis_discovery_write_boundary_001', 'METHODOLOGY'),
  ('src_project_kis_discovery_write_boundary_001', 'PRODUCT_RISK'),
  ('src_project_kis_market_calendar_001', 'API'),
  ('src_project_kis_market_calendar_001', 'DATA'),
  ('src_project_kis_rate_limit_token_001', 'API'),
  ('src_project_krx_etf_etn_structure_001', 'DATA'),
  ('src_project_krx_etf_etn_structure_001', 'PRODUCT_RISK'),
  ('src_project_krx_etn_risk_indicator_001', 'PRODUCT_RISK'),
  ('src_project_krx_etn_risk_indicator_001', 'RISK'),
  ('src_project_krx_last_trading_settlement_001', 'DATA'),
  ('src_project_krx_last_trading_settlement_001', 'METHODOLOGY'),
  ('src_project_krx_last_trading_settlement_001', 'PRODUCT_RISK'),
  ('src_project_krx_service_coverage_001', 'DATA'),
  ('src_project_mean_reversion_stationarity_001', 'METHODOLOGY'),
  ('src_project_monte_carlo_not_stress_probability_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_monte_carlo_not_stress_probability_001', 'RISK'),
  ('src_project_naver_news_discovery_boundary_001', 'API'),
  ('src_project_naver_news_discovery_boundary_001', 'DATA'),
  ('src_project_notional_not_exposure_001', 'RISK'),
  ('src_project_opendart_corporation_code_001', 'API'),
  ('src_project_opendart_corporation_code_001', 'DATA'),
  ('src_project_opendart_financial_statement_scope_001', 'API'),
  ('src_project_opendart_financial_statement_scope_001', 'DATA'),
  ('src_project_opendart_status_quota_001', 'API'),
  ('src_project_opendart_status_quota_001', 'DATA'),
  ('src_project_sharpe_drawdown_partial_metrics_001', 'METHODOLOGY'),
  ('src_project_sharpe_drawdown_partial_metrics_001', 'RISK'),
  ('src_project_threshold_cvar_not_exact_es_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_threshold_cvar_not_exact_es_001', 'RISK'),
  ('src_project_valuation_delta_not_guard_delta_001', 'FINANCIAL_ENGINEERING'),
  ('src_project_valuation_delta_not_guard_delta_001', 'RISK'),
    ('src_project_var_es_coherence_001', 'FINANCIAL_ENGINEERING'),
    ('src_project_var_es_coherence_001', 'RISK')
)
INSERT INTO rag_source_public_topics (source_id, public_topic)
SELECT approved.source_id, approved.public_topic
FROM approved_topics AS approved
JOIN rag_sources AS source
  ON source.source_id = approved.source_id
 AND source.source_type = 'PROJECT_SOURCE_CARD'
 AND source.retired_at IS NULL
ON CONFLICT DO NOTHING;

WITH approved_identifiers(source_id, identifier, identifier_kind) AS (
  VALUES
    ('src_project_kis_current_price_snapshot_001', 'FHKST01010100', 'KIS_TR_ID'),
    ('src_project_kis_adjusted_price_001', 'FHKST03010100', 'KIS_TR_ID'),
    ('src_project_gold_futures_etf_132030_001', '132030', 'SYMBOL')
)
INSERT INTO rag_source_exact_identifiers (source_id, identifier, identifier_kind)
SELECT approved.source_id, approved.identifier, approved.identifier_kind
FROM approved_identifiers AS approved
JOIN rag_sources AS source
  ON source.source_id = approved.source_id
 AND source.source_type = 'PROJECT_SOURCE_CARD'
 AND source.retired_at IS NULL
ON CONFLICT DO NOTHING;

-- writer는 verification/topic table DML을 받지 않고 immutable v2 revision과 일치하는 bounded
-- registration 함수만 실행한다.
CREATE FUNCTION register_rag_verified_source_card(
  p_source_revision_id text,
  p_source_id text,
  p_card_id text,
  p_card_metadata_hash text,
  p_verified_at timestamptz,
  p_public_topics text[]
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $register_rag_verified_source_card$
DECLARE
  inserted_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_source_revision_id !~ '^src_rev_[0-9a-f]{32}$'
     OR p_source_id !~ '^src_project_[a-z0-9_]+_[0-9]{3}$'
     OR p_card_id !~ '^card_[a-z0-9][a-z0-9_]{1,127}_[0-9]{3}$'
     OR p_card_id IS DISTINCT FROM regexp_replace(p_source_id, '^src_project_', 'card_')
     OR p_card_metadata_hash !~ '^[0-9a-f]{64}$'
     OR p_verified_at IS NULL
     OR p_verified_at > transaction_timestamp() + interval '1 minute'
     OR p_public_topics IS NULL
     OR cardinality(p_public_topics) NOT BETWEEN 1 AND 5
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_public_topics)))
        <> cardinality(p_public_topics)
     OR NOT (
       p_public_topics <@ ARRAY[
         'API',
         'DATA',
         'FINANCIAL_ENGINEERING',
         'METHODOLOGY',
         'PRODUCT_RISK',
         'RISK'
       ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_source_revisions AS revision
       JOIN public.rag_sources AS source
         ON source.source_id = revision.source_id
        AND source.source_type = 'PROJECT_SOURCE_CARD'
        AND source.retired_at IS NULL
       WHERE revision.source_revision_id = p_source_revision_id
         AND revision.source_id = p_source_id
         AND revision.registry_version = 's4-7b-source-card-v2'
         AND revision.tier = 'PROJECT'
         AND revision.access_level = 'PUBLIC'
         AND revision.initial_processing = 'PROJECT_AUTHORED_CARD'
         AND NOT revision.external_processing_allowed
         AND revision.metadata_hash = p_card_metadata_hash
     ) THEN
    RAISE EXCEPTION 'RAG verified source-card registration is invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.rag_source_card_verifications (
    source_revision_id,
    card_id,
    status,
    card_metadata_hash,
    verified_at
  )
  VALUES (
    p_source_revision_id,
    p_card_id,
    'VERIFIED',
    p_card_metadata_hash,
    p_verified_at
  )
  ON CONFLICT (source_revision_id) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;

  IF inserted_count = 0
     AND NOT EXISTS (
       SELECT 1
       FROM public.rag_source_card_verifications AS verification
       WHERE verification.source_revision_id = p_source_revision_id
         AND verification.card_id = p_card_id
         AND verification.status = 'VERIFIED'
         AND verification.card_metadata_hash = p_card_metadata_hash
         AND verification.verified_at = p_verified_at
     ) THEN
    RAISE EXCEPTION 'RAG verified source-card identity drifted'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_source_public_topics (source_id, public_topic)
  SELECT p_source_id, topic
  FROM unnest(p_public_topics) AS topic
  ORDER BY convert_to(topic, 'UTF8')
  ON CONFLICT DO NOTHING;

  IF (
    SELECT array_agg(topic.public_topic ORDER BY convert_to(topic.public_topic, 'UTF8'))
    FROM public.rag_source_public_topics AS topic
    WHERE topic.source_id = p_source_id
  ) IS DISTINCT FROM (
    SELECT array_agg(topic ORDER BY convert_to(topic, 'UTF8'))
    FROM unnest(p_public_topics) AS topic
  ) THEN
    RAISE EXCEPTION 'RAG verified source-card public topic identity drifted'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_source_exact_identifiers (
    source_id,
    identifier,
    identifier_kind
  )
  SELECT approved.source_id, approved.identifier, approved.identifier_kind
  FROM (
    VALUES
      ('src_project_kis_current_price_snapshot_001', 'FHKST01010100', 'KIS_TR_ID'),
      ('src_project_kis_adjusted_price_001', 'FHKST03010100', 'KIS_TR_ID'),
      ('src_project_gold_futures_etf_132030_001', '132030', 'SYMBOL')
  ) AS approved(source_id, identifier, identifier_kind)
  WHERE approved.source_id = p_source_id
  ON CONFLICT DO NOTHING;
  RETURN inserted_count;
END
$register_rag_verified_source_card$;
ALTER FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  FROM PUBLIC;

CREATE TABLE rag_retrieval_scope_claims (
  scope_claim_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  session_id text NOT NULL,
  allowed_topics text[] NOT NULL,
  active_generation_id text NOT NULL,
  effective_profile_id text NOT NULL,
  policy_version bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  expires_at timestamptz NOT NULL,
  CONSTRAINT rag_retrieval_scope_claims_id_check
    CHECK (scope_claim_id ~ '^rag_scope_[0-9a-f]{32}$'),
  CONSTRAINT rag_retrieval_scope_claims_session_check
    CHECK (
      char_length(session_id) BETWEEN 16 AND 128
      AND session_id ~ '^[A-Za-z0-9._:-]+$'
    ),
  CONSTRAINT rag_retrieval_scope_claims_topic_count_check
    CHECK (cardinality(allowed_topics) BETWEEN 1 AND 6),
  CONSTRAINT rag_retrieval_scope_claims_topic_allowlist_check
    CHECK (
      allowed_topics <@ ARRAY[
        'API',
        'DATA',
        'FINANCIAL_ENGINEERING',
        'METHODOLOGY',
        'PRODUCT_RISK',
        'RISK'
      ]::text[]
    ),
  CONSTRAINT rag_retrieval_scope_claims_profile_fkey
    FOREIGN KEY (active_generation_id, effective_profile_id)
    REFERENCES rag_corpus_generations(corpus_generation_id, embedding_profile_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_retrieval_scope_claims_policy_check CHECK (policy_version > 0),
  CONSTRAINT rag_retrieval_scope_claims_expiry_check
    CHECK (
      expires_at > created_at
      AND expires_at <= created_at + interval '2 minutes'
    ),
  CONSTRAINT rag_retrieval_scope_claims_owner_session_unique
    UNIQUE (owner_user_id, session_id, scope_claim_id)
);
CREATE INDEX rag_retrieval_scope_claims_expiry_idx
  ON rag_retrieval_scope_claims (expires_at, scope_claim_id);

CREATE FUNCTION create_rag_retrieval_scope_claim(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics text[]
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  allowed_topics text[],
  active_generation_id text,
  effective_profile_id text,
  policy_version bigint,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $create_rag_retrieval_scope_claim$
DECLARE
  generated_claim_id text;
  selected_generation_id text;
  selected_profile_id text;
  selected_policy_version bigint;
  selected_expiry timestamptz;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_owner_user_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR p_allowed_topics IS NULL
     OR cardinality(p_allowed_topics) NOT BETWEEN 1 AND 6
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_allowed_topics)))
        <> cardinality(p_allowed_topics)
     OR NOT (
       p_allowed_topics <@ ARRAY[
         'API',
         'DATA',
         'FINANCIAL_ENGINEERING',
         'METHODOLOGY',
         'PRODUCT_RISK',
         'RISK'
       ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG retrieval scope claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    state.active_generation_id,
    state.effective_profile_id,
    state.version
  INTO
    selected_generation_id,
    selected_profile_id,
    selected_policy_version
  FROM public.rag_embedding_policy_state AS state
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = state.active_generation_id
   AND generation.embedding_profile_id = state.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  WHERE state.state_id = 'default'
  FOR SHARE OF state, generation;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG retrieval has no active verified generation'
      USING ERRCODE = '55000';
  END IF;

  generated_claim_id :=
    'rag_scope_' ||
    substr(
      encode(
        digest(
          gen_random_bytes(32) ||
          convert_to(
            concat_ws(
              E'\n',
              p_owner_user_id,
              p_session_id,
              selected_generation_id,
              selected_profile_id,
              selected_policy_version::text,
              clock_timestamp()::text
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );
  selected_expiry := transaction_timestamp() + interval '2 minutes';

  INSERT INTO public.rag_retrieval_scope_claims (
    scope_claim_id,
    owner_user_id,
    session_id,
    allowed_topics,
    active_generation_id,
    effective_profile_id,
    policy_version,
    expires_at
  )
  VALUES (
    generated_claim_id,
    p_owner_user_id,
    p_session_id,
    ARRAY(
      SELECT topic
      FROM unnest(p_allowed_topics) AS topic
      ORDER BY convert_to(topic, 'UTF8')
    ),
    selected_generation_id,
    selected_profile_id,
    selected_policy_version,
    selected_expiry
  );

  RETURN QUERY
  SELECT
    generated_claim_id,
    p_owner_user_id,
    p_session_id,
    ARRAY(
      SELECT topic
      FROM unnest(p_allowed_topics) AS topic
      ORDER BY convert_to(topic, 'UTF8')
    ),
    selected_generation_id,
    selected_profile_id,
    selected_policy_version,
    selected_expiry;
END
$create_rag_retrieval_scope_claim$;
ALTER FUNCTION create_rag_retrieval_scope_claim(text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION create_rag_retrieval_scope_claim(text, text, text[])
  FROM PUBLIC;

-- 아래 세 함수는 같은 helper/view에 권한 검사를 위임하지 않고 channel마다 active pointer,
-- claim, VERIFIED card, PROJECT/PUBLIC, topic, generation/profile을 독립적으로 다시 확인한다.
CREATE FUNCTION search_authorized_rag_exact(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_identifiers text[]
)
RETURNS TABLE (
  rank_no integer,
  chunk_revision_id text,
  source_revision_id text,
  source_id text,
  card_id text,
  title text,
  heading_path text[],
  canonical_content text,
  canonical_content_hash text,
  topic text,
  public_topics text[],
  access_level text,
  tier text,
  source_status text,
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_exact$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_topics IS NULL
     OR cardinality(p_topics) NOT BETWEEN 1 AND 6
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_topics))) <> cardinality(p_topics)
     OR NOT (
       p_topics <@ ARRAY[
         'API', 'DATA', 'FINANCIAL_ENGINEERING', 'METHODOLOGY', 'PRODUCT_RISK', 'RISK'
       ]::text[]
     )
     OR p_identifiers IS NULL
     OR cardinality(p_identifiers) NOT BETWEEN 0 AND 16
     OR EXISTS (
       SELECT 1
       FROM unnest(p_identifiers) AS identifier
       WHERE octet_length(identifier) NOT BETWEEN 1 AND 128
     ) THEN
    RAISE EXCEPTION 'RAG exact retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    row_number() OVER (
      ORDER BY
        CASE
          WHEN source.source_id = ANY(p_identifiers) THEN 0
          WHEN EXISTS (
            SELECT 1
            FROM public.rag_source_exact_identifiers AS exact_identifier
            WHERE exact_identifier.source_id = source.source_id
              AND exact_identifier.identifier = ANY(p_identifiers)
              AND exact_identifier.identifier_kind = 'KIS_TR_ID'
          ) THEN 1
          WHEN EXISTS (
            SELECT 1
            FROM public.rag_source_exact_identifiers AS exact_identifier
            WHERE exact_identifier.source_id = source.source_id
              AND exact_identifier.identifier = ANY(p_identifiers)
              AND exact_identifier.identifier_kind = 'SYMBOL'
          ) THEN 2
          ELSE 3
        END,
        membership.ordinal,
        convert_to(source.source_id, 'UTF8'),
        convert_to(chunk.chunk_revision_id, 'UTF8')
    )::integer,
    chunk.chunk_revision_id,
    revision.source_revision_id,
    source.source_id,
    verification.card_id,
    revision.title,
    chunk.heading_path,
    chunk.canonical_content,
    chunk.canonical_content_hash,
    chunk.topic,
    ARRAY(
      SELECT selected_topic.public_topic
      FROM public.rag_source_public_topics AS selected_topic
      WHERE selected_topic.source_id = source.source_id
      ORDER BY convert_to(selected_topic.public_topic, 'UTF8')
    ),
    chunk.access_level,
    chunk.tier,
    verification.status,
    claim.scope_claim_id,
    claim.owner_user_id,
    claim.session_id,
    claim.active_generation_id,
    claim.effective_profile_id,
    claim.policy_version
  FROM public.rag_retrieval_scope_claims AS claim
  JOIN public.rag_embedding_policy_state AS policy_state
    ON policy_state.state_id = 'default'
   AND policy_state.active_generation_id = claim.active_generation_id
   AND policy_state.effective_profile_id = claim.effective_profile_id
   AND policy_state.version = claim.policy_version
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy_state.active_generation_id
   AND generation.embedding_profile_id = policy_state.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = claim.active_generation_id
   AND membership.embedding_profile_id = claim.effective_profile_id
  JOIN public.rag_chunk_embeddings AS embedding
    ON embedding.corpus_generation_id = membership.corpus_generation_id
   AND embedding.chunk_revision_id = membership.chunk_revision_id
   AND embedding.embedding_profile_id = membership.embedding_profile_id
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = membership.chunk_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = chunk.source_revision_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  WHERE claim.scope_claim_id = p_scope_claim_id
    AND claim.owner_user_id = p_owner_user_id
    AND claim.session_id = p_session_id
    AND claim.expires_at > statement_timestamp()
    AND p_topics <@ claim.allowed_topics
    AND EXISTS (
      SELECT 1
      FROM public.rag_source_public_topics AS topic
      WHERE topic.source_id = source.source_id
        AND topic.public_topic = ANY(claim.allowed_topics)
        AND topic.public_topic = ANY(p_topics)
    )
    AND EXISTS (
      SELECT 1
      FROM unnest(p_identifiers) AS identifier
      WHERE source.source_id = identifier
         OR EXISTS (
           SELECT 1
           FROM public.rag_source_exact_identifiers AS exact_identifier
           WHERE exact_identifier.source_id = source.source_id
             AND exact_identifier.identifier = identifier
         )
         OR strpos(lower(revision.title), lower(identifier)) > 0
         OR strpos(lower(chunk.canonical_content), lower(identifier)) > 0
    )
  ORDER BY
    CASE
      WHEN source.source_id = ANY(p_identifiers) THEN 0
      WHEN EXISTS (
        SELECT 1
        FROM public.rag_source_exact_identifiers AS exact_identifier
        WHERE exact_identifier.source_id = source.source_id
          AND exact_identifier.identifier = ANY(p_identifiers)
          AND exact_identifier.identifier_kind = 'KIS_TR_ID'
      ) THEN 1
      WHEN EXISTS (
        SELECT 1
        FROM public.rag_source_exact_identifiers AS exact_identifier
        WHERE exact_identifier.source_id = source.source_id
          AND exact_identifier.identifier = ANY(p_identifiers)
          AND exact_identifier.identifier_kind = 'SYMBOL'
      ) THEN 2
      ELSE 3
    END,
    membership.ordinal,
    convert_to(source.source_id, 'UTF8'),
    convert_to(chunk.chunk_revision_id, 'UTF8')
  LIMIT 30;
END
$search_authorized_rag_exact$;
ALTER FUNCTION search_authorized_rag_exact(text, text, text, text[], text[])
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_exact(text, text, text, text[], text[])
  FROM PUBLIC;

CREATE FUNCTION search_authorized_rag_lexical(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_query_text text
)
RETURNS TABLE (
  rank_no integer,
  chunk_revision_id text,
  source_revision_id text,
  source_id text,
  card_id text,
  title text,
  heading_path text[],
  canonical_content text,
  canonical_content_hash text,
  topic text,
  public_topics text[],
  access_level text,
  tier text,
  source_status text,
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_lexical$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_topics IS NULL
     OR cardinality(p_topics) NOT BETWEEN 1 AND 6
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_topics))) <> cardinality(p_topics)
     OR NOT (
       p_topics <@ ARRAY[
         'API', 'DATA', 'FINANCIAL_ENGINEERING', 'METHODOLOGY', 'PRODUCT_RISK', 'RISK'
       ]::text[]
     )
     OR p_query_text IS NULL
     OR octet_length(p_query_text) NOT BETWEEN 1 AND 12288 THEN
    RAISE EXCEPTION 'RAG lexical retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    row_number() OVER (
      ORDER BY
        lexical.score DESC,
        membership.ordinal,
        convert_to(source.source_id, 'UTF8'),
        convert_to(chunk.chunk_revision_id, 'UTF8')
    )::integer,
    chunk.chunk_revision_id,
    revision.source_revision_id,
    source.source_id,
    verification.card_id,
    revision.title,
    chunk.heading_path,
    chunk.canonical_content,
    chunk.canonical_content_hash,
    chunk.topic,
    ARRAY(
      SELECT selected_topic.public_topic
      FROM public.rag_source_public_topics AS selected_topic
      WHERE selected_topic.source_id = source.source_id
      ORDER BY convert_to(selected_topic.public_topic, 'UTF8')
    ),
    chunk.access_level,
    chunk.tier,
    verification.status,
    claim.scope_claim_id,
    claim.owner_user_id,
    claim.session_id,
    claim.active_generation_id,
    claim.effective_profile_id,
    claim.policy_version
  FROM public.rag_retrieval_scope_claims AS claim
  JOIN public.rag_embedding_policy_state AS policy_state
    ON policy_state.state_id = 'default'
   AND policy_state.active_generation_id = claim.active_generation_id
   AND policy_state.effective_profile_id = claim.effective_profile_id
   AND policy_state.version = claim.policy_version
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy_state.active_generation_id
   AND generation.embedding_profile_id = policy_state.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = claim.active_generation_id
   AND membership.embedding_profile_id = claim.effective_profile_id
  JOIN public.rag_chunk_embeddings AS embedding
    ON embedding.corpus_generation_id = membership.corpus_generation_id
   AND embedding.chunk_revision_id = membership.chunk_revision_id
   AND embedding.embedding_profile_id = membership.embedding_profile_id
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = membership.chunk_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = chunk.source_revision_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  CROSS JOIN LATERAL (
    SELECT similarity(
      lower(
        concat_ws(
          ' ',
          source.source_id,
          revision.title,
          source.topic,
          chunk.canonical_content
        )
      ),
      lower(p_query_text)
    ) AS score
  ) AS lexical
  WHERE claim.scope_claim_id = p_scope_claim_id
    AND claim.owner_user_id = p_owner_user_id
    AND claim.session_id = p_session_id
    AND claim.expires_at > statement_timestamp()
    AND p_topics <@ claim.allowed_topics
    AND EXISTS (
      SELECT 1
      FROM public.rag_source_public_topics AS topic
      WHERE topic.source_id = source.source_id
        AND topic.public_topic = ANY(claim.allowed_topics)
        AND topic.public_topic = ANY(p_topics)
    )
    AND lexical.score >= 0.05
  ORDER BY
    lexical.score DESC,
    membership.ordinal,
    convert_to(source.source_id, 'UTF8'),
    convert_to(chunk.chunk_revision_id, 'UTF8')
  LIMIT 30;
END
$search_authorized_rag_lexical$;
ALTER FUNCTION search_authorized_rag_lexical(text, text, text, text[], text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_lexical(text, text, text, text[], text)
  FROM PUBLIC;

-- 기존 lexical GIN의 `gin_trgm_ops`와 아래 pgvector `<=>`/`vector_cosine_ops` 의미를
-- 맞춘다. exact 30 benchmark는 이득 없는 ANN index를 강제하지 않고 sequential scan을 유지한다.
CREATE FUNCTION search_authorized_rag_dense(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_query_embedding vector(1024)
)
RETURNS TABLE (
  rank_no integer,
  chunk_revision_id text,
  source_revision_id text,
  source_id text,
  card_id text,
  title text,
  heading_path text[],
  canonical_content text,
  canonical_content_hash text,
  topic text,
  public_topics text[],
  access_level text,
  tier text,
  source_status text,
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_dense$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_topics IS NULL
     OR cardinality(p_topics) NOT BETWEEN 1 AND 6
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_topics))) <> cardinality(p_topics)
     OR NOT (
       p_topics <@ ARRAY[
         'API', 'DATA', 'FINANCIAL_ENGINEERING', 'METHODOLOGY', 'PRODUCT_RISK', 'RISK'
       ]::text[]
     )
     OR p_query_embedding IS NULL
     OR vector_dims(p_query_embedding) <> 1024
     OR vector_norm(p_query_embedding)::text IN ('NaN', 'Infinity', '-Infinity')
     OR abs(vector_norm(p_query_embedding)::double precision - 1.0) > 0.00001 THEN
    RAISE EXCEPTION 'RAG dense retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    row_number() OVER (
      ORDER BY
        embedding.embedding <=> p_query_embedding,
        membership.ordinal,
        convert_to(source.source_id, 'UTF8'),
        convert_to(chunk.chunk_revision_id, 'UTF8')
    )::integer,
    chunk.chunk_revision_id,
    revision.source_revision_id,
    source.source_id,
    verification.card_id,
    revision.title,
    chunk.heading_path,
    chunk.canonical_content,
    chunk.canonical_content_hash,
    chunk.topic,
    ARRAY(
      SELECT selected_topic.public_topic
      FROM public.rag_source_public_topics AS selected_topic
      WHERE selected_topic.source_id = source.source_id
      ORDER BY convert_to(selected_topic.public_topic, 'UTF8')
    ),
    chunk.access_level,
    chunk.tier,
    verification.status,
    claim.scope_claim_id,
    claim.owner_user_id,
    claim.session_id,
    claim.active_generation_id,
    claim.effective_profile_id,
    claim.policy_version
  FROM public.rag_retrieval_scope_claims AS claim
  JOIN public.rag_embedding_policy_state AS policy_state
    ON policy_state.state_id = 'default'
   AND policy_state.active_generation_id = claim.active_generation_id
   AND policy_state.effective_profile_id = claim.effective_profile_id
   AND policy_state.version = claim.policy_version
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy_state.active_generation_id
   AND generation.embedding_profile_id = policy_state.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = claim.active_generation_id
   AND membership.embedding_profile_id = claim.effective_profile_id
  JOIN public.rag_chunk_embeddings AS embedding
    ON embedding.corpus_generation_id = membership.corpus_generation_id
   AND embedding.chunk_revision_id = membership.chunk_revision_id
   AND embedding.embedding_profile_id = membership.embedding_profile_id
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = membership.chunk_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = chunk.source_revision_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  WHERE claim.scope_claim_id = p_scope_claim_id
    AND claim.owner_user_id = p_owner_user_id
    AND claim.session_id = p_session_id
    AND claim.expires_at > statement_timestamp()
    AND p_topics <@ claim.allowed_topics
    AND EXISTS (
      SELECT 1
      FROM public.rag_source_public_topics AS topic
      WHERE topic.source_id = source.source_id
        AND topic.public_topic = ANY(claim.allowed_topics)
        AND topic.public_topic = ANY(p_topics)
    )
    AND embedding.embedding <=> p_query_embedding <= 0.55
  ORDER BY
    embedding.embedding <=> p_query_embedding,
    membership.ordinal,
    convert_to(source.source_id, 'UTF8'),
    convert_to(chunk.chunk_revision_id, 'UTF8')
  LIMIT 30;
END
$search_authorized_rag_dense$;
ALTER FUNCTION search_authorized_rag_dense(text, text, text, text[], vector)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_dense(text, text, text, text[], vector)
  FROM PUBLIC;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

DO $s4_3_authorized_retrieval_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_source_card_verifications,
      rag_source_public_topics,
      rag_source_exact_identifiers,
      rag_retrieval_scope_claims
    FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    GRANT EXECUTE
      ON FUNCTION create_rag_retrieval_scope_claim(text, text, text[])
      TO decision_app;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_source_card_verifications,
      rag_source_public_topics,
      rag_source_exact_identifiers,
      rag_retrieval_scope_claims
    FROM decision_rag_writer;
    GRANT EXECUTE
      ON FUNCTION register_rag_verified_source_card(
        text, text, text, text, timestamptz, text[]
      )
      TO decision_rag_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_query;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_query;
    REVOKE CREATE ON SCHEMA public FROM decision_rag_query;
    GRANT EXECUTE
      ON FUNCTION search_authorized_rag_exact(text, text, text, text[], text[])
      TO decision_rag_query;
    GRANT EXECUTE
      ON FUNCTION search_authorized_rag_lexical(text, text, text, text[], text)
      TO decision_rag_query;
    GRANT EXECUTE
      ON FUNCTION search_authorized_rag_dense(text, text, text, text[], vector)
      TO decision_rag_query;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_source_card_verifications,
      rag_source_public_topics,
      rag_source_exact_identifiers,
      rag_retrieval_scope_claims
    FROM decision_rag_admin;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_worker') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_source_card_verifications,
      rag_source_public_topics,
      rag_source_exact_identifiers,
      rag_retrieval_scope_claims
    FROM decision_worker;
  END IF;
END
$s4_3_authorized_retrieval_acl$;

ALTER FUNCTION create_rag_retrieval_scope_claim(text, text, text[]) OWNER TO flyway;
ALTER FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  OWNER TO flyway;
ALTER FUNCTION search_authorized_rag_exact(text, text, text, text[], text[]) OWNER TO flyway;
ALTER FUNCTION search_authorized_rag_lexical(text, text, text, text[], text) OWNER TO flyway;
ALTER FUNCTION search_authorized_rag_dense(text, text, text, text[], vector) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION create_rag_retrieval_scope_claim(text, text, text[])
  FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_exact(text, text, text, text[], text[])
  FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_lexical(text, text, text, text[], text)
  FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION search_authorized_rag_dense(text, text, text, text[], vector)
  FROM PUBLIC;
GRANT EXECUTE
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  TO decision_rag_writer;
GRANT EXECUTE
  ON FUNCTION create_rag_retrieval_scope_claim(text, text, text[])
  TO decision_app;
GRANT EXECUTE
  ON FUNCTION search_authorized_rag_exact(text, text, text, text[], text[])
  TO decision_rag_query;
GRANT EXECUTE
  ON FUNCTION search_authorized_rag_lexical(text, text, text, text[], text)
  TO decision_rag_query;
GRANT EXECUTE
  ON FUNCTION search_authorized_rag_dense(text, text, text, text[], vector)
  TO decision_rag_query;
