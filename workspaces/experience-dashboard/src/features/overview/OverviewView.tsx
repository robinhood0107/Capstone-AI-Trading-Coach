'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric, Delta } from '@/shared/ui/Numeric';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { ready, withFreshness } from '@/shared/lib/viewState';
import { formatKrw, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import type { PortfolioRisk } from '@/shared/api/wire';
import { AUTOMATION_STATE_LABELS } from '@/features/automation/policy';

const SHORTCUTS = [
  { href: '/principles', label: '내 원칙 정하기', detail: '먼저 기준을 정해야 주문 검토가 동작합니다.' },
  { href: '/order-review', label: '주문 검토하기', detail: '지금 낼 주문이 내 원칙에 맞는지 확인합니다.' },
  { href: '/backtest', label: '백테스트 리포트', detail: '원칙을 켰을 때 손실이 얼마나 줄었는지 봅니다.' },
];

export function OverviewView() {
  const { state, reload } = useResource(async () => {
    const { data } = await api.riskPortfolio();
    return withFreshness<PortfolioRisk>(
      data,
      data.asOf,
      15,
      '포트폴리오 관측이 15분 넘게 갱신되지 않았습니다. 값은 참고용으로만 보세요.',
    );
  }, []);

  return (
    <div className="space-y-6">
      <AsyncBoundary state={state} onRetry={reload}>
        {(risk) => (
          <Panel
            contract="GET /api/v1/risk/portfolio"
            title="지금 내 포트폴리오"
            hint="근거가 없는 값은 0으로 채우지 않고 빈 자리로 둡니다."
          >
            <div className="grid gap-px bg-line sm:grid-cols-2 xl:grid-cols-4">
              <Tile label="평가금액">
                <Numeric
                  value={risk.portfolioValue}
                  format={formatKrw}
                  className="text-xl font-semibold text-ink"
                />
              </Tile>
              <Tile label="오늘 손익률">
                <Delta value={risk.dailyPnlRate} format={(v) => formatSignedRatio(v, 2)} />
              </Tile>
              <Tile label="최대 낙폭 (MDD)">
                <Numeric
                  value={risk.mdd}
                  format={(v) => formatRatio(v, 1)}
                  className="text-xl font-semibold text-ink"
                />
              </Tile>
              <Tile label="연환산 변동성">
                <Numeric
                  value={risk.annualizedVolatility20d}
                  format={(v) => formatRatio(v, 0)}
                  className="text-xl font-semibold text-ink"
                />
              </Tile>
              <Tile label="VaR 95">
                <Numeric
                  value={risk.var95}
                  format={(v) => formatRatio(v, 1)}
                  missingReason="이 값을 만드는 단계가 아직 운영에 없습니다."
                />
              </Tile>
              <Tile label="CVaR 95">
                <Numeric
                  value={risk.cvar95}
                  format={(v) => formatRatio(v, 1)}
                  missingReason="이 값을 만드는 단계가 아직 운영에 없습니다."
                />
              </Tile>
              <Tile label="시장 국면">
                {risk.hmmRegime ? (
                  <span className="font-mono text-[14px] text-ink">{risk.hmmRegime}</span>
                ) : (
                  <Numeric
                    value={null}
                    format={String}
                    missingReason="시장 국면 추정 결과가 아직 없습니다."
                  />
                )}
              </Tile>
              <Tile label="자동주문">
                <AutomationStatusTile />
              </Tile>
            </div>
          </Panel>
        )}
      </AsyncBoundary>

      <Panel contract="navigation" title="여기서부터 시작하세요" hint="왼쪽 순서대로 따라가면 됩니다.">
        <ul className="grid gap-px bg-line md:grid-cols-3">
          {SHORTCUTS.map((shortcut) => (
            <li key={shortcut.href} className="bg-panel">
              <Link href={shortcut.href} className="block px-4 py-4 hover:bg-surface">
                <p className="text-[14px] font-medium text-navy">{shortcut.label}</p>
                <p className="mt-1.5 text-[13px] leading-6 text-muted">{shortcut.detail}</p>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bg-panel px-4 py-4">
      <p className="font-mono text-eyebrow uppercase text-faint">{label}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function AutomationStatusTile() {
  const { state, reload } = useResource(async () => {
    const { data } = await api.automationStatusV2();
    return ready(data, data.policy?.updatedAt ?? null);
  }, []);

  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(status) => {
        const tone =
          status.projectionState === 'RUNNING'
            ? 'text-allow'
            : status.projectionState === 'HALTED'
              ? 'text-block'
              : status.projectionState === 'ARMED'
                ? 'text-warn'
                : 'text-muted';
        return (
          <div>
            <Link href="/automation" className={`text-[15px] font-medium ${tone}`}>
              {AUTOMATION_STATE_LABELS[status.projectionState]}
            </Link>
            {status.killSwitchActive ? (
              <p className="mt-1 text-[11px] text-block">Kill Switch 작동 중</p>
            ) : null}
          </div>
        );
      }}
    </AsyncBoundary>
  );
}
