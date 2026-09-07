-- Owner-authorized removal of the per-order KRW ceiling for BUY and SELL.
-- Keep the historical rule tuple and the eight-rule API contract; disable enforcement.
UPDATE public.principle_presets preset SET rules_json=(
 SELECT jsonb_agg(CASE WHEN rule->>'ruleId'='max_single_order_amount'
   THEN rule||'{"enabled":false,"severity":"ALLOW"}'::jsonb ELSE rule END ORDER BY ordinal)
 FROM jsonb_array_elements(preset.rules_json) WITH ORDINALITY AS item(rule,ordinal)
);
