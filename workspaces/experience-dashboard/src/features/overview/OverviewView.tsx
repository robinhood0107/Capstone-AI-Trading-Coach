'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { withFreshness } from '@/shared/lib/viewState';
import { formatKrw, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import type {
  AutomationPositionPageV2,
  AutomationStatusV2,
  PortfolioRisk,
  RecentRiskResult,
} from '@/shared/api/wire';
import { AUTOMATION_STATE_LABELS } from '@/features/automation/policy';

const STEPS = [
  { href: '/principles', label: '내 원칙 정하기', detail: '먼저 기준을 정해야 주문 검토가 동작합니다.' },
  { href: '/strategy', label: '전략 검증하기', detail: '모델 비교와 백테스트로 이 전략이 쓸 만한지 봅니다.' },
  { href: '/order-review', label: '주문 검토하기', detail: '지금 낼 주문이 내 원칙에 맞는지 확인합니다.' },
];

export function OverviewView() {
  const { state, reload } = useResource(async () => {
    const [risk, status, positions, latestRisk] = await Promise.all([
      api.riskPortfolio(),
      api.automationStatusV2(),
      api.automationPositionsV2(),
      api.dashboardLatestRiskResult().catch(() => null),
    ]);
    return withFreshness<OverviewData>(
      {
        risk: risk.data,
        status: status.data,
        positions: positions.data,
        latestRisk: latestRisk?.data ?? null,
      },
      risk.data.asOf,
      15,
      '포트폴리오 관측이 15분 넘게 갱신되지 않았습니다. 값은 참고용으로만 보세요.',
    );
  }, []);

  return (
    <div className="space-y-6">
      <AsyncBoundary state={state} onRetry={reload}>
        {(data) => (
          <>
            <section className="rounded-panel bg-brand px-6 py-7 text-on-brand shadow-hero sm:px-8 sm:py-8">
              <div className="flex flex-wrap items-end justify-between gap-6">
                <div className="min-w-0">
                  <p className="text-[13px] text-on-brand/60">평가금액</p>
                  <p className="tnum mt-2 text-[28px] font-semibold leading-tight tracking-tight sm:text-display">
                    {finite(data.risk.portfolioValue) ? (
                      formatKrw(data.risk.portfolioValue)
                    ) : (
                      <span className="text-[22px] font-medium text-on-brand/45">확인 중</span>
                    )}
                  </p>
                  {finite(data.risk.dailyPnlRate) ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <HeroDelta value={data.risk.dailyPnlRate} />
                      <span className="text-[13px] text-on-brand/50">오늘</span>
                    </div>
                  ) : null}
                </div>
                <AutomationHeroTile status={data.status} />
              </div>

              <HeroStats risk={data.risk} />
            </section>

            <TailRiskPanel risk={data.risk} />
            <LiveSummary data={data} />
          </>
        )}
      </AsyncBoundary>

      <Panel title="여기서부터 시작하세요" hint="왼쪽 메뉴와 같은 순서입니다.">
        <ul className="grid gap-3 md:grid-cols-3">
          {STEPS.map((shortcut, index) => (
            <li key={shortcut.href}>
              <Link
                href={shortcut.href}
                className="group flex h-full flex-col rounded-tile bg-subtle px-4 py-4 hover:bg-panel hover:shadow-card"
              >
                <span className="tnum text-[12px] font-semibold text-faint">0{index + 1}</span>
                <span className="mt-2 flex items-center gap-1.5 text-[15px] font-semibold text-ink">
                  {shortcut.label}
                  <span aria-hidden className="text-faint group-hover:text-navy">
                    →
                  </span>
                </span>
                <span className="mt-1.5 text-[13px] leading-6 text-muted">{shortcut.detail}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="금융 Agent에게 바로 물어보기" hint="공식 자료와 검증된 출처를 바탕으로 금융 개념과 위험을 설명합니다.">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="max-w-2xl text-[13px] leading-6 text-muted">
            어려운 용어, 백테스트 지표, ETF 구조가 궁금하면 금융 Agent에서 전체 설명과 출처를 함께 확인할 수 있습니다.
          </p>
          <Link href="/rag" className="rounded-full bg-brand px-4 py-2 text-[13px] font-semibold text-on-brand">
            금융 Agent 열기
          </Link>
        </div>
      </Panel>
    </div>
  );
}

function HeroStats({ risk }: { risk: PortfolioRisk }) {
  const stats = [
    finite(risk.mdd) ? { label: '최대 낙폭 (MDD)', value: formatRatio(risk.mdd, 1) } : null,
    finite(risk.annualizedVolatility20d)
      ? { label: '연환산 변동성', value: formatRatio(risk.annualizedVolatility20d, 0) }
      : null,
    risk.hmmRegime ? { label: '시장 국면', value: risk.hmmRegime } : null,
  ].filter((entry): entry is { label: string; value: string } => entry !== null);

  if (stats.length === 0) return null;

  return (
    <div className="mt-7 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-on-brand/10 pt-5 sm:grid-cols-3">
      {stats.map((stat) => (
        <div key={stat.label}>
          <p className="text-[12px] text-on-brand/50">{stat.label}</p>
          <p className="tnum mt-1 text-[18px] font-semibold text-on-brand">{stat.value}</p>
        </div>
      ))}
    </div>
  );
}

function TailRiskPanel({ risk }: { risk: PortfolioRisk }) {
  const tiles = [
    finite(risk.var95)
      ? {
          label: 'VaR 95',
          note: '하루에 5% 확률로 이 정도 넘게 잃을 수 있다는 뜻입니다.',
          value: formatRatio(risk.var95, 1),
        }
      : null,
    finite(risk.cvar95)
      ? {
          label: 'CVaR 95',
          note: '그 5%가 실제로 벌어졌을 때의 평균 손실입니다.',
          value: formatRatio(risk.cvar95, 1),
        }
      : null,
  ].filter((entry): entry is { label: string; note: string; value: string } => entry !== null);

  if (tiles.length === 0) return null;

  return (
    <Panel title="손실 위험 지표" hint="관측된 값만 표시합니다.">
      <div className="grid gap-3 sm:grid-cols-2">
        {tiles.map((tile) => (
          <Tile key={tile.label} label={tile.label} note={tile.note}>
            <span className="tnum text-[22px] font-semibold text-ink">{tile.value}</span>
          </Tile>
        ))}
      </div>
    </Panel>
  );
}

function finite(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

function Tile({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
  return (
    <div className="rounded-tile bg-subtle px-4 py-4">
      <p className="text-[12px] font-medium text-muted">{label}</p>
      <div className="mt-2">{children}</div>
      {note ? <p className="mt-2 text-[11px] leading-4 text-faint">{note}</p> : null}
    </div>
  );
}

function HeroDelta({ value }: { value: number | null }) {
  if (!finite(value)) return null;
  const tone = value > 0 ? 'bg-on-brand/15 text-on-brand' : value < 0 ? 'bg-on-brand/15 text-on-brand' : 'bg-on-brand/10 text-on-brand/80';
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : '·';
  return (
    <span className={`tnum inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[14px] font-semibold ${tone}`}>
      <span aria-hidden className="text-[10px]">
        {arrow}
      </span>
      {formatSignedRatio(value, 2)}
    </span>
  );
}

function AutomationHeroTile({ status }: { status: AutomationStatusV2 }) {
  const dot = status.projectionState === 'RUNNING'
      ? 'bg-emerald-300'
      : status.projectionState === 'HALTED'
        ? 'bg-red-300'
        : status.projectionState === 'ARMED'
          ? 'bg-amber-300'
          : 'bg-on-brand/40';

  return (
    <div className="min-w-[168px] rounded-tile bg-on-brand/10 px-4 py-3">
      <p className="text-[12px] text-on-brand/60">자동주문</p>
      <Link
        href="/automation"
        className="mt-1 flex items-center gap-2 text-[16px] font-semibold text-on-brand hover:underline"
      >
        <span aria-hidden className={`h-2 w-2 rounded-full ${dot}`} />
        {AUTOMATION_STATE_LABELS[status.projectionState]}
      </Link>
      {status?.killSwitchActive ? (
        <p className="mt-1 text-[11px] text-red-200">Kill Switch 작동 중</p>
      ) : null}
    </div>
  );
}

interface OverviewData {
  risk: PortfolioRisk;
  status: AutomationStatusV2;
  positions: AutomationPositionPageV2;
  latestRisk: RecentRiskResult | null;
}

function LiveSummary({ data }: { data: OverviewData }) {
  const latest = data.latestRisk;
  return (
    <Panel title="저장된 운용 결과" hint="KIS Mock 계좌와 Decision Platform이 저장한 값만 표시합니다.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="KIS 열린 포지션">
          <span className="tnum text-[22px] font-semibold text-ink">{data.status.openPositionCount}개</span>
        </Tile>
        <Tile label="종료된 포지션">
          <span className="tnum text-[22px] font-semibold text-ink">
            {data.positions.realizedSummary.closedPositionCount}개
          </span>
        </Tile>
        <Tile label="확정 손익">
          <span className="tnum text-[22px] font-semibold text-ink">
            {formatKrw(data.positions.realizedSummary.realizedPnlKrw)}
          </span>
        </Tile>
        <Tile label="최근 판정">
          {latest ? (
            <Link href="/order-review" className="text-[18px] font-semibold text-navy hover:underline">
              {latest.symbol} · {latest.action}
            </Link>
          ) : (
            <span className="text-[14px] text-muted">아직 없음</span>
          )}
        </Tile>
      </div>
      {data.positions.items.length > 0 ? (
        <ul className="mt-5 divide-y divide-line/60 border-t border-line">
          {data.positions.items.slice(0, 5).map((position) => (
            <li key={position.positionId} className="flex flex-wrap items-center justify-between gap-3 py-3 text-[13px]">
              <span className="font-medium text-ink">{position.symbol}</span>
              <span className="tnum text-muted">
                {position.quantity}주 · 평균 {formatKrw(position.entryAverageFillPriceKrw)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
