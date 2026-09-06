/**
 * 어둡게 2막 플레이그라운드의 계산.
 *
 * **화면에 적어 둔 고정 시나리오 값만 쓴다.** 실제 계좌나 API 를 부르지 않는다 — 소개
 * 페이지는 로그인 전에도 뜨고, 여기서 보여 주는 건 "원칙 값을 바꾸면 같은 후보의 판정이
 * 뒤집힌다"는 규칙 그 자체이지 특정 계좌의 현재 상태가 아니다.
 *
 * 판정 규칙은 한 줄이다 — 한도 대비 80% 이상이면 WARN, 100%를 넘으면 BLOCK.
 */

export type Verdict = 'ALLOW' | 'WARN' | 'BLOCK';

/** 화면에 그대로 적어 둔 고정 시나리오. */
export const SCENARIO = { dailyLoss: 1.8, goldWeight: 12.0, posWeight: 6.5 } as const;

export interface Dials {
  /** 일일 손실 한도 (%) */
  loss: number;
  /** 금 ETF/ETN 최대 비중 (%) */
  gold: number;
  /** 종목별 최대 보유 비중 (%) */
  pos: number;
}

export interface Preset extends Dials {
  /** 손절 / 익절 */
  sl: string;
  /** 최대 보유 기간 */
  hold: string;
}

export type PresetKey = 'cons' | 'bal' | 'agg';

export const PRESETS: { [K in PresetKey]: Preset } = {
  cons: { loss: 1.0, gold: 10, pos: 12, sl: '3% / 5%', hold: '20세션' },
  bal: { loss: 1.5, gold: 15, pos: 20, sl: '5% / 10%', hold: '60세션' },
  agg: { loss: 3.0, gold: 25, pos: 30, sl: '8% / 15%', hold: '제한 없음' },
};

export const PRESET_LABELS: { key: PresetKey; label: string }[] = [
  { key: 'cons', label: '보수형' },
  { key: 'bal', label: '균형형' },
  { key: 'agg', label: '공격형' },
];

export function verdict(actual: number, limit: number): Verdict {
  if (limit <= 0) return 'BLOCK';
  const ratio = actual / limit;
  if (ratio > 1) return 'BLOCK';
  return ratio >= 0.8 ? 'WARN' : 'ALLOW';
}

export interface JudgedRow {
  code: string;
  name: string;
  detail: string;
  verdict: Verdict;
}

export function judge(dials: Dials): { rows: JudgedRow[]; blocked: number; line: string } {
  const rows: JudgedRow[] = [
    {
      code: '005930',
      name: '삼성전자',
      detail: `005930 · BUY · 종목별 최대 보유 비중 ${SCENARIO.posWeight.toFixed(1)} / ${dials.pos}%`,
      verdict: verdict(SCENARIO.posWeight, dials.pos),
    },
    {
      code: '132030',
      name: 'KODEX 골드선물',
      detail: `132030 · BUY · 금 ETF 최대 비중 ${SCENARIO.goldWeight.toFixed(1)} / ${dials.gold}%`,
      verdict: verdict(SCENARIO.goldWeight, dials.gold),
    },
    {
      code: '000660',
      name: 'SK하이닉스',
      detail: `000660 · BUY · 일일 손실 한도 ${SCENARIO.dailyLoss.toFixed(1)} / ${dials.loss.toFixed(1)}%`,
      verdict: verdict(SCENARIO.dailyLoss, dials.loss),
    },
  ];
  const blocked = rows.filter((row) => row.verdict === 'BLOCK').length;
  const line =
    blocked === 0
      ? '지금 값에서는 세 후보 모두 통과합니다. 한도를 조이면 판정이 뒤집힙니다.'
      : `↑ ${blocked}건에서 원칙이 모델을 이겼습니다. 이 주문은 나가지 않습니다.`;
  return { rows, blocked, line };
}

/** 슬라이더 트랙의 채워진 비율. CSS 가 `--fill` 로 읽는다. */
export function fillPercent(value: number, min: number, max: number): string {
  return `${(((value - min) / (max - min)) * 100).toFixed(1)}%`;
}
