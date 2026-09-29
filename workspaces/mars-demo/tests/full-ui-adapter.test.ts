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
  const routeParts = pathname.replace(/^\/api\//, '').split('/');
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
