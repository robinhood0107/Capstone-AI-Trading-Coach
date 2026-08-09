-- V44 closes the owner document delete capability.  A local admin process may receive only a
-- server-issued rtd_ ticket; it cannot choose another owner/document through the older V25/V33
-- functions.  The ticket consumption and irreversible graph deletion commit atomically.

CREATE TABLE rag_v2_immutable_owner_delete_tickets (
  ticket_hash text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  document_id text NOT NULL,
  operation text NOT NULL,
  policy_version text NOT NULL,
  state text NOT NULL,
  issued_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  consumer_deletion_receipt_id text REFERENCES rag_v2_immutable_deletion_receipts(deletion_receipt_id) ON DELETE RESTRICT,
  consumer_result text,
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_hash_check CHECK (ticket_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_document_check CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_operation_check CHECK (operation = 'OWNER_DELETE'),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_policy_check CHECK (policy_version = 'RAG_V2_OWNER_DOCUMENT_DELETE_V1'),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_state_check CHECK (state IN ('ISSUED', 'CONSUMED')),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_expiry_check CHECK (expires_at = issued_at + interval '5 minutes'),
  CONSTRAINT rag_v2_immutable_owner_delete_ticket_consumed_check
    CHECK (
      (state = 'ISSUED'
        AND consumed_at IS NULL
        AND consumer_deletion_receipt_id IS NULL
        AND consumer_result IS NULL)
      OR
      (state = 'CONSUMED'
        AND consumed_at IS NOT NULL
        AND consumer_result IN ('DELETED', 'ABSENT')
        AND (
          (consumer_result = 'DELETED'
            AND consumer_deletion_receipt_id IS NOT NULL
            AND consumer_deletion_receipt_id ~ '^rgr_del_[0-9a-f]{32}$')
          OR (consumer_result = 'ABSENT' AND consumer_deletion_receipt_id IS NULL)
        ))
    )
);
CREATE INDEX rag_v2_immutable_owner_delete_tickets_owner_document_expiry_idx
  ON rag_v2_immutable_owner_delete_tickets (owner_user_id, document_id, expires_at DESC);

ALTER TABLE rag_v2_immutable_owner_delete_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_owner_delete_tickets FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_owner_delete_ticket_owner_policy
  ON rag_v2_immutable_owner_delete_tickets
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));

CREATE FUNCTION issue_rag_v2_immutable_owner_delete_ticket(
  p_owner_user_id text,
  p_document_id text,
  p_ticket_id text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_immutable_owner_delete_ticket$
DECLARE
  issued_at timestamptz := clock_timestamp();
  ticket_digest text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR p_ticket_id !~ '^rtd_[0-9a-f]{32}$'
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner delete ticket arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-owner-delete-ticket|' || ticket_digest, 0)
  );
  INSERT INTO public.rag_v2_immutable_owner_delete_tickets (
    ticket_hash,
    owner_user_id,
    document_id,
    operation,
    policy_version,
    state,
    issued_at,
    expires_at
  )
  VALUES (
    ticket_digest,
    p_owner_user_id,
    p_document_id,
    'OWNER_DELETE',
    'RAG_V2_OWNER_DOCUMENT_DELETE_V1',
    'ISSUED',
    issued_at,
    issued_at + interval '5 minutes'
  );
  RETURN issued_at + interval '5 minutes';
END;
$issue_rag_v2_immutable_owner_delete_ticket$;
ALTER FUNCTION issue_rag_v2_immutable_owner_delete_ticket(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_immutable_owner_delete_ticket(text, text, text) FROM PUBLIC;

CREATE FUNCTION delete_rag_v2_immutable_owner_document_with_ticket(
  p_owner_user_id text,
  p_document_id text,
  p_ticket_id text,
  p_activation_receipt_id text,
  p_deletion_receipt_id text,
  p_reason_hash text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $delete_rag_v2_immutable_owner_document_with_ticket$
DECLARE
  ticket_digest text;
  ticket_record public.rag_v2_immutable_owner_delete_tickets%ROWTYPE;
  deletion_result boolean;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR p_ticket_id !~ '^rtd_[0-9a-f]{32}$'
     OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$'
     OR p_deletion_receipt_id !~ '^rgr_del_[0-9a-f]{32}$'
     OR p_reason_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner delete ticket consume arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-owner-delete-ticket|' || ticket_digest, 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  SELECT *
  INTO ticket_record
  FROM public.rag_v2_immutable_owner_delete_tickets
  WHERE ticket_hash = ticket_digest
  FOR UPDATE;
  IF NOT FOUND
     OR ticket_record.owner_user_id IS DISTINCT FROM p_owner_user_id
     OR ticket_record.document_id IS DISTINCT FROM p_document_id
     OR ticket_record.operation <> 'OWNER_DELETE'
     OR ticket_record.policy_version <> 'RAG_V2_OWNER_DOCUMENT_DELETE_V1' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner delete ticket is invalid'
      USING ERRCODE = '22023';
  END IF;
  IF ticket_record.state = 'CONSUMED' THEN
    RETURN ticket_record.consumer_result = 'DELETED';
  END IF;
  IF ticket_record.state <> 'ISSUED'
     OR ticket_record.expires_at <= statement_timestamp() THEN
    RAISE EXCEPTION 'immutable RAG v2 owner delete ticket is expired'
      USING ERRCODE = '23514';
  END IF;

  -- V25 handles unreferenced/staged rows.  Active owner graph deletion intentionally falls
  -- through only on its typed replacement-required constraint to V33, inside this same xact.
  BEGIN
    deletion_result := public.delete_rag_v2_immutable_owner_document(
      p_owner_user_id,
      p_document_id,
      NULL,
      NULL,
      NULL,
      NULL,
      p_deletion_receipt_id,
      p_reason_hash
    );
  EXCEPTION
    WHEN SQLSTATE '23514' THEN
      deletion_result := public.replace_and_delete_rag_v2_immutable_owner_document(
        p_owner_user_id,
        p_document_id,
        p_activation_receipt_id,
        p_deletion_receipt_id,
        p_reason_hash
      );
  END;

  UPDATE public.rag_v2_immutable_owner_delete_tickets
  SET state = 'CONSUMED',
      consumed_at = clock_timestamp(),
      consumer_deletion_receipt_id = CASE WHEN deletion_result THEN p_deletion_receipt_id ELSE NULL END,
      consumer_result = CASE WHEN deletion_result THEN 'DELETED' ELSE 'ABSENT' END
  WHERE ticket_hash = ticket_digest
    AND state = 'ISSUED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner delete ticket consumption conflicted'
      USING ERRCODE = '40001';
  END IF;
  RETURN deletion_result;
END;
$delete_rag_v2_immutable_owner_document_with_ticket$;
ALTER FUNCTION delete_rag_v2_immutable_owner_document_with_ticket(text, text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION delete_rag_v2_immutable_owner_document_with_ticket(text, text, text, text, text, text) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON FUNCTION delete_rag_v2_immutable_owner_document(text, text, text, text, bigint, text, text, text)
  FROM decision_rag_admin;
REVOKE ALL PRIVILEGES ON FUNCTION replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text)
  FROM decision_rag_admin;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_owner_delete_tickets FROM PUBLIC;

DO $rag_v2_owner_delete_ticket_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_owner_delete_tickets FROM decision_app;
    GRANT EXECUTE ON FUNCTION issue_rag_v2_immutable_owner_delete_ticket(text, text, text)
      TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_owner_delete_tickets FROM decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_owner_delete_tickets FROM decision_rag_admin;
    GRANT EXECUTE ON FUNCTION delete_rag_v2_immutable_owner_document_with_ticket(text, text, text, text, text, text)
      TO decision_rag_admin;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_owner_delete_tickets FROM decision_rag_query;
  END IF;
END;
$rag_v2_owner_delete_ticket_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_immutable_owner_delete_ticket(text, text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION delete_rag_v2_immutable_owner_document_with_ticket(text, text, text, text, text, text) FROM PUBLIC;
