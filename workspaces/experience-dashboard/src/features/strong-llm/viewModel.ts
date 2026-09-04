/** Strong LLM settings projection; stored credentials never return to the browser. */
import { api } from '@/shared/api/endpoints';
import type { PutStrongLlmSettingsRequest, RagV2CorpusStatus } from '@/shared/api/wire';
import { ready, type ViewState } from '@/shared/lib/viewState';

export const PROVIDERS = ['vertex', 'openai', 'anthropic', 'google_genai', 'custom'] as const;
export type Provider = (typeof PROVIDERS)[number];

export const PROVIDER_LABEL: Record<Provider, string> = {
  vertex: 'Vertex AI (Gemini)',
  openai: 'OpenAI',
  anthropic: 'Anthropic (Claude)',
  google_genai: 'Google AI (Gemini API)',
  custom: '직접 입력 (OpenAI 호환)',
};

/** Vertex는 서비스계정 파일로 붙는다. 화면에서 키를 받지 않는다. */
export const PROVIDER_NEEDS_KEY: Record<Provider, boolean> = {
  vertex: false,
  openai: true,
  anthropic: true,
  google_genai: true,
  custom: true,
};

export interface StrongLlmSettingsView {
  provider: Provider;
  fallbackProvider: Provider | '';
  modelId: string;
  fallbackModelId: string;
  baseUrl: string;
  fallbackBaseUrl: string;
  answerLanguage: 'ko' | 'en';
  dailyGenerateCallCap: number;
  keyLast4: string | null;
  fallbackKeyLast4: string | null;
  usedToday: number | null;
  remaining: number | null;
  /** 서버가 실제로 쓰고 있는 하루 상한. 설정값과 다르면 배포 정책이 더 좁다는 뜻이다. */
  effectiveDailyCap: number | null;
}

function asProvider(value: string | null): Provider {
  return (PROVIDERS as readonly string[]).includes(value ?? '') ? (value as Provider) : 'vertex';
}

export function toView(status: RagV2CorpusStatus): StrongLlmSettingsView {
  return {
    provider: asProvider(status.strongLlmProvider),
    fallbackProvider: (PROVIDERS as readonly string[]).includes(status.strongLlmFallbackProvider ?? '')
      ? (status.strongLlmFallbackProvider as Provider)
      : '',
    modelId: status.strongLlmModelId ?? '',
    fallbackModelId: status.strongLlmFallbackModelId ?? '',
    baseUrl: status.strongLlmBaseUrl ?? '',
    fallbackBaseUrl: status.strongLlmFallbackBaseUrl ?? '',
    answerLanguage: status.strongLlmAnswerLanguage === 'en' ? 'en' : 'ko',
    dailyGenerateCallCap: status.strongLlmDailyGenerateCallCap ?? 50,
    keyLast4: status.strongLlmKeyLast4,
    fallbackKeyLast4: status.strongLlmFallbackKeyLast4,
    usedToday: status.generationUsedToday,
    remaining: status.generationRemaining,
    effectiveDailyCap: status.generationDailyCap,
  };
}

export async function loadSettings(): Promise<ViewState<StrongLlmSettingsView>> {
  return ready(toView(await api.ragV2CorpusStatus()));
}

/**
 * 빈 문자열은 "지운다", `undefined`는 "그대로 둔다"이다. 그 둘을 하나로 합치면 설정만
 * 바꾸려는 저장이 이미 넣어 둔 키를 조용히 지운다.
 */
export function toRequest(
  view: StrongLlmSettingsView,
  apiKey: string,
  fallbackApiKey: string,
  clearPrimary: boolean,
  clearFallback: boolean,
): PutStrongLlmSettingsRequest {
  const request: PutStrongLlmSettingsRequest = {
    provider: view.provider,
    fallbackProvider: view.fallbackProvider === '' ? null : view.fallbackProvider,
    modelId: view.modelId.trim() === '' ? null : view.modelId.trim(),
    fallbackModelId: view.fallbackModelId.trim() === '' ? null : view.fallbackModelId.trim(),
    baseUrl: view.baseUrl.trim() === '' ? null : view.baseUrl.trim(),
    fallbackBaseUrl: view.fallbackBaseUrl.trim() === '' ? null : view.fallbackBaseUrl.trim(),
    answerLanguage: view.answerLanguage,
    dailyGenerateCallCap: view.dailyGenerateCallCap,
  };
  if (clearPrimary) request.apiKey = '';
  else if (apiKey !== '') request.apiKey = apiKey;
  if (clearFallback) request.fallbackApiKey = '';
  else if (fallbackApiKey !== '') request.fallbackApiKey = fallbackApiKey;
  return request;
}

/** 저장이 막힐 이유를 화면이 미리 말한다. 서버는 같은 규칙으로 한 번 더 닫는다. */
export function validationError(
  view: StrongLlmSettingsView,
  apiKey: string,
  fallbackApiKey: string,
): string | null {
  if (view.provider === 'custom' && view.baseUrl.trim() === '') {
    return '직접 입력 provider는 https 주소가 필요합니다.';
  }
  if (view.fallbackProvider === 'custom' && view.fallbackBaseUrl.trim() === '') {
    return '2차 provider가 직접 입력이면 https 주소가 필요합니다.';
  }
  if (view.dailyGenerateCallCap < 1 || view.dailyGenerateCallCap > 500) {
    return '하루 호출 상한은 1에서 500 사이입니다.';
  }
  for (const key of [apiKey, fallbackApiKey]) {
    if (key !== '' && key.trim().length < 8) return 'API 키가 너무 짧습니다.';
    if (key !== '' && key !== key.trim()) return 'API 키 앞뒤의 공백을 지워 주세요.';
  }
  if (PROVIDER_NEEDS_KEY[view.provider] && apiKey === '' && view.keyLast4 === null) {
    return `${PROVIDER_LABEL[view.provider]}에는 API 키가 필요합니다.`;
  }
  if (
    view.fallbackProvider !== '' &&
    PROVIDER_NEEDS_KEY[view.fallbackProvider] &&
    fallbackApiKey === '' &&
    view.fallbackKeyLast4 === null
  ) {
    return `2차 ${PROVIDER_LABEL[view.fallbackProvider]}에는 API 키가 필요합니다.`;
  }
  return null;
}

export async function saveSettings(request: PutStrongLlmSettingsRequest): Promise<void> {
  await api.putStrongLlmSettings(request);
}
