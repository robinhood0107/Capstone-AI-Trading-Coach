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
  usage?: {
    inputTokens: number;
    outputTokens: number;
    estimatedCostUsd: number;
    modelVersion: string;
    latencyMs: number;
  };
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

/** 2026-09-30 FULL 3002의 demo-user가 실제로 답을 받은 질문만 예시로 둔다. */
export const RAG_EXAMPLES: readonly {
  question: string;
  answer: string;
  citationCoverage: number | null;
  source: SourceItem | null;
}[] = [
  {
    question: '132030 금선물 ETF의 환헤지와 롤오버 위험을 설명해 주세요.',
    answer:
      '132030 KODEX 골드선물(H)은 S&P GSCI Gold Index Total Return을 추종하는 COMEX 금선물 기반 환헤지 상품이므로 현물 금과 동일한 성과로 간주하지 않습니다.\n이 상품은 선물 롤오버(rollover)와 환헤지 효과로 인해 현물 금과 성과가 달라질 수 있습니다.',
    citationCoverage: 1,
    source: {
      sourceId: 'recorded-132030-gold-futures',
      title: '132030 금선물 ETF의 선물·환헤지·롤오버 경계',
      citationKind: 'PUBLIC_WEB',
      summary: '',
      href: 'https://www.samsungfund.com/etf/product/view.do?id=2ETF24',
      institution: '삼성자산운용',
    },
  },
  {
    question: 'Sharpe 비율과 최대낙폭(MDD)은 각각 무엇을 측정하나요?',
    answer:
      'Sharpe 비율과 최대낙폭(maximum drawdown)은 서로 다른 위험의 단면을 나타내는 지표입니다.\n구체적으로 Sharpe 비율은 수익의 분산 대비 수익률(return-dispersion ratio)을 측정하며, 최대낙폭은 경로 의존적인 낙폭 측정치(path-dependent drawdown measure)를 나타냅니다.\n따라서 이 두 지표 중 어느 하나가 전체 위험을 완벽히 대변하는 충분통계량으로 쓰일 수 없으며, 단일 지표 조합만으로는 미래의 손실을 완전히 설명하는 데 한계가 있습니다.',
    citationCoverage: 2 / 3,
    source: {
      sourceId: 'recorded-sharpe-drawdown-risk',
      title: 'Sharpe와 maximum drawdown의 부분 위험 관점',
      citationKind: 'PUBLIC_WEB',
      summary: '',
      href: 'https://doi.org/10.1111/joes.12520',
      institution: '학술 논문',
    },
  },
  {
    question: '복리와 단리는 어떻게 다른가요?',
    answer:
      '단리와 복리는 이자가 계산되는 방식에서 가장 큰 차이가 있습니다.\n단리는 원금에 대해서만 이자가 발생하는 방식인 반면, 복리는 원금뿐만 아니라 발생한 이자에도 다시 이자가 붙는 방식입니다.\n따라서 투자 기간이 길어질수록 복리 효과에 의해 자산의 증식 속도가 단리보다 훨씬 빨라집니다.',
    citationCoverage: null,
    source: null,
  },
  {
    question: '인덱스 펀드의 추적오차란 무엇인가요?',
    answer:
      '인덱스 펀드의 추적오차(Tracking Error)는 펀드의 수익률이 추종하고자 하는 기초지수(벤치마크)의 수익률을 얼마나 잘 따라가는지를 나타내는 지표입니다.\n이는 일정 기간 동안 발생한 펀드 수익률과 기초지수 수익률 간 차이(추적편차)의 표준편차로 계산됩니다.\n추적오차가 낮을수록 펀드가 기초지수를 밀접하게 복제하고 있음을 의미하며, 운용 보수나 거래 비용, 구성 종목의 편입 비율 차이 등이 추적오차를 발생시키는 주요 요인이 됩니다.',
    citationCoverage: null,
    source: null,
  },
  {
    question: '과거 성과는 어떻게 읽어야 하나요?',
    answer:
      '금융 상품의 과거 성과(수익률)를 읽을 때는 과거의 실적이 미래의 결과를 보장하지 않는다는 점을 명확히 인지해야 합니다.\n성과를 평가할 때는 단순 수익률뿐만 아니라 당시의 시장 상황, 비교 대상이 되는 벤치마크(BM) 대비 성과, 그리고 변동성이나 최대 낙폭(MDD) 같은 위험 요소를 함께 분석하는 것이 중요합니다.\n또한 일시적인 고수익인지 혹은 장기적으로 꾸준한 성과를 내었는지 기간별 추이를 다각도로 살펴보아야 합니다.',
    citationCoverage: null,
    source: null,
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
    headline: 'Vertex AI Gemini 답변',
    detail: 'Vertex AI Gemini가 생성한 답변입니다. 외부 문헌 인용은 연결되지 않았습니다.',
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
  if (locator?.section && locator.section !== 'source-card') return locator.section;
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

export async function loadConsentProfile(): Promise<{
  granted: boolean;
  disclosure: string;
  policy: string;
  processors: string;
  agentUsage?: import('@/shared/api/wire').RagV2CorpusStatus['agentUsage'];
}> {
  try {
    const consent = await api.ragV2Consent();
    const corpus = await api.ragV2CorpusStatus().catch(() => null);
    return {
      granted: consent.effective,
      disclosure: consent.disclosureText ?? EXTERNAL_DISCLOSURE,
      policy: consent.policyText ?? EXTERNAL_POLICY,
      processors: consent.processorNames ?? EXTERNAL_PROCESSORS,
      agentUsage: corpus?.agentUsage,
    };
  } catch {
    return {
      granted: false,
      disclosure: '질문 전송 범위를 확인할 수 없습니다. 처리 방식이 확인될 때까지 질문은 보내지 않습니다.',
      policy: '연결이 복구되면 처리자와 보관 정책을 다시 확인할 수 있습니다.',
      processors: '확인 불가',
    };
  }
}

async function digest(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function recordConsent(
  action: 'GRANT' | 'REVOKE',
  profile: { disclosure: string; policy: string; processors: string } = {
    disclosure: EXTERNAL_DISCLOSURE,
    policy: EXTERNAL_POLICY,
    processors: EXTERNAL_PROCESSORS,
  },
): Promise<void> {
  await api.ragV2RecordConsent({
    contractId: 's4-rag-v2-external-consent-v1',
    schemaVersion: 1,
    consentType: 'EXTERNAL_AI_RAG_V2',
    action,
    disclosureDigest: await digest(profile.disclosure),
    policyDigest: await digest(profile.policy),
    processorSetDigest: await digest(profile.processors),
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
    // 예시 버튼은 이전 성공 답변이 있으므로 생성 불가를 확인한 뒤 두 번째 비용을 쓰지 않는다.
    // 자유 질문만 무응답이 명시된 경우 한 번 재시도하며, 애매한 HTTP 실패는 재전송하지 않는다.
    if (!fallback && answer.generationStatus === 'GENERATION_UNAVAILABLE' && answer.answer === null) {
      answer = await api.ragV2Ask(request);
    }
  } catch (cause) {
    if (canUseVertexUnavailableFallbackForError(cause)) {
      return ready(fallback ?? vertexUnavailableView([]));
    }
    throw cause;
  }

  // 예시 버튼의 질문에만 이전에 성공한 답변을 보관한다. 자유 질문과 민감정보 차단은
  // 언제나 live API 결과를 유지하며, 저장 답변에 없던 출처를 붙이지 않는다.
  if (fallback && canUseExampleFallbackForAnswer(answer)) return ready(fallback);
  if (canUseVertexUnavailableFallbackForAnswer(answer)) return ready(vertexUnavailableView(answer.citations));

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
    usage: answer.usage,
    // 생성된 문장이 없으면 연결률은 의미가 없다. 0으로 표시하지 않는다.
    citationCoverage: answered && items.length > 0 ? answer.citationCoverage : null,
    retrievalFailure: answer.retrievalFailure,
    guardrailFlags: answer.guardrailFlags,
    topSources: items.slice(0, TOP_SOURCE_COUNT),
    expandableSources: items.slice(TOP_SOURCE_COUNT),
    sourcesUnavailableReason:
      items.length === 0
        ? answered
          ? '생성 모델: Google Vertex AI Gemini. 외부 문헌 인용은 연결되지 않았습니다.'
          : '이 질문에 연결된 출처가 없습니다.'
        : null,
    fallbackUsed: false,
  });
}

function vertexUnavailableView(citations: RagV2Citation[]): RagAnswerView {
  const sources = toSourceItems(citations, new Map());
  return {
    answerId: null,
    generationStatus: 'GENERATION_UNAVAILABLE',
    statusHeadline: 'Vertex AI 응답을 가져오지 못했습니다',
    statusDetail: 'Vertex AI가 답변을 생성하지 못해 고정 안내를 표시합니다. 잠시 후 다시 질문해 주세요.',
    answer: 'Vertex AI 응답 생성이 일시적으로 불가능합니다. 잠시 후 다시 질문해 주세요.',
    citationCoverage: null,
    retrievalFailure: true,
    guardrailFlags: ['VERTEX_UNAVAILABLE'],
    topSources: sources.slice(0, TOP_SOURCE_COUNT),
    expandableSources: sources.slice(TOP_SOURCE_COUNT),
    sourcesUnavailableReason:
      sources.length === 0 ? 'Vertex AI가 복구되면 다시 질문해 주세요.' : null,
    fallbackUsed: true,
  };
}

function exampleFallbackView(example: (typeof RAG_EXAMPLES)[number]): RagAnswerView {
  return {
    answerId: null,
    generationStatus: 'ANSWERED',
    statusHeadline: '저장된 예시 답변',
    statusDetail: '실시간 생성에 실패해 이전에 성공한 같은 질문의 답변을 표시합니다.',
    answer: example.answer,
    citationCoverage: example.citationCoverage,
    retrievalFailure: false,
    guardrailFlags: [],
    topSources: example.source ? [example.source] : [],
    expandableSources: [],
    sourcesUnavailableReason: example.source ? null : '이 저장된 답변에는 외부 문헌 인용이 연결되지 않았습니다.',
    fallbackUsed: true,
  };
}

function canUseExampleFallbackForAnswer(answer: RagV2Answer): boolean {
  if (answer.generationStatus === 'BLOCKED_ADVICE' || answer.generationStatus === 'BLOCKED_SENSITIVE') {
    return false;
  }
  return answer.answer === null;
}

function canUseVertexUnavailableFallbackForAnswer(answer: RagV2Answer): boolean {
  if (answer.answer !== null) return false;
  if (answer.generationStatus === 'BLOCKED_ADVICE' || answer.generationStatus === 'BLOCKED_SENSITIVE') {
    return false;
  }
  return ['GENERATION_UNAVAILABLE', 'RETRIEVAL_FAILURE', 'RETRIEVAL_ONLY'].includes(answer.generationStatus);
}

function canUseVertexUnavailableFallbackForError(cause: unknown): boolean {
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
