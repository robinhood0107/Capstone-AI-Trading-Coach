import { ApiFailure, type ApiEnvelope } from '@/shared/api/envelope';
import type { AutomationPresetId } from '@/shared/api/wire';
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
    return {
      requestId,
      answerId: answer.citations.length > 0 ? answer.answerId : null,
      generationStatus: answer.citations.length > 0 ? 'RETRIEVAL_ONLY' : answer.generationStatus,
      answer: null,
      citationCoverage: answer.citations.length > 0 ? 1 : 0,
      retrievalFailure: answer.citations.length === 0 && !answer.generationStatus.startsWith('BLOCKED'),
      guardrailFlags: answer.guardrailFlags,
      citations: answer.citations.map((citation, index) => ({
        citationId: `cit_${index + 1}`,
        citationKind: 'PUBLIC_WEB' as const,
        sourceId: citation.sourceId,
        title: citation.title,
        canonicalUrl: citation.canonicalUrl,
        locator: { section: citation.sectionTitle },
        chunkRevisionId: `rag_v2_chk_mock_${index + 1}`,
        sourceRevisionId: `srv_mock_${index + 1}`,
        generationId: 'rgr_mock',
      })),
    } as T;
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

  if (target === '/api/v2/automation/status' && method === 'GET') {
    return ok(fixtures.automationStatus, requestId) as ApiEnvelope<T>;
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
        killSwitchActive: fixtures.automationStatus.killSwitchActive,
        certificationStatus: fixtures.automationStatus.certificationStatus,
      },
      requestId,
    ) as ApiEnvelope<T>;
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

  if (target === '/api/v1/principles') return ok(fixtures.principleList, requestId) as ApiEnvelope<T>;

  if (target.startsWith('/api/v1/principles/')) {
    const principleId = target.split('/').pop() ?? '';
    if (!ID_PATTERN.principleId.test(principleId)) {
      return fail('VALIDATION_ERROR', '원칙 ID 형식이 올바르지 않습니다.', requestId);
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

  if (target.startsWith('/api/v2/signals/')) {
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
    if (!ID_PATTERN.decisionId.test(decisionId)) {
      return fail('VALIDATION_ERROR', '판정 ID 형식이 올바르지 않습니다.', requestId);
    }
    const envelope = fixtures.dashboardRiskResults[decisionId];
    if (!envelope) return fail('NOT_FOUND', '해당 판정을 찾을 수 없습니다.', requestId);
    return ok(envelope, requestId) as ApiEnvelope<T>;
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

  return fail('NOT_FOUND', `mock 경로가 정의되지 않았습니다: ${target}`, requestId);
}
