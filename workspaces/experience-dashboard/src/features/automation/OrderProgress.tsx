'use client';

import { Numeric } from '@/shared/ui/Numeric';
import { formatKrw } from '@/shared/lib/format';
import { Term } from '@/shared/ui/Term';
import type { AutomationRunV2 } from '@/shared/api/wire';

/**
 * 이 컴포넌트가 실제로 쓰는 값만 요구한다.
 *
 * `AutomationRunV2` 로 못박으면 v3 실행을 넘길 수 없다(계약 id 가 다르다). 두 버전에서
 * 수량 필드는 똑같으므로 그 다섯 개만 받는다.
 */
type OrderProgressRun = Pick<
  AutomationRunV2,
  'orderQuantity' | 'filledQuantity' | 'leavesQuantity' | 'limitPriceKrw' | 'estimatedAmountKrw' | 'selectedSide'
>;

/**
 * 오늘 낸 주문이 지금 어디까지 왔는가.
 *
 * 서버는 처음부터 `orderQuantity`·`filledQuantity`·`leavesQuantity`·`limitPriceKrw`·
 * `selectedSide` 를 내려 주고 있었다(`automation_order_reservations` 를 조인해서).
 * **그런데 화면이 그 다섯 개를 하나도 그리지 않았다.** 2026-09-16 에 45주를 주문해
 * 27주가 체결됐는데, 화면에는 종목 이름과 상태 글자만 있었다. 돈이 이미 나간 사실이
 * 어디에도 없었다.
 *
 * `orders` 표는 주문이 끝난 뒤에 한 번에 갱신된다(V147). 그래서 장중에 그 표를 보면
 * `filled=0` 이다. 살아 있는 값은 예약 쪽에 있고, 이 컴포넌트는 그것을 그린다.
 */
export function OrderProgress({ run }: { run: OrderProgressRun }) {
  const ordered = run.orderQuantity;
  if (ordered === null || ordered <= 0) return null;

  const filled = run.filledQuantity ?? 0;
  const leaves = run.leavesQuantity ?? 0;
  const ratio = Math.min(1, Math.max(0, filled / ordered));
  const side = run.selectedSide === 'SELL' ? '매도' : '매수';

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[12px]">
        <span className="font-medium text-ink">
          {side} {ordered.toLocaleString('ko-KR')}주
        </span>
        {run.limitPriceKrw !== null ? (
          <span className="text-muted">
            <Term name="limitPrice">지정가</Term> {formatKrw(run.limitPriceKrw)}
          </span>
        ) : null}
        <span
          className={
            filled === 0 ? 'text-faint' : filled === ordered ? 'text-allow' : 'text-hold'
          }
        >
          {filled === 0
            ? '아직 체결 없음'
            : filled === ordered
              ? '전량 체결'
              : `${filled.toLocaleString('ko-KR')}주 체결 · ${leaves.toLocaleString('ko-KR')}주 대기`}
        </span>
      </div>

      {/*
       * 막대 하나로 진행을 보여 준다. 숫자만 있으면 "27/45"가 많은 건지 적은 건지 한눈에
       * 안 들어온다. 화면낭독기에는 위 문장이 이미 같은 사실을 말하므로 막대는 숨긴다.
       */}
      <div aria-hidden className="mt-1.5 h-1 w-full max-w-[220px] bg-line">
        <div
          className={filled === ordered ? 'h-1 bg-allow' : 'h-1 bg-hold'}
          style={{ width: `${(ratio * 100).toFixed(1)}%` }}
        />
      </div>

      {run.estimatedAmountKrw !== null ? (
        <p className="mt-1.5 text-[11px] text-faint">
          예상 주문 금액{' '}
          <Numeric
            value={run.estimatedAmountKrw}
            format={formatKrw}
            missingReason="예상 금액이 기록되지 않았습니다."
          />
        </p>
      ) : null}
    </div>
  );
}
