import { ApiFailure, isEnvelope, type ApiEnvelope, type ApiResult } from './envelope';
import { session } from './session';

export type ApiMode = 'mock' | 'live';

type MockTransportModule = typeof import('@/shared/mock/transport');
let mockModule: MockTransportModule | null = null;
if (process.env.NODE_ENV !== 'production') {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  mockModule = require('@/shared/mock/transport') as MockTransportModule;
}

export function apiMode(): ApiMode {
  return process.env.NEXT_PUBLIC_API_MODE === 'mock' ? 'mock' : 'live';
}

export function baseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
}

/**
 * 화면별 503 복구를 검증하려면 한 endpoint 의 첫 응답을 실패시켜야 한다. 그 스위치를
 * 프로덕션 번들에 남기지 않는다.
 *
 * mock 모드에서는 언제나 허용한다(합성 데이터 화면이다). live 를 보는 QA 는
 * `NEXT_PUBLIC_QA_FAULT_INJECTION=1` 을 켠 빌드에서만 돈다.
 */
function qaFaultInjectionAllowed(): boolean {
  return apiMode() === 'mock' || process.env.NEXT_PUBLIC_QA_FAULT_INJECTION === '1';
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

/**
 * 응답이 오지 않는 요청의 상한.
 *
 * 이 값이 없으면 서버가 응답을 끊지 않고 붙잡고 있을 때 화면이 영구히 로딩 스켈레톤으로
 * 남고 사용자가 취소할 방법도 없다. 시연 중 API 가 한 번 멈추면 그 화면은 끝이다.
 * 15초는 이 앱의 가장 느린 정상 경로(RAG 생성)보다 짧으므로 그 경로만 별도로 늘린다.
 */
const DEFAULT_TIMEOUT_MS = 15_000;
/** RAG ask 는 Vertex 생성을 기다리므로 실측 15초를 넘긴다. */
const SLOW_PATH_TIMEOUT_MS = 90_000;
const SLOW_PATHS = ['/api/v2/rag/ask'];

function timeoutFor(path: string): number {
  return SLOW_PATHS.some((slow) => path.startsWith(slow)) ? SLOW_PATH_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
}

/**
 * 봉투가 아닌 응답을 HTTP status 로 분류한다.
 *
 * 예전에는 전부 `VALIDATION_ERROR` 였다. 그 코드는 재시도 불가라서, 프록시가 끼워 넣은
 * 502 나 공통 handler 가 잡지 못한 500 이 화면에 "요청 형식이 올바르지 않습니다" 를 띄우고
 * 다시 조회 버튼도 주지 않았다. 원인과 무관한 문구로 막다른 길을 만들지 않는다.
 */
function nonEnvelopeFailure(status: number, requestId: string): ApiFailure {
  if (status >= 500) {
    return new ApiFailure(
      { code: 'INTERNAL_ERROR', message: '서버가 요청을 처리하지 못했습니다.' },
      requestId,
    );
  }
  if (status === 401) {
    return new ApiFailure({ code: 'UNAUTHORIZED', message: '로그인이 필요합니다.' }, requestId);
  }
  if (status === 403) {
    return new ApiFailure({ code: 'FORBIDDEN', message: '권한이 없습니다.' }, requestId);
  }
  if (status === 404) {
    return new ApiFailure({ code: 'NOT_FOUND', message: '해당 자료를 찾을 수 없습니다.' }, requestId);
  }
  if (status === 429) {
    return new ApiFailure({ code: 'RATE_LIMITED', message: '요청이 너무 잦습니다.' }, requestId);
  }
  return new ApiFailure(
    { code: 'RESPONSE_CONTRACT_MISMATCH', message: '서버 응답 형식이 계약과 다릅니다.' },
    requestId,
  );
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** 금융 부작용이 있는 write와 RAG ask에만 부여한다. */
  idempotencyKey?: string;
  /** 로그인처럼 토큰 없이 호출하는 경우 true. */
  anonymous?: boolean;
  /** OAuth link intent is kept in a short-lived same-origin HttpOnly session. */
  credentials?: RequestCredentials;
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

  // 주입은 mock 모드 밖에서도 동작해야 한다. 예전에는 mock 분기 안에만 있어서 실제
  // Spring 을 보는 화면의 503 복구를 한 번도 검증하지 못했다 - 12화면 QA 가 전부
  // 통과해도 그것은 mock 예외 경로의 통과였다.
  mockQaFailure(path, requestId);

  if (apiMode() === 'mock') {
    if (!mockModule) throw new Error('Mock API mode is unavailable in production.');
    return unwrap(await mockModule.mockTransport<T>(path, method, options.body, requestId));
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
      credentials: options.credentials ?? 'omit',
      signal: options.signal ?? AbortSignal.timeout(timeoutFor(path)),
    });
  } catch (cause) {
    // 연결 실패와 타임아웃은 분석 서비스(Python)와 무관하다. 예전 코드가
    // PYTHON_SERVICE_UNAVAILABLE 을 붙여서 원인 진단을 어렵게 만들었다.
    throw new ApiFailure(
      {
        code: 'NETWORK_UNAVAILABLE',
        message:
          cause instanceof DOMException && cause.name === 'TimeoutError'
            ? '서버가 제한 시간 안에 응답하지 않았습니다. 다시 조회하세요.'
            : '서버에 연결하지 못했습니다. 연결 상태와 API 주소를 확인하세요.',
      },
      requestId,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw nonEnvelopeFailure(response.status, requestId);
  }

  if (!isEnvelope(payload)) {
    throw nonEnvelopeFailure(response.status, requestId);
  }

  return unwrap(payload as ApiEnvelope<T>);
}

/** Calls v2 endpoints that return bare DTOs instead of the shared API envelope. */
export async function apiFetchBare<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const requestId = newRequestId();
  const method = options.method ?? 'GET';

  mockQaFailure(path, requestId);

  if (apiMode() === 'mock') {
    if (!mockModule) throw new Error('Mock API mode is unavailable in production.');
    return mockModule.mockBareTransport<T>(path, method, options.body, requestId);
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
      signal: options.signal ?? AbortSignal.timeout(timeoutFor(path)),
    });
  } catch (cause) {
    throw new ApiFailure(
      {
        code: 'NETWORK_UNAVAILABLE',
        message:
          cause instanceof DOMException && cause.name === 'TimeoutError'
            ? '서버가 제한 시간 안에 응답하지 않았습니다. 다시 조회하세요.'
            : '서버에 연결하지 못했습니다. 연결 상태와 API 주소를 확인하세요.',
      },
      requestId,
    );
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw nonEnvelopeFailure(response.status, requestId);
  }

  if (!response.ok) {
    const error = payload as { code?: unknown; message?: unknown; requestId?: unknown };
    // code 가 없으면 이 응답은 v2 계약이 아니다(프록시·기본 error handler). status 로 분류해야
    // 재시도 가능 여부가 정확해진다.
    if (typeof error.code !== 'string') {
      throw nonEnvelopeFailure(
        response.status,
        typeof error.requestId === 'string' ? error.requestId : requestId,
      );
    }
    throw new ApiFailure(
      {
        code: error.code,
        message: typeof error.message === 'string' ? error.message : '알 수 없는 오류입니다.',
      },
      typeof error.requestId === 'string' ? error.requestId : requestId,
    );
  }
  return payload as T;
}

/**
 * QA 가 명시적으로 심어 둔 sessionStorage 표식이 있을 때만 한 endpoint 의 첫 응답을
 * 실패시켜 화면 자체의 retry 를 검증한다.
 *
 * mock 모드로 제한하지 않는다. 실제 Spring 을 보는 화면의 복구가 진짜로 검증돼야 하고,
 * 표식이 없으면 아무 일도 하지 않으므로 일반 사용자 경로에는 영향이 없다.
 */
function mockQaFailure(path: string, requestId: string): void {
  if (typeof window === 'undefined') return;
  // 프로덕션 번들에는 장애 주입 스위치를 남기지 않는다. 남겨 두면 스크립트 실행 권한을
  // 얻은 쪽이 sessionStorage 표식 하나로 특정 화면(예: 주문 중지)을 탭 수명 내내
  // 실패시킬 수 있다. QA 는 개발/검증 빌드에서 돈다.
  if (!qaFaultInjectionAllowed()) return;
  const raw = window.sessionStorage.getItem('p1-qa-api-fault');
  if (!raw) return;
  try {
    const fault = JSON.parse(raw) as { path?: string; remaining?: number };
    if (fault.path !== path.split('?')[0] || !fault.remaining || fault.remaining < 1) return;
    const remaining = fault.remaining - 1;
    if (remaining) {
      window.sessionStorage.setItem('p1-qa-api-fault', JSON.stringify({ ...fault, remaining }));
    } else {
      window.sessionStorage.removeItem('p1-qa-api-fault');
    }
    throw new ApiFailure(
      { code: 'INTERNAL_ERROR', message: '화면별 복구 검증을 위한 일회성 503입니다.' },
      requestId,
    );
  } catch (error) {
    if (error instanceof ApiFailure) throw error;
    window.sessionStorage.removeItem('p1-qa-api-fault');
  }
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
