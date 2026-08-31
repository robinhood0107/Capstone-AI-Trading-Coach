import { lstatSync, readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';

import {
  TeamAClient,
  teamAOperations,
  type Components,
  type TeamAOperationId,
  type TeamARequests,
  type TeamAResult,
} from '../../src/shared/api/generated/p1-team-a-client.v3';

const USER_ID = 'usr_demo_user';
const PAPER_ACCOUNT_ID = 'acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const KIS_ACCOUNT_ID = 'acct_cccccccccccccccccccccccccccccccc';
const RAG_FIXTURE_ID = 'rag_team_a_fixture_0001';
const TEAM_B_RUN_ID = 'demo_s8_fake_e2e_0001';
const AUTOMATION_RUN_ID = 'auto_run_team_a_news_veto_0001';

type Envelope<T> = {
  readonly success?: boolean;
  readonly data?: T | null;
  readonly error?: unknown;
};

class AcceptanceTracker {
  private readonly observed = new Map<TeamAOperationId, number>();

  record(operationId: TeamAOperationId, status: number): void {
    const prior = this.observed.get(operationId);
    if (prior !== undefined && prior !== status) {
      throw new Error(`${operationId} returned inconsistent success statuses.`);
    }
    this.observed.set(operationId, status);
  }

  verify(): void {
    const expected = Object.keys(teamAOperations).sort();
    const actual = [...this.observed.keys()].sort();
    expect(actual).toEqual(expected);
    expect(actual).toHaveLength(45);
  }
}

function secret(pathValue: string | undefined, label: string): string {
  if (!pathValue) throw new Error(`${label} file is required.`);
  const metadata = lstatSync(pathValue);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size < 1 || metadata.size > 1024) {
    throw new Error(`${label} file boundary is invalid.`);
  }
  return readFileSync(pathValue, 'utf8').trimEnd();
}

function data<T>(body: Envelope<T>, operationId: TeamAOperationId): T {
  if (body.success !== true || body.data === null || body.data === undefined || body.error) {
    throw new Error(`${operationId} returned an invalid success envelope.`);
  }
  return body.data;
}

function key(purpose: string): string {
  return `teama.${purpose}.${crypto.randomUUID().replaceAll('-', '')}`;
}

test('owner backend satisfies the exact Team A 45-operation live Spring catalog', async () => {
  const dashboardUrl = process.env.P1_DASHBOARD_URL ?? 'http://127.0.0.1:3000';
  const userPassword = secret(process.env.P1_USER_PASSWORD_FILE, 'user password');
  const adminPassword = secret(process.env.P1_ADMIN_PASSWORD_FILE, 'admin password');
  const client = new TeamAClient({ baseUrl: dashboardUrl });
  const tracker = new AcceptanceTracker();
  let userToken: string | null = null;
  let adminToken: string | null = null;
  let initialKillSwitchActive: boolean | null = null;
  let automationVersion: number | null = null;

  async function call<K extends TeamAOperationId>(
    operationId: K,
    input: TeamARequests[K],
  ): Promise<TeamAResult<K>> {
    const result = await client.call(operationId, input);
    tracker.record(operationId, result.status);
    return result;
  }

  try {
    const login = await call('login', { body: { username: 'demo-user', password: userPassword } });
    const loggedIn = data<Components['LoginResponse']>(login.body, 'login');
    expect(loggedIn.user?.userId).toBe(USER_ID);
    if (typeof loggedIn.accessToken !== 'string') throw new Error('login omitted its access token.');
    userToken = loggedIn.accessToken;
    client.setAccessToken(userToken);

    await call('health', {});
    const presets = await call('listPrinciplePresets', {});
    const presetData = data<Components['PrinciplePresetListData']>(presets.body, 'listPrinciplePresets');
    expect(presetData.items.map((item) => item.presetId)).toContain('balanced');

    const created = await call('createPrinciple', {
      body: { presetId: 'balanced', title: 'Team A acceptance created principle' },
    });
    const principle = data<Components['PrincipleCurrent']>(created.body, 'createPrinciple');
    await call('listPrinciples', { query: {} });
    await call('getPrinciple', { path: { principleId: principle.principleId } });
    const updated = await call('updatePrinciple', {
      path: { principleId: principle.principleId },
      body: {
        expectedVersion: principle.version,
        mode: principle.mode,
        rules: principle.rules,
        status: 'ACTIVE',
        title: 'Team A acceptance updated principle',
      },
    });
    const currentPrinciple = data<Components['PrincipleCurrent']>(updated.body, 'updatePrinciple');

    await call('getPortfolio', {});
    const initialKill = await call('getKillSwitch', {});
    initialKillSwitchActive = data<Components['S24KillSwitchState']>(initialKill.body, 'getKillSwitch').active;
    await call('changeKillSwitch', {
      body: { active: true, reason: 'Team A acceptance safety check' },
      idempotencyKey: key('killon'),
    });

    client.clearAccessToken();
    const adminLogin = await call('login', { body: { username: 'demo-admin', password: adminPassword } });
    const admin = data<Components['LoginResponse']>(adminLogin.body, 'login');
    if (typeof admin.accessToken !== 'string') throw new Error('admin login omitted its access token.');
    adminToken = admin.accessToken;
    client.setAccessToken(adminToken);
    await call('changeKillSwitch', {
      body: { active: false },
      idempotencyKey: key('killoff'),
    });
    client.setAccessToken(userToken);

    await call('read', { path: { symbol: '005930' } });
    await call('listSources', {});
    await call('record', {
      body: {
        action: 'GRANT',
        consentType: 'EXTERNAL_AI_RAG_V1',
        policyVersion: 'EXTERNAL_AI_RAG_V1',
      },
    });
    const asked = await call('ask', {
      body: { answerMode: 'CONCISE', question: '분산투자의 기본 원칙을 설명해 주세요.', topics: ['RISK'] },
      idempotencyKey: key('ragask'),
    });
    const answer = data<Components['S44RagAnswer']>(asked.body, 'ask');
    await call('feedback', { path: { answerId: answer.answerId }, body: { helpful: true } });
    await call('rag', { path: { answerId: RAG_FIXTURE_ID } });
    await call('modelEvaluation', { path: { runId: TEAM_B_RUN_ID } });
    await call('backtest', { path: { runId: TEAM_B_RUN_ID } });

    const orderIntent = {
      symbol: '005930',
      side: 'BUY',
      orderType: 'MARKET',
      quantity: 1,
      estimatedPrice: 70_000,
      estimatedAmount: 70_000,
      timeframe: '1d',
      strategyId: 'team-a-acceptance-v1',
    } as const;
    const evaluated = await call('evaluateOrder', {
      body: { principleId: currentPrinciple.principleId, portfolioSource: 'KIS_MOCK', orderIntent },
      idempotencyKey: key('decision'),
    });
    const decision = data<Components['S23Decision']>(evaluated.body, 'evaluateOrder');
    if (!decision.riskDecision.canSubmitOrder) {
      throw new Error('evaluateOrder fixture was not orderable.');
    }
    await call('getDecision', { path: { decisionId: decision.decisionId } });
    await call('risk', { path: { decisionId: decision.decisionId } });
    await call('getMockBalance', { path: { accountId: KIS_ACCOUNT_ID } });
    await call('getMockBuyable', {
      path: { accountId: KIS_ACCOUNT_ID },
      query: { symbol: '005930', price: 70_000 },
    });
    const submitted = await call('submitMockOrder', {
      body: {
        decisionId: decision.decisionId,
        orderIntent,
        userAcknowledgement: { warningsAccepted: true },
      },
      idempotencyKey: key('submit'),
    });
    const order = data<Components['S31MockOrderSubmitted']>(submitted.body, 'submitMockOrder');
    await call('getOrder', { path: { orderId: order.orderId } });
    await call('cancelOrder', {
      path: { orderId: order.orderId },
      body: '{}',
      idempotencyKey: key('cancel'),
    });
    const now = Date.now();
    await call('mockFills', {
      path: { accountId: KIS_ACCOUNT_ID },
      query: {
        from: new Date(now - 7 * 86_400_000).toISOString().slice(0, 10),
        to: new Date(now).toISOString().slice(0, 10),
      },
    });

    const status = await call('getAutomationStatus', {});
    const control = data<Components['AutomationControl']>(status.body, 'getAutomationStatus');
    expect(control.controlState).toBe('DISARMED');
    const armed = await call('armAutomation', {
      body: {
        brokerageMode: 'INTERNAL_PAPER',
        accountId: PAPER_ACCOUNT_ID,
        principleId: currentPrinciple.principleId,
        strategyId: 'strategy_team_a_acceptance',
        expectedVersion: control.version,
      },
      idempotencyKey: key('arm'),
    });
    automationVersion = data<Components['AutomationControl']>(armed.body, 'armAutomation').version;
    const runs = await call('listAutomationRuns', { query: { size: 20 } });
    const runPage = data<Components['AutomationRunPage']>(runs.body, 'listAutomationRuns');
    expect(runPage.items.some((item) => item.runId === AUTOMATION_RUN_ID && item.state === 'NEWS_VETOED')).toBe(true);
    const disarmed = await call('disarmAutomation', {
      body: { expectedVersion: automationVersion },
      idempotencyKey: key('disarm'),
    });
    automationVersion = data<Components['AutomationControl']>(disarmed.body, 'disarmAutomation').version;

    const statusV2 = await call('getAutomationStatusV2', {});
    const automationV2 = data<Components['AutomationStatusV2']>(statusV2.body, 'getAutomationStatusV2');
    expect(automationV2.canArm).toBe(false);
    expect(automationV2.blockers).toContain('BLOCKED_INCOMPLETE_RISK_BALANCE');
    const policyResult = await call('putAutomationPolicyV2', {
      body: {
        capitalLimitKrw: 1_000_000,
        stopLossBps: 500,
        takeProfitBps: 1000,
        expectedVersion: automationV2.policy?.version ?? 0,
      },
      idempotencyKey: key('policyv2'),
    });
    const policyV2 = data<Components['AutomationPolicyV2']>(policyResult.body, 'putAutomationPolicyV2');
    expect(policyV2.presetId).toBe('balanced');
    const blockedArm = await call('armAutomationV2', {
      body: {
        accountId: KIS_ACCOUNT_ID,
        policyId: policyV2.policyId,
        expectedPolicyVersion: policyV2.version,
        expectedControlVersion: automationV2.controlVersion,
      },
      idempotencyKey: key('armv2blocked'),
    });
    // v2 arm은 kill switch와 activation gate를 risk balance보다 먼저 닫으므로 일반 409로 끝난다.
    // 어떤 blocker가 열려 있는지는 status가 단일 소스이며 위에서 이미 검증했다.
    expect(blockedArm.status).toBe(409);
    await call('listAutomationRunsV2', { query: { size: 20 } });
    await call('listAutomationPositionsV2', {});

    const strongSettings = await fetch(`${dashboardUrl}/api/v2/strong-llm/settings`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${userToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        provider: 'vertex',
        fallbackProvider: null,
        modelId: 'gemini-3.5-flash',
        fallbackModelId: null,
        baseUrl: null,
        fallbackBaseUrl: null,
        answerLanguage: 'ko',
        dailyGenerateCallCap: 50,
        aiJudgementEnabled: false,
        thinkingLevel: 'low',
      }),
    });
    tracker.record('putStrongLlmSettings', strongSettings.status);
    expect(strongSettings.status).toBe(200);

    const statusV3 = await call('getAutomationStatusV3', {});
    const automationV3 = data<Components['AutomationStatusV3']>(statusV3.body, 'getAutomationStatusV3');
    expect(automationV3.marketHistoryStatus).toBe('EMPTY');
    const policyV3Result = await call('putAutomationPolicyV3', {
      body: {
        capitalLimitKrw: 1_000_000,
        stopLossBps: 500,
        takeProfitBps: 1000,
        maxHoldingSessions: 60,
        atrPeriod: 22,
        atrMultiplierMilli: 3000,
        modelSellEnabled: true,
        expectedVersion: policyV2.version,
      },
      idempotencyKey: key('policyv3'),
    });
    const policyV3 = data<Components['AutomationPolicyV3']>(policyV3Result.body, 'putAutomationPolicyV3');
    expect(policyV3.presetId).toBe('balanced');
    const blockedArmV3 = await call('armAutomationV3', {
      body: {
        accountId: KIS_ACCOUNT_ID,
        policyId: policyV3.policyId,
        expectedPolicyVersion: policyV3.version,
        expectedControlVersion: automationV3.controlVersion,
      },
      idempotencyKey: key('armv3blocked'),
    });
    expect(blockedArmV3.status).toBe(409);
    await call('listAutomationRunsV3', { query: { size: 20 } });
    await call('getAutomationRunV3', { path: { runId: AUTOMATION_RUN_ID } });
    await call('listAutomationPositionsV3', {});

    await call('createJournal', {
      body: {
        title: 'Team A acceptance journal',
        content: '실제 Spring API 45개 수용성 검증 기록입니다.',
        tags: ['acceptance', 'risk'],
        links: {
          decisionId: decision.decisionId,
          ragAnswerId: answer.answerId,
          orderId: order.orderId,
          automationRunId: AUTOMATION_RUN_ID,
        },
      },
      idempotencyKey: key('journal'),
    });
    await call('listJournals', { query: { size: 20 } });
    tracker.verify();
  } finally {
    if (userToken) {
      client.setAccessToken(userToken);
      const status = await client.call('getAutomationStatus', {});
      const control = data<Components['AutomationControl']>(status.body, 'getAutomationStatus');
      if (control.controlState === 'ARMED') {
        await client.call('disarmAutomation', {
          body: { expectedVersion: control.version },
          idempotencyKey: key('finallydisarm'),
        });
      }
      await client.call('record', {
        body: {
          action: 'REVOKE',
          consentType: 'EXTERNAL_AI_RAG_V1',
          policyVersion: 'EXTERNAL_AI_RAG_V1',
        },
      });
    }
    if (initialKillSwitchActive !== null && userToken && adminToken) {
      client.setAccessToken(initialKillSwitchActive ? userToken : adminToken);
      await client.call('changeKillSwitch', {
        body: { active: initialKillSwitchActive, ...(initialKillSwitchActive ? { reason: 'Team A acceptance state restore' } : {}) },
        idempotencyKey: key('finallykill'),
      });
    }
    client.clearAccessToken();
  }
});
