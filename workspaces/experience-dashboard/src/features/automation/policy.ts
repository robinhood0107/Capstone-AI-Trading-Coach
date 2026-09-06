import type {
  AutomationBlocker,
  AutomationBlockerV3,
  AutomationExitReasonV3,
  AutomationPresetId,
  AutomationProjectionState,
  MarketHistoryStatus,
} from '@/shared/api/wire';

export interface AutomationPolicyValues {
  capitalLimitKrw: number;
  stopLossBps: number;
  takeProfitBps: number;
}

export interface AutomationPreset {
  presetId: Exclude<AutomationPresetId, 'custom'>;
  label: string;
  stopLossBps: number;
  takeProfitBps: number;
  description: string;
}

export const AUTOMATION_PRESETS: readonly AutomationPreset[] = [
  {
    presetId: 'conservative',
    label: '보수',
    stopLossBps: 300,
    takeProfitBps: 500,
    description: '한국시장 3% dynamic VI와 좁은 실증 후보 범위를 참고한 기본값입니다.',
  },
  {
    presetId: 'balanced',
    label: '균형',
    stopLossBps: 500,
    takeProfitBps: 1000,
    description: '일상 변동보다 넓은 손실 허용과 한국시장 10% static VI 경계를 참고했습니다.',
  },
  {
    presetId: 'aggressive',
    label: '공격',
    stopLossBps: 800,
    takeProfitBps: 1500,
    description: '넓은 실증 후보 범위를 허용하며 손실 폭도 커질 수 있습니다.',
  },
] as const;

export const AUTOMATION_EVIDENCE_LINKS = [
  {
    label: '좁은 손절과 거래비용 연구',
    href: 'https://www.sciencedirect.com/science/article/pii/S1386418117300472',
  },
  {
    label: '일별 주식 손절·익절 후보 실증연구',
    href: 'https://www.mdpi.com/2227-7390/9/10/1093',
  },
  {
    label: '한국시장 dynamic·static VI 연구',
    href: 'https://www.mdpi.com/1911-8074/15/3/105',
  },
] as const;

export const AUTOMATION_BLOCKER_LABELS: Record<AutomationBlocker, string> = {
  ACCOUNT_NOT_CONFIGURED: 'KIS 계좌 설정이 필요합니다.',
  POLICY_NOT_CONFIGURED: '예산·손절·익절 정책을 먼저 저장해야 합니다.',
  POLICY_VERSION_DRIFT: '저장된 정책 버전이 바뀌었습니다. 최신 값을 다시 확인하세요.',
  PRINCIPLE_NOT_CONFIGURED: '활성 투자 원칙이 없습니다.',
  REAL_TEAM_B_POINTER_INACTIVE: '검증된 Team B 실제 전략이 활성화되지 않았습니다.',
  RELEASE_BINDING_UNCLEAN: '릴리스 바인딩이 검증된 산출물과 일치하지 않습니다.',
  CERTIFICATION_INVALID: 'KIS 모의계좌 인증 근거가 유효하지 않습니다.',
  KILL_SWITCH_ACTIVE: 'Kill Switch가 작동 중입니다.',
  UNRESOLVED_RECONCILIATION: '확정되지 않은 주문 대사가 남아 있습니다.',
  CONTROL_HALTED: '자동운용이 안전 중단 상태입니다.',
  BLOCKED_INCOMPLETE_RISK_BALANCE:
    '완전한 온라인 위험 잔고 근거가 없어 자동운용을 시작할 수 없습니다.',
};

/**
 * v3 가 더 내려보내는 차단 사유 여섯 가지.
 *
 * v2 계약에는 없다 — v3 status 를 보는 화면에서만 나타난다. `Record<AutomationBlockerV3, …>`
 * 라 새 사유가 계약에 붙으면 타입이 먼저 깨진다. 라벨 없이 빈 항목이 렌더되는 일이 없다.
 */
export const AUTOMATION_BLOCKER_LABELS_V3: Record<AutomationBlockerV3, string> = {
  ...AUTOMATION_BLOCKER_LABELS,
  POLICY_V3_REQUIRED: 'v3 정책을 먼저 저장해야 합니다.',
  LEGACY_POSITION_PRESENT: '봇이 만들지 않은 포지션이 남아 있습니다. 먼저 정리해야 합니다.',
  MARKET_HISTORY_EMPTY: '시세 이력이 없습니다. ATR과 추적손절을 계산할 수 없습니다.',
  MARKET_HISTORY_INSUFFICIENT: '시세 이력이 ATR 계산에 필요한 만큼 쌓이지 않았습니다.',
  MARKET_DATA_CATCHUP_REQUIRED: '시세 이력이 밀려 있습니다. 따라잡기가 끝나야 시작할 수 있습니다.',
  AI_PROVIDER_NOT_READY: 'AI 판단 제공자가 준비되지 않았습니다.',
};

export const MARKET_HISTORY_LABELS: Record<MarketHistoryStatus, string> = {
  EMPTY: '없음',
  PARTIAL: '부족',
  READY: '준비됨',
  CATCHUP_REQUIRED: '따라잡기 필요',
};

/** v3 가 추가한 청산 사유까지 포함한다. */
export const AUTOMATION_EXIT_REASON_LABELS: Record<AutomationExitReasonV3, string> = {
  STOP_LOSS: '손절',
  TAKE_PROFIT: '익절',
  ATR_TRAILING: 'ATR 추적손절',
  MAX_HOLDING_SESSIONS: '보유 기간 초과',
  MODEL_SELL: '모델 매도 신호',
};

export const AUTOMATION_STATE_LABELS: Record<AutomationProjectionState, string> = {
  DISARMED: '꺼짐',
  ARMED: '켜짐 · 장 시작 대기',
  RUNNING: '실행 중',
  HALTED: '안전 중단',
};

export function presetFor(stopLossBps: number, takeProfitBps: number): AutomationPresetId {
  return (
    AUTOMATION_PRESETS.find(
      (preset) =>
        preset.stopLossBps === stopLossBps && preset.takeProfitBps === takeProfitBps,
    )?.presetId ?? 'custom'
  );
}

export function validateAutomationPolicy(values: AutomationPolicyValues): string[] {
  const errors: string[] = [];
  if (
    !Number.isSafeInteger(values.capitalLimitKrw) ||
    values.capitalLimitKrw < 10_000 ||
    values.capitalLimitKrw > 10_000_000_000 ||
    values.capitalLimitKrw % 10_000 !== 0
  ) {
    errors.push('최대 자동운용 금액은 1만원 이상 100억원 이하, 1만원 단위여야 합니다.');
  }
  if (
    !Number.isSafeInteger(values.stopLossBps) ||
    values.stopLossBps < 100 ||
    values.stopLossBps > 1500
  ) {
    errors.push('손절률은 1% 이상 15% 이하로 입력하세요.');
  }
  if (
    !Number.isSafeInteger(values.takeProfitBps) ||
    values.takeProfitBps < 200 ||
    values.takeProfitBps > 3000
  ) {
    errors.push('익절률은 2% 이상 30% 이하로 입력하세요.');
  }
  if (values.takeProfitBps <= values.stopLossBps) {
    errors.push('익절률은 손절률보다 커야 합니다.');
  }
  return errors;
}

export function bpsToPercent(value: number): number {
  return value / 100;
}

export function percentToBps(value: number): number {
  return Math.round(value * 100);
}

export function slotBudgetKrw(capitalLimitKrw: number): number {
  return Math.floor(capitalLimitKrw / 5);
}
