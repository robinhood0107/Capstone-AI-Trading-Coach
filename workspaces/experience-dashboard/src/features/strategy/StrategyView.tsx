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
      '규칙 baseline과 LSTM을 같은 조건에서 비교합니다. 근거를 내지 못한 모델은 값 대신 평가 보류로 남습니다.',
  },
  {
    id: 'backtest',
    label: '백테스트 리포트',
    title: '백테스트 리포트',
    description: '원칙과 안전장치를 켰을 때 수익률·낙폭·원칙 위반이 어떻게 달라지는지 비교합니다.',
  },
] as const;

export function StrategyView({ defaultTab = 'model' }: { defaultTab?: StrategyTab }) {
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

      {tab === 'model' ? (
        <ModelEvaluationView />
      ) : (
        <BacktestReportView />
      )}
    </div>
  );
}
