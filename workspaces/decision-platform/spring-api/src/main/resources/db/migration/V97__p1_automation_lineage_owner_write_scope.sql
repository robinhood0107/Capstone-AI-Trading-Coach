-- V96과 같은 결함이 인접 테이블에 하나 더 있다. `automation_account_lineage`의 정책은 읽기는
-- `decision_app`에게 열어 두었지만 쓰기(WITH CHECK)는 `decision_automation_runtime`에게만 열어
-- 두었다. 그런데 `p1_arm_automation_v2`(V91)는 arm 시점에 `ARM_BASELINE` 계보 행을 직접 넣고,
-- 그 함수는 Spring 경로에서 `decision_app`으로 실행된다.
--
-- 그래서 V96으로 스케줄 삽입을 연 뒤에도 arm은 바로 다음 문장에서
-- "new row violates row-level security policy"로 42501을 내고 403으로 보였다.
--
-- 이 확장은 arm이 계좌 기대치의 출발점을 세우는 정상 동작을 위한 것이다. 조건은 이미 열려 있는
-- 읽기 절과 같고, 여기에 `CURRENT_USER='flyway'`를 더해 flyway 소유 SECURITY DEFINER 함수
-- 안에서만 성립하게 한다. 테이블 GRANT는 바꾸지 않으므로 `decision_app`의 직접 쓰기는 열리지
-- 않는다. 체결로 인한 계보 전진은 계속 runtime 전용(`p1_advance_automation_account_lineage_v3`)이다.
--
-- 같은 감사에서 `automation_activation_gate`와 `automation_order_reservations`도 읽기만 열려
-- 있는 것을 확인했지만, 그 둘은 `decision_app`이 쓰지 않으므로 그대로 둔다.

DROP POLICY automation_account_lineage_owner_v91 ON public.automation_account_lineage;

CREATE POLICY automation_account_lineage_owner_v97 ON public.automation_account_lineage
TO PUBLIC
USING (
  (
    SESSION_USER = 'decision_app'
    AND user_id = current_setting('app.actor_user_id', true)
    AND public.actor_rls_scope_is_open_v1()
  )
  OR (
    CURRENT_USER = 'flyway'
    AND SESSION_USER = 'decision_automation_runtime'
    AND user_id = current_setting('app.automation_owner_user_id', true)
  )
)
WITH CHECK (
  (
    CURRENT_USER = 'flyway'
    AND SESSION_USER = 'decision_app'
    AND user_id = current_setting('app.actor_user_id', true)
    AND public.actor_rls_scope_is_open_v1()
  )
  OR (
    CURRENT_USER = 'flyway'
    AND SESSION_USER = 'decision_automation_runtime'
    AND user_id = current_setting('app.automation_owner_user_id', true)
  )
);
