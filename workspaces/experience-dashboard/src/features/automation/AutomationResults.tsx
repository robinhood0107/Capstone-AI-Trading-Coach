'use client';

import { useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { formatKrw } from '@/shared/lib/format';
import { useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';

export function AutomationResults() {
  const [open, setOpen] = useState(false);
  const { state, reload } = useResource(
    async () => ready((await api.automationPositionsV2()).data.realizedSummary),
    [],
    open,
  );

  useEffect(() => {
    if (window.location.hash !== '#operation-results') return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById('operation-results')?.scrollIntoView({ block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  return (
    <Panel title="운용 기록">
      <details id="operation-results" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-[13px] font-medium text-navy">
          종료된 포지션과 손익 보기
        </summary>
        <div className="mt-4 border-t border-line pt-4">
          <AsyncBoundary state={state} onRetry={reload}>
            {(summary) => (
              <div className="space-y-3 text-[13px]">
                <div className="flex flex-wrap gap-x-8 gap-y-2">
                  <p><span className="text-muted">종료된 포지션</span> <strong className="ml-2 font-semibold text-ink">{summary.closedPositionCount}개</strong></p>
                  <p><span className="text-muted">실현 손익 · 비용 추정 반영</span> <strong className="tnum ml-2 font-semibold text-ink">{formatKrw(summary.realizedPnlKrw)}</strong></p>
                </div>
                <p className="text-[12px] leading-5 text-muted">
                  KIS 모의계좌의 저장된 자동운용 결과입니다. 거래비용은 시스템 추정치이며 실거래 수익으로 해석할 수 없습니다.
                </p>
              </div>
            )}
          </AsyncBoundary>
        </div>
      </details>
    </Panel>
  );
}
