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

export function credentialChangeBlocker(cause: unknown): CredentialBlocker | null {
  if (!(cause instanceof ApiFailure) || cause.code !== 'CONFLICT') return null;
  const reason = cause.details?.reason;
  return typeof reason === 'string' && Object.hasOwn(CREDENTIAL_BLOCKER_MESSAGE, reason)
    ? (reason as CredentialBlocker)
    : null;
}
