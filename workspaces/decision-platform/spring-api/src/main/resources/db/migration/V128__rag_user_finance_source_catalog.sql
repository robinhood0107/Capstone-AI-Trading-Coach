CREATE TABLE public.rag_user_finance_source_catalog (
  source_id text PRIMARY KEY,
  display_order smallint NOT NULL UNIQUE CHECK (display_order BETWEEN 1 AND 30),
  display_category text NOT NULL CHECK (char_length(display_category) BETWEEN 1 AND 40),
  display_institution text NOT NULL CHECK (char_length(display_institution) BETWEEN 1 AND 80),
  display_attribution text NOT NULL CHECK (char_length(display_attribution) BETWEEN 1 AND 120)
);

ALTER TABLE public.rag_user_finance_source_catalog OWNER TO flyway;
REVOKE ALL PRIVILEGES ON TABLE public.rag_user_finance_source_catalog FROM PUBLIC;

INSERT INTO public.rag_user_finance_source_catalog (
  source_id,
  display_order,
  display_category,
  display_institution,
  display_attribution
) VALUES
  ('src_project_gold_futures_etf_132030_001', 1, 'ETF 상품 이해', '삼성자산운용', '공식 상품 정보와 프로젝트 검증 카드'),
  ('src_project_krx_etf_etn_structure_001', 2, 'ETF·ETN 위험', '한국거래소', '공식 시장 안내와 프로젝트 검증 카드'),
  ('src_project_sharpe_drawdown_partial_metrics_001', 3, '성과와 위험', '학술 논문', '동료평가 논문과 프로젝트 검증 카드'),
  ('src_project_var_es_coherence_001', 4, '꼬리 위험', '학술 논문', '동료평가 논문과 프로젝트 검증 카드'),
  ('src_project_threshold_cvar_not_exact_es_001', 5, '꼬리 위험', '학술 논문', '동료평가 논문과 프로젝트 검증 카드'),
  ('src_project_backtest_overfitting_001', 6, '백테스트 검증', '학술 논문', '동료평가 논문과 프로젝트 검증 카드'),
  ('src_project_mean_reversion_stationarity_001', 7, '시계열 해석', '학술 논문', '동료평가 논문과 프로젝트 검증 카드'),
  ('src_project_monte_carlo_not_stress_probability_001', 8, '확률과 스트레스', '학술 논문', '동료평가 논문과 프로젝트 검증 카드');

CREATE OR REPLACE FUNCTION public.read_rag_source_registry(p_actor_user_id text)
RETURNS TABLE(source_id text, title text, institution text, topic text,
              attribution text, canonical_url text, last_checked_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path TO 'public', 'pg_catalog', 'pg_temp'
AS $read_rag_source_registry_v128$
BEGIN
  IF p_actor_user_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_actor_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.users actor
       WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG source projection actor mismatch' USING ERRCODE='42501';
  END IF;

  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);

  RETURN QUERY
  WITH curated AS (
    SELECT DISTINCT ON (catalog.display_order)
      source.source_id,
      source.citation_title AS title,
      catalog.display_institution AS institution,
      catalog.display_category AS topic,
      catalog.display_attribution AS attribution,
      source.canonical_https_url AS canonical_url,
      NULL::timestamptz AS last_checked_at,
      catalog.display_order
    FROM public.rag_user_finance_source_catalog catalog
    JOIN public.rag_v2_immutable_source_revisions source
      ON source.source_id=catalog.source_id
     AND source.owner_user_id IS NULL
     AND source.source_scope='EXACT30'
    JOIN public.rag_v2_immutable_public_bundle_pointers pointer
      ON pointer.state_id='default'
     AND pointer.state='ACTIVE'
    JOIN public.rag_v2_immutable_generation_memberships membership
      ON membership.component_generation_id=pointer.exact30_generation_id
     AND membership.source_revision_id=source.source_revision_id
     AND membership.component_scope='EXACT30'
     AND membership.owner_user_id IS NULL
    WHERE source.citation_title IS NOT NULL
      AND source.canonical_https_url IS NOT NULL
      AND NOT ('API'=ANY(source.retrieval_topics))
    ORDER BY catalog.display_order,source.created_at DESC,source.source_revision_id DESC
  ), indexed AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id,revision.title,source.institution,source.topic,
      revision.attribution,revision.canonical_url,latest_check.checked_at
    FROM public.rag_embedding_policy_state policy_state
    JOIN public.rag_corpus_generations generation
      ON generation.corpus_generation_id=policy_state.active_generation_id
     AND generation.embedding_profile_id=policy_state.effective_profile_id
     AND generation.status='ACTIVE' AND generation.evaluation_status='PASSED'
    JOIN public.rag_generation_chunks membership
      ON membership.corpus_generation_id=generation.corpus_generation_id
     AND membership.embedding_profile_id=generation.embedding_profile_id
    JOIN public.rag_chunk_revisions chunk
      ON chunk.chunk_revision_id=membership.chunk_revision_id AND chunk.access_level='PUBLIC'
    JOIN public.rag_source_revisions revision
      ON revision.source_revision_id=chunk.source_revision_id AND revision.access_level='PUBLIC'
    JOIN public.rag_sources source
      ON source.source_id=revision.source_id
     AND source.source_type='PROJECT_SOURCE_CARD'
     AND source.retired_at IS NULL
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at FROM public.rag_source_checks source_check
      WHERE source_check.source_revision_id=revision.source_revision_id
      ORDER BY source_check.checked_at DESC,source_check.source_check_id DESC LIMIT 1
    ) latest_check ON true
    ORDER BY source.source_id,revision.revision_seq DESC
  ), registered AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id,revision.title,source.institution,source.topic,
      revision.attribution,revision.canonical_url,latest_check.checked_at
    FROM public.rag_sources source
    JOIN public.rag_source_revisions revision
      ON revision.source_id=source.source_id AND revision.access_level='PUBLIC'
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at FROM public.rag_source_checks source_check
      WHERE source_check.source_revision_id=revision.source_revision_id
      ORDER BY source_check.checked_at DESC,source_check.source_check_id DESC LIMIT 1
    ) latest_check ON true
    WHERE source.source_type='PROJECT_SOURCE_CARD' AND source.retired_at IS NULL
    ORDER BY source.source_id,revision.revision_seq DESC
  ), fallback AS (
    SELECT * FROM indexed
    UNION ALL SELECT * FROM registered WHERE NOT EXISTS (SELECT 1 FROM indexed)
  )
  SELECT item.source_id,item.title,item.institution,item.topic,item.attribution,
         item.canonical_url,item.last_checked_at
  FROM (
    SELECT curated.source_id,curated.title,curated.institution,curated.topic,
           curated.attribution,curated.canonical_url,curated.last_checked_at,
           curated.display_order
    FROM curated
    UNION ALL
    SELECT fallback.source_id,fallback.title,fallback.institution,fallback.topic,
           fallback.attribution,fallback.canonical_url,fallback.checked_at,
           row_number() OVER (ORDER BY fallback.source_id)::smallint
    FROM fallback WHERE NOT EXISTS (SELECT 1 FROM curated)
  ) item
  ORDER BY item.display_order
  LIMIT 8;
END
$read_rag_source_registry_v128$;
