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

test('preset RAG buttons use previously successful cited or uncited answers only as a final fallback', async () => {
  const previousApiMode = process.env.NEXT_PUBLIC_API_MODE;
  const previousProduct = process.env.NEXT_PUBLIC_MARS_PRODUCT;
  const previousFetch = globalThis.fetch;
  const example = RAG_EXAMPLES[0]!;
  let responseMode:
    | 'NO_EVIDENCE'
    | 'MODEL_KNOWLEDGE'
    | 'VERTEX_UNAVAILABLE'
    | 'NETWORK_ERROR'
    | 'BLOCKED'
    | 'CONSENT_BLOCK' = 'NO_EVIDENCE';
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
    const modelKnowledge = responseMode === 'MODEL_KNOWLEDGE';
    const vertexUnavailable = responseMode === 'VERTEX_UNAVAILABLE';
    return new Response(JSON.stringify({
      requestId: 'req_rag_00000000000000000000000000000',
      answerId: modelKnowledge ? 'rag_fixture_vertex_answer_00000000000000000000' : null,
      generationStatus: blocked
        ? 'BLOCKED_ADVICE'
        : modelKnowledge
          ? 'ANSWERED'
          : vertexUnavailable
            ? 'GENERATION_UNAVAILABLE'
            : 'RETRIEVAL_FAILURE',
      answer: modelKnowledge ? 'Vertex AI Gemini가 만든 근거 없는 설명입니다.' : null,
      citationCoverage: 0,
      citations: [],
      retrievalFailure: !blocked && !modelKnowledge,
      guardrailFlags: blocked
        ? ['DIRECT_ADVICE_BLOCKED']
        : modelKnowledge
          ? ['MODEL_KNOWLEDGE_ONLY']
          : vertexUnavailable
            ? ['GENERATION_UNAVAILABLE']
            : ['RAG_INSUFFICIENT_EVIDENCE'],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  try {
    const noEvidenceButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(noEvidenceButton.kind, 'ready');
    if (noEvidenceButton.kind !== 'ready') return;
    assert.equal(noEvidenceButton.data.fallbackUsed, true);
    assert.equal(noEvidenceButton.data.statusHeadline, '저장된 예시 답변');
    assert.equal(noEvidenceButton.data.citationCoverage, 1);
    assert.equal(noEvidenceButton.data.topSources[0]?.institution, '삼성자산운용');
    assert.equal(noEvidenceButton.data.topSources[0]?.href, example.source?.href);

    responseMode = 'MODEL_KNOWLEDGE';
    const vertexAnswer = await askRag(example.question, 'CONCISE', true);
    assert.equal(vertexAnswer.kind, 'ready');
    if (vertexAnswer.kind === 'ready') {
      assert.equal(vertexAnswer.data.fallbackUsed, false);
      assert.equal(vertexAnswer.data.answer, 'Vertex AI Gemini가 만든 근거 없는 설명입니다.');
      assert.equal(vertexAnswer.data.statusHeadline, 'Vertex AI Gemini 답변');
      assert.equal(vertexAnswer.data.citationCoverage, null);
      assert.match(vertexAnswer.data.sourcesUnavailableReason ?? '', /Vertex AI Gemini/);
      assert.deepEqual(vertexAnswer.data.topSources, []);
    }

    responseMode = 'NETWORK_ERROR';
    const offlineButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(offlineButton.kind, 'ready');
    if (offlineButton.kind === 'ready') assert.equal(offlineButton.data.fallbackUsed, true);

    const offlineUncitedButton = await askRag(RAG_EXAMPLES[2]!.question, 'CONCISE', true);
    assert.equal(offlineUncitedButton.kind, 'ready');
    if (offlineUncitedButton.kind === 'ready') {
      assert.equal(offlineUncitedButton.data.fallbackUsed, true);
      assert.ok(offlineUncitedButton.data.answer);
      assert.equal(offlineUncitedButton.data.citationCoverage, null);
      assert.deepEqual(offlineUncitedButton.data.topSources, []);
      assert.match(offlineUncitedButton.data.sourcesUnavailableReason ?? '', /인용이 연결되지/);
    }

    const offlineFreeText = await askRag('자유 질문은 Vertex 장애 때 고정 안내를 받습니다.', 'CONCISE');
    assert.equal(offlineFreeText.kind, 'ready');
    if (offlineFreeText.kind === 'ready') {
      assert.equal(offlineFreeText.data.fallbackUsed, true);
      assert.equal(offlineFreeText.data.statusHeadline, 'Vertex AI 응답을 가져오지 못했습니다');
      assert.match(offlineFreeText.data.answer ?? '', /다시 질문/);
    }

    responseMode = 'VERTEX_UNAVAILABLE';
    const unavailableVertexButton = await askRag(example.question, 'CONCISE', true);
    assert.equal(unavailableVertexButton.kind, 'ready');
    if (unavailableVertexButton.kind === 'ready') {
      assert.equal(unavailableVertexButton.data.fallbackUsed, true);
      assert.equal(unavailableVertexButton.data.topSources[0]?.institution, '삼성자산운용');
    }

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
