from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote


class GuardrailDecision(StrEnum):
    ALLOW = "ALLOW"
    BLOCKED_SENSITIVE = "BLOCKED_SENSITIVE"
    BLOCKED_ADVICE = "BLOCKED_ADVICE"


@dataclass(frozen=True)
class GuardrailResult:
    decision: GuardrailDecision
    flags: tuple[str, ...]
    external_processing_allowed: bool


FixtureModel = Callable[[str], str]


class BoundedFixtureGuardrail:
    """질문 원문을 외부로 보내기 전에 결정적 규칙과 bounded local fixture label을 적용한다."""

    def __init__(self, *, fixture_model: FixtureModel | None = None) -> None:
        self._fixture_model = fixture_model or (lambda _question: "ALLOW")

    def classify(self, question: str) -> GuardrailResult:
        if not _is_bounded_question(question):
            return _blocked("INVALID_OR_OVERSIZED_INPUT")

        variants = _normalized_variants(question)
        if any(_PROMPT_INJECTION.search(value) for value in variants):
            return _blocked("PROMPT_INJECTION")
        if any(_SECRET_OR_TOKEN.search(value) for value in variants):
            return _blocked("SECRET_OR_TOKEN")
        if any(_ACCOUNT_OR_HOLDING.search(value) for value in variants):
            return _blocked("ACCOUNT_OR_HOLDING_DATA")

        # 조언성 질문은 더 이상 차단하지 않는다. 앞단에서 막으면 "금 ETF의 롤오버 위험"
        # 같은 순수 개념 질문까지 설명 없이 닫혔고, 사용자가 얻는 것은 답이 아니라 거절이었다.
        # 조언이 아니라는 사실은 동의 화면의 고지와 프롬프트 경계가 말한다. 여기서는
        # 관측 flag만 남겨 화면이 그 맥락을 함께 보여줄 수 있게 한다.
        flags: tuple[str, ...] = ()
        if any(_PERSONALIZED_ADVICE.search(value) for value in variants):
            flags = ("PERSONALIZED_TRADING_ADVICE",)

        try:
            label = self._fixture_model(question)
        except Exception:
            # 분류기 장애로 설명 기능 전체가 닫히지 않게 한다. 위의 결정적 규칙이 이미
            # PII·인젝션 경계를 지켰으므로 여기서 fail-closed할 이유가 남지 않는다.
            return _allowed(*flags, "CLASSIFIER_UNAVAILABLE")
        if label == "ALLOW":
            return _allowed(*flags)
        if label == "ADVICE":
            return _allowed(*flags, "PERSONALIZED_TRADING_ADVICE")
        if label in {"SENSITIVE", "PROMPT_INJECTION"}:
            flag = "PROMPT_INJECTION" if label == "PROMPT_INJECTION" else "SENSITIVE_DATA"
            return _blocked(flag)
        return _allowed(*flags, "CLASSIFIER_UNAVAILABLE")


def _allowed(*flags: str) -> GuardrailResult:
    """차단하지 않고 통과시키되 무엇이 관측됐는지는 응답까지 들고 간다."""

    return GuardrailResult(
        decision=GuardrailDecision.ALLOW,
        flags=tuple(dict.fromkeys(flags)),
        external_processing_allowed=True,
    )


def _blocked(flag: str) -> GuardrailResult:
    return GuardrailResult(
        decision=GuardrailDecision.BLOCKED_SENSITIVE,
        flags=(flag,),
        external_processing_allowed=False,
    )


def _is_bounded_question(question: object) -> bool:
    if not isinstance(question, str) or not 1 <= len(question) <= 1_000:
        return False
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in question):
        return False
    try:
        return len(question.encode("utf-8")) <= 8_192
    except UnicodeEncodeError:
        return False


def _normalized_variants(question: str) -> tuple[str, ...]:
    decoded: list[str] = [question, html.unescape(question)]
    url_decoded = question
    for _ in range(2):
        next_value = unquote(url_decoded)
        if next_value == url_decoded:
            break
        decoded.append(next_value)
        url_decoded = next_value
    base64_value = _decode_base64(question)
    if base64_value is not None:
        decoded.append(base64_value)

    variants: list[str] = []
    for value in decoded:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        normalized = "".join(
            character
            for character in normalized
            if unicodedata.category(character) not in {"Cf", "Cs"}
        )
        compact = "".join(character for character in normalized if character.isalnum())
        variants.extend((normalized, compact))
    return tuple(dict.fromkeys(variants))


def _decode_base64(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value)
    if (
        len(compact) < 16
        or len(compact) > 4_096
        or len(compact) % 4 != 0
        or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact) is None
    ):
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return text if 1 <= len(text) <= 2_048 else None


_PROMPT_INJECTION = re.compile(
    (
        r"(?:ignore|disregard|bypass)\W*(?:all\W*)?(?:previous|prior|system)"
        r"|ignorepreviousinstructions"
        r"|system\W*prompt|systemprompt"
        r"|(?:reveal|print|exfiltrate|send)\W*.{0,50}"
        r"(?:secret|credential|token|prompt)"
        r"|(?:call|invoke|execute|run)\W*.{0,30}(?:tool|function|mcp|plugin|code|shell)"
        r"|(?:이전|기존)\W*지시.{0,16}무시"
        r"|시스템\W*프롬프트|시스템프롬프트"
        r"|(?:도구|함수|mcp|플러그인).{0,20}(?:호출|실행)"
        r"|(?:비밀|토큰|프롬프트).{0,20}(?:출력|노출|전송)"
    ),
    re.IGNORECASE,
)
_SECRET_OR_TOKEN = re.compile(
    (
        r"\b(?:access\W*token|refresh\W*token|api\W*key|client\W*secret|password)\b"
        r"|accesstoken|refreshtoken|apikey|clientsecret"
        r"|\bbearer\W+[a-z0-9._~-]{8,}"
        r"|\bsk-[a-z0-9_-]{16,}\b"
        r"|비밀번호|시크릿\W*키|시크릿키|api\W*키|api키"
    ),
    re.IGNORECASE,
)
# "보유", "주문", "체결", "잔고", "계좌"는 금융 개념 질문의 기본 어휘다. 그 명사 하나로
# 질문을 닫으면 이 기능이 설명할 수 있는 주제가 거의 남지 않는다. 막아야 하는 것은 개념이
# 아니라 "특정인의 자료를 달라"는 요구이므로, 실제 식별자와 그 요구의 형태만 잡는다.
_ACCOUNT_OR_HOLDING = re.compile(
    (
        # 그 자체로 식별자인 것.
        r"\b(?:account\W*(?:number|balance)|social\W*security\W*number)\b"
        r"|accountnumber|accountbalance"
        r"|계좌\W*번호|계좌번호"
        r"|주민\W*(?:등록)?\W*번호|주민번호|주민등록번호"
        r"|(?<![\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\.[a-z]{2,63}(?![\w.-])"
        r"|(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"
        r"|(?<!\d)\d{6}[ -]?[1-4]\d{6}(?!\d)"
        # 누군가의 계좌 자료를 지목하는 요구. 소유격이 붙거나 조회·추출을 요구할 때만이다.
        # `내 `, `제 `는 뒤에 공백을 요구한다. 그렇지 않으면 "내일", "내년", "제도"가
        # 소유격으로 오인된다. 공백을 지운 우회는 compact variant가 식별자 패턴으로 잡는다.
        r"|(?:내\W+|제\W+|나의|저의|본인|타인|남의|다른\W*사용자|다른\W*사람)"
        r"\W*.{0,3}(?:계좌|잔고|보유\W*종목|보유종목|포지션|주문\W*내역|주문내역|체결\W*내역|체결내역)"
        r"|(?:계좌|잔고|보유\W*종목|보유종목|주문\W*내역|주문내역|체결\W*내역|체결내역)"
        r".{0,16}(?:조회|추출|공개|열람|내려받|다운로드|알려|보여)"
        r"|\bmy\W+(?:\w+\W+)?(?:account|balance|holdings?|positions?|orders?|fills?)\b"
        r"|\b(?:another|other)\W+users?\W+(?:account|balance|holdings?|positions?|orders?)\b"
    ),
    re.IGNORECASE,
)
_PERSONALIZED_ADVICE = re.compile(
    (
        r"\b(?:should\W+i\W+(?:buy|sell)|how\W+many\W+(?:shares?|units?)"
        r"|when\W+should\W+i\W+(?:buy|sell)|position\W*size)\b"
        r"|shouldibuy|shouldisell|howmanyshares|whenbuy|whensell"
        r"|(?:내가|나는|저는|제게|나한테).{0,24}(?:사야|팔아|매수|매도)"
        r"|(?:몇\W*주|얼마나).{0,20}(?:사|매수|팔|매도)"
        r"|(?:언제|내일|지금).{0,20}(?:사야|팔아|매수|매도)"
        r"|(?:지금|오늘|내일)?.{0,24}(?:사|팔|매수|매도)(?:아|어|해)?(?:도|면)"
        r".{0,12}(?:되|될|좋|괜찮)"
        r"|(?:사라|팔아라|매수해|매도해)"
    ),
    re.IGNORECASE,
)
