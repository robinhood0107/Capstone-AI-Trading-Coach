-- 적용된 V163 bytes를 보존하고, 편입 대상 탐색 전에 runtime owner RLS context를 연다.
CREATE FUNCTION public.p1_adopt_current_automation_position_v1(
  p_transition_id text,p_user_id text,p_effective_session date,p_apply boolean
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $adopt_current$
DECLARE control public.automation_control%ROWTYPE;
DECLARE position public.automation_positions%ROWTYPE;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_transition_id!~'^auto_adopt_[0-9a-f]{32}$'
     OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$' OR p_effective_session IS NULL OR p_apply IS NULL THEN
    RAISE EXCEPTION 'automation current position adoption input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT * INTO control FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  SELECT * INTO position FROM public.automation_positions item
  WHERE item.user_id=p_user_id AND item.account_id=control.account_id
    AND item.bot_owned AND item.status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
  IF control.user_id IS NULL OR position.position_id IS NULL OR (
    SELECT count(*) FROM public.automation_positions item
    WHERE item.user_id=p_user_id AND item.account_id=control.account_id
      AND item.bot_owned AND item.status IN ('OPEN','EXIT_PENDING')
  )<>1 THEN
    RAISE EXCEPTION 'automation current position adoption target drift' USING ERRCODE='40001';
  END IF;
  RETURN public.p1_adopt_automation_position_v1(
    p_transition_id,p_user_id,control.account_id,position.position_id,p_effective_session,p_apply
  );
END $adopt_current$;

ALTER FUNCTION public.p1_adopt_current_automation_position_v1(text,text,date,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_adopt_current_automation_position_v1(text,text,date,boolean)
  FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_adopt_current_automation_position_v1(text,text,date,boolean)
  TO decision_automation_runtime;
