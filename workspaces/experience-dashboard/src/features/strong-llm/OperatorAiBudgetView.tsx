'use client';

import { useId, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { useSession } from '@/shared/api/session';
import type { OperatorAiBudgetPolicy } from '@/shared/api/wire';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { ready } from '@/shared/lib/viewState';
import { toErrorState, useResource } from '@/shared/lib/useResource';

const MONEY = /^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?$/;

function centsToUsd(cents: number): string {
  return (cents / 100).toFixed(2);
}

function usdToCents(value: string): number | null {
  if (!MONEY.test(value)) return null;
  const [dollars, fraction = ''] = value.split('.');
  return Number(dollars) * 100 + Number(fraction.padEnd(2, '0'));
}

async function loadPolicy() {
  const { data } = await api.operatorAiBudget();
  return ready<OperatorAiBudgetPolicy>(data, null);
}

/** ADMIN only. The server verifies both the JWT role and the current Google identity in DB. */
export function OperatorAiBudgetView() {
  const { user } = useSession();
  if (user?.role !== 'ADMIN') return null;
  return <OperatorAiBudgetContent />;
}

function OperatorAiBudgetContent() {
  const resource = useResource(loadPolicy, []);
  return (
    <AsyncBoundary state={resource.state} onRetry={resource.reload}>
      {(policy) => <OperatorAiBudgetForm policy={policy} reload={resource.reload} />}
    </AsyncBoundary>
  );
}

function OperatorAiBudgetForm({
  policy,
  reload,
}: {
  policy: OperatorAiBudgetPolicy;
  reload: () => void;
}) {
  const id = useId();
  const [amount, setAmount] = useState(centsToUsd(policy.dailySoftCapCents));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cents = usdToCents(amount);
  const valid = cents !== null && cents <= policy.hardCapCents;

  async function save() {
    if (!valid || pending || cents === null) return;
    setPending(true);
    setMessage(null);
    setError(null);
    try {
      await api.putOperatorAiBudget({ dailySoftCapCents: cents, expectedRevision: policy.revision });
      setMessage('일일 한도를 저장했습니다.');
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '저장하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel
      contract="operator-ai-budget"
      title="운영자 AI 비용 한도"
      hint="Agent와 매매 AI가 함께 쓰는 하루 한도입니다. NAS 환경변수의 절대 상한보다 높일 수 없습니다."
    >
      <p className="text-[13px] text-muted">
        NAS 절대 상한: ${centsToUsd(policy.hardCapCents)} / 일 · 현재 웹 설정: ${centsToUsd(policy.dailySoftCapCents)} / 일
      </p>
      <label htmlFor={id} className="mt-5 block text-[13px] font-medium text-muted">
        웹 일일 한도 (USD)
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={amount}
          maxLength={11}
          onChange={(event) => setAmount(event.target.value)}
          className="mt-1.5 w-full max-w-xs rounded-control border border-line bg-panel px-4 py-2.5 text-[14px] text-ink focus:border-navy focus:outline-none"
        />
      </label>
      <p className="mt-2 text-[12px] text-muted">0.00을 저장하면 추가 과금 호출을 중단합니다.</p>
      <button
        type="button"
        disabled={!valid || pending}
        onClick={() => void save()}
        className="mt-4 rounded-control bg-brand px-5 py-2.5 text-[14px] font-semibold text-on-brand disabled:opacity-50"
      >
        {pending ? '저장 중…' : '한도 저장'}
      </button>
      {message ? <p role="status" className="mt-3 text-[13px] text-allow">{message}</p> : null}
      {error ? <p role="alert" className="mt-3 text-[13px] text-block">{error}</p> : null}
    </Panel>
  );
}
