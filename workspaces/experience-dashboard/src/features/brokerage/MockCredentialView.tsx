'use client';

import { useId, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import type { MockCredentialReadResponse } from '@/shared/api/wire';
import { AsyncBoundary } from '@/shared/ui/AsyncBoundary';
import { Panel } from '@/shared/ui/Panel';
import { ready } from '@/shared/lib/viewState';
import { toErrorState, useResource } from '@/shared/lib/useResource';

const INPUT =
  'mt-1.5 w-full rounded-control border border-line bg-panel px-4 py-2.5 text-[14px] text-ink ' +
  'focus:border-navy focus:outline-none';

async function loadSummary() {
  const { data } = await api.mockCredentialSummary();
  return ready<MockCredentialReadResponse>(data, null);
}

export function MockCredentialView() {
  const resource = useResource(loadSummary, []);
  return (
    <AsyncBoundary state={resource.state} onRetry={resource.reload}>
      {(status) => <MockCredentialForm status={status} reload={resource.reload} />}
    </AsyncBoundary>
  );
}

function MockCredentialForm({
  status,
  reload,
}: {
  status: MockCredentialReadResponse;
  reload: () => void;
}) {
  const id = useId();
  const [appKey, setAppKey] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [accountNo, setAccountNo] = useState('');
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const valid =
    appKey.length >= 8 &&
    appKey.length <= 256 &&
    /^[A-Za-z0-9._-]+$/.test(appKey) &&
    /^[A-Za-z0-9_-]{4}$/.test(appKey.slice(-4)) &&
    appSecret.length >= 8 &&
    appSecret.length <= 512 &&
    /^[!-~]+$/.test(appSecret) &&
    /^[0-9]{10}$/.test(accountNo);

  async function save() {
    if (!valid || pending) return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      await api.putMockCredential({ appKey, appSecret, accountNo });
      setAppKey('');
      setAppSecret('');
      setAccountNo('');
      setOutcome('암호화해 저장했습니다. 연결 확인과 모의주문 인증은 별도 단계입니다.');
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '저장하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  async function verifyConnection() {
    if (!status.registered || !status.credential || pending || status.credential.state === 'DISCONNECTING') return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      await api.verifyMockCredentialConnection();
      setOutcome('본인 모의계좌의 읽기 연결을 확인했습니다. 자동주문 인증은 아직 별도입니다.');
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '연결을 확인하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  async function certifyMockAccount() {
    if (!status.registered || !status.credential || pending || !status.credential.connected) return;
    const priorFillNeedsReview =
      status.credential.certificationStatus === 'FAILED' &&
      status.credential.certificationFailureCode === 'EXECUTION_FILLED';
    const confirmation = priorFillNeedsReview
      ? '이전 1주 인증 주문이 체결된 것으로 보입니다. KIS 모의계좌에서 해당 주문과 보유수량을 확인·정리한 뒤 진행해 주세요. 새로 1주 테스트 주문을 시작할까요?'
      : 'KIS 모의계좌에서 삼성전자 1주를 현재 하한가로 매수 주문한 뒤 즉시 취소하고 체결·잔고를 확인합니다. 하한가 주문도 체결될 수 있으며, 체결되면 계좌에 1주가 남을 수 있습니다. 진행할까요?';
    if (
      !window.confirm(
        confirmation,
      )
    ) return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      const { data } = await api.certifyMockCredential();
      if (data.status === 'PASS') {
        setOutcome('모의주문·취소·체결·잔고 대사가 확인됐습니다.');
      } else if (data.status === 'RECOVERY_REQUIRED') {
        setError('이전 인증 주문의 상태 확인이 필요합니다. 같은 인증을 다시 확인해 주세요.');
      } else if (data.failureCode === 'TEST_ORDER_RECOVERED') {
        setOutcome('이전 테스트 주문을 취소·대사했습니다. 새 1주 인증을 다시 진행해 주세요.');
      } else {
        setError(`모의주문 인증에 실패했습니다 (${data.failureCode ?? 'PROVIDER_FAILED'}).`);
      }
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '모의주문 인증을 완료하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  async function confirmCertificationRecovery() {
    if (!status.credential || pending || status.credential.certificationStatus !== 'RECOVERY_REQUIRED') return;
    if (
      !window.confirm(
        'KIS 모의계좌에서 인증 주문의 체결·미체결·잔고를 직접 확인하고 정리했습니까? 확인하면 MARS의 복구 잠금만 해제됩니다. 모의주문 인증이나 자동운용을 완료 처리하지는 않습니다.',
      )
    ) return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      await api.acknowledgeMockCredentialCertificationRecovery();
      setOutcome('복구 잠금을 해제했습니다. 계좌를 확인한 뒤 1주 인증을 다시 진행할 수 있습니다.');
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '복구 상태를 변경하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    if (!status.registered || !status.credential || pending) return;
    if (
      status.credential.state !== 'DISCONNECTING' &&
      !window.confirm('모의계좌 연결을 해제할까요? 새 주문은 중단되고 미대사 주문이 있으면 복구용 암호문은 보존됩니다.')
    ) return;
    setPending(true);
    setOutcome(null);
    setError(null);
    try {
      const result = await api.disconnectMockCredential();
      setOutcome(
        result?.state === 'DISCONNECTING'
          ? '새 주문을 중단했습니다. 미대사 주문이 정리된 뒤 해제 완료를 다시 확인해 주세요.'
          : '모의계좌 연결 정보를 삭제했습니다.',
      );
      reload();
    } catch (cause) {
      const state = toErrorState<never>(cause);
      setError(state.kind === 'error' ? state.message : '연결을 해제하지 못했습니다.');
    } finally {
      setPending(false);
    }
  }

  const credential = status.credential;
  return (
    <Panel
      contract="mock-credential-settings"
      title="내 KIS 모의계좌 연결"
      hint="본인이 KIS에서 발급받은 모의투자 정보만 입력합니다. 값은 저장 뒤 다시 표시하지 않습니다."
    >
      <div className="rounded-tile border border-line bg-subtle px-4 py-3 text-[13px] leading-6 text-ink">
        {status.registered && credential ? (
          <>
            <p className="font-medium">저장됨 · App Key 끝 4자리 {credential.appKeyLast4} · 계좌 끝 4자리 {credential.accountNoLast4}</p>
            <p className="text-muted">
              {credential.state === 'DISCONNECTING'
                ? '연결 해제 중 · 새 주문 중단 · 미대사 주문 확인 필요'
              : credential.certified
                ? `모의주문 인증 완료${credential.certificationSessionDate ? ` · ${credential.certificationSessionDate}` : ''}`
                : credential.connected
                  ? credential.certificationStatus === 'RECOVERY_REQUIRED'
                    ? '이전 인증 주문 복구 확인 필요 · 자동주문은 닫혀 있음'
                    : credential.certificationStatus === 'RUNNING'
                      ? '모의주문 인증 중 · 자동주문은 닫혀 있음'
                      : credential.certificationStatus === 'FAILED'
                        ? credential.certificationFailureCode === 'TEST_ORDER_RECOVERED'
                          ? '이전 테스트 주문 정리 완료 · 1주 인증 재시도 필요'
                          : '모의주문 인증 실패 · 자동주문은 닫혀 있음'
                      : '연결 확인됨 · 모의주문 인증 전'
                  : '연결 확인 전 · 자동주문 시작 전'}
            </p>
          </>
        ) : (
          <p>등록된 모의계좌 정보가 없습니다.</p>
        )}
      </div>

      {status.registered && credential && credential.state !== 'DISCONNECTING' ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => void verifyConnection()}
          className="mt-4 rounded-control border border-line px-5 py-2.5 text-[14px] font-semibold text-ink hover:border-navy disabled:opacity-50"
        >
          {pending ? '확인 중…' : credential?.connected ? '읽기 연결 다시 확인' : '읽기 연결 확인'}
        </button>
      ) : null}
      {status.registered && credential?.connected && !credential.certified ? (
        <div className="mt-4">
          <p className="text-[12px] leading-5 text-muted">
            인증은 KRX 거래일 09:10~15:00 KST에만 실행할 수 있습니다. 주문은 005930 1주 하한가 지정가로 고정됩니다.
          </p>
          <button
            type="button"
            disabled={pending}
            onClick={() => void certifyMockAccount()}
            className="mt-2 rounded-control border border-brand px-5 py-2.5 text-[14px] font-semibold text-brand hover:bg-subtle disabled:opacity-50"
          >
            {pending
              ? '모의주문 확인 중…'
              : credential.certificationStatus === 'RECOVERY_REQUIRED'
                ? '이전 인증 주문 복구 확인'
              : credential.certificationStatus === 'RUNNING'
                ? '인증 상태 다시 확인'
                  : '1주 모의주문 인증'}
          </button>
        </div>
      ) : null}
      {status.registered && credential?.certificationStatus === 'RECOVERY_REQUIRED' ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => void confirmCertificationRecovery()}
          className="ml-3 mt-4 rounded-control border border-line px-5 py-2.5 text-[14px] font-semibold text-muted disabled:opacity-50"
        >
          {pending ? '복구 상태 확인 중…' : 'KIS에서 상태 확인 후 복구 잠금 해제'}
        </button>
      ) : null}
      {status.registered && credential ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => void disconnect()}
          className="ml-3 mt-4 rounded-control border border-line px-5 py-2.5 text-[14px] font-semibold text-block hover:border-block disabled:opacity-50"
        >
          {pending ? '처리 중…' : credential.state === 'DISCONNECTING' ? '해제 완료 확인' : '연결 해제'}
        </button>
      ) : null}

      <div className="mt-5 grid gap-4">
        <label className="text-[13px] font-medium text-muted" htmlFor={`${id}-key`}>
          KIS_MOCK App Key
          <input
            id={`${id}-key`}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={appKey}
            maxLength={256}
            onChange={(event) => setAppKey(event.target.value)}
            className={INPUT}
          />
        </label>
        <label className="text-[13px] font-medium text-muted" htmlFor={`${id}-secret`}>
          KIS_MOCK App Secret
          <input
            id={`${id}-secret`}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={appSecret}
            maxLength={512}
            onChange={(event) => setAppSecret(event.target.value)}
            className={INPUT}
          />
        </label>
        <label className="text-[13px] font-medium text-muted" htmlFor={`${id}-account`}>
          모의계좌번호 · 하이픈 없이 숫자 10자리
          <input
            id={`${id}-account`}
            type="password"
            inputMode="numeric"
            autoComplete="off"
            value={accountNo}
            maxLength={10}
            onChange={(event) => setAccountNo(event.target.value)}
            className={INPUT}
          />
        </label>
      </div>
      <button
        type="button"
        disabled={!valid || pending}
        onClick={() => void save()}
        className="mt-5 rounded-control bg-brand px-5 py-2.5 text-[14px] font-semibold text-on-brand disabled:opacity-50"
      >
        {pending ? '저장 중…' : status.registered ? '모의계좌 정보 교체' : '모의계좌 정보 저장'}
      </button>
      {outcome ? <p className="mt-3 text-[13px] text-allow">{outcome}</p> : null}
      {error ? <p className="mt-3 text-[13px] text-block">{error}</p> : null}
    </Panel>
  );
}
