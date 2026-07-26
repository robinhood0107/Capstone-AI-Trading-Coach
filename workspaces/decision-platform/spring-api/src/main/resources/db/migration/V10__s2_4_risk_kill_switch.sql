-- S2.4는 legacy risk_snapshots를 사용하지 않는다(P0-5). 해당 skeleton은 producer 세션에서 재평가한다.
-- kill switch 상태, 전이, decision 무효화는 DB 한 transaction의 append-only 증거로 관리한다.
DO $v10_precondition$
BEGIN
  IF to_regclass('public.risk_kill_switch') IS NOT NULL
     OR to_regclass('public.risk_kill_switch_transitions') IS NOT NULL
     OR to_regclass('public.decision_invalidations') IS NOT NULL THEN
    RAISE EXCEPTION 'S2.4 V10 precondition failed: kill switch objects already exist';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM users WHERE user_id = 'usr_demo_admin') THEN
    RAISE EXCEPTION 'S2.4 V10 precondition failed: V7 actor trust root is required';
  END IF;
END
$v10_precondition$;

CREATE TABLE risk_kill_switch (
  kill_switch_id text PRIMARY KEY CHECK (kill_switch_id = 'GLOBAL'),
  active boolean NOT NULL,
  reason_class text NOT NULL CHECK (
    reason_class IN (
      'USER_MANUAL_STOP',
      'OPERATOR_MANUAL_STOP',
      'DATA_FRESHNESS_STOP',
      'BROKERAGE_FAILURE_STOP',
      'DEMO_SAFETY_STOP',
      'ADMIN_RESUME',
      'INITIAL_STATE'
    )
  ),
  generation bigint NOT NULL CHECK (generation > 0),
  changed_by text REFERENCES users(user_id) ON DELETE RESTRICT,
  changed_by_role text NOT NULL CHECK (changed_by_role IN ('USER', 'ADMIN', 'SYSTEM')),
  changed_at timestamptz NOT NULL,
  request_id text,
  CONSTRAINT risk_kill_switch_actor_check CHECK (
    (changed_by_role = 'SYSTEM' AND changed_by IS NULL)
    OR (changed_by_role IN ('USER', 'ADMIN') AND changed_by IS NOT NULL)
  ),
  CONSTRAINT risk_kill_switch_resume_role_check
    CHECK (active OR changed_by_role IN ('ADMIN', 'SYSTEM')),
  CONSTRAINT risk_kill_switch_request_id_check CHECK (
    request_id IS NULL OR char_length(request_id) BETWEEN 1 AND 128
  )
);

CREATE TABLE risk_kill_switch_transitions (
  transition_id text PRIMARY KEY,
  generation bigint NOT NULL UNIQUE CHECK (generation > 0),
  previous_active boolean NOT NULL,
  next_active boolean NOT NULL,
  reason_class text NOT NULL CHECK (
    reason_class IN (
      'USER_MANUAL_STOP',
      'OPERATOR_MANUAL_STOP',
      'DATA_FRESHNESS_STOP',
      'BROKERAGE_FAILURE_STOP',
      'DEMO_SAFETY_STOP',
      'ADMIN_RESUME',
      'INITIAL_STATE'
    )
  ),
  changed_by text REFERENCES users(user_id) ON DELETE RESTRICT,
  changed_by_role text NOT NULL CHECK (changed_by_role IN ('USER', 'ADMIN', 'SYSTEM')),
  changed_at timestamptz NOT NULL,
    request_id text CHECK (request_id IS NULL OR char_length(request_id) BETWEEN 1 AND 128),
  invalidated_decision_count integer NOT NULL CHECK (invalidated_decision_count >= 0),
  CONSTRAINT risk_kill_switch_transitions_change_check CHECK (previous_active <> next_active),
  CONSTRAINT risk_kill_switch_transitions_actor_check CHECK (
    (changed_by_role = 'SYSTEM' AND changed_by IS NULL)
    OR (changed_by_role IN ('USER', 'ADMIN') AND changed_by IS NOT NULL)
  ),
  CONSTRAINT risk_kill_switch_transitions_resume_role_check
    CHECK (next_active OR changed_by_role IN ('ADMIN', 'SYSTEM'))
);

CREATE TABLE decision_invalidations (
  invalidation_id text PRIMARY KEY,
  decision_id text NOT NULL,
  evaluation_id text NOT NULL,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  reason_class text NOT NULL CHECK (
    reason_class IN (
      'KILL_SWITCH_ACTIVATED',
      'PRINCIPLE_VERSION_SUPERSEDED',
      'DATA_FRESHNESS_BLOCK'
    )
  ),
  source_generation bigint CHECK (source_generation IS NULL OR source_generation > 0),
  invalidated_at timestamptz NOT NULL,
  request_id text CHECK (request_id IS NULL OR char_length(request_id) BETWEEN 1 AND 128),
  CONSTRAINT decision_invalidations_identity_unique UNIQUE (decision_id, reason_class),
  CONSTRAINT decision_invalidations_decision_evaluation_fkey
    FOREIGN KEY (decision_id, evaluation_id)
    REFERENCES decisions(decision_id, evaluation_id) ON DELETE RESTRICT
);
CREATE INDEX decision_invalidations_owner_idx
  ON decision_invalidations (owner_user_id, invalidated_at DESC, invalidation_id);

INSERT INTO risk_kill_switch (
  kill_switch_id,
  active,
  reason_class,
  generation,
  changed_by,
  changed_by_role,
  changed_at,
  request_id
)
VALUES ('GLOBAL', false, 'INITIAL_STATE', 1, NULL, 'SYSTEM', now(), NULL);

ALTER TABLE decision_invalidations ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_invalidations FORCE ROW LEVEL SECURITY;
CREATE POLICY decision_invalidations_owner_select_policy
  ON decision_invalidations
  FOR SELECT
  USING (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY decision_invalidations_owner_insert_policy
  ON decision_invalidations
  FOR INSERT
  TO decision_app
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));

-- FORCE RLS를 유지하면서 전역 switch의 definer만 모든 owner를 집합 처리할 수 있게 한다.
CREATE POLICY decision_invalidations_definer_select_policy
  ON decision_invalidations
  FOR SELECT
  TO flyway
  USING (true);
CREATE POLICY decision_invalidations_definer_insert_policy
  ON decision_invalidations
  FOR INSERT
  TO flyway
  WITH CHECK (true);
CREATE POLICY decisions_kill_switch_definer_select_policy
  ON decisions
  FOR SELECT
  TO flyway
  USING (true);

CREATE FUNCTION invalidate_unused_decisions_for_kill_switch(
  requested_source_generation bigint,
  requested_invalidated_at timestamptz,
  requested_request_id text
)
RETURNS integer
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $invalidate_unused_decisions_for_kill_switch$
DECLARE
  inserted_count integer;
BEGIN
  IF requested_source_generation IS NULL
     OR requested_source_generation <= 0
     OR requested_request_id IS NULL
     OR char_length(requested_request_id) NOT BETWEEN 1 AND 128 THEN
    RAISE EXCEPTION 'S2.4 invalid kill switch invalidation request';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.risk_kill_switch gate
    WHERE gate.kill_switch_id = 'GLOBAL'
      AND gate.active
      AND gate.generation = requested_source_generation
      AND gate.changed_at = requested_invalidated_at
      AND gate.request_id = requested_request_id
  ) THEN
    RAISE EXCEPTION 'S2.4 kill switch generation is not current and active';
  END IF;

  INSERT INTO public.decision_invalidations (
    invalidation_id,
    decision_id,
    evaluation_id,
    owner_user_id,
    reason_class,
    source_generation,
    invalidated_at,
    request_id
  )
  SELECT
    'dinv_' || pg_catalog.md5(decision.decision_id || ':' || requested_source_generation::text),
    decision.decision_id,
    decision.evaluation_id,
    decision.user_id,
    'KILL_SWITCH_ACTIVATED',
    requested_source_generation,
    requested_invalidated_at,
    requested_request_id
  FROM public.decisions decision
  WHERE decision.valid_until > requested_invalidated_at
    AND NOT EXISTS (
      SELECT 1
      FROM public.orders consumed
      WHERE consumed.decision_id = decision.decision_id
    )
  ON CONFLICT (decision_id, reason_class) DO NOTHING;

  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  RETURN inserted_count;
END
$invalidate_unused_decisions_for_kill_switch$;
ALTER FUNCTION invalidate_unused_decisions_for_kill_switch(bigint, timestamptz, text)
  OWNER TO flyway;
REVOKE ALL ON FUNCTION invalidate_unused_decisions_for_kill_switch(bigint, timestamptz, text)
  FROM PUBLIC;

CREATE VIEW kill_switch_user_projection
WITH (security_barrier = true)
AS
SELECT active, reason_class, changed_at
FROM risk_kill_switch
WHERE kill_switch_id = 'GLOBAL';

CREATE FUNCTION read_kill_switch_gate()
RETURNS TABLE (
  active boolean,
  generation bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_kill_switch_gate$
  SELECT gate.active, gate.generation
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id = 'GLOBAL'
  LIMIT 1
$read_kill_switch_gate$;
ALTER FUNCTION read_kill_switch_gate() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_kill_switch_gate() FROM PUBLIC;

CREATE FUNCTION revalidate_kill_switch_admin(
  requested_actor_user_id text,
  requested_security_version bigint
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $revalidate_kill_switch_admin$
DECLARE
  stored_actor_role text;
  stored_actor_status text;
  stored_security_version bigint;
BEGIN
  SELECT actor.role, actor.status, actor.security_version
  INTO stored_actor_role, stored_actor_status, stored_security_version
  FROM public.users actor
  WHERE actor.user_id = requested_actor_user_id
  FOR SHARE;

  IF NOT FOUND
     OR stored_actor_status <> 'ACTIVE'
     OR stored_security_version <> requested_security_version THEN
    RETURN 'UNAUTHORIZED';
  END IF;
  IF stored_actor_role <> 'ADMIN' THEN
    RETURN 'FORBIDDEN';
  END IF;
  RETURN 'AUTHORIZED';
END
$revalidate_kill_switch_admin$;
ALTER FUNCTION revalidate_kill_switch_admin(text, bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION revalidate_kill_switch_admin(text, bigint) FROM PUBLIC;

CREATE FUNCTION read_kill_switch_audit_projection()
RETURNS TABLE (
  audit_log_id text,
  action text,
  request_id text,
  created_at timestamptz,
  payload_json jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_kill_switch_audit_projection$
  SELECT
    audit.audit_log_id,
    audit.action,
    audit.request_id,
    audit.created_at,
    jsonb_build_object(
      'generation', audit.payload_json -> 'generation',
      'previousActive', audit.payload_json -> 'previousActive',
      'nextActive', audit.payload_json -> 'nextActive',
      'reasonClass', audit.payload_json -> 'reasonClass',
      'changedBy', audit.payload_json -> 'changedBy',
      'changedByRole', audit.payload_json -> 'changedByRole',
      'correlationId', audit.payload_json -> 'correlationId',
      'invalidatedDecisionCount', audit.payload_json -> 'invalidatedDecisionCount'
    )
  FROM public.audit_logs audit
  WHERE audit.action = 'KILL_SWITCH_CHANGED'
    AND audit.target_type = 'KILL_SWITCH'
    AND EXISTS (
      SELECT 1
      FROM public.users actor
      WHERE actor.user_id = current_setting('app.actor_user_id', true)
        AND actor.status = 'ACTIVE'
        AND actor.role = 'ADMIN'
    )
  ORDER BY audit.created_at DESC, audit.audit_log_id DESC
  LIMIT 100
$read_kill_switch_audit_projection$;
ALTER FUNCTION read_kill_switch_audit_projection() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_kill_switch_audit_projection() FROM PUBLIC;

CREATE FUNCTION read_decision_usability()
RETURNS TABLE (
  decision_id text,
  evaluation_id text,
  valid_until timestamptz,
  invalidated boolean,
  invalidation_reason_class text,
  consumed_by_order_id text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $read_decision_usability$
  SELECT
    decision.decision_id,
    decision.evaluation_id,
    decision.valid_until,
    invalidation.invalidation_id IS NOT NULL AS invalidated,
    invalidation.reason_class AS invalidation_reason_class,
    consumed.order_id AS consumed_by_order_id
  FROM public.decisions decision
  LEFT JOIN LATERAL (
    SELECT entry.invalidation_id, entry.reason_class
    FROM public.decision_invalidations entry
    WHERE entry.decision_id = decision.decision_id
    ORDER BY entry.invalidated_at DESC, entry.invalidation_id DESC
    LIMIT 1
  ) invalidation ON true
  LEFT JOIN public.orders consumed
    ON consumed.decision_id = decision.decision_id
  WHERE decision.user_id = current_setting('app.actor_user_id', true)
    AND decision.decision_id = current_setting('app.requested_decision_id', true)
  LIMIT 1
$read_decision_usability$;
ALTER FUNCTION read_decision_usability() OWNER TO flyway;
REVOKE ALL ON FUNCTION read_decision_usability() FROM PUBLIC;

CREATE INDEX kill_switch_audit_projection_idx
  ON audit_logs (created_at DESC, audit_log_id)
  WHERE action = 'KILL_SWITCH_CHANGED' AND target_type = 'KILL_SWITCH';

ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_kill_switch_contract_check
  CHECK (
    target_type <> 'KILL_SWITCH'
    OR (
      action = 'KILL_SWITCH_CHANGED'
      AND user_id IS NOT NULL
      AND target_id = 'GLOBAL'
      AND request_id = payload_json ->> 'correlationId'
      AND user_id = payload_json ->> 'changedBy'
      AND actor_role = payload_json ->> 'changedByRole'
      AND payload_json ?& ARRAY[
        'generation',
        'previousActive',
        'nextActive',
        'reasonClass',
        'changedBy',
        'changedByRole',
        'correlationId',
        'invalidatedDecisionCount'
      ]
      AND payload_json - ARRAY[
        'generation',
        'previousActive',
        'nextActive',
        'reasonClass',
        'changedBy',
        'changedByRole',
        'correlationId',
        'invalidatedDecisionCount'
      ] = '{}'::jsonb
    )
  );

ALTER TABLE event_outbox
  ADD CONSTRAINT event_outbox_kill_switch_contract_check
  CHECK (
    event_type <> 'kill-switch.changed'
    OR (
      aggregate_type = 'KILL_SWITCH'
      AND aggregate_id = 'GLOBAL'
      AND partition_key = 'GLOBAL'
      AND schema_version = '1.0.0'
      AND payload_json ?& ARRAY['active', 'changedAt']
      AND payload_json - ARRAY['active', 'changedAt'] = '{}'::jsonb
    )
  );

REVOKE ALL PRIVILEGES ON TABLE
  risk_kill_switch,
  risk_kill_switch_transitions,
  decision_invalidations,
  kill_switch_user_projection
FROM PUBLIC;

DO $v10_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flyway') THEN
    GRANT SELECT ON TABLE
      users,
      audit_logs,
      decisions,
      orders,
      risk_kill_switch,
      risk_kill_switch_transitions,
      decision_invalidations
    TO flyway;
    GRANT INSERT ON TABLE decision_invalidations TO flyway;
    -- SELECT ... FOR SHARE는 SELECT와 함께 대상 row의 UPDATE privilege를 요구한다.
    GRANT UPDATE (status) ON TABLE users TO flyway;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      risk_kill_switch,
      risk_kill_switch_transitions,
      decision_invalidations,
      kill_switch_user_projection
    FROM decision_app;

    GRANT SELECT ON TABLE risk_kill_switch TO decision_app;
    GRANT UPDATE (
      active,
      reason_class,
      generation,
      changed_by,
      changed_by_role,
      changed_at,
      request_id
    ) ON TABLE risk_kill_switch TO decision_app;
    GRANT INSERT ON TABLE risk_kill_switch_transitions TO decision_app;
    GRANT SELECT ON TABLE kill_switch_user_projection TO decision_app;
    GRANT EXECUTE ON FUNCTION
      read_kill_switch_gate(),
      revalidate_kill_switch_admin(text, bigint),
      read_kill_switch_audit_projection(),
      read_decision_usability(),
      invalidate_unused_decisions_for_kill_switch(bigint, timestamptz, text)
    TO decision_app;

    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$v10_privileges$;
