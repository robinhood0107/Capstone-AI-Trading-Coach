-- 무장 응답은 같은 트랜잭션 안에서 상태를 다시 읽는다. V218 의 자격증명 상태 함수가
-- READ_AUTOMATION_STATUS 스코프만 받아서, ARM_AUTOMATION 스코프로 연 무장 요청이 42501 로
-- 거부되고 화면에는 "이 자료에 접근할 권한이 없습니다."만 남았다. 같은 소유자·같은 대상이면
-- 무장 스코프에서도 상태 문자열 하나만 읽게 넓힌다. 자격증명 본문은 여전히 나가지 않는다.
CREATE OR REPLACE FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(p_owner_user_id text)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_owner_mock_credential_state_for_automation_v218$
DECLARE credential_state_value text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true) IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.actor_rls_scope_v1 scope
       WHERE scope.backend_pid=pg_backend_pid() AND scope.transaction_id=txid_current()
         AND scope.actor_user_id=p_owner_user_id
         AND scope.operation IN ('READ_AUTOMATION_STATUS','ARM_AUTOMATION')
         AND scope.target_kind='AUTOMATION' AND scope.target_id=p_owner_user_id
         AND scope.expires_at>statement_timestamp()
     ) THEN
    RAISE EXCEPTION 'automation credential state scope denied' USING ERRCODE='42501';
  END IF;
  SELECT credential.credential_state INTO credential_state_value
  FROM public.user_broker_credentials credential
  WHERE credential.owner_user_id=p_owner_user_id
    AND credential.brokerage_mode='KIS_MOCK';
  RETURN credential_state_value;
END
$p1_read_owner_mock_credential_state_for_automation_v218$;
ALTER FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_owner_mock_credential_state_for_automation_v218(text)
  TO decision_app;
