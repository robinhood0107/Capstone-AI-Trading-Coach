import assert from 'node:assert/strict';
import test from 'node:test';

import { buildNewsFeed } from '../../src/features/rag-source/worldNewsPresentation.ts';
import type { WorldNewsItem, WorldNewsPage } from '../../src/shared/api/wire.ts';

const NOW = Date.parse('2026-09-16T12:00:00+09:00');

function item(overrides: Partial<WorldNewsItem> & { documentId: string }): WorldNewsItem {
  return {
    documentVersionId: `dv_${overrides.documentId}`,
    sourceId: 'src_1',
    provider: 'GDELT_GQG',
    providerDocumentId: null,
    // 가공 층은 금융과 무관한 기사를 접는다. 이 파일이 확인하는 것은 라벨·시각·
    // 중복 묶기·제목 정리이므로, 픽스처가 그 관문을 통과하도록 금융면 경로를 쓴다.
    canonicalUrl: 'https://example.com/markets/a',
    republicationOfDocumentId: null,
    identityStatus: 'VERIFIED',
    title: '제목',
    boundedQuote: '인용',
    boundedPassage: null,
    language: 'en',
    publishedAt: '2026-09-16T09:00:00+09:00',
    publicationStatus: 'VERIFIED',
    providerObservedAt: '2026-09-16T09:05:00+09:00',
    firstSeenAt: '2026-09-16T09:05:00+09:00',
    availableAt: '2026-09-16T09:05:00+09:00',
    rightsProfile: 'GDELT_METADATA_QUOTE',
    externalLlmAllowed: false,
    lookupAllowed: true,
    ragRetrievalAllowed: true,
    promptUntrusted: true,
    collectionStatus: 'COMPLETE',
    contentSha256: 'a'.repeat(64),
    versionSha256: 'b'.repeat(64),
    ...overrides,
  };
}

function page(items: WorldNewsItem[], collections: WorldNewsPage['collections'] = []): WorldNewsPage {
  return {
    items,
    collections,
    asOf: '2026-09-16T11:30:00+09:00',
    decisionAuthority: 'NONE',
    signalAuthority: 'NONE',
    orderAuthority: 'NONE',
  };
}

const allowAll = (url: string) => url;

/** `noUncheckedIndexedAccess` 아래에서 인덱스 접근은 undefined 를 낳는다. 없으면 그
 *  자리에서 실패하는 편이 이후 단정이 조용히 통과하는 것보다 낫다. */
function at<T>(items: T[], index: number): T {
  const value = items[index];
  assert.ok(value, `${index}번째 항목이 없다`);
  return value;
}

test('저장소 코드가 화면에 그대로 새어 나가지 않는다', () => {
  const feed = buildNewsFeed(
    page(
      [item({ documentId: 'd1', provider: 'FINNHUB_MARKET_NEWS' })],
      [
        {
          provider: 'GDELT_GQG',
          collectionStatus: 'COLLECTION_FAILED',
          startedAt: '2026-09-16T11:00:00+09:00',
          completedAt: null,
          observedThrough: null,
          itemCount: 0,
          errorCode: 'GDELT_FILE_TIMEOUT',
        },
      ],
    ),
    allowAll,
    NOW,
  );

  const rendered = JSON.stringify(feed);
  for (const code of [
    'FINNHUB_MARKET_NEWS',
    'GDELT_GQG',
    'COLLECTION_FAILED',
    'GDELT_METADATA_QUOTE',
    'VERIFIED',
  ]) {
    assert.ok(!rendered.includes(code), `원시 코드 ${code} 가 화면 모델에 남아 있다`);
  }
  assert.equal(at(feed.cards, 0).providerLabel, 'Finnhub 시장 뉴스');
  assert.match(at(feed.warnings, 0).label, /GDELT 글로벌 뉴스 · 수집 실패/);
  // 상태만 옮기면 여전히 못 읽는다. "그래서 지금 목록을 믿어도 되나"에 답해야 한다.
  assert.match(at(feed.warnings, 0).meaning, /가져오지 못했습니다/);
});

test('모르는 provider 는 버리지 않고 그대로 보여 준다', () => {
  // 새 출처가 붙었을 때 화면이 비는 것보다 낯선 이름이 보이는 편이 낫다.
  const feed = buildNewsFeed(
    page([item({ documentId: 'd1', provider: 'REUTERS_TOPNEWS' as WorldNewsItem['provider'] })]),
    allowAll,
    NOW,
  );
  assert.equal(at(feed.cards, 0).providerLabel, 'REUTERS_TOPNEWS');
});

test('시각은 상대 표기로 바꾸고 절대 시각은 함께 남긴다', () => {
  const feed = buildNewsFeed(page([item({ documentId: 'd1' })]), allowAll, NOW);
  assert.equal(at(feed.cards, 0).relativeTime, '3시간 전');
  assert.ok(at(feed.cards, 0).absoluteTime);
  assert.equal(at(feed.cards, 0).timeUncertain, false);
  // 전부 확정된 시각이면 목록 아래 안내도 붙이지 않는다.
  assert.equal(feed.timeNote, null);
});

test('발행 시각을 확정하지 못하면 그 사실을 말한다', () => {
  const missing = buildNewsFeed(
    page([item({ documentId: 'd1', publicationStatus: 'MISSING', publishedAt: null })]),
    allowAll,
    NOW,
  );
  assert.equal(at(missing.cards, 0).timeUncertain, true);
  assert.match(missing.timeNote ?? '', /확인하지 못해/);

  // 폴백을 실제로 증명하려면 두 시각이 달라야 한다. publishedAt(09:00)은 3시간 전,
  // firstSeenAt(10:30)은 2시간 전이므로 어느 쪽을 썼는지 값으로 갈린다.
  const conflict = buildNewsFeed(
    page([
      item({
        documentId: 'd2',
        publicationStatus: 'CONFLICT',
        firstSeenAt: '2026-09-16T10:30:00+09:00',
      }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(at(conflict.cards, 0).timeUncertain, true);
  // 확정하지 못했으면 발행 시각이 아니라 수집 시각을 보여 준다.
  assert.equal(at(conflict.cards, 0).relativeTime, '2시간 전');
});

test('같은 기사의 재게재본은 한 줄로 묶는다', () => {
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'orig', title: '원본 기사' }),
      item({ documentId: 'copy1', republicationOfDocumentId: 'orig' }),
      item({ documentId: 'copy2', republicationOfDocumentId: 'orig' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.equal(at(feed.cards, 0).title, '원본 기사');
  assert.equal(at(feed.cards, 0).duplicateCount, 2);
});

test('원본이 이 페이지에 없으면 재게재본이 스스로 대표가 된다', () => {
  // 원본을 못 찾았다고 기사를 통째로 숨기면 독자는 그 뉴스를 아예 못 본다.
  const feed = buildNewsFeed(
    page([item({ documentId: 'copy', republicationOfDocumentId: 'missing-original' })]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.equal(at(feed.cards, 0).duplicateCount, 0);
});

test('인용문은 공백만 다듬고 잘라내지 않는다', () => {
  const long = 'a'.repeat(400);
  const feed = buildNewsFeed(
    page([item({ documentId: 'd1', boundedQuote: `  줄바꿈\n\n  섞인   ${long}  ` })]),
    allowAll,
    NOW,
  );
  // 인용을 길이로 자르면 뜻이 바뀔 수 있다. 접는 것은 화면이 한다.
  assert.equal(at(feed.cards, 0).quote, `줄바꿈 섞인 ${long}`);
});

test('안전하지 않은 링크는 화면에 내보내지 않는다', () => {
  const feed = buildNewsFeed(
    // URL 을 바꾸면 금융면 경로가 사라지므로 제목으로 관문을 통과시킨다.
    page([item({ documentId: 'd1', canonicalUrl: 'javascript:alert(1)', title: 'Markets open' })]),
    () => null,
    NOW,
  );
  assert.equal(at(feed.cards, 0).href, null);
});

test('제목이 없어도 카드가 사라지지 않는다', () => {
  const feed = buildNewsFeed(
    page([item({ documentId: 'd1', title: '   ', boundedQuote: null, boundedPassage: null })]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.match(at(feed.cards, 0).title, /확인되지 않은/);
  assert.equal(at(feed.cards, 0).quote, null);
});

test('정상 수집만 있으면 경고를 만들지 않는다', () => {
  const feed = buildNewsFeed(
    page(
      [item({ documentId: 'd1' })],
      [
        {
          provider: 'GDELT_GQG',
          collectionStatus: 'COMPLETE',
          startedAt: '2026-09-16T11:00:00+09:00',
          completedAt: '2026-09-16T11:05:00+09:00',
          observedThrough: '2026-09-16T11:05:00+09:00',
          itemCount: 12,
          errorCode: null,
        },
      ],
    ),
    allowAll,
    NOW,
  );
  assert.equal(feed.warnings.length, 0);
});


test('재게재 링크가 없어도 제목이 같으면 한 줄로 묶는다', () => {
  // 실제 수집 데이터에서 같은 기사가 링크 없이 두 번 들어왔다. 서버가 잇지 못한 건도
  // 화면에서는 한 줄이어야 한다.
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', title: 'Volver al colegio cuando llegar a fin de mes' }),
      item({ documentId: 'b', title: '  volver al colegio cuando llegar a fin de mes  ' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.equal(at(feed.cards, 0).duplicateCount, 1);
});

test('제목이 없는 기사끼리는 묶지 않는다', () => {
  // 제목 없는 것들을 한 덩어리로 만들면 서로 다른 뉴스가 화면에서 사라진다.
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', title: null }),
      item({ documentId: 'b', title: '   ' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 2);
});

test('같은 안내를 카드마다 반복하지 않는다', () => {
  // 열 장 전부에 같은 문장이 붙으면 그 문장은 읽히지 않고 목록만 어지럽힌다.
  const feed = buildNewsFeed(
    page(
      Array.from({ length: 5 }, (_, index) =>
        item({ documentId: `d${index}`, title: `기사 ${index}`, publicationStatus: 'CONFLICT' }),
      ),
    ),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 5);
  assert.ok(feed.cards.every((card) => card.timeUncertain));
  // 설명은 목록 전체에 한 번이다.
  assert.ok(feed.timeNote);
});


test('제목의 마크업 찌꺼기만 걷어내고 낱말은 건드리지 않는다', () => {
  // 실제 화면에 `Aufruhr<!-- --> | DrimbleTV` 가 그대로 나왔다.
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', title: 'Aufruhr<!-- --> | DrimbleTV' }),
      item({ documentId: 'b', title: '<b>Bold</b> &amp; brief' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(at(feed.cards, 0).title, 'Aufruhr | DrimbleTV');
  assert.equal(at(feed.cards, 1).title, 'Bold & brief');
});

test('마크업만 있던 제목은 없는 것으로 본다', () => {
  const feed = buildNewsFeed(page([item({ documentId: 'a', title: '<!-- x -->' })]), allowAll, NOW);
  assert.match(at(feed.cards, 0).title, /확인되지 않은/);
});

test('찌꺼기 차이로 같은 기사가 두 줄로 갈리지 않는다', () => {
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', title: 'Same Story' }),
      item({ documentId: 'b', title: 'Same Story<!-- ad -->' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.equal(at(feed.cards, 0).duplicateCount, 1);
});


test('금융과 무관한 기사는 접고, 접은 수를 숨기지 않는다', () => {
  // 실제로 "세계 뉴스"에 올라왔던 제목들이다. 화면이 조용히 골라내면 무엇을 숨겼는지
  // 아무도 모르므로, 접은 수가 반드시 숫자로 남아야 한다.
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', canonicalUrl: 'https://example.com/games/x', title: 'Graveyard Keeper II' }),
      item({ documentId: 'b', canonicalUrl: 'https://example.com/life/y', title: 'Forest Treehouse' }),
      item({ documentId: 'c', canonicalUrl: 'https://example.com/news/z', title: 'Fed signals a rate cut' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.match(at(feed.cards, 0).title, /Fed signals/);
  assert.equal(feed.hiddenUnrelated, 2);
});

test('금융면 경로면 제목이 평범해도 남긴다', () => {
  const feed = buildNewsFeed(
    page([
      item({ documentId: 'a', canonicalUrl: 'https://www.reuters.com/markets/asia/s', title: 'Quiet day' }),
      item({ documentId: 'b', canonicalUrl: 'https://finance.yahoo.com/news/s', title: 'Quiet day two' }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 2);
  assert.equal(feed.hiddenUnrelated, 0);
});

test('읽을 글자가 없으면 접지 않는다', () => {
  // 판정할 근거가 없는 것을 근거 없이 감추면, 감춘 사실조차 확인할 수 없다.
  const feed = buildNewsFeed(
    page([
      item({
        documentId: 'a',
        canonicalUrl: 'https://example.com/x/y',
        title: null,
        boundedQuote: null,
        boundedPassage: null,
      }),
    ]),
    allowAll,
    NOW,
  );
  assert.equal(feed.cards.length, 1);
  assert.equal(feed.hiddenUnrelated, 0);
});
