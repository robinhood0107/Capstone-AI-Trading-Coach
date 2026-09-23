-- The operator selected a $1.00/day initial ceiling. Only the untouched
-- migration seed is advanced; an ADMIN's explicit zero or lower value wins.
-- This temporary policy exists solely for the restricted Flyway login and is
-- removed in the same migration before any application connection can use it.
CREATE POLICY operator_ai_budget_seed_v203 ON public.operator_ai_budget_policy
  TO PUBLIC USING (current_user = 'flyway' AND session_user = 'flyway')
  WITH CHECK (current_user = 'flyway' AND session_user = 'flyway');

UPDATE public.operator_ai_budget_policy
SET daily_soft_cap_microusd = 1000000,
    revision = revision + 1,
    changed_at = statement_timestamp()
WHERE singleton AND revision = 1 AND changed_by IS NULL
  AND daily_soft_cap_microusd = 0;

DROP POLICY operator_ai_budget_seed_v203 ON public.operator_ai_budget_policy;
