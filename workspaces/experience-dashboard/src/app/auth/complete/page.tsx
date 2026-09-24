'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { isEnvelope } from '@/shared/api/envelope';
import { session } from '@/shared/api/session';
import type { LoginResponse } from '@/shared/api/wire';

function readLoginResponse(payload: unknown): LoginResponse {
  if (!isEnvelope(payload) || !payload.success || !payload.data || typeof payload.data !== 'object') {
    throw new Error('OIDC exchange failed');
  }
  const data = payload.data as Partial<LoginResponse>;
  if (
    typeof data.accessToken !== 'string' ||
    typeof data.expiresAt !== 'string' ||
    data.tokenType !== 'Bearer' ||
    !data.user ||
    typeof data.user.userId !== 'string' ||
    typeof data.user.username !== 'string' ||
    (data.user.role !== 'USER' && data.user.role !== 'ADMIN')
  ) {
    throw new Error('OIDC exchange response is invalid');
  }
  return data as LoginResponse;
}

export default function SocialLoginComplete() {
  const router = useRouter();
  const started = useRef(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    async function exchange() {
      try {
        const response = await fetch('/api/v1/auth/oidc/exchange', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: AbortSignal.timeout(10_000),
        });
        if (!response.ok) throw new Error('OIDC exchange failed');
        const login = readLoginResponse(await response.json());
        session.set(login.accessToken, login.expiresAt, login.user);
        router.replace('/');
      } catch {
        setFailed(true);
      }
    }
    void exchange();
  }, [router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-[420px] flex-col items-center justify-center px-6 text-center">
        <h1 className="text-[22px] font-semibold text-ink">로그인 확인</h1>
      {failed ? (
        <>
          <p className="mt-3 text-[14px] leading-6 text-muted">로그인을 완료하지 못했습니다. 다시 시도해 주세요.</p>
          <Link href="/" className="mt-6 rounded-control bg-brand px-5 py-3 text-on-brand">처음으로</Link>
        </>
      ) : (
        <p className="mt-3 text-[14px] leading-6 text-muted">안전하게 로그인 처리 중입니다.</p>
      )}
    </main>
  );
}
