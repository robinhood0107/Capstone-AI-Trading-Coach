import assert from 'node:assert/strict';
import test from 'node:test';
import { api } from '../../src/shared/api/endpoints.ts';
import { session } from '../../src/shared/api/session.ts';

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
