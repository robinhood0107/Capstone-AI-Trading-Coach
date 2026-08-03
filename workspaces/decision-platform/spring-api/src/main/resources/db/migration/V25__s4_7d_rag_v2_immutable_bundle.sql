-- V25는 V24 overlay를 변경하지 않고, actual RAG v2 materializer가 사용할 immutable
-- source/chunk/component/bundle graph를 별도로 둔다. 원본 file/path/provider response는 저장하지 않는다.

-- PostgreSQL은 JSON Schema를 직접 실행하지 않으므로, materializer가 전달하는 Document IR의
-- 보안 관련 closed shape를 DB에서도 재검증한다. 이 함수는 외부 I/O 없이 jsonb만 검사한다.
CREATE FUNCTION rag_v2_immutable_locator_is_valid(p_locator jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_locator_is_valid$
DECLARE
  locator_key text;
  locator_value text;
BEGIN
  IF jsonb_typeof(p_locator) <> 'object'
     OR (SELECT COUNT(*) FROM jsonb_object_keys(p_locator)) <> 1 THEN
    RETURN false;
  END IF;
  FOR locator_key IN SELECT jsonb_object_keys(p_locator)
  LOOP
    IF locator_key IN ('page', 'slide') THEN
      IF jsonb_typeof(p_locator -> locator_key) <> 'number'
         OR p_locator ->> locator_key !~ '^[1-9][0-9]*$' THEN
        RETURN false;
      END IF;
    ELSIF locator_key IN ('sheet', 'section') THEN
      locator_value := p_locator ->> locator_key;
      IF jsonb_typeof(p_locator -> locator_key) <> 'string'
         OR char_length(locator_value) < 1
         OR char_length(locator_value) > (CASE WHEN locator_key = 'sheet' THEN 128 ELSE 300 END)
         OR locator_value ~ '[[:cntrl:]]'
         OR locator_value ~ '(^/|^\\\\|^[A-Za-z]:[\\\\/]|^[A-Za-z][A-Za-z0-9+.-]*://|[\\\\/]|(^|[\\\\/])\\.\\.?([\\\\/]|$))' THEN
        RETURN false;
      END IF;
    ELSE
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END;
$rag_v2_immutable_locator_is_valid$;

CREATE FUNCTION rag_v2_immutable_document_ir_blocks_are_valid(p_blocks jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_document_ir_blocks_are_valid$
DECLARE
  block jsonb;
  cell jsonb;
  item jsonb;
  key text;
  block_type text;
  locator jsonb;
BEGIN
  IF jsonb_typeof(p_blocks) <> 'array'
     OR jsonb_array_length(p_blocks) NOT BETWEEN 1 AND 50000 THEN
    RETURN false;
  END IF;

  FOR block IN SELECT value FROM jsonb_array_elements(p_blocks)
  LOOP
    IF jsonb_typeof(block) <> 'object'
       OR NOT (block ? 'blockType')
       OR block ->> 'blockType' NOT IN ('HEADING', 'PARAGRAPH', 'LIST', 'TABLE', 'FORMULA', 'CAPTION') THEN
      RETURN false;
    END IF;
    block_type := block ->> 'blockType';
    locator := block -> 'locator';
    IF NOT public.rag_v2_immutable_locator_is_valid(locator) THEN
      RETURN false;
    END IF;
    IF jsonb_typeof(block -> 'readingOrder') <> 'number'
       OR block ->> 'readingOrder' !~ '^[0-9]+$'
       OR NOT (block ? 'ocrConfidence')
       OR jsonb_typeof(block -> 'ocrConfidence') NOT IN ('null', 'number') THEN
      RETURN false;
    END IF;
    IF jsonb_typeof(block -> 'ocrConfidence') = 'number'
       AND (block ->> 'ocrConfidence')::double precision NOT BETWEEN 0 AND 1 THEN
      RETURN false;
    END IF;

    FOR key IN SELECT jsonb_object_keys(block)
    LOOP
      IF block_type = 'HEADING' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'level', 'text']) THEN
        RETURN false;
      ELSIF block_type = 'PARAGRAPH' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'text']) THEN
        RETURN false;
      ELSIF block_type = 'CAPTION' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'text', 'targetReadingOrder']) THEN
        RETURN false;
      ELSIF block_type = 'LIST' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'items', 'ordered']) THEN
        RETURN false;
      ELSIF block_type = 'TABLE' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'cells', 'rowCount', 'columnCount']) THEN
        RETURN false;
      ELSIF block_type = 'FORMULA' AND key <> ALL (ARRAY['blockType', 'locator', 'readingOrder', 'ocrConfidence', 'normalizedFormula', 'sourceText']) THEN
        RETURN false;
      END IF;
    END LOOP;

    IF block_type = 'HEADING' THEN
      IF jsonb_typeof(block -> 'level') <> 'number'
         OR block ->> 'level' !~ '^[1-6]$'
         OR jsonb_typeof(block -> 'text') <> 'string'
         OR char_length(block ->> 'text') NOT BETWEEN 1 AND 65536 THEN
        RETURN false;
      END IF;
    ELSIF block_type = 'PARAGRAPH' THEN
      IF jsonb_typeof(block -> 'text') <> 'string'
         OR char_length(block ->> 'text') NOT BETWEEN 1 AND 65536 THEN
        RETURN false;
      END IF;
    ELSIF block_type = 'CAPTION' THEN
      IF jsonb_typeof(block -> 'text') <> 'string'
         OR char_length(block ->> 'text') NOT BETWEEN 1 AND 65536
         OR jsonb_typeof(block -> 'targetReadingOrder') <> 'number'
         OR block ->> 'targetReadingOrder' !~ '^[0-9]+$' THEN
        RETURN false;
      END IF;
    ELSIF block_type = 'LIST' THEN
      IF jsonb_typeof(block -> 'items') <> 'array'
         OR jsonb_array_length(block -> 'items') NOT BETWEEN 1 AND 1000
         OR jsonb_typeof(block -> 'ordered') <> 'boolean' THEN
        RETURN false;
      END IF;
      FOR item IN SELECT value FROM jsonb_array_elements(block -> 'items')
      LOOP
        IF jsonb_typeof(item) <> 'string'
           OR char_length(item #>> '{}') NOT BETWEEN 1 AND 65536 THEN
          RETURN false;
        END IF;
      END LOOP;
    ELSIF block_type = 'TABLE' THEN
      IF jsonb_typeof(block -> 'cells') <> 'array'
         OR jsonb_array_length(block -> 'cells') NOT BETWEEN 1 AND 50000
         OR jsonb_typeof(block -> 'rowCount') <> 'number'
         OR jsonb_typeof(block -> 'columnCount') <> 'number'
         OR block ->> 'rowCount' !~ '^[1-9][0-9]*$'
         OR block ->> 'columnCount' !~ '^[1-9][0-9]*$'
         OR (block ->> 'rowCount')::integer > 50000
         OR (block ->> 'columnCount')::integer > 256
         OR (block ->> 'rowCount')::integer * (block ->> 'columnCount')::integer > 50000 THEN
        RETURN false;
      END IF;
      FOR cell IN SELECT value FROM jsonb_array_elements(block -> 'cells')
      LOOP
        IF jsonb_typeof(cell) <> 'object'
           OR EXISTS (
             SELECT 1 FROM jsonb_object_keys(cell) AS cell_key
             WHERE cell_key NOT IN ('row', 'column', 'rowSpan', 'columnSpan', 'text')
           )
           OR jsonb_typeof(cell -> 'row') <> 'number'
           OR jsonb_typeof(cell -> 'column') <> 'number'
           OR jsonb_typeof(cell -> 'rowSpan') <> 'number'
           OR jsonb_typeof(cell -> 'columnSpan') <> 'number'
           OR jsonb_typeof(cell -> 'text') <> 'string'
           OR cell ->> 'row' !~ '^[0-9]+$'
           OR cell ->> 'column' !~ '^[0-9]+$'
           OR cell ->> 'rowSpan' !~ '^[1-9][0-9]*$'
           OR cell ->> 'columnSpan' !~ '^[1-9][0-9]*$'
           OR (cell ->> 'row')::integer >= (block ->> 'rowCount')::integer
           OR (cell ->> 'column')::integer >= (block ->> 'columnCount')::integer
           OR (cell ->> 'row')::integer + (cell ->> 'rowSpan')::integer > (block ->> 'rowCount')::integer
           OR (cell ->> 'column')::integer + (cell ->> 'columnSpan')::integer > (block ->> 'columnCount')::integer
           OR char_length(cell ->> 'text') NOT BETWEEN 1 AND 65536 THEN
          RETURN false;
        END IF;
      END LOOP;
    ELSE
      IF jsonb_typeof(block -> 'normalizedFormula') <> 'string'
         OR jsonb_typeof(block -> 'sourceText') <> 'string'
         OR char_length(block ->> 'normalizedFormula') NOT BETWEEN 1 AND 65536
         OR char_length(block ->> 'sourceText') NOT BETWEEN 1 AND 65536 THEN
        RETURN false;
      END IF;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM jsonb_array_elements(p_blocks) AS block_value
    GROUP BY block_value ->> 'readingOrder'
    HAVING COUNT(*) > 1
  ) THEN
    RETURN false;
  END IF;
  RETURN true;
END;
$rag_v2_immutable_document_ir_blocks_are_valid$;

CREATE FUNCTION rag_v2_immutable_document_ir_structure_is_valid(p_document_ir jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_document_ir_structure_is_valid$
DECLARE
  language_tag jsonb;
  parser_evidence jsonb;
  ocr_evidence jsonb;
  safety jsonb;
  ocr_backend text;
BEGIN
  IF jsonb_typeof(p_document_ir) <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_document_ir) AS root_key
       WHERE root_key NOT IN (
         'contractId', 'documentIrVersion', 'sourceId', 'sourceRevisionId', 'mimeType',
         'rawContentSha256', 'normalizedContentSha256', 'languageTags', 'extractionMode',
         'parserEvidence', 'blocks', 'safetyClassification'
       )
     )
     OR NOT (p_document_ir ?& ARRAY[
       'contractId', 'documentIrVersion', 'sourceId', 'sourceRevisionId', 'mimeType',
       'rawContentSha256', 'normalizedContentSha256', 'languageTags', 'extractionMode',
       'parserEvidence', 'blocks', 'safetyClassification'
     ])
     OR p_document_ir ->> 'contractId' <> 'rag-document-ir-v1'
     OR jsonb_typeof(p_document_ir -> 'documentIrVersion') <> 'number'
     OR p_document_ir ->> 'documentIrVersion' <> '1'
     OR jsonb_typeof(p_document_ir -> 'sourceId') <> 'string'
     OR p_document_ir ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR jsonb_typeof(p_document_ir -> 'sourceRevisionId') <> 'string'
     OR p_document_ir ->> 'sourceRevisionId' !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_document_ir ->> 'mimeType' NOT IN (
       'application/pdf',
       'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
       'application/vnd.openxmlformats-officedocument.presentationml.presentation',
       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
       'text/html', 'text/markdown', 'text/plain', 'image/png', 'image/jpeg', 'image/tiff'
     )
     OR jsonb_typeof(p_document_ir -> 'rawContentSha256') <> 'string'
     OR p_document_ir ->> 'rawContentSha256' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_document_ir -> 'normalizedContentSha256') <> 'string'
     OR p_document_ir ->> 'normalizedContentSha256' !~ '^[0-9a-f]{64}$'
     OR p_document_ir ->> 'extractionMode' NOT IN ('NATIVE', 'MIXED', 'OCR')
     OR NOT public.rag_v2_immutable_document_ir_blocks_are_valid(p_document_ir -> 'blocks') THEN
    RETURN false;
  END IF;

  IF jsonb_typeof(p_document_ir -> 'languageTags') <> 'array'
     OR jsonb_array_length(p_document_ir -> 'languageTags') NOT BETWEEN 1 AND 10 THEN
    RETURN false;
  END IF;
  FOR language_tag IN SELECT value FROM jsonb_array_elements(p_document_ir -> 'languageTags')
  LOOP
    IF jsonb_typeof(language_tag) <> 'string'
       OR language_tag #>> '{}' !~ '^[a-z]{2,3}(-[A-Z]{2})?$' THEN
      RETURN false;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM jsonb_array_elements_text(p_document_ir -> 'languageTags') AS tags(value)
    GROUP BY value
    HAVING COUNT(*) > 1
  ) THEN
    RETURN false;
  END IF;

  parser_evidence := p_document_ir -> 'parserEvidence';
  IF jsonb_typeof(parser_evidence) <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(parser_evidence) AS parser_key
       WHERE parser_key NOT IN ('parserBackend', 'parserVersion', 'parserArtifactSha256', 'ocr')
     )
     OR NOT (parser_evidence ?& ARRAY['parserBackend', 'parserVersion', 'parserArtifactSha256', 'ocr'])
     OR jsonb_typeof(parser_evidence -> 'parserBackend') <> 'string'
     OR char_length(parser_evidence ->> 'parserBackend') NOT BETWEEN 1 AND 128
     OR jsonb_typeof(parser_evidence -> 'parserVersion') <> 'string'
     OR char_length(parser_evidence ->> 'parserVersion') NOT BETWEEN 1 AND 128
     OR jsonb_typeof(parser_evidence -> 'parserArtifactSha256') <> 'string'
     OR parser_evidence ->> 'parserArtifactSha256' !~ '^[0-9a-f]{64}$' THEN
    RETURN false;
  END IF;
  ocr_evidence := parser_evidence -> 'ocr';
  IF jsonb_typeof(ocr_evidence) <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(ocr_evidence) AS ocr_key
       WHERE ocr_key NOT IN ('backend', 'backendVersion', 'modelSha256')
     )
     OR NOT (ocr_evidence ?& ARRAY['backend', 'backendVersion', 'modelSha256']) THEN
    RETURN false;
  END IF;
  ocr_backend := ocr_evidence ->> 'backend';
  IF jsonb_typeof(ocr_evidence -> 'backend') <> 'string'
     OR ocr_backend NOT IN ('NOT_USED', 'PADDLE_STRUCTURED', 'PADDLE_VL', 'UNLIMITED_GGUF') THEN
    RETURN false;
  END IF;
  IF ocr_backend = 'NOT_USED' THEN
    IF jsonb_typeof(ocr_evidence -> 'backendVersion') <> 'null'
       OR jsonb_typeof(ocr_evidence -> 'modelSha256') <> 'null' THEN
      RETURN false;
    END IF;
  ELSIF jsonb_typeof(ocr_evidence -> 'backendVersion') <> 'string'
     OR char_length(ocr_evidence ->> 'backendVersion') NOT BETWEEN 1 AND 128
     OR jsonb_typeof(ocr_evidence -> 'modelSha256') <> 'string'
     OR ocr_evidence ->> 'modelSha256' !~ '^[0-9a-f]{64}$' THEN
    RETURN false;
  END IF;

  safety := p_document_ir -> 'safetyClassification';
  IF jsonb_typeof(safety) <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(safety) AS safety_key
       WHERE safety_key NOT IN ('externalLlmEligible', 'piiDetected', 'promptInjectionDetected', 'secretDetected')
     )
     OR NOT (safety ?& ARRAY['externalLlmEligible', 'piiDetected', 'promptInjectionDetected', 'secretDetected']) THEN
    RETURN false;
  END IF;
  FOR language_tag IN SELECT value FROM jsonb_each(safety)
  LOOP
    IF jsonb_typeof(language_tag) <> 'boolean' THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END;
$rag_v2_immutable_document_ir_structure_is_valid$;

-- URL을 실제로 열지는 않지만, immutable registry에 internal origin·credential·fragment가
-- 섞이지 않게 public HTTPS locator의 최소 문법을 고정한다. DNS/redirect 검증은 downloader가 맡는다.
CREATE FUNCTION rag_v2_immutable_public_https_url_is_valid(p_url text)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_public_https_url_is_valid$
DECLARE
  host_name text;
BEGIN
  IF octet_length(p_url) NOT BETWEEN 9 AND 2048
     OR p_url !~ '^https://[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+(/[^[:space:]#]*)?(\?[^[:space:]#]*)?$' THEN
    RETURN false;
  END IF;
  host_name := lower(substring(p_url FROM '^https://([^/]+)'));
  IF host_name IS NULL
     OR host_name ~ '^[0-9.]+$'
     OR host_name = 'localhost'
     OR host_name LIKE 'localhost.%'
     OR host_name LIKE '%.localhost'
     OR host_name LIKE '%.local'
     OR host_name LIKE '%.internal'
     OR host_name LIKE '%.home.arpa'
     OR host_name LIKE '%.test' THEN
    RETURN false;
  END IF;
  RETURN true;
END;
$rag_v2_immutable_public_https_url_is_valid$;

-- Active OA112는 logical selection의 14개 track을 그대로 보존한다. source revision만의
-- 112개 총수는 한 track으로 치우친 release를 막지 못하므로 activation은 이 catalog의 8개씩을 재검증한다.
CREATE TABLE rag_v2_immutable_oa_track_catalog (
  track_id text PRIMARY KEY,
  track_ordinal integer NOT NULL,
  required_active_source_count integer NOT NULL DEFAULT 8,
  CONSTRAINT rag_v2_immutable_oa_track_ordinal_check CHECK (track_ordinal BETWEEN 1 AND 14),
  CONSTRAINT rag_v2_immutable_oa_track_ordinal_unique UNIQUE (track_ordinal),
  CONSTRAINT rag_v2_immutable_oa_track_required_count_check CHECK (required_active_source_count = 8)
);
INSERT INTO rag_v2_immutable_oa_track_catalog (track_id, track_ordinal)
VALUES
  ('MICRO_GAME_INFO_MARKET_DESIGN', 1),
  ('MACRO_MONETARY_INTERNATIONAL', 2),
  ('PROBABILITY_STATISTICS_OPTIMIZATION', 3),
  ('ECONOMETRICS_CAUSAL_EVENT_STUDY', 4),
  ('TIME_SERIES_REGIME_VOLATILITY', 5),
  ('ACCOUNTING_CORPORATE_FINANCE_VALUATION', 6),
  ('ASSET_PRICING_FACTOR_PORTFOLIO', 7),
  ('FIXED_INCOME_RATES_CREDIT', 8),
  ('DERIVATIVES_STOCHASTIC_NUMERICS', 9),
  ('MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY', 10),
  ('RISK_STRESS_BACKTEST_MODEL_RISK', 11),
  ('BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING', 12),
  ('FINANCIAL_ML_PIT_DATA_PROVENANCE', 13),
  ('CROSS_MARKET_COMMODITIES_POLICY_KOREA', 14);

CREATE TABLE rag_v2_immutable_source_revisions (
  source_revision_id text PRIMARY KEY,
  document_id text NOT NULL,
  source_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_scope text NOT NULL,
  oa_track_id text REFERENCES rag_v2_immutable_oa_track_catalog(track_id) ON DELETE RESTRICT,
  reserve_source boolean NOT NULL DEFAULT false,
  source_revision_sha256 text NOT NULL,
  raw_content_sha256 text NOT NULL,
  normalized_document_ir_sha256 text NOT NULL,
  canonical_text_sha256 text NOT NULL,
  document_ir jsonb NOT NULL,
  canonical_text text NOT NULL,
  sanitized_display_name text,
  source_locator jsonb NOT NULL,
  canonical_https_url text,
  license_evidence_sha256 text,
  access_evidence_sha256 text,
  mime_type text NOT NULL,
  machine_fetch_allowed boolean NOT NULL,
  local_processing_allowed boolean NOT NULL,
  external_embedding_allowed boolean NOT NULL,
  external_generation_allowed boolean NOT NULL,
  external_processing_eligible boolean NOT NULL,
  parser_version text NOT NULL,
  tokenizer_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_source_revision_id_check
    CHECK (source_revision_id ~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT rag_v2_immutable_source_document_id_check
    CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_immutable_source_id_check
    CHECK (source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT rag_v2_immutable_source_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_source_oa_track_check
    CHECK (
      (source_scope = 'OA112' AND oa_track_id IS NOT NULL)
      OR (source_scope <> 'OA112' AND oa_track_id IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_source_reserve_check
    CHECK (NOT reserve_source OR source_scope = 'OA112'),
  CONSTRAINT rag_v2_immutable_source_hash_check
    CHECK (
      source_revision_sha256 ~ '^[0-9a-f]{64}$'
      AND raw_content_sha256 ~ '^[0-9a-f]{64}$'
      AND normalized_document_ir_sha256 ~ '^[0-9a-f]{64}$'
      AND canonical_text_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_source_ir_check
    CHECK (
      jsonb_typeof(document_ir) = 'object'
      AND octet_length(document_ir::text) BETWEEN 2 AND 16777216
      AND public.rag_v2_immutable_document_ir_structure_is_valid(document_ir)
      AND document_ir ->> 'sourceId' = source_id
      AND document_ir ->> 'sourceRevisionId' = source_revision_id
      AND document_ir ->> 'mimeType' = mime_type
      AND document_ir ->> 'rawContentSha256' = raw_content_sha256
      AND document_ir ->> 'normalizedContentSha256' = normalized_document_ir_sha256
      AND document_ir -> 'parserEvidence' ->> 'parserVersion' = parser_version
      AND document_ir -> 'parserEvidence' ->> 'parserArtifactSha256' ~ '^[0-9a-f]{64}$'
      AND CASE
        WHEN jsonb_typeof(document_ir -> 'safetyClassification' -> 'externalLlmEligible') = 'boolean'
          AND jsonb_typeof(document_ir -> 'safetyClassification' -> 'piiDetected') = 'boolean'
          AND jsonb_typeof(document_ir -> 'safetyClassification' -> 'promptInjectionDetected') = 'boolean'
          AND jsonb_typeof(document_ir -> 'safetyClassification' -> 'secretDetected') = 'boolean'
        THEN
          document_ir -> 'safetyClassification' ->> 'secretDetected' = 'false'
          AND (
            (document_ir -> 'safetyClassification' ->> 'piiDetected' = 'false'
              AND document_ir -> 'safetyClassification' ->> 'promptInjectionDetected' = 'false')
            OR document_ir -> 'safetyClassification' ->> 'externalLlmEligible' = 'false'
          )
          AND external_processing_eligible = (
            (document_ir -> 'safetyClassification' ->> 'externalLlmEligible')::boolean
            AND external_embedding_allowed
            AND external_generation_allowed
          )
        ELSE false
      END
      AND NOT (document_ir ? 'originalPath')
      AND NOT (document_ir ? 'rawPath')
      AND NOT (document_ir ? 'absolutePath')
      AND NOT (document_ir ? 'filePath')
      AND NOT (document_ir ? 'url')
    ),
  CONSTRAINT rag_v2_immutable_source_text_check
    CHECK (
      octet_length(canonical_text) BETWEEN 1 AND 16777216
      AND canonical_text_sha256 = encode(digest(canonical_text, 'sha256'), 'hex')
    ),
  CONSTRAINT rag_v2_immutable_source_display_name_check
    CHECK (
      sanitized_display_name IS NULL
      OR (
        char_length(sanitized_display_name) BETWEEN 1 AND 160
        AND sanitized_display_name !~ '[/\\:]'
      )
    ),
  CONSTRAINT rag_v2_immutable_source_locator_check
    CHECK (public.rag_v2_immutable_locator_is_valid(source_locator)),
  CONSTRAINT rag_v2_immutable_source_public_url_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112')
        AND public.rag_v2_immutable_public_https_url_is_valid(canonical_https_url))
      OR (source_scope = 'OWNER_PRIVATE' AND canonical_https_url IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_source_oa_evidence_check
    CHECK (
      (source_scope = 'OA112'
        AND license_evidence_sha256 ~ '^[0-9a-f]{64}$'
        AND access_evidence_sha256 ~ '^[0-9a-f]{64}$')
      OR (source_scope <> 'OA112'
        AND license_evidence_sha256 IS NULL
        AND access_evidence_sha256 IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_source_permission_check
    CHECK (
      NOT external_processing_eligible
      OR (external_embedding_allowed AND external_generation_allowed)
    ),
  CONSTRAINT rag_v2_immutable_source_owner_processing_check
    CHECK (source_scope <> 'OWNER_PRIVATE' OR local_processing_allowed),
  CONSTRAINT rag_v2_immutable_source_runtime_text_check
    CHECK (
      char_length(mime_type) BETWEEN 3 AND 128
      AND char_length(parser_version) BETWEEN 1 AND 128
      AND char_length(tokenizer_version) BETWEEN 1 AND 128
    ),
  CONSTRAINT rag_v2_immutable_source_scope_identity_unique
    UNIQUE (source_revision_id, source_scope),
  CONSTRAINT rag_v2_immutable_source_scope_owner_unique
    UNIQUE (source_revision_id, source_scope, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_source_owner_document_idx
  ON rag_v2_immutable_source_revisions (owner_user_id, document_id, created_at DESC);
CREATE INDEX rag_v2_immutable_source_public_scope_idx
  ON rag_v2_immutable_source_revisions (source_scope, reserve_source, source_revision_id)
  WHERE owner_user_id IS NULL;

CREATE FUNCTION rag_v2_immutable_oa_source_card_v4_is_valid(p_card jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $rag_v2_immutable_oa_source_card_v4_is_valid$
DECLARE
  nested_key text;
  author_value jsonb;
BEGIN
  IF jsonb_typeof(p_card) <> 'object'
     OR EXISTS (
       SELECT 1
       FROM jsonb_object_keys(p_card) AS root_key
       WHERE root_key NOT IN (
         'accessEvidence', 'activeOa112Eligible', 'authors', 'canonicalUrl', 'canonicalUrlSha256',
         'contractId', 'identifier', 'licenseEvidenceDigest', 'mimeType', 'permissions',
         'rawContentSha256', 'revision', 'revisionDate', 'schemaVersion', 'sourceId', 'sourceKind', 'title'
       )
     )
     OR NOT (p_card ?& ARRAY[
       'accessEvidence', 'activeOa112Eligible', 'authors', 'canonicalUrl', 'canonicalUrlSha256',
       'contractId', 'identifier', 'licenseEvidenceDigest', 'mimeType', 'permissions',
       'rawContentSha256', 'revision', 'revisionDate', 'schemaVersion', 'sourceId', 'sourceKind', 'title'
     ])
     OR jsonb_typeof(p_card -> 'contractId') <> 'string'
     OR p_card ->> 'contractId' <> 'rag-source-card-v4'
     OR jsonb_typeof(p_card -> 'schemaVersion') <> 'number'
     OR p_card ->> 'schemaVersion' <> '4'
     OR jsonb_typeof(p_card -> 'sourceKind') <> 'string'
     OR p_card ->> 'sourceKind' <> 'OPEN_ACCESS_DOCUMENT'
     OR jsonb_typeof(p_card -> 'activeOa112Eligible') <> 'boolean'
     OR jsonb_typeof(p_card -> 'title') <> 'string'
     OR char_length(p_card ->> 'title') NOT BETWEEN 1 AND 500
     OR jsonb_typeof(p_card -> 'canonicalUrl') <> 'string'
     OR NOT public.rag_v2_immutable_public_https_url_is_valid(p_card ->> 'canonicalUrl')
     OR jsonb_typeof(p_card -> 'canonicalUrlSha256') <> 'string'
     OR p_card ->> 'canonicalUrlSha256' <> encode(public.digest(p_card ->> 'canonicalUrl', 'sha256'), 'hex')
     OR jsonb_typeof(p_card -> 'licenseEvidenceDigest') <> 'string'
     OR p_card ->> 'licenseEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_card -> 'rawContentSha256') <> 'string'
     OR p_card ->> 'rawContentSha256' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_card -> 'revision') <> 'string'
     OR char_length(p_card ->> 'revision') NOT BETWEEN 1 AND 128
     OR jsonb_typeof(p_card -> 'revisionDate') <> 'string'
     OR p_card ->> 'revisionDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
     OR jsonb_typeof(p_card -> 'sourceId') <> 'string'
     OR p_card ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR jsonb_typeof(p_card -> 'mimeType') <> 'string'
     OR p_card ->> 'mimeType' NOT IN ('application/pdf', 'text/html', 'text/plain') THEN
    RETURN false;
  END IF;

  IF jsonb_typeof(p_card -> 'authors') <> 'array'
     OR jsonb_array_length(p_card -> 'authors') NOT BETWEEN 1 AND 50 THEN
    RETURN false;
  END IF;
  FOR author_value IN SELECT value FROM jsonb_array_elements(p_card -> 'authors')
  LOOP
    IF jsonb_typeof(author_value) <> 'string'
       OR char_length(author_value #>> '{}') NOT BETWEEN 1 AND 300
       OR btrim(author_value #>> '{}') = ''
       OR author_value #>> '{}' ~ '[[:cntrl:]]' THEN
      RETURN false;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1
    FROM jsonb_array_elements_text(p_card -> 'authors') AS authors(value)
    GROUP BY value
    HAVING COUNT(*) > 1
  ) THEN
    RETURN false;
  END IF;

  IF jsonb_typeof(p_card -> 'identifier') <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_card -> 'identifier') AS identifier_key
       WHERE identifier_key NOT IN ('scheme', 'value')
     )
     OR NOT ((p_card -> 'identifier') ?& ARRAY['scheme', 'value'])
     OR jsonb_typeof(p_card -> 'identifier' -> 'scheme') <> 'string'
     OR p_card -> 'identifier' ->> 'scheme' NOT IN ('DOI', 'ISBN', 'ARXIV')
     OR jsonb_typeof(p_card -> 'identifier' -> 'value') <> 'string'
     OR char_length(p_card -> 'identifier' ->> 'value') NOT BETWEEN 1 AND 256 THEN
    RETURN false;
  END IF;

  IF jsonb_typeof(p_card -> 'permissions') <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_card -> 'permissions') AS permission_key
       WHERE permission_key NOT IN (
         'machineFetchAllowed', 'localProcessingAllowed', 'externalEmbeddingAllowed', 'externalGenerationAllowed'
       )
     )
     OR NOT ((p_card -> 'permissions') ?& ARRAY[
       'machineFetchAllowed', 'localProcessingAllowed', 'externalEmbeddingAllowed', 'externalGenerationAllowed'
     ]) THEN
    RETURN false;
  END IF;
  FOR nested_key IN SELECT jsonb_object_keys(p_card -> 'permissions')
  LOOP
    IF jsonb_typeof(p_card -> 'permissions' -> nested_key) <> 'boolean' THEN
      RETURN false;
    END IF;
  END LOOP;

  IF jsonb_typeof(p_card -> 'accessEvidence') <> 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_card -> 'accessEvidence') AS evidence_key
       WHERE evidence_key NOT IN ('accessCheckedAt', 'accessEvidenceDigest', 'verificationState')
     )
     OR NOT ((p_card -> 'accessEvidence') ?& ARRAY['accessCheckedAt', 'accessEvidenceDigest', 'verificationState'])
     OR jsonb_typeof(p_card -> 'accessEvidence' -> 'accessCheckedAt') <> 'string'
     OR p_card -> 'accessEvidence' ->> 'accessCheckedAt' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$'
     OR jsonb_typeof(p_card -> 'accessEvidence' -> 'accessEvidenceDigest') <> 'string'
     OR p_card -> 'accessEvidence' ->> 'accessEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_card -> 'accessEvidence' -> 'verificationState') <> 'string'
     OR p_card -> 'accessEvidence' ->> 'verificationState' <> 'VERIFIED' THEN
    RETURN false;
  END IF;

  RETURN p_card ->> 'activeOa112Eligible' = 'false'
    OR (
      p_card -> 'permissions' ->> 'machineFetchAllowed' = 'true'
      AND p_card -> 'permissions' ->> 'localProcessingAllowed' = 'true'
      AND p_card -> 'permissions' ->> 'externalEmbeddingAllowed' = 'true'
      AND p_card -> 'permissions' ->> 'externalGenerationAllowed' = 'true'
    );
END;
$rag_v2_immutable_oa_source_card_v4_is_valid$;

-- OA source-card v4 is content-free immutable evidence. It stores bibliographic and rights
-- assertions plus digests only; the OA raw object and extracted text remain outside Git and DB.
CREATE TABLE rag_v2_immutable_oa_source_cards (
  source_revision_id text PRIMARY KEY,
  source_scope text NOT NULL DEFAULT 'OA112',
  source_id text NOT NULL,
  source_card jsonb NOT NULL,
  source_card_sha256 text NOT NULL,
  active_oa112_eligible boolean NOT NULL,
  title text NOT NULL,
  authors text[] NOT NULL,
  canonical_https_url text NOT NULL,
  canonical_https_url_sha256 text NOT NULL,
  identifier_scheme text NOT NULL,
  identifier_value text NOT NULL,
  revision text NOT NULL,
  revision_date date NOT NULL,
  raw_content_sha256 text NOT NULL,
  mime_type text NOT NULL,
  license_evidence_sha256 text NOT NULL,
  access_evidence_sha256 text NOT NULL,
  access_checked_at text NOT NULL,
  access_verification_state text NOT NULL,
  machine_fetch_allowed boolean NOT NULL,
  local_processing_allowed boolean NOT NULL,
  external_embedding_allowed boolean NOT NULL,
  external_generation_allowed boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_oa_card_scope_check CHECK (source_scope = 'OA112'),
  CONSTRAINT rag_v2_immutable_oa_card_source_identity_check
    CHECK (
      source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
      AND source_card_sha256 ~ '^[0-9a-f]{64}$'
      AND source_card_sha256 = encode(digest(source_card::text, 'sha256'), 'hex')
      AND raw_content_sha256 ~ '^[0-9a-f]{64}$'
      AND public.rag_v2_immutable_public_https_url_is_valid(canonical_https_url)
      AND canonical_https_url_sha256 = encode(digest(canonical_https_url, 'sha256'), 'hex')
      AND license_evidence_sha256 ~ '^[0-9a-f]{64}$'
      AND access_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_oa_card_bibliography_check
    CHECK (
      char_length(title) BETWEEN 1 AND 500
      AND cardinality(authors) BETWEEN 1 AND 50
      AND array_position(authors, NULL) IS NULL
      AND octet_length(array_to_string(authors, '')) BETWEEN 1 AND 15000
      AND identifier_scheme IN ('DOI', 'ISBN', 'ARXIV')
      AND char_length(identifier_value) BETWEEN 1 AND 256
      AND char_length(revision) BETWEEN 1 AND 128
      AND mime_type IN ('application/pdf', 'text/html', 'text/plain')
    ),
  CONSTRAINT rag_v2_immutable_oa_card_access_check
    CHECK (
      access_verification_state = 'VERIFIED'
      AND access_checked_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$'
    ),
  CONSTRAINT rag_v2_immutable_oa_card_active_permission_check
    CHECK (
      NOT active_oa112_eligible
      OR (
        machine_fetch_allowed
        AND local_processing_allowed
        AND external_embedding_allowed
        AND external_generation_allowed
      )
    ),
  CONSTRAINT rag_v2_immutable_oa_card_payload_check
    CHECK (
      public.rag_v2_immutable_oa_source_card_v4_is_valid(source_card)
      AND source_card ->> 'sourceId' = source_id
      AND source_card -> 'activeOa112Eligible' = to_jsonb(active_oa112_eligible)
      AND source_card ->> 'title' = title
      AND source_card -> 'authors' = to_jsonb(authors)
      AND source_card ->> 'canonicalUrl' = canonical_https_url
      AND source_card ->> 'canonicalUrlSha256' = canonical_https_url_sha256
      AND source_card -> 'identifier' ->> 'scheme' = identifier_scheme
      AND source_card -> 'identifier' ->> 'value' = identifier_value
      AND source_card ->> 'revision' = revision
      AND source_card ->> 'revisionDate' = revision_date::text
      AND source_card ->> 'rawContentSha256' = raw_content_sha256
      AND source_card ->> 'mimeType' = mime_type
      AND source_card ->> 'licenseEvidenceDigest' = license_evidence_sha256
      AND source_card -> 'accessEvidence' ->> 'accessEvidenceDigest' = access_evidence_sha256
      AND source_card -> 'accessEvidence' ->> 'accessCheckedAt' = access_checked_at
      AND source_card -> 'accessEvidence' ->> 'verificationState' = access_verification_state
      AND source_card -> 'permissions' = jsonb_build_object(
        'machineFetchAllowed', machine_fetch_allowed,
        'localProcessingAllowed', local_processing_allowed,
        'externalEmbeddingAllowed', external_embedding_allowed,
        'externalGenerationAllowed', external_generation_allowed
      )
    ),
  CONSTRAINT rag_v2_immutable_oa_card_source_fkey
    FOREIGN KEY (source_revision_id, source_scope)
    REFERENCES rag_v2_immutable_source_revisions (source_revision_id, source_scope)
    ON DELETE RESTRICT
);
CREATE UNIQUE INDEX rag_v2_immutable_oa_card_source_card_hash_unique
  ON rag_v2_immutable_oa_source_cards (source_card_sha256);

CREATE TABLE rag_v2_immutable_chunks (
  chunk_id text PRIMARY KEY,
  source_revision_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_scope text NOT NULL,
  chunk_ordinal integer NOT NULL,
  heading_path text[] NOT NULL,
  locator jsonb NOT NULL,
  canonical_text text NOT NULL,
  canonical_text_sha256 text NOT NULL,
  token_count integer NOT NULL,
  contains_table boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_chunk_id_check
    CHECK (chunk_id ~ '^rag_v2_chk_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_chunk_source_scope_owner_fkey
    FOREIGN KEY (source_revision_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_source_revisions (source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_chunk_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_chunk_ordinal_check
    CHECK (chunk_ordinal BETWEEN 1 AND 100000),
  CONSTRAINT rag_v2_immutable_chunk_heading_check
    CHECK (
      cardinality(heading_path) BETWEEN 0 AND 12
      AND octet_length(array_to_string(heading_path, '')) <= 4096
      AND array_position(heading_path, '') IS NULL
    ),
  CONSTRAINT rag_v2_immutable_chunk_locator_check
    CHECK (public.rag_v2_immutable_locator_is_valid(locator)),
  CONSTRAINT rag_v2_immutable_chunk_text_check
    CHECK (
      octet_length(canonical_text) BETWEEN 1 AND 1048576
      AND canonical_text_sha256 ~ '^[0-9a-f]{64}$'
      AND canonical_text_sha256 = encode(digest(canonical_text, 'sha256'), 'hex')
      AND token_count BETWEEN 1 AND 600
    ),
  CONSTRAINT rag_v2_immutable_chunk_source_ordinal_unique
    UNIQUE (source_revision_id, chunk_ordinal),
  CONSTRAINT rag_v2_immutable_chunk_source_identity_unique
    UNIQUE (chunk_id, source_revision_id),
  CONSTRAINT rag_v2_immutable_chunk_source_scope_owner_unique
    UNIQUE (chunk_id, source_revision_id, source_scope, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_chunks_trgm_idx
  ON rag_v2_immutable_chunks USING gin (canonical_text gin_trgm_ops);
CREATE INDEX rag_v2_immutable_chunks_owner_source_idx
  ON rag_v2_immutable_chunks (owner_user_id, source_revision_id, chunk_id);

CREATE TABLE rag_v2_immutable_component_generations (
  component_generation_id text PRIMARY KEY,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  component_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  state text NOT NULL,
  evaluation_status text NOT NULL,
  expected_source_count integer NOT NULL,
  expected_chunk_count integer NOT NULL,
  actual_source_count integer NOT NULL DEFAULT 0,
  actual_chunk_count integer NOT NULL DEFAULT 0,
  generation_hash text NOT NULL,
  manifest_hash text NOT NULL,
  failure_code text,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  evaluated_at timestamptz,
  activated_at timestamptz,
  CONSTRAINT rag_v2_immutable_component_generation_id_check
    CHECK (component_generation_id ~ '^rgr_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_component_scope_check
    CHECK (
      (component_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (component_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_component_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_immutable_component_state_check
    CHECK (state IN ('STAGING', 'EVALUATED', 'ACTIVE', 'SUPERSEDED', 'FAILED', 'DELETING')),
  CONSTRAINT rag_v2_immutable_component_evaluation_check
    CHECK (evaluation_status IN ('PENDING', 'PASSED', 'FAILED')),
  CONSTRAINT rag_v2_immutable_component_count_check
    CHECK (
      expected_source_count >= 0
      AND expected_chunk_count >= 0
      AND actual_source_count >= 0
      AND actual_chunk_count >= 0
      AND (
        (component_scope = 'EXACT30' AND expected_source_count = 30)
        OR (component_scope = 'OA112' AND expected_source_count = 112)
        OR component_scope = 'OWNER_PRIVATE'
      )
    ),
  CONSTRAINT rag_v2_immutable_component_hash_check
    CHECK (generation_hash ~ '^[0-9a-f]{64}$' AND manifest_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_component_failure_check
    CHECK (
      (state = 'FAILED' AND failure_code IN ('PARSER_FAILED', 'EMBEDDING_FAILED', 'DISK_FULL', 'ACTIVATION_CONFLICT'))
      OR (state <> 'FAILED' AND failure_code IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_component_evaluated_check
    CHECK (
      (evaluation_status = 'PENDING' AND evaluated_at IS NULL)
      OR (evaluation_status IN ('PASSED', 'FAILED') AND evaluated_at IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_component_activation_check
    CHECK (
      (state = 'ACTIVE' AND evaluation_status = 'PASSED' AND activated_at IS NOT NULL)
      OR state <> 'ACTIVE'
    ),
  CONSTRAINT rag_v2_immutable_component_generation_profile_unique
    UNIQUE (component_generation_id, embedding_profile_id),
  CONSTRAINT rag_v2_immutable_component_generation_scope_unique
    UNIQUE (component_generation_id, component_scope),
  CONSTRAINT rag_v2_immutable_component_generation_scope_owner_unique
    UNIQUE (component_generation_id, component_scope, owner_partition_key),
  CONSTRAINT rag_v2_immutable_component_generation_profile_scope_owner_unique
    UNIQUE (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_component_owner_state_idx
  ON rag_v2_immutable_component_generations (owner_user_id, component_scope, state, created_at DESC);

CREATE TABLE rag_v2_immutable_generation_memberships (
  component_generation_id text NOT NULL,
  chunk_id text NOT NULL,
  source_revision_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  component_scope text NOT NULL,
  ordinal integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_membership_pkey PRIMARY KEY (component_generation_id, chunk_id),
  CONSTRAINT rag_v2_immutable_membership_generation_scope_owner_fkey
    FOREIGN KEY (component_generation_id, component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_membership_chunk_source_scope_owner_fkey
    FOREIGN KEY (chunk_id, source_revision_id, component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_chunks (chunk_id, source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_membership_scope_check
    CHECK (
      (component_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (component_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_membership_ordinal_check CHECK (ordinal BETWEEN 1 AND 100000),
  CONSTRAINT rag_v2_immutable_membership_generation_ordinal_unique
    UNIQUE (component_generation_id, ordinal),
  CONSTRAINT rag_v2_immutable_membership_scope_owner_unique
    UNIQUE (component_generation_id, chunk_id, component_scope, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_memberships_source_idx
  ON rag_v2_immutable_generation_memberships (source_revision_id, component_generation_id);
CREATE INDEX rag_v2_immutable_memberships_owner_idx
  ON rag_v2_immutable_generation_memberships (owner_user_id, component_generation_id, chunk_id);

CREATE TABLE rag_v2_immutable_generation_embeddings (
  component_generation_id text NOT NULL,
  chunk_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  component_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_generation_embedding_pkey
    PRIMARY KEY (component_generation_id, chunk_id),
  CONSTRAINT rag_v2_immutable_generation_embedding_membership_scope_owner_fkey
    FOREIGN KEY (component_generation_id, chunk_id, component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_generation_memberships (component_generation_id, chunk_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_generation_embedding_profile_scope_owner_fkey
    FOREIGN KEY (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_generation_embedding_scope_check
    CHECK (
      (component_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (component_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_generation_embedding_hash_check
    CHECK (
      embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_v2_immutable_generation_embedding_context_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_generation_embedding_dimension_check
    CHECK (vector_dims(embedding) = 1024),
  CONSTRAINT rag_v2_immutable_generation_embedding_finite_check
    CHECK (vector_norm(embedding)::text NOT IN ('NaN', 'Infinity', '-Infinity')),
  CONSTRAINT rag_v2_immutable_generation_embedding_normalized_check
    CHECK (abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001),
  CONSTRAINT rag_v2_immutable_generation_embedding_scope_owner_unique
    UNIQUE (component_generation_id, chunk_id, component_scope, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_generation_embeddings_owner_idx
  ON rag_v2_immutable_generation_embeddings (owner_user_id, component_generation_id, chunk_id);

CREATE TABLE rag_v2_immutable_embedding_cache (
  cache_id text PRIMARY KEY,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_revision_id text NOT NULL,
  chunk_id text NOT NULL,
  source_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_embedding_cache_id_check
    CHECK (cache_id ~ '^rgr_cache_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_embedding_cache_chunk_scope_owner_fkey
    FOREIGN KEY (chunk_id, source_revision_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_chunks (chunk_id, source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_embedding_cache_source_scope_owner_fkey
    FOREIGN KEY (source_revision_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_source_revisions (source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_embedding_cache_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_embedding_cache_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_immutable_embedding_cache_hash_check
    CHECK (
      embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_v2_immutable_embedding_cache_context_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_embedding_cache_dimension_check
    CHECK (vector_dims(embedding) = 1024),
  CONSTRAINT rag_v2_immutable_embedding_cache_finite_check
    CHECK (vector_norm(embedding)::text NOT IN ('NaN', 'Infinity', '-Infinity')),
  CONSTRAINT rag_v2_immutable_embedding_cache_normalized_check
    CHECK (abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001),
  CONSTRAINT rag_v2_immutable_embedding_cache_identity_unique
    UNIQUE NULLS NOT DISTINCT (
      owner_user_id,
      chunk_id,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash
    )
);

CREATE TABLE rag_v2_immutable_materialization_runs (
  materialization_run_id text PRIMARY KEY,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  component_generation_id text,
  component_scope text NOT NULL,
  document_id text,
  state text NOT NULL,
  source_reused_count integer NOT NULL DEFAULT 0,
  chunk_reused_count integer NOT NULL DEFAULT 0,
  embedding_reused_count integer NOT NULL DEFAULT 0,
  failure_code text,
  started_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  completed_at timestamptz,
  CONSTRAINT rag_v2_immutable_materialization_run_id_check
    CHECK (materialization_run_id ~ '^rgr_run_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_materialization_run_component_scope_owner_fkey
    FOREIGN KEY (component_generation_id, component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_materialization_run_scope_check
    CHECK (
      (component_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR
      (component_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_materialization_run_document_check
    CHECK (
      (component_scope = 'OWNER_PRIVATE' AND document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$')
      OR (component_scope IN ('EXACT30', 'OA112') AND document_id IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_materialization_run_state_check
    CHECK (state IN ('OPEN', 'STAGED', 'EVALUATED', 'ACTIVATED', 'FAILED')),
  CONSTRAINT rag_v2_immutable_materialization_run_reuse_check
    CHECK (source_reused_count >= 0 AND chunk_reused_count >= 0 AND embedding_reused_count >= 0),
  CONSTRAINT rag_v2_immutable_materialization_run_failure_check
    CHECK (
      (state = 'FAILED' AND failure_code IN ('PARSER_FAILED', 'EMBEDDING_FAILED', 'DISK_FULL', 'ACTIVATION_CONFLICT', 'OWNER_DELETED'))
      OR (state <> 'FAILED' AND failure_code IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_materialization_run_completion_check
    CHECK ((state IN ('OPEN', 'STAGED') AND completed_at IS NULL) OR (state NOT IN ('OPEN', 'STAGED') AND completed_at IS NOT NULL)),
  CONSTRAINT rag_v2_immutable_materialization_run_scope_owner_unique
    UNIQUE (materialization_run_id, component_scope, owner_partition_key),
  CONSTRAINT rag_v2_immutable_materialization_run_owner_unique
    UNIQUE (materialization_run_id, owner_partition_key)
);
CREATE INDEX rag_v2_immutable_materialization_runs_owner_idx
  ON rag_v2_immutable_materialization_runs (owner_user_id, started_at DESC);
CREATE INDEX rag_v2_immutable_materialization_runs_owner_document_idx
  ON rag_v2_immutable_materialization_runs (owner_user_id, document_id, state, started_at DESC)
  WHERE document_id IS NOT NULL;

CREATE TABLE rag_v2_immutable_source_receipts (
  receipt_id text PRIMARY KEY,
  materialization_run_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_scope text NOT NULL,
  source_revision_id text,
  raw_content_sha256 text NOT NULL,
  canonical_text_sha256 text NOT NULL,
  reuse_state text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_source_receipt_id_check CHECK (receipt_id ~ '^rgr_src_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_source_receipt_run_scope_owner_fkey
    FOREIGN KEY (materialization_run_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_materialization_runs (materialization_run_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_source_receipt_source_scope_owner_fkey
    FOREIGN KEY (source_revision_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_source_revisions (source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_source_receipt_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_source_receipt_hash_check
    CHECK (raw_content_sha256 ~ '^[0-9a-f]{64}$' AND canonical_text_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_source_receipt_identity_check
    CHECK (source_revision_id IS NULL OR source_revision_id ~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT rag_v2_immutable_source_receipt_reuse_check CHECK (reuse_state IN ('NEW', 'REUSED'))
);

CREATE TABLE rag_v2_immutable_chunk_receipts (
  receipt_id text PRIMARY KEY,
  materialization_run_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_scope text NOT NULL,
  source_revision_id text,
  chunk_id text,
  canonical_text_sha256 text NOT NULL,
  reuse_state text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_chunk_receipt_id_check CHECK (receipt_id ~ '^rgr_chk_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_chunk_receipt_run_scope_owner_fkey
    FOREIGN KEY (materialization_run_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_materialization_runs (materialization_run_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_chunk_receipt_chunk_scope_owner_fkey
    FOREIGN KEY (chunk_id, source_revision_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_chunks (chunk_id, source_revision_id, source_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_chunk_receipt_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_chunk_receipt_identity_check
    CHECK (
      (
        (source_revision_id IS NULL AND chunk_id IS NULL)
        OR (
          source_revision_id ~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
          AND chunk_id ~ '^rag_v2_chk_[0-9a-f]{32}$'
        )
      )
      AND canonical_text_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_chunk_receipt_reuse_check CHECK (reuse_state IN ('NEW', 'REUSED'))
);

CREATE TABLE rag_v2_immutable_embedding_receipts (
  receipt_id text PRIMARY KEY,
  materialization_run_id text NOT NULL,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  source_scope text NOT NULL,
  component_generation_id text,
  chunk_id text,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  reuse_state text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_embedding_receipt_id_check CHECK (receipt_id ~ '^rgr_emb_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_embedding_receipt_run_scope_owner_fkey
    FOREIGN KEY (materialization_run_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_materialization_runs (materialization_run_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_embedding_receipt_embedding_scope_owner_fkey
    FOREIGN KEY (component_generation_id, chunk_id, source_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_generation_embeddings (component_generation_id, chunk_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_embedding_receipt_scope_check
    CHECK (
      (source_scope IN ('EXACT30', 'OA112') AND owner_user_id IS NULL)
      OR (source_scope = 'OWNER_PRIVATE' AND owner_user_id IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_embedding_receipt_identity_check
    CHECK (
      (
        (component_generation_id IS NULL AND chunk_id IS NULL)
        OR (
          component_generation_id ~ '^rgr_[0-9a-f]{32}$'
          AND chunk_id ~ '^rag_v2_chk_[0-9a-f]{32}$'
        )
      )
      AND embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
      AND embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_v2_immutable_embedding_receipt_context_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_embedding_receipt_reuse_check CHECK (reuse_state IN ('NEW', 'REUSED'))
);

CREATE TABLE rag_v2_immutable_public_bundle_pointers (
  state_id text PRIMARY KEY,
  state text NOT NULL,
  exact30_generation_id text,
  oa112_generation_id text,
  embedding_profile_id text,
  exact30_component_scope text NOT NULL DEFAULT 'EXACT30',
  oa112_component_scope text NOT NULL DEFAULT 'OA112',
  public_owner_partition_key text NOT NULL DEFAULT '__PUBLIC__',
  pointer_version bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_public_pointer_id_check CHECK (state_id = 'default'),
  CONSTRAINT rag_v2_immutable_public_pointer_state_check
    CHECK (state IN ('NOT_MATERIALIZED', 'BUILDING', 'ACTIVE', 'FAILED')),
  CONSTRAINT rag_v2_immutable_public_pointer_profile_check
    CHECK (embedding_profile_id IS NULL OR embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_immutable_public_pointer_scope_check
    CHECK (
      exact30_component_scope = 'EXACT30'
      AND oa112_component_scope = 'OA112'
      AND public_owner_partition_key = '__PUBLIC__'
    ),
  CONSTRAINT rag_v2_immutable_public_pointer_version_check CHECK (pointer_version >= 1),
  CONSTRAINT rag_v2_immutable_public_pointer_active_check
    CHECK (
      (state = 'ACTIVE' AND exact30_generation_id IS NOT NULL AND oa112_generation_id IS NOT NULL AND embedding_profile_id IS NOT NULL)
      OR state <> 'ACTIVE'
    ),
  CONSTRAINT rag_v2_immutable_public_pointer_exact_profile_fkey
    FOREIGN KEY (exact30_generation_id, embedding_profile_id, exact30_component_scope, public_owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_public_pointer_oa_profile_fkey
    FOREIGN KEY (oa112_generation_id, embedding_profile_id, oa112_component_scope, public_owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT
);
INSERT INTO rag_v2_immutable_public_bundle_pointers (
  state_id,
  state,
  exact30_generation_id,
  oa112_generation_id,
  embedding_profile_id,
  pointer_version
)
VALUES ('default', 'NOT_MATERIALIZED', NULL, NULL, NULL, 1)
ON CONFLICT (state_id) DO NOTHING;

CREATE TABLE rag_v2_immutable_bundles (
  bundle_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  exact30_generation_id text NOT NULL,
  oa112_generation_id text NOT NULL,
  owner_private_generation_id text NOT NULL,
  embedding_profile_id text NOT NULL,
  exact30_component_scope text NOT NULL DEFAULT 'EXACT30',
  oa112_component_scope text NOT NULL DEFAULT 'OA112',
  owner_private_component_scope text NOT NULL DEFAULT 'OWNER_PRIVATE',
  public_owner_partition_key text NOT NULL DEFAULT '__PUBLIC__',
  state text NOT NULL,
  evaluation_status text NOT NULL,
  bundle_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  evaluated_at timestamptz,
  activated_at timestamptz,
  CONSTRAINT rag_v2_immutable_bundle_id_check CHECK (bundle_id ~ '^rgb_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_bundle_component_distinct_check
    CHECK (
      exact30_generation_id <> oa112_generation_id
      AND exact30_generation_id <> owner_private_generation_id
      AND oa112_generation_id <> owner_private_generation_id
    ),
  CONSTRAINT rag_v2_immutable_bundle_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_immutable_bundle_component_scope_check
    CHECK (
      exact30_component_scope = 'EXACT30'
      AND oa112_component_scope = 'OA112'
      AND owner_private_component_scope = 'OWNER_PRIVATE'
      AND public_owner_partition_key = '__PUBLIC__'
    ),
  CONSTRAINT rag_v2_immutable_bundle_state_check
    CHECK (state IN ('STAGED', 'EVALUATED', 'ACTIVE', 'SUPERSEDED', 'FAILED')),
  CONSTRAINT rag_v2_immutable_bundle_evaluation_check
    CHECK (evaluation_status IN ('PENDING', 'PASSED', 'FAILED')),
  CONSTRAINT rag_v2_immutable_bundle_hash_check CHECK (bundle_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_bundle_evaluated_check
    CHECK (
      (evaluation_status = 'PENDING' AND evaluated_at IS NULL)
      OR (evaluation_status IN ('PASSED', 'FAILED') AND evaluated_at IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_bundle_active_check
    CHECK ((state = 'ACTIVE' AND evaluation_status = 'PASSED' AND activated_at IS NOT NULL) OR state <> 'ACTIVE'),
  CONSTRAINT rag_v2_immutable_bundle_exact_profile_fkey
    FOREIGN KEY (exact30_generation_id, embedding_profile_id, exact30_component_scope, public_owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_bundle_oa_profile_fkey
    FOREIGN KEY (oa112_generation_id, embedding_profile_id, oa112_component_scope, public_owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_bundle_owner_profile_fkey
    FOREIGN KEY (owner_private_generation_id, embedding_profile_id, owner_private_component_scope, owner_partition_key)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, embedding_profile_id, component_scope, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_bundle_owner_identity_unique UNIQUE (bundle_id, owner_user_id)
);
CREATE INDEX rag_v2_immutable_bundles_owner_state_idx
  ON rag_v2_immutable_bundles (owner_user_id, state, created_at DESC);
CREATE INDEX rag_v2_immutable_bundles_owner_component_idx
  ON rag_v2_immutable_bundles (owner_user_id, owner_private_generation_id);

CREATE TABLE rag_v2_immutable_owner_bundle_pointers (
  owner_user_id text PRIMARY KEY REFERENCES users(user_id) ON DELETE RESTRICT,
  state text NOT NULL,
  active_bundle_id text,
  bundle_version bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_owner_pointer_state_check CHECK (state IN ('ABSENT', 'BUILDING', 'READY', 'FAILED')),
  CONSTRAINT rag_v2_immutable_owner_pointer_version_check CHECK (bundle_version >= 0),
  CONSTRAINT rag_v2_immutable_owner_pointer_active_check
    CHECK ((state = 'READY' AND active_bundle_id IS NOT NULL) OR (state <> 'READY' AND active_bundle_id IS NULL)),
  CONSTRAINT rag_v2_immutable_owner_pointer_bundle_fkey
    FOREIGN KEY (active_bundle_id, owner_user_id)
    REFERENCES rag_v2_immutable_bundles (bundle_id, owner_user_id)
    ON DELETE RESTRICT
);

CREATE TABLE rag_v2_immutable_consent_events (
  consent_event_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  action text NOT NULL,
  policy_version text NOT NULL,
  disclosure_digest text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_consent_id_check CHECK (consent_event_id ~ '^cns_v2_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_consent_action_check CHECK (action IN ('GRANT', 'REVOKE')),
  CONSTRAINT rag_v2_immutable_consent_policy_check CHECK (policy_version = 'EXTERNAL_AI_RAG_V2'),
  CONSTRAINT rag_v2_immutable_consent_digest_check CHECK (disclosure_digest ~ '^[0-9a-f]{64}$')
);
CREATE INDEX rag_v2_immutable_consent_owner_created_idx
  ON rag_v2_immutable_consent_events (owner_user_id, created_at DESC, consent_event_id DESC);

CREATE TABLE rag_v2_immutable_import_tickets (
  ticket_hash text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_partition_key text GENERATED ALWAYS AS (coalesce(owner_user_id, '__PUBLIC__')) STORED,
  operation text NOT NULL,
  policy_version text NOT NULL,
  state text NOT NULL,
  issued_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  consumer_run_id text,
  CONSTRAINT rag_v2_immutable_import_ticket_hash_check CHECK (ticket_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_import_ticket_operation_check CHECK (operation = 'OWNER_IMPORT'),
  CONSTRAINT rag_v2_immutable_import_ticket_consumer_run_owner_fkey
    FOREIGN KEY (consumer_run_id, owner_partition_key)
    REFERENCES rag_v2_immutable_materialization_runs (materialization_run_id, owner_partition_key)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_import_ticket_policy_check CHECK (policy_version = 'RAG_V2_OWNER_DOCUMENT_V1'),
  CONSTRAINT rag_v2_immutable_import_ticket_state_check CHECK (state IN ('ISSUED', 'CONSUMED')),
  CONSTRAINT rag_v2_immutable_import_ticket_expiry_check CHECK (expires_at = issued_at + interval '5 minutes'),
  CONSTRAINT rag_v2_immutable_import_ticket_consumed_check
    CHECK (
      (state = 'ISSUED' AND consumed_at IS NULL AND consumer_run_id IS NULL)
      OR (state = 'CONSUMED' AND consumed_at IS NOT NULL AND consumer_run_id ~ '^rgr_run_[0-9a-f]{32}$')
    )
);
CREATE INDEX rag_v2_immutable_import_tickets_owner_expiry_idx
  ON rag_v2_immutable_import_tickets (owner_user_id, expires_at DESC);

CREATE TABLE rag_v2_immutable_activation_receipts (
  activation_receipt_id text PRIMARY KEY,
  owner_user_id text REFERENCES users(user_id) ON DELETE RESTRICT,
  activation_kind text NOT NULL,
  activated_bundle_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text NOT NULL,
  previous_pointer_version bigint NOT NULL,
  new_pointer_version bigint NOT NULL,
  invalidated_owner_bundle_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_activation_receipt_id_check CHECK (activation_receipt_id ~ '^rgr_act_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_activation_receipt_kind_check CHECK (activation_kind IN ('PUBLIC_BASE', 'OWNER_BUNDLE', 'OWNER_DELETE_REPLACEMENT')),
  CONSTRAINT rag_v2_immutable_activation_receipt_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_v2_immutable_activation_receipt_pointer_check CHECK (previous_pointer_version >= 0 AND new_pointer_version = previous_pointer_version + 1),
  CONSTRAINT rag_v2_immutable_activation_receipt_invalidation_check
    CHECK (
      invalidated_owner_bundle_count >= 0
      AND (activation_kind = 'PUBLIC_BASE' OR invalidated_owner_bundle_count = 0)
    ),
  CONSTRAINT rag_v2_immutable_activation_receipt_public_check
    CHECK (
      (activation_kind = 'PUBLIC_BASE' AND owner_user_id IS NULL AND activated_bundle_id IS NULL AND exact30_generation_id IS NOT NULL AND oa112_generation_id IS NOT NULL AND owner_private_generation_id IS NULL)
      OR
      (activation_kind IN ('OWNER_BUNDLE', 'OWNER_DELETE_REPLACEMENT') AND owner_user_id IS NOT NULL AND activated_bundle_id IS NOT NULL AND exact30_generation_id IS NULL AND oa112_generation_id IS NULL AND owner_private_generation_id IS NULL)
    )
);

CREATE TABLE rag_v2_immutable_deletion_receipts (
  deletion_receipt_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  document_id text NOT NULL,
  deletion_kind text NOT NULL,
  replacement_bundle_id text,
  activation_receipt_id text,
  reason_hash text NOT NULL,
  deleted_source_revision_count integer NOT NULL,
  deleted_chunk_count integer NOT NULL,
  deleted_embedding_count integer NOT NULL,
  affected_materialization_run_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_deletion_receipt_id_check CHECK (deletion_receipt_id ~ '^rgr_del_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_deletion_document_check CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_immutable_deletion_kind_check CHECK (deletion_kind IN ('REPLACED_ACTIVE', 'UNREFERENCED_STAGING', 'UNMATERIALIZED_RUN')),
  CONSTRAINT rag_v2_immutable_deletion_replacement_check
    CHECK (
      (deletion_kind = 'REPLACED_ACTIVE'
        AND replacement_bundle_id ~ '^rgb_[0-9a-f]{32}$'
        AND activation_receipt_id ~ '^rgr_act_[0-9a-f]{32}$')
      OR
      (deletion_kind IN ('UNREFERENCED_STAGING', 'UNMATERIALIZED_RUN')
        AND replacement_bundle_id IS NULL
        AND activation_receipt_id IS NULL)
    ),
  CONSTRAINT rag_v2_immutable_deletion_hash_check CHECK (reason_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_deletion_affected_run_count_check CHECK (affected_materialization_run_count >= 0),
  CONSTRAINT rag_v2_immutable_deletion_count_check
    CHECK (
      deleted_chunk_count >= 0
      AND deleted_embedding_count >= 0
      AND (
        (deletion_kind = 'UNMATERIALIZED_RUN'
          AND deleted_source_revision_count = 0
          AND deleted_chunk_count = 0
          AND deleted_embedding_count = 0
          AND affected_materialization_run_count >= 1)
        OR
        (deletion_kind <> 'UNMATERIALIZED_RUN' AND deleted_source_revision_count >= 1)
      )
    )
);

-- 동일 owner/document identity는 삭제 완료 뒤 stale run이 재개해 raw-derived graph를 되살리지
-- 못하도록 tombstone으로 닫는다. 재import는 항상 새 document_id를 발급해야 한다.
CREATE TABLE rag_v2_immutable_owner_document_deletion_tombstones (
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  document_id text NOT NULL,
  deletion_receipt_id text NOT NULL REFERENCES rag_v2_immutable_deletion_receipts(deletion_receipt_id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (owner_user_id, document_id),
  CONSTRAINT rag_v2_immutable_owner_document_tombstone_document_check
    CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_immutable_owner_document_tombstone_receipt_check
    CHECK (deletion_receipt_id ~ '^rgr_del_[0-9a-f]{32}$')
);

ALTER TABLE rag_v2_immutable_oa_track_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_oa_track_catalog FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_oa_source_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_oa_source_cards FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_source_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_source_revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_component_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_component_generations FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_generation_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_generation_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_generation_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_generation_embeddings FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_embedding_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_embedding_cache FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_materialization_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_materialization_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_source_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_source_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_chunk_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_chunk_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_embedding_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_embedding_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_public_bundle_pointers ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_public_bundle_pointers FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_bundles FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_owner_bundle_pointers ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_owner_bundle_pointers FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_consent_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_consent_events FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_import_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_import_tickets FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_activation_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_activation_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_deletion_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_deletion_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_owner_document_deletion_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_owner_document_deletion_tombstones FORCE ROW LEVEL SECURITY;

CREATE POLICY rag_v2_immutable_oa_track_catalog_read_policy
  ON rag_v2_immutable_oa_track_catalog
  FOR SELECT
  USING (true);
CREATE POLICY rag_v2_immutable_owner_document_tombstone_internal_read_policy
  ON rag_v2_immutable_owner_document_deletion_tombstones
  FOR SELECT
  USING (current_user = 'flyway');
CREATE POLICY rag_v2_immutable_owner_document_tombstone_delete_insert_policy
  ON rag_v2_immutable_owner_document_deletion_tombstones
  FOR INSERT
  WITH CHECK (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND owner_user_id = current_setting('app.actor_user_id', true)
  );
CREATE POLICY rag_v2_immutable_oa_source_card_read_policy
  ON rag_v2_immutable_oa_source_cards
  FOR SELECT
  USING (true);
-- public pointer는 두 bounded activation function만 읽을 수 있고, write는 public base
-- replacement만 NOT_MATERIALIZED|ACTIVE|FAILED→ACTIVE로 수행할 수 있다.
CREATE POLICY rag_v2_immutable_public_pointer_activation_read_policy
  ON rag_v2_immutable_public_bundle_pointers
  FOR SELECT
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) IN ('public_base_activation', 'owner_bundle_activation')
    AND state_id = 'default'
    AND state IN ('NOT_MATERIALIZED', 'ACTIVE', 'FAILED')
  );
CREATE POLICY rag_v2_immutable_public_pointer_public_base_update_policy
  ON rag_v2_immutable_public_bundle_pointers
  FOR UPDATE
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state_id = 'default'
    AND state IN ('NOT_MATERIALIZED', 'ACTIVE', 'FAILED')
  )
  WITH CHECK (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state_id = 'default'
    AND state = 'ACTIVE'
  );
CREATE POLICY rag_v2_immutable_source_owner_policy
  ON rag_v2_immutable_source_revisions
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_chunk_owner_policy
  ON rag_v2_immutable_chunks
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_component_owner_policy
  ON rag_v2_immutable_component_generations
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_membership_owner_policy
  ON rag_v2_immutable_generation_memberships
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_embedding_owner_policy
  ON rag_v2_immutable_generation_embeddings
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_embedding_cache_owner_policy
  ON rag_v2_immutable_embedding_cache
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_run_owner_policy
  ON rag_v2_immutable_materialization_runs
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_source_receipt_owner_policy
  ON rag_v2_immutable_source_receipts
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_chunk_receipt_owner_policy
  ON rag_v2_immutable_chunk_receipts
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_embedding_receipt_owner_policy
  ON rag_v2_immutable_embedding_receipts
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_bundle_owner_policy
  ON rag_v2_immutable_bundles
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_owner_pointer_policy
  ON rag_v2_immutable_owner_bundle_pointers
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
-- Public base replacement is the sole cross-owner maintenance path. The policy permits only
-- the validated fail-closed invalidation transitions inside its SECURITY DEFINER function;
-- callers still have no table DML grant and cannot turn a custom GUC into a standalone capability.
CREATE POLICY rag_v2_immutable_owner_pointer_public_base_invalidation_policy
  ON rag_v2_immutable_owner_bundle_pointers
  FOR UPDATE
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state = 'READY'
    AND active_bundle_id IS NOT NULL
  )
  WITH CHECK (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state = 'BUILDING'
    AND active_bundle_id IS NULL
  );
CREATE POLICY rag_v2_immutable_owner_pointer_public_base_invalidation_read_policy
  ON rag_v2_immutable_owner_bundle_pointers
  FOR SELECT
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND (
      (state = 'READY' AND active_bundle_id IS NOT NULL)
      OR (state = 'BUILDING' AND active_bundle_id IS NULL)
    )
  );
CREATE POLICY rag_v2_immutable_bundle_public_base_invalidation_policy
  ON rag_v2_immutable_bundles
  FOR UPDATE
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state = 'ACTIVE'
  )
  WITH CHECK (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state = 'SUPERSEDED'
  );
CREATE POLICY rag_v2_immutable_bundle_public_base_invalidation_read_policy
  ON rag_v2_immutable_bundles
  FOR SELECT
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_admin'
    AND current_setting('app.rag_admin_maintenance', true) = 'public_base_activation'
    AND state IN ('ACTIVE', 'SUPERSEDED')
  );
CREATE POLICY rag_v2_immutable_consent_owner_policy
  ON rag_v2_immutable_consent_events
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_ticket_owner_policy
  ON rag_v2_immutable_import_tickets
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_activation_owner_policy
  ON rag_v2_immutable_activation_receipts
  USING (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id IS NULL OR owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_immutable_deletion_owner_policy
  ON rag_v2_immutable_deletion_receipts
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));

-- consent는 append-only다. 어떤 caller도 grant/revoke history를 rewrite하지 못한다.
CREATE FUNCTION reject_rag_v2_immutable_append_only()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reject_rag_v2_immutable_append_only$
BEGIN
  IF session_user <> 'flyway' THEN
    RAISE EXCEPTION 'immutable RAG v2 record cannot be updated or deleted'
      USING ERRCODE = '42501';
  END IF;
  RETURN NULL;
END;
$reject_rag_v2_immutable_append_only$;
ALTER FUNCTION reject_rag_v2_immutable_append_only() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_rag_v2_immutable_append_only() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_consent_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_consent_events
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_append_only();

CREATE TRIGGER rag_v2_immutable_oa_source_card_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_oa_source_cards
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_append_only();

CREATE TRIGGER rag_v2_immutable_oa_track_catalog_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_oa_track_catalog
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_append_only();

-- run과 source revision 양쪽에서 tombstone을 확인해, 삭제된 document_id로 stale resume이나
-- writer 재시작이 IR/text/chunk/vector를 다시 만들지 못하게 한다.
CREATE FUNCTION reject_rag_v2_immutable_deleted_document_reuse()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reject_rag_v2_immutable_deleted_document_reuse$
BEGIN
  IF NEW.owner_user_id IS NOT NULL AND NEW.document_id IS NOT NULL THEN
    -- delete와 모든 owner-document writer는 같은 transaction advisory lock을 먼저 잡는다.
    -- 따라서 delete가 tombstone을 commit하기 전 stale writer가 check를 통과해 재생성할 수 없다.
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(
        'rag-v2-immutable-owner-document|' || NEW.owner_user_id || '|' || NEW.document_id,
        0
      )
    );
    IF EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_owner_document_deletion_tombstones AS tombstone
      WHERE tombstone.owner_user_id = NEW.owner_user_id
        AND tombstone.document_id = NEW.document_id
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner document was deleted and cannot be resumed'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$reject_rag_v2_immutable_deleted_document_reuse$;
ALTER FUNCTION reject_rag_v2_immutable_deleted_document_reuse() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_rag_v2_immutable_deleted_document_reuse() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_owner_document_run_tombstone
BEFORE INSERT OR UPDATE OF owner_user_id, document_id ON rag_v2_immutable_materialization_runs
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_deleted_document_reuse();

CREATE TRIGGER rag_v2_immutable_owner_document_source_tombstone
BEFORE INSERT OR UPDATE OF owner_user_id, document_id ON rag_v2_immutable_source_revisions
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_deleted_document_reuse();

CREATE TRIGGER rag_v2_immutable_owner_document_tombstone_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_owner_document_deletion_tombstones
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_append_only();

CREATE FUNCTION record_rag_v2_immutable_consent(
  p_owner_user_id text,
  p_consent_event_id text,
  p_action text,
  p_disclosure_digest text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_rag_v2_immutable_consent$
DECLARE
  recorded_at timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_consent_event_id !~ '^cns_v2_[0-9a-f]{32}$'
     OR p_action NOT IN ('GRANT', 'REVOKE')
     OR p_disclosure_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 consent arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-consent|' || p_owner_user_id, 0)
  );
  INSERT INTO public.rag_v2_immutable_consent_events (
    consent_event_id,
    owner_user_id,
    action,
    policy_version,
    disclosure_digest,
    created_at
  )
  VALUES (
    p_consent_event_id,
    p_owner_user_id,
    p_action,
    'EXTERNAL_AI_RAG_V2',
    p_disclosure_digest,
    recorded_at
  );
  RETURN recorded_at;
END;
$record_rag_v2_immutable_consent$;
ALTER FUNCTION record_rag_v2_immutable_consent(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_immutable_consent(text, text, text, text) FROM PUBLIC;

CREATE FUNCTION issue_rag_v2_immutable_import_ticket(
  p_owner_user_id text,
  p_ticket_id text,
  p_operation text,
  p_policy_version text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_immutable_import_ticket$
DECLARE
  issued_at timestamptz := clock_timestamp();
  ticket_digest text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_operation <> 'OWNER_IMPORT'
     OR p_policy_version <> 'RAG_V2_OWNER_DOCUMENT_V1'
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 import ticket arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-ticket|' || ticket_digest, 0)
  );
  INSERT INTO public.rag_v2_immutable_import_tickets (
    ticket_hash,
    owner_user_id,
    operation,
    policy_version,
    state,
    issued_at,
    expires_at
  )
  VALUES (
    ticket_digest,
    p_owner_user_id,
    p_operation,
    p_policy_version,
    'ISSUED',
    issued_at,
    issued_at + interval '5 minutes'
  );
  RETURN issued_at + interval '5 minutes';
END;
$issue_rag_v2_immutable_import_ticket$;
ALTER FUNCTION issue_rag_v2_immutable_import_ticket(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_immutable_import_ticket(text, text, text, text) FROM PUBLIC;

CREATE FUNCTION consume_rag_v2_immutable_import_ticket(
  p_owner_user_id text,
  p_ticket_id text,
  p_operation text,
  p_policy_version text,
  p_materialization_run_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $consume_rag_v2_immutable_import_ticket$
DECLARE
  ticket_digest text;
  consumed boolean := false;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_operation <> 'OWNER_IMPORT'
     OR p_policy_version <> 'RAG_V2_OWNER_DOCUMENT_V1'
     OR p_materialization_run_id !~ '^rgr_run_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 import ticket consume arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-ticket|' || ticket_digest, 0)
  );
  -- writer는 owner/JWT를 보유하지 않으며, one-time capability가 owner scope를 재검증한 뒤에만 actor를 좁힌다.
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_materialization_runs AS run
    JOIN public.users AS owner
      ON owner.user_id = run.owner_user_id
    WHERE run.materialization_run_id = p_materialization_run_id
      AND run.owner_user_id = p_owner_user_id
      AND run.component_scope = 'OWNER_PRIVATE'
      AND run.state IN ('OPEN', 'STAGED')
      AND owner.status = 'ACTIVE'
  ) THEN
    RETURN false;
  END IF;
  UPDATE public.rag_v2_immutable_import_tickets
  SET state = 'CONSUMED',
      consumed_at = clock_timestamp(),
      consumer_run_id = p_materialization_run_id
  WHERE ticket_hash = ticket_digest
    AND owner_user_id = p_owner_user_id
    AND operation = p_operation
    AND policy_version = p_policy_version
    AND state = 'ISSUED'
    AND consumed_at IS NULL
    AND expires_at > statement_timestamp()
  RETURNING true INTO consumed;
  RETURN coalesce(consumed, false);
END;
$consume_rag_v2_immutable_import_ticket$;
ALTER FUNCTION consume_rag_v2_immutable_import_ticket(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION consume_rag_v2_immutable_import_ticket(text, text, text, text, text) FROM PUBLIC;

CREATE FUNCTION activate_rag_v2_immutable_public_base(
  p_exact30_generation_id text,
  p_oa112_generation_id text,
  p_expected_pointer_version bigint,
  p_activation_receipt_id text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_rag_v2_immutable_public_base$
DECLARE
  pointer_record public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  exact_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  oa_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  exact_source_count bigint;
  exact_source_revision_count bigint;
  exact_chunk_count bigint;
  exact_embedding_count bigint;
  oa_chunk_count bigint;
  oa_embedding_count bigint;
  invalidated_owner_bundle_count integer := 0;
  superseded_owner_bundle_count integer := 0;
  activation_timestamp timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_exact30_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_oa112_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_exact30_generation_id = p_oa112_generation_id
     OR p_expected_pointer_version < 1
     OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 public activation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- public pointer RLS는 이 definer 호출의 bounded activation state만 읽고 바꾸도록 닫는다.
  PERFORM set_config('app.rag_admin_maintenance', 'public_base_activation', true);
  -- public/owner activation은 같은 advisory lock을 먼저 잡아 서로 반대인 pointer lock 순서의 deadlock을 막는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  SELECT *
  INTO pointer_record
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default'
  FOR UPDATE;
  IF NOT FOUND
     OR pointer_record.pointer_version IS DISTINCT FROM p_expected_pointer_version
     OR pointer_record.state NOT IN ('NOT_MATERIALIZED', 'ACTIVE', 'FAILED') THEN
    RAISE EXCEPTION 'immutable RAG v2 public pointer CAS failed'
      USING ERRCODE = '40001';
  END IF;
  IF pointer_record.state = 'ACTIVE'
     AND pointer_record.exact30_generation_id IS NOT DISTINCT FROM p_exact30_generation_id
     AND pointer_record.oa112_generation_id IS NOT DISTINCT FROM p_oa112_generation_id THEN
    RAISE EXCEPTION 'immutable RAG v2 public base is already active'
      USING ERRCODE = '23514';
  END IF;

  SELECT * INTO exact_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = p_exact30_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 exact-30 component was not found'
      USING ERRCODE = '23514';
  END IF;
  SELECT * INTO oa_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = p_oa112_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 component was not found'
      USING ERRCODE = '23514';
  END IF;
  IF exact_generation.component_scope <> 'EXACT30'
     OR exact_generation.owner_user_id IS NOT NULL
     OR exact_generation.state <> 'EVALUATED'
     OR exact_generation.evaluation_status <> 'PASSED'
     OR oa_generation.component_scope <> 'OA112'
     OR oa_generation.owner_user_id IS NOT NULL
     OR oa_generation.state <> 'EVALUATED'
     OR oa_generation.evaluation_status <> 'PASSED'
     OR exact_generation.embedding_profile_id IS DISTINCT FROM oa_generation.embedding_profile_id THEN
    RAISE EXCEPTION 'immutable RAG v2 public component is not activation ready'
      USING ERRCODE = '23514';
  END IF;

  SELECT
    COUNT(DISTINCT source.source_id),
    COUNT(DISTINCT membership.source_revision_id),
    COUNT(membership.chunk_id),
    COUNT(embedding.chunk_id)
  INTO exact_source_count, exact_source_revision_count, exact_chunk_count, exact_embedding_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  LEFT JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
  JOIN public.rag_v2_immutable_source_revisions AS source
    ON source.source_revision_id = membership.source_revision_id
  WHERE membership.component_generation_id = p_exact30_generation_id
    AND membership.component_scope = 'EXACT30'
    AND source.source_scope = 'EXACT30'
    AND source.owner_user_id IS NULL
    AND NOT source.reserve_source;
  IF exact_source_count <> 30
     OR exact_source_revision_count <> exact_source_count
     OR exact_chunk_count <> exact_generation.expected_chunk_count
     OR exact_embedding_count <> exact_generation.expected_chunk_count
     OR exact_generation.actual_source_count <> exact_source_count
     OR exact_generation.actual_chunk_count <> exact_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 exact-30 component cardinality is invalid'
      USING ERRCODE = '23514';
  END IF;

  IF (
    SELECT COUNT(DISTINCT source.source_id) <> 112
        OR COUNT(DISTINCT membership.source_revision_id) <> 112
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE membership.component_generation_id = p_oa112_generation_id
      AND membership.component_scope = 'OA112'
      AND source.source_scope = 'OA112'
      AND source.owner_user_id IS NULL
      AND source.reserve_source = false
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 active source count is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_oa_track_catalog AS track
    LEFT JOIN (
      SELECT
        source.oa_track_id,
        COUNT(DISTINCT source.source_id) AS active_source_count,
        COUNT(DISTINCT membership.source_revision_id) AS active_revision_count
      FROM public.rag_v2_immutable_generation_memberships AS membership
      JOIN public.rag_v2_immutable_source_revisions AS source
        ON source.source_revision_id = membership.source_revision_id
      WHERE membership.component_generation_id = p_oa112_generation_id
        AND membership.component_scope = 'OA112'
        AND source.source_scope = 'OA112'
        AND source.owner_user_id IS NULL
        AND source.reserve_source = false
      GROUP BY source.oa_track_id
    ) AS track_counts
      ON track_counts.oa_track_id = track.track_id
    WHERE coalesce(track_counts.active_source_count, 0) <> track.required_active_source_count
       OR coalesce(track_counts.active_revision_count, 0) <> track.required_active_source_count
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 track distribution is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF (
    SELECT COUNT(*) > 28
    FROM public.rag_v2_immutable_source_revisions AS source
    WHERE source.source_scope = 'OA112'
      AND source.owner_user_id IS NULL
      AND source.reserve_source = true
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 reserve limit is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE membership.component_generation_id = p_oa112_generation_id
      AND (
        membership.component_scope <> 'OA112'
        OR source.source_scope <> 'OA112'
        OR source.owner_user_id IS NOT NULL
        OR source.reserve_source
        OR NOT source.machine_fetch_allowed
        OR NOT source.local_processing_allowed
        OR NOT source.external_embedding_allowed
        OR NOT source.external_generation_allowed
      )
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 rights or reserve gate is invalid'
      USING ERRCODE = '23514';
  END IF;
  -- A logical 14×8 count is not sufficient evidence for a physical OA activation. Every active
  -- membership must bind to one closed, hash-verified source-card v4 record with matching rights.
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    LEFT JOIN public.rag_v2_immutable_oa_source_cards AS card
      ON card.source_revision_id = source.source_revision_id
       AND card.source_scope = source.source_scope
    WHERE membership.component_generation_id = p_oa112_generation_id
      AND (
        card.source_revision_id IS NULL
        OR card.source_id IS DISTINCT FROM source.source_id
        OR card.raw_content_sha256 IS DISTINCT FROM source.raw_content_sha256
        OR card.canonical_https_url IS DISTINCT FROM source.canonical_https_url
        OR card.license_evidence_sha256 IS DISTINCT FROM source.license_evidence_sha256
        OR card.access_evidence_sha256 IS DISTINCT FROM source.access_evidence_sha256
        OR card.mime_type IS DISTINCT FROM source.mime_type
        OR card.machine_fetch_allowed IS DISTINCT FROM source.machine_fetch_allowed
        OR card.local_processing_allowed IS DISTINCT FROM source.local_processing_allowed
        OR card.external_embedding_allowed IS DISTINCT FROM source.external_embedding_allowed
        OR card.external_generation_allowed IS DISTINCT FROM source.external_generation_allowed
        OR card.active_oa112_eligible = false
        OR card.access_verification_state <> 'VERIFIED'
      )
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 source-card evidence gate is invalid'
      USING ERRCODE = '23514';
  END IF;
  SELECT COUNT(membership.chunk_id), COUNT(embedding.chunk_id)
  INTO oa_chunk_count, oa_embedding_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  LEFT JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
  WHERE membership.component_generation_id = p_oa112_generation_id;
  IF oa_chunk_count <> oa_generation.expected_chunk_count
     OR oa_embedding_count <> oa_generation.expected_chunk_count
     OR oa_generation.actual_source_count <> 112
     OR oa_generation.actual_chunk_count <> oa_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 OA112 component materialization is incomplete'
      USING ERRCODE = '23514';
  END IF;
  IF exact_generation.embedding_profile_id = 'voyage_context_4_1024_v1'
     AND EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       JOIN public.rag_v2_immutable_source_revisions AS source
         ON source.source_revision_id = membership.source_revision_id
       WHERE membership.component_generation_id IN (p_exact30_generation_id, p_oa112_generation_id)
         AND NOT source.external_processing_eligible
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 Voyage source safety gate is invalid'
      USING ERRCODE = '23514';
  END IF;

  -- 새 public base는 기존 owner bundle의 두 public component와 섞일 수 없다.
  -- owner evidence를 삭제하지 않고 pointer를 BUILDING으로 fail-close하여 whole-bundle 재생성을 강제한다.
  -- FORCE RLS hides every foreign owner's row by default. This transaction-local marker opens
  -- only the two typed READY→BUILDING / ACTIVE→SUPERSEDED transitions declared above.
  WITH eligible_owner_pointers AS (
    SELECT active_bundle_id
    FROM public.rag_v2_immutable_owner_bundle_pointers
    WHERE state = 'READY'
      AND active_bundle_id IS NOT NULL
  ), superseded_owner_bundles AS (
    UPDATE public.rag_v2_immutable_bundles AS bundle
    SET state = 'SUPERSEDED'
    FROM eligible_owner_pointers AS eligible
    WHERE bundle.bundle_id = eligible.active_bundle_id
      AND bundle.state = 'ACTIVE'
    RETURNING bundle.bundle_id
  ), invalidated_owner_pointers AS (
    UPDATE public.rag_v2_immutable_owner_bundle_pointers
    SET state = 'BUILDING',
        active_bundle_id = NULL,
        bundle_version = bundle_version + 1,
        updated_at = activation_timestamp
    WHERE state = 'READY'
      AND active_bundle_id IS NOT NULL
    RETURNING owner_user_id
  )
  SELECT
    (SELECT COUNT(*) FROM invalidated_owner_pointers),
    (SELECT COUNT(*) FROM superseded_owner_bundles)
  INTO invalidated_owner_bundle_count, superseded_owner_bundle_count;
  IF invalidated_owner_bundle_count <> superseded_owner_bundle_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner invalidation state is inconsistent'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.rag_v2_immutable_component_generations
  SET state = 'SUPERSEDED'
  WHERE component_generation_id IN (pointer_record.exact30_generation_id, pointer_record.oa112_generation_id)
    AND state = 'ACTIVE';
  UPDATE public.rag_v2_immutable_component_generations
  SET state = 'ACTIVE', activated_at = activation_timestamp
  WHERE component_generation_id IN (p_exact30_generation_id, p_oa112_generation_id);
  UPDATE public.rag_v2_immutable_public_bundle_pointers
  SET state = 'ACTIVE',
      exact30_generation_id = p_exact30_generation_id,
      oa112_generation_id = p_oa112_generation_id,
      embedding_profile_id = exact_generation.embedding_profile_id,
      pointer_version = pointer_record.pointer_version + 1,
      updated_at = activation_timestamp
  WHERE state_id = 'default';
  INSERT INTO public.rag_v2_immutable_activation_receipts (
    activation_receipt_id,
    owner_user_id,
    activation_kind,
    activated_bundle_id,
    exact30_generation_id,
    oa112_generation_id,
    owner_private_generation_id,
    embedding_profile_id,
    previous_pointer_version,
    new_pointer_version,
    invalidated_owner_bundle_count,
    created_at
  )
  VALUES (
    p_activation_receipt_id,
    NULL,
    'PUBLIC_BASE',
    NULL,
    p_exact30_generation_id,
    p_oa112_generation_id,
    NULL,
    exact_generation.embedding_profile_id,
    pointer_record.pointer_version,
    pointer_record.pointer_version + 1,
    invalidated_owner_bundle_count,
    activation_timestamp
  );
  PERFORM set_config('app.rag_admin_maintenance', '', true);
  RETURN pointer_record.pointer_version + 1;
END;
$activate_rag_v2_immutable_public_base$;
ALTER FUNCTION activate_rag_v2_immutable_public_base(text, text, bigint, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION activate_rag_v2_immutable_public_base(text, text, bigint, text) FROM PUBLIC;

CREATE FUNCTION activate_rag_v2_immutable_owner_bundle(
  p_owner_user_id text,
  p_bundle_id text,
  p_expected_active_bundle_id text,
  p_expected_bundle_version bigint,
  p_activation_receipt_id text,
  p_activation_kind text DEFAULT 'OWNER_BUNDLE'
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_rag_v2_immutable_owner_bundle$
DECLARE
  pointer_record public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  bundle_record public.rag_v2_immutable_bundles%ROWTYPE;
  owner_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  owner_source_count bigint;
  owner_chunk_count bigint;
  owner_embedding_count bigint;
  activation_timestamp timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_bundle_id !~ '^rgb_[0-9a-f]{32}$'
     OR (p_expected_active_bundle_id IS NOT NULL AND p_expected_active_bundle_id !~ '^rgb_[0-9a-f]{32}$')
     OR p_expected_bundle_version < 0
     OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$'
     OR p_activation_kind NOT IN ('OWNER_BUNDLE', 'OWNER_DELETE_REPLACEMENT')
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner bundle activation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- owner bundle activation도 public pointer를 읽기만 하므로 별도 transaction-local capability로 제한한다.
  PERFORM set_config('app.rag_admin_maintenance', 'owner_bundle_activation', true);
  -- public base replacement과 같은 lock을 사용해 owner→public pointer lock 순서를 직렬화한다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  INSERT INTO public.rag_v2_immutable_owner_bundle_pointers (
    owner_user_id, state, active_bundle_id, bundle_version
  )
  VALUES (p_owner_user_id, 'ABSENT', NULL, 0)
  ON CONFLICT (owner_user_id) DO NOTHING;
  SELECT * INTO pointer_record
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF pointer_record.bundle_version IS DISTINCT FROM p_expected_bundle_version
     OR pointer_record.active_bundle_id IS DISTINCT FROM p_expected_active_bundle_id THEN
    RAISE EXCEPTION 'immutable RAG v2 owner pointer CAS failed'
      USING ERRCODE = '40001';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default';
  -- 동일 advisory lock 아래에서 읽으므로 owner activation이 public pointer row lock까지 획득할 필요는 없다.
  -- 이로써 owner marker에는 public pointer UPDATE RLS capability를 부여하지 않는다.
  IF NOT FOUND OR public_pointer.state <> 'ACTIVE' THEN
    RAISE EXCEPTION 'immutable RAG v2 public base is not active'
      USING ERRCODE = '23514';
  END IF;

  SELECT * INTO bundle_record
  FROM public.rag_v2_immutable_bundles
  WHERE bundle_id = p_bundle_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR bundle_record.state <> 'EVALUATED'
     OR bundle_record.evaluation_status <> 'PASSED'
     OR bundle_record.exact30_generation_id IS DISTINCT FROM public_pointer.exact30_generation_id
     OR bundle_record.oa112_generation_id IS DISTINCT FROM public_pointer.oa112_generation_id
     OR bundle_record.embedding_profile_id IS DISTINCT FROM public_pointer.embedding_profile_id THEN
    RAISE EXCEPTION 'immutable RAG v2 bundle is not eligible for the active public base'
      USING ERRCODE = '23514';
  END IF;
  SELECT * INTO owner_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = bundle_record.owner_private_generation_id
  FOR UPDATE;
  SELECT
    COUNT(DISTINCT membership.source_revision_id),
    COUNT(membership.chunk_id),
    COUNT(embedding.chunk_id)
  INTO owner_source_count, owner_chunk_count, owner_embedding_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  LEFT JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
  WHERE membership.component_generation_id = bundle_record.owner_private_generation_id;
  IF NOT FOUND
     OR owner_generation.component_scope <> 'OWNER_PRIVATE'
     OR owner_generation.owner_user_id IS DISTINCT FROM p_owner_user_id
     OR owner_generation.embedding_profile_id IS DISTINCT FROM bundle_record.embedding_profile_id
     OR owner_generation.state <> 'EVALUATED'
     OR owner_generation.evaluation_status <> 'PASSED'
     OR owner_generation.expected_source_count <> owner_source_count
     OR owner_generation.expected_chunk_count <> owner_chunk_count
     OR owner_generation.actual_source_count <> owner_source_count
     OR owner_generation.actual_chunk_count <> owner_chunk_count
     OR owner_embedding_count <> owner_chunk_count
     OR EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       JOIN public.rag_v2_immutable_source_revisions AS source
         ON source.source_revision_id = membership.source_revision_id
       WHERE membership.component_generation_id = owner_generation.component_generation_id
         AND (
           membership.component_scope <> 'OWNER_PRIVATE'
           OR membership.owner_user_id IS DISTINCT FROM p_owner_user_id
           OR source.source_scope <> 'OWNER_PRIVATE'
           OR source.owner_user_id IS DISTINCT FROM p_owner_user_id
     )
     OR EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_embeddings AS embedding
       WHERE embedding.component_generation_id = owner_generation.component_generation_id
         AND (
           embedding.component_scope <> 'OWNER_PRIVATE'
           OR embedding.owner_user_id IS DISTINCT FROM p_owner_user_id
           OR embedding.embedding_profile_id IS DISTINCT FROM owner_generation.embedding_profile_id
         )
     )
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner component scope is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF bundle_record.embedding_profile_id = 'voyage_context_4_1024_v1'
     AND (
       EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_generation_memberships AS membership
         JOIN public.rag_v2_immutable_source_revisions AS source
           ON source.source_revision_id = membership.source_revision_id
         WHERE membership.component_generation_id = owner_generation.component_generation_id
           AND NOT source.external_processing_eligible
       )
       OR coalesce((
         SELECT consent.action = 'GRANT'
         FROM public.rag_v2_immutable_consent_events AS consent
         WHERE consent.owner_user_id = p_owner_user_id
           AND consent.policy_version = 'EXTERNAL_AI_RAG_V2'
         ORDER BY consent.created_at DESC, consent.consent_event_id DESC
         LIMIT 1
       ), false) = false
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 Voyage owner consent or safety gate is invalid'
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.rag_v2_immutable_bundles
  SET state = 'SUPERSEDED'
  WHERE bundle_id = pointer_record.active_bundle_id
    AND state = 'ACTIVE';
  UPDATE public.rag_v2_immutable_bundles
  SET state = 'ACTIVE', activated_at = activation_timestamp
  WHERE bundle_id = p_bundle_id;
  UPDATE public.rag_v2_immutable_owner_bundle_pointers
  SET state = 'READY',
      active_bundle_id = p_bundle_id,
      bundle_version = pointer_record.bundle_version + 1,
      updated_at = activation_timestamp
  WHERE owner_user_id = p_owner_user_id;
  INSERT INTO public.rag_v2_immutable_activation_receipts (
    activation_receipt_id,
    owner_user_id,
    activation_kind,
    activated_bundle_id,
    exact30_generation_id,
    oa112_generation_id,
    owner_private_generation_id,
    embedding_profile_id,
    previous_pointer_version,
    new_pointer_version,
    created_at
  )
  VALUES (
    p_activation_receipt_id,
    p_owner_user_id,
    p_activation_kind,
    p_bundle_id,
    NULL,
    NULL,
    NULL,
    bundle_record.embedding_profile_id,
    pointer_record.bundle_version,
    pointer_record.bundle_version + 1,
    activation_timestamp
  );
  PERFORM set_config('app.rag_admin_maintenance', '', true);
  RETURN pointer_record.bundle_version + 1;
END;
$activate_rag_v2_immutable_owner_bundle$;
ALTER FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) FROM PUBLIC;

CREATE FUNCTION delete_rag_v2_immutable_owner_document(
  p_owner_user_id text,
  p_document_id text,
  p_replacement_bundle_id text,
  p_expected_active_bundle_id text,
  p_expected_bundle_version bigint,
  p_activation_receipt_id text,
  p_deletion_receipt_id text,
  p_reason_hash text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $delete_rag_v2_immutable_owner_document$
DECLARE
  target_source_count integer;
  target_chunk_count integer;
  target_embedding_count integer;
  target_run_count integer;
  target_source_revision_ids text[] := ARRAY[]::text[];
  target_chunk_ids text[] := ARRAY[]::text[];
  target_generation_ids text[] := ARRAY[]::text[];
  is_replaced_active boolean;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR p_deletion_receipt_id !~ '^rgr_del_[0-9a-f]{32}$'
     OR p_reason_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 document deletion arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  is_replaced_active := p_replacement_bundle_id IS NOT NULL
    OR p_expected_active_bundle_id IS NOT NULL
    OR p_expected_bundle_version IS NOT NULL
    OR p_activation_receipt_id IS NOT NULL;
  IF is_replaced_active
     AND (
       p_replacement_bundle_id IS NULL
       OR p_expected_active_bundle_id IS NULL
       OR p_expected_bundle_version IS NULL
       OR p_activation_receipt_id IS NULL
       OR p_replacement_bundle_id !~ '^rgb_[0-9a-f]{32}$'
       OR p_expected_active_bundle_id !~ '^rgb_[0-9a-f]{32}$'
       OR p_expected_bundle_version < 1
       OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 deletion mode arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- owner document writer가 source/run INSERT 뒤 activation까지 같은 transaction에서 진행해도
  -- deadlock이 나지 않도록 document lock을 먼저, global activation lock을 다음에 잡는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-owner-document|' || p_owner_user_id || '|' || p_document_id,
      0
    )
  );
  -- deletion와 public/owner activation은 같은 lock을 공유해 stale pointer와 partial hard-delete를 막는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  SELECT
    COUNT(*)::integer,
    coalesce(array_agg(source.source_revision_id ORDER BY source.source_revision_id), ARRAY[]::text[])
  INTO target_source_count, target_source_revision_ids
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.owner_user_id = p_owner_user_id
    AND source.source_scope = 'OWNER_PRIVATE'
    AND source.document_id = p_document_id;
  SELECT COUNT(*)::integer
  INTO target_run_count
  FROM public.rag_v2_immutable_materialization_runs AS run
  WHERE run.owner_user_id = p_owner_user_id
    AND run.component_scope = 'OWNER_PRIVATE'
    AND run.document_id = p_document_id;
  IF target_source_count = 0 THEN
    IF target_run_count = 0 THEN
      RETURN false;
    END IF;
    IF is_replaced_active THEN
      RAISE EXCEPTION 'immutable RAG v2 replacement deletion requires a materialized document'
        USING ERRCODE = '23514';
    END IF;
    -- 첫 source가 아직 없더라도 owner가 삭제를 요청한 run은 재개 불가 상태와 tombstone으로
    -- 닫는다. 그렇지 않으면 stale writer가 첫 source를 뒤늦게 삽입해 document를 되살릴 수 있다.
    UPDATE public.rag_v2_immutable_materialization_runs
    SET component_generation_id = NULL,
        state = CASE WHEN state IN ('OPEN', 'STAGED') THEN 'FAILED' ELSE state END,
        failure_code = CASE WHEN state IN ('OPEN', 'STAGED') THEN 'OWNER_DELETED' ELSE failure_code END,
        completed_at = CASE WHEN state IN ('OPEN', 'STAGED') THEN clock_timestamp() ELSE completed_at END
    WHERE owner_user_id = p_owner_user_id
      AND component_scope = 'OWNER_PRIVATE'
      AND document_id = p_document_id;
    INSERT INTO public.rag_v2_immutable_deletion_receipts (
      deletion_receipt_id,
      owner_user_id,
      document_id,
      deletion_kind,
      replacement_bundle_id,
      activation_receipt_id,
      reason_hash,
      deleted_source_revision_count,
      deleted_chunk_count,
      deleted_embedding_count,
      affected_materialization_run_count
    )
    VALUES (
      p_deletion_receipt_id,
      p_owner_user_id,
      p_document_id,
      'UNMATERIALIZED_RUN',
      NULL,
      NULL,
      p_reason_hash,
      0,
      0,
      0,
      target_run_count
    );
    INSERT INTO public.rag_v2_immutable_owner_document_deletion_tombstones (
      owner_user_id,
      document_id,
      deletion_receipt_id
    )
    VALUES (
      p_owner_user_id,
      p_document_id,
      p_deletion_receipt_id
    );
    RETURN true;
  END IF;

  SELECT
    COUNT(*)::integer,
    coalesce(array_agg(chunk.chunk_id ORDER BY chunk.chunk_id), ARRAY[]::text[])
  INTO target_chunk_count, target_chunk_ids
  FROM public.rag_v2_immutable_chunks AS chunk
  WHERE chunk.owner_user_id = p_owner_user_id
    AND chunk.source_scope = 'OWNER_PRIVATE'
    AND chunk.source_revision_id = ANY(target_source_revision_ids);

  SELECT coalesce(
    array_agg(DISTINCT candidate.component_generation_id)
      FILTER (WHERE candidate.component_generation_id IS NOT NULL),
    ARRAY[]::text[]
  )
  INTO target_generation_ids
  FROM (
    SELECT membership.component_generation_id
    FROM public.rag_v2_immutable_generation_memberships AS membership
    WHERE membership.owner_user_id = p_owner_user_id
      AND membership.component_scope = 'OWNER_PRIVATE'
      AND membership.source_revision_id = ANY(target_source_revision_ids)
    UNION
    SELECT run.component_generation_id
    FROM public.rag_v2_immutable_source_receipts AS receipt
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.materialization_run_id = receipt.materialization_run_id
     AND run.owner_user_id = receipt.owner_user_id
    WHERE receipt.owner_user_id = p_owner_user_id
      AND receipt.source_scope = 'OWNER_PRIVATE'
      AND receipt.source_revision_id = ANY(target_source_revision_ids)
    UNION
    SELECT run.component_generation_id
    FROM public.rag_v2_immutable_chunk_receipts AS receipt
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.materialization_run_id = receipt.materialization_run_id
     AND run.owner_user_id = receipt.owner_user_id
    WHERE receipt.owner_user_id = p_owner_user_id
      AND receipt.source_scope = 'OWNER_PRIVATE'
      AND (
        receipt.source_revision_id = ANY(target_source_revision_ids)
        OR receipt.chunk_id = ANY(target_chunk_ids)
      )
  ) AS candidate;

  IF is_replaced_active AND EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_bundles AS bundle
    JOIN public.rag_v2_immutable_generation_memberships AS membership
      ON membership.component_generation_id = bundle.owner_private_generation_id
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE bundle.bundle_id = p_replacement_bundle_id
      AND bundle.owner_user_id = p_owner_user_id
      AND source.owner_user_id = p_owner_user_id
      AND source.document_id = p_document_id
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 replacement bundle still contains the deleted document'
      USING ERRCODE = '23514';
  END IF;
  -- deletion은 old component generation 전체를 hard-delete한다. 따라서 replacement는 삭제 대상이
  -- 아닌 기존 membership을 빠짐없이 다시 가리켜야 하며, 한 문서 삭제로 다른 owner 문서가 검색에서
  -- 사라지는 partial replacement를 허용하지 않는다.
  IF is_replaced_active AND EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_generation_memberships AS surviving_membership
    JOIN public.rag_v2_immutable_source_revisions AS surviving_source
      ON surviving_source.source_revision_id = surviving_membership.source_revision_id
    WHERE surviving_membership.component_generation_id = ANY(target_generation_ids)
      AND surviving_membership.owner_user_id = p_owner_user_id
      AND surviving_membership.component_scope = 'OWNER_PRIVATE'
      AND surviving_source.owner_user_id = p_owner_user_id
      AND surviving_source.source_scope = 'OWNER_PRIVATE'
      AND surviving_source.document_id <> p_document_id
      AND NOT EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_bundles AS replacement_bundle
        JOIN public.rag_v2_immutable_generation_memberships AS replacement_membership
          ON replacement_membership.component_generation_id = replacement_bundle.owner_private_generation_id
        WHERE replacement_bundle.bundle_id = p_replacement_bundle_id
          AND replacement_bundle.owner_user_id = p_owner_user_id
          AND replacement_membership.owner_user_id = p_owner_user_id
          AND replacement_membership.component_scope = 'OWNER_PRIVATE'
          AND replacement_membership.source_revision_id = surviving_membership.source_revision_id
          AND replacement_membership.chunk_id = surviving_membership.chunk_id
      )
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 replacement bundle omits a surviving owner document membership'
      USING ERRCODE = '23514';
  END IF;
  IF is_replaced_active THEN
    -- CAS activation succeeds atomically with deletion; any later error rolls the pointer back with the old artifacts intact.
    PERFORM activate_rag_v2_immutable_owner_bundle(
      p_owner_user_id,
      p_replacement_bundle_id,
      p_expected_active_bundle_id,
      p_expected_bundle_version,
      p_activation_receipt_id,
      'OWNER_DELETE_REPLACEMENT'
    );
  ELSIF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_bundles AS bundle
    WHERE bundle.owner_user_id = p_owner_user_id
      AND bundle.owner_private_generation_id = ANY(target_generation_ids)
  )
  OR EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_component_generations AS generation
    WHERE generation.component_generation_id = ANY(target_generation_ids)
      AND (
        generation.owner_user_id IS DISTINCT FROM p_owner_user_id
        OR generation.component_scope <> 'OWNER_PRIVATE'
        OR generation.state NOT IN ('STAGING', 'FAILED')
      )
  )
  OR EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE membership.component_generation_id = ANY(target_generation_ids)
      AND source.document_id IS DISTINCT FROM p_document_id
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 staged deletion still has a bundle, active generation, or foreign document reference'
      USING ERRCODE = '23514';
  END IF;

  SELECT COUNT(*) INTO target_embedding_count
  FROM public.rag_v2_immutable_generation_embeddings
  WHERE component_generation_id = ANY(target_generation_ids);

  -- replacement pointer activation has completed above. 어느 mode든 active pointer가 삭제 대상
  -- generation을 가리키면 hard-delete 전에 중단해 active evidence를 남기거나 끊지 않는다.
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
    JOIN public.rag_v2_immutable_bundles AS bundle
      ON bundle.bundle_id = pointer.active_bundle_id
     AND bundle.owner_user_id = pointer.owner_user_id
    WHERE pointer.owner_user_id = p_owner_user_id
      AND bundle.owner_private_generation_id = ANY(target_generation_ids)
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner pointer still references a deletion target'
      USING ERRCODE = '23514';
  END IF;

  -- replacement mode는 old bundle을, staging mode는 앞의 unreferenced check를 통과한 zero bundle을
  -- 제거한다. 이후 receipt/run은 hash ledger로 남기되 subject identity만 NULL로 scrub한다.
  DELETE FROM public.rag_v2_immutable_bundles
  WHERE owner_user_id = p_owner_user_id
    AND owner_private_generation_id = ANY(target_generation_ids);
  DELETE FROM public.rag_v2_immutable_embedding_cache
  WHERE owner_user_id = p_owner_user_id
    AND source_revision_id = ANY(target_source_revision_ids);
  UPDATE public.rag_v2_immutable_embedding_receipts
  SET component_generation_id = NULL,
      chunk_id = NULL
  WHERE owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE'
    AND (
      component_generation_id = ANY(target_generation_ids)
      OR chunk_id = ANY(target_chunk_ids)
    );
  UPDATE public.rag_v2_immutable_chunk_receipts
  SET source_revision_id = NULL,
      chunk_id = NULL
  WHERE owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE'
    AND (
      source_revision_id = ANY(target_source_revision_ids)
      OR chunk_id = ANY(target_chunk_ids)
    );
  UPDATE public.rag_v2_immutable_source_receipts
  SET source_revision_id = NULL
  WHERE owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE'
    AND source_revision_id = ANY(target_source_revision_ids);
  UPDATE public.rag_v2_immutable_materialization_runs
  SET component_generation_id = NULL,
      state = CASE WHEN state IN ('OPEN', 'STAGED') THEN 'FAILED' ELSE state END,
      failure_code = CASE WHEN state IN ('OPEN', 'STAGED') THEN 'OWNER_DELETED' ELSE failure_code END,
      completed_at = CASE WHEN state IN ('OPEN', 'STAGED') THEN clock_timestamp() ELSE completed_at END
  WHERE owner_user_id = p_owner_user_id
    AND component_scope = 'OWNER_PRIVATE'
    AND (
      component_generation_id = ANY(target_generation_ids)
      OR document_id = p_document_id
    );
  DELETE FROM public.rag_v2_immutable_generation_embeddings
  WHERE component_generation_id = ANY(target_generation_ids);
  DELETE FROM public.rag_v2_immutable_generation_memberships
  WHERE component_generation_id = ANY(target_generation_ids);
  DELETE FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = ANY(target_generation_ids);
  DELETE FROM public.rag_v2_immutable_chunks
  WHERE owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE'
    AND source_revision_id = ANY(target_source_revision_ids);
  DELETE FROM public.rag_v2_immutable_source_revisions
  WHERE owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE'
    AND document_id = p_document_id;
  INSERT INTO public.rag_v2_immutable_deletion_receipts (
    deletion_receipt_id,
    owner_user_id,
    document_id,
    deletion_kind,
    replacement_bundle_id,
    activation_receipt_id,
    reason_hash,
    deleted_source_revision_count,
    deleted_chunk_count,
    deleted_embedding_count,
    affected_materialization_run_count
  )
  VALUES (
    p_deletion_receipt_id,
    p_owner_user_id,
    p_document_id,
    CASE WHEN is_replaced_active THEN 'REPLACED_ACTIVE' ELSE 'UNREFERENCED_STAGING' END,
    p_replacement_bundle_id,
    p_activation_receipt_id,
    p_reason_hash,
    target_source_count,
    target_chunk_count,
    target_embedding_count,
    target_run_count
  );
  INSERT INTO public.rag_v2_immutable_owner_document_deletion_tombstones (
    owner_user_id,
    document_id,
    deletion_receipt_id
  )
  VALUES (
    p_owner_user_id,
    p_document_id,
    p_deletion_receipt_id
  );
  RETURN true;
END;
$delete_rag_v2_immutable_owner_document$;
ALTER FUNCTION delete_rag_v2_immutable_owner_document(text, text, text, text, bigint, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION delete_rag_v2_immutable_owner_document(text, text, text, text, bigint, text, text, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON FUNCTION delete_owner_rag_v2_document(text, text, text, text) FROM decision_app;

REVOKE ALL PRIVILEGES ON TABLE
  rag_v2_immutable_oa_track_catalog,
  rag_v2_immutable_oa_source_cards,
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
  rag_v2_immutable_public_bundle_pointers,
  rag_v2_immutable_bundles,
  rag_v2_immutable_owner_bundle_pointers,
  rag_v2_immutable_consent_events,
  rag_v2_immutable_import_tickets,
  rag_v2_immutable_activation_receipts,
  rag_v2_immutable_deletion_receipts,
  rag_v2_immutable_owner_document_deletion_tombstones
FROM PUBLIC;

DO $rag_v2_immutable_bundle_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_oa_track_catalog,
      rag_v2_immutable_oa_source_cards,
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
      rag_v2_immutable_public_bundle_pointers,
      rag_v2_immutable_bundles,
      rag_v2_immutable_owner_bundle_pointers,
      rag_v2_immutable_consent_events,
      rag_v2_immutable_import_tickets,
      rag_v2_immutable_activation_receipts,
      rag_v2_immutable_deletion_receipts,
      rag_v2_immutable_owner_document_deletion_tombstones
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION record_rag_v2_immutable_consent(text, text, text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION issue_rag_v2_immutable_import_ticket(text, text, text, text) TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_oa_track_catalog,
      rag_v2_immutable_oa_source_cards,
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
      rag_v2_immutable_public_bundle_pointers,
      rag_v2_immutable_bundles,
      rag_v2_immutable_owner_bundle_pointers,
      rag_v2_immutable_consent_events,
      rag_v2_immutable_import_tickets,
      rag_v2_immutable_activation_receipts,
      rag_v2_immutable_deletion_receipts,
      rag_v2_immutable_owner_document_deletion_tombstones
    FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION consume_rag_v2_immutable_import_ticket(text, text, text, text, text) TO decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_oa_track_catalog,
      rag_v2_immutable_oa_source_cards,
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
      rag_v2_immutable_public_bundle_pointers,
      rag_v2_immutable_bundles,
      rag_v2_immutable_owner_bundle_pointers,
      rag_v2_immutable_consent_events,
      rag_v2_immutable_import_tickets,
      rag_v2_immutable_activation_receipts,
      rag_v2_immutable_deletion_receipts,
      rag_v2_immutable_owner_document_deletion_tombstones
    FROM decision_rag_admin;
    GRANT EXECUTE ON FUNCTION activate_rag_v2_immutable_public_base(text, text, bigint, text) TO decision_rag_admin;
    GRANT EXECUTE ON FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) TO decision_rag_admin;
    GRANT EXECUTE ON FUNCTION delete_rag_v2_immutable_owner_document(text, text, text, text, bigint, text, text, text) TO decision_rag_admin;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_oa_track_catalog,
      rag_v2_immutable_oa_source_cards,
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
      rag_v2_immutable_public_bundle_pointers,
      rag_v2_immutable_bundles,
      rag_v2_immutable_owner_bundle_pointers,
      rag_v2_immutable_consent_events,
      rag_v2_immutable_import_tickets,
      rag_v2_immutable_activation_receipts,
      rag_v2_immutable_deletion_receipts,
      rag_v2_immutable_owner_document_deletion_tombstones
    FROM decision_rag_query;
  END IF;
END;
$rag_v2_immutable_bundle_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_immutable_consent(text, text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_immutable_import_ticket(text, text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION consume_rag_v2_immutable_import_ticket(text, text, text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION activate_rag_v2_immutable_public_base(text, text, bigint, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION delete_rag_v2_immutable_owner_document(text, text, text, text, bigint, text, text, text) FROM PUBLIC;
