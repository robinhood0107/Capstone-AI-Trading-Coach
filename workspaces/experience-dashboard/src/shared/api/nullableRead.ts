import { ApiFailure, type ApiResult } from './envelope';

/**
 * 값이 없으면 `data: null` 로 성공하는 읽기(예: v4 자본 정책·상태)를 감싼다.
 *
 * 공통 unwrap 은 null 을 NOT_FOUND 로 던진다. 그 경우만 "읽었고 비어 있음"(`{ data: null }`)으로
 * 되돌리고, 다른 실패는 "읽지 못함"(`null`)으로 둔다. 둘을 섞으면 새 계정이 정책을 처음 저장할
 * 수 없고, 서버 장애가 "아직 저장 안 함"으로 보인다.
 */
export async function nullableRead<T>(
  request: Promise<ApiResult<T | null>>,
): Promise<{ data: T | null } | null> {
  try {
    return await request;
  } catch (cause) {
    if (cause instanceof ApiFailure && cause.code === 'NOT_FOUND') return { data: null };
    return null;
  }
}
