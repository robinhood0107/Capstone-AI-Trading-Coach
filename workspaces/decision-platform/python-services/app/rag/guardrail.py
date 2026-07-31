from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote


class GuardrailDecision(str, Enum):
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
        if any(_PERSONALIZED_ADVICE.search(value) for value in variants):
            return GuardrailResult(
                decision=GuardrailDecision.BLOCKED_ADVICE,
                flags=("PERSONALIZED_TRADING_ADVICE",),
                external_processing_allowed=False,
            )

        try:
            label = self._fixture_model(question)
        except Exception:
            return _blocked("CLASSIFIER_UNAVAILABLE")
        if label == "ALLOW":
            return GuardrailResult(
                decision=GuardrailDecision.ALLOW,
                flags=(),
                external_processing_allowed=True,
            )
        if label == "ADVICE":
            return GuardrailResult(
                decision=GuardrailDecision.BLOCKED_ADVICE,
                flags=("PERSONALIZED_TRADING_ADVICE",),
                external_processing_allowed=False,
            )
        if label in {"SENSITIVE", "PROMPT_INJECTION"}:
            flag = "PROMPT_INJECTION" if label == "PROMPT_INJECTION" else "SENSITIVE_DATA"
            return _blocked(flag)
        return _blocked("CLASSIFIER_UNAVAILABLE")


def _blocked(flag: str) -> GuardrailResult:
    return GuardrailResult(
        decision=GuardrailDecision.BLOCKED_SENSITIVE,
        flags=(flag,),
        external_processing_allowed=False,
    )


def _is_bounded_question(question: object) -> bool:
    if not isinstance(question, str) or not 1 <= len(question) <= 1_000:
        return False
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in question
    ):
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
        r"|https?://|https3a2f2f"
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
        r"|(?:비밀|시크릿|토큰|비밀번호|api\W*키)"
    ),
    re.IGNORECASE,
)
_ACCOUNT_OR_HOLDING = re.compile(
    (
        r"\b(?:account\W*(?:number|balance)|holdings?|positions?|orders?|fills?"
        r"|phone\W*number|email\W*address|social\W*security\W*number)\b"
        r"|accountnumber|accountbalance"
        r"|계좌\W*번호|계좌번호|계좌|잔고|보유\W*(?:종목|수량)?|보유종목"
        r"|주문\W*(?:내역|번호)?|체결\W*(?:내역|번호)?"
        r"|전화\W*번호|이메일|주민\W*번호|연락처"
        r"|(?<![\w.+-])[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,253}\.[a-z]{2,63}(?![\w.-])"
        r"|(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"
        r"|(?<!\d)\d{6}[ -]?[1-4]\d{6}(?!\d)"
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
        r"|(?:사라|팔아라|매수해|매도해)"
    ),
    re.IGNORECASE,
)
