'use client';

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { apiFetch } from '@/shared/api/client';
import { useSession } from '@/shared/api/session';
import { toErrorState } from '@/shared/lib/useResource';

type AdminUser = {
  userId: string;
  username: string;
  role: 'USER' | 'ADMIN';
  status: 'ACTIVE' | 'LOCKED' | 'DISABLED';
  createdAt: string | null;
  email: string | null;
  providers: string[];
  brokerState: string | null;
  automationState: string | null;
};
type UserPage = { items: AdminUser[]; total: number };
type AutomationRow = {
  userId: string;
  username: string;
  controlState: string;
  accountId: string;
  controlUpdatedAt: string | null;
  todayClaimState: string | null;
  todayRunId: string | null;
};
type Limits = {
  signupCap: number | null;
  automationActiveCap: number;
  updatedBy: string | null;
  updatedAt: string | null;
  userCount: number;
  activeUserCount: number;
  armedCount: number;
};

const PAGE_SIZE = 50;
const panel = 'rounded-panel border border-line bg-panel p-5 shadow-card sm:p-7';
const cell = 'px-3 py-2.5 text-[13px] text-ink';
const head = 'px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wide text-faint';

function message(cause: unknown, fallback: string): string {
  const state = toErrorState<never>(cause);
  return state.kind === 'error' ? state.message : fallback;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
}

export function AdminConsole({ readOnly = false }: { readOnly?: boolean } = {}) {
  const { user } = useSession();
  if (!readOnly && user?.role !== 'ADMIN') {
    return <p className="text-[14px] text-muted">관리자만 볼 수 있는 화면입니다.</p>;
  }
  return (
    <div className="space-y-8">
      <LimitsPanel readOnly={readOnly} />
      <AiReviewPanel readOnly={readOnly} />
      <UsersPanel selfUserId={user?.userId ?? 'visitor-session'} readOnly={readOnly} />
      <AutomationPanel />
    </div>
  );
}

type AdminAiUsageRow = {
  userId: string;
  username: string;
  email: string | null;
  hasOwnKey: boolean;
  aiJudgementEnabled: boolean;
  ownToday: number;
  sharedToday: number;
  ownMonth: number;
  sharedMonth: number;
};
type AdminAiReview = {
  operator: { configured: boolean; projectId: string | null; modelId: string | null; reachable: boolean };
  deploymentAllowsShared: boolean;
  sharedEnabled: boolean;
  sharedEffective: boolean;
  switchUpdatedBy: string | null;
  switchUpdatedAt: string | null;
  users: AdminAiUsageRow[];
};

/**
 * 공용 Vertex 와 사용자별 AI 검토 사용량. 관리자는 사용자가 자기 키를 등록했는지만 보고, 키 값이나 끝자리는
 * 보지 못한다. "공용 Vertex 사용 허용"을 끄면 자기 키가 없는 사용자는 AI 검토를 켠 채 자동매매를 시작할 수
 * 없다(이미 켜진 실행은 다음 AI 호출부터 멈춘다).
 */
function AiReviewPanel({ readOnly = false }: { readOnly?: boolean }) {
  const [review, setReview] = useState<AdminAiReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await apiFetch<AdminAiReview>('/api/v1/admin/ai');
      setReview(data);
    } catch (cause) {
      setError(message(cause, 'AI 검토 설정을 불러오지 못했습니다.'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(enabled: boolean) {
    if (
      !enabled &&
      !window.confirm('공용 Vertex 사용을 끄면 자기 키가 없는 사용자는 AI 검토를 켠 채 자동매매를 시작할 수 없습니다. 끌까요?')
    ) {
      return;
    }
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const { data } = await apiFetch<AdminAiReview>('/api/v1/admin/ai/operator-fallback', {
        method: 'PUT',
        body: { enabled },
      });
      setReview(data);
      setNotice(enabled ? '공용 Vertex 사용을 허용했습니다.' : '공용 Vertex 사용을 껐습니다. 자기 키가 있는 사용자만 AI 검토를 씁니다.');
    } catch (cause) {
      setError(message(cause, '공용 Vertex 설정을 바꾸지 못했습니다.'));
    } finally {
      setPending(false);
    }
  }

  const operator = review?.operator;
  return (
    <section aria-labelledby="admin-ai" className={panel}>
      <h2 id="admin-ai" className="text-[18px] font-semibold text-ink">AI 검토 (Vertex)</h2>
      {review && operator ? (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
            <Stat label="공용 Vertex 구성" value={operator.configured ? '구성됨' : '없음'} />
            <Stat label="연결" value={operator.reachable ? '정상' : '실패'} />
            <Stat label="프로젝트" value={operator.projectId ?? '—'} />
            <Stat label="모델" value={operator.modelId ?? '—'} />
          </dl>
          {readOnly ? null : <label className="mt-5 flex items-center gap-2 text-[14px] text-ink">
            <input
              type="checkbox"
              data-testid="admin-shared-vertex-toggle"
              checked={review.sharedEnabled}
              disabled={pending || !review.deploymentAllowsShared}
              onChange={(event) => void toggle(event.target.checked)}
            />
            공용 Vertex 사용 허용 (자기 키가 없는 사용자의 AI 검토)
          </label>}
          <p className="mt-2 text-[12px] leading-5 text-muted">
            {review.deploymentAllowsShared
              ? review.sharedEffective
                ? '지금은 자기 키가 없는 사용자도 공용 Vertex로 AI 검토를 받습니다.'
                : '지금은 자기 키가 있는 사용자만 AI 검토를 받습니다.'
              : '이 배포 설정이 공용 Vertex를 막고 있어 스위치를 켤 수 없습니다.'}
            {review.switchUpdatedAt ? ` · 마지막 변경 ${formatDate(review.switchUpdatedAt)}` : ''}
          </p>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr>
                  <th className={head}>사용자</th>
                  <th className={head}>자기 키</th>
                  <th className={head}>AI 검토</th>
                  <th className={head}>오늘 (자기/공용)</th>
                  <th className={head}>이번 달 (자기/공용)</th>
                </tr>
              </thead>
              <tbody>
                {review.users.map((row) => (
                  <tr key={row.userId} className="border-t border-line" data-testid="admin-ai-usage-row">
                    <td className={cell}>
                      {row.username}
                      <span className="block text-[11px] text-faint">{row.email ?? row.userId}</span>
                    </td>
                    <td className={cell}>{row.hasOwnKey ? '등록됨' : '없음'}</td>
                    <td className={cell}>{row.aiJudgementEnabled ? '켜짐' : '꺼짐'}</td>
                    <td className={cell}>{`${row.ownToday} / ${row.sharedToday}`}</td>
                    <td className={cell}>{`${row.ownMonth} / ${row.sharedMonth}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <Feedback error={error} notice={notice} />
    </section>
  );
}

function LimitsPanel({ readOnly = false }: { readOnly?: boolean }) {
  const [limits, setLimits] = useState<Limits | null>(null);
  const [signupCap, setSignupCap] = useState('');
  const [automationCap, setAutomationCap] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await apiFetch<Limits>('/api/v1/admin/limits');
      setLimits(data);
      setSignupCap(data.signupCap === null ? '' : String(data.signupCap));
      setAutomationCap(String(data.automationActiveCap));
    } catch (cause) {
      setError(message(cause, '상한을 불러오지 못했습니다.'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const body = {
        signupCap: signupCap.trim() === '' ? null : Number(signupCap),
        automationActiveCap: Number(automationCap),
      };
      const { data } = await apiFetch<Limits>('/api/v1/admin/limits', { method: 'PUT', body });
      setLimits(data);
      setNotice('상한을 저장했습니다. 이미 가입했거나 자동운용 중인 사용자는 영향을 받지 않습니다.');
    } catch (cause) {
      setError(message(cause, '상한을 저장하지 못했습니다.'));
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="admin-limits" className={panel}>
      <h2 id="admin-limits" className="text-[18px] font-semibold text-ink">서비스 상한</h2>
      {limits ? (
        <dl className="mt-4 grid grid-cols-3 gap-3 text-center">
          <Stat label="전체 계정" value={limits.userCount} />
          <Stat label="사용 중 계정" value={limits.activeUserCount} />
          <Stat label="자동운용 중" value={`${limits.armedCount} / ${limits.automationActiveCap}`} />
        </dl>
      ) : null}
      {readOnly ? <p className="mt-5 text-[12px] leading-5 text-muted">서비스 상한은 배포 환경에서 관리합니다.</p> : <form onSubmit={(event) => void save(event)} className="mt-5 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="text-[12px] font-medium text-muted">
          가입자 상한 (비우면 제한 없음)
          <input
            type="number"
            min={1}
            max={1000000}
            value={signupCap}
            onChange={(event) => setSignupCap(event.target.value)}
            className="mt-1.5 w-full rounded-control border border-line bg-subtle px-3 py-2.5 text-[14px] text-ink"
          />
        </label>
        <label className="text-[12px] font-medium text-muted">
          동시 자동운용 상한
          <input
            type="number"
            min={1}
            max={1000}
            required
            value={automationCap}
            onChange={(event) => setAutomationCap(event.target.value)}
            className="mt-1.5 w-full rounded-control border border-line bg-subtle px-3 py-2.5 text-[14px] text-ink"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="tap rounded-control bg-brand px-4 py-2.5 text-[13px] font-semibold text-on-brand disabled:opacity-50"
        >
          {pending ? '저장 중' : '저장'}
        </button>
      </form>}
      <p className="mt-3 text-[12px] leading-5 text-muted">
        상한에 닿으면 새 가입과 새 자동운용 시작만 막습니다. 이미 가입했거나 자동운용 중인 사용자는 멈추지 않습니다.
      </p>
      {limits?.updatedAt ? (
        <p className="mt-1 text-[11px] text-faint">마지막 변경 {formatDate(limits.updatedAt)}</p>
      ) : null}
      <Feedback error={error} notice={notice} />
    </section>
  );
}

function UsersPanel({ selfUserId, readOnly = false }: { selfUserId: string; readOnly?: boolean }) {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState<UserPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ search: query, page: String(page), size: String(PAGE_SIZE) });
      const { data: next } = await apiFetch<UserPage>(`/api/v1/admin/users?${params.toString()}`);
      setData(next);
    } catch (cause) {
      setError(message(cause, '계정 목록을 불러오지 못했습니다.'));
    }
  }, [page, query]);

  useEffect(() => {
    void load();
  }, [load]);

  async function change(target: AdminUser, role: AdminUser['role'], status: 'ACTIVE' | 'DISABLED') {
    const label = status === 'DISABLED' ? '정지' : role === 'ADMIN' ? '관리자로 지정' : '일반 사용자로 변경';
    if (!window.confirm(`${target.email ?? target.username} 계정을 ${label}할까요? 기존 로그인은 모두 끊깁니다.`)) return;
    setPendingUser(target.userId);
    setError(null);
    setNotice(null);
    try {
      await apiFetch(`/api/v1/admin/users/${encodeURIComponent(target.userId)}/access`, {
        method: 'PUT',
        body: { role, status },
      });
      setNotice('계정 권한을 변경했습니다.');
      await load();
    } catch (cause) {
      setError(message(cause, '계정 권한을 바꾸지 못했습니다.'));
    } finally {
      setPendingUser(null);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section aria-labelledby="admin-users" className={panel}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="admin-users" className="text-[18px] font-semibold text-ink">
          가입자 {data ? <span className="text-[13px] font-normal text-muted">{data.total}명</span> : null}
        </h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setPage(0);
            setQuery(search.trim());
          }}
          className="flex gap-2"
        >
          <input
            type="search"
            placeholder="이메일·아이디 검색"
            value={search}
            maxLength={254}
            onChange={(event) => setSearch(event.target.value)}
            className="w-56 rounded-control border border-line bg-subtle px-3 py-2 text-[13px] text-ink"
          />
          <button type="submit" className="tap rounded-control border border-line px-3 py-2 text-[12px] font-medium text-ink">
            검색
          </button>
        </form>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <th className={head}>계정</th>
              <th className={head}>로그인 방법</th>
              <th className={head}>역할</th>
              <th className={head}>상태</th>
              <th className={head}>KIS</th>
              <th className={head}>자동운용</th>
              <th className={head}>가입</th>
              <th className={head}>관리</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items ?? []).map((row) => {
              const self = row.userId === selfUserId;
              const busy = pendingUser === row.userId;
              return (
                <tr key={row.userId} className="border-b border-line/60 align-top">
                  <td className={cell}>
                    <p className="font-medium">{row.email ?? row.username}</p>
                    <p className="text-[11px] text-faint">{row.userId}</p>
                  </td>
                  <td className={cell}>
                    {[row.email ? '이메일' : null, row.userId === 'usr_demo_user' ? '아이디' : null, ...row.providers.map((p) => (p === 'google' ? 'Google' : '카카오'))]
                      .filter(Boolean)
                      .join(', ') || '—'}
                  </td>
                  <td className={cell}>{row.role === 'ADMIN' ? '관리자' : '사용자'}</td>
                  <td className={cell}>{row.status === 'ACTIVE' ? '사용 중' : row.status === 'DISABLED' ? '정지' : '잠김'}</td>
                  <td className={cell}>{row.brokerState ?? '미연결'}</td>
                  <td className={cell}>{row.automationState ?? '—'}</td>
                  <td className={cell}>{formatDate(row.createdAt)}</td>
                  <td className={cell}>
                    {readOnly ? (
                      <span className="text-[11px] text-faint">읽기 전용</span>
                    ) : self ? (
                      <span className="text-[11px] text-faint">본인</span>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {row.status === 'ACTIVE' ? (
                          <>
                            <ActionButton disabled={busy} onClick={() => void change(row, row.role === 'ADMIN' ? 'USER' : 'ADMIN', 'ACTIVE')}>
                              {row.role === 'ADMIN' ? '관리자 해제' : '관리자 지정'}
                            </ActionButton>
                            <ActionButton disabled={busy} onClick={() => void change(row, row.role, 'DISABLED')}>
                              정지
                            </ActionButton>
                          </>
                        ) : row.userId === 'usr_demo_admin' ? (
                          <span className="text-[11px] text-faint">은퇴 계정</span>
                        ) : (
                          <ActionButton disabled={busy} onClick={() => void change(row, row.role, 'ACTIVE')}>
                            정지 해제
                          </ActionButton>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2 text-[12px] text-muted">
        <ActionButton disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
          이전
        </ActionButton>
        <span>
          {page + 1} / {totalPages}
        </span>
        <ActionButton disabled={page + 1 >= totalPages} onClick={() => setPage((value) => value + 1)}>
          다음
        </ActionButton>
      </div>
      <Feedback error={error} notice={notice} />
    </section>
  );
}

function AutomationPanel() {
  const [rows, setRows] = useState<AutomationRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void apiFetch<AutomationRow[]>('/api/v1/admin/automation')
      .then(({ data }) => {
        if (active) setRows(data);
      })
      .catch((cause: unknown) => {
        if (active) setError(message(cause, '자동운용 현황을 불러오지 못했습니다.'));
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section aria-labelledby="admin-automation" className={panel}>
      <h2 id="admin-automation" className="text-[18px] font-semibold text-ink">
        자동운용 현황 {rows ? <span className="text-[13px] font-normal text-muted">{rows.length}명</span> : null}
      </h2>
      <p className="mt-1 text-[12px] text-muted">무장했거나 오늘 실행 중인 계정입니다. 계정을 정지하면 그 계정의 자동운용도 더 이상 실행되지 않습니다.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse">
          <thead>
            <tr className="border-b border-line">
              <th className={head}>계정</th>
              <th className={head}>상태</th>
              <th className={head}>계좌</th>
              <th className={head}>오늘 실행</th>
              <th className={head}>변경 시각</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((row) => (
              <tr key={row.userId} className="border-b border-line/60">
                <td className={cell}>
                  <p className="font-medium">{row.username}</p>
                  <p className="text-[11px] text-faint">{row.userId}</p>
                </td>
                <td className={cell}>{row.controlState}</td>
                <td className={cell}>{row.accountId}</td>
                <td className={cell}>{row.todayClaimState ?? '—'}</td>
                <td className={cell}>{formatDate(row.controlUpdatedAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Feedback error={error} notice={null} />
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-tile border border-line px-3 py-3">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="mt-1 text-[18px] font-semibold text-ink">{value}</dd>
    </div>
  );
}

function ActionButton({
  children,
  disabled,
  onClick,
}: {
  children: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="tap rounded-control border border-line px-2.5 py-1.5 text-[12px] font-medium text-ink hover:bg-subtle disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function Feedback({ error, notice }: { error: string | null; notice: string | null }) {
  return (
    <>
      {error ? <p role="alert" className="mt-4 rounded-tile bg-block/[0.06] px-4 py-2.5 text-[13px] leading-6 text-block">{error}</p> : null}
      {notice ? <p role="status" className="mt-4 rounded-tile bg-brand/[0.06] px-4 py-2.5 text-[13px] leading-6 text-brand">{notice}</p> : null}
    </>
  );
}
