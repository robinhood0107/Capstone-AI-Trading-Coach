'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Numeric } from '@/shared/ui/Numeric';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { hasData, ready, withFreshness } from '@/shared/lib/viewState';
import { formatKrw, formatRatio, formatSignedRatio } from '@/shared/lib/format';
import type { PortfolioRisk } from '@/shared/api/wire';
import { AUTOMATION_STATE_LABELS } from '@/features/automation/policy';

/** 최종 명세서 5.1 한 줄 흐름을 그대로 세 단계로 줄인 것. 사이드바 01·02·03과 같은 순서다. */
const STEPS = [
  { href: '/principles', label: '내 원칙 정하기', detail: '먼저 기준을 정해야 주문 검토가 동작합니다.' },
  { href: '/strategy', label: '전략 검증하기', detail: '모델 비교와 백테스트로 이 전략이 쓸 만한지 봅니다.' },
  { href: '/order-review', label: '주문 검토하기', detail: '지금 낼 주문이 내 원칙에 맞는지 확인합니다.' },
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
          <>
            {/*
              히어로: 사용자가 이 화면을 열자마자 알고 싶은 것은 "내 돈이 얼마이고 오늘 얼마 움직였나"다.
              나머지 리스크 지표는 그 아래 등급으로 내린다.
            */}
            <section className="rounded-panel bg-navy px-6 py-7 text-white shadow-hero sm:px-8 sm:py-8">
              <div className="flex flex-wrap items-end justify-between gap-6">
                <div className="min-w-0">
                  <p className="text-[13px] text-white/60">평가금액</p>
                  <p className="tnum mt-2 text-[28px] font-semibold leading-tight tracking-tight sm:text-display">
                    {risk.portfolioValue === null || !Number.isFinite(risk.portfolioValue) ? (
                      <span className="text-[22px] font-medium text-white/45">근거 없음</span>
                    ) : (
                      formatKrw(risk.portfolioValue)
                    )}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <HeroDelta value={risk.dailyPnlRate} />
                    <span className="text-[13px] text-white/50">오늘</span>
                  </div>
                </div>
                <AutomationHeroTile />
              </div>

              <div className="mt-7 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-white/10 pt-5 sm:grid-cols-3">
                <HeroStat label="최대 낙폭 (MDD)" value={risk.mdd} format={(v) => formatRatio(v, 1)} />
                <HeroStat
                  label="연환산 변동성"
                  value={risk.annualizedVolatility20d}
                  format={(v) => formatRatio(v, 0)}
                />
                <HeroStat
                  label="시장 국면"
                  text={risk.hmmRegime}
                  missingReason="시장 국면 추정 결과가 아직 없습니다."
                />
              </div>
            </section>

            <Panel
              contract="GET /api/v1/risk/portfolio"
              title="손실 위험 지표"
              hint="근거가 없는 값은 0으로 채우지 않고 빈 자리로 둡니다."
            >
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Tile label="최대 낙폭 (MDD)">
                  <Numeric
                    value={risk.mdd}
                    format={(v) => formatRatio(v, 1)}
                    className="text-[22px] font-semibold text-ink"
                  />
                </Tile>
                <Tile label="연환산 변동성">
                  <Numeric
                    value={risk.annualizedVolatility20d}
                    format={(v) => formatRatio(v, 0)}
                    className="text-[22px] font-semibold text-ink"
                  />
                </Tile>
                <Tile label="VaR 95" note="하루에 5% 확률로 이 정도 넘게 잃을 수 있다는 뜻입니다.">
                  <Numeric
                    value={risk.var95}
                    format={(v) => formatRatio(v, 1)}
                    className="text-[22px] font-semibold text-ink"
                    missingReason="이 값을 만드는 단계가 아직 운영에 없습니다."
                  />
                </Tile>
                <Tile label="CVaR 95" note="그 5%가 실제로 벌어졌을 때의 평균 손실입니다.">
                  <Numeric
                    value={risk.cvar95}
                    format={(v) => formatRatio(v, 1)}
                    className="text-[22px] font-semibold text-ink"
                    missingReason="이 값을 만드는 단계가 아직 운영에 없습니다."
                  />
                </Tile>
              </div>
            </Panel>
          </>
        )}
      </AsyncBoundary>

      <Panel
        contract="navigation"
        title="여기서부터 시작하세요"
        hint="왼쪽 메뉴의 01 → 02 → 03과 같은 순서입니다."
      >
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
    </div>
  );
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

/** 어두운 히어로 위에서는 공용 Numeric의 회색 톤이 읽히지 않으므로 별도 표기를 쓴다. */
function HeroStat({
  label,
  value,
  format,
  text,
  missingReason,
}: {
  label: string;
  value?: number | null;
  format?: (v: number) => string;
  text?: string | null;
  missingReason?: string;
}) {
  const resolved =
    text !== undefined
      ? (text ?? null)
      : value !== null && value !== undefined && Number.isFinite(value) && format
        ? format(value)
        : null;
  return (
    <div>
      <p className="text-[12px] text-white/50">{label}</p>
      {resolved === null ? (
        <p className="mt-1 text-[15px] text-white/40" title={missingReason ?? '검증된 근거가 없습니다.'}>
          근거 없음
        </p>
      ) : (
        <p className="tnum mt-1 text-[18px] font-semibold text-white">{resolved}</p>
      )}
    </div>
  );
}

function HeroDelta({ value }: { value: number | null }) {
  if (value === null || !Number.isFinite(value)) {
    return <span className="text-[14px] text-white/40">오늘 손익률 근거 없음</span>;
  }
  const tone = value > 0 ? 'bg-white/15 text-white' : value < 0 ? 'bg-white/15 text-white' : 'bg-white/10 text-white/80';
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

function AutomationHeroTile() {
  const { state } = useResource(async () => {
    const { data } = await api.automationStatusV2();
    return ready(data, data.policy?.updatedAt ?? null);
  }, []);

  const status = hasData(state) ? state.data : null;
  const dot = !status
    ? 'bg-white/30'
    : status.projectionState === 'RUNNING'
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
        {status
          ? AUTOMATION_STATE_LABELS[status.projectionState]
          : state.kind === 'error'
            ? '확인 실패'
            : '확인 중'}
      </Link>
      {status?.killSwitchActive ? (
        <p className="mt-1 text-[11px] text-red-200">Kill Switch 작동 중</p>
      ) : null}
    </div>
  );
}
