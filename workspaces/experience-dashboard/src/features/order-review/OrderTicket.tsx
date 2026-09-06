'use client';

import { useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { newIdempotencyKey } from '@/shared/api/client';
import { toErrorState, useResource } from '@/shared/lib/useResource';
import { ready } from '@/shared/lib/viewState';
import { formatKrw, formatKstDateTime } from '@/shared/lib/format';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DecisionBadge } from '@/shared/ui/Decision';
import type {
  AutomationStatusV2,
  DecisionProjection,
  MockBuyable,
  OrderDetail,
  OrderIntent,
  PrincipleSummary,
} from '@/shared/api/wire';
import {
  buildIntent,
  canConfirm,
  canSubmit,
  evaluateGates,
  firstBlocking,
  type Gate,
} from './orderGates';

interface TicketContext {
  status: AutomationStatusV2;
  principle: PrincipleSummary;
  /** 주문 계약이 요구하는 값. v1 통제 상태에만 있다. */
  strategyId: string;
  /** `null` 은 못 읽었다는 뜻이다 — 꺼져 있다는 뜻이 아니다. */
  killSwitchActive: boolean | null;
}

/**
 * 주문 내기.
 *
 * 수량과 단가를 넣고 → 원칙으로 평가하고 → 내용을 다시 확인하고 → 모의계좌에 낸다.
 * 각 단계가 관문 하나씩이며, 막힌 관문은 이유와 함께 화면에 남는다(`orderGates.ts`).
 *
 * **쓰기는 자동으로 다시 보내지 않는다.** 실패는 그 자리에 적고 사용자가 다시 누른다
 * (`envelope.ts` 의 `RETRYABLE` 에 `CONFLICT` 가 빠진 것은 의도된 설계다).
 */
export function OrderTicket() {
  const { state, reload } = useResource(async () => {
    const [status, principles, control] = await Promise.all([
      api.automationStatusV2(),
      api.principles(),
      api.automationControlV1(),
    ]);
    const principle =
      principles.data.items.find((item) => item.status === 'ACTIVE') ?? principles.data.items[0];
    if (!principle) {
      throw new Error('활성 원칙이 없습니다. 먼저 내 원칙 화면에서 원칙을 만드세요.');
    }
    // Kill Switch 는 곁다리다. 못 읽어도 화면은 뜨고, 관문이 "아직 모른다"로 남는다.
    const killSwitch = await api
      .killSwitch()
      .then((result) => result.data.effectiveActive ?? result.data.active)
      .catch(() => null);
    return ready<TicketContext>({
      status: status.data,
      principle,
      strategyId: control.data.strategyId,
      killSwitchActive: killSwitch,
    });
  }, []);

  return (
    <AsyncBoundary state={state} onRetry={reload}>
      {(context) => <Ticket context={context} />}
    </AsyncBoundary>
  );
}

function Ticket({ context }: { context: TicketContext }) {
  const { status, principle, strategyId, killSwitchActive } = context;

  const [symbol, setSymbol] = useState('');
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');

  const [buyable, setBuyable] = useState<MockBuyable | null>(null);
  const [decision, setDecision] = useState<DecisionProjection | null>(null);
  const [confirming, setConfirming] = useState(false);
  /** 확인 화면에 들어갈 때마다 새로 만든다. 폼 단위로 잡으면 재제출이 조용히 무시된다. */
  const [submitKey, setSubmitKey] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<OrderDetail | null>(null);
  const [busy, setBusy] = useState<'evaluate' | 'submit' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const intent = buildIntent({
    symbol: symbol.trim().toUpperCase(),
    side,
    orderType: 'MARKET',
    quantity: Number(quantity),
    estimatedPrice: Number(price),
    strategyId,
  });

  // 입력이 바뀌면 이전 판정은 더 이상 이 주문의 것이 아니다. 지운다.
  const intentKey = intent ? JSON.stringify(intent) : '';
  useEffect(() => {
    setDecision(null);
    setConfirming(false);
    setSubmitKey(null);
    setSubmitted(null);
  }, [intentKey]);

  // 주문가능금액은 종목과 단가가 둘 다 정해져야 읽을 수 있다 — 서버가 둘 다 요구한다.
  const priceNumber = Number(price);
  useEffect(() => {
    const target = symbol.trim().toUpperCase();
    if (!target || !status.accountId || side !== 'BUY' || !Number.isInteger(priceNumber) || priceNumber < 1) {
      setBuyable(null);
      return;
    }
    let alive = true;
    api
      .mockBuyable(status.accountId, target, priceNumber)
      .then((result) => {
        if (alive) setBuyable(result.data);
      })
      .catch(() => {
        if (alive) setBuyable(null);
      });
    return () => {
      alive = false;
    };
  }, [symbol, side, priceNumber, status.accountId]);

  const action = decision?.riskDecision.decision ?? null;
  const decisionExpired = decision ? Date.parse(decision.validUntil) <= Date.now() : false;
  const gates = evaluateGates({
    status,
    killSwitchActive,
    buyable,
    intent,
    action,
    decisionExpired,
    acknowledged: confirming,
  });
  const blocking = firstBlocking(gates);

  async function evaluate() {
    if (!intent || busy) return;
    setBusy('evaluate');
    setError(null);
    try {
      const result = await api.evaluateOrder({
        principleId: principle.principleId,
        portfolioSource: 'KIS_MOCK',
        orderIntent: intent,
      });
      setDecision(result.data);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '평가하지 못했습니다.');
    } finally {
      setBusy(null);
    }
  }

  function openConfirm() {
    setSubmitKey(newIdempotencyKey('mock-order'));
    setConfirming(true);
    setError(null);
  }

  function cancelConfirm() {
    setSubmitKey(null);
    setConfirming(false);
  }

  async function submit() {
    if (!intent || !decision || !submitKey || busy) return;
    setBusy('submit');
    setError(null);
    try {
      const result = await api.submitMockOrder(
        {
          decisionId: decision.decisionId,
          orderIntent: intent,
          userAcknowledgement: { warningsAccepted: true },
        },
        submitKey,
      );
      setSubmitted({
        orderId: result.data.orderId,
        accountId: result.data.accountId,
        decisionId: decision.decisionId,
        brokerageMode: 'KIS_MOCK',
        status: result.data.status,
        submittedAt: result.data.submittedAt,
      });
      setConfirming(false);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '주문을 내지 못했습니다.');
    } finally {
      setBusy(null);
    }
  }

  async function cancelOrder() {
    if (!submitted || busy) return;
    setBusy('cancel');
    setError(null);
    try {
      const result = await api.cancelOrder(submitted.orderId, newIdempotencyKey('order-cancel'));
      setSubmitted(result.data);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '취소하지 못했습니다.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel
      contract="POST /api/v1/decisions/evaluate-order · POST /api/v1/brokerage/mock/orders"
      title="주문 내기"
      hint="원칙 판정을 통과한 주문만 모의계좌로 나갑니다. 실계좌 경로는 이 화면에 없습니다."
    >
      {submitted ? (
        <SubmittedOrder
          order={submitted}
          busy={busy === 'cancel'}
          onCancel={() => void cancelOrder()}
          onNew={() => {
            setSubmitted(null);
            setDecision(null);
            setQuantity('');
            setPrice('');
          }}
          error={error}
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="종목코드" htmlFor="order-symbol">
              <input
                id="order-symbol"
                value={symbol}
                placeholder="005930"
                onChange={(event) => setSymbol(event.target.value)}
                className="w-full rounded-control border border-line bg-subtle px-3 py-2 font-mono text-[14px] text-ink placeholder:text-faint focus:border-navy focus:bg-panel"
              />
            </Field>
            <Field label="매수 / 매도" htmlFor="order-side">
              <select
                id="order-side"
                value={side}
                onChange={(event) => setSide(event.target.value as 'BUY' | 'SELL')}
                className="w-full rounded-control border border-line bg-subtle px-3 py-2 text-[14px] text-ink focus:border-navy focus:bg-panel"
              >
                <option value="BUY">매수</option>
                <option value="SELL">매도</option>
              </select>
            </Field>
            <Field label="수량 (주)" htmlFor="order-qty">
              <input
                id="order-qty"
                value={quantity}
                inputMode="numeric"
                placeholder="10"
                onChange={(event) => setQuantity(event.target.value.replace(/[^0-9]/g, ''))}
                className="tnum w-full rounded-control border border-line bg-subtle px-3 py-2 text-[14px] text-ink placeholder:text-faint focus:border-navy focus:bg-panel"
              />
            </Field>
            <Field label="예상 단가 (원)" htmlFor="order-price">
              <input
                id="order-price"
                value={price}
                inputMode="numeric"
                placeholder="71000"
                onChange={(event) => setPrice(event.target.value.replace(/[^0-9]/g, ''))}
                className="tnum w-full rounded-control border border-line bg-subtle px-3 py-2 text-[14px] text-ink placeholder:text-faint focus:border-navy focus:bg-panel"
              />
            </Field>
          </div>

          <div className="mt-4 flex flex-wrap items-baseline justify-between gap-3 border-t border-line pt-4">
            <span className="text-[13px] text-muted">
              주문 금액{' '}
              <span className="tnum font-semibold text-ink">
                {intent ? formatKrw(intent.estimatedAmount) : '—'}
              </span>
            </span>
            {buyable ? (
              <span className="text-[12px] text-faint">
                주문가능 {formatKrw(buyable.buyableAmountKrw)} · 최대 {buyable.buyableQuantity}주
              </span>
            ) : null}
          </div>

          <GateList gates={gates} />

          {decision ? (
            <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
              <DecisionBadge status={decision.riskDecision.decision} />
              <span className="tnum text-[12px] text-faint">
                유효 {formatKstDateTime(decision.validUntil) ?? '미상'}
              </span>
            </div>
          ) : null}

          {error ? (
            <p className="mt-4 border-l-2 border-block bg-block/5 px-3 py-2 text-[13px] leading-6 text-ink">
              {error}
            </p>
          ) : null}

          {confirming && intent ? (
            <ConfirmStep
              intent={intent}
              busy={busy === 'submit'}
              disabled={!canSubmit(gates)}
              onBack={cancelConfirm}
              onSubmit={() => void submit()}
            />
          ) : (
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p className="max-w-2xl text-[12px] leading-5 text-muted">
                {blocking && blocking.note ? blocking.note : '평가를 통과하면 확인 화면으로 넘어갑니다.'}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  disabled={!intent || busy !== null}
                  onClick={() => void evaluate()}
                  className="rounded-full border border-line px-4 py-1.5 text-[13px] font-semibold text-muted hover:border-navy hover:text-navy"
                >
                  {busy === 'evaluate' ? '평가 중' : '원칙으로 평가'}
                </Button>
                <Button
                  disabled={!canConfirm(gates) || busy !== null}
                  onClick={openConfirm}
                  variant="primary"
                >
                  내용 확인
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

function GateList({ gates }: { gates: Gate[] }) {
  return (
    <ul className="mt-5 space-y-1.5 border-t border-line pt-4">
      {gates.map((gate) => (
        <li key={gate.id} className="flex items-baseline gap-2 text-[13px] leading-6">
          <span
            aria-hidden
            className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
              gate.passed === true ? 'bg-allow' : gate.passed === false ? 'bg-block' : 'bg-faint'
            }`}
          />
          <span className={gate.passed === true ? 'text-muted' : 'text-ink'}>{gate.label}</span>
          {gate.note ? <span className="min-w-0 text-muted">— {gate.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function ConfirmStep({
  intent,
  busy,
  disabled,
  onBack,
  onSubmit,
}: {
  intent: OrderIntent;
  busy: boolean;
  disabled: boolean;
  onBack: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="mt-5 border-t-2 border-navy pt-4">
      <p className="text-[14px] font-semibold text-ink">이대로 모의계좌에 냅니다</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
        <Summary label="종목" value={intent.symbol} mono />
        <Summary label="구분" value={intent.side === 'BUY' ? '매수' : '매도'} />
        <Summary label="수량" value={`${intent.quantity}주`} mono />
        <Summary label="금액" value={formatKrw(intent.estimatedAmount)} mono />
      </dl>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] leading-5 text-muted">
          제출하면 되돌릴 수 없습니다. 낸 뒤 취소는 체결 전까지만 됩니다.
        </p>
        <div className="flex items-center gap-2">
          <Button
            disabled={busy}
            onClick={onBack}
            className="rounded-full border border-line px-3 py-1.5 text-[13px] text-muted"
          >
            뒤로
          </Button>
          <Button disabled={disabled || busy} onClick={onSubmit} variant="primary">
            {busy ? '내는 중' : '주문 제출'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function SubmittedOrder({
  order,
  busy,
  onCancel,
  onNew,
  error,
}: {
  order: OrderDetail;
  busy: boolean;
  onCancel: () => void;
  onNew: () => void;
  error: string | null;
}) {
  const cancellable = order.status === 'SUBMITTED' || order.status === 'ACCEPTED';
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-[13px] sm:grid-cols-4">
        <Summary label="주문번호" value={order.orderId} mono />
        <Summary label="상태" value={order.status} mono />
        <Summary label="제출시각" value={formatKstDateTime(order.submittedAt) ?? '미상'} mono />
        <Summary label="판정" value={order.decisionId} mono />
      </dl>

      {error ? (
        <p className="mt-4 border-l-2 border-block bg-block/5 px-3 py-2 text-[13px] leading-6 text-ink">
          {error}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        <p className="text-[12px] leading-5 text-muted">
          {cancellable
            ? '아직 체결되지 않았습니다. 지금은 취소할 수 있습니다.'
            : '이 주문은 더 이상 취소할 수 없습니다.'}
        </p>
        <div className="flex items-center gap-2">
          {cancellable ? (
            <Button
              disabled={busy}
              onClick={onCancel}
              className="rounded-full border border-block px-4 py-1.5 text-[13px] font-semibold text-block"
            >
              {busy ? '취소 중' : '주문 취소'}
            </Button>
          ) : null}
          <Button
            onClick={onNew}
            className="rounded-full border border-line px-4 py-1.5 text-[13px] text-muted hover:border-navy hover:text-navy"
          >
            새 주문
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="text-[12px] font-medium text-muted">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Summary({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] text-faint">{label}</dt>
      <dd className={`mt-0.5 truncate text-ink ${mono ? 'font-mono text-[12px]' : ''}`}>{value}</dd>
    </div>
  );
}
