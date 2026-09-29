'use client';

import { ApiFailure, isEnvelope, type ApiEnvelope, type ApiResult } from '@/shared/api/envelope';

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  idempotencyKey?: string;
  anonymous?: boolean;
  credentials?: RequestCredentials;
  signal?: AbortSignal;
}

export type ApiMode = 'mock' | 'live';

function requestId(): string {
  return `req_${crypto.randomUUID().replaceAll('-', '')}`;
}

export function newRequestId(): string {
  return requestId();
}

export function newIdempotencyKey(purpose: string): string {
  return `${purpose.replace(/[^A-Za-z0-9._~-]/g, '-').slice(0, 24)}.${crypto.randomUUID().replaceAll('-', '')}`;
}

export function apiMode(): ApiMode {
  return 'live';
}

export function baseUrl(): string {
  return '';
}

function headersFor(options: RequestOptions, id: string): Headers {
  const headers = new Headers({ 'X-Request-Id': id, Accept: 'application/json' });
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (options.idempotencyKey) headers.set('X-Idempotency-Key', options.idempotencyKey);
  return headers;
}

async function readJson(response: Response, id: string): Promise<unknown> {
  if (response.status === 204) return undefined;
  try {
    return await response.json();
  } catch {
    throw new ApiFailure({ code: response.status >= 500 ? 'INTERNAL_ERROR' : 'RESPONSE_CONTRACT_MISMATCH', message: '응답을 읽지 못했습니다.' }, id);
  }
}

function unwrap<T>(payload: unknown, response: Response, id: string, allowEmpty = false): ApiResult<T> {
  if (!isEnvelope(payload)) {
    throw new ApiFailure({ code: response.status >= 500 ? 'INTERNAL_ERROR' : 'RESPONSE_CONTRACT_MISMATCH', message: '서버 응답 형식이 다릅니다.' }, id);
  }
  const envelope = payload as ApiEnvelope<T>;
  if (!response.ok || !envelope.success || (envelope.data === null && !allowEmpty)) {
    throw new ApiFailure(envelope.error ?? { code: 'INTERNAL_ERROR', message: '요청을 완료하지 못했습니다.' }, id);
  }
  return { data: envelope.data as T, warnings: envelope.warnings ?? [], requestId: envelope.requestId ?? id };
}

async function send(path: string, options: RequestOptions, id: string): Promise<Response> {
  return fetch(path, {
    method: options.method ?? 'GET',
    headers: headersFor(options, id),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: 'no-store',
    credentials: 'same-origin',
    signal: options.signal ?? AbortSignal.timeout(path === '/api/v2/rag/ask' ? 50_000 : 15_000),
  });
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const id = requestId();
  try {
    const response = await send(path, options, id);
    const allowsEmptyData = new Set([
      '/api/v1/dashboard/risk-results/latest',
      '/api/v4/automation/capital-policy',
      '/api/v4/automation/capital-status',
    ]).has(path);
    return unwrap<T>(await readJson(response, id), response, id, allowsEmptyData);
  } catch (cause) {
    if (cause instanceof ApiFailure) throw cause;
    throw new ApiFailure({ code: 'NETWORK_UNAVAILABLE', message: '서버에 연결하지 못했습니다.' }, id);
  }
}

export async function apiFetchBare<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const id = requestId();
  try {
    const response = await send(path, options, id);
    const payload = await readJson(response, id);
    if (!response.ok) {
      const error = typeof payload === 'object' && payload !== null
        ? payload as { code?: string; message?: string }
        : {};
      throw new ApiFailure({ code: error.code ?? 'INTERNAL_ERROR', message: error.message ?? '요청을 완료하지 못했습니다.' }, id);
    }
    return payload as T;
  } catch (cause) {
    if (cause instanceof ApiFailure) throw cause;
    throw new ApiFailure({ code: 'NETWORK_UNAVAILABLE', message: '서버에 연결하지 못했습니다.' }, id);
  }
}
