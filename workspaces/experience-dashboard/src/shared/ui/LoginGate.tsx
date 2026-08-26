'use client';

import { useState, type ReactNode } from 'react';
import { api } from '@/shared/api/endpoints';
import { apiMode, baseUrl } from '@/shared/api/client';
import { session, useSession } from '@/shared/api/session';
import { toErrorState } from '@/shared/lib/useResource';

/**
 * 모든 데이터 endpoint가 JWT를 요구하므로 로그인 전에는 화면을 열지 않는다.
 * 토큰은 메모리에만 두므로 새로고침하면 다시 로그인해야 한다. 이것이 의도된 동작이다.
 */
export function LoginGate({ children }: { children: ReactNode }) {
  const { authenticated } = useSession();

  // mock 모드는 서버가 없으므로 로그인 화면을 건너뛴다.
  if (apiMode() === 'mock' || authenticated) return <>{children}</>;

  return <LoginForm />;
}

function LoginForm() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (pending || username.length === 0 || password.length === 0) return;
    setPending(true);
    setError(null);
    try {
      const { data } = await api.login(username, password);
      session.set(data.accessToken, data.expiresAt, data.user);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '로그인하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mx-auto max-w-md py-16">
      <p className="font-mono text-eyebrow uppercase text-navy">Sign in</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">로그인</h1>
      <p className="mt-2 text-sm leading-6 text-muted">
        서버에 연결하려면 로그인이 필요합니다. 비밀번호는 저장하지 않으며, 토큰은 이 탭의 메모리에만
        보관합니다. 새로고침하면 다시 로그인해야 합니다.
      </p>

      <div className="mt-8 space-y-4 border border-line bg-panel px-5 py-5">
        <div>
          <label htmlFor="username" className="font-mono text-eyebrow uppercase text-faint">
            아이디
          </label>
          <input
            id="username"
            value={username}
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            className="mt-2 w-full border border-line px-3 py-2 text-[14px] text-ink"
          />
        </div>
        <div>
          <label htmlFor="password" className="font-mono text-eyebrow uppercase text-faint">
            비밀번호
          </label>
          <input
            id="password"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit();
            }}
            className="mt-2 w-full border border-line px-3 py-2 text-[14px] text-ink"
          />
        </div>

        {error ? (
          <p className="border-l-2 border-block bg-block/5 px-3 py-2 text-[13px] leading-6 text-ink">
            {error}
          </p>
        ) : null}

        <button
          type="button"
          onClick={() => void submit()}
          disabled={pending}
          className="w-full border border-navy bg-navy px-4 py-2 text-[14px] font-medium text-white disabled:border-line disabled:bg-line disabled:text-faint"
        >
          {pending ? '연결 중' : '로그인'}
        </button>

        <p className="font-mono text-[11px] text-faint">서버 {baseUrl() || 'same-origin /api'}</p>
      </div>
    </div>
  );
}
