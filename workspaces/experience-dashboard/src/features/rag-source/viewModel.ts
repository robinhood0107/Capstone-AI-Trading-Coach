/**
 * 금융 가이드 ViewModel.
 *
 *  1) GET  /api/v2/rag/corpus-status   — 코퍼스 준비 상태
 *  2) GET  /api/v2/rag/consent         — 외부 처리 동의 상태
 *  3) POST /api/v2/rag/consents        — 동의 기록/철회
 *  4) POST /api/v2/rag/ask             — 실제 검색 결과와 인용
 *  5) GET  /api/v1/rag/sources         — 전체 출처 registry (링크 보강용)
 *
 * v1 ask는 60문항 고정 fixture라 DB를 읽지 않는다. v2는 같은 화면에 실제 인용을 준다.
 * v2는 인용을 응답에 직접 담으므로 dashboard-rag-sources 두 번째 홉이 필요 없다.
 *
 * RAG는 설명 기능이다. 매수·매도 지시를 하지 않으며 주문 판단에 영향을 주지 않는다.
 */
import { api } from '@/shared/api/endpoints';
import { ApiFailure } from '@/shared/api/envelope';
import { safeExternalUrl } from '@/shared/api/session';
import type {
  RagGenerationStatus,
  RagSourceResponse,
  RagV2Answer,
  RagV2Citation,
  RagV2CorpusStatus,
  RagV2HistoryDetail,
  WorldNewsPage,
} from '@/shared/api/wire';
import { ready, type ViewState } from '@/shared/lib/viewState';

/**
 * 동의 화면이 보여 주는 문장 그대로의 해시를 기록한다. 문장을 고치면 해시가 달라지고,
 * 사용자가 무엇에 동의했는지가 기록과 어긋나지 않는다.
 */
export const EXTERNAL_DISCLOSURE =
  '질문은 외부 임베딩 제공자(Voyage AI)로 전송되어 검색 벡터로 변환됩니다. ' +
  '보유 종목, 잔고, 주문 내역은 전송되지 않습니다. ' +
  '이 기능의 답변은 개념과 위험에 대한 설명이며 투자 조언이 아니고 정확성을 보장하지 않습니다. ' +
  '무엇을 사고 팔지는 원칙 설정과 주문 검토 화면에서 직접 판단하세요.';
export const EXTERNAL_POLICY =
  '전송된 질문은 답변 생성 목적으로만 사용되며 사용량 원장에 요청 1건으로 기록됩니다. ' +
  '동의는 언제든 철회할 수 있고 철회 후에는 외부 전송이 즉시 닫힙니다.';
export const EXTERNAL_PROCESSORS = 'VOYAGE_AI';

export interface SourceItem {
  sourceId: string;
  title: string;
  /** v2 인용은 공개 웹 문서와 소유자 로컬 문서를 구분한다. */
  citationKind: RagV2Citation['citationKind'];
  summary: string;
  /** 인용이 준 원문 링크. https가 아니면 null이다. */
  href: string | null;
  institution: string | null;
}

export interface RagAnswerView {
  answerId: string | null;
  generationStatus: RagGenerationStatus;
  statusHeadline: string;
  statusDetail: string;
  answer: string | null;
  citationCoverage: number | null;
  retrievalFailure: boolean;
  guardrailFlags: string[];
  topSources: SourceItem[];
  expandableSources: SourceItem[];
  sourcesUnavailableReason: string | null;
  /** True only when a preset button used its locally stored source-backed answer. */
  fallbackUsed: boolean;
}

const TOP_SOURCE_COUNT = 3;

export const RAG_EXAMPLES: readonly {
  question: string;
  answer: string;
  source: SourceItem;
}[] = [
  {
    question: '분산투자는 위험을 어떻게 줄이나요?',
    answer:
      '분산투자는 여러 투자에 자금을 나누어 한 곳에 집중되는 위험을 줄이는 방법입니다. 시장 전체가 하락할 때의 손실까지 막아 주지는 않습니다.',
    source: {
      sourceId: 'investor-gov-diversify',
      title: 'Investor.gov — Diversify Your Investments',
      citationKind: 'PUBLIC_WEB',
      summary: '',
      href: 'https://www.investor.gov/introduction-investing/investing-basics/save-and-invest/diversify-your-investments',
      institution: 'Investor.gov',
    },
  },
  {
    question: '자산 배분은 무엇을 고려하나요?',
    answer:
      '자산 배분은 주식, 채권, 현금 같은 자산군에 투자금을 나누는 것입니다. 적절한 비율은 투자 기간과 위험 감수 성향에 따라 달라집니다.',
    source: {
      sourceId: 'investor-gov-asset-allocation',
      title: 'Investor.gov — Asset Allocation and Diversification',
      citationKind: 'PUBLIC_WEB',
      summary: '',
      href: 'https://www.investor.gov/introduction-investing/getting-started/asset-allocation',
      institution: 'Investor.gov',
    },
  },
  {
    question: '과거 성과는 어떻게 읽어야 하나요?',
    answer:
      '과거 성과는 미래 결과를 보장하지 않습니다. 백테스트는 과거 조건을 가정해 계산한 결과이며 실제 운용 성과가 아닙니다.',
    source: {
      sourceId: 'investor-gov-performance-claims',
      title: 'Investor.gov — Performance Claims',
      citationKind: 'PUBLIC_WEB',
      summary: '',
      href: 'https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-47',
      institution: 'Investor.gov',
    },
  },
];

const STATUS_COPY: Record<
  RagGenerationStatus | 'ANSWERED_WITHOUT_SOURCES',
  { headline: string; detail: string }
> = {
  ANSWERED: {
    headline: '출처를 확인한 설명',
    detail: '아래 문장은 표시된 출처에서 나온 내용입니다.',
  },
  // 근거 없이 답할 때도 설명은 나온다. 같은 ANSWERED라도 읽는 사람이 그 차이를
  // 알아야 하므로 문장을 갈라 둔다.
  ANSWERED_WITHOUT_SOURCES: {
    headline: '출처 없이 설명합니다',
    detail:
      '설명은 제공하지만 출처와의 대조가 완료되지 않았습니다. 숫자와 최신 사실은 연결된 자료에서 확인해 주세요.',
  },
  RETRIEVAL_ONLY: {
    headline: '설명 문장 없이 출처만 제공합니다',
    detail: '근거는 찾았지만 설명 문장을 생성하지 않았습니다. 출처를 직접 확인하세요.',
  },
  RETRIEVAL_FAILURE: {
    headline: '충분한 근거를 찾지 못했습니다',
    detail: '근거 없이 답을 만들지 않습니다. 질문을 좁혀서 다시 물어보세요.',
  },
  BLOCKED_SENSITIVE: {
    headline: '계좌·개인정보 질문에는 답하지 않습니다',
    detail: '보유 종목, 잔고, 주문 내역 같은 개인 정보는 이 기능의 범위 밖입니다.',
  },
  BLOCKED_ADVICE: {
    headline: '매수·매도 조언은 하지 않습니다',
    detail:
      '이 기능은 개념과 위험을 설명합니다. 무엇을 사고 팔지는 원칙 설정과 주문 검토 화면에서 직접 판단하세요.',
  },
  GENERATION_UNAVAILABLE: {
    headline: '설명 생성이 지금은 불가능합니다',
    detail: '출처 목록은 계속 확인할 수 있습니다.',
  },
};

function locatorText(citation: RagV2Citation): string {
  const locator = citation.locator;
  if (locator?.page !== undefined) return `${locator.page}쪽`;
  if (locator?.section) return locator.section;
  return '';
}

function toSourceItems(
  citations: RagV2Citation[],
  registry: Map<string, RagSourceResponse>,
): SourceItem[] {
  return citations.map((citation) => {
    const card = registry.get(citation.sourceId);
    return {
      sourceId: `${citation.citationId} · ${citation.sourceId}`,
      title: citation.title,
      citationKind: citation.citationKind,
      summary: locatorText(citation),
      href: safeExternalUrl(citation.canonicalUrl ?? card?.canonicalUrl),
      institution: card?.institution ?? null,
    };
  });
}

/** 코퍼스 준비 상태. FULL_READY가 아니면 질문 자체를 열지 않는다. */
export async function loadCorpusStatus(): Promise<ViewState<RagV2CorpusStatus>> {
  return ready(await api.ragV2CorpusStatus());
}

/** 동의 상태. 미동의는 409로 오므로 예외가 아니라 false로 접는다. */
export async function loadConsentGranted(): Promise<boolean> {
  try {
    const consent = await api.ragV2Consent();
    return consent.effective;
  } catch {
    return false;
  }
}

async function digest(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function recordConsent(action: 'GRANT' | 'REVOKE'): Promise<void> {
  await api.ragV2RecordConsent({
    contractId: 's4-rag-v2-external-consent-v1',
    schemaVersion: 1,
    consentType: 'EXTERNAL_AI_RAG_V2',
    action,
    disclosureDigest: await digest(EXTERNAL_DISCLOSURE),
    policyDigest: await digest(EXTERNAL_POLICY),
    processorSetDigest: await digest(EXTERNAL_PROCESSORS),
  });
}

export async function askRag(
  question: string,
  answerMode: 'CONCISE' | 'DETAILED',
  allowExampleFallback = false,
): Promise<ViewState<RagAnswerView>> {
  const example = allowExampleFallback ? RAG_EXAMPLES.find((item) => item.question === question.trim()) : null;
  const fallback = example ? exampleFallbackView(example) : null;
  const request: Parameters<typeof api.ragV2Ask>[0] = {
    question,
    answerMode,
    // 서버는 1~6개의 허용 주제를 요구한다. 이 화면은 개념·위험 설명이 목적이다.
    topics: ['DATA', 'FINANCIAL_ENGINEERING', 'RISK', 'METHODOLOGY', 'PRODUCT_RISK'],
  };
  let answer: RagV2Answer;
  try {
    answer = await api.ragV2Ask(request);
    // Retry once only when the server explicitly confirms that no answer was produced.
    // Ambiguous HTTP failures and successful answers are never resubmitted.
    if (answer.generationStatus === 'GENERATION_UNAVAILABLE' && answer.answer === null) {
      answer = await api.ragV2Ask(request);
    }
  } catch (cause) {
    if (fallback && canUseExampleFallbackForError(cause)) return ready(fallback);
    throw cause;
  }

  // Only preset questions have a source-backed stored answer. Free text and guardrail
  // blocks always keep the live API outcome.
  if (fallback && canUseExampleFallbackForAnswer(answer)) return ready(fallback);

  // 출처 registry는 기관명 보강용이다. 실패해도 인용 자체는 그대로 보여준다.
  const registry = new Map<string, RagSourceResponse>();
  try {
    const list = await api.ragSources();
    for (const card of list.data.items) registry.set(card.sourceId, card);
  } catch {
    /* 기관명 없이 진행한다 */
  }

  const items = toSourceItems(answer.citations, registry);
  const answered = answer.generationStatus === 'ANSWERED';
  const copy =
    answered && items.length === 0
      ? STATUS_COPY.ANSWERED_WITHOUT_SOURCES
      : STATUS_COPY[answer.generationStatus];

  return ready<RagAnswerView>({
    answerId: answer.answerId,
    generationStatus: answer.generationStatus,
    statusHeadline: copy.headline,
    statusDetail: copy.detail,
    answer: answer.answer,
    // 생성된 문장이 없으면 연결률은 의미가 없다. 0으로 표시하지 않는다.
    citationCoverage: answered ? answer.citationCoverage : null,
    retrievalFailure: answer.retrievalFailure,
    guardrailFlags: answer.guardrailFlags,
    topSources: items.slice(0, TOP_SOURCE_COUNT),
    expandableSources: items.slice(TOP_SOURCE_COUNT),
    sourcesUnavailableReason:
      items.length === 0
        ? answered
          ? '이 질문에 연결된 출처가 없습니다. 위 설명은 모델 지식에 기반합니다.'
          : '이 질문에 연결된 출처가 없습니다.'
        : null,
    fallbackUsed: false,
  });
}

function exampleFallbackView(example: (typeof RAG_EXAMPLES)[number]): RagAnswerView {
  return {
    answerId: null,
    generationStatus: 'ANSWERED',
    statusHeadline: STATUS_COPY.ANSWERED.headline,
    statusDetail: STATUS_COPY.ANSWERED.detail,
    answer: example.answer,
    citationCoverage: 1,
    retrievalFailure: false,
    guardrailFlags: [],
    topSources: [example.source],
    expandableSources: [],
    sourcesUnavailableReason: null,
    fallbackUsed: true,
  };
}

function canUseExampleFallbackForAnswer(answer: RagV2Answer): boolean {
  if (answer.generationStatus === 'BLOCKED_ADVICE' || answer.generationStatus === 'BLOCKED_SENSITIVE') {
    return false;
  }
  return answer.answer === null || answer.citations.length === 0;
}

function canUseExampleFallbackForError(cause: unknown): boolean {
  if (!(cause instanceof ApiFailure)) return false;
  return [
    'INTERNAL_ERROR',
    'NETWORK_UNAVAILABLE',
    'RAG_HISTORY_PERSIST_FAILED',
    'RAG_UNAVAILABLE',
    'PYTHON_SERVICE_UNAVAILABLE',
    'RATE_LIMITED',
    'RESPONSE_CONTRACT_MISMATCH',
  ].includes(cause.code);
}

export async function loadWorldNews(query = ''): Promise<ViewState<WorldNewsPage>> {
  /*
   * 50 은 서버 상한이다(`WorldNewsService.kt:22` 가 `1..50` 밖을 거부한다).
   *
   * 10 이던 것을 올린 이유: GDELT GEMG/GQG 는 일반 사건 피드라 최근 500건 중 금융 관련이
   * 29건(5.8%)뿐이다. 10건만 뜨면 화면이 거의 항상 빈다. 넓게 떠서 화면 쪽에서 거른다.
   * 수집 단계 필터가 자리를 잡으면 저장되는 것 자체가 금융 기사가 되고, 그때는 이 값이
   * 곧 표시 건수가 된다.
   */
  return ready(await api.ragV2WorldNews(query, 50), new Date().toISOString());
}

export async function loadRegistry(): Promise<ViewState<RagSourceResponse[]>> {
  const { data } = await api.ragSources();
  return ready(data.items);
}

export async function loadRecentQuestions(): Promise<ViewState<RagV2HistoryDetail[]>> {
  const page = await api.ragV2History(10);
  const current = page.items.filter((item) => /^rag_[0-9a-f]{32}$/.test(item.answerId)).slice(0, 5);
  // 예전에는 Promise.all 이라 다섯 건 중 한 건의 상세가 503 이면 "최근 질문" 패널이 통째로
  // 사라졌다. 읽을 수 있는 것만 보여 주는 편이 정직하고, 못 읽은 건은 목록에서 빠진다.
  const settled = await Promise.allSettled(
    current.map((item) => api.ragV2HistoryDetail(item.answerId)),
  );
  const details = settled
    .filter((result): result is PromiseFulfilledResult<RagV2HistoryDetail> => result.status === 'fulfilled')
    .map((result) => result.value);
  if (details.length === 0 && current.length > 0) {
    // 전부 실패한 것은 "질문이 없음"과 다르다. 첫 실패를 그대로 올려 보내 오류로 표시한다.
    const failure = settled.find((result) => result.status === 'rejected');
    throw (failure as PromiseRejectedResult).reason;
  }
  return ready(details, details[0]?.createdAt ?? null);
}
