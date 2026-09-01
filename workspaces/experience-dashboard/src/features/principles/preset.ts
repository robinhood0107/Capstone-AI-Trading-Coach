import type { PrincipleRule } from '@/shared/api/wire';

function canonicalRules(rules: PrincipleRule[]) {
  return rules
    .map(
      (rule) =>
        [
          rule.ruleId,
          rule.ruleType,
          rule.metric,
          rule.operator,
          rule.threshold,
          rule.severity,
          rule.enabled,
          rule.evidenceRequirement,
        ] as const,
    )
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
}

/** 현재 draft 전체가 preset과 같을 때만 그 preset을 선택 상태로 표시한다. */
export function matchesPreset(draft: PrincipleRule[], presetRules: PrincipleRule[]): boolean {
  if (draft.length !== presetRules.length) return false;
  return JSON.stringify(canonicalRules(draft)) === JSON.stringify(canonicalRules(presetRules));
}
