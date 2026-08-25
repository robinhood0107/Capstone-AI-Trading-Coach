-- P1 closes the remaining write paths behind one-use actor capabilities.
-- Historical migrations remain immutable; this migration only narrows privileges and replaces capabilities.

DO $decision_auth_role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_auth') THEN
    RAISE EXCEPTION 'decision_auth bootstrap role is required before V86'
      USING ERRCODE = '55000';
  END IF;
END
$decision_auth_role$;

CREATE OR REPLACE FUNCTION public.read_demo_credentials()
RETURNS TABLE(user_id text, username text, password_hash text, role text, status text, security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_demo_credentials$
BEGIN
  IF session_user <> 'decision_auth' AND NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
  ) THEN RAISE EXCEPTION 'credential reader role denied' USING ERRCODE='42501'; END IF;
  RETURN QUERY SELECT item.user_id,item.username,item.password_hash,item.role,item.status,item.security_version
  FROM public.users item WHERE item.user_id IN ('usr_demo_user','usr_demo_admin') ORDER BY item.user_id;
END
$read_demo_credentials$;

CREATE OR REPLACE FUNCTION public.read_user_actor(p_user_id text)
RETURNS TABLE(user_id text, username text, role text, status text, security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_user_actor$
BEGIN
  IF (session_user <> 'decision_auth' AND NOT EXISTS (
       SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
     )) OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$' THEN
    RAISE EXCEPTION 'actor reader denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY SELECT item.user_id,item.username,item.role,item.status,item.security_version
  FROM public.users item WHERE item.user_id=p_user_id;
END
$read_user_actor$;

CREATE POLICY decisions_definer_insert_policy
  ON public.decisions
  FOR INSERT
  TO flyway
  WITH CHECK (true);

CREATE FUNCTION public.transition_kill_switch_authorized(
  p_capability text,
  p_actor_user_id text,
  p_actor_security_version bigint,
  p_requested_active boolean,
  p_observed_generation bigint,
  p_request_id text
)
RETURNS TABLE(
  active boolean,
  reason_class text,
  changed_at timestamptz,
  changed boolean,
  previous_active boolean,
  generation bigint,
  invalidated_decision_count integer
)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $transition_kill_switch_authorized$
#variable_conflict use_column
DECLARE
  actor_role text;
  current_gate public.risk_kill_switch%ROWTYPE;
  next_reason text;
  transition_time timestamptz;
  invalidated_count integer := 0;
BEGIN
  IF session_user <> 'decision_app'
     OR p_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_observed_generation <= 0 THEN
    RAISE EXCEPTION 'kill switch capability request denied' USING ERRCODE = '42501';
  END IF;

  IF NOT public.consume_actor_request_capability(
    p_capability,
    p_actor_user_id,
    p_actor_security_version,
    CASE WHEN p_requested_active THEN NULL ELSE 'ADMIN' END
  ) THEN
    RAISE EXCEPTION 'kill switch actor capability denied' USING ERRCODE = '42501';
  END IF;

  SELECT actor.role INTO actor_role
  FROM public.users actor
  WHERE actor.user_id = p_actor_user_id
    AND actor.status = 'ACTIVE'
    AND actor.security_version = p_actor_security_version
    AND actor.role IN ('USER', 'ADMIN')
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch current actor denied' USING ERRCODE = '42501';
  END IF;

  SELECT * INTO current_gate
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id = 'GLOBAL'
  FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE = 'P5501'; END IF;

  IF current_gate.generation <> p_observed_generation THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE = '40001';
  END IF;

  IF current_gate.active = p_requested_active THEN
    RETURN QUERY SELECT current_gate.active,current_gate.reason_class,current_gate.changed_at,false,
      current_gate.active,current_gate.generation,0;
    RETURN;
  END IF;

  next_reason := CASE
    WHEN NOT p_requested_active THEN 'ADMIN_RESUME'
    WHEN actor_role = 'ADMIN' THEN 'OPERATOR_MANUAL_STOP'
    ELSE 'USER_MANUAL_STOP'
  END;
  transition_time := greatest(statement_timestamp(), current_gate.changed_at);

  UPDATE public.risk_kill_switch gate
  SET active = p_requested_active,
      reason_class = next_reason,
      generation = current_gate.generation + 1,
      changed_by = p_actor_user_id,
      changed_by_role = actor_role,
      changed_at = transition_time,
      request_id = p_request_id
  WHERE gate.kill_switch_id = 'GLOBAL'
    AND gate.generation = p_observed_generation;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch generation conflict' USING ERRCODE = '40001';
  END IF;

  IF p_requested_active THEN
    invalidated_count := public.invalidate_unused_decisions_for_kill_switch(
      current_gate.generation + 1,
      transition_time,
      p_request_id
    );
  END IF;

  INSERT INTO public.risk_kill_switch_transitions(
    transition_id,generation,previous_active,next_active,reason_class,changed_by,
    changed_by_role,changed_at,request_id,invalidated_decision_count
  ) VALUES (
    'kst_' || replace(gen_random_uuid()::text,'-',''),current_gate.generation + 1,
    current_gate.active,p_requested_active,next_reason,p_actor_user_id,actor_role,
    transition_time,p_request_id,invalidated_count
  );

  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_' || replace(gen_random_uuid()::text,'-',''),p_actor_user_id,actor_role,
    'KILL_SWITCH_CHANGED','KILL_SWITCH','GLOBAL',p_request_id,
    jsonb_build_object(
      'generation',current_gate.generation + 1,
      'previousActive',current_gate.active,
      'nextActive',p_requested_active,
      'reasonClass',next_reason,
      'changedBy',p_actor_user_id,
      'changedByRole',actor_role,
      'correlationId',p_request_id,
      'invalidatedDecisionCount',invalidated_count
    ),transition_time
  );

  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_' || replace(gen_random_uuid()::text,'-',''),'kill-switch.changed','KILL_SWITCH',
    'GLOBAL','GLOBAL',jsonb_build_object('active',p_requested_active,'changedAt',transition_time::text),
    '1.0.0','PENDING',0,transition_time,transition_time
  );

  RETURN QUERY SELECT p_requested_active,next_reason,transition_time,true,current_gate.active,
    current_gate.generation + 1,invalidated_count;
END
$transition_kill_switch_authorized$;

CREATE FUNCTION public.persist_decision_bundle_authorized(
  p_capability text,
  p_bundle jsonb
)
RETURNS TABLE(outcome text, result_canonical_json text)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $persist_decision_bundle_authorized$
#variable_conflict use_column
DECLARE
  actor_user_id text;
  actor_role text;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user <> 'decision_app'
     OR jsonb_typeof(p_bundle) <> 'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value = 'null'::jsonb)
     OR p_bundle - ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] <> '{}'::jsonb
     OR NOT p_bundle ?& ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE = '22023';
  END IF;

  BEGIN
    actor_user_id := p_bundle->>'actorUserId';
    actor_role := p_bundle->>'actorRole';
    created_at := (p_bundle->>'createdAt')::timestamptz;
    result_text := p_bundle->>'resultCanonicalJson';
    snapshot_text := p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE = '22023';
  END;

  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions') <> 'object'
     OR jsonb_typeof(p_bundle->'violations') <> 'array'
     OR jsonb_array_length(p_bundle->'violations') > 14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb) <> 'object'
     OR jsonb_typeof(snapshot_text::jsonb) <> 'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE = '22023';
  END IF;

  IF NOT public.consume_current_actor_capability(p_capability,actor_user_id) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  ) THEN
    RAISE EXCEPTION 'decision current actor denied' USING ERRCODE = '42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE = 'P5501';
  END IF;
  IF gate_active THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=item.current_version
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND item.current_version=(p_bundle->>'principleVersion')::integer
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE = '40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal := ordinal + 1;
    IF jsonb_typeof(violation)<>'object'
       OR violation - ARRAY['ruleId','severity','observedValue','thresholdValue','message'] <> '{}'::jsonb
       OR NOT violation ?& ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal := array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );

  reference_payload := jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$persist_decision_bundle_authorized$;

CREATE OR REPLACE FUNCTION public.claim_async_job_by_event(
  p_worker text,p_event_id text,p_event_type text,p_job_id text,p_payload_hash text,p_partition_key text
)
RETURNS TABLE(job_id text,job_type text,payload_json jsonb,claim_token uuid,attempt_count integer,hard_deadline_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $claim_async_job_by_event$
#variable_conflict use_column
DECLARE candidate public.async_job%ROWTYPE; claimed public.async_job%ROWTYPE;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'Kafka async claim role denied' USING ERRCODE='42501'; END IF;
  IF p_worker !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$' OR p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$'
     OR p_job_id !~ '^job_[A-Za-z0-9_-]{8,96}$' OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid Kafka async claim' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.event_outbox source
    WHERE source.event_id=p_event_id AND source.event_type=p_event_type
      AND source.aggregate_type='ASYNC_JOB' AND source.aggregate_id=p_job_id
      AND source.partition_key=p_partition_key AND source.schema_version='1.0.0'
      AND source.transport_payload_hash=p_payload_hash
      AND source.status IN ('PENDING','FAILED','PUBLISHED')
  ) THEN RAISE EXCEPTION 'Kafka event is not bound to outbox' USING ERRCODE='42501'; END IF;
  SELECT item.* INTO candidate
  FROM public.async_job item
  WHERE item.job_id=p_job_id
    AND (
      (item.status IN ('REQUESTED','FAILED') AND item.next_attempt_at<=statement_timestamp()
        AND (item.lease_expires_at IS NULL OR item.lease_expires_at<=statement_timestamp()))
      OR (item.status='RUNNING' AND item.lease_expires_at<=statement_timestamp())
    )
  FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN RETURN; END IF;

  IF candidate.status='RUNNING' THEN
    UPDATE public.async_job item
    SET status='FAILED',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
      error_code='LEASE_EXPIRED',error_class='CONTRACT_VIOLATION',error_message='LEASE_EXPIRED',
      next_attempt_at=statement_timestamp(),updated_at=statement_timestamp()
    WHERE item.job_id=candidate.job_id;
    candidate.status := 'FAILED';
  END IF;

  IF candidate.attempt_count>=3
     OR (candidate.hard_deadline_at IS NOT NULL AND candidate.hard_deadline_at<=statement_timestamp()) THEN
    IF candidate.status='REQUESTED' THEN
      UPDATE public.async_job item
      SET status='FAILED',error_code='HARD_DEADLINE_EXCEEDED',error_class='CONTRACT_VIOLATION',
        error_message='HARD_DEADLINE_EXCEEDED',updated_at=statement_timestamp()
      WHERE item.job_id=candidate.job_id;
    END IF;
    UPDATE public.async_job item
    SET status='NEEDS_REVIEW',claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,heartbeat_at=NULL,
      error_code=coalesce(item.error_code,'LEASE_EXPIRED'),
      error_class=coalesce(item.error_class,'CONTRACT_VIOLATION'),
      error_message=coalesce(item.error_message,'LEASE_EXPIRED'),updated_at=statement_timestamp()
    WHERE item.job_id=candidate.job_id AND item.status='FAILED';
    RETURN;
  END IF;

  UPDATE public.async_job item
  SET status='RUNNING',claim_token=gen_random_uuid(),claimed_by=p_worker,
    lease_expires_at=statement_timestamp()+interval '5 minutes',heartbeat_at=statement_timestamp(),
    hard_deadline_at=coalesce(item.hard_deadline_at,statement_timestamp()+interval '15 minutes'),
    attempt_count=item.attempt_count+1,error_code=NULL,error_class=NULL,error_message=NULL,
    started_at=coalesce(item.started_at,statement_timestamp()),updated_at=statement_timestamp()
  WHERE item.job_id=candidate.job_id
  RETURNING item.* INTO claimed;
  RETURN QUERY SELECT claimed.job_id,claimed.job_type,claimed.payload_json,claimed.claim_token,
    claimed.attempt_count,claimed.hard_deadline_at;
END
$claim_async_job_by_event$;

CREATE OR REPLACE FUNCTION public.record_kafka_poison(
  p_event_id text,p_event_type text,p_payload_hash text,p_source_topic text,p_source_partition integer,
  p_source_offset bigint,p_attempt integer,p_failure_code text,p_partition_key text
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $record_kafka_poison$
#variable_conflict use_column
DECLARE existing public.kafka_poison_receipt%ROWTYPE; dlq_event_id text;
BEGIN
  IF session_user <> 'decision_worker' THEN RAISE EXCEPTION 'Kafka poison role denied' USING ERRCODE='42501'; END IF;
  IF p_event_id !~ '^evt_[A-Za-z0-9_-]{8,96}$' OR p_payload_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_source_topic<>p_event_type OR p_source_partition NOT BETWEEN 0 AND 1023 OR p_source_offset<0
     OR p_attempt NOT BETWEEN 1 AND 3 OR p_failure_code !~ '^[A-Z][A-Z0-9_]{2,63}$'
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM public.async_event_registry registry
       WHERE registry.event_type=p_event_type AND registry.topic_name=p_source_topic AND registry.enabled) THEN
    RAISE EXCEPTION 'invalid Kafka poison record' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('s7-kafka-poison-admission',0));
  SELECT * INTO existing FROM public.kafka_poison_receipt receipt
  WHERE receipt.source_topic=p_source_topic AND receipt.source_partition=p_source_partition
    AND receipt.source_offset=p_source_offset FOR UPDATE;
  IF FOUND THEN
    IF existing.event_id=p_event_id AND existing.payload_hash=p_payload_hash
       AND existing.failure_code=p_failure_code THEN RETURN false; END IF;
    RAISE EXCEPTION 'Kafka poison identity conflict' USING ERRCODE='23505';
  END IF;
  IF (SELECT count(*) FROM public.kafka_poison_receipt
      WHERE recorded_at>=statement_timestamp()-interval '1 hour')>=1000
     OR (SELECT count(*) FROM public.kafka_poison_receipt
      WHERE source_topic=p_source_topic AND source_partition=p_source_partition
        AND recorded_at>=statement_timestamp()-interval '1 hour')>=100
     OR (SELECT count(*) FROM public.event_outbox WHERE status='DLQ_REQUESTED')>=1000 THEN
    RAISE EXCEPTION 'Kafka poison admission exhausted' USING ERRCODE='54000';
  END IF;
  INSERT INTO public.kafka_poison_receipt(source_topic,source_partition,source_offset,event_id,payload_hash,failure_code)
  VALUES (p_source_topic,p_source_partition,p_source_offset,p_event_id,p_payload_hash,p_failure_code);
  dlq_event_id:='evt_dlq_'||substr(encode(public.digest(
    p_source_topic||'|'||p_source_partition::text||'|'||p_source_offset::text,'sha256'),'hex'),1,32);
  INSERT INTO public.event_outbox(event_id,event_type,aggregate_type,aggregate_id,partition_key,
    payload_json,schema_version,status,failure_code,error_class,last_error)
  VALUES (dlq_event_id,p_event_type,'ASYNC_DLQ',p_event_id,p_partition_key,
    jsonb_build_object('eventId',p_event_id,'eventType',p_event_type,'payloadHash',p_payload_hash,
      'failureCode',p_failure_code,'sourceTopic',p_source_topic,'attempt',p_attempt),
    '1.0.0','DLQ_REQUESTED',p_failure_code,'CONTRACT_VIOLATION',p_failure_code);
  RETURN true;
END
$record_kafka_poison$;

CREATE TABLE public.p1_offline_demo_authority (
  authority_id text PRIMARY KEY CHECK (authority_id = 'P1_OFFLINE_DEMO'),
  active boolean NOT NULL CHECK (active),
  credential_bundle_digest text NOT NULL CHECK (credential_bundle_digest ~ '^[0-9a-f]{64}$'),
  activated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

ALTER TABLE public.p1_offline_demo_authority OWNER TO flyway;
REVOKE ALL PRIVILEGES ON TABLE public.p1_offline_demo_authority FROM PUBLIC;

CREATE FUNCTION public.stage_p1_synthetic_async_request(p_mode text,p_partition_key text,p_run_id text)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $stage_p1_synthetic_async_request$
#variable_conflict use_column
DECLARE target_job_id text; target_event_id text; payload jsonb; existing_job public.async_job%ROWTYPE;
  existing_event public.event_outbox%ROWTYPE;
BEGIN
  IF session_user <> 'decision_demo' OR p_mode NOT IN ('DB','KAFKA')
     OR p_partition_key !~ '^hmac-sha256:[0-9a-f]{64}$'
     OR p_run_id !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'P1 synthetic async staging denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.p1_offline_demo_authority authority
    WHERE authority.authority_id='P1_OFFLINE_DEMO' AND authority.active
  ) THEN
    RAISE EXCEPTION 'P1 offline demo authority inactive' USING ERRCODE='42501';
  END IF;
  target_job_id:='job_p1_container_'||lower(p_mode)||'_'||p_run_id;
  target_event_id:='evt_p1_container_'||lower(p_mode)||'_'||p_run_id;
  payload:=jsonb_build_object(
    'jobId',target_job_id,
    'ownerRef','usr_demo_user',
    'artifactId','artifact_s8_0ed32aac66088e495ae853bb',
    'contentHash','sha256:0ed32aac66088e495ae853bbac98a35b2c4a22420138bdd58dcdbbb0d9d8ad02'
  );
  PERFORM pg_advisory_xact_lock(hashtextextended('p1-synthetic-async-'||p_mode,8601));
  SELECT * INTO existing_job FROM public.async_job item WHERE item.job_id=target_job_id;
  SELECT * INTO existing_event FROM public.event_outbox item WHERE item.event_id=target_event_id;
  IF existing_event.event_id IS NOT NULL OR existing_job.job_id IS NOT NULL THEN
    IF existing_job.job_id=target_job_id AND existing_job.job_type='ARTIFACT_INGEST'
       AND existing_job.requested_by='usr_demo_user' AND existing_job.payload_json=payload
       AND existing_event.event_id=target_event_id AND existing_event.event_type='artifact.ingest-requested.v1'
       AND existing_event.aggregate_type='ASYNC_JOB' AND existing_event.aggregate_id=target_job_id
       AND existing_event.partition_key=p_partition_key AND existing_event.payload_json=payload
       AND existing_event.schema_version='1.0.0' THEN RETURN target_job_id; END IF;
    RAISE EXCEPTION 'P1 synthetic async identity conflict' USING ERRCODE='23505';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id='usr_demo_user' AND actor.status='ACTIVE') THEN
    RAISE EXCEPTION 'P1 synthetic owner unavailable' USING ERRCODE='55000';
  END IF;
  INSERT INTO public.async_job(job_id,job_type,status,requested_by,payload_json,next_attempt_at)
  VALUES (target_job_id,'ARTIFACT_INGEST','REQUESTED','usr_demo_user',payload,statement_timestamp());
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,next_attempt_at
  ) VALUES (
    target_event_id,'artifact.ingest-requested.v1','ASYNC_JOB',target_job_id,p_partition_key,payload,'1.0.0',statement_timestamp()
  );
  RETURN target_job_id;
END
$stage_p1_synthetic_async_request$;

CREATE FUNCTION public.verify_p1_synthetic_async_request(p_mode text,p_run_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $verify_p1_synthetic_async_request$
DECLARE target_job_id text; target_event_id text;
BEGIN
  IF session_user <> 'decision_demo' OR p_mode NOT IN ('DB','KAFKA')
     OR p_run_id !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'P1 synthetic async verification denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.p1_offline_demo_authority authority
    WHERE authority.authority_id='P1_OFFLINE_DEMO' AND authority.active
  ) THEN
    RAISE EXCEPTION 'P1 offline demo authority inactive' USING ERRCODE='42501';
  END IF;
  target_job_id:='job_p1_container_'||lower(p_mode)||'_'||p_run_id;
  target_event_id:='evt_p1_container_'||lower(p_mode)||'_'||p_run_id;
  RETURN EXISTS (
    SELECT 1 FROM public.async_job job
    JOIN public.async_materialization_receipt receipt ON receipt.job_id=job.job_id AND receipt.event_id=target_event_id
    JOIN public.processed_event processed ON processed.event_id=target_event_id
      AND processed.consumer_name='python-async-worker-v1' AND NOT processed.payload_hash_conflict
    JOIN public.event_outbox requested ON requested.event_id=target_event_id AND requested.aggregate_id=job.job_id
    WHERE job.job_id=target_job_id AND job.status='COMPLETED' AND job.result_json->>'resultRef'=receipt.result_ref
      AND requested.status='PUBLISHED'
  );
END
$verify_p1_synthetic_async_request$;

CREATE FUNCTION public.reject_direct_p1_audit_write()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $reject_direct_p1_audit_write$
BEGIN
  IF current_user <> 'flyway'
     AND NOT EXISTS (
       SELECT 1
       FROM pg_catalog.pg_roles
       WHERE rolname = current_user
         AND rolsuper
     ) THEN
    RAISE EXCEPTION 'P1 audit capability required' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
END
$reject_direct_p1_audit_write$;

CREATE TRIGGER p1_audit_capability_guard
BEFORE INSERT ON public.audit_logs
FOR EACH ROW EXECUTE FUNCTION public.reject_direct_p1_audit_write();

ALTER FUNCTION public.transition_kill_switch_authorized(text,text,bigint,boolean,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.read_demo_credentials() OWNER TO flyway;
ALTER FUNCTION public.read_user_actor(text) OWNER TO flyway;
ALTER FUNCTION public.persist_decision_bundle_authorized(text,jsonb) OWNER TO flyway;
ALTER FUNCTION public.claim_async_job_by_event(text,text,text,text,text,text) OWNER TO flyway;
ALTER FUNCTION public.record_kafka_poison(text,text,text,text,integer,bigint,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.stage_p1_synthetic_async_request(text,text,text) OWNER TO flyway;
ALTER FUNCTION public.verify_p1_synthetic_async_request(text,text) OWNER TO flyway;
ALTER FUNCTION public.reject_direct_p1_audit_write() OWNER TO flyway;

REVOKE ALL ON FUNCTION public.transition_kill_switch_authorized(text,text,bigint,boolean,bigint,text),
  public.persist_decision_bundle_authorized(text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.transition_kill_switch_authorized(text,text,bigint,boolean,bigint,text),
  public.persist_decision_bundle_authorized(text,jsonb) TO decision_app;

REVOKE UPDATE ON TABLE public.risk_kill_switch FROM decision_app;
REVOKE INSERT ON TABLE public.risk_kill_switch_transitions FROM decision_app;
REVOKE INSERT ON TABLE public.audit_logs FROM decision_app;
REVOKE INSERT ON TABLE public.decisions,public.decision_violations,public.decision_artifacts,
  public.decision_traces,public.decision_idempotency_results FROM decision_app;
REVOKE EXECUTE ON FUNCTION public.append_decision_created_outbox(text,text,jsonb,timestamptz),
  public.append_kill_switch_outbox(text,boolean,timestamptz),
  public.invalidate_unused_decisions_for_kill_switch(bigint,timestamptz,text),
  public.revalidate_kill_switch_admin(text,bigint) FROM decision_app;

GRANT SELECT ON TABLE public.users TO flyway;
GRANT SELECT,INSERT ON TABLE public.decisions,public.decision_violations,public.decision_artifacts,
  public.decision_traces,public.decision_idempotency_results TO flyway;
GRANT SELECT,UPDATE ON TABLE public.risk_kill_switch TO flyway;
GRANT INSERT ON TABLE public.risk_kill_switch_transitions,public.decision_invalidations,
  public.audit_logs,public.event_outbox,public.async_job_transition_audit TO flyway;

REVOKE EXECUTE ON FUNCTION public.read_demo_credentials() FROM PUBLIC,decision_app;
REVOKE EXECUTE ON FUNCTION public.read_user_actor(text) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.read_demo_credentials(),public.read_user_actor(text) TO decision_auth;
REVOKE ALL ON FUNCTION public.stage_p1_synthetic_async_request(text,text,text),
  public.verify_p1_synthetic_async_request(text,text) FROM PUBLIC,decision_app,decision_worker;
GRANT EXECUTE ON FUNCTION public.stage_p1_synthetic_async_request(text,text,text),
  public.verify_p1_synthetic_async_request(text,text) TO decision_demo;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_auth;
GRANT USAGE ON SCHEMA public TO decision_auth;
REVOKE CREATE ON SCHEMA public FROM decision_auth;
