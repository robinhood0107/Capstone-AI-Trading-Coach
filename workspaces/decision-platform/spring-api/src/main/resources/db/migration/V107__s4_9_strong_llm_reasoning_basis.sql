-- 답에 추론 문장을 허용한다.
--
-- EVIDENCE는 모든 문장에 정확 인용을 요구해서 모델이 근거를 잇거나 비교하거나 한계를 말하는
-- 문장을 쓸 수 없었다. 그래서 답이 인용의 나열이 되고, Strong LLM을 쓰는 이유가 사라진다.
-- EVIDENCE_WITH_REASONING은 그 문장을 허용하되 검증 가능한 성질을 지킨다 - 추론 문장은 인용을
-- 갖지 않고, 시점을 주장하지 않으며, 같은 답의 근거 문장이 이미 인용으로 증명한 숫자만 다시
-- 쓸 수 있다. 즉 새 사실은 여전히 근거에서만 나온다. 그 검증은 Kotlin validator가 하고,
-- 여기서는 그 결과가 원장에 그대로 남을 수 있게 저장 경계를 넓힌다.
--
-- 원장이 basis를 EVIDENCE로 뭉뚱그리면 "인용 없는 문장이 섞인 답"과 "전부 인용된 답"을 사후에
-- 구분할 수 없다. 그러면 이 완화를 받아들일 근거였던 감사 가능성이 사라진다.

ALTER TABLE public.s4_9_strong_llm_usage_ledger
  DROP CONSTRAINT IF EXISTS s4_9_strong_llm_usage_ledger_answer_basis_check;
ALTER TABLE public.s4_9_strong_llm_usage_ledger
  ADD CONSTRAINT s4_9_strong_llm_usage_ledger_answer_basis_check CHECK (
    answer_basis IN ('EVIDENCE','EVIDENCE_WITH_REASONING','MODEL_KNOWLEDGE','INSUFFICIENT_EVIDENCE')
  );

-- 본문은 V70과 같고 basis 허용 집합과 인용 비율 하한, 표식 검사만 다르다.
CREATE OR REPLACE FUNCTION public.persist_s4_9_strong_llm_history_v2(
  p_owner_user_id text, p_answer_id text, p_request_id text, p_answer_mode text,
  p_session_id text, p_scope_claim_id text, p_answer_basis text,
  p_citation_coverage double precision, p_guardrail_flags text[], p_kek_version text,
  p_wrap_nonce bytea, p_wrapped_dek bytea, p_wrap_tag bytea,
  p_question_nonce bytea, p_question_ciphertext bytea, p_question_tag bytea,
  p_answer_nonce bytea, p_answer_ciphertext bytea, p_answer_tag bytea,
  p_created_at timestamptz, p_citations jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $persist_s4_9_strong_llm_history_v2$
DECLARE
  canonical_citations jsonb := '[]'::jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_answer_mode NOT IN ('CONCISE','DETAILED')
     OR p_answer_basis NOT IN ('EVIDENCE','EVIDENCE_WITH_REASONING','MODEL_KNOWLEDGE')
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$'
     OR octet_length(p_wrap_nonce) <> 12 OR octet_length(p_wrapped_dek) <> 32
     OR octet_length(p_wrap_tag) <> 16 OR octet_length(p_question_nonce) <> 12
     OR octet_length(p_question_ciphertext) NOT BETWEEN 1 AND 8192 OR octet_length(p_question_tag) <> 16
     OR octet_length(p_answer_nonce) <> 12 OR octet_length(p_answer_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_answer_tag) <> 16
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds'
       AND transaction_timestamp() + interval '60 seconds' THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 history arguments are invalid' USING ERRCODE = '22023';
  END IF;
  IF p_answer_basis IN ('EVIDENCE','EVIDENCE_WITH_REASONING') THEN
    -- 추론 문장이 섞이면 모든 문장이 인용을 갖지는 않는다. 그래서 인용 비율 하한이 낮다.
    -- 인용이 하나도 없는 답은 validator가 이 basis로 통과시키지 않으므로 0은 여전히 아니다.
    -- plpgsql은 IF 조건을 첫 THEN에서 끊는다. 여기에 CASE를 쓰면 그 THEN이 조건을 잘라
    -- 함수 정의 자체가 파싱되지 않는다. 그래서 basis별 비교를 나란히 쓴다.
    IF (p_answer_basis = 'EVIDENCE' AND p_citation_coverage < 0.8)
       OR (p_answer_basis = 'EVIDENCE_WITH_REASONING' AND p_citation_coverage < 0.2)
       OR coalesce(cardinality(p_guardrail_flags), 0) > 7
       OR NOT coalesce(p_guardrail_flags, ARRAY[]::text[]) <@ ARRAY[
         'SINGLE_SOURCE','STALE_SOURCE','CONFLICTING_SOURCES','LOW_RELEVANCE',
         'SECONDARY_SOURCE','GOOGLE_GROUNDING_ONLY','REASONING_SENTENCES_PRESENT'
       ]::text[]
       -- 이 표식이 없으면 화면도 감사도 어느 문장이 추론인지 알 수 없다. 그래서 필수다.
       OR (p_answer_basis = 'EVIDENCE_WITH_REASONING'
           AND NOT 'REASONING_SENTENCES_PRESENT' = ANY(coalesce(p_guardrail_flags, ARRAY[]::text[])))
       OR (p_answer_basis = 'EVIDENCE'
           AND 'REASONING_SENTENCES_PRESENT' = ANY(coalesce(p_guardrail_flags, ARRAY[]::text[]))) THEN
      RAISE EXCEPTION 'S4.9 v2 evidence history arguments are invalid' USING ERRCODE = '22023';
    END IF;
    canonical_citations := public.canonicalize_s4_9_strong_llm_citations_v2(
      p_owner_user_id, p_request_id, p_session_id, p_scope_claim_id, p_citations
    );
  ELSE
    IF p_citation_coverage <> 0.0 OR p_guardrail_flags <> ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]
       OR p_citations <> '[]'::jsonb THEN
      RAISE EXCEPTION 'S4.9 v2 model knowledge arguments are invalid' USING ERRCODE = '22023';
    END IF;
  END IF;

  SELECT * INTO claim_row FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 scope disappeared' USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_answer_history(
    answer_id,owner_user_id,request_id,answer_mode,generation_status,citation_coverage,
    retrieval_failure,guardrail_flags,public_corpus_version,private_overlay_state,kek_version,
    wrap_nonce,wrapped_dek,wrap_tag,question_nonce,question_ciphertext,question_tag,
    answer_nonce,answer_ciphertext,answer_tag,citation_count,created_at,expires_at
  ) VALUES (
    p_answer_id,p_owner_user_id,p_request_id,p_answer_mode,'ANSWERED',p_citation_coverage,
    false,p_guardrail_flags,'immutable-v2-' || claim_row.public_pointer_version::text,
    CASE WHEN claim_row.owner_private_generation_id IS NULL THEN 'ABSENT' ELSE 'READY' END,
    p_kek_version,p_wrap_nonce,p_wrapped_dek,p_wrap_tag,p_question_nonce,p_question_ciphertext,
    p_question_tag,p_answer_nonce,p_answer_ciphertext,p_answer_tag,jsonb_array_length(canonical_citations),
    p_created_at,p_created_at + interval '30 days'
  );
  INSERT INTO public.rag_v2_answer_citations(
    answer_id,owner_user_id,ordinal,citation_kind,source_id,title,canonical_url,
    document_id,sanitized_display_name,locator
  )
  SELECT p_answer_id,p_owner_user_id,ordinal::integer,citation.value ->> 'citationKind',
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'sourceId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'title' END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'canonicalUrl' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'documentId' END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'displayName' END,
    citation.value -> 'locator'
  FROM jsonb_array_elements(canonical_citations) WITH ORDINALITY AS citation(value,ordinal)
  ORDER BY ordinal;
  RETURN canonical_citations;
END
$persist_s4_9_strong_llm_history_v2$;

CREATE OR REPLACE FUNCTION public.record_s4_9_strong_llm_usage_v2(
  p_usage_event_id text, p_owner_user_id text, p_request_id text, p_model_id text,
  p_answer_basis text, p_outcome text, p_tool_round_count integer,
  p_search_call_count integer, p_read_call_count integer,
  p_prompt_token_count integer, p_output_token_count integer, p_evidence_set_sha256 text,
  p_vertex_generate_call_count integer, p_google_grounding_query_count integer,
  p_search_backend text, p_evidence_validation_mode text, p_failure_leaf text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_s4_9_strong_llm_usage_v2$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^s49_llu_[0-9a-f]{32}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_model_id !~ '^[a-z][a-z0-9.-]{2,127}$'
     OR p_outcome NOT IN ('COMMITTED','REJECTED','UNKNOWN_BILLING')
     OR p_tool_round_count NOT BETWEEN 0 AND 3
     OR p_search_call_count NOT BETWEEN 0 AND 3 OR p_read_call_count NOT BETWEEN 0 AND 8
     OR p_vertex_generate_call_count NOT BETWEEN 0 AND 4
     OR p_google_grounding_query_count NOT BETWEEN 0 AND 128
     OR p_search_backend NOT IN ('NONE','VERTEX_GOOGLE','SEARXNG')
     OR p_evidence_validation_mode NOT IN ('CANONICAL_EXACT','GOOGLE_GROUNDING','MIXED','NONE')
     OR p_evidence_set_sha256 !~ '^[0-9a-f]{64}$'
     OR (p_failure_leaf IS NOT NULL AND p_failure_leaf !~ '^[A-Z0-9_]{3,96}$') THEN
    RAISE EXCEPTION 'S4.9 Strong LLM v2 usage is invalid' USING ERRCODE = '22023';
  END IF;
  IF p_outcome = 'COMMITTED' THEN
    IF p_answer_basis NOT IN ('EVIDENCE','EVIDENCE_WITH_REASONING','MODEL_KNOWLEDGE','INSUFFICIENT_EVIDENCE')
       OR p_prompt_token_count NOT BETWEEN 0 AND 500000 OR p_output_token_count NOT BETWEEN 0 AND 100000
       OR p_failure_leaf IS NOT NULL THEN
      RAISE EXCEPTION 'S4.9 committed v2 usage is invalid' USING ERRCODE = '22023';
    END IF;
  ELSE
    IF p_answer_basis IS NOT NULL OR p_prompt_token_count IS NOT NULL OR p_output_token_count IS NOT NULL
       OR p_failure_leaf IS NULL THEN
      RAISE EXCEPTION 'S4.9 failed v2 usage is invalid' USING ERRCODE = '22023';
    END IF;
  END IF;
  INSERT INTO public.s4_9_strong_llm_usage_ledger(
    usage_event_id, owner_user_id, request_id, provider, model_id, answer_basis, outcome,
    tool_round_count, search_call_count, read_call_count, prompt_token_count, output_token_count,
    evidence_set_sha256, usage_schema_version, vertex_generate_call_count,
    google_grounding_query_count, search_backend, evidence_validation_mode, failure_leaf
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, 'VERTEX_AI', p_model_id, p_answer_basis, p_outcome,
    p_tool_round_count, p_search_call_count, p_read_call_count, p_prompt_token_count, p_output_token_count,
    p_evidence_set_sha256, 2, p_vertex_generate_call_count,
    p_google_grounding_query_count, p_search_backend, p_evidence_validation_mode, p_failure_leaf
  );
END
$record_s4_9_strong_llm_usage_v2$;
