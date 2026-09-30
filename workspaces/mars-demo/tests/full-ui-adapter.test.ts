import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { tmpdir } from 'node:os';
import test from 'node:test';
import { NextRequest } from 'next/server';
import { apiFetch as demoApiFetch } from '../src/adapters/client';
import { closeDatabase } from '../src/server/store';
import { dispatchFullUiApi } from '../src/server/full-ui-adapter';
import { DEMO_SESSION_COOKIE, issueDemoSession } from '../src/server/session';
import { sessionHash } from '../src/server/session';
import { showcaseMetadata } from '../src/server/ledger';
import { initialJournalEntries } from '../src/server/seed-journals';
import { POST as createVisitorSession } from '../src/app/api/demo/session/route';
import { POST as askDemoAgent } from '../src/app/api/demo/agent/route';
import signalFixture from '../data/signals.v1.json';

const tempRoot = mkdtempSync(path.join(tmpdir(), 'mars-full-ui-adapter-'));
const signingKeyFile = path.join(tempRoot, 'session.key');
const databaseFile = path.join(tempRoot, 'state.sqlite');
writeFileSync(signingKeyFile, Buffer.alloc(32, 0x42), { mode: 0o600 });

const environmentBefore = {
  keyFile: process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE,
  database: process.env.MARS_DEMO_STATE_DB_PATH,
  vertexFile: process.env.MARS_DEMO_VERTEX_SERVICE_ACCOUNT_FILE,
};
process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE = signingKeyFile;
process.env.MARS_DEMO_STATE_DB_PATH = databaseFile;
delete process.env.MARS_DEMO_VERTEX_SERVICE_ACCOUNT_FILE;

function apiRequest(pathname: string, token: string, method = 'GET', body?: unknown) {
  const headers = new Headers({
    Host: 'localhost:3021',
    Origin: 'http://localhost:3021',
    Cookie: `${DEMO_SESSION_COOKIE}=${token}`,
  });
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  return new NextRequest(`http://localhost:3021${pathname}`, {
    method,
    headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

async function call(pathname: string, token: string, method = 'GET', body?: unknown) {
  const routeParts = new URL(pathname, 'http://localhost:3021').pathname.replace(/^\/api\//, '').split('/');
  return dispatchFullUiApi(apiRequest(pathname, token, method, body), routeParts);
}

test('FULL pages share fixture reads while arm and Agent consent stay in each signed DEMO session', async () => {
  const first = issueDemoSession();
  const second = issueDemoSession();

  const portfolio = await call('/api/v1/risk/portfolio', first.token);
  assert.equal(portfolio.status, 200);
  const portfolioBody = await portfolio.json() as { success: boolean; data: { portfolioValue: number } };
  assert.equal(portfolioBody.success, true);
  assert.ok(portfolioBody.data.portfolioValue > 0);

  const armed = await call('/api/v3/automation/arm', first.token, 'POST', {});
  assert.equal(armed.status, 200);
  const firstStatus = await call('/api/v3/automation/status', first.token);
  const secondStatus = await call('/api/v3/automation/status', second.token);
  assert.equal((await firstStatus.json() as { data: { controlState: string } }).data.controlState, 'ARMED');
  assert.equal((await secondStatus.json() as { data: { controlState: string } }).data.controlState, 'DISARMED');

  const firstConsent = await call('/api/v2/rag/consents', first.token, 'POST', { action: 'GRANT' });
  assert.equal(firstConsent.status, 204);
  const consentA = await call('/api/v2/rag/consent', first.token);
  const consentB = await call('/api/v2/rag/consent', second.token);
  assert.equal((await consentA.json() as { effective: boolean }).effective, true);
  const consentBBody = await consentB.json() as { effective: boolean; processorNames?: string };
  assert.equal(consentBBody.effective, false);
  assert.equal(consentBBody.processorNames, 'Google Vertex AI');

  const agent = await call('/api/v2/rag/ask', first.token, 'POST', { question: '시장 위험 기준을 설명해 주세요.' });
  assert.equal(agent.status, 503);
  const usage = await call('/api/v2/rag/corpus-status', first.token);
  const usageBody = await usage.json() as { agentUsage: { sessionCalls: number; globalCalls: number; estimatedGlobalCostUsd: number } };
  assert.equal(usageBody.agentUsage.sessionCalls, 0);
  assert.equal(usageBody.agentUsage.globalCalls, 0);
  assert.equal(usageBody.agentUsage.estimatedGlobalCostUsd, 0);
});

test('holdings, fills, decisions, runs, journal seeds, and report reconcile to one historical ledger', async () => {
  const visitor = issueDemoSession();
  const receipt = showcaseMetadata().final;
  const balance = await (await call('/api/v1/brokerage/mock/accounts/current/balances', visitor.token)).json() as {
    data: { cashKrw: number; portfolioEquityKrw: number; positions: { symbol: string; marketValueKrw: number }[] };
  };
  assert.equal(balance.data.cashKrw, receipt.cash);
  assert.equal(balance.data.portfolioEquityKrw, receipt.equity);
  assert.equal(balance.data.positions.length, receipt.openPositionCount);
  assert.equal(balance.data.cashKrw + balance.data.positions.reduce((sum, item) => sum + item.marketValueKrw, 0), receipt.equity);

  const positions = await (await call('/api/v2/automation/positions', visitor.token)).json() as {
    data: { realizedSummary: { closedPositionCount: number; realizedPnlKrw: number; winningPositionCount: number; losingPositionCount: number }; items: unknown[] };
  };
  assert.equal(positions.data.items.length, receipt.openPositionCount);
  assert.equal(positions.data.realizedSummary.closedPositionCount, receipt.closedPositionCount);
  assert.equal(positions.data.realizedSummary.realizedPnlKrw, receipt.realizedPnl);
  assert.equal(positions.data.realizedSummary.winningPositionCount, 1);
  assert.equal(positions.data.realizedSummary.losingPositionCount, 1);

  const fills = await (await call('/api/v1/brokerage/mock/accounts/current/fills', visitor.token)).json() as {
    data: { items: { orderId: string; side: string; fillAmountKrw: number }[] };
  };
  assert.equal(fills.data.items.length, receipt.fillCount);
  assert.equal(fills.data.items.filter((item) => item.side === 'SELL').length, receipt.winningSaleCount + receipt.losingSaleCount);
  const order = await (await call(`/api/v1/brokerage/orders/${fills.data.items[0]!.orderId}`, visitor.token)).json() as {
    data: { status: string };
  };
  assert.equal(order.data.status, 'FILLED');

  const decisions = await (await call('/api/v1/dashboard/risk-results/recent', visitor.token)).json() as {
    data: { items: { decisionId: string; action: string }[] };
  };
  assert.equal(decisions.data.items.length, receipt.orderCount);
  assert.ok(decisions.data.items.every((item) => item.action === 'ALLOW'));
  const dashboardDecision = await (await call(`/api/v1/dashboard/risk-results/${decisions.data.items[0]!.decisionId}`, visitor.token)).json() as {
    data: { viewState: string; view: { action: string } };
  };
  assert.equal(dashboardDecision.data.viewState, 'READY');
  assert.equal(dashboardDecision.data.view.action, 'ALLOW');
  const detail = await (await call(`/api/v1/decisions/${decisions.data.items[0]!.decisionId}`, visitor.token)).json() as {
    data: { decisionId: string };
  };
  assert.equal(detail.data.decisionId, decisions.data.items[0]!.decisionId);

  const runs = await (await call('/api/v3/automation/runs', visitor.token)).json() as {
    data: { items: { runId: string; state: string }[] };
  };
  const completed = runs.data.items.find((item) => item.state === 'COMPLETED');
  const noOrder = runs.data.items.find((item) => item.state === 'SKIPPED_NO_ACTION');
  assert.ok(completed);
  assert.ok(noOrder);
  const completedDetail = await (await call(`/api/v3/automation/runs/${completed.runId}`, visitor.token)).json() as {
    data: { stageOutcomes: { stage: string; outcome: string }[] };
  };
  assert.ok(completedDetail.data.stageOutcomes.some((item) => item.stage === 'ORDER' && item.outcome === 'PASS'));
  const noOrderDetail = await (await call(`/api/v3/automation/runs/${noOrder.runId}`, visitor.token)).json() as {
    data: { stageOutcomes: { stage: string; outcome: string }[] };
  };
  assert.ok(noOrderDetail.data.stageOutcomes.some((item) => item.stage === 'RULE_BUY' && item.outcome === 'DROPPED'));
  assert.equal(initialJournalEntries(sessionHash(visitor.session.id)).length, 29);

  const report = await (await call('/api/v1/dashboard/performance-reports/latest', visitor.token)).json() as {
    data: { report: { sections: { actualTrading: { openPositionCount: number; closedPositionCount: number; realizedPnlKrw: number } } } };
  };
  assert.equal(report.data.report.sections.actualTrading.openPositionCount, receipt.openPositionCount);
  assert.equal(report.data.report.sections.actualTrading.closedPositionCount, receipt.closedPositionCount);
  assert.equal(report.data.report.sections.actualTrading.realizedPnlKrw, receipt.realizedPnl);
  assert.equal(receipt.realizedPnl + receipt.unrealizedPnl + receipt.dividendCash, receipt.netPnlKrw);
  assert.ok(receipt.netPnlKrw > 0);
});

test('one-click entry starts an independent armed portfolio with editable trade reviews', async () => {
  const response = await createVisitorSession(new NextRequest('http://localhost:3021/api/demo/session', {
    method: 'POST',
    headers: { Host: 'localhost:3021', Origin: 'http://localhost:3021', 'Content-Type': 'application/json' },
    body: '{}',
  }));
  assert.equal(response.status, 200);
  const token = response.cookies.get(DEMO_SESSION_COOKIE)?.value;
  assert.ok(token);
  const status = await (await call('/api/v3/automation/status', token)).json() as { data: { controlState: string } };
  assert.equal(status.data.controlState, 'ARMED');
  const journals = await (await call('/api/v1/journals', token)).json() as {
    data: { items: { journalId: string; tags: string[]; links: { orderId: string | null; automationRunId: string } }[] };
  };
  assert.equal(journals.data.items.length, 29);
  assert.equal(journals.data.items.filter((item) => item.links.orderId).length, showcaseMetadata().final.fillCount);
  assert.ok(journals.data.items.every((item) => item.tags.includes('자동 생성') && item.links.automationRunId));
});

test('all historical runs, report fields, and exact-31 recorded model signals are available', async () => {
  const visitor = issueDemoSession();
  const runs = await (await call('/api/v3/automation/runs?size=40', visitor.token)).json() as { data: { items: { runId: string }[] } };
  assert.equal(runs.data.items.length, 29);
  for (const run of runs.data.items) {
    const response = await call(`/api/v3/automation/runs/${run.runId}`, visitor.token);
    assert.equal(response.status, 200);
    const detail = await response.json() as { data: { stageOutcomes: { stage: string; reasonDetail: string }[] } };
    assert.ok(detail.data.stageOutcomes.some((stage) => stage.stage === 'OBSERVATION' && stage.reasonDetail.includes('장마감 평가액')));
  }
  const backtest = await (await call('/api/v1/dashboard/backtests/demo', visitor.token)).json() as { data: { view: { strategies: { curve: unknown[]; metrics: Record<string, number> }[]; heatmap: unknown[] } } };
  assert.equal(backtest.data.view.strategies.length, 3);
  assert.ok(backtest.data.view.strategies.every((row) => row.curve.length === 29 && Object.values(row.metrics).every(Number.isFinite)));
  assert.equal(backtest.data.view.heatmap.length, 2);
  const models = await (await call('/api/v1/dashboard/model-evaluations/demo', visitor.token)).json() as { data: { view: { models: { status: string; metrics: Record<string, number> }[]; timeline: { value: number }[] } } };
  assert.equal(models.data.view.models.length, 2);
  assert.ok(models.data.view.models.every((row) => row.status === 'AVAILABLE' && Object.values(row.metrics).every(Number.isFinite)));
  assert.ok(models.data.view.timeline.every((point) => point.value > 1_000_000));
  for (const row of signalFixture.rows) {
    const symbol = row.symbol.replace(/\.(KS|KQ)$/, '');
    const signal = await (await call(`/api/v3/signals/${symbol}`, visitor.token)).json() as { data: { sourceSession: string; targetSession: string; composite: { status: string }; components: { ruleBaseline: { status: string }; lstm: { status: string }; hmmRegime: { status: string } } } };
    assert.equal(signal.data.sourceSession, '2026-09-29');
    assert.equal(signal.data.targetSession, '2026-09-30');
    assert.equal(signal.data.composite.status, 'AVAILABLE');
    assert.equal(signal.data.components.ruleBaseline.status, 'AVAILABLE');
    assert.equal(signal.data.components.lstm.status, 'AVAILABLE');
    assert.equal(signal.data.components.hmmRegime.status, 'AVAILABLE');
  }
});

test('the direct Agent route requires the same external-processing consent as the FULL UI route', async () => {
  const visitor = issueDemoSession();
  const response = await askDemoAgent(new NextRequest('http://localhost:3021/api/demo/agent', {
    method: 'POST',
    headers: {
      Host: 'localhost:3021',
      Origin: 'http://localhost:3021',
      Cookie: `${DEMO_SESSION_COOKIE}=${visitor.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ question: '보유 종목을 알려 주세요.' }),
  }));
  assert.equal(response.status, 403);
  assert.equal((await response.json() as { error: { code: string } }).error.code, 'EXTERNAL_AI_CONSENT_REQUIRED');
});

test('personal order stop persists per visitor and blocks arm until resumed', async () => {
  const stoppedVisitor = issueDemoSession();
  const otherVisitor = issueDemoSession();
  const stopped = await call('/api/v2/risk/kill-switch', stoppedVisitor.token, 'POST', { active: true });
  assert.equal(stopped.status, 200);
  const personalState = await call('/api/v2/risk/kill-switch', stoppedVisitor.token);
  assert.equal((await personalState.json() as { data: { active: boolean; effectiveActive: boolean } }).data.active, true);
  const automation = await call('/api/v3/automation/status', stoppedVisitor.token);
  const automationBody = await automation.json() as { data: { killSwitchActive: boolean; canArm: boolean; blockers: string[] } };
  assert.equal(automationBody.data.killSwitchActive, true);
  assert.equal(automationBody.data.canArm, false);
  assert.ok(automationBody.data.blockers.includes('KILL_SWITCH_ACTIVE'));
  assert.equal((await call('/api/v3/automation/arm', stoppedVisitor.token, 'POST', {})).status, 409);
  assert.equal((await (await call('/api/v2/risk/kill-switch', otherVisitor.token)).json() as { data: { active: boolean } }).data.active, false);
  assert.equal((await call('/api/v2/risk/kill-switch', stoppedVisitor.token, 'POST', { active: false })).status, 200);
  assert.equal((await call('/api/v3/automation/status', stoppedVisitor.token)).status, 200);
});

test('invalid Agent environment is isolated from the rest of the FULL screens', async () => {
  const session = issueDemoSession();
  const previousLimit = process.env.MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT;
  process.env.MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT = 'not-a-number';
  try {
    const portfolio = await call('/api/v1/risk/portfolio', session.token);
    assert.equal(portfolio.status, 200);
    const consent = await call('/api/v2/rag/consents', session.token, 'POST', { action: 'GRANT' });
    assert.equal(consent.status, 204);
    const rag = await call('/api/v2/rag/ask', session.token, 'POST', { question: '시장 위험을 설명해 주세요.' });
    assert.equal(rag.status, 503);
    const usage = await call('/api/v2/rag/corpus-status', session.token);
    const body = await usage.json() as { agentUsage: { configured: boolean; sessionCalls: number; globalCalls: number } };
    assert.equal(body.agentUsage.configured, false);
    assert.equal(body.agentUsage.sessionCalls, 0);
    assert.equal(body.agentUsage.globalCalls, 0);
  } finally {
    if (previousLimit === undefined) delete process.env.MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT;
    else process.env.MARS_DEMO_AGENT_GLOBAL_DAILY_LIMIT = previousLimit;
  }
});

test('FULL automation capital reads start as empty and persist through the visitor overlay', async () => {
  const session = issueDemoSession();
  const emptyPolicy = await call('/api/v4/automation/capital-policy', session.token);
  const emptyStatus = await call('/api/v4/automation/capital-status', session.token);
  assert.equal(emptyPolicy.status, 200);
  assert.equal(emptyStatus.status, 200);
  assert.equal((await emptyPolicy.json() as { data: unknown }).data, null);
  assert.equal((await emptyStatus.json() as { data: unknown }).data, null);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    success: true,
    requestId: 'req_demo_empty_01',
    data: null,
    warnings: [],
    error: null,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  try {
    const empty = await demoApiFetch<null>('/api/v4/automation/capital-policy');
    assert.equal(empty.data, null);
  } finally {
    globalThis.fetch = originalFetch;
  }

  const saved = await call('/api/v4/automation/capital-policy', session.token, 'PUT', {
    reinvestRealizedPnl: true,
    expectedVersion: 0,
  });
  assert.equal(saved.status, 200);
  const savedBody = await saved.json() as { data: { version: number; reinvestRealizedPnl: boolean } };
  assert.equal(savedBody.data.version, 1);
  assert.equal(savedBody.data.reinvestRealizedPnl, true);

  const persisted = await call('/api/v4/automation/capital-policy', session.token);
  assert.equal((await persisted.json() as { data: { version: number } }).data.version, 1);
  const capital = await call('/api/v4/automation/capital-status', session.token);
  const capitalBody = await capital.json() as { data: { policyVersion: number; positions: unknown[]; botPositionMarketValueKrw: number; brokerBuyableCashKrw: number } };
  assert.equal(capitalBody.data.policyVersion, 1);
  assert.equal(capitalBody.data.positions.length, showcaseMetadata().final.openPositionCount);
  assert.equal(capitalBody.data.botPositionMarketValueKrw + capitalBody.data.brokerBuyableCashKrw, showcaseMetadata().final.equity);
  const updatedAutomationPolicy = await call('/api/v3/automation/policy', session.token, 'PUT', {
    expectedVersion: 1,
    capitalLimitKrw: 8_500_000,
    stopLossBps: 400,
    takeProfitBps: 900,
  });
  assert.equal(updatedAutomationPolicy.status, 200);
  const capitalPolicyAfterAutomationSave = await call('/api/v4/automation/capital-policy', session.token);
  const capitalPolicyAfterAutomationSaveBody = await capitalPolicyAfterAutomationSave.json() as {
    data: { version: number; reinvestRealizedPnl: boolean };
  };
  assert.equal(capitalPolicyAfterAutomationSaveBody.data.version, 1);
  assert.equal(capitalPolicyAfterAutomationSaveBody.data.reinvestRealizedPnl, true);
  const stale = await call('/api/v4/automation/capital-policy', session.token, 'PUT', {
    reinvestRealizedPnl: false,
    expectedVersion: 0,
  });
  assert.equal(stale.status, 409);
});

test.after(() => {
  closeDatabase(databaseFile);
  if (environmentBefore.keyFile === undefined) delete process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE;
  else process.env.MARS_DEMO_SESSION_SIGNING_KEY_FILE = environmentBefore.keyFile;
  if (environmentBefore.database === undefined) delete process.env.MARS_DEMO_STATE_DB_PATH;
  else process.env.MARS_DEMO_STATE_DB_PATH = environmentBefore.database;
  if (environmentBefore.vertexFile === undefined) delete process.env.MARS_DEMO_VERTEX_SERVICE_ACCOUNT_FILE;
  else process.env.MARS_DEMO_VERTEX_SERVICE_ACCOUNT_FILE = environmentBefore.vertexFile;
  rmSync(tempRoot, { recursive: true, force: true });
});
