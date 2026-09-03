'use client';

import { api } from '@/shared/api/endpoints';
import { apiMode, baseUrl } from '@/shared/api/client';
import { session, useSession } from '@/shared/api/session';
import { useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { relativeAge } from '@/shared/lib/format';
import type { SystemHealthResponse } from '@/shared/api/wire';

/** 사용자가 지금 어느 모드인지, 데이터가 신선한지, 자동주문이 멈췄는지를 상시 표기한다. */
export function StatusBar() {
  const { authenticated, user } = useSession();
  const mock = apiMode() === 'mock';
  const { state } = useResource(async () => {
    const { data } = await api.health();
    return ready<SystemHealthResponse>(data, data.asOf);
  }, [authenticated], mock || authenticated);

  const health = state.kind === 'ready' || state.kind === 'stale' ? state.data : null;

  return (
    <div className="sticky top-0 z-30 border-b border-line bg-panel/85 backdrop-blur">
      {mock ? (
        <p className="flex items-center gap-2 bg-warn/[0.07] px-5 py-2 text-[12px] leading-5 text-ink sm:px-8">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
          <span>
            <span className="font-semibold text-warn">합성 데이터</span> 화면 검증용 값입니다. 실제 성과나
            계좌 상태가 아니며 보고서에 성과로 인용할 수 없습니다.
          </span>
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 px-5 py-3 sm:px-8">
        <Chip label="연결" value={mock ? '합성 데이터' : baseUrl() || 'same-origin /api'} tone="navy" />
        <Chip
          label="자동주문"
          value={health ? (health.killSwitchActive ? '정지됨' : '작동 중') : '확인 중'}
          tone={health?.killSwitchActive ? 'block' : health ? 'allow' : 'default'}
        />
        <Chip
          label="가격"
          value={freshnessLabel(health?.dataFreshness?.priceFresh)}
          tone={health?.dataFreshness?.priceFresh === false ? 'warn' : 'default'}
        />
        <Chip label="신호" value={freshnessLabel(health?.dataFreshness?.signalFresh)} />
        <Chip label="설명 근거" value={freshnessLabel(health?.dataFreshness?.ragFresh)} />
        <span className="ml-auto flex items-center gap-3">
          <span className="tnum text-[11px] text-faint">
            {health ? `기준 ${relativeAge(health.asOf) ?? '방금'}` : '상태 확인 중'}
          </span>
          {authenticated && user ? (
            <button
              type="button"
              onClick={() => session.clear()}
              className="rounded-full border border-line px-3 py-1 text-[12px] font-medium text-muted hover:border-navy hover:text-navy"
            >
              {user.username} · 로그아웃
            </button>
          ) : null}
        </span>
      </div>
    </div>
  );
}

function freshnessLabel(value: boolean | null | undefined): string {
  if (value === true) return '최신';
  if (value === false) return '지연';
  return '근거 없음';
}

const DOT: Record<string, string> = {
  navy: 'bg-navy',
  allow: 'bg-allow',
  warn: 'bg-warn',
  block: 'bg-block',
  default: 'bg-faint',
};

function Chip({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'navy' | 'allow' | 'warn' | 'block';
}) {
  const missing = value === '근거 없음';
  const toneClass =
    tone === 'navy'
      ? 'text-ink'
      : tone === 'allow'
        ? 'text-allow'
        : tone === 'warn'
          ? 'text-warn'
          : tone === 'block'
            ? 'text-block'
            : missing
              ? 'text-faint'
              : 'text-ink';
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-subtle px-3 py-1.5">
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${missing ? 'bg-line' : DOT[tone]}`} />
      <span className="text-[11px] text-faint">{label}</span>
      <span className={`text-[12px] font-medium ${toneClass}`}>{value}</span>
    </span>
  );
}
