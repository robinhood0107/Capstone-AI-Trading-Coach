import { ApiFailure, isEnvelope, type ApiEnvelope, type ApiResult } from './envelope';
import { session } from './session';
import { mockTransport, mockBareTransport } from '@/shared/mock/transport';

export type ApiMode = 'mock' | 'live';

export function apiMode(): ApiMode {
  return process.env.NEXT_PUBLIC_API_MODE === 'mock' ? 'mock' : 'live';
}

export function baseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
}

function randomToken(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * X-Request-Id는 공통 HTTP 경계뿐 아니라 RAG의 scope/history/provider 원장에서도 같은 값이
 * 이어진다. 모든 하위 경계가 공유하는 canonical ``req_`` 형식의 교집합만 생성한다.
 */
export function newRequestId(): string {
  return `req_${randomToken(16)}`;
}

/** X-Idempotency-Key: 16~128자, [A-Za-z0-9._~-] 만 허용. */
export function newIdempotencyKey(purpose: string): string {
  const scope = purpose.replace(/[^A-Za-z0-9._~-]/g, '-').slice(0, 24);
  return `${scope}.${randomToken(16)}`;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** 금융 부작용이 있는 write와 RAG ask에만 부여한다. */
  idempotencyKey?: string;
  /** 로그인처럼 토큰 없이 호출하는 경우 true. */
  anonymous?: boolean;
  signal?: AbortSignal;
}

/**
 * 중요: 서버 CORS 설정이 허용하는 요청 헤더는 아래 네 개뿐이다.
 *   Authorization, Content-Type, X-Request-Id, X-Idempotency-Key
 * 그 밖의 커스텀 헤더를 추가하면 preflight 단계에서 요청이 막힌다.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const requestId = newRequestId();
  const method = options.method ?? 'GET';

  if (apiMode() === 'mock') {
    return unwrap(await mockTransport<T>(path, method, options.body, requestId));
  }

  const headers: Record<string, string> = {
    'X-Request-Id': requestId,
    Accept: 'application/json',
  };
  if (!options.anonymous) {
    const token = session.token();
    if (!token) {
      throw new ApiFailure({ code: 'UNAUTHORIZED', message: 'No session token.' }, requestId);
    }
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.idempotencyKey) headers['X-Idempotency-Key'] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
      credentials: 'omit',
      signal: options.signal ?? null,
    });
  } catch {
    throw new ApiFailure(
      {
        code: 'PYTHON_SERVICE_UNAVAILABLE',
        message: '서버에 연결하지 못했습니다. API 주소와 서버 실행 상태를 확인하세요.',
      },
      requestId,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiFailure(
      { code: 'INTERNAL_ERROR', message: '서버 응답을 읽지 못했습니다.' },
      requestId,
    );
  }

  if (!isEnvelope(payload)) {
    throw new ApiFailure(
      { code: 'VALIDATION_ERROR', message: '서버 응답 형식이 계약과 다릅니다.' },
      requestId,
    );
  }

  return unwrap(payload as ApiEnvelope<T>);
}

/** Calls v2 endpoints that return bare DTOs instead of the shared API envelope. */
export async function apiFetchBare<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const requestId = newRequestId();
  const method = options.method ?? 'GET';

  if (apiMode() === 'mock') {
    return mockBareTransport<T>(path, method, options.body, requestId);
  }

  const headers: Record<string, string> = {
    'X-Request-Id': requestId,
    Accept: 'application/json',
  };
  const token = session.token();
  if (!token) {
    throw new ApiFailure({ code: 'UNAUTHORIZED', message: 'No session token.' }, requestId);
  }
  headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: 'no-store',
      credentials: 'omit',
      signal: options.signal ?? null,
    });
  } catch {
    throw new ApiFailure(
      {
        code: 'PYTHON_SERVICE_UNAVAILABLE',
        message: '서버에 연결하지 못했습니다. API 주소와 서버 실행 상태를 확인하세요.',
      },
      requestId,
    );
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiFailure(
      { code: 'INTERNAL_ERROR', message: '서버 응답을 읽지 못했습니다.' },
      requestId,
    );
  }

  if (!response.ok) {
    const error = payload as { code?: unknown; message?: unknown; requestId?: unknown };
    throw new ApiFailure(
      {
        code: typeof error.code === 'string' ? error.code : 'INTERNAL_ERROR',
        message: typeof error.message === 'string' ? error.message : '알 수 없는 오류입니다.',
      },
      typeof error.requestId === 'string' ? error.requestId : requestId,
    );
  }
  return payload as T;
}

function unwrap<T>(envelope: ApiEnvelope<T>): ApiResult<T> {
  if (!envelope.success || envelope.error) {
    throw new ApiFailure(
      envelope.error ?? { code: 'INTERNAL_ERROR', message: '알 수 없는 오류입니다.' },
      envelope.requestId,
    );
  }
  if (envelope.data === null || envelope.data === undefined) {
    throw new ApiFailure({ code: 'NOT_FOUND', message: '조회 결과가 없습니다.' }, envelope.requestId);
  }
  return { data: envelope.data, warnings: envelope.warnings ?? [], requestId: envelope.requestId };
}
