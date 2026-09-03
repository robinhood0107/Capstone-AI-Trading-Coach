'use client';

import { useState } from 'react';
import { PageHeader } from '@/shared/ui/Panel';
import { ModelEvaluationView } from '@/features/model-evaluation/ModelEvaluationView';
import { BacktestReportView } from '@/features/backtest-report/BacktestReportView';

export type StrategyTab = 'model' | 'backtest';

const TABS: readonly [
  { id: StrategyTab; label: string; title: string; description: string },
  { id: StrategyTab; label: string; title: string; description: string },
] = [
  {
    id: 'model',
    label: '모델 비교',
    title: '모델 비교',
    description:
      '규칙 baseline, LSTM, LightGBM이 같은 조건에서 무엇을 말하는지 나란히 봅니다. 근거를 내지 못한 모델은 값 대신 ABSTAIN으로 남습니다.',
  },
  {
    id: 'backtest',
    label: '백테스트 리포트',
    title: '백테스트 리포트',
    description: '원칙과 안전장치를 켰을 때 수익률·낙폭·원칙 위반이 어떻게 달라지는지 비교합니다.',
  },
] as const;

/**
 * 모델 비교와 백테스트는 "이 전략이 쓸 만한가"라는 같은 질문에 답한다.
 * 화면을 갈라 두면 시연 중 탭을 오가야 하므로 한 화면 안의 전환으로 합쳤다.
 * ViewModel(최종 명세서 7.4의 7·8번)은 각각 그대로 유지한다.
 */
export function StrategyView({
  defaultTab = 'model',
  defaultRunId = '',
}: {
  defaultTab?: StrategyTab;
  defaultRunId?: string;
}) {
  const [tab, setTab] = useState<StrategyTab>(defaultTab);
  const active = TABS.find((item) => item.id === tab) ?? TABS[0];

  return (
    <div className="space-y-8">
      <PageHeader eyebrow="전략 검증" title={active.title} description={active.description} />

      <div
        role="tablist"
        aria-label="전략 검증 보기"
        className="inline-flex gap-1 rounded-full bg-subtle p-1"
      >
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            type="button"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={`rounded-full px-4 py-1.5 text-[13px] ${
              tab === item.id
                ? 'bg-panel font-semibold text-ink shadow-card'
                : 'font-medium text-muted hover:text-ink'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {/*
        display:none으로 숨기지 않고 언마운트한다.
        recharts ResponsiveContainer는 너비 0인 컨테이너에서 다시 그리지 못한다.
      */}
      {tab === 'model' ? (
        <ModelEvaluationView defaultRunId={defaultRunId} />
      ) : (
        <BacktestReportView defaultRunId={defaultRunId} />
      )}
    </div>
  );
}
