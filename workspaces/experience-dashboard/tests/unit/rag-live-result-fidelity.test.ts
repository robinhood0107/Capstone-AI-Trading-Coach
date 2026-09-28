import assert from 'node:assert/strict';
import test from 'node:test';
import { api } from '../../src/shared/api/endpoints.ts';
import { session } from '../../src/shared/api/session.ts';
import { askRag, RAG_EXAMPLES } from '../../src/features/rag-source/viewModel.ts';

test('live RAG preserves server blocks and retrieved citations instead of substituting cached answers', async () => {
  const previousApiMode = process.env.NEXT_PUBLIC_API_MODE;
  const previousProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const previousFetch = globalThis.fetch;
  const responses = [
    {
      requestId: 'req_blocked_0000000000000000000000000000',
      answerId: null,
      generationStatus: 'BLOCKED_ADVICE',
      answer: null,
      citationCoverage: 0,
      citations: [],
      retrievalFailure: false,
      guardrailFlags: ['DIRECT_ADVICE_BLOCKED'],
    },
    {
      requestId: 'req_retrieval_0000000000000000000000000000',
      answerId: 'rag_0123456789abcdef0123456789abcdef',
      generationStatus: 'RETRIEVAL_ONLY',
      answer: null,
      citationCoverage: 1,
      citations: [
        {
          citationId: 'cit_1',
          sourceId: 'src_fixture_source',
          title: '근거 문서',
          canonicalUrl: 'https://example.org/evidence',
          citationKind: 'PUBLIC_WEB',
          locator: { section: '위험 설명' },
        },
      ],
      retrievalFailure: false,
      guardrailFlags: [],
    },
  ];

  process.env.NEXT_PUBLIC_API_MODE = 'live';
  process.env.NEXT_PUBLIC_MARS_PRODUCT = 'full';
  session.set('fixture-access-token', new Date(Date.now() + 60_000).toISOString(), {
    userId: 'usr_fixture_user',
    username: 'fixture',
    role: 'USER',
  });
  globalThis.fetch = async () => {
    const body = responses.shift();
    assert.ok(body, 'each request should receive its server response');
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    const blocked = await api.ragV2Ask({
      question: '삼성전자 지금 사도 되나요?',
      answerMode: 'CONCISE',
    });
    assert.equal(blocked.generationStatus, 'BLOCKED_ADVICE');
    assert.equal(blocked.answer, null);
    assert.deepEqual(blocked.citations, []);

    const retrievalOnly = await api.ragV2Ask({
      question: '금 ETF의 롤오버 위험은 무엇인가요?',
      answerMode: 'CONCISE',
    });
    assert.equal(retrievalOnly.generationStatus, 'RETRIEVAL_ONLY');
    assert.equal(retrievalOnly.answer, null);
    assert.equal(retrievalOnly.citations[0]?.sourceId, 'src_fixture_source');

    globalThis.fetch = async () => {
      throw new Error('fixture transport failure');
    };
    await assert.rejects(
      api.ragV2Ask({
        question: '삼성전자 지금 사도 되나요?',
        answerMode: 'CONCISE',
      }),
    );
  } finally {
    session.clear();
    globalThis.fetch = previousFetch;
    if (previousApiMode === undefined) delete process.env.NEXT_PUBLIC_API_MODE;
    else process.env.NEXT_PUBLIC_API_MODE = previousApiMode;
    if (previousProduct === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previousProduct;
  }
});

test('preset RAG buttons use their fixed cited answer only as a final fallback', async () => {
  const previousApiMode = process.env.NEXT_PUBLIC_API_MODE;
  const previousProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const previousFetch = globalThis.fetch;
  const example = RAG_EXAMPLES[0]!;
  let responseMode: 'NO_EVIDENCE' | 'NETWORK_ERROR' | 'BLOCKED' | 'CONSENT_BLOCK' = 'NO_EVIDENCE';
  assert.deepEqual(
    RAG_EXAMPLES.map((item) => item.question),
    [
      '분산투자는 위험을 어떻게 줄이나요?',
      '자산 배분은 무엇을 고려하나요?',
      '과거 성과는 어떻게 읽어야 하나요?',
    ],
  );
  assert.ok(RAG_EXAMPLES.every((item) => item.source.href?.startsWith('https://www.investor.gov/')));

  process.env.NEXT_PUBLIC_API_MODE = 'live';
  process.env.NEXT_PUBLIC_MARS_PRODUCT = 'full';
  session.set('fixture-access-token', new Date(Date.now() + 60_000).toISOString(), {
    userId: 'usr_fixture_user',
    username: 'fixture',
    role: 'USER',
  });
  globalThis.fetch = async (input) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path === '/api/v1/rag/sources') {
      return new Response(JSON.stringify({
        success: true,
        requestId: 'req_registry_0000000000000000000000000',
        data: { items: [] },
        warnings: [],
        error: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (responseMode === 'NETWORK_ERROR') throw new Error('fixture network failure');
    if (responseMode === 'CONSENT_BLOCK') {
      return new Response(JSON.stringify({
        success: false,
        requestId: 'req_consent_00000000000000000000000000',
        data: null,
        warnings: [],
        error: { code: 'EXTERNAL_AI_CONSENT_REQUIRED', message: 'Consent required.' },
      }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    }
    const blocked = responseMode === 'BLOCKED';
    return new Response(JSON.stringify({
      requestId: 'req_rag_00000000000000000000000000000',
      answerId: null,
      generationStatus: blocked ? 'BLOCKED_ADVICE' : 'RETRIEVAL_FAILURE',
      answer: null,
      citationCoverage: 0,
      citations: [],
      retrievalFailure: !blocked,
      guardrailFlags: blocked ? ['DIRECT_ADVICE_BLOCKED'] : ['RAG_INSUFFICIENT_EVIDENCE'],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  try {
    const noEvidenceButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(noEvidenceButton.kind, 'ready');
    if (noEvidenceButton.kind !== 'ready') return;
    assert.equal(noEvidenceButton.data.fallbackUsed, true);
    assert.equal(noEvidenceButton.data.statusHeadline, '출처를 확인한 설명');
    assert.equal(noEvidenceButton.data.citationCoverage, 1);
    assert.equal(noEvidenceButton.data.topSources[0]?.institution, 'Investor.gov');
    assert.equal(noEvidenceButton.data.topSources[0]?.href, example.source.href);

    responseMode = 'NETWORK_ERROR';
    const offlineButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(offlineButton.kind, 'ready');
    if (offlineButton.kind === 'ready') assert.equal(offlineButton.data.fallbackUsed, true);
    await assert.rejects(askRag('자유 질문은 fallback 하면 안 됩니다.', 'CONCISE'));

    responseMode = 'BLOCKED';
    const blockedButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(blockedButton.kind, 'ready');
    if (blockedButton.kind === 'ready') {
      assert.equal(blockedButton.data.fallbackUsed, false);
      assert.equal(blockedButton.data.answer, null);
      assert.match(blockedButton.data.statusHeadline, /매수·매도 조언/);
    }

    responseMode = 'CONSENT_BLOCK';
    await assert.rejects(askRag(example.question, 'CONCISE', true));
  } finally {
    session.clear();
    globalThis.fetch = previousFetch;
    if (previousApiMode === undefined) delete process.env.NEXT_PUBLIC_API_MODE;
    else process.env.NEXT_PUBLIC_API_MODE = previousApiMode;
    if (previousProduct === undefined) delete process.env.NEXT_PUBLIC_MARS_PRODUCT;
    else process.env.NEXT_PUBLIC_MARS_PRODUCT = previousProduct;
  }
});
