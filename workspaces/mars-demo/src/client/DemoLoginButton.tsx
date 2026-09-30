'use client';

import { useState } from 'react';

export function DemoLoginButton() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  async function enter() {
    if (pending) return;
    setPending(true);
    setError('');
    try {
      const response = await fetch('/api/demo/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) {
        setError(response.status === 429
          ? '잠시 후 다시 입장해 주세요.'
          : '로그인하지 못했습니다. 잠시 후 다시 시도해 주세요.');
        setPending(false);
        return;
      }
      window.location.assign('/');
    } catch {
      setError('서버에 연결하지 못했습니다. 연결 상태를 확인해 주세요.');
      setPending(false);
    }
  }

  return (
    <div className="mt-7">
      {error ? <p role="alert" className="mb-3 rounded-control bg-block/[0.08] px-3 py-2 text-[13px] text-block">{error}</p> : null}
      <button
        type="button"
        onClick={() => void enter()}
        disabled={pending}
        className="tap w-full rounded-control bg-brand px-4 py-3 text-[15px] font-semibold text-on-brand hover:opacity-90 disabled:bg-line disabled:text-faint"
      >
        {pending ? '로그인 중…' : '로그인'}
      </button>
    </div>
  );
}
