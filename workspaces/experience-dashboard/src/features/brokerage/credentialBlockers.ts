import { ApiFailure } from '@/shared/api/envelope';

/**
 * 서버가 409 의 `details.reason` 으로 알려 주는, 사용자가 스스로 풀 수 있는 저장 차단 사유
 * (`MockCredentialExceptionHandler.CONFLICT_REASONS`). 이유 없이 "다른 변경과 충돌했습니다" 만
 * 보이면 무엇을 해야 할지 알 수 없다.
 */
export const CREDENTIAL_BLOCKER_MESSAGE = {
  AUTOMATION_ARMED: '자동매매가 켜져 있어 계좌 정보를 바꿀 수 없습니다. 자동매매를 먼저 해제하세요.',
  PENDING_RECONCILIATION: '대사가 끝나지 않은 주문이 있어 계좌 정보를 바꿀 수 없습니다. 주문 정리가 끝난 뒤 다시 시도하세요.',
  PENDING_EXECUTION: '실행 중인 자동매매 주문이 있어 계좌 정보를 바꿀 수 없습니다. 주문 정리가 끝난 뒤 다시 시도하세요.',
} as const;

export type CredentialBlocker = keyof typeof CREDENTIAL_BLOCKER_MESSAGE;

/**
 * "연결 확인"은 KIS 잔고조회 한 번이다. 서버가 `details.reason` 으로 알린 실패 이유를 한국어로 바꾼다.
 * 앞의 둘은 사용자가 입력을 고쳐 풀고(409), 뒤의 둘은 KIS 쪽 사정이다(503). 어느 경우든 저장된 키는
 * "저장됨"에 머물고 연결됨으로 넘어가지 않는다.
 */
export const CONNECTION_FAILURE_MESSAGE = {
  APP_KEY_REJECTED:
    'KIS가 앱 키 또는 앱 시크릿을 거부했습니다. 모의투자용으로 발급한 키인지, 앞뒤 공백 없이 붙여 넣었는지 확인하세요.',
  ACCOUNT_REJECTED:
    'KIS가 이 계좌의 잔고조회를 거부했습니다. 계좌번호가 이 앱 키로 신청한 모의투자 계좌(8자리+상품코드 2자리)인지 확인하세요.',
  RATE_LIMITED: 'KIS 호출 한도에 걸렸습니다. 잠시 후 다시 확인하세요.',
  KIS_UNAVAILABLE: 'KIS 모의투자 서버에 연결하지 못했습니다. 잠시 후 다시 확인하세요.',
} as const;

export type ConnectionFailure = keyof typeof CONNECTION_FAILURE_MESSAGE;

export function connectionFailure(cause: unknown): ConnectionFailure | null {
  if (!(cause instanceof ApiFailure) || (cause.code !== 'CONFLICT' && cause.code !== 'BROKERAGE_UNAVAILABLE')) {
    return null;
  }
  const reason = cause.details?.reason;
  return typeof reason === 'string' && Object.hasOwn(CONNECTION_FAILURE_MESSAGE, reason)
    ? (reason as ConnectionFailure)
    : null;
}

export function credentialChangeBlocker(cause: unknown): CredentialBlocker | null {
  if (!(cause instanceof ApiFailure) || cause.code !== 'CONFLICT') return null;
  const reason = cause.details?.reason;
  return typeof reason === 'string' && Object.hasOwn(CREDENTIAL_BLOCKER_MESSAGE, reason)
    ? (reason as CredentialBlocker)
    : null;
}
