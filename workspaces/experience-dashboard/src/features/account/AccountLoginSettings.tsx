'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { apiFetch } from '@/shared/api/client';
import { session } from '@/shared/api/session';
import type { LoginResponse } from '@/shared/api/wire';
import { toErrorState } from '@/shared/lib/useResource';

type Provider = 'google' | 'kakao';
type AuthMethod = { provider: 'password' | 'demo-password' | Provider; email: string | null; linkedAt: string };
type LinkStart = { authorizationPath: string };

const PROVIDERS: readonly Provider[] = ['google', 'kakao'];

export function AccountLoginSettings() {
  const [methods, setMethods] = useState<AuthMethod[]>([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [available, setAvailable] = useState<string[]>([]);

  async function refreshMethods() {
    const { data } = await apiFetch<AuthMethod[]>('/api/v1/auth/identities');
    setMethods(data);
  }

  useEffect(() => {
    let active = true;
    void apiFetch<{ providers: string[] }>('/api/v1/auth/options', { anonymous: true })
      .then(({ data }) => { if (active) setAvailable(data.providers); })
      .catch(() => { if (active) setAvailable([]); });
    void apiFetch<AuthMethod[]>('/api/v1/auth/identities')
      .then(({ data }) => { if (active) setMethods(data); })
      .catch((cause: unknown) => {
        const state = toErrorState<never>(cause);
        if (active) setError(state.kind === 'error' ? state.message : '로그인 정보를 불러오지 못했습니다.');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const linkedProviders = methods.filter((method) => method.provider === 'google' || method.provider === 'kakao');
  const hasPassword = methods.some((method) => method.provider === 'password' || method.provider === 'demo-password');

  async function startLink(provider: Provider) {
    setPending(provider);
    setError(null);
    try {
      const { data } = await apiFetch<LinkStart>(`/api/v1/auth/identities/${provider}/link/start`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      const expectedPath = `/api/v1/auth/oidc/start/${provider}`;
      if (data.authorizationPath !== expectedPath) throw new Error('연동 주소를 확인할 수 없습니다.');
      window.location.assign(data.authorizationPath);
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '계정을 연결하지 못했습니다.');
      setPending(null);
    }
  }

  async function unlink(provider: Provider) {
    if (!window.confirm(`${provider === 'google' ? 'Google' : '카카오'} 연결을 해제할까요?`)) return;
    setPending(provider);
    setError(null);
    setNotice(null);
    try {
      await apiFetch(`/api/v1/auth/identities/${provider}`, { method: 'DELETE' });
      await refreshMethods();
      setNotice('연결을 해제했습니다.');
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '연결을 해제하지 못했습니다.');
    } finally {
      setPending(null);
    }
  }

  async function addPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending('password');
    setError(null);
    setNotice(null);
    try {
      const { data } = await apiFetch<LoginResponse>('/api/v1/auth/password', {
        method: 'PUT',
        body: { email, password },
      });
      session.set(data.accessToken, data.expiresAt, data.user);
      setPassword('');
      await refreshMethods();
      setNotice('이메일·비밀번호 로그인을 추가했습니다.');
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '비밀번호 로그인을 추가하지 못했습니다.');
    } finally {
      setPending(null);
    }
  }

  return (
    <section aria-labelledby="account-login-settings-title" className="rounded-panel border border-line bg-panel p-5 shadow-card sm:p-7">
      <h2 id="account-login-settings-title" className="text-[18px] font-semibold text-ink">로그인 방법</h2>
      <p className="mt-1 text-[13px] leading-6 text-muted">이 계정에 연결된 로그인 방법을 관리합니다. 같은 이메일이어도 직접 연결하기 전까지 다른 계정입니다.</p>

      {loading ? <p className="mt-5 text-[13px] text-muted">확인 중…</p> : (
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {PROVIDERS.map((provider) => {
            const connected = linkedProviders.some((method) => method.provider === provider);
            const canUnlink = hasPassword || linkedProviders.length > 1;
            return (
              <div key={provider} className="flex min-h-20 items-center justify-between gap-3 rounded-tile border border-line px-4 py-3">
                <div>
                  <p className="text-[14px] font-medium text-ink">{provider === 'google' ? 'Google' : '카카오'}</p>
                  <p className="mt-1 text-[12px] text-muted">
                    {connected ? '연결됨' : available.includes(provider) ? '연결되지 않음' : '서버에 로그인 연동이 설정되지 않음'}
                  </p>
                </div>
                {connected ? (
                  <button
                    type="button"
                    onClick={() => void unlink(provider)}
                    disabled={pending !== null || !canUnlink}
                    title={!canUnlink ? '다른 로그인 방법을 먼저 추가하세요.' : undefined}
                    className="tap rounded-control border border-line px-3 py-2 text-[12px] font-medium text-muted hover:text-ink disabled:opacity-50"
                  >
                    {pending === provider ? '처리 중' : '연결 해제'}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void startLink(provider)}
                    disabled={pending !== null || !available.includes(provider)}
                    className="tap rounded-control bg-brand px-3 py-2 text-[12px] font-semibold text-on-brand disabled:opacity-50"
                  >
                    {pending === provider ? '이동 중' : '연결하기'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {!hasPassword ? (
        <form onSubmit={(event) => void addPassword(event)} className="mt-5 border-t border-line pt-5">
          <h3 className="text-[14px] font-semibold text-ink">이메일·비밀번호 로그인 추가</h3>
          <p className="mt-1 text-[12px] leading-5 text-muted">비밀번호는 계정에 해시 형태로 저장합니다. 이메일 인증이나 비밀번호 재설정 메일은 제공하지 않습니다.</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-[12px] font-medium text-muted">
              이메일
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                maxLength={254}
                className="mt-1.5 w-full rounded-control border border-line bg-subtle px-3 py-2.5 text-[14px] text-ink"
              />
            </label>
            <label className="text-[12px] font-medium text-muted">
              새 비밀번호
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={15}
                maxLength={64}
                className="mt-1.5 w-full rounded-control border border-line bg-subtle px-3 py-2.5 text-[14px] text-ink"
              />
            </label>
          </div>
          <button type="submit" disabled={pending !== null} className="tap mt-3 rounded-control border border-line px-4 py-2.5 text-[13px] font-medium text-ink disabled:opacity-50">
            {pending === 'password' ? '저장 중' : '비밀번호 로그인 추가'}
          </button>
        </form>
      ) : null}

      {error ? <p role="alert" className="mt-4 rounded-tile bg-block/[0.06] px-4 py-2.5 text-[13px] leading-6 text-block">{error}</p> : null}
      {notice ? <p role="status" className="mt-4 rounded-tile bg-brand/[0.06] px-4 py-2.5 text-[13px] leading-6 text-brand">{notice}</p> : null}
    </section>
  );
}
