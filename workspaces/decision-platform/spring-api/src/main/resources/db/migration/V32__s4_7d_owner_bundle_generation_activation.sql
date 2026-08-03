-- V25 owner bundle activation은 pointer/bundle만 ACTIVE로 전환하고 component generation은
-- EVALUATED에 남겼다. V29 scope resolver는 ACTIVE component만 허용하므로 pointer transition과
-- 같은 transaction에서 generation state를 바꾸는 trigger를 추가한다.

CREATE FUNCTION transition_rag_v2_immutable_owner_component_generation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $transition_rag_v2_immutable_owner_component_generation$
DECLARE
  next_generation_id text;
  previous_generation_id text;
  activation_timestamp timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR NEW.owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR NEW.state <> 'READY'
     OR NEW.active_bundle_id IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 owner component activation trigger is not authorized'
      USING ERRCODE = '42501';
  END IF;

  SELECT bundle.owner_private_generation_id
  INTO next_generation_id
  FROM public.rag_v2_immutable_bundles AS bundle
  WHERE bundle.bundle_id = NEW.active_bundle_id
    AND bundle.owner_user_id = NEW.owner_user_id
    AND bundle.state = 'ACTIVE'
    AND bundle.evaluation_status = 'PASSED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 active owner bundle has no evaluated component'
      USING ERRCODE = '23514';
  END IF;

  IF TG_OP = 'UPDATE' AND OLD.active_bundle_id IS NOT NULL
     AND OLD.active_bundle_id IS DISTINCT FROM NEW.active_bundle_id THEN
    SELECT bundle.owner_private_generation_id
    INTO previous_generation_id
    FROM public.rag_v2_immutable_bundles AS bundle
    WHERE bundle.bundle_id = OLD.active_bundle_id
      AND bundle.owner_user_id = NEW.owner_user_id;
  END IF;

  UPDATE public.rag_v2_immutable_component_generations
  SET state = 'SUPERSEDED'
  WHERE component_generation_id = previous_generation_id
    AND owner_user_id = NEW.owner_user_id
    AND component_scope = 'OWNER_PRIVATE'
    AND state = 'ACTIVE';
  UPDATE public.rag_v2_immutable_component_generations
  SET state = 'ACTIVE',
      activated_at = coalesce(activated_at, activation_timestamp)
  WHERE component_generation_id = next_generation_id
    AND owner_user_id = NEW.owner_user_id
    AND component_scope = 'OWNER_PRIVATE'
    AND state = 'EVALUATED'
    AND evaluation_status = 'PASSED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner component activation transition failed'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$transition_rag_v2_immutable_owner_component_generation$;
ALTER FUNCTION transition_rag_v2_immutable_owner_component_generation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION transition_rag_v2_immutable_owner_component_generation() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_owner_pointer_component_activation
AFTER INSERT OR UPDATE OF state, active_bundle_id ON rag_v2_immutable_owner_bundle_pointers
FOR EACH ROW
WHEN (NEW.state = 'READY' AND NEW.active_bundle_id IS NOT NULL)
EXECUTE FUNCTION transition_rag_v2_immutable_owner_component_generation();
