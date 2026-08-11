-- public component의 Python canonical text는 chunk 사이를 실제 LF 두 개로 결합한다.
-- V36/V45의 escape literal은 백슬래시 문자를 결합해 다중 청크 source만 manifest 밖으로 밀어냈다.

CREATE OR REPLACE FUNCTION rag_v2_immutable_public_bge_source_member_digest(
  p_source_revision_id text,
  p_component_scope text
)
RETURNS text
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_public_bge_source_member_digest$
DECLARE
  source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  encoded_chunks text;
  joined_canonical_text text;
BEGIN
  SELECT * INTO source_record
  FROM public.rag_v2_immutable_source_revisions
  WHERE source_revision_id = p_source_revision_id
    AND source_scope = p_component_scope
    AND owner_user_id IS NULL;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  SELECT
    string_agg(
      '{"canonicalTextSha256":' || pg_catalog.to_json(chunk.canonical_text_sha256)::text ||
      ',"chunkId":' || pg_catalog.to_json(chunk.chunk_id)::text ||
      ',"chunkOrdinal":' || chunk.chunk_ordinal::text || '}',
      ',' ORDER BY chunk.chunk_ordinal
    ),
    string_agg(
      chunk.canonical_text,
      pg_catalog.chr(10) || pg_catalog.chr(10) ORDER BY chunk.chunk_ordinal
    )
  INTO encoded_chunks, joined_canonical_text
  FROM public.rag_v2_immutable_chunks AS chunk
  WHERE chunk.source_revision_id = p_source_revision_id
    AND chunk.source_scope = p_component_scope
    AND chunk.owner_user_id IS NULL;
  IF encoded_chunks IS NULL OR joined_canonical_text IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"canonicalTextSha256":' || pg_catalog.to_json(encode(
      public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex'
    ))::text || ',"chunks":[' || encoded_chunks || '],"documentId":' ||
    pg_catalog.to_json(source_record.document_id)::text || ',"rawContentSha256":' ||
    pg_catalog.to_json(source_record.raw_content_sha256)::text || ',"sourceId":' ||
    pg_catalog.to_json(source_record.source_id)::text || ',"sourceRevisionId":' ||
    pg_catalog.to_json(source_record.source_revision_id)::text || ',"sourceRevisionSha256":' ||
    pg_catalog.to_json(source_record.source_revision_sha256)::text || '}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_public_bge_source_member_digest$;
ALTER FUNCTION rag_v2_immutable_public_bge_source_member_digest(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_bge_source_member_digest(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rag_v2_immutable_public_voyage_source_member_digest(
  p_component_generation_id text,
  p_source_revision_id text
)
RETURNS text
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_public_voyage_source_member_digest$
DECLARE
  source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  oa_card_record public.rag_v2_immutable_oa_source_cards%ROWTYPE;
  encoded_chunks text;
  joined_canonical_text text;
BEGIN
  SELECT source.* INTO source_record
  FROM public.rag_v2_immutable_source_revisions AS source
  JOIN public.rag_v2_immutable_component_generations AS generation
    ON generation.component_generation_id = p_component_generation_id
  WHERE source.source_revision_id = p_source_revision_id
    AND source.source_scope = generation.component_scope
    AND source.owner_user_id IS NULL
    AND generation.owner_user_id IS NULL
    AND generation.embedding_profile_id = 'voyage_context_4_1024_v1';
  IF NOT FOUND
     OR source_record.source_scope NOT IN ('EXACT30', 'OA112')
     OR NOT source_record.external_processing_eligible
     OR (source_record.source_scope = 'EXACT30' AND (
       source_record.machine_fetch_allowed
       OR NOT source_record.local_processing_allowed
       OR NOT source_record.external_embedding_allowed
       OR NOT source_record.external_generation_allowed
       OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
         source_record.source_id,
         source_record.canonical_https_url,
         source_record.raw_content_sha256,
         source_record.exact30_source_card_sha256
       ) IS NOT TRUE
     ))
     OR (source_record.source_scope = 'OA112' AND (
       NOT source_record.machine_fetch_allowed
       OR NOT source_record.local_processing_allowed
       OR NOT source_record.external_embedding_allowed
       OR NOT source_record.external_generation_allowed
       OR source_record.oa_track_id IS NULL
       OR source_record.reserve_source
     )) THEN
    RETURN NULL;
  END IF;

  IF source_record.source_scope = 'OA112' THEN
    SELECT * INTO oa_card_record
    FROM public.rag_v2_immutable_oa_source_cards AS card
    WHERE card.source_revision_id = p_source_revision_id
      AND card.source_scope = 'OA112'
      AND card.source_id = source_record.source_id
      AND card.active_oa112_eligible
      AND card.raw_content_sha256 = source_record.raw_content_sha256
      AND card.canonical_https_url = source_record.canonical_https_url
      AND card.license_evidence_sha256 = source_record.license_evidence_sha256
      AND card.access_evidence_sha256 = source_record.access_evidence_sha256
      AND card.machine_fetch_allowed
      AND card.local_processing_allowed
      AND card.external_embedding_allowed
      AND card.external_generation_allowed;
    IF NOT FOUND THEN
      RETURN NULL;
    END IF;
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
    string_agg(
      chunk.canonical_text,
      pg_catalog.chr(10) || pg_catalog.chr(10) ORDER BY chunk.chunk_ordinal
    )
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
    AND membership.component_scope = source_record.source_scope
    AND membership.source_revision_id = p_source_revision_id;
  IF encoded_chunks IS NULL OR joined_canonical_text IS NULL THEN
    RETURN NULL;
  END IF;
  IF source_record.source_scope = 'EXACT30' THEN
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
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"accessEvidenceSha256":' || pg_catalog.to_json(source_record.access_evidence_sha256)::text ||
    ',"canonicalTextSha256":' || pg_catalog.to_json(encode(
      public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex'
    ))::text || ',"chunks":[' || encoded_chunks || '],"documentId":' ||
    pg_catalog.to_json(source_record.document_id)::text || ',"licenseEvidenceSha256":' ||
    pg_catalog.to_json(source_record.license_evidence_sha256)::text || ',"oaTrackId":' ||
    pg_catalog.to_json(source_record.oa_track_id)::text || ',"rawContentSha256":' ||
    pg_catalog.to_json(source_record.raw_content_sha256)::text || ',"sourceId":' ||
    pg_catalog.to_json(source_record.source_id)::text || ',"sourceRevisionId":' ||
    pg_catalog.to_json(source_record.source_revision_id)::text || ',"sourceRevisionSha256":' ||
    pg_catalog.to_json(source_record.source_revision_sha256)::text || '}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_public_voyage_source_member_digest$;
ALTER FUNCTION rag_v2_immutable_public_voyage_source_member_digest(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_voyage_source_member_digest(text, text) FROM PUBLIC;
