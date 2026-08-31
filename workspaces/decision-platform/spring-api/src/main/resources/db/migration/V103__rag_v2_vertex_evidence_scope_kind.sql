-- Vertex 생성형 답변은 켜도 언제나 `GENERATION_UNAVAILABLE`이었다. 원인은 증거 판독 한 곳이다.
--
-- `read_rag_v2_vertex_generation_evidence`(wrapper)는 `RAG_SCOPE` target으로 열린 actor scope를
-- 확인하고 넘긴다. 그런데 그 안의 `_legacy_v87`은 `app.required_actor_target_kind`를 `RAG_ANSWER`로
-- 선언한다. `actor_rls_scope_is_open_v1()`이 그 값을 열린 scope의 target_kind와 대조하므로, 두
-- 값이 어긋나면 함수 안에서 scope가 닫힌 것으로 판정되고 `app.actor_user_id`가 빈 값이 된다.
-- 그러면 자기 인자 가드가 22023으로 닫히고, 호출자에게는 이유 없는 GENERATION_UNAVAILABLE만 남는다.
--
-- 이 판독이 다루는 대상은 답변이 아니라 검색 scope다. 답변 ID는 생성이 끝나야 생기므로 이 시점에
-- 존재하지도 않는다. 형제 함수 `read_rag_v2_vertex_prepared_scope_v2`도 `RAG_SCOPE`를 요구한다.
-- 그래서 어긋난 쪽을 함수 선언으로 보고 `RAG_SCOPE`로 맞춘다. 본문과 권한은 그대로다.

CREATE OR REPLACE FUNCTION public.read_rag_v2_vertex_generation_evidence_legacy_v87(p_owner_user_id text, p_session_id text, p_scope_claim_id text, p_citations jsonb)
 RETURNS TABLE(ordinal integer, citation_id text, chunk_revision_id text, canonical_content text, canonical_content_sha256 text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_catalog', 'pg_temp'
 SET "app.required_actor_operation" TO 'READ_VERTEX_EVIDENCE'
 SET "app.required_actor_target_kind" TO 'RAG_SCOPE'
AS $function$
#variable_conflict use_column
DECLARE
  canonical_citations jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  citation_item jsonb;
  candidate record;
  output_ordinal integer := 0;
  total_content_bytes integer := 0;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR jsonb_typeof(p_citations) <> 'array'
     OR jsonb_array_length(p_citations) NOT BETWEEN 1 AND 5
     OR octet_length(p_citations::text) > 16384 THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  canonical_citations := public.canonicalize_rag_v2_immutable_retrieval_citations(
    p_owner_user_id,
    p_session_id,
    p_scope_claim_id,
    p_citations
  );
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence scope disappeared'
      USING ERRCODE = '55000';
  END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(canonical_citations)
  LOOP
    SELECT
      chunk.canonical_text,
      chunk.canonical_text_sha256
    INTO candidate
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_chunks AS chunk
      ON chunk.chunk_id = membership.chunk_id
     AND chunk.source_revision_id = membership.source_revision_id
     AND chunk.source_scope = membership.component_scope
     AND chunk.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
     AND source.source_scope = membership.component_scope
     AND source.owner_partition_key = membership.owner_partition_key
    WHERE membership.component_generation_id = citation_item ->> 'generationId'
      AND membership.chunk_id = citation_item ->> 'chunkRevisionId'
      AND membership.source_revision_id = citation_item ->> 'sourceRevisionId'
      AND source.source_id = citation_item ->> 'sourceId'
      AND membership.component_generation_id = ANY(
        ARRAY[
          claim_row.exact30_generation_id,
          claim_row.oa112_generation_id,
          claim_row.owner_private_generation_id
        ]::text[]
      )
      AND source.retrieval_topics && claim_row.allowed_topics
      AND source.external_processing_eligible
      AND (
        (source.source_scope IN ('EXACT30', 'OA112') AND source.owner_user_id IS NULL)
        OR (source.source_scope = 'OWNER_PRIVATE' AND source.owner_user_id = p_owner_user_id)
      )
      AND EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_generation_embeddings AS embedding
        WHERE embedding.component_generation_id = membership.component_generation_id
          AND embedding.chunk_id = membership.chunk_id
          AND embedding.component_scope = membership.component_scope
          AND embedding.owner_partition_key = membership.owner_partition_key
          AND embedding.embedding_profile_id = CASE
            WHEN membership.component_scope = 'OWNER_PRIVATE' THEN claim_row.owner_embedding_profile_id
            ELSE claim_row.embedding_profile_id
          END
      );
    IF NOT FOUND
       OR candidate.canonical_text IS NULL
       OR candidate.canonical_text_sha256 !~ '^[0-9a-f]{64}$'
       OR octet_length(candidate.canonical_text) NOT BETWEEN 1 AND 16384 THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex evidence is not externally eligible'
        USING ERRCODE = '55000';
    END IF;

    total_content_bytes := total_content_bytes + octet_length(candidate.canonical_text);
    IF total_content_bytes > 60000 THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex evidence exceeds the bounded input cap'
        USING ERRCODE = '22023';
    END IF;
    output_ordinal := output_ordinal + 1;
    ordinal := output_ordinal;
    citation_id := citation_item ->> 'citationId';
    chunk_revision_id := citation_item ->> 'chunkRevisionId';
    canonical_content := candidate.canonical_text;
    canonical_content_sha256 := candidate.canonical_text_sha256;
    RETURN NEXT;
  END LOOP;
  IF output_ordinal NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence is empty'
      USING ERRCODE = '55000';
  END IF;
END
$function$
