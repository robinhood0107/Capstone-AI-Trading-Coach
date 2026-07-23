-- S2.1은 sparse V1 Principle을 추정 복원하지 않는다. 첫 실행 블록이 기존 row를 발견하면 DDL 전에 중단한다.
DO $v8_precondition$
DECLARE
  principle_count bigint;
  version_count bigint;
  preset_count bigint;
BEGIN
  SELECT count(*) INTO principle_count FROM principles;
  SELECT count(*) INTO version_count FROM principle_versions;
  SELECT count(*) INTO preset_count FROM principle_presets;

  IF principle_count <> 0 OR version_count <> 0 THEN
    RAISE EXCEPTION
      'S2.1 V8 precondition failed: principles=% principle_versions=%; deploy requires both counts to be zero',
      principle_count,
      version_count;
  END IF;
  IF preset_count <> 0 THEN
    RAISE EXCEPTION
      'S2.1 V8 preset identity conflict: expected an empty legacy preset table but found % row(s)',
      preset_count;
  END IF;
END
$v8_precondition$;

ALTER TABLE principle_presets RENAME COLUMN name TO name_ko;
ALTER TABLE principle_presets
  ADD COLUMN name_en text,
  ADD COLUMN description_ko text,
  ADD COLUMN description_en text,
  ADD COLUMN display_order integer;
ALTER TABLE principle_presets
  ALTER COLUMN name_en SET NOT NULL,
  ALTER COLUMN description_ko SET NOT NULL,
  ALTER COLUMN description_en SET NOT NULL,
  ALTER COLUMN display_order SET NOT NULL;
ALTER TABLE principle_presets
  ADD CONSTRAINT principle_presets_identity_check
    CHECK (
      (preset_id = 'conservative' AND display_order = 1)
      OR (preset_id = 'balanced' AND display_order = 2)
      OR (preset_id = 'aggressive' AND display_order = 3)
    ),
  ADD CONSTRAINT principle_presets_rules_check
    CHECK (jsonb_typeof(rules_json) = 'array' AND jsonb_array_length(rules_json) = 8),
  ADD CONSTRAINT principle_presets_display_order_unique UNIQUE (display_order);

INSERT INTO principle_presets (
  preset_id,
  name_ko,
  name_en,
  description_ko,
  description_en,
  mode,
  rules_json,
  is_active,
  display_order
)
VALUES
(
  'conservative',
  '보수형',
  'Conservative',
  '손실 제한과 분산투자를 우선하는 데모 원칙',
  'Demo principle prioritizing loss limits and diversification',
  'GUIDE',
  $json$[
    {"ruleId":"max_position_per_asset","ruleType":"POSITION_LIMIT","metric":"asset_weight","operator":"<=","threshold":0.15,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_gold_etf_etn_weight","ruleType":"POSITION_LIMIT","metric":"gold_etf_etn_weight","operator":"<=","threshold":0.2,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_single_order_amount","ruleType":"ORDER_SIZE","metric":"order_amount_krw","operator":"<=","threshold":300000,"severity":"BLOCK","enabled":true},
    {"ruleId":"daily_loss_guard","ruleType":"LOSS_LIMIT","metric":"daily_loss_rate","operator":">=","threshold":-0.02,"severity":"BLOCK","enabled":true},
    {"ruleId":"mdd_guard","ruleType":"DRAWDOWN_LIMIT","metric":"mdd","operator":">=","threshold":-0.1,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_daily_orders","ruleType":"TRADING_FREQUENCY","metric":"daily_order_count","operator":"<=","threshold":2,"severity":"BLOCK","enabled":true},
    {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score","operator":"<=","threshold":0.5,"severity":"ALLOW","enabled":false},
    {"ruleId":"disclosure_risk_guard","ruleType":"DISCLOSURE_GUARD","metric":"disclosure_risk_score","operator":"<=","threshold":0.5,"severity":"ALLOW","enabled":false}
  ]$json$::jsonb,
  true,
  1
),
(
  'balanced',
  '균형형',
  'Balanced',
  '위험 제한과 거래 기회를 균형 있게 적용하는 데모 원칙',
  'Demo principle balancing risk limits and trading flexibility',
  'GUIDE',
  $json$[
    {"ruleId":"max_position_per_asset","ruleType":"POSITION_LIMIT","metric":"asset_weight","operator":"<=","threshold":0.2,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_gold_etf_etn_weight","ruleType":"POSITION_LIMIT","metric":"gold_etf_etn_weight","operator":"<=","threshold":0.3,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_single_order_amount","ruleType":"ORDER_SIZE","metric":"order_amount_krw","operator":"<=","threshold":500000,"severity":"BLOCK","enabled":true},
    {"ruleId":"daily_loss_guard","ruleType":"LOSS_LIMIT","metric":"daily_loss_rate","operator":">=","threshold":-0.03,"severity":"BLOCK","enabled":true},
    {"ruleId":"mdd_guard","ruleType":"DRAWDOWN_LIMIT","metric":"mdd","operator":">=","threshold":-0.15,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_daily_orders","ruleType":"TRADING_FREQUENCY","metric":"daily_order_count","operator":"<=","threshold":3,"severity":"WARN","enabled":true},
    {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score","operator":"<=","threshold":0.7,"severity":"ALLOW","enabled":false},
    {"ruleId":"disclosure_risk_guard","ruleType":"DISCLOSURE_GUARD","metric":"disclosure_risk_score","operator":"<=","threshold":0.7,"severity":"ALLOW","enabled":false}
  ]$json$::jsonb,
  true,
  2
),
(
  'aggressive',
  '공격형',
  'Aggressive',
  '더 넓은 위험 한도 안에서도 핵심 손실 제한을 유지하는 데모 원칙',
  'Demo principle retaining core loss controls within wider risk limits',
  'GUIDE',
  $json$[
    {"ruleId":"max_position_per_asset","ruleType":"POSITION_LIMIT","metric":"asset_weight","operator":"<=","threshold":0.3,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_gold_etf_etn_weight","ruleType":"POSITION_LIMIT","metric":"gold_etf_etn_weight","operator":"<=","threshold":0.4,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_single_order_amount","ruleType":"ORDER_SIZE","metric":"order_amount_krw","operator":"<=","threshold":1000000,"severity":"BLOCK","enabled":true},
    {"ruleId":"daily_loss_guard","ruleType":"LOSS_LIMIT","metric":"daily_loss_rate","operator":">=","threshold":-0.05,"severity":"BLOCK","enabled":true},
    {"ruleId":"mdd_guard","ruleType":"DRAWDOWN_LIMIT","metric":"mdd","operator":">=","threshold":-0.25,"severity":"BLOCK","enabled":true},
    {"ruleId":"max_daily_orders","ruleType":"TRADING_FREQUENCY","metric":"daily_order_count","operator":"<=","threshold":5,"severity":"WARN","enabled":true},
    {"ruleId":"negative_news_guard","ruleType":"NEWS_GUARD","metric":"negative_news_score","operator":"<=","threshold":0.85,"severity":"ALLOW","enabled":false},
    {"ruleId":"disclosure_risk_guard","ruleType":"DISCLOSURE_GUARD","metric":"disclosure_risk_score","operator":"<=","threshold":0.85,"severity":"ALLOW","enabled":false}
  ]$json$::jsonb,
  true,
  3
);

ALTER TABLE principles
  DROP CONSTRAINT principles_user_id_fkey,
  DROP CONSTRAINT principles_preset_id_fkey,
  DROP CONSTRAINT principles_status_check,
  DROP CONSTRAINT principles_current_version_check;
ALTER TABLE principles RENAME COLUMN name TO title;
ALTER TABLE principles
  ALTER COLUMN preset_id SET NOT NULL,
  ALTER COLUMN current_version SET DEFAULT 1;
ALTER TABLE principles
  ADD CONSTRAINT principles_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
  ADD CONSTRAINT principles_preset_id_fkey
    FOREIGN KEY (preset_id) REFERENCES principle_presets(preset_id) ON DELETE RESTRICT,
  ADD CONSTRAINT principles_title_check CHECK (char_length(title) BETWEEN 1 AND 120),
  ADD CONSTRAINT principles_status_check CHECK (status IN ('ACTIVE', 'ARCHIVED')),
  ADD CONSTRAINT principles_current_version_check CHECK (current_version >= 1);
CREATE INDEX principles_owner_updated_idx
  ON principles (user_id, updated_at, principle_id);
CREATE INDEX principles_owner_id_idx
  ON principles (user_id, principle_id);

ALTER TABLE principle_versions
  DROP CONSTRAINT principle_versions_principle_id_fkey,
  DROP CONSTRAINT principle_versions_created_by_fkey;
ALTER TABLE principle_versions
  ADD COLUMN preset_id text,
  ADD COLUMN title text,
  ADD COLUMN mode text,
  ADD COLUMN status text,
  ADD COLUMN changed_fields text[];
ALTER TABLE principle_versions
  ALTER COLUMN preset_id SET NOT NULL,
  ALTER COLUMN title SET NOT NULL,
  ALTER COLUMN mode SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN changed_fields SET NOT NULL,
  ALTER COLUMN created_by SET NOT NULL;
ALTER TABLE principle_versions DROP COLUMN summary;
ALTER TABLE principle_versions
  ADD CONSTRAINT principle_versions_principle_id_fkey
    FOREIGN KEY (principle_id) REFERENCES principles(principle_id) ON DELETE RESTRICT,
  ADD CONSTRAINT principle_versions_preset_id_fkey
    FOREIGN KEY (preset_id) REFERENCES principle_presets(preset_id) ON DELETE RESTRICT,
  ADD CONSTRAINT principle_versions_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE RESTRICT,
  ADD CONSTRAINT principle_versions_title_check CHECK (char_length(title) BETWEEN 1 AND 120),
  ADD CONSTRAINT principle_versions_mode_check CHECK (mode IN ('GUIDE', 'STRICT')),
  ADD CONSTRAINT principle_versions_status_check CHECK (status IN ('ACTIVE', 'ARCHIVED')),
  ADD CONSTRAINT principle_versions_rules_check
    CHECK (
      jsonb_typeof(rules_json) = 'array'
      AND jsonb_array_length(rules_json) BETWEEN 1 AND 8
    ),
  ADD CONSTRAINT principle_versions_changed_fields_check
    CHECK (
      cardinality(changed_fields) BETWEEN 1 AND 5
      AND changed_fields <@ ARRAY['presetId', 'title', 'mode', 'status', 'rules']::text[]
    );

-- 기존 auth audit action은 그대로 허용하고 target_type=PRINCIPLE인 row에만 S2.1 allowlist와 최소 payload를 적용한다.
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_principle_contract_check
    CHECK (
      target_type <> 'PRINCIPLE'
      OR (
        action IN (
          'PRINCIPLE_CREATED',
          'PRINCIPLE_UPDATED',
          'PRINCIPLE_ARCHIVED',
          'PRINCIPLE_REACTIVATED'
        )
        AND jsonb_typeof(payload_json) = 'object'
        AND payload_json ?& ARRAY['principleId', 'newVersion', 'changedFields']
        AND payload_json - ARRAY['principleId', 'newVersion', 'changedFields'] = '{}'::jsonb
      )
    );

REVOKE ALL PRIVILEGES ON TABLE
  principle_presets,
  principles,
  principle_versions,
  audit_logs
FROM PUBLIC;

DO $principle_privileges$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    -- V5의 broad SELECT와 과거 grant를 target table에서 제거한 뒤 승인된 operation만 다시 부여한다.
    REVOKE ALL PRIVILEGES ON TABLE
      users,
      principle_presets,
      principles,
      principle_versions,
      audit_logs
    FROM decision_app;
    GRANT SELECT ON TABLE users, principle_presets TO decision_app;
    GRANT SELECT, INSERT ON TABLE principles, principle_versions TO decision_app;
    GRANT UPDATE (title, mode, status, current_version, updated_at)
      ON TABLE principles TO decision_app;
    GRANT INSERT ON TABLE audit_logs TO decision_app;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    REVOKE ALL PRIVILEGES ON TABLE flyway_schema_history FROM decision_app;
  END IF;
END
$principle_privileges$;
