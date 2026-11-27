import assert from 'node:assert/strict';
import test from 'node:test';
import { api } from '../../src/shared/api/endpoints.ts';
import { ApiFailure } from '../../src/shared/api/envelope.ts';
import type { RagV2Answer } from '../../src/shared/api/wire.ts';
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

test('mock RAG still requires consent for a previously cached question', async (t) => {
  const previousApiMode = process.env.NEXT_PUBLIC_API_MODE;
  process.env.NEXT_PUBLIC_API_MODE = 'mock';
  t.after(() => {
    if (previousApiMode === undefined) delete process.env.NEXT_PUBLIC_API_MODE;
    else process.env.NEXT_PUBLIC_API_MODE = previousApiMode;
  });
  await assert.rejects(
    api.ragV2Ask({ question: '금 ETF의 롤오버 위험은 무엇인가요?', answerMode: 'CONCISE' }),
    (cause) => cause instanceof ApiFailure && cause.code === 'EXTERNAL_AI_CONSENT_REQUIRED',
  );
});

test('the RAG view keeps current statuses, citations and errors for preset questions', async (t) => {
  const citedExample = RAG_EXAMPLES[0]!;
  const uncitedExample = RAG_EXAMPLES[2]!;
  assert.deepEqual(
    RAG_EXAMPLES.map((item) => item.question),
    [
      '132030 금선물 ETF의 환헤지와 롤오버 위험을 설명해 주세요.',
      'Sharpe 비율과 최대낙폭(MDD)은 각각 무엇을 측정하나요?',
      '복리와 단리는 어떻게 다른가요?',
      '인덱스 펀드의 추적오차란 무엇인가요?',
      '과거 성과는 어떻게 읽어야 하나요?',
    ],
  );
  assert.equal(RAG_EXAMPLES.filter((item) => item.source !== null).length, 2);
  assert.ok(RAG_EXAMPLES.every((item) => item.source === null || item.source.href?.startsWith('https://')));
  const citation: RagV2Answer['citations'][number] = {
    citationId: 'cit_1',
    sourceId: 'src_fixture',
    title: 'Current citation',
    canonicalUrl: 'https://example.org/current',
    citationKind: 'PUBLIC_WEB',
    locator: null,
    chunkRevisionId: 'chunk_fixture',
    sourceRevisionId: 'revision_fixture',
    generationId: 'generation_fixture',
  };
  const base: RagV2Answer = {
    requestId: 'req_0123456789abcdef0123456789abcdef',
    answerId: null,
    answer: null,
    generationStatus: 'RETRIEVAL_FAILURE',
    citations: [],
    citationCoverage: 0,
    retrievalFailure: true,
    guardrailFlags: ['INSUFFICIENT_EVIDENCE'],
  };
  const unavailable = { ...base, generationStatus: 'GENERATION_UNAVAILABLE', retrievalFailure: false } as const;
  const cases: {
    response: RagV2Answer;
    headline: string;
    example: typeof citedExample | null;
    question?: string;
    showExample?: boolean;
  }[] = [
    { response: base, headline: '충분한 근거를 찾지 못했습니다', example: citedExample },
    { response: unavailable, headline: '설명 생성이 지금은 불가능합니다', example: uncitedExample, question: uncitedExample.question },
    { response: unavailable, headline: '설명 생성이 지금은 불가능합니다', example: null, showExample: false },
    { response: { ...unavailable, citations: [citation] }, headline: '설명 생성이 지금은 불가능합니다', example: null },
    {
      response: { ...base, generationStatus: 'RETRIEVAL_ONLY', citations: [citation], citationCoverage: 1, retrievalFailure: false },
      headline: '설명 문장 없이 출처만 제공합니다',
      example: null,
    },
    {
      response: { ...base, generationStatus: 'ANSWERED', answer: 'Fixture explanation', retrievalFailure: false },
      headline: 'Vertex AI Gemini 답변',
      example: null,
    },
    {
      response: { ...base, generationStatus: 'BLOCKED_ADVICE', retrievalFailure: false },
      headline: '매수·매도 조언은 하지 않습니다',
      example: null,
    },
    {
      response: { ...base, generationStatus: 'BLOCKED_SENSITIVE', retrievalFailure: false },
      headline: '계좌·개인정보 질문에는 답하지 않습니다',
      example: null,
    },
  ];
  const ask = t.mock.method(api, 'ragV2Ask');
  t.mock.method(api, 'ragSources', async () => {
    throw new Error('fixture registry failure');
  });

  for (const { response, headline, example, question = citedExample.question, showExample = true } of cases) {
    const calls = ask.mock.callCount();
    ask.mock.mockImplementation(async () => response);
    const view = await askRag(question, 'CONCISE', showExample);
    assert.equal(ask.mock.callCount(), calls + 1);
    assert.equal(view.kind, 'ready');
    assert.equal(view.data.generationStatus, response.generationStatus);
    assert.equal(view.data.statusHeadline, headline);
    assert.equal(view.data.answer, response.answer);
    assert.equal(view.data.retrievalFailure, response.retrievalFailure);
    assert.equal(view.data.citationCoverage, null);
    assert.deepEqual(view.data.guardrailFlags, response.guardrailFlags);
    assert.deepEqual(view.data.topSources.map((source) => source.href), response.citations.map((source) => source.canonicalUrl));
    assert.equal(view.data.example, example);
  }

  for (const code of ['RATE_LIMITED', 'NETWORK_UNAVAILABLE', 'RAG_UNAVAILABLE', 'EXTERNAL_AI_CONSENT_REQUIRED']) {
    const failure = new ApiFailure({ code, message: 'Fixture failure' }, base.requestId);
    ask.mock.mockImplementation(async () => { throw failure; });
    await assert.rejects(askRag(citedExample.question, 'CONCISE', true), (cause) => cause === failure);
  }
});
