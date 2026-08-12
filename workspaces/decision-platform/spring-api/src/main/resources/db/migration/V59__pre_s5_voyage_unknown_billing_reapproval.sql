-- UNKNOWN_BILLING은 과거 packet의 불확실한 청구 evidence를 보존하되, 새 exact packet의
-- 명시적 재승인까지 영구 차단해서는 안 된다. 동일 packet 재사용과 동시/중복 성공은 계속 거부한다.
ALTER TABLE public.rag_v2_immutable_voyage_document_batch_attempts
  ADD COLUMN attempt_ordinal integer;

WITH numbered_attempts AS (
  SELECT
    usage_event_id,
    row_number() OVER (
      PARTITION BY batch_plan_sha256, batch_id
      ORDER BY claimed_at, usage_event_id
    )::integer AS attempt_ordinal
  FROM public.rag_v2_immutable_voyage_document_batch_attempts
)
UPDATE public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
SET attempt_ordinal = numbered_attempts.attempt_ordinal
FROM numbered_attempts
WHERE numbered_attempts.usage_event_id = attempt.usage_event_id;

ALTER TABLE public.rag_v2_immutable_voyage_document_batch_attempts
  ALTER COLUMN attempt_ordinal SET NOT NULL,
  ADD CONSTRAINT rag_v2_immutable_voyage_document_batch_attempt_ordinal_check
    CHECK (attempt_ordinal BETWEEN 1 AND 10000),
  DROP CONSTRAINT rag_v2_immutable_voyage_document_batch_attempts_pkey,
  ADD CONSTRAINT rag_v2_immutable_voyage_document_batch_attempts_pkey
    PRIMARY KEY (batch_plan_sha256, batch_id, attempt_ordinal);

CREATE UNIQUE INDEX rag_v2_immutable_voyage_document_batch_attempt_packet_unique
  ON public.rag_v2_immutable_voyage_document_batch_attempts (
    batch_plan_sha256, batch_id, packet_sha256
  );
CREATE UNIQUE INDEX rag_v2_immutable_voyage_document_batch_attempt_active_unique
  ON public.rag_v2_immutable_voyage_document_batch_attempts (batch_plan_sha256, batch_id)
  WHERE state = 'CLAIMED';
CREATE UNIQUE INDEX rag_v2_immutable_voyage_document_batch_attempt_committed_unique
  ON public.rag_v2_immutable_voyage_document_batch_attempts (batch_plan_sha256, batch_id)
  WHERE state = 'COMMITTED';

CREATE OR REPLACE FUNCTION claim_rag_v2_immutable_voyage_document_batch_attempt(
  p_usage_event_id text,
  p_batch_plan_sha256 text,
  p_batch_id text,
  p_batch_manifest_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_voyage_document_batch_attempt$
DECLARE
  p_packet_sha256 text;
  next_attempt_ordinal integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vou_[0-9a-f]{32}$'
     OR p_batch_plan_sha256 !~ '^[0-9a-f]{64}$'
     OR p_batch_id !~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'
     OR p_batch_manifest_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch claim is invalid' USING ERRCODE = '22023';
  END IF;

  SELECT reservation.packet_sha256 INTO p_packet_sha256
  FROM public.rag_v2_immutable_voyage_usage_reservations AS reservation
  WHERE reservation.usage_event_id = p_usage_event_id
    AND reservation.bundle_manifest_sha256 = p_batch_manifest_sha256
    AND reservation.expires_at > statement_timestamp();
  IF p_packet_sha256 IS NULL THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch reservation is absent' USING ERRCODE = '55000';
  END IF;

  -- 같은 plan/batch의 재승인은 직렬화하며, UNKNOWN_BILLING 뒤의 서로 다른 packet만 허용한다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-voyage-document-batch-attempt|' || p_batch_plan_sha256 || '|' || p_batch_id,
      0
    )
  );
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.batch_id = p_batch_id
      AND attempt.state IN ('CLAIMED', 'COMMITTED')
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch already has an active or committed attempt'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.batch_id = p_batch_id
      AND attempt.packet_sha256 = p_packet_sha256
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch packet was already consumed'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.batch_id = p_batch_id
  ) AND NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.batch_id = p_batch_id
      AND attempt.state = 'UNKNOWN_BILLING'
      AND attempt.packet_sha256 <> p_packet_sha256
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch reapproval requires a fresh packet'
      USING ERRCODE = '55000';
  END IF;

  SELECT COALESCE(max(attempt.attempt_ordinal), 0) + 1
  INTO next_attempt_ordinal
  FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
  WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
    AND attempt.batch_id = p_batch_id;
  IF next_attempt_ordinal NOT BETWEEN 1 AND 10000 THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch attempt limit is exhausted'
      USING ERRCODE = '54000';
  END IF;

  PERFORM public.claim_rag_v2_immutable_voyage_usage_attempt(p_usage_event_id);
  INSERT INTO public.rag_v2_immutable_voyage_document_batch_attempts (
    batch_plan_sha256, batch_id, attempt_ordinal, batch_manifest_sha256,
    usage_event_id, packet_sha256
  ) VALUES (
    p_batch_plan_sha256, p_batch_id, next_attempt_ordinal, p_batch_manifest_sha256,
    p_usage_event_id, p_packet_sha256
  );
END
$claim_rag_v2_immutable_voyage_document_batch_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text)
  TO decision_rag_writer;

CREATE OR REPLACE FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(
  p_batch_plan_sha256 text
)
RETURNS TABLE (
  batch_id text,
  chunk_id text,
  embedding vector(1024)
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $load_rag_v2_immutable_voyage_document_batch_vectors$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_batch_plan_sha256 IS NULL
     OR p_batch_plan_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch resume is unavailable' USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.state = 'CLAIMED'
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch has an active attempt'
      USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
    WHERE plan.batch_plan_sha256 = p_batch_plan_sha256
  ) THEN
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
    WHERE plan.batch_plan_sha256 = p_batch_plan_sha256 AND plan.state IN ('STAGING','COMPLETE')
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch resume state is invalid' USING ERRCODE = '55000';
  END IF;
  RETURN QUERY
  SELECT vector_row.batch_id, vector_row.chunk_id, vector_row.embedding
  FROM public.rag_v2_immutable_voyage_document_batch_vectors AS vector_row
  JOIN public.rag_v2_immutable_voyage_document_batches AS batch
    ON batch.batch_id = vector_row.batch_id
   AND batch.batch_plan_sha256 = vector_row.batch_plan_sha256
   AND batch.state = 'COMMITTED'
  WHERE vector_row.batch_plan_sha256 = p_batch_plan_sha256
  ORDER BY batch.batch_ordinal, vector_row.chunk_id;
END
$load_rag_v2_immutable_voyage_document_batch_vectors$;
ALTER FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  load_rag_v2_immutable_voyage_document_batch_vectors(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  load_rag_v2_immutable_voyage_document_batch_vectors(text) TO decision_rag_writer;
