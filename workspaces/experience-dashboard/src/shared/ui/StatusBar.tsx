'use client';

import { useEffect } from 'react';
import { api } from '@/shared/api/endpoints';
import { apiMode } from '@/shared/api/client';
import { session, useSession } from '@/shared/api/session';
import { useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { relativeAge } from '@/shared/lib/format';
import { UtilityNav } from '@/shared/ui/NavRail';
import { ThemeToggle } from '@/shared/ui/ThemeToggle';
import type { AutomationStatusV2 } from '@/shared/api/wire';

export function StatusBar() {
  const { authenticated, user } = useSession();
  const mock = apiMode() === 'mock';
  const { state, reload } = useResource(async () => {
    const { data } = await api.automationStatusV2();
    return ready<AutomationStatusV2>(data, data.policy?.updatedAt ?? null);
  }, [authenticated], mock || authenticated);

  useEffect(() => {
    window.addEventListener('capstone-automation-changed', reload);
    return () => window.removeEventListener('capstone-automation-changed', reload);
  }, [reload]);

  const automation = state.kind === 'ready' || state.kind === 'stale' ? state.data : null;

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
      <div className="flex flex-wrap items-center gap-3 px-5 py-3 sm:px-8">
        <AutomationState status={automation} />
        <BrokerageMode status={automation} />
        {automation?.policy?.updatedAt ? (
          <span className="tnum text-[11px] text-faint">기준 {relativeAge(automation.policy.updatedAt) ?? '방금'}</span>
        ) : null}

        <span className="ml-auto flex flex-wrap items-center gap-2">
          <UtilityNav />
          <span aria-hidden className="mx-1 hidden h-4 w-px bg-line sm:block" />
          <ThemeToggle />
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

/**
 * 지금 붙어 있는 계좌 종류.
 *
 * 화면 문구에는 "모의"를 박아 두지 않는다. 계좌가 어떤 것인지는 실행 중인 시스템이 알고
 * 있고(`brokerageMode`), 화면은 그 값을 그대로 비춘다. 값이 아직 없으면 배지를 감춘다 —
 * 모르는 것을 추측해서 채우지 않는다.
 */
function BrokerageMode({ status }: { status: AutomationStatusV2 | null }) {
  if (!status) return null;
  const label =
    status.brokerageMode === 'KIS_MOCK'
      ? 'KIS 모의계좌'
      : status.brokerageMode === 'INTERNAL_PAPER'
        ? '내부 가상원장'
        : status.brokerageMode;
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-subtle px-3 py-1.5">
      <span className="text-[11px] text-faint">계좌</span>
      <span className="text-[12px] font-semibold text-ink">{label}</span>
    </span>
  );
}

function AutomationState({ status }: { status: AutomationStatusV2 | null }) {
  if (!status) {
    return (
      <span className="inline-flex items-center gap-2 rounded-full bg-subtle px-3 py-1.5">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-line" />
        <span className="text-[12px] font-medium text-faint">상태 확인 중</span>
      </span>
    );
  }
  const halted = status.killSwitchActive || status.projectionState === 'HALTED';
  const disarmed = status.controlState === 'DISARMED';
  const label = halted ? '안전 중단' : disarmed ? '꺼짐' : status.controlState === 'ARMED' ? '예약됨' : '작동 중';
  const tone = halted ? 'text-block' : disarmed ? 'text-muted' : 'text-allow';
  const dot = halted ? 'bg-block' : disarmed ? 'bg-faint' : 'bg-allow';
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-subtle px-3 py-1.5">
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className="text-[11px] text-faint">자동주문</span>
      <span className={`text-[12px] font-semibold ${tone}`}>{label}</span>
    </span>
  );
}
