import { ApiFailure, type ApiEnvelope } from '@/shared/api/envelope';
import type {
  AutomationPresetId,
  KillSwitchState,
  OrderDetail,
  OrderFill,
  RagV2HistoryDetail,
} from '@/shared/api/wire';
import * as fixtures from './fixtures';

/**
 * 백엔드가 없을 때 화면과 ViewModel을 검증하기 위한 mock transport.
 *
 * 실제 서버의 제약을 일부러 그대로 흉내낸다.
 *  - /api/v1/dashboard/* 는 query parameter를 붙이면 VALIDATION_ERROR
 *  - ID 형식이 틀리면 VALIDATION_ERROR
 *  - 없는 ID는 NOT_FOUND
 * 여기서 지름길을 만들면 live 전환 시점에 화면이 깨진다.
 */
const LATENCY_MS = 240;

/** 오프라인 픽스처가 "최근 실행"으로 내보내는 실행 id. 두 대시보드 경로가 함께 쓴다. */
const LATEST_RUN_ID = 'demo_s8_offline_0001';

const ID_PATTERN = {
  decisionId: /^dec_[A-Za-z0-9_-]{8,96}$/,
  runId: /^(run|demo)_[A-Za-z0-9_-]{8,96}$/,
  answerId: /^rag_[A-Za-z0-9_-]{12,96}$/,
  principleId: /^prc_[0-9a-f]{32}$/,
};

function ok<T>(data: T, requestId: string, warnings: ApiEnvelope<T>['warnings'] = []): ApiEnvelope<T> {
  return { success: true, requestId, data, warnings, error: null };
}

function fail<T>(code: string, message: string, requestId: string): ApiEnvelope<T> {
  return { success: false, requestId, data: null, warnings: [], error: { code, message } };
}

/**
 * v2 RAG는 공통 봉투 없이 DTO를 그대로 돌려준다. mock도 같은 모양이어야 live 전환에서
 * 화면이 깨지지 않는다. 실제 인용은 서버에만 있으므로 여기서는 v1 fixture를 v2 모양으로
 * 옮겨 담는다. 값이 fixture라는 사실은 화면이 별도로 표시한다.
 */
let mockConsentGranted = false;

/**
 * mock 의 RAG 질문 기록.
 *
 * 예전에는 빈 배열을 돌려주고 상세는 언제나 404 였다. 그러면 mock 에서 "최근 질문"이 늘
 * 비어 있어 삭제도 피드백도 눌러 볼 수가 없다. 물어보면 여기에 쌓고, 지우면 여기서 뺀다 —
 * 탭이 살아 있는 동안만 유지되는 값이다.
 */
const mockRagHistory: RagV2HistoryDetail[] = [];

/** v1 통제 상태에만 있는 값. 주문 계약이 요구한다. */
const MOCK_STRATEGY_ID = 'strategy_00000000';

/** mock 이 낸 주문. 탭이 살아 있는 동안만 유지된다. */
const mockOrders = new Map<string, OrderDetail>();
const mockFills: OrderFill[] = [];

let mockOrderCounter = 0;

function mockOrderId(): string {
  mockOrderCounter += 1;
  return `ord_${mockOrderCounter.toString(16).padStart(32, '0')}`;
}

/** mock 의 Kill Switch 상태. 탭이 살아 있는 동안만 유지된다. */
let mockKillSwitch: KillSwitchState = {
  active: false,
  changedAt: new Date().toISOString(),
  reasonClass: 'INITIAL_STATE',
};

/**
 * 자동운용 상태에 지금의 Kill Switch 값을 얹는다.
 *
 * Kill Switch 가 켜져 있으면 시작할 수 없다 — 실제로도 DB 가 그렇게 막는다
 * (`V93__p1_automation_pipeline_continuity.sql:128` 이 `kill_switch_inactive` 를 요구하고,
 * `V109...:60` 이 활성 상태에서의 arm 을 거부한다). mock 도 같은 규칙을 지킨다.
 */
function killSwitchAware(status: typeof fixtures.automationStatus) {
  if (!mockKillSwitch.active) return status;
  return {
    ...status,
    killSwitchActive: true,
    canArm: false,
    blockers: status.blockers.includes('KILL_SWITCH_ACTIVE')
      ? status.blockers
      : [...status.blockers, 'KILL_SWITCH_ACTIVE' as const],
  };
}

let mockRagAnswerCounter = 0;

/** 화면이 받아 주는 `rag_` + 32 hex 형태로 만든다. mock 안에서만 쓴다. */
function mockRagAnswerId(): string {
  mockRagAnswerCounter += 1;
  return `rag_${mockRagAnswerCounter.toString(16).padStart(32, '0')}`;
}

export async function mockBareTransport<T>(
  path: string,
  method: string,
  body: unknown,
  requestId: string,
): Promise<T> {
  await new Promise((resolve) => setTimeout(resolve, LATENCY_MS));
  const target = path.split('?')[0] ?? path;

  if (target === '/api/v2/rag/corpus-status') {
    return {
      state: 'FULL_READY',
      publicCorpusVersion: 'immutable-v2-mock',
      privateOverlayState: 'ABSENT',
      progressPercent: 100,
      failureCode: null,
    } as T;
  }

  if (target === '/api/v2/rag/consent') {
    if (!mockConsentGranted) {
      throw new ApiFailure(
        { code: 'EXTERNAL_AI_CONSENT_REQUIRED', message: '외부 처리 동의가 필요합니다.' },
        requestId,
      );
    }
    return {
      contractId: 's4-rag-v2-effective-consent-v1',
      schemaVersion: 1,
      consentEventId: 'rce_mock',
      effective: true,
      policyDigest: '0'.repeat(64),
      processorSetDigest: '0'.repeat(64),
      state: 'GRANTED',
    } as T;
  }

  if (target === '/api/v2/rag/consents' && method === 'POST') {
    const action =
      typeof body === 'object' && body !== null
        ? String((body as { action?: unknown }).action ?? '')
        : '';
    mockConsentGranted = action === 'GRANT';
    return undefined as T;
  }

  if (target === '/api/v2/rag/ask' && method === 'POST') {
    if (!mockConsentGranted) {
      throw new ApiFailure(
        { code: 'EXTERNAL_AI_CONSENT_REQUIRED', message: '외부 처리 동의가 필요합니다.' },
        requestId,
      );
    }
    const question =
      typeof body === 'object' && body !== null
        ? String((body as { question?: unknown }).question ?? '')
        : '';
    if (question.trim().length === 0) {
      throw new ApiFailure({ code: 'RAG_VALIDATION_FAILED', message: '질문을 입력하세요.' }, requestId);
    }
    const answer = fixtures.ragAnswerFor(question);
    const generationStatus =
      answer.citations.length > 0 ? 'RETRIEVAL_ONLY' : answer.generationStatus;
    const citations = answer.citations.map((citation, index) => ({
      citationId: `cit_${index + 1}`,
      citationKind: 'PUBLIC_WEB' as const,
      sourceId: citation.sourceId,
      title: citation.title,
      canonicalUrl: citation.canonicalUrl,
      locator: { section: citation.sectionTitle },
      chunkRevisionId: `rag_v2_chk_mock_${index + 1}`,
      sourceRevisionId: `srv_mock_${index + 1}`,
      generationId: 'rgr_mock',
    }));

    // 답변이 실제로 만들어졌을 때만 기록에 쌓는다. 차단된 질문은 남기지 않는다.
    //
    // 화면(`loadRecentQuestions`)은 `rag_` + 32 hex 형태의 id 만 받아 준다. 픽스처의 v1 id
    // (`rag_demo_answer_000001`)는 그 형태가 아니라, v2 기록에는 형태를 맞춘 id 를 따로 만든다.
    const v2AnswerId = answer.citations.length > 0 ? mockRagAnswerId() : null;
    if (v2AnswerId) {
      const now = new Date();
      mockRagHistory.unshift({
        answerId: v2AnswerId,
        question,
        answer: null,
        generationStatus,
        citations,
        createdAt: now.toISOString(),
        expiresAt: new Date(now.getTime() + 30 * 24 * 60 * 60_000).toISOString(),
      });
    }

    return {
      requestId,
      answerId: v2AnswerId,
      generationStatus,
      answer: null,
      citationCoverage: answer.citations.length > 0 ? 1 : 0,
      retrievalFailure: answer.citations.length === 0 && !answer.generationStatus.startsWith('BLOCKED'),
      guardrailFlags: answer.guardrailFlags,
      citations,
    } as T;
  }

  if (target === '/api/v2/rag/history') {
    return {
      items: mockRagHistory.map((entry) => ({
        answerId: entry.answerId,
        createdAt: entry.createdAt,
        expiresAt: entry.expiresAt,
        generationStatus: entry.generationStatus,
      })),
      nextCursor: null,
    } as T;
  }

  if (target.startsWith('/api/v2/rag/history/')) {
    const answerId = target.slice('/api/v2/rag/history/'.length);
    const index = mockRagHistory.findIndex((entry) => entry.answerId === answerId);
    if (index < 0) {
      throw new ApiFailure(
        { code: 'NOT_FOUND', message: '해당 질문 기록을 찾을 수 없습니다.' },
        requestId,
      );
    }
    if (method === 'DELETE') {
      mockRagHistory.splice(index, 1);
      return undefined as T;
    }
    return mockRagHistory[index] as T;
  }

  throw new ApiFailure(
    { code: 'NOT_FOUND', message: `mock 경로가 정의되지 않았습니다: ${target}` },
    requestId,
  );
}

export async function mockTransport<T>(
  path: string,
  method: string,
  body: unknown,
  requestId: string,
): Promise<ApiEnvelope<T>> {
  await new Promise((resolve) => setTimeout(resolve, LATENCY_MS));

  const [route, query] = path.split('?');
  const target = route ?? path;

  if (target.startsWith('/api/v1/dashboard/') && query) {
    return fail('VALIDATION_ERROR', 'Dashboard endpoint는 query parameter를 받지 않습니다.', requestId);
  }

  if (target === '/api/v1/auth/login' && method === 'POST') {
    return ok(
      {
        accessToken: 'mock.token.not-a-real-jwt',
        tokenType: 'Bearer',
        expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
        user: { userId: 'usr_mock', username: 'demo-user', role: 'USER' as const },
      },
      requestId,
    ) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/system/health') return ok(fixtures.health, requestId) as ApiEnvelope<T>;

  if (target === '/api/v1/instruments/display' && method === 'GET') {
    return ok(fixtures.instrumentDisplayCatalog, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v2/automation/status' && method === 'GET') {
    // Kill Switch 는 `mockKillSwitch` 하나만 진실이다. 픽스처의 고정값을 그대로 내보내면
    // 방금 켠 것이 자동운용 화면에는 반영되지 않아, 같은 사실이 화면 두 곳에서 어긋난다.
    return ok(killSwitchAware(fixtures.automationStatus), requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v2/automation/policy' && method === 'PUT') {
    const request = body as
      | {
          expectedVersion?: number;
          capitalLimitKrw?: number;
          stopLossBps?: number;
          takeProfitBps?: number;
        }
      | undefined;
    const { capitalLimitKrw, stopLossBps, takeProfitBps } = request ?? {};
    if (request?.expectedVersion !== fixtures.automationPolicy.version) {
      return fail('CONFLICT', '자동운용 정책 버전이 맞지 않습니다.', requestId);
    }
    if (
      typeof capitalLimitKrw !== 'number' ||
      capitalLimitKrw < 10_000 ||
      capitalLimitKrw > 10_000_000_000 ||
      capitalLimitKrw % 10_000 !== 0 ||
      typeof stopLossBps !== 'number' ||
      stopLossBps < 100 ||
      stopLossBps > 1500 ||
      typeof takeProfitBps !== 'number' ||
      takeProfitBps < 200 ||
      takeProfitBps > 3000 ||
      takeProfitBps <= stopLossBps
    ) {
      return fail('VALIDATION_ERROR', '자동운용 정책 값이 허용 범위를 벗어났습니다.', requestId);
    }
    const presetId: AutomationPresetId =
      stopLossBps === 300 && takeProfitBps === 500
        ? 'conservative'
        : stopLossBps === 500 && takeProfitBps === 1000
          ? 'balanced'
          : stopLossBps === 800 && takeProfitBps === 1500
            ? 'aggressive'
            : 'custom';
    const policy = {
      ...fixtures.automationPolicy,
      version: fixtures.automationPolicy.version + 1,
      presetId,
      capitalLimitKrw,
      stopLossBps,
      takeProfitBps,
      updatedAt: new Date().toISOString(),
    };
    fixtures.replaceAutomationPolicy(policy);
    return ok(policy, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v2/automation/arm' && method === 'POST') {
    return fail(
      'CONFLICT',
      'BLOCKED_INCOMPLETE_RISK_BALANCE: 완전한 온라인 위험 잔고 근거가 없어 시작할 수 없습니다.',
      requestId,
    );
  }

  if (target === '/api/v2/automation/runs' && method === 'GET') {
    return ok(fixtures.automationRuns, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v2/automation/positions' && method === 'GET') {
    return ok(fixtures.automationPositions, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/automation/status' && method === 'GET') {
    return ok(
      {
        contractId: 'automation-control.v1' as const,
        controlState: fixtures.automationStatus.controlState,
        projectionState: fixtures.automationStatus.projectionState,
        version: fixtures.automationStatus.controlVersion,
        brokerageMode: fixtures.automationStatus.brokerageMode,
        principleId: fixtures.principle.principleId,
        strategyId: MOCK_STRATEGY_ID,
        killSwitchActive: mockKillSwitch.active,
        certificationStatus: fixtures.automationStatus.certificationStatus,
      },
      requestId,
    ) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/automation/disarm' && method === 'POST') {
    fixtures.automationStatus.controlState = 'DISARMED';
    fixtures.automationStatus.projectionState = 'DISARMED';
    fixtures.automationStatus.controlVersion += 1;
    return ok(
      {
        contractId: 'automation-control.v1' as const,
        controlState: 'DISARMED' as const,
        projectionState: 'DISARMED' as const,
        version: fixtures.automationStatus.controlVersion,
        brokerageMode: fixtures.automationStatus.brokerageMode,
        principleId: 'prc_00000000',
        strategyId: 'strategy_00000000',
        killSwitchActive: mockKillSwitch.active,
        certificationStatus: fixtures.automationStatus.certificationStatus,
      },
      requestId,
    ) as ApiEnvelope<T>;
  }

  /* ─────────────────────────────── 증권 · 주문 ─────────────────────────── */

  if (target.startsWith('/api/v1/brokerage/mock/accounts/')) {
    const rest = target.slice('/api/v1/brokerage/mock/accounts/'.length).split('/');
    const accountId = rest[0] ?? '';
    const leaf = rest[1] ?? '';
    if (leaf === 'balances') return ok(fixtures.mockBalance, requestId) as ApiEnvelope<T>;
    if (leaf === 'buyable') {
      const symbol = new URLSearchParams(path.split('?')[1] ?? '').get('symbol') ?? '';
      if (!symbol) return fail('VALIDATION_ERROR', '종목을 지정해야 합니다.', requestId);
      const price = fixtures.mockPriceFor(symbol);
      const cash = fixtures.mockBalance.cashKrw;
      return ok(
        {
          accountId,
          brokerageMode: 'KIS_MOCK' as const,
          symbol,
          cashKrw: cash,
          estimatedPrice: price,
          buyableAmountKrw: cash,
          buyableQuantity: Math.floor(cash / price),
          observedAt: new Date().toISOString(),
          sourceVersion: fixtures.mockBalance.sourceVersion,
        },
        requestId,
      ) as ApiEnvelope<T>;
    }
    if (leaf === 'fills') {
      return ok(
        { items: mockFills, nextCursor: null },
        requestId,
      ) as ApiEnvelope<T>;
    }
    return fail('NOT_FOUND', `mock 경로가 정의되지 않았습니다: ${target}`, requestId);
  }

  if (target === '/api/v1/brokerage/mock/orders' && method === 'POST') {
    const request = body as
      | { decisionId?: string; orderIntent?: { symbol?: string; quantity?: number } }
      | undefined;
    if (!request?.decisionId || !ID_PATTERN.decisionId.test(request.decisionId)) {
      return fail('VALIDATION_ERROR', '판정 근거가 필요합니다.', requestId);
    }
    const order = {
      orderId: mockOrderId(),
      accountId: fixtures.mockBalance.accountId,
      brokerageMode: 'KIS_MOCK' as const,
      status: 'SUBMITTED' as const,
      submittedAt: new Date().toISOString(),
    };
    mockOrders.set(order.orderId, { ...order, decisionId: request.decisionId });
    return ok(order, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/brokerage/orders/')) {
    const rest = target.slice('/api/v1/brokerage/orders/'.length).split('/');
    const orderId = rest[0] ?? '';
    const existing = mockOrders.get(orderId);
    if (!existing) return fail('NOT_FOUND', '해당 주문을 찾을 수 없습니다.', requestId);
    if (rest[1] === 'cancel') {
      if (existing.status !== 'SUBMITTED' && existing.status !== 'ACCEPTED') {
        return fail('CONFLICT', '이 주문은 더 이상 취소할 수 없습니다.', requestId);
      }
      const cancelled = { ...existing, status: 'CANCELLED' as const };
      mockOrders.set(orderId, cancelled);
      return ok(cancelled, requestId) as ApiEnvelope<T>;
    }
    return ok(existing, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/decisions/evaluate-order' && method === 'POST') {
    const request = body as
      | { orderIntent?: { symbol?: string; quantity?: number; estimatedAmount?: number } }
      | undefined;
    const intent = request?.orderIntent;
    if (!intent?.symbol || !intent.quantity || !intent.estimatedAmount) {
      return fail('VALIDATION_ERROR', '주문 내용이 온전하지 않습니다.', requestId);
    }
    return ok(fixtures.evaluateOrder(intent.symbol, intent.estimatedAmount), requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/risk/kill-switch') {
    if (method === 'POST') {
      const request = body as { active?: unknown; reason?: unknown } | undefined;
      if (typeof request?.active !== 'boolean') {
        return fail('VALIDATION_ERROR', 'active 는 true 또는 false 여야 합니다.', requestId);
      }
      // 해제는 ADMIN 만 된다(KillSwitchTransitionPolicy.kt:22). mock 은 USER 로 동작하므로
      // 서버와 같은 자리에서 같은 이유로 막는다.
      if (!request.active) {
        return fail('FORBIDDEN', 'Kill Switch 해제는 관리자만 할 수 있습니다.', requestId);
      }
      mockKillSwitch = {
        active: true,
        changedAt: new Date().toISOString(),
        reasonClass: 'USER_MANUAL_STOP',
      };
      return ok(mockKillSwitch, requestId) as ApiEnvelope<T>;
    }
    return ok(mockKillSwitch, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/risk/portfolio') {
    return ok(fixtures.riskPortfolio, requestId, [
      {
        code: 'MISSING_SOURCE',
        message: '일부 포트폴리오 리스크 근거가 없습니다.',
        details: { fields: ['var95', 'cvar95', 'realizedVolatility20d', 'hmmRegime'] },
      },
    ]) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/principle-presets') return ok(fixtures.presetList, requestId) as ApiEnvelope<T>;

  if (target === '/api/v1/principles') {
    if (method === 'POST') {
      const request = body as { title?: string; presetId?: string } | undefined;
      if (!request?.title || !request.presetId) {
        return fail('VALIDATION_ERROR', '제목과 preset 을 모두 지정해야 합니다.', requestId);
      }
      const preset = fixtures.presetList.items.find((item) => item.presetId === request.presetId);
      if (!preset) return fail('VALIDATION_ERROR', '알 수 없는 preset 입니다.', requestId);
      return ok(
        {
          ...fixtures.principle,
          title: request.title,
          presetId: preset.presetId,
          mode: preset.mode,
          rules: preset.defaultRules,
          version: 1,
        },
        requestId,
      ) as ApiEnvelope<T>;
    }
    return ok(fixtures.principleList, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/principles/')) {
    const segments = target.slice('/api/v1/principles/'.length).split('/');
    const principleId = segments[0] ?? '';
    if (!ID_PATTERN.principleId.test(principleId)) {
      return fail('VALIDATION_ERROR', '원칙 ID 형식이 올바르지 않습니다.', requestId);
    }
    if (segments[1] === 'versions') {
      return ok(fixtures.principleHistory, requestId) as ApiEnvelope<T>;
    }
    if (method === 'PUT') {
      const request = body as { expectedVersion?: number; rules?: unknown } | undefined;
      if (request?.expectedVersion !== fixtures.principle.version) {
        return fail('CONFLICT', '원칙 버전이 맞지 않습니다.', requestId);
      }
      return ok(
        { ...fixtures.principle, version: fixtures.principle.version + 1 },
        requestId,
      ) as ApiEnvelope<T>;
    }
    return ok(fixtures.principle, requestId) as ApiEnvelope<T>;
  }

  // 화면(`api.signal`)은 v3 를 부르고 픽스처도 `SignalV3Runtime` 인데 여기만 v2 를 받고 있었다.
  // 그래서 mock 모드에서는 신호 패널이 언제나 NOT_FOUND 였다. 백엔드에는 두 버전이 다 있으므로
  // 둘 다 받아 준다.
  if (target.startsWith('/api/v3/signals/') || target.startsWith('/api/v2/signals/')) {
    const symbol = target.split('/').pop() ?? '';
    const signal = fixtures.signals[symbol];
    if (!signal) return fail('NOT_FOUND', '해당 종목의 신호가 없습니다.', requestId);
    return ok(signal, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/decisions/')) {
    const decisionId = target.split('/').pop() ?? '';
    if (!ID_PATTERN.decisionId.test(decisionId)) {
      return fail('VALIDATION_ERROR', '판정 ID 형식이 올바르지 않습니다.', requestId);
    }
    const decision = fixtures.decisions[decisionId];
    if (!decision) return fail('NOT_FOUND', '해당 판정을 찾을 수 없습니다.', requestId);
    return ok(decision, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/dashboard/risk-results/')) {
    const decisionId = target.split('/').pop() ?? '';

    // `latest` 와 `recent` 는 판정 ID 가 아니라 별도 경로다. 그냥 두면 ID 형식 검사에 걸려
    // VALIDATION_ERROR 가 나고, 주문 검토의 "최근 주문 판정" 패널이 통째로 뜨지 않는다.
    if (decisionId === 'recent' || decisionId === 'latest') {
      const items = fixtures.recentRiskResults();
      if (decisionId === 'latest') {
        const first = items[0];
        if (!first) return fail('NOT_FOUND', '최근 판정이 없습니다.', requestId);
        return ok(first, requestId) as ApiEnvelope<T>;
      }
      return ok({ items }, requestId) as ApiEnvelope<T>;
    }

    if (!ID_PATTERN.decisionId.test(decisionId)) {
      return fail('VALIDATION_ERROR', '판정 ID 형식이 올바르지 않습니다.', requestId);
    }
    const envelope = fixtures.dashboardRiskResults[decisionId];
    if (!envelope) return fail('NOT_FOUND', '해당 판정을 찾을 수 없습니다.', requestId);
    return ok(envelope, requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/dashboard/model-evaluations/latest') {
    const envelope = fixtures.modelEvaluations[LATEST_RUN_ID];
    if (!envelope) return fail('NOT_FOUND', '최근 모델 평가를 찾을 수 없습니다.', requestId);
    return ok(
      { runId: LATEST_RUN_ID, fixtureClass: 'DEMO_OFFLINE', asOf: envelope.asOf },
      requestId,
    ) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/dashboard/backtests/latest') {
    const envelope = fixtures.backtests[LATEST_RUN_ID];
    if (!envelope) return fail('NOT_FOUND', '최근 백테스트를 찾을 수 없습니다.', requestId);
    return ok(
      { runId: LATEST_RUN_ID, fixtureClass: 'DEMO_OFFLINE', asOf: envelope.asOf },
      requestId,
    ) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/dashboard/model-evaluations/')) {
    const runId = target.split('/').pop() ?? '';
    if (!ID_PATTERN.runId.test(runId)) {
      return fail('VALIDATION_ERROR', '실행 ID 형식이 올바르지 않습니다.', requestId);
    }
    const envelope = fixtures.modelEvaluations[runId];
    if (!envelope) return fail('NOT_FOUND', '해당 실행을 찾을 수 없습니다.', requestId);
    return ok(envelope, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/dashboard/backtests/')) {
    const runId = target.split('/').pop() ?? '';
    if (!ID_PATTERN.runId.test(runId)) {
      return fail('VALIDATION_ERROR', '실행 ID 형식이 올바르지 않습니다.', requestId);
    }
    const envelope = fixtures.backtests[runId];
    if (!envelope) return fail('NOT_FOUND', '해당 실행을 찾을 수 없습니다.', requestId);
    return ok(envelope, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/dashboard/rag-sources/')) {
    const answerId = target.split('/').pop() ?? '';
    if (!ID_PATTERN.answerId.test(answerId)) {
      return fail('VALIDATION_ERROR', '답변 ID 형식이 올바르지 않습니다.', requestId);
    }
    const sourceIds =
      fixtures.lastCitations.answerId === answerId ? fixtures.lastCitations.sourceIds : [];
    return ok(fixtures.ragSourcesFor(answerId, sourceIds), requestId) as ApiEnvelope<T>;
  }

  if (target === '/api/v1/rag/sources') return ok(fixtures.ragSourceList, requestId) as ApiEnvelope<T>;

  if (target === '/api/v1/rag/ask' && method === 'POST') {
    const question =
      typeof body === 'object' && body !== null
        ? String((body as { question?: unknown }).question ?? '')
        : '';
    if (question.trim().length === 0) {
      return fail('VALIDATION_ERROR', '질문을 입력하세요.', requestId);
    }
    const answer = fixtures.ragAnswerFor(question);
    fixtures.lastCitations.answerId = answer.answerId;
    fixtures.lastCitations.sourceIds = answer.citations.map((citation) => citation.sourceId);
    return ok(answer, requestId) as ApiEnvelope<T>;
  }

  if (target.startsWith('/api/v1/rag/answers/') && target.endsWith('/feedback')) {
    const answerId = target.slice('/api/v1/rag/answers/'.length, -'/feedback'.length);
    if (!ID_PATTERN.answerId.test(answerId)) {
      return fail('VALIDATION_ERROR', '답변 ID 형식이 올바르지 않습니다.', requestId);
    }
    const request = body as { helpful?: unknown } | undefined;
    if (typeof request?.helpful !== 'boolean') {
      return fail('VALIDATION_ERROR', 'helpful 은 true 또는 false 여야 합니다.', requestId);
    }
    return ok({ answerId, helpful: request.helpful }, requestId) as ApiEnvelope<T>;
  }

  return fail('NOT_FOUND', `mock 경로가 정의되지 않았습니다: ${target}`, requestId);
}
