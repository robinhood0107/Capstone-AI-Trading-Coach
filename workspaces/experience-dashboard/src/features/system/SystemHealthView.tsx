'use client';

import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { useResource } from '@/shared/lib/useResource';
import { api } from '@/shared/api/endpoints';
import { ready } from '@/shared/lib/viewState';
import { formatKstDateTime } from '@/shared/lib/format';
import type { SystemHealthResponse } from '@/shared/api/wire';

/**
 * 시스템 상태.
 *
 * 백엔드는 진작 `/api/v1/system/health` 를 내보내고 있었는데 화면이 없었다. 값이 틀어졌을 때
 * "왜 안 되지"를 로그가 아니라 화면에서 먼저 볼 수 있게 한다.
 *
 * 모르는 것은 모른다고 적는다 — `dataFreshness` 는 `null` 이 올 수 있고, 그건 "신선하다"도
 * "낡았다"도 아니라 **아직 판단할 근거가 없다**는 뜻이다.
 */
export function SystemHealthView() {
  const { state, reload } = useResource(async () => {
    const { data } = await api.health();
    return ready<SystemHealthResponse>(data, data.asOf);
  }, []);

  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(health) => (
        <Panel
          contract="GET /api/v1/system/health"
          title="시스템 상태"
          hint="지금 무엇이 붙어 있고 무엇이 낡았는지 봅니다."
          actions={
            <span className="tnum text-[12px] text-faint">{formatKstDateTime(health.asOf)}</span>
          }
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <ServiceRow label="Python 서비스" value={health.pythonService} />
            <ServiceRow label="증권사 연결" value={health.brokerage} />
            <ServiceRow
              label="Kill Switch"
              value={health.killSwitchActive ? 'ACTIVE' : 'OFF'}
              tone={health.killSwitchActive ? 'block' : 'ok'}
            />
          </div>

          <div className="mt-6 border-t border-line pt-4">
            <p className="text-[12px] font-medium text-muted">데이터 신선도</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              <FreshnessRow label="시세" value={health.dataFreshness.priceFresh} />
              <FreshnessRow label="신호" value={health.dataFreshness.signalFresh} />
              <FreshnessRow label="RAG 색인" value={health.dataFreshness.ragFresh} />
            </div>
          </div>

          <div className="mt-6 border-t border-line pt-4">
            <p className="text-[12px] font-medium text-muted">기능 축소</p>
            {health.degradedFeatures.length === 0 ? (
              <p className="mt-2 text-[13px] leading-6 text-muted">축소된 기능이 없습니다.</p>
            ) : (
              <ul className="mt-2 flex flex-wrap gap-2">
                {health.degradedFeatures.map((feature) => (
                  <li
                    key={feature}
                    className="rounded-full border border-warn px-3 py-1 font-mono text-[12px] text-warn"
                  >
                    {feature}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      )}
    </AsyncBoundary>
  );
}

/** 서비스 상태 문자열은 백엔드가 정한다. 아는 값만 색으로 구분하고 나머지는 그대로 적는다. */
function ServiceRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: 'ok' | 'block';
}) {
  const resolved = tone ?? (value === 'UP' ? 'ok' : value === 'DOWN' ? 'block' : undefined);
  const color =
    resolved === 'ok' ? 'text-allow' : resolved === 'block' ? 'text-block' : 'text-ink';
  return (
    <div className="flex items-baseline justify-between gap-3 rounded-tile bg-subtle px-4 py-3">
      <span className="text-[13px] text-muted">{label}</span>
      <span className={`font-mono text-[13px] font-semibold ${color}`}>{value}</span>
    </div>
  );
}

function FreshnessRow({ label, value }: { label: string; value: boolean | null }) {
  const text = value === null ? '판단 근거 없음' : value ? '최신' : '낡음';
  const color = value === null ? 'text-faint' : value ? 'text-allow' : 'text-warn';
  return (
    <div className="flex items-baseline justify-between gap-3 rounded-tile bg-subtle px-4 py-3">
      <span className="text-[13px] text-muted">{label}</span>
      <span className={`text-[13px] font-semibold ${color}`}>{text}</span>
    </div>
  );
}
