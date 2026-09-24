import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import nextConfig from '../../next.config.mjs';

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

test('Next forwards only the /api namespace to the matching Compose API service', async () => {
  const config = await readFile(configUrl, 'utf8');
  assert.match(config, /source: '\/api\/:path\*'/);
  assert.match(config, /http:\/\/decision-platform:8080/);
  assert.match(config, /http:\/\/api:8080/);

  const previousProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const previousUpstream = process.env.DECISION_PLATFORM_INTERNAL_URL;
  try {
    delete process.env.DECISION_PLATFORM_INTERNAL_URL;
    for (const product of ['demo', 'full']) {
      process.env.NEXT_PUBLIC_MARS_PRODUCT = product;
      assert.deepEqual(await nextConfig.rewrites(), [
        { source: '/api/:path*', destination: 'http://api:8080/api/:path*' },
      ]);
    }
    process.env.NEXT_PUBLIC_MARS_PRODUCT = 'local';
    assert.deepEqual(await nextConfig.rewrites(), [
      { source: '/api/:path*', destination: 'http://decision-platform:8080/api/:path*' },
    ]);
  } finally {
    if (previousProduct === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previousProduct;
    if (previousUpstream === undefined) delete process.env.DECISION_PLATFORM_INTERNAL_URL;
    else process.env.DECISION_PLATFORM_INTERNAL_URL = previousUpstream;
  }
});

test('protected health is requested only after live authentication', async () => {
  const endpoints = await readFile(endpointsUrl, 'utf8');
  const statusBar = await readFile(statusBarUrl, 'utf8');
  assert.match(endpoints, /health\(\)[\s\S]*apiFetch<SystemHealthResponse>\('\/api\/v1\/system\/health'\)/);
  assert.doesNotMatch(endpoints, /system\/health'[\s\S]{0,40}anonymous: true/);
  assert.match(statusBar, /mock \|\| authenticated/);
});

test('RAG v2 answers carry their own citations without a second projection hop', async () => {
  const viewModel = await readFile(ragViewModelUrl, 'utf8');
  // v2는 인용을 응답에 직접 담는다. 두 번째 홉을 부르면 answerId가 null인 경우에
  // 없는 투영을 요청하게 된다.
  assert.doesNotMatch(viewModel, /api\.dashboardRagSources/);
  assert.match(viewModel, /api\.ragV2Ask/);
  assert.match(viewModel, /items\.length === 0/);
});

test('RAG v2 asks only after an explicit external consent', async () => {
  const viewModel = await readFile(ragViewModelUrl, 'utf8');
  assert.match(viewModel, /api\.ragV2RecordConsent/);
  assert.match(viewModel, /EXTERNAL_AI_RAG_V2/);
});
