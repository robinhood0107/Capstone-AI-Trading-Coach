-- Public BGE base activation은 pointer version을 caller가 직접 읽거나 추측하지 않게 한다.
-- 이 read-for-CAS helper는 V25 activation과 같은 transaction에서만 사용되며 raw corpus graph를 노출하지 않는다.
CREATE FUNCTION prepare_rag_v2_immutable_public_base_activation(
  p_exact30_generation_id text,
  p_oa112_generation_id text
)
RETURNS TABLE (
  expected_pointer_version bigint,
  activation_required boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $prepare_rag_v2_immutable_public_base_activation$
DECLARE
  pointer_record public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_exact30_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_oa112_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_exact30_generation_id = p_oa112_generation_id THEN
    RAISE EXCEPTION 'immutable RAG v2 public activation prepare arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- V25 public pointer RLS의 기존 maintenance marker만 사용해 direct table grant를 만들지 않는다.
  PERFORM set_config('app.rag_admin_maintenance', 'public_base_activation', true);
  -- prepare와 V25 activation이 같은 transaction에서 호출될 때 stale version/lock reordering을 닫는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  SELECT *
  INTO pointer_record
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default'
  FOR UPDATE;
  IF NOT FOUND
     OR pointer_record.state NOT IN ('NOT_MATERIALIZED', 'ACTIVE', 'FAILED')
     OR pointer_record.pointer_version < 1 THEN
    RAISE EXCEPTION 'immutable RAG v2 public activation prepare pointer is unavailable'
      USING ERRCODE = '40001';
  END IF;

  -- Same active pair is an idempotent local operator success. No previous pointer IDs are returned.
  RETURN QUERY
  SELECT
    pointer_record.pointer_version,
    NOT (
      pointer_record.state = 'ACTIVE'
      AND pointer_record.exact30_generation_id IS NOT DISTINCT FROM p_exact30_generation_id
      AND pointer_record.oa112_generation_id IS NOT DISTINCT FROM p_oa112_generation_id
    );
END;
$prepare_rag_v2_immutable_public_base_activation$;
ALTER FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text) FROM PUBLIC;

DO $rag_v2_public_bge_activation_prepare_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    GRANT EXECUTE ON FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text)
      TO decision_rag_admin;
  END IF;
END;
$rag_v2_public_bge_activation_prepare_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text) FROM PUBLIC;
