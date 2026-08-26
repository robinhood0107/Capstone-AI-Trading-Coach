/**
 * 공통 응답 envelope.
 * 출처: contracts/openapi/openapi.json 의 ApiResponse / ApiError / ApiWarning,
 *       그리고 서버의 ErrorCode enum.
 *
 * 클라이언트는 HTTP status가 아니라 error.code로 분기한다.
 */

/** 서버 ErrorCode enum과 1:1로 맞춘 목록. */
export const API_ERROR_CODES = [
  'VALIDATION_ERROR',
  'UNAUTHORIZED',
  'FORBIDDEN',
  'NOT_FOUND',
  'CONFLICT',
  'VERSION_EXHAUSTED',
  'DECISION_EXPIRED',
  'IDEMPOTENCY_CONFLICT',
  'IDEMPOTENCY_IN_PROGRESS',
  'IDEMPOTENCY_RESULT_UNAVAILABLE',
  'PAYLOAD_TOO_LARGE',
  'RISK_BLOCKED',
  'RISK_UNAVAILABLE',
  'RAG_UNAVAILABLE',
  'RAG_HISTORY_PERSIST_FAILED',
  'RAG_HISTORY_CORRUPTED',
  'DATA_STALE',
  'RATE_LIMITED',
  'PYTHON_SERVICE_UNAVAILABLE',
  'BROKERAGE_UNAVAILABLE',
  'SIGNAL_UNAVAILABLE',
  'INTERNAL_ERROR',
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

/** 서버가 새 code를 추가해도 화면이 깨지지 않도록 unknown 문자열을 허용한다. */
export type WireErrorCode = ApiErrorCode | (string & {});

export interface ApiWarning {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ApiError {
  code: WireErrorCode;
  message: string;
  details?: Record<string, unknown>;
}

export interface ApiEnvelope<T> {
  success: boolean;
  requestId: string;
  data: T | null;
  warnings: ApiWarning[];
  error: ApiError | null;
}

export interface ApiResult<T> {
  data: T;
  warnings: ApiWarning[];
  requestId: string;
}

/** 다시 조회 버튼을 노출해도 되는 코드. 금융 write 재시도는 여기에 넣지 않는다. */
const RETRYABLE: ReadonlySet<string> = new Set<ApiErrorCode>([
  'RISK_UNAVAILABLE',
  'RAG_UNAVAILABLE',
  'RAG_HISTORY_PERSIST_FAILED',
  'RAG_HISTORY_CORRUPTED',
  'PYTHON_SERVICE_UNAVAILABLE',
  'BROKERAGE_UNAVAILABLE',
  'SIGNAL_UNAVAILABLE',
  'DATA_STALE',
  'RATE_LIMITED',
  'INTERNAL_ERROR',
]);

/** 사용자에게 보여줄 한국어 안내. 서버 message는 영어라 그대로 노출하지 않는다. */
const KOREAN_MESSAGE: Partial<Record<ApiErrorCode, string>> = {
  VALIDATION_ERROR: '요청 형식이 올바르지 않습니다.',
  UNAUTHORIZED: '로그인이 필요합니다. 다시 로그인하세요.',
  FORBIDDEN: '이 자료에 접근할 권한이 없습니다.',
  NOT_FOUND: '해당 자료를 찾을 수 없습니다. ID를 다시 확인하세요.',
  CONFLICT: '다른 변경과 충돌했습니다. 최신 상태를 다시 불러오세요.',
  VERSION_EXHAUSTED: '원칙 버전 한도에 도달했습니다.',
  DECISION_EXPIRED: '판정 유효시간이 지났습니다. 다시 평가해야 합니다.',
  IDEMPOTENCY_CONFLICT: '같은 키로 다른 내용을 보냈습니다. 새로 요청하세요.',
  IDEMPOTENCY_IN_PROGRESS: '같은 요청이 처리 중입니다. 잠시 후 확인하세요.',
  IDEMPOTENCY_RESULT_UNAVAILABLE: '이전 요청 결과가 더 이상 남아 있지 않습니다.',
  PAYLOAD_TOO_LARGE: '요청 내용이 너무 큽니다.',
  RISK_BLOCKED: '위험 통제에 의해 요청이 차단됐습니다.',
  RISK_UNAVAILABLE: '위험 평가를 지금 사용할 수 없습니다.',
  RAG_UNAVAILABLE: '설명 근거 저장소를 지금 사용할 수 없습니다.',
  RAG_HISTORY_PERSIST_FAILED: '질문 기록을 저장하지 못했습니다.',
  RAG_HISTORY_CORRUPTED: '질문 기록을 읽을 수 없습니다.',
  DATA_STALE: '필요한 데이터가 오래돼 사용할 수 없습니다.',
  RATE_LIMITED: '요청이 너무 잦습니다. 잠시 후 다시 시도하세요.',
  PYTHON_SERVICE_UNAVAILABLE: '분석 서비스에 연결하지 못했습니다.',
  BROKERAGE_UNAVAILABLE: '증권 연동 서비스에 연결하지 못했습니다.',
  SIGNAL_UNAVAILABLE: '신호 저장소를 지금 사용할 수 없습니다.',
  INTERNAL_ERROR: '요청이 안전하게 중단됐습니다.',
};

export class ApiFailure extends Error {
  readonly code: WireErrorCode;
  readonly requestId: string;
  readonly details: Record<string, unknown> | undefined;
  readonly retryable: boolean;
  /** 화면에 그대로 띄울 한국어 문구. */
  readonly userMessage: string;

  constructor(error: ApiError, requestId: string) {
    super(error.message);
    this.name = 'ApiFailure';
    this.code = error.code;
    this.requestId = requestId;
    this.details = error.details;
    this.retryable = RETRYABLE.has(error.code);
    this.userMessage =
      KOREAN_MESSAGE[error.code as ApiErrorCode] ?? error.message ?? '알 수 없는 오류가 발생했습니다.';
  }
}

export function isEnvelope(value: unknown): value is ApiEnvelope<unknown> {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.success === 'boolean' &&
    typeof candidate.requestId === 'string' &&
    'data' in candidate &&
    'error' in candidate
  );
}
