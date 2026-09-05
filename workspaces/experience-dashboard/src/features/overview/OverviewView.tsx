'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Delta } from '@/shared/ui/Numeric';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { withFreshness } from '@/shared/lib/viewState';
import { formatKrw, formatKstDateTime, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import type {
  AutomationPositionPageV2,
  AutomationStatusV2,
  InstrumentDisplayCatalog,
  MockBalance,
  PortfolioRisk,
  RecentRiskResult,
} from '@/shared/api/wire';
import { AUTOMATION_STATE_LABELS } from '@/features/automation/policy';
import { InstrumentIdentity, instrumentMap } from '@/shared/ui/InstrumentIdentity';

const STEPS = [
  { href: '/principles', label: '내 원칙 정하기', detail: '먼저 기준을 정해야 주문 검토가 동작합니다.' },
  { href: '/strategy', label: '전략 검증하기', detail: '모델 비교와 백테스트로 이 전략이 쓸 만한지 봅니다.' },
  { href: '/order-review', label: '주문 검토하기', detail: '지금 낼 주문이 내 원칙에 맞는지 확인합니다.' },
];

export function OverviewView() {
  const { state, reload } = useResource(async () => {
    const [risk, status, positions, latestRisk, instruments] = await Promise.all([
      api.riskPortfolio(),
      api.automationStatusV2(),
      api.automationPositionsV2(),
      api.dashboardLatestRiskResult().catch(() => null),
      api.instrumentDisplayCatalog(),
    ]);
    const balance = status.data.accountId
      ? await api.mockBalance(status.data.accountId).then((result) => result.data).catch(() => null)
      : null;
    return withFreshness<OverviewData>(
      {
        risk: risk.data,
        status: status.data,
        positions: positions.data,
        latestRisk: latestRisk?.data ?? null,
        balance,
        instruments: instruments.data,
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
            <section className="relative overflow-hidden rounded-panel bg-[#12151F] px-6 py-7 text-white sm:px-8 sm:py-8">
              <div className="relative z-10">
                <div className="flex flex-wrap items-end justify-between gap-6">
                  <div className="min-w-0">
                    <p className="text-[13px] text-white/60">평가금액</p>
                    <p className="tnum mt-2 text-[28px] font-semibold leading-tight tracking-tight sm:text-display">
                      {finite(data.balance?.portfolioEquityKrw ?? data.risk.portfolioValue) ? (
                        formatKrw(data.balance?.portfolioEquityKrw ?? data.risk.portfolioValue!)
                      ) : (
                        <span className="text-[18px] font-medium text-white/65">KIS Mock 계좌 연결 필요</span>
                      )}
                    </p>
                    {finite(data.risk.dailyPnlRate) ? (
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <HeroDelta value={data.risk.dailyPnlRate} />
                        <span className="text-[13px] text-white/50">오늘</span>
                      </div>
                    ) : null}
                  </div>
                  <AutomationHeroTile status={data.status} />
                </div>

                <HeroStats risk={data.risk} />
              </div>
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
                className="tap group flex h-full flex-col rounded-card border border-line bg-subtle px-4 py-4 transition-colors hover:border-navy/40 hover:bg-panel"
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

      <section className="rounded-panel border border-navy/15 bg-navy/[0.05] px-6 py-6 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-6">
          <div className="flex min-w-0 items-start gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-navy/10 text-navy">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
                <path d="M4 5h16v11H9l-4 4V5Z" />
                <path d="M8 9h8M8 12h5" />
              </svg>
            </span>
            <div className="min-w-0">
              <h2 className="text-[16px] font-semibold tracking-tight text-ink">금융 Agent에게 바로 물어보기</h2>
              <p className="mt-1 max-w-2xl text-[13px] leading-6 text-muted">
                어려운 용어, 백테스트 지표, ETF 구조가 궁금하면 공식 자료와 검증된 출처를 바탕으로 전체 설명과
                출처를 함께 확인할 수 있습니다.
              </p>
            </div>
          </div>
          <Link
            href="/rag"
            className="tap shrink-0 rounded-control bg-brand px-4 py-2 text-[13px] font-semibold text-on-brand hover:opacity-90"
          >
            금융 Agent 열기
          </Link>
        </div>
      </section>
    </div>
  );
}

function HeroStats({ risk }: { risk: PortfolioRisk }) {
  const stats = [
    finite(risk.mdd)
      ? { label: '최대 낙폭 (MDD)', value: formatRatio(risk.mdd, 1), tone: 'down' as const }
      : null,
    finite(risk.annualizedVolatility20d)
      ? { label: '연환산 변동성', value: formatRatio(risk.annualizedVolatility20d, 0), tone: 'flat' as const }
      : null,
    risk.hmmRegime ? { label: '시장 국면', value: risk.hmmRegime, tone: 'flat' as const } : null,
  ].filter((entry): entry is { label: string; value: string; tone: 'down' | 'flat' } => entry !== null);

  if (stats.length === 0) return null;

  return (
    <div className="mt-7 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-white/10 pt-5 sm:grid-cols-3">
      {stats.map((stat) => (
        <div key={stat.label}>
          <p className="text-[12px] text-white/50">{stat.label}</p>
          <p
            className="tnum mt-1 text-[18px] font-semibold"
            style={{ color: stat.tone === 'down' ? '#7EA0FF' : '#FFFFFF' }}
          >
            {stat.value}
          </p>
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
          value: risk.var95,
        }
      : null,
    finite(risk.cvar95)
      ? {
          label: 'CVaR 95',
          note: '그 5%가 실제로 벌어졌을 때의 평균 손실입니다.',
          value: risk.cvar95,
        }
      : null,
  ].filter((entry): entry is { label: string; note: string; value: number } => entry !== null);

  if (tiles.length === 0) return null;

  return (
    <Panel title="손실 위험 지표" hint="관측된 값만 표시합니다.">
      <div className="grid gap-3 sm:grid-cols-2">
        {tiles.map((tile) => (
          <Tile key={tile.label} label={tile.label} note={tile.note}>
            <Delta value={tile.value} format={(v) => formatRatio(v, 1)} className="text-[22px]" />
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
  const up = value > 0;
  const down = value < 0;
  const hex = up ? '#FF6B70' : down ? '#7EA0FF' : '#FFFFFF';
  const arrow = up ? '▲' : down ? '▼' : '·';
  return (
    <span
      className="tnum inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[14px] font-semibold"
      style={{ backgroundColor: `${hex}26`, color: hex }}
    >
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
          : 'bg-white/40';

  return (
    <div className="min-w-[168px] rounded-tile bg-white/10 px-4 py-3">
      <p className="text-[12px] text-white/60">자동주문</p>
      <Link
        href="/automation"
        className="mt-1 flex items-center gap-2 text-[16px] font-semibold text-white hover:underline"
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
  balance: MockBalance | null;
  instruments: InstrumentDisplayCatalog;
}

function LiveSummary({ data }: { data: OverviewData }) {
  const latest = data.latestRisk;
  const balance = data.balance;
  const managedSymbols = new Set(
    data.positions.items
      .filter((position) => position.status === 'OPEN' || position.status === 'EXIT_PENDING')
      .map((position) => position.symbol),
  );
  const stockValue = balance
    ? balance.positions.reduce((sum, position) => sum + position.marketValueKrw, 0)
    : null;
  const observedAt = formatKstDateTime(balance?.observedAt ?? null);
  const policy = data.status.policy;
  const instruments = instrumentMap(data.instruments.items);
  const remainingCapital =
    policy && stockValue !== null ? Math.max(0, policy.capitalLimitKrw - stockValue) : null;

  return (
    <div className="space-y-6">
      <Panel
        title="현재 KIS Mock 계좌"
        hint={observedAt ? `${observedAt} KST에 저장된 잔고입니다.` : '저장된 최신 잔고를 표시합니다.'}
      >
        {balance ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Tile label="총 평가금액">
                <span className="tnum text-[22px] font-semibold text-ink">
                  {formatKrw(balance.portfolioEquityKrw)}
                </span>
              </Tile>
              <Tile label="현금 잔고">
                <span className="tnum text-[22px] font-semibold text-ink">{formatKrw(balance.cashKrw)}</span>
              </Tile>
              <Tile label="주식 평가금액">
                <span className="tnum text-[22px] font-semibold text-ink">{formatKrw(stockValue ?? 0)}</span>
              </Tile>
              <Tile label="보유 종목">
                <span className="tnum text-[22px] font-semibold text-ink">{balance.positions.length}개</span>
              </Tile>
            </div>

            <ul className="mt-5 divide-y divide-line/60 border-t border-line">
              {balance.positions.map((position) => {
                const managed = managedSymbols.has(position.symbol);
                return (
                  <li
                    key={position.symbol}
                    className="grid gap-2 py-3 text-[13px] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                  >
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <InstrumentIdentity symbol={position.symbol} instrument={instruments.get(position.symbol)} compact />
                      <span
                        className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                          managed ? 'bg-navy/10 text-navy' : 'bg-subtle text-muted'
                        }`}
                      >
                        {managed ? '자동매매 관리' : '직접 보유 · 매매 제외'}
                      </span>
                      {position.isGoldEtfEtn ? (
                        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800">
                          금 ETF·ETN
                        </span>
                      ) : null}
                    </div>
                    <span className="tnum text-muted sm:text-right">
                      {position.quantity}주 · 평가 {formatKrw(position.marketValueKrw)}
                    </span>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-[12px] leading-5 text-muted">
              직접 보유 종목도 운용 한도와 위험 계산에는 포함되지만, 자동매매가 임의로 매도하지 않습니다.
            </p>
          </>
        ) : (
          <p className="text-[13px] text-muted">계좌 상세 잔고를 불러오지 못했습니다.</p>
        )}
      </Panel>

      <Panel title="자동매매 운용 상태" hint="현재 제어 상태와 저장된 운용 정책을 그대로 표시합니다.">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="자동주문">
            <Link href="/automation" className="text-[18px] font-semibold text-navy hover:underline">
              {AUTOMATION_STATE_LABELS[data.status.projectionState]}
            </Link>
          </Tile>
          <Tile label="모의계좌 인증">
            <span className="text-[16px] font-semibold text-ink">
              {data.status.certificationStatus === 'VALID' ? '정상' : data.status.certificationStatus}
            </span>
          </Tile>
          <Tile label="Kill Switch">
            <span className="text-[16px] font-semibold text-ink">
              {data.status.killSwitchActive ? '작동 중' : '꺼짐'}
            </span>
          </Tile>
          <Tile label="미해결 대사">
            <span className="text-[16px] font-semibold text-ink">
              {data.status.unresolvedReconciliation ? '확인 필요' : '없음'}
            </span>
          </Tile>
        </div>

        {policy ? (
          <div className="mt-4 rounded-tile border border-line bg-panel px-4 py-4">
            <p className="text-[12px] font-medium text-muted">현재 운용 정책</p>
            <div className="mt-2 grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
              <SummaryPair label="운용 한도" value={formatKrw(policy.capitalLimitKrw)} />
              <SummaryPair
                label="계좌 주식 반영"
                value={stockValue === null ? '잔고 없음' : `-${formatKrw(stockValue)}`}
              />
              <SummaryPair
                label="남은 운용 한도"
                value={remainingCapital === null ? '계산 불가' : formatKrw(remainingCapital)}
              />
              <SummaryPair label="손절 / 익절" value={`${formatRatio(policy.stopLossBps / 10_000, 1)} / ${formatRatio(policy.takeProfitBps / 10_000, 1)}`} />
              <SummaryPair label="최대 열린 포지션" value={`${policy.maxOpenPositions}개`} />
              <SummaryPair label="평가 / 매수 마감" value={`${policy.evaluationTimeKst} / ${policy.buyCutoffTimeKst} KST`} />
            </div>
          </div>
        ) : null}

        {data.positions.items.length > 0 ? (
          <ul className="mt-5 divide-y divide-line/60 border-t border-line">
            {data.positions.items.map((position) => (
              <li key={position.positionId} className="grid gap-2 py-3 text-[13px] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                  <InstrumentIdentity symbol={position.symbol} instrument={instruments.get(position.symbol)} compact />
                  <span className="text-muted">{position.status === 'OPEN' ? '보유 중' : position.status}</span>
                  <span className="tnum text-muted">{position.quantity}주</span>
                </div>
                <span className="tnum text-muted lg:text-right">
                  평균 {formatKrw(position.entryAverageFillPriceKrw)} · 손절 {formatRatio(position.stopLossBps / 10_000, 1)} · 익절 {formatRatio(position.takeProfitBps / 10_000, 1)}
                  {position.expirySession ? ` · 만료 ${position.expirySession}` : ''}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-4 text-[13px] text-muted">자동매매가 관리하는 포지션이 없습니다.</p>
        )}
      </Panel>

      <Panel title="저장된 운용 결과" hint="확정된 자동매매 결과와 최근 주문 판정을 표시합니다.">
        <div className="grid gap-3 sm:grid-cols-3">
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
                {instruments.get(latest.symbol)?.nameKo ?? latest.symbol} · {latest.action}
              </Link>
            ) : (
              <span className="text-[14px] text-muted">아직 없음</span>
            )}
          </Tile>
        </div>
      </Panel>
    </div>
  );
}

function SummaryPair({ label, value }: { label: string; value: string }) {
  return (
    <p>
      <span className="text-faint">{label}</span>
      <span className="tnum ml-2 font-medium text-ink">{value}</span>
    </p>
  );
}
