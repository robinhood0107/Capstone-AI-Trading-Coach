-- Keep the persisted RAG invariant aligned with the Strong LLM reasoning contract.
-- EVIDENCE_WITH_REASONING may contain uncited reasoning sentences, but V107 still
-- requires at least one verified citation and 20% citation coverage.
ALTER TABLE public.rag_v2_answer_history
  DROP CONSTRAINT rag_v2_answer_history_status_result_check;
ALTER TABLE public.rag_v2_answer_history
  ADD CONSTRAINT rag_v2_answer_history_status_result_check CHECK (
    (
      generation_status = 'ANSWERED'
      AND NOT retrieval_failure
      AND (
        (
          citation_count BETWEEN 1 AND 5
          AND (
            citation_coverage >= 0.8
            OR (
              citation_coverage >= 0.2
              AND 'REASONING_SENTENCES_PRESENT' = ANY(guardrail_flags)
            )
          )
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

-- Brokerage events already have strict payload constraints, but V80 never registered
-- their event types. Without these rows the async safety sweep sends valid submissions
-- directly to DLQ as UNREGISTERED_EVENT.
INSERT INTO public.async_event_registry(
  event_type,outbox_schema_version,kafka_schema_version,topic_name
) VALUES
  ('brokerage.mock-order-submitted.v1','1.0.0',1,'brokerage.mock-order-submitted.v1'),
  ('brokerage.mock-order-cancel-requested.v1','1.0.0',1,'brokerage.mock-order-cancel-requested.v1'),
  ('brokerage.paper-order-accepted.v1','1.0.0',1,'brokerage.paper-order-accepted.v1'),
  ('brokerage.paper-order-filled.v1','1.0.0',1,'brokerage.paper-order-filled.v1'),
  ('brokerage.paper-order-cancelled.v1','1.0.0',1,'brokerage.paper-order-cancelled.v1')
ON CONFLICT (event_type) DO NOTHING;

-- A completed physical automation submit must retain its automation-owned reservation.
-- Missing historical evidence cannot be fabricated, so both operator readiness and the
-- user status projection fail closed until the account is explicitly reconciled.
CREATE OR REPLACE FUNCTION public.p1_automation_open_work_clear_v3(
  p_user_id text,p_account_id text
)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_open_work_clear_v3$
  SELECT NOT EXISTS (
    SELECT 1 FROM public.orders item
    WHERE item.user_id=p_user_id AND item.account_id=p_account_id
      AND (item.status IN ('SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED')
        OR item.reconciliation_status='MISMATCH')
      AND NOT EXISTS (
        SELECT 1 FROM public.automation_order_reservations reservation
        WHERE reservation.order_id=item.order_id
      )
  ) AND NOT EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.user_id=p_user_id AND run.state='PENDING_RECONCILIATION'
  ) AND NOT EXISTS (
    SELECT 1 FROM public.automation_runs run
    WHERE run.user_id=p_user_id AND run.physical_submit_count=1
      AND NOT EXISTS (
        SELECT 1 FROM public.automation_order_reservations reservation
        WHERE reservation.run_id=run.run_id
      )
  )
$p1_automation_open_work_clear_v3$;

CREATE OR REPLACE FUNCTION public.p1_automation_status_facts_v2(
  p_user_id text,p_account_id text
)
RETURNS TABLE(
  principle_configured boolean,unresolved_reconciliation boolean,active_principle_id text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_status_facts_v2$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1()
     OR (p_account_id IS NOT NULL AND p_account_id!~'^acct_[0-9a-f]{32}$') THEN
    RAISE EXCEPTION 'automation status facts scope denied' USING ERRCODE='42501';
  END IF;
  SELECT principle.principle_id INTO active_principle_id FROM public.principles principle
  WHERE principle.user_id=p_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id DESC LIMIT 1;
  principle_configured:=active_principle_id IS NOT NULL;
  unresolved_reconciliation:=p_account_id IS NOT NULL AND (
    EXISTS (
      SELECT 1 FROM public.orders item
      WHERE item.user_id=p_user_id AND item.account_id=p_account_id
        AND (item.status IN (
          'SUBMITTED','PENDING_RECONCILIATION','ACCEPTED','PARTIALLY_FILLED','CANCEL_REQUESTED'
        ) OR item.reconciliation_status='MISMATCH')
    )
    OR EXISTS (
      SELECT 1 FROM public.automation_runs run
      WHERE run.user_id=p_user_id AND run.physical_submit_count=1
        AND NOT EXISTS (
          SELECT 1 FROM public.automation_order_reservations reservation
          WHERE reservation.run_id=run.run_id
        )
    )
  );
  RETURN NEXT;
END
$p1_automation_status_facts_v2$;

-- Preserve identifiers and references while removing acceptance-only copy from the
-- persistent demo user's visible principle data.
UPDATE public.principles
SET title='균형형 원칙'
WHERE user_id='usr_demo_user' AND title LIKE 'Team A acceptance%';

UPDATE public.principle_versions version
SET title='균형형 원칙'
FROM public.principles principle
WHERE principle.principle_id=version.principle_id
  AND principle.user_id='usr_demo_user'
  AND version.title LIKE 'Team A acceptance%';
