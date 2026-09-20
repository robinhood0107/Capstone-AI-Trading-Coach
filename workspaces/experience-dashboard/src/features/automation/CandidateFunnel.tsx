import { useMemo } from 'react';

import type { AutomationStageName, AutomationStageOutcome } from '../../shared/api/wire';

/**
 * 후보가 어느 단계에서 왜 빠졌는지를 위에서 아래로 읽게 한다.
 *
 * 2026-09-09 실행은 후보 7종목이 실시간 안전필터에서 전부 탈락했는데, 화면에는
 * "AI 판단 근거 없음 / 남은 후보 심사 기록이 없습니다" 만 떴다. 그 실행이 정상인지
 * 결함인지 화면만 보고는 알 수 없었다. 단계별 숫자와 탈락 사유를 보여주면
 * 무주문이어도 원인을 가리킬 수 있다.
 */

/** 통과 순서. 서버 정렬과 같아야 서사가 깨지지 않는다. */
const STAGE_ORDER: AutomationStageName[] = [
  'OBSERVATION',
  'RULE_BUY',
  'LSTM_VETO',
  'ATR_HISTORY',
  'QUOTE_SAFETY',
  'NEWS_DISCLOSURE',
  'AI_JUDGE',
  'RISK_ENGINE',
  'ORDER',
];

const STAGE_LABELS: Record<AutomationStageName, string> = {
  OBSERVATION: '관측 적재',
  RULE_BUY: '규칙 매수 후보',
  LSTM_VETO: '모델 거부권',
  ATR_HISTORY: '변동성 이력',
  QUOTE_SAFETY: '실시간 안전 확인',
  NEWS_DISCLOSURE: '뉴스·공시 확인',
  AI_JUDGE: 'AI 순위 판단',
  RISK_ENGINE: '위험 검증',
  ORDER: '주문',
};

/** 기계 사유 코드를 사용자가 읽는 문장으로. 모르는 코드는 코드를 그대로 보여준다. */
const REASON_LABELS: Record<string, string> = {
  ALREADY_HELD: '이미 보유 중',
  RULE_NOT_BUY: '규칙이 매수로 보지 않음',
  FORECAST_NOT_FINITE: '예측값이 유효하지 않음',
  POSITION_CAP_REACHED: '동시 보유 상한에 도달',
  MODEL_SELL: '모델이 매도로 판단',
  ATR_HISTORY_MISSING: '변동성 계산에 필요한 이력이 부족',
  TEMP_STOP: '거래 정지 상태',
  MANAGEMENT_ISSUE: '관리종목 지정',
  LIQUIDATION_TRADING: '정리매매 중',
  QUOTE_FIELD_MISSING: '거래 상태를 확인할 수 없음',
  QUOTE_UNAVAILABLE: '현재가를 받지 못함',
  DISCLOSURE_VETO: '공시 근거로 매수 제외',
  NO_REMAINING_RETURN: '기대 상승분이 거래 비용을 넘지 못함',
  SIZING_BELOW_ONE_SHARE: '설정 금액으로는 1주도 살 수 없음',
  OBSERVATION_NOT_PUBLISHED: '위험지표 입력이 적재되지 않음',
};

/** 종목이 아니라 세션 전체를 막는 단계의 자리표시. */
const SESSION_SYMBOL = '000000';

export interface CandidateFunnelProps {
  outcomes: AutomationStageOutcome[];
  /** 종목코드를 사람이 읽는 이름으로 바꾼다. 없으면 코드를 그대로 쓴다. */
  nameOf?: (symbol: string) => string;
}

interface StageRow {
  stage: AutomationStageName;
  passed: string[];
  dropped: { symbol: string; reasonCode: string; reasonDetail: string | null }[];
}

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}

export function CandidateFunnel({ outcomes, nameOf }: CandidateFunnelProps) {
  const rows = useMemo<StageRow[]>(() => {
    const byStage = new Map<AutomationStageName, StageRow>();
    for (const item of outcomes) {
      let row = byStage.get(item.stage);
      if (!row) {
        row = { stage: item.stage, passed: [], dropped: [] };
        byStage.set(item.stage, row);
      }
      if (item.outcome === 'PASS') {
        row.passed.push(item.symbol);
      } else {
        row.dropped.push({
          symbol: item.symbol,
          reasonCode: item.reasonCode ?? 'UNKNOWN',
          reasonDetail: item.reasonDetail,
        });
      }
    }
    return STAGE_ORDER.filter((stage) => byStage.has(stage)).map(
      (stage) => byStage.get(stage) as StageRow,
    );
  }, [outcomes]);

  if (rows.length === 0) {
    return null;
  }

  const label = (symbol: string) =>
    symbol === SESSION_SYMBOL ? '이 실행 전체' : (nameOf?.(symbol) ?? symbol);

  return (
    <ol className="space-y-2">
      {rows.map((row) => {
        const total = row.passed.length + row.dropped.length;
        const sessionWide = row.stage === 'OBSERVATION';
        return (
          <li key={row.stage} className="text-[12px] leading-6">
            <p className="font-semibold text-ink">
              {STAGE_LABELS[row.stage]}
              {sessionWide ? null : (
                <span className="tnum ml-2 font-mono text-[11px] text-faint">
                  {row.passed.length} / {total} 통과
                </span>
              )}
            </p>
            {row.dropped.length > 0 ? (
              <ul className="mt-0.5 space-y-0.5">
                {row.dropped.map((item) => (
                  <li key={`${row.stage}:${item.symbol}`} className="text-[11px] text-muted">
                    {label(item.symbol)} · {reasonLabel(item.reasonCode)}
                    {item.reasonDetail ? (
                      <span className="ml-1 text-faint">({item.reasonDetail})</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * 무주문 실행을 한 문장으로 설명한다. 원인을 가리키지 못하면 빈 값을 돌려준다.
 *
 * "AI 판단 근거 없음" 같은 표현은 결과만 말하고 원인을 말하지 않는다. 사용자가
 * 알아야 하는 것은 "왜 주문이 없었는가" 다.
 */
export function summarizeNoOrder(outcomes: AutomationStageOutcome[]): string | null {
  const dropped = outcomes.filter((item) => item.outcome === 'DROPPED');
  if (dropped.length === 0) {
    return null;
  }
  for (const stage of [...STAGE_ORDER].reverse()) {
    const atStage = dropped.filter((item) => item.stage === stage);
    if (atStage.length === 0) {
      continue;
    }
    const passedHere = outcomes.some(
      (item) => item.stage === stage && item.outcome === 'PASS',
    );
    if (passedHere) {
      continue;
    }
    const reasons = [...new Set(atStage.map((item) => reasonLabel(item.reasonCode ?? '')))];
    if (stage === 'OBSERVATION') {
      return `${STAGE_LABELS[stage]} 단계에서 멈췄습니다 — ${reasons.join(', ')}.`;
    }
    return `${STAGE_LABELS[stage]} 단계에서 ${atStage.length}종목이 모두 제외됐습니다 — ${reasons.join(', ')}.`;
  }
  return null;
}
