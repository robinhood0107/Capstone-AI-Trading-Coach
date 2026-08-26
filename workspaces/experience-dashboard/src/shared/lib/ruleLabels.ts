import type { PrincipleRuleId } from '@/shared/api/wire';

interface RuleLabel {
  name: string;
  /** 사용자가 이 값을 어떻게 읽어야 하는지. 용어를 그대로 던지지 않는다. */
  reading: string;
  unit: 'RATIO' | 'KRW' | 'COUNT';
}

export const RULE_LABELS: Record<PrincipleRuleId, RuleLabel> = {
  max_position_per_asset: {
    name: '단일 종목 최대 비중',
    reading: '한 종목이 전체 자산에서 차지할 수 있는 최대 비율',
    unit: 'RATIO',
  },
  max_gold_etf_etn_weight: {
    name: '금 상품 최대 비중',
    reading: '금 ETF 132030이 차지할 수 있는 최대 비율 (P1에서 ETN은 대상 아님)',
    unit: 'RATIO',
  },
  max_single_order_amount: {
    name: '1회 주문 최대 금액',
    reading: '한 번의 주문으로 쓸 수 있는 최대 금액',
    unit: 'KRW',
  },
  daily_loss_guard: {
    name: '일일 손실 한도',
    reading: '하루 손실이 이 비율보다 커지면 새 주문을 막습니다',
    unit: 'RATIO',
  },
  mdd_guard: {
    name: 'MDD 한도',
    reading: '고점 대비 누적 하락폭의 허용 한계',
    unit: 'RATIO',
  },
  max_daily_orders: {
    name: '하루 최대 주문 횟수',
    reading: '과매매를 막기 위한 하루 주문 건수 상한',
    unit: 'COUNT',
  },
  negative_news_guard: {
    name: '부정 뉴스 대응',
    reading: '부정 뉴스 점수가 높을 때의 처리 기준',
    unit: 'RATIO',
  },
  disclosure_risk_guard: {
    name: '공시 위험 대응',
    reading: '공시 위험 점수가 높을 때의 처리 기준',
    unit: 'RATIO',
  },
};

export function ruleName(ruleId: string): string {
  return RULE_LABELS[ruleId as PrincipleRuleId]?.name ?? ruleId;
}
