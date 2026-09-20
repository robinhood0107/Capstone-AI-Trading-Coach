"""수집한 기사가 금융과 관련 있는지 판정한다.

GDELT GQG/GEMG 는 **일반 사건 피드**다. 주제 필터가 없어서 15분마다 오는 파일의 모든 줄이
그대로 저장됐고, 화면에는 `Graveyard Keeper II`, `Forest Treehouse`, `Once Upon a Bride`
같은 것이 "세계 뉴스"로 올라왔다. 투자 판단을 돕는 화면에 게임 제목과 웨딩드레스 기사가
섞여 있으면 그 화면은 아무도 읽지 않는다.

**화면에서 거르지 않고 수집에서 거른다.** 화면이 내용을 골라내기 시작하면 무엇을 숨겼는지
아무도 모르게 된다. 수집에서 거르면 "몇 건을 왜 뺐는지"가 영수증에 숫자로 남는다.

판정 기준은 둘뿐이고 **둘 다 보수적**이다 — 애매하면 남긴다.
  1) 출처 경로가 금융면인가 (`/business/`, `/markets/`, `/finance/` …)
  2) 제목·인용에 금융 낱말이 있는가

GDELT 는 주제 분류(V2GKG themes)를 이 두 피드에 싣지 않는다. 그래서 우리가 가진 것은
URL 과 글자뿐이고, 그 한계를 숨기지 않는다 — 이 판정은 **완벽하지 않고 완벽할 필요도
없다.** 목적은 "읽을 수 있는 목록"이지 "완전한 금융 아카이브"가 아니다.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: 출처가 금융면임을 드러내는 경로 조각. 언론사 URL 관행을 따른다.
_FINANCIAL_PATH_HINTS: frozenset[str] = frozenset(
    {
        "business",
        "markets",
        "market",
        "finance",
        "financial",
        "economy",
        "economic",
        "money",
        "investing",
        "investment",
        "stocks",
        "equities",
        "wirtschaft",  # 독일어권
        "economia",  # 스페인·이탈리아·포르투갈어권
        "경제",
        "증권",
    }
)

#: 금융 낱말. 영어·한국어·주요 유럽어를 함께 본다 - GDELT 는 다국어 피드다.
#: 낱말 경계로 찾으므로 `ai` 같은 짧은 조각이 `said` 안에서 걸리지 않는다.
_FINANCIAL_TERMS: tuple[str, ...] = (
    # 시장·거래
    "stock",
    "stocks",
    "share",
    "shares",
    "equity",
    "equities",
    "bond",
    "bonds",
    "market",
    "markets",
    "index",
    "nasdaq",
    "dow jones",
    "s&p",
    "nikkei",
    "kospi",
    "ipo",
    "dividend",
    "earnings",
    "revenue",
    "profit",
    "loss",
    "quarterly",
    "investor",
    "investors",
    "investment",
    "trading",
    "trader",
    "portfolio",
    # 거시
    "inflation",
    "deflation",
    "interest rate",
    "rate hike",
    "rate cut",
    "central bank",
    "federal reserve",
    "the fed",
    "ecb",
    "bank of japan",
    "gdp",
    "recession",
    "unemployment",
    "tariff",
    "tariffs",
    "sanctions",
    "currency",
    "dollar",
    "euro",
    "yen",
    "yuan",
    "exchange rate",
    # 기업 행위
    "merger",
    "acquisition",
    "buyout",
    "bankruptcy",
    "layoff",
    "layoffs",
    "guidance",
    "forecast",
    "valuation",
    "shareholder",
    "stake",
    # 원자재
    "oil price",
    "crude",
    "opec",
    "gold price",
    "commodity",
    "commodities",
    # 한국어
    "주가",
    "증시",
    "코스피",
    "코스닥",
    "금리",
    "환율",
    "실적",
    "배당",
    "인수",
    "합병",
    "상장",
    "공모",
    "무역",
    "관세",
    "물가",
    "경기침체",
    "투자자",
    "매수",
    "매도",
    "시가총액",
    "반도체 가격",
)

_TERM_PATTERN = re.compile(
    "|".join(
        # 한글에는 낱말 경계(\b)가 동작하지 않으므로 그대로 찾는다.
        re.escape(term) if not term.isascii() else r"\b" + re.escape(term) + r"\b"
        for term in _FINANCIAL_TERMS
    ),
    re.IGNORECASE,
)


def _path_looks_financial(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    segments = {segment.lower() for segment in parts.path.split("/") if segment}
    # 호스트도 본다 - `markets.ft.com`, `finance.yahoo.com` 처럼 서브도메인에 오기도 한다.
    segments.update(part.lower() for part in parts.netloc.split("."))
    return bool(segments & _FINANCIAL_PATH_HINTS)


def is_financially_relevant(
    *, url: str, title: str | None, quote: str | None, passage: str | None
) -> bool:
    """이 기사를 세계 뉴스 목록에 남길 것인가.

    애매하면 **남긴다.** 진짜 금융 기사를 조용히 버리는 쪽이 게임 제목 하나가 섞이는
    쪽보다 나쁘다 - 버린 것은 화면에서 영영 볼 수 없다.
    """

    if _path_looks_financial(url):
        return True
    text = " ".join(part for part in (title, quote, passage) if part)
    if not text.strip():
        # 읽을 글자가 없으면 판정할 근거가 없다. 근거 없이 버리지 않는다.
        return True
    return _TERM_PATTERN.search(text) is not None
