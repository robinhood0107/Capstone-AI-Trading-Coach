'use client';

import { api } from '@/shared/api/endpoints';
import { useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { formatKrw, formatKstDateTime } from '@/shared/lib/format';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { InstrumentIdentity, instrumentMap } from '@/shared/ui/InstrumentIdentity';
import type { OrderFill } from '@/shared/api/wire';
import { ApiFailure } from '@/shared/api/envelope';
import { fillWindow, FILL_WINDOW_MAX_DAYS } from './orderGates';

/**
 * 최근 체결 내역.
 *
 * 조회 창은 서버가 최대 31일만 받으므로 `fillWindow()` 가 만든 것만 쓴다.
 *
 * **체결 원장이 없는 계좌는 404 가 온다.** 오류가 아니라 아직 아무것도 체결되지 않았다는
 * 뜻이므로(`JdbcOrderFillRepository.kt:209`) 빈 상태로 그린다 — 붉은 오류 상자를 띄우면
 * 사용자가 고장으로 읽는다.
 */
export function FillsPanel() {
  const { state, reload, refreshError } = useResource(async () => {
    // 종목명 카탈로그는 표시용 곁가지다. 이것 하나가 실패해서 체결 목록이 사라지면
    // 사용자는 주문이 실제로 어떻게 됐는지 확인할 방법을 잃는다. 없으면 코드로 표시한다.
    const [status, catalog] = await Promise.all([
      api.automationStatusV2(),
      api.instrumentDisplayCatalog().catch(() => null),
    ]);
    const instruments = catalog?.data ?? { items: [] };
    const accountId = status.data.accountId;
    if (!accountId) return ready<{ fills: OrderFill[]; instruments: typeof instruments }>({
      fills: [],
      instruments,
    });

    const { from, to } = fillWindow();
    const fills = await api
      .mockFills(accountId, from, to)
      .then((result) => result.data.items)
      .catch((error: unknown) => {
        if (error instanceof ApiFailure && error.code === 'NOT_FOUND') return [] as OrderFill[];
        throw error;
      });
    return ready({ fills, instruments });
  }, [], true, 5_000);

  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(data) => {
        const bySymbol = instrumentMap(data.instruments.items);
        return (
          <Panel
            contract="GET /api/v1/brokerage/mock/accounts/{accountId}/fills"
            title="최근 체결"
            hint={`최근 ${FILL_WINDOW_MAX_DAYS}일 동안 실제로 체결된 것만 표시합니다.`}
          >
            <p className="h-8 overflow-hidden text-[11px] text-muted" role="status">{refreshError ?? '\u00a0'}</p>
            {data.fills.length === 0 ? (
              <p className="rounded-tile border border-dashed border-rule px-4 py-6 text-[13px] leading-6 text-muted">
                이 기간에 체결된 주문이 없습니다.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead className="border-b border-line text-left text-eyebrow font-semibold uppercase text-faint">
                    <tr>
                      <th className="pb-2 font-normal">종목</th>
                      <th className="pb-2 font-normal">구분</th>
                      <th className="pb-2 text-right font-normal">수량</th>
                      <th className="pb-2 text-right font-normal">체결가</th>
                      <th className="pb-2 text-right font-normal">체결시각</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.fills.map((fill) => (
                      <tr key={`${fill.orderId}:${fill.execRefHash}`} className="border-b border-line/60 last:border-0">
                        <td className="py-2.5">
                          <InstrumentIdentity
                            symbol={fill.symbol}
                            instrument={bySymbol.get(fill.symbol)}
                            compact
                          />
                        </td>
                        <td className="py-2.5 text-muted">
                          {fill.side === 'BUY' ? '매수' : '매도'}
                        </td>
                        <td className="tnum py-2.5 text-right font-mono">{Number.isFinite(fill.fillQuantity) ? `${fill.fillQuantity}주` : '확인 필요'}</td>
                        <td className="tnum py-2.5 text-right font-mono">
                          {formatKrw(fill.fillPriceKrw)}
                        </td>
                        <td className="tnum py-2.5 text-right font-mono text-faint">
                          {formatKstDateTime(fill.filledAt) ?? '미상'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        );
      }}
    </AsyncBoundary>
  );
}
