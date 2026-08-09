-- V33은 active owner document 삭제 시 V31 overlay replacement와 V25 hard-delete를 같은
-- security-definer transaction으로 묶는다. Python admin adapter는 raw graph table을 읽지 않는다.

CREATE FUNCTION replace_and_delete_rag_v2_immutable_owner_document(
  p_owner_user_id text,
  p_document_id text,
  p_activation_receipt_id text,
  p_deletion_receipt_id text,
  p_reason_hash text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $replace_and_delete_rag_v2_immutable_owner_document$
DECLARE
  replacement record;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$'
     OR p_deletion_receipt_id !~ '^rgr_del_[0-9a-f]{32}$'
     OR p_reason_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 active owner deletion arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- V31 acquires the target document lock before the shared global activation lock. V25 sees
  -- the same xact locks reentrantly, so replacement activation and old graph hard-delete commit
  -- together or both roll back.
  SELECT * INTO replacement
  FROM public.prepare_rag_v2_immutable_owner_overlay(p_owner_user_id, p_document_id);
  IF NOT FOUND
     OR replacement.bundle_id IS NULL
     OR replacement.bundle_id !~ '^rgb_[0-9a-f]{32}$'
     OR replacement.expected_active_bundle_id IS NULL
     OR replacement.expected_active_bundle_id !~ '^rgb_[0-9a-f]{32}$'
     OR replacement.expected_bundle_version IS NULL
     OR replacement.expected_bundle_version < 1 THEN
    RAISE EXCEPTION 'immutable RAG v2 active owner deletion replacement is not ready'
      USING ERRCODE = '23514';
  END IF;

  RETURN public.delete_rag_v2_immutable_owner_document(
    p_owner_user_id,
    p_document_id,
    replacement.bundle_id,
    replacement.expected_active_bundle_id,
    replacement.expected_bundle_version,
    p_activation_receipt_id,
    p_deletion_receipt_id,
    p_reason_hash
  );
END;
$replace_and_delete_rag_v2_immutable_owner_document$;
ALTER FUNCTION replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text) FROM PUBLIC;

DO $rag_v2_active_owner_delete_admin_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    GRANT EXECUTE ON FUNCTION replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text)
      TO decision_rag_admin;
  END IF;
END;
$rag_v2_active_owner_delete_admin_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text) FROM PUBLIC;
