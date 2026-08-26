/**
 * 금융 가이드 ViewModel.
 *
 *  1) POST /api/v1/rag/ask                            — 답변, 출처 연결률, 가드레일 (answerId 발급)
 *  2) GET  /api/v1/dashboard/rag-sources/{answerId}   — sanitized 출처 ViewModel (권위)
 *  3) GET  /api/v1/rag/sources                        — 전체 출처 registry
 *
 * RAG는 설명 기능이다. 매수·매도 지시를 하지 않으며 주문 판단에 영향을 주지 않는다.
 */
import { api } from '@/shared/api/endpoints';
import { safeExternalUrl } from '@/shared/api/session';
import type {
  DashboardRagSourcesView,
  RagAnswerProjection,
  RagGenerationStatus,
  RagSourceClassification,
  RagSourceResponse,
} from '@/shared/api/wire';
import { ready, type ViewState } from '@/shared/lib/viewState';

export interface SourceItem {
  sourceId: string;
  title: string;
  classification: RagSourceClassification;
  summary: string;
  /** registry에서 찾아낸 원문 링크. https가 아니면 null이다. */
  href: string | null;
  institution: string | null;
}

export interface RagAnswerView {
  answerId: string;
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
}

const STATUS_COPY: Record<RagGenerationStatus, { headline: string; detail: string }> = {
  ANSWERED: {
    headline: '출처를 확인한 설명',
    detail: '아래 문장은 표시된 출처에서 나온 내용입니다.',
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

function enrich(
  items: DashboardRagSourcesView['topSources'],
  registry: Map<string, RagSourceResponse>,
): SourceItem[] {
  return items.map((item) => {
    const card = registry.get(item.sourceId);
    return {
      sourceId: item.sourceId,
      title: item.title,
      classification: item.classification,
      summary: item.summary,
      href: safeExternalUrl(card?.canonicalUrl),
      institution: card?.institution ?? null,
    };
  });
}

export async function askRag(
  question: string,
  answerMode: 'CONCISE' | 'DETAILED',
): Promise<ViewState<RagAnswerView>> {
  const answerResult = await api.ragAsk({ question, answerMode });
  const answer: RagAnswerProjection = answerResult.data;

  // 출처 registry는 링크 보강용이다. 실패해도 답변 자체는 보여준다.
  const registry = new Map<string, RagSourceResponse>();
  try {
    const list = await api.ragSources();
    for (const card of list.data.items) registry.set(card.sourceId, card);
  } catch {
    /* 링크 없이 진행한다 */
  }

  let topSources: SourceItem[] = [];
  let expandableSources: SourceItem[] = [];
  let sourcesUnavailableReason: string | null = null;

  if (answer.citations.length === 0) {
    sourcesUnavailableReason = '이 답변에 연결된 출처가 없습니다.';
  } else {
    try {
      const sources = await api.dashboardRagSources(answer.answerId);
      if (sources.data.view) {
        topSources = enrich(sources.data.view.topSources, registry);
        expandableSources = enrich(sources.data.view.expandableSources, registry);
      } else {
        sourcesUnavailableReason = '이 답변에 연결된 출처가 없습니다.';
      }
    } catch {
      sourcesUnavailableReason = '출처 목록을 불러오지 못했습니다. 답변 내용만 표시합니다.';
    }
  }

  const copy = STATUS_COPY[answer.generationStatus];
  const answered = answer.generationStatus === 'ANSWERED';

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
    topSources,
    expandableSources,
    sourcesUnavailableReason,
  });
}

export async function loadRegistry(): Promise<ViewState<RagSourceResponse[]>> {
  const { data } = await api.ragSources();
  return ready(data.items);
}
