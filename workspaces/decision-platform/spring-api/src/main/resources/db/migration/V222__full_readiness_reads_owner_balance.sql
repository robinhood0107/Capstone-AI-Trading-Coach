-- FULL 자동운용 bridge(MOCK_CREDENTIAL)는 Spring(decision_app) 에서
-- p1_full_owner_connection_readiness_v1 로 읽기 연결 증명을 확인한다. 그 함수는 잔고 관측을
-- 조인하는데, portfolio_balance_observations 의 RLS 는 decision_automation_runtime 만 열어 두어
-- decision_app 경로에서는 행이 보이지 않았다. 그래서 준비도가 항상 false 가 되고 bridge 가
-- BROKERAGE_CREDENTIAL_NOT_READY 로 닫혀 FULL 세션이 후보 선별 전에 멈췄다.
-- 연결 증명 표(full_owner_mock_connection_proofs_v217)와 같은 경계로, definer 함수 안에서
-- 자기 소유 행만 읽게 연다. 쓰기 권한은 늘리지 않는다.
DROP POLICY IF EXISTS portfolio_balance_full_owner_readiness_v222
  ON public.portfolio_balance_observations;
CREATE POLICY portfolio_balance_full_owner_readiness_v222
  ON public.portfolio_balance_observations FOR SELECT TO PUBLIC
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_app'
    AND owner_user_id = pg_catalog.current_setting('app.actor_user_id', true)
  );
