import { expect, test, type Page, type Route } from '@playwright/test';
import type { RagV2Answer } from '../../src/shared/api/wire';

const exampleQuestion = '132030 금선물 ETF의 환헤지와 롤오버 위험을 설명해 주세요.';
const storedExample = '저장된 예시 답변';
const citation: RagV2Answer['citations'][number] = {
  citationId: 'cit_1',
  sourceId: 'source_fixture',
  citationKind: 'PUBLIC_WEB',
  title: '합성 출처 문서',
  canonicalUrl: 'https://example.org/rag-source',
  locator: { section: '합성 출처 절' },
  chunkRevisionId: 'chunk_fixture',
  sourceRevisionId: 'revision_fixture',
  generationId: 'generation_fixture',
};

function answer(generationStatus: RagV2Answer['generationStatus'], fields: Partial<RagV2Answer> = {}): RagV2Answer {
  return {
    requestId: 'req_0123456789abcdef0123456789abcdef',
    answerId: null,
    generationStatus,
    answer: null,
    citationCoverage: 0,
    citations: [],
    retrievalFailure: generationStatus === 'RETRIEVAL_FAILURE',
    guardrailFlags: [],
    ...fields,
  };
}

function envelope(data: unknown) {
  return { success: true, requestId: 'req_0123456789abcdef0123456789abcdef', data, warnings: [], error: null };
}

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

async function openRag(
  page: Page,
  granted: boolean,
  ask: (route: Route) => Promise<void>,
  consentWrite?: Promise<void>,
) {
  const asks: unknown[] = [];
  const consentActions: string[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    switch (path) {
      case '/api/v1/auth/refresh':
        return route.fulfill({ json: envelope({
          accessToken: 'fixture-access-token',
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          user: { userId: 'fixture-user', username: 'fixture', role: 'USER' },
        }) });
      case '/api/v2/rag/consent':
        return granted
          ? route.fulfill({ json: { effective: true, state: 'GRANTED' } })
          : route.fulfill({ status: 409, json: { code: 'EXTERNAL_AI_CONSENT_REQUIRED' } });
      case '/api/v2/rag/consents': {
        const { action } = route.request().postDataJSON() as { action: string };
        consentActions.push(action);
        await consentWrite;
        granted = action === 'GRANT';
        return route.fulfill({ status: 204 });
      }
      case '/api/v2/rag/ask':
        asks.push(route.request().postDataJSON());
        return ask(route);
      case '/api/v1/rag/sources':
        return route.fulfill({ json: envelope({ items: [] }) });
      case '/api/v2/rag/corpus-status':
        return route.fulfill({ json: { state: 'FULL_READY' } });
      case '/api/v2/rag/history':
        return route.fulfill({ json: { items: [], nextCursor: null } });
      case '/api/v2/rag/world-news':
        return route.fulfill({ json: { items: [], collections: [], asOf: '2026-01-01T00:00:00Z' } });
      default:
        return route.abort();
    }
  });
  await page.goto('/rag');
  await expect(page.getByText(granted ? '동의 완료' : '동의 필요', { exact: true })).toBeVisible();
  return { asks, consentActions };
}

test.beforeEach(({ baseURL }) => {
  expect(new URL(baseURL!).hostname).toBe('127.0.0.1');
});

test('preset keeps current retrieval-only citations', async ({ page }) => {
  await openRag(page, true, (route) => route.fulfill({
    json: answer('RETRIEVAL_ONLY', { citations: [citation], citationCoverage: 1 }),
  }));
  await page.getByRole('button', { name: exampleQuestion }).click();
  await expect(page.getByRole('heading', { name: '설명 문장 없이 출처만 제공합니다' })).toBeVisible();
  await expect(page.getByText(citation.title, { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '원문 열기' })).toHaveAttribute('href', citation.canonicalUrl!);
  await expect(page.getByText(storedExample, { exact: true })).toHaveCount(0);
  await expect(page.getByText('출처 연결률', { exact: true })).toHaveCount(0);
});

test('empty evidence, generation failure, HTTP failure and rate limit stay distinct', async ({ page }) => {
  const responses = [
    { json: answer('RETRIEVAL_FAILURE') },
    { json: answer('GENERATION_UNAVAILABLE') },
    { status: 503, json: { code: 'RAG_UNAVAILABLE' } },
    { status: 429, json: { code: 'RATE_LIMITED' } },
  ];
  const { asks } = await openRag(page, true, (route) => {
    const response = responses.shift();
    expect(response).toBeDefined();
    return route.fulfill(response);
  });
  const preset = page.getByRole('button', { name: exampleQuestion });
  await expect(page.getByText(/아직 저장된 질문이 없습니다/)).toBeVisible();
  await preset.click();
  await expect.soft(page.getByRole('heading', { name: '충분한 근거를 찾지 못했습니다' })).toBeVisible();
  await expect.soft(page.getByRole('heading', { name: storedExample })).toBeVisible();
  await preset.click();
  await expect.soft(page.getByRole('heading', { name: '설명 생성이 지금은 불가능합니다' })).toBeVisible();
  await expect.soft(page.getByRole('heading', { name: storedExample })).toBeVisible();
  await preset.click();
  await expect.soft(page.getByText('불러오기 실패 · RAG_UNAVAILABLE')).toBeVisible();
  await expect.soft(page.getByRole('heading', { name: storedExample })).toHaveCount(0);
  await page.getByRole('textbox', { name: '질문' }).fill('다른 합성 질문');
  await page.getByRole('button', { name: '자세히' }).click();
  await page.getByRole('button', { name: '다시 조회' }).click();
  await expect(page.getByText('질문 사용량 제한에 걸렸습니다. 잠시 후 다시 질문해 주세요.')).toBeVisible();
  await expect(page.getByRole('heading', { name: storedExample })).toHaveCount(0);
  expect(asks).toHaveLength(4);
  expect(asks[3]).toMatchObject({ question: exampleQuestion, answerMode: 'CONCISE' });
});

test('keyboard submission waits for consent to be saved', async ({ page }) => {
  const consentWrite = deferred();
  const { asks, consentActions } = await openRag(page, false, (route) =>
    route.fulfill({ json: answer('ANSWERED', { answer: '합성 답변입니다.' }) }), consentWrite.promise);
  const question = page.getByRole('textbox', { name: '질문' });
  await question.fill('동의 경계 합성 질문');
  await expect(page.getByRole('button', { name: '물어보기' })).toBeDisabled();
  await expect(page.getByRole('button', { name: exampleQuestion })).toBeDisabled();
  await question.press('Control+Enter');
  await question.press('Meta+Enter');
  try {
    await Promise.all([
      page.waitForRequest('**/api/v2/rag/consents'),
      page.getByRole('button', { name: '동의', exact: true }).click(),
    ]);
    await question.press('Control+Enter');
    await expect(page.getByRole('button', { name: '물어보기' })).toBeDisabled();
  } finally {
    consentWrite.release();
  }
  await expect(page.getByText('동의 완료', { exact: true })).toBeVisible();
  expect(asks).toEqual([]);
  expect(consentActions).toEqual(['GRANT']);
  await question.press('Control+Enter');
  await expect(page.getByText('합성 답변입니다.', { exact: true })).toBeVisible();
  expect(asks).toHaveLength(1);
  expect(asks[0]).toMatchObject({ question: '동의 경계 합성 질문' });
});

for (const lateResponse of ['answer', 'error'] as const) {
  test(`revocation discards a delayed ${lateResponse} across a new grant`, async ({ page }) => {
    const oldAsk = deferred();
    const newAsk = deferred();
    let calls = 0;
    await openRag(page, true, async (route) => {
      if (++calls === 1) {
        await oldAsk.promise;
        if (lateResponse === 'error') return route.fulfill({ status: 503, json: { code: 'RAG_UNAVAILABLE' } });
        return route.fulfill({ json: answer('ANSWERED', { answer: '철회 전 합성 답변', citations: [citation] }) });
      }
      await newAsk.promise;
      return route.fulfill({ json: answer('ANSWERED', { answer: '다시 동의한 뒤의 합성 답변' }) });
    });
    const question = page.getByRole('textbox', { name: '질문' });
    await question.fill('철회 전 합성 질문');
    await Promise.all([
      page.waitForRequest('**/api/v2/rag/ask'),
      page.getByRole('button', { name: '물어보기' }).click(),
    ]);

    try {
      await page.getByRole('button', { name: '철회', exact: true }).click();
      await expect(page.getByText('동의 필요', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: '동의', exact: true }).click();
      await expect(page.getByText('동의 완료', { exact: true })).toBeVisible();
      await question.fill('다시 동의한 뒤의 합성 질문');
      const submit = page.getByRole('button', { name: /^(물어보기|찾는 중)$/ });
      await expect(submit).toBeEnabled();
      await Promise.all([page.waitForRequest('**/api/v2/rag/ask'), submit.click()]);
      const oldResponse = page.waitForResponse('**/api/v2/rag/ask');
      const registryResponse = lateResponse === 'answer' ? page.waitForResponse('**/api/v1/rag/sources') : null;
      oldAsk.release();
      await (await oldResponse).finished();
      if (registryResponse) await (await registryResponse).finished();
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(page.getByRole('button', { name: '찾는 중' })).toBeDisabled();
      await expect(page.getByText('철회 전 합성 답변', { exact: true })).toHaveCount(0);
      await expect(page.getByText('불러오기 실패 · RAG_UNAVAILABLE')).toHaveCount(0);
    } finally {
      oldAsk.release();
      newAsk.release();
    }
    await expect(page.getByText('철회 전 합성 답변', { exact: true })).toHaveCount(0);
    await expect(page.getByText('불러오기 실패 · RAG_UNAVAILABLE')).toHaveCount(0);
    await expect(page.getByText('다시 동의한 뒤의 합성 답변', { exact: true })).toBeVisible();
    expect(calls).toBe(2);
  });
}
