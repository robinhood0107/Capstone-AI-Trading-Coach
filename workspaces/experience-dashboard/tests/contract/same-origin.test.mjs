import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const clientUrl = new URL('../../src/shared/api/client.ts', import.meta.url);
const configUrl = new URL('../../next.config.mjs', import.meta.url);
const endpointsUrl = new URL('../../src/shared/api/endpoints.ts', import.meta.url);
const statusBarUrl = new URL('../../src/shared/ui/StatusBar.tsx', import.meta.url);
const ragViewModelUrl = new URL('../../src/features/rag-source/viewModel.ts', import.meta.url);

test('live browser calls stay on the same origin', async () => {
  const client = await readFile(clientUrl, 'utf8');
  assert.match(client, /NEXT_PUBLIC_API_BASE_URL \?\? ''/);
  assert.doesNotMatch(client, /127\.0\.0\.1:8080/);
});

test('Next forwards only the /api namespace to decision-platform', async () => {
  const config = await readFile(configUrl, 'utf8');
  assert.match(config, /source: '\/api\/:path\*'/);
  assert.match(config, /http:\/\/decision-platform:8080/);
});

test('protected health is requested only after live authentication', async () => {
  const endpoints = await readFile(endpointsUrl, 'utf8');
  const statusBar = await readFile(statusBarUrl, 'utf8');
  assert.match(endpoints, /health\(\)[\s\S]*apiFetch<SystemHealthResponse>\('\/api\/v1\/system\/health'\)/);
  assert.doesNotMatch(endpoints, /system\/health'[\s\S]{0,40}anonymous: true/);
  assert.match(statusBar, /mock \|\| authenticated/);
});

test('fixture RAG without citations does not request a missing dashboard projection', async () => {
  const viewModel = await readFile(ragViewModelUrl, 'utf8');
  assert.match(viewModel, /answer\.citations\.length === 0/);
  assert.match(viewModel, /else \{[\s\S]*api\.dashboardRagSources\(answer\.answerId\)/);
});
