'use client';

import Image from 'next/image';
import { useEffect, useState, type FormEvent } from 'react';
import { apiFetch } from '@/shared/api/client';
import { session } from '@/shared/api/session';
import type { LoginResponse } from '@/shared/api/wire';
import { toErrorState } from '@/shared/lib/useResource';

type AuthMode = 'login' | 'signup';
type AuthOptions = { signup: boolean; providers: string[] };

/** ID/email password login, minimal email signup, and provider login when the server enables it. */
export function LoginCard() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return null;
  return <AccountAuthCard />;
}

function AccountAuthCard() {
  const [mode, setMode] = useState<AuthMode>('login');
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    void apiFetch<AuthOptions>('/api/v1/auth/options', { anonymous: true })
      .then(({ data }) => { if (active) setProviders(data.providers); })
      .catch(() => { if (active) setProviders([]); });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const path = mode === 'signup' ? '/api/v1/auth/signup' : '/api/v1/auth/login';
      // FULL names the login field `identifier`; the private stack keeps its original `username`.
      const loginBody = process.env.NEXT_PUBLIC_MARS_PRODUCT === 'full'
        ? { identifier, password }
        : { username: identifier, password };
      const body = mode === 'signup' ? { email: identifier, password } : loginBody;
      const { data } = await apiFetch<LoginResponse>(path, {
        method: 'POST',
        body,
        anonymous: true,
      });
      session.set(data.accessToken, data.expiresAt, data.user);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '요청을 완료하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  const signingUp = mode === 'signup';

  return (
    <div className="mx-auto w-full max-w-[420px] pb-40 sm:pb-0">
      <section
        aria-labelledby="full-auth-title"
        className="rounded-panel border border-line bg-panel px-6 py-8 shadow-card sm:px-8"
      >
        <h1 id="full-auth-title" className="text-center text-[24px] font-semibold tracking-tight text-ink">
          {signingUp ? '계정 만들기' : '로그인'}
        </h1>
        <p className="mt-2 text-center text-[14px] leading-6 text-muted">
          투자 원칙과 운용 데이터를 계정에 안전하게 보관합니다.
        </p>

        <div className="mt-6 grid grid-cols-2 rounded-control bg-subtle p-1" role="tablist" aria-label="계정 사용 방식">
          <button
            type="button"
            role="tab"
            aria-selected={!signingUp}
            onClick={() => { setMode('login'); setError(null); }}
            className={`tap rounded-control px-3 py-2 text-[14px] font-medium ${!signingUp ? 'bg-panel text-ink shadow-sm' : 'text-muted'}`}
          >
            로그인
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={signingUp}
            onClick={() => { setMode('signup'); setError(null); }}
            className={`tap rounded-control px-3 py-2 text-[14px] font-medium ${signingUp ? 'bg-panel text-ink shadow-sm' : 'text-muted'}`}
          >
            회원가입
          </button>
        </div>

        <form className="mt-5 space-y-4" onSubmit={(event) => void submit(event)}>
          <div>
            <label htmlFor="full-auth-identifier" className="text-[12px] font-medium text-muted">
              {signingUp ? '이메일' : '이메일 또는 아이디'}
            </label>
            <input
              id="full-auth-identifier"
              type={signingUp ? 'email' : 'text'}
              autoComplete={signingUp ? 'email' : 'username'}
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              required
              maxLength={254}
              className="mt-1.5 w-full rounded-control border border-line bg-subtle px-4 py-2.5 text-[15px] text-ink focus:border-navy focus:bg-panel"
            />
          </div>
          <div>
            <label htmlFor="full-auth-password" className="text-[12px] font-medium text-muted">
              비밀번호
            </label>
            <input
              id="full-auth-password"
              type="password"
              autoComplete={signingUp ? 'new-password' : 'current-password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={signingUp ? 15 : 1}
              maxLength={64}
              className="mt-1.5 w-full rounded-control border border-line bg-subtle px-4 py-2.5 text-[15px] text-ink focus:border-navy focus:bg-panel"
            />
            {signingUp ? <p className="mt-1 text-[11px] leading-5 text-faint">15~64자</p> : null}
          </div>

          {error ? (
            <p role="alert" className="rounded-tile bg-block/[0.06] px-4 py-2.5 text-[13px] leading-6 text-block">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={pending}
            className="tap w-full rounded-control bg-brand px-4 py-3 text-[15px] font-semibold text-on-brand hover:opacity-90 disabled:bg-line disabled:text-faint"
          >
            {pending ? '처리 중' : signingUp ? '가입하기' : '로그인'}
          </button>
        </form>

        {providers.length > 0 ? (
        <>
        <div className="my-5 flex items-center gap-3 text-[11px] text-faint" aria-hidden="true">
          <span className="h-px flex-1 bg-line" />
          <span>또는</span>
          <span className="h-px flex-1 bg-line" />
        </div>

        <div className="grid gap-3" role="group" aria-label="소셜 로그인">
          {providers.includes('google') ? (
          <a
            href="/api/v1/auth/oidc/start/google"
            data-provider="google"
            aria-label="Google로 로그인"
            className="tap provider-signin-button google-signin-button flex items-center justify-center rounded-full border px-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            style={{ color: 'var(--google-signin-text)' }}
          >
            <span aria-hidden="true" className="absolute left-3 top-1/2 grid h-6 w-6 shrink-0 -translate-y-1/2 place-items-center rounded-full bg-white">
              <Image src="/auth/google-g-logo.png" alt="" width={18} height={18} className="h-[18px] w-[18px]" />
            </span>
            <span className="w-full text-center">Google로 계속</span>
          </a>
          ) : null}
          {providers.includes('kakao') ? (
          <a
            href="/api/v1/auth/oidc/start/kakao"
            data-provider="kakao"
            aria-label="카카오로 로그인"
            className="tap provider-signin-button kakao-signin-button flex items-center justify-center gap-[10px] rounded-xl bg-[#FEE500] px-3 text-[14px] font-medium leading-[18px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            style={{ color: 'rgba(0, 0, 0, 0.85)' }}
          >
            <Image src="/auth/kakao-talk-symbol.svg" alt="" width={32} height={32} className="h-8 w-8 shrink-0" />
            <span>카카오 로그인</span>
          </a>
          ) : null}
        </div>
        <p className="mt-5 text-center text-[11px] leading-5 text-muted">
          연결하지 않은 Google·카카오 계정은 새 계정으로 만들어집니다. 기존 계정에 연결하려면 로그인 후 설정에서 연결하세요.
        </p>
        </>
        ) : null}
      </section>
    </div>
  );
}
