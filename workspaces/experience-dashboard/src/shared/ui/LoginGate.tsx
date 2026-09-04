'use client';

import { useState, type ReactNode } from 'react';
import { api } from '@/shared/api/endpoints';
import { apiMode } from '@/shared/api/client';
import { session, useSession } from '@/shared/api/session';
import { toErrorState } from '@/shared/lib/useResource';

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
    <div className="mx-auto max-w-[420px] py-12 sm:py-20">
      <div className="rounded-panel bg-panel px-7 py-8 shadow-card">
        <span
          aria-hidden
          className="grid h-11 w-11 place-items-center rounded-tile bg-brand text-[14px] font-semibold text-on-brand"
        >
          AI
        </span>
        <h1 className="mt-5 text-[24px] font-semibold tracking-tight text-ink">로그인</h1>
        <p className="mt-2 text-[14px] leading-6 text-muted">
          서버에 연결하려면 로그인이 필요합니다. 비밀번호는 저장하지 않으며, 로그인 상태는 이 탭에서만
          유지됩니다. 탭을 닫으면 자동으로 로그아웃됩니다.
        </p>

        <div className="mt-7 space-y-4">
          <div>
            <label htmlFor="username" className="text-[12px] font-medium text-muted">
              아이디
            </label>
            <input
              id="username"
              value={username}
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
              className="mt-1.5 w-full rounded-tile border border-line bg-subtle px-4 py-2.5 text-[15px] text-ink focus:border-navy focus:bg-panel"
            />
          </div>
          <div>
            <label htmlFor="password" className="text-[12px] font-medium text-muted">
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
              className="mt-1.5 w-full rounded-tile border border-line bg-subtle px-4 py-2.5 text-[15px] text-ink focus:border-navy focus:bg-panel"
            />
          </div>

          {error ? (
            <p className="rounded-tile bg-block/[0.06] px-4 py-2.5 text-[13px] leading-6 text-block">
              {error}
            </p>
          ) : null}

          <button
            type="button"
            onClick={() => void submit()}
            disabled={pending}
            className="w-full rounded-tile bg-brand px-4 py-3 text-[15px] font-semibold text-on-brand hover:opacity-90 disabled:bg-line disabled:text-faint"
          >
            {pending ? '연결 중' : '로그인'}
          </button>
        </div>
      </div>
    </div>
  );
}
