/**
 * 세계 뉴스를 **읽을 수 있는 모양**으로 바꾸는 층.
 *
 * 화면이 `GDELT_GQG`, `GDELT_METADATA_QUOTE`, `COLLECTION_FAILED`, `documentVersionId` 를
 * 그대로 뿌리고 있었다. 저장소 스키마를 독자에게 떠넘긴 것이다. 그 코드들은 수집 파이프라인
 * 안에서 의미가 있는 값이지 일반 독자가 읽을 말이 아니다.
 *
 * 여기서 하는 일은 셋이다.
 *   1) 코드 → 사람 말 (모르는 값은 버리지 않고 그대로 보여 준다. 새 provider 가 생겼을 때
 *      화면이 비는 것보다 낯선 이름이 보이는 편이 낫다.)
 *   2) 시각 → "3시간 전" 같은 상대 표기. 절대 시각은 title 로 남긴다.
 *   3) 같은 기사의 재게재본 묶기 — 같은 뉴스가 여러 줄로 반복되던 것을 한 줄로 만든다.
 *
 * **판단 로직은 여기 없다.** 이 목록은 거부권·시그널·주문에 쓰이지 않는다(서버가
 * `decisionAuthority: 'NONE'` 으로 못박는다). 표시만 다듬는다.
 */

import type { WorldNewsItem, WorldNewsPage } from '@/shared/api/wire';

const PROVIDER_LABEL: Record<string, string> = {
  GDELT_GQG: 'GDELT 글로벌 뉴스',
  GDELT_GEMG: 'GDELT 이벤트 뉴스',
  FINNHUB_MARKET_NEWS: 'Finnhub 시장 뉴스',
};

const COLLECTION_LABEL: Record<string, string> = {
  COMPLETE: '정상 수집',
  PARTIAL: '일부만 수집',
  COLLECTION_FAILED: '수집 실패',
  NOT_COLLECTED: '수집 안 함',
};

/** 수집 상태를 "그래서 지금 목록을 믿어도 되나"로 옮긴다. */
const COLLECTION_MEANING: Record<string, string> = {
  PARTIAL: '이 출처의 기사 일부가 아직 들어오지 않았습니다.',
  COLLECTION_FAILED: '이 출처에서 새 기사를 가져오지 못했습니다. 아래 목록은 그 전에 받아 둔 것입니다.',
  NOT_COLLECTED: '이 출처는 이번 주기에 수집하지 않았습니다.',
};

/**
 * 실패 코드 중 **실패가 아닌 것**.
 *
 * GDELT GQG/GEMG 는 15분마다 한 번씩만 파일을 낸다. 그 사이의 분을 물으면 서버는
 * `GDELT_NOT_PUBLISHED` 를 받고 그 시도를 `COLLECTION_FAILED` 로 적는다 - 수집기 안에서는
 * 맞는 기록이지만, 화면이 그대로 옮기면 "수집 실패"라는 붉은 말만 남는다. 실제로는
 * 다음 heartbeat 를 기다리는 정상 상태다.
 */
const BENIGN_COLLECTION_CODES: ReadonlySet<string> = new Set([
  'GDELT_NOT_PUBLISHED',
  'GDELT_FILE_NOT_FOUND',
]);

export interface NewsCard {
  key: string;
  title: string;
  quote: string | null;
  providerLabel: string;
  /** "3시간 전" 같은 상대 표기. 확정된 발행 시각이 없으면 null. */
  relativeTime: string | null;
  /** 마우스를 올렸을 때 보여 줄 절대 시각. */
  absoluteTime: string | null;
  /** 발행 시각을 확정하지 못했으면 true. 설명은 목록 아래에 한 번만 둔다. */
  timeUncertain: boolean;
  href: string | null;
  /** 같은 기사를 실은 다른 매체 수. 0이면 표시하지 않는다. */
  duplicateCount: number;
}

export interface NewsFeed {
  cards: NewsCard[];
  /** 정상이 아닌 수집만. 정상일 때는 아무 말도 하지 않는다. */
  warnings: { label: string; meaning: string }[];
  asOfRelative: string | null;
  /** 시각이 불확실한 카드가 하나라도 있으면 목록 아래에 둘 설명. 없으면 null. */
  timeNote: string | null;
  /**
   * 금융과 관련 없어 접은 기사 수.
   *
   * 화면이 조용히 골라내면 무엇을 숨겼는지 아무도 모른다. 숫자로 적는다.
   */
  hiddenUnrelated: number;
}

function relative(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return null;
  const minutes = Math.round((now - at) / 60_000);
  if (minutes < 0) return '방금';
  if (minutes < 1) return '방금';
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}일 전`;
  const weeks = Math.round(days / 7);
  if (weeks < 5) return `${weeks}주 전`;
  return `${Math.round(days / 30)}개월 전`;
}

function absolute(iso: string | null): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Seoul',
  }).format(at);
}

/**
 * 인용문을 다듬는다.
 *
 * 저장된 bounded 인용은 줄바꿈과 공백이 원문 그대로라 화면에서 들쭉날쭉하게 보인다.
 * **자르지는 않는다** - 인용은 길이를 줄이면 뜻이 바뀔 수 있다. 접는 것은 화면이 한다.
 */
function tidyQuote(item: WorldNewsItem): string | null {
  const raw = item.boundedQuote ?? item.boundedPassage;
  if (!raw) return null;
  const text = raw.replace(/\s+/g, ' ').trim();
  return text.length > 0 ? text : null;
}

/**
 * 제목에서 마크업 찌꺼기만 걷어낸다.
 *
 * 실제 수집물에 `Aufruhr<!-- --> | DrimbleTV` 처럼 HTML 주석이 남아 있었다. 낱말은
 * 손대지 않는다 - 제목을 우리가 고쳐 쓰면 그건 그 기사의 제목이 아니다.
 */
function tidyTitle(raw: string | null): string | null {
  if (!raw) return null;
  const text = raw
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    // 마크업을 지우면 " | DrimbleTV" 처럼 구분자가 앞뒤에 남는다.
    .replace(/^[\s|·\-–—]+|[\s|·\-–—]+$/g, '')
    .trim();
  return text.length > 0 ? text : null;
}

/** 제목을 비교용으로 정규화한다. 공백·대소문자·양끝 구두점 차이는 같은 기사로 본다. */
function titleKey(item: WorldNewsItem): string | null {
  // 찌꺼기 차이로 같은 기사가 두 줄로 갈리면 안 되므로 정리한 제목으로 비교한다.
  return tidyTitle(item.title)?.toLowerCase() ?? null;
}

/**
 * 금융과 관련 있는 기사인가.
 *
 * 같은 판정이 수집기(`app/data/news/financial_relevance.py`)에도 있다. 중복이지만 목적이
 * 다르다 - 저쪽은 **저장 여부**, 이쪽은 **표시 여부**다. 수집기 필터는 앞으로 들어올
 * 기사에만 듣고, 이미 저장된 105만 건은 이 층이 없으면 계속 화면에 나온다.
 *
 * 기준은 저쪽과 같고 둘 다 보수적이다 - 애매하면 남긴다. 진짜 금융 기사를 조용히
 * 감추는 쪽이 게임 제목 하나가 섞이는 쪽보다 나쁘다.
 */
const FINANCIAL_PATH_HINTS: ReadonlySet<string> = new Set([
  'business', 'markets', 'market', 'finance', 'financial', 'economy', 'economic',
  'money', 'investing', 'investment', 'stocks', 'equities', 'wirtschaft', 'economia',
  '경제', '증권',
]);

const FINANCIAL_TERMS = [
  'stock', 'stocks', 'share', 'shares', 'equity', 'equities', 'bond', 'bonds',
  'market', 'markets', 'index', 'nasdaq', 'dow jones', 'nikkei', 'kospi',
  'ipo', 'dividend', 'earnings', 'revenue', 'profit', 'quarterly',
  'investor', 'investors', 'investment', 'trading', 'trader', 'portfolio',
  'inflation', 'deflation', 'interest rate', 'rate hike', 'rate cut',
  'central bank', 'federal reserve', 'ecb', 'gdp', 'recession', 'unemployment',
  'tariff', 'tariffs', 'sanctions', 'currency', 'dollar', 'euro', 'yen', 'yuan',
  'merger', 'acquisition', 'buyout', 'bankruptcy', 'layoff', 'layoffs',
  'valuation', 'shareholder', 'oil price', 'crude', 'opec', 'commodity', 'commodities',
  '주가', '증시', '코스피', '코스닥', '금리', '환율', '실적', '배당',
  '인수', '합병', '상장', '공모', '무역', '관세', '물가', '투자자', '시가총액',
];

const TERM_PATTERN = new RegExp(
  FINANCIAL_TERMS.map((term) =>
    // 한글에는 낱말 경계가 동작하지 않으므로 그대로 찾는다.
    /^[\x00-\x7f]+$/.test(term) ? `\\b${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b` : term,
  ).join('|'),
  'i',
);

function financiallyRelevant(item: WorldNewsItem): boolean {
  let segments: string[] = [];
  try {
    const parsed = new URL(item.canonicalUrl);
    segments = [...parsed.pathname.split('/'), ...parsed.hostname.split('.')]
      .filter(Boolean)
      .map((part) => part.toLowerCase());
  } catch {
    segments = [];
  }
  if (segments.some((segment) => FINANCIAL_PATH_HINTS.has(segment))) return true;

  const text = [item.title, item.boundedQuote, item.boundedPassage]
    .filter((part): part is string => Boolean(part))
    .join(' ');
  // 읽을 글자가 없으면 판정할 근거가 없다. 근거 없이 감추지 않는다.
  if (text.trim().length === 0) return true;
  return TERM_PATTERN.test(text);
}

export function buildNewsFeed(
  page: WorldNewsPage,
  safeUrl: (url: string) => string | null,
  now: number = Date.now(),
): NewsFeed {
  /*
   * 같은 기사의 재게재본을 묶는다. 서버가 `republicationOfDocumentId` 로 원본을 가리키므로
   * 그 값이 있으면 원본 쪽에 세어 넣는다. 원본이 이 페이지에 없으면 자기 자신을 대표로 둔다.
   */
  const byDocument = new Map<string, WorldNewsItem>();
  for (const item of page.items) byDocument.set(item.documentId, item);

  /*
   * 실제 데이터에서는 같은 기사가 재게재 링크 없이 제목만 같은 채로 두 번 들어온다.
   * 서버가 잇지 못한 건도 화면에서는 한 줄이어야 하므로 제목으로도 묶는다. 제목이
   * 없으면 묶지 않는다 - 제목 없는 기사끼리 한 덩어리가 되면 서로 다른 뉴스가 사라진다.
   */
  const leadByTitle = new Map<string, string>();
  const groups = new Map<string, { lead: WorldNewsItem; duplicates: number }>();
  let hiddenUnrelated = 0;
  for (const item of page.items) {
    if (!financiallyRelevant(item)) {
      hiddenUnrelated += 1;
      continue;
    }
    const parent = item.republicationOfDocumentId;
    let leadId = parent && byDocument.has(parent) ? parent : item.documentId;

    const key = titleKey(item);
    if (key) {
      const known = leadByTitle.get(key);
      if (known) leadId = known;
      else leadByTitle.set(key, leadId);
    }

    const existing = groups.get(leadId);
    if (existing) {
      existing.duplicates += 1;
      continue;
    }
    groups.set(leadId, { lead: byDocument.get(leadId) ?? item, duplicates: 0 });
  }

  const cards: NewsCard[] = [...groups.values()].map(({ lead, duplicates }) => {
    const verified = lead.publicationStatus === 'VERIFIED' ? lead.publishedAt : null;
    const shown = verified ?? lead.firstSeenAt;
    return {
      key: lead.documentVersionId,
      title: tidyTitle(lead.title) ?? '제목이 확인되지 않은 기사',
      quote: tidyQuote(lead),
      providerLabel: PROVIDER_LABEL[lead.provider] ?? lead.provider,
      relativeTime: relative(shown, now),
      absoluteTime: absolute(shown),
      timeUncertain: verified === null,
      href: safeUrl(lead.canonicalUrl),
      duplicateCount: duplicates,
    };
  });

  /*
   * 아직 발행되지 않은 것은 경고가 아니다. 그것까지 경고로 띄우면 15분 중 14분은 화면에
   * 붉은 줄이 서 있게 되고, 그러면 진짜 실패가 왔을 때 아무도 알아채지 못한다.
   */
  const warnings = page.collections
    .filter(
      (item) =>
        item.collectionStatus !== 'COMPLETE' &&
        !(item.errorCode !== null && BENIGN_COLLECTION_CODES.has(item.errorCode)),
    )
    .map((item) => ({
      label: `${PROVIDER_LABEL[item.provider] ?? item.provider} · ${
        COLLECTION_LABEL[item.collectionStatus] ?? item.collectionStatus
      }`,
      meaning: COLLECTION_MEANING[item.collectionStatus] ?? '이 출처의 상태를 확인하지 못했습니다.',
    }));

  const timeNote = cards.some((card) => card.timeUncertain)
    ? '앞에 ‘약’ 이 붙은 시각은 발행 시각을 확인하지 못해 기사를 받은 시각으로 대신 적은 것입니다.'
    : null;

  return { cards, warnings, asOfRelative: relative(page.asOf, now), timeNote, hiddenUnrelated };
}
