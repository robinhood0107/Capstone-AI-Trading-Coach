-- V91이 Spring 경로의 arm v2(`p1_arm_automation_v2`)를 추가하면서 그 함수가 쓰는
-- `automation_runtime_schedule`의 RLS 정책은 넓히지 않았다. 그 정책은 V90에서 만들어졌고
-- runtime CLI만 쓰던 시절의 것이라 `SESSION_USER`를 `decision_automation_runtime`과
-- `decision_replay_authorizer` 둘로만 제한한다.
--
-- 그래서 `POST /api/v2/automation/arm`은 다른 모든 게이트가 열려도 스케줄 행을 넣는 순간
-- "new row violates row-level security policy"로 42501을 내고 호출자에게는 403으로 보인다.
-- 즉 자동운용을 API로 개시하는 것이 구조적으로 불가능했다.
--
-- 같은 V91이 추가한 `automation_policy_versions`는 이 문제를 제대로 처리했다. 그 정책의
-- 형태를 그대로 따른다 — `CURRENT_USER='flyway'`를 함께 요구하므로 이 절은 flyway 소유
-- SECURITY DEFINER 함수 안에서만 성립하고, `decision_app`의 직접 테이블 접근을 열지 않는다.
-- 테이블 GRANT도 바꾸지 않는다.
--
-- `automation_claim_scan` 예외는 USING에만 남긴다. runtime이 소유자를 모르는 상태에서 다음
-- 세션을 훑어야 하기 때문이며, 쓰기(WITH CHECK)에는 계속 적용하지 않는다.

DROP POLICY automation_runtime_schedule_scope_v90 ON public.automation_runtime_schedule;

CREATE POLICY automation_runtime_schedule_scope_v96 ON public.automation_runtime_schedule
TO PUBLIC
USING (
  (
    CURRENT_USER = 'flyway'
    AND SESSION_USER = 'decision_app'
    AND user_id = current_setting('app.actor_user_id', true)
    AND public.actor_rls_scope_is_open_v1()
  )
  OR (
    CURRENT_USER = 'flyway'
    AND SESSION_USER IN ('decision_automation_runtime', 'decision_replay_authorizer')
    AND (
      user_id = current_setting('app.automation_owner_user_id', true)
      OR current_setting('app.automation_claim_scan', true) = '1'
    )
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
    AND SESSION_USER IN ('decision_automation_runtime', 'decision_replay_authorizer')
    AND user_id = current_setting('app.automation_owner_user_id', true)
  )
);
