-- 인용률 하한을 저장 경계에서 제거해 앱 계약과 맞춘다.
--
-- 무엇이 어긋났나. 커밋 12593078(rag-always-answer)이 앱 계층에서 인용률 하한
-- (EVIDENCE 0.8, EVIDENCE_WITH_REASONING 0.2)을 제거했다. 연결률은 답을 통과시킬 문턱이
-- 아니라 화면이 보여 주는 지표라는 판단이었다. 그런데 저장 경계는 두 곳에서 그 하한을
-- 그대로 들고 있었다 - V107 함수와 V129 테이블 CHECK. 그래서 하한 밑의 답은 생성과 과금까지
-- 끝난 뒤 저장에서만 거부됐고, RagV2Controller 의 광범위 handler 가 그것을 503
-- RAG_UNAVAILABLE 하나로 접어 "설명 근거 저장소를 지금 사용할 수 없습니다"로 보여 줬다.
-- 실제로는 저장소가 살아 있었고 계약이 어긋나 있었다.
--
-- 왜 둘을 함께 바꾸는가. 함수만 고치면 INSERT 가 테이블 CHECK 에서 다시 막힌다. 두 방어층이
-- 같은 규칙을 표현하므로 같은 마이그레이션에서 함께 옮긴다.
--
-- 무엇을 유지하는가. 인용이 붙을 때 그 인용이 진짜인지 확인하는 규칙은 그대로 둔다 -
-- basis 열거값, 인용 1..5개, 플래그 어휘 subset, REASONING_SENTENCES_PRESENT 표식 정합성,
-- MODEL_KNOWLEDGE 분기의 정확한 모양, owner/scope 경계, canonicalize 의 quote·숫자 결속.
-- 근거 위조 방어와 인용률 하한은 성격이 다르다. 하한만 뺀다.
--
-- 무엇을 새로 막는가. 하한이 없어지면 범위 자체를 검사하는 곳이 사라지므로
-- p_citation_coverage 를 0.0..1.0 으로 명시 검증한다. 앱도 같은 범위를 require 한다.

ALTER TABLE public.rag_v2_answer_history
  DROP CONSTRAINT rag_v2_answer_history_status_result_check;
ALTER TABLE public.rag_v2_answer_history
  ADD CONSTRAINT rag_v2_answer_history_status_result_check CHECK (
    (
      generation_status = 'ANSWERED'
      AND NOT retrieval_failure
      AND (
        (
          -- 인용이 하나라도 붙은 답. 연결률은 범위만 본다 - 표식과 basis 의 정합성은
          -- persist 함수가 강제하고, 테이블은 저장된 행의 모양만 지킨다.
          citation_count BETWEEN 1 AND 5
          AND citation_coverage BETWEEN 0.0 AND 1.0
        )
        OR (
          citation_count = 0
          AND citation_coverage = 0.0
          AND guardrail_flags = ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]
        )
      )
    )
    OR (
      generation_status = 'RETRIEVAL_ONLY'
      AND citation_count BETWEEN 0 AND 5
      AND NOT retrieval_failure
    )
    OR (
      generation_status = 'RETRIEVAL_FAILURE'
      AND citation_count = 0
      AND citation_coverage = 0.0
      AND retrieval_failure
    )
  );

-- 본문은 V107과 같고 인용률 하한 두 줄만 범위 검사로 바뀐다.
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
    -- 연결률은 이제 문턱이 아니라 지표다. 하한을 두면 근거가 얇을 때 설명까지 함께 사라지고
    -- 사용자는 낮은 연결률을 보고 스스로 판단할 기회조차 얻지 못한다. 범위만 검사한다.
    -- plpgsql은 IF 조건을 첫 THEN에서 끊으므로 CASE를 쓰지 않고 비교를 나란히 쓴다.
    IF p_citation_coverage IS NULL
       OR p_citation_coverage NOT BETWEEN 0.0 AND 1.0
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
