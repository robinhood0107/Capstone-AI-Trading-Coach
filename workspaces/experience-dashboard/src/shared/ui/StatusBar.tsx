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
    <div className="border-b border-rule bg-panel">
      {mock ? (
        <p className="bg-warn/10 px-6 py-1.5 text-[12px] text-ink">
          <span className="font-mono text-eyebrow uppercase text-warn">합성 fixture</span> 화면 검증용
          합성 데이터입니다. 실제 성과나 계좌 상태가 아니며 보고서에 성과로 인용할 수 없습니다.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-2.5">
        <Chip label="연결" value={mock ? '합성 데이터' : baseUrl() || 'same-origin /api'} tone="navy" />
        <Chip
          label="자동주문"
          value={health ? (health.killSwitchActive ? '정지됨' : '작동 중') : '확인 중'}
          tone={health?.killSwitchActive ? 'block' : 'default'}
        />
        <Chip
          label="가격"
          value={freshnessLabel(health?.dataFreshness?.priceFresh)}
          tone={health?.dataFreshness?.priceFresh === false ? 'warn' : 'default'}
        />
        <Chip label="신호" value={freshnessLabel(health?.dataFreshness?.signalFresh)} />
        <Chip label="설명 근거" value={freshnessLabel(health?.dataFreshness?.ragFresh)} />
        <span className="ml-auto flex items-center gap-4">
          <span className="font-mono text-[11px] text-faint">
            {health ? `기준 ${relativeAge(health.asOf) ?? '방금'}` : '상태 확인 중'}
          </span>
          {authenticated && user ? (
            <button
              type="button"
              onClick={() => session.clear()}
              className="border border-line px-2 py-0.5 text-[12px] text-muted hover:border-navy hover:text-navy"
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

function Chip({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: string;
  tone?: 'default' | 'navy' | 'warn' | 'block';
}) {
  const toneClass =
    tone === 'navy'
      ? 'text-navy'
      : tone === 'warn'
        ? 'text-warn'
        : tone === 'block'
          ? 'text-block'
          : value === '근거 없음'
            ? 'text-faint'
            : 'text-ink';
  return (
    <span className="flex items-baseline gap-2">
      <span className="font-mono text-eyebrow uppercase text-faint">{label}</span>
      <span className={`text-[13px] font-medium ${toneClass}`}>{value}</span>
    </span>
  );
}
