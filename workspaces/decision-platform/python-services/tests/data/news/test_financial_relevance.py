"""금융 관련성 판정의 경계를 고정한다.

이 판정은 **완벽하지 않다.** 목적은 "읽을 수 있는 목록"이지 완전한 금융 아카이브가
아니다. 그래서 고정하는 것은 정확도가 아니라 **기울기** — 애매하면 남긴다.
"""

from app.data.news.financial_relevance import is_financially_relevant


def judge(url: str = "https://example.com/a", title=None, quote=None, passage=None) -> bool:
    return is_financially_relevant(url=url, title=title, quote=quote, passage=passage)


def test_화면에_올라왔던_무관한_기사들을_거른다() -> None:
    # 실제로 "세계 뉴스"에 올라왔던 제목들이다.
    for title in (
        "Graveyard Keeper II",
        "Forest Treehouse",
        "A Dress With a Fighting Chance",
        "Once Upon a Bride",
        "Returning Favorites",
    ):
        assert judge(title=title) is False, f"{title} 를 걸러야 한다"


def test_금융_기사는_남긴다() -> None:
    for title in (
        "Fed signals another rate cut as inflation cools",
        "Samsung Electronics quarterly earnings beat estimates",
        "Oil price jumps after OPEC supply decision",
        "코스피 2,700선 회복 · 외국인 순매수",
        "한국은행 기준금리 동결",
    ):
        assert judge(title=title) is True, f"{title} 를 남겨야 한다"


def test_출처_경로가_금융면이면_제목과_무관하게_남긴다() -> None:
    # 언론사 금융면에 실린 기사는 제목이 평범해도 금융 맥락이다.
    assert judge(url="https://www.reuters.com/markets/asia/some-slug", title="Quiet day") is True
    assert judge(url="https://finance.yahoo.com/news/anything", title="Quiet day") is True
    assert judge(url="https://www.handelsblatt.com/wirtschaft/x", title="Ruhiger Tag") is True
    assert judge(url="https://www.mk.co.kr/경제/123", title="조용한 하루") is True


def test_읽을_글자가_없으면_버리지_않는다() -> None:
    # 판정할 근거가 없는 것을 근거 없이 버리면, 버린 사실조차 확인할 수 없다.
    assert judge(title=None, quote=None, passage=None) is True
    assert judge(title="   ", quote="") is True


def test_인용문에만_금융_낱말이_있어도_남긴다() -> None:
    assert judge(title="Analysts speak", quote="We expect the dividend to hold.") is True


def test_짧은_낱말이_다른_낱말_안에서_걸리지_않는다() -> None:
    # `share` 가 `shareholder` 안에서 걸리는 것은 맞지만, 금융 낱말이 아닌 말 안에서
    # 우연히 걸리면 안 된다.
    # `\bmarket\b` 는 "marketing" 에 걸리지 않는다. 낱말 경계가 실제로 동작한다는 뜻이고,
    # 그래서 이 제목은 금융으로 보지 않는다 - 내가 처음에 반대로 적었다가 테스트가 잡았다.
    assert judge(title="Sharing a recipe for marketing brunch") is False
    assert judge(title="A gentle bonding ceremony in the garden") is False
    # 낱말 그대로 나오면 걸린다.
    assert judge(title="Asian markets open higher") is True
    assert judge(title="Investors bought the bond") is True


def test_판정이_대소문자를_가리지_않는다() -> None:
    assert judge(title="INFLATION SLOWS") is True
    assert judge(title="inflation slows") is True
