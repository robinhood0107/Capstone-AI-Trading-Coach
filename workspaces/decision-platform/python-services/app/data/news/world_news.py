"""기사 원문 없이 조회와 RAG에 필요한 세계 뉴스 v2 문서를 정규화한다."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from urllib.parse import urlsplit, urlunsplit

from app.data._shared.canonical_json import canonical_json_bytes

Provider = Literal["GDELT_GQG", "GDELT_GEMG", "FINNHUB_MARKET_NEWS"]
PublicationStatus = Literal["VERIFIED", "MISSING", "CONFLICT"]
IdentityStatus = Literal["VERIFIED", "PROVIDER_ID_CONFLICT", "URL_HASH_CONFLICT"]
CollectionStatus = Literal["COMPLETE", "PARTIAL", "COLLECTION_FAILED", "NOT_COLLECTED"]
RightsProfile = Literal["GDELT_METADATA_QUOTE", "FINNHUB_PERSONAL_LOCAL"]

_PROVIDERS: Final = {"GDELT_GQG", "GDELT_GEMG", "FINNHUB_MARKET_NEWS"}
_PUBLICATION_STATUSES: Final = {"VERIFIED", "MISSING", "CONFLICT"}
_IDENTITY_STATUSES: Final = {"VERIFIED", "PROVIDER_ID_CONFLICT", "URL_HASH_CONFLICT"}
_COLLECTION_STATUSES: Final = {"COMPLETE", "PARTIAL", "COLLECTION_FAILED", "NOT_COLLECTED"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
# DB CHECK 은 `~ '[[:cntrl:]]'` 이고 Postgres 의 그 class 는 C0(U+0000-U+001F), DEL,
# **그리고 C1(U+0080-U+009F)** 까지 잡는다. 여기가 더 좁으면 파이썬이 통과시킨 텍스트를
# DB 가 거부해 그 파일의 배치 INSERT 전체가 깨진다 - GDELT 에는 Windows-1252 mojibake
# 때문에 C1 이 실제로 섞여 들어온다. 두 경계를 같은 범위로 맞춘다. 탭/개행/복귀는
# 앞선 공백 정규화가 이미 지우므로 범위에 넣어도 정상 문서를 버리지 않는다.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_TITLE = 300
_MAX_QUOTE = 600
_MAX_PASSAGE = 1_200


class WorldNewsError(ValueError):
    """세계 뉴스 문서가 bounded 조회/RAG 계약을 벗어났다."""


@dataclass(frozen=True, slots=True)
class WorldNewsDocument:
    """한 canonical URL의 한 content version과 최신 provider 관측을 함께 운반한다."""

    document_id: str
    document_version_id: str
    source_id: str
    provider: Provider
    provider_document_id: str | None
    canonical_url: str
    canonical_url_sha256: str
    republication_of_document_id: str | None
    identity_status: IdentityStatus
    title: str | None
    bounded_quote: str | None
    bounded_passage: str | None
    canonical_content: str
    language: str
    published_at: datetime | None
    publication_status: PublicationStatus
    provider_observed_at: datetime
    first_seen_at: datetime
    available_at: datetime
    rights_profile: RightsProfile
    external_llm_allowed: bool
    lookup_allowed: bool
    rag_retrieval_allowed: bool
    prompt_untrusted: bool
    collection_status: CollectionStatus
    content_sha256: str
    version_sha256: str

    def __post_init__(self) -> None:
        aware_times = (self.provider_observed_at, self.first_seen_at, self.available_at)
        if self.provider not in _PROVIDERS or any(not _aware(value) for value in aware_times):
            raise WorldNewsError("WORLD_NEWS_IDENTITY_OR_TIME_INVALID")
        if self.published_at is not None and not _aware(self.published_at):
            raise WorldNewsError("WORLD_NEWS_PUBLICATION_TIME_INVALID")
        if not _SOURCE_ID.fullmatch(self.source_id):
            raise WorldNewsError("WORLD_NEWS_SOURCE_INVALID")
        if not self.document_id.startswith("news_doc_") or not self.document_version_id.startswith(
            "news_ver_"
        ):
            raise WorldNewsError("WORLD_NEWS_IDENTITY_INVALID")
        if self.provider_document_id is not None and not _SAFE_ID.fullmatch(
            self.provider_document_id
        ):
            raise WorldNewsError("WORLD_NEWS_PROVIDER_ID_INVALID")
        if self.republication_of_document_id == self.document_id:
            raise WorldNewsError("WORLD_NEWS_LINEAGE_INVALID")
        if self.identity_status not in _IDENTITY_STATUSES:
            raise WorldNewsError("WORLD_NEWS_IDENTITY_STATUS_INVALID")
        if self.publication_status not in _PUBLICATION_STATUSES:
            raise WorldNewsError("WORLD_NEWS_PUBLICATION_STATUS_INVALID")
        if self.collection_status not in _COLLECTION_STATUSES:
            raise WorldNewsError("WORLD_NEWS_COLLECTION_STATUS_INVALID")
        if not self.lookup_allowed or not self.rag_retrieval_allowed:
            raise WorldNewsError("WORLD_NEWS_RETRIEVAL_DISABLED_DOCUMENT")
        if not self.prompt_untrusted:
            raise WorldNewsError("WORLD_NEWS_PROMPT_TRUST_INVALID")
        if self.first_seen_at > self.available_at:
            raise WorldNewsError("WORLD_NEWS_AVAILABILITY_PRECEDES_FIRST_SEEN")
        if self.publication_status == "MISSING" and self.published_at is not None:
            raise WorldNewsError("WORLD_NEWS_MISSING_PUBLICATION_MUST_BE_NULL")
        if self.publication_status == "VERIFIED" and self.published_at is None:
            raise WorldNewsError("WORLD_NEWS_VERIFIED_PUBLICATION_REQUIRED")
        if (
            self.publication_status == "VERIFIED"
            and self.published_at is not None
            and self.published_at > self.provider_observed_at
        ):
            raise WorldNewsError("WORLD_NEWS_FUTURE_PUBLICATION_MUST_CONFLICT")
        if self.provider == "FINNHUB_MARKET_NEWS" and self.external_llm_allowed:
            raise WorldNewsError("WORLD_NEWS_FINNHUB_EXTERNAL_LLM_FORBIDDEN")
        if (
            self.provider == "FINNHUB_MARKET_NEWS"
            and self.rights_profile != "FINNHUB_PERSONAL_LOCAL"
        ):
            raise WorldNewsError("WORLD_NEWS_RIGHTS_PROFILE_INVALID")
        if self.provider != "FINNHUB_MARKET_NEWS" and self.rights_profile != "GDELT_METADATA_QUOTE":
            raise WorldNewsError("WORLD_NEWS_RIGHTS_PROFILE_INVALID")
        if not _LANGUAGE.fullmatch(self.language):
            raise WorldNewsError("WORLD_NEWS_LANGUAGE_INVALID")
        _validate_https(self.canonical_url)
        if hashlib.sha256(self.canonical_url.encode()).hexdigest() != self.canonical_url_sha256:
            raise WorldNewsError("WORLD_NEWS_URL_HASH_INVALID")
        for value, maximum in (
            (self.title, _MAX_TITLE),
            (self.bounded_quote, _MAX_QUOTE),
            (self.bounded_passage, _MAX_PASSAGE),
        ):
            _validate_bounded_text(value, maximum=maximum)
        expected_content = _canonical_content(self.title, self.bounded_quote, self.bounded_passage)
        if self.canonical_content != expected_content:
            raise WorldNewsError("WORLD_NEWS_CANONICAL_CONTENT_INVALID")
        if hashlib.sha256(expected_content.encode()).hexdigest() != self.content_sha256:
            raise WorldNewsError("WORLD_NEWS_CONTENT_HASH_INVALID")
        if not _HASH.fullmatch(self.version_sha256):
            raise WorldNewsError("WORLD_NEWS_VERSION_HASH_INVALID")

    def projection(self) -> dict[str, object]:
        """DB writer용 exact projection. provider raw body/header와 기사 원문은 없다."""

        return {
            "documentId": self.document_id,
            "documentVersionId": self.document_version_id,
            "sourceId": self.source_id,
            "provider": self.provider,
            "providerDocumentId": self.provider_document_id,
            "canonicalUrl": self.canonical_url,
            "canonicalUrlSha256": self.canonical_url_sha256,
            "republicationOfDocumentId": self.republication_of_document_id,
            "identityStatus": self.identity_status,
            "title": self.title,
            "boundedQuote": self.bounded_quote,
            "boundedPassage": self.bounded_passage,
            "canonicalContent": self.canonical_content,
            "language": self.language,
            "publishedAt": _time(self.published_at),
            "publicationStatus": self.publication_status,
            "providerObservedAt": _time(self.provider_observed_at),
            "firstSeenAt": _time(self.first_seen_at),
            "availableAt": _time(self.available_at),
            "rightsProfile": self.rights_profile,
            "externalLlmAllowed": self.external_llm_allowed,
            "lookupAllowed": self.lookup_allowed,
            "ragRetrievalAllowed": self.rag_retrieval_allowed,
            "promptUntrusted": self.prompt_untrusted,
            "collectionStatus": self.collection_status,
            "contentSha256": self.content_sha256,
            "versionSha256": self.version_sha256,
        }


def normalize_world_news(
    *,
    provider: Provider,
    canonical_url: str,
    provider_document_id: str | None,
    source_id: str,
    title: str | None,
    bounded_quote: str | None,
    bounded_passage: str | None,
    language: str,
    published_at: datetime | None,
    publication_status: PublicationStatus,
    provider_observed_at: datetime,
    received_at: datetime,
    available_at: datetime,
    republication_of_document_id: str | None = None,
    identity_status: IdentityStatus = "VERIFIED",
    collection_status: CollectionStatus = "COMPLETE",
    external_llm_allowed: bool = False,
    lookup_allowed: bool = True,
    rag_retrieval_allowed: bool = True,
) -> WorldNewsDocument:
    """URL identity와 content version을 만들되 인용을 의미가 바뀌게 자르지 않는다."""

    canonical = canonicalize_https_url(canonical_url)
    normalized_title = _normalize_bounded(title, maximum=_MAX_TITLE, field="TITLE")
    normalized_quote = _normalize_bounded(bounded_quote, maximum=_MAX_QUOTE, field="QUOTE")
    normalized_passage = _normalize_bounded(bounded_passage, maximum=_MAX_PASSAGE, field="PASSAGE")
    canonical_content = _canonical_content(normalized_title, normalized_quote, normalized_passage)
    url_hash = hashlib.sha256(canonical.encode()).hexdigest()
    document_hash = hashlib.sha256(f"world-news/v2|{provider}|{url_hash}".encode()).hexdigest()
    document_id = f"news_doc_{document_hash[:32]}"
    content_sha256 = hashlib.sha256(canonical_content.encode()).hexdigest()
    rights_profile: RightsProfile = (
        "FINNHUB_PERSONAL_LOCAL" if provider == "FINNHUB_MARKET_NEWS" else "GDELT_METADATA_QUOTE"
    )
    version_preimage = {
        "documentId": document_id,
        "contentSha256": content_sha256,
        "language": language,
        "publicationStatus": publication_status,
        "publishedAt": _time(published_at),
        "rightsProfile": rights_profile,
    }
    version_sha256 = hashlib.sha256(canonical_json_bytes(version_preimage)).hexdigest()
    return WorldNewsDocument(
        document_id=document_id,
        document_version_id=f"news_ver_{version_sha256[:32]}",
        source_id=source_id,
        provider=provider,
        provider_document_id=provider_document_id,
        canonical_url=canonical,
        canonical_url_sha256=url_hash,
        republication_of_document_id=republication_of_document_id,
        identity_status=identity_status,
        title=normalized_title,
        bounded_quote=normalized_quote,
        bounded_passage=normalized_passage,
        canonical_content=canonical_content,
        language=language,
        published_at=published_at,
        publication_status=publication_status,
        provider_observed_at=provider_observed_at,
        first_seen_at=received_at,
        available_at=available_at,
        rights_profile=rights_profile,
        external_llm_allowed=external_llm_allowed,
        lookup_allowed=lookup_allowed,
        rag_retrieval_allowed=rag_retrieval_allowed,
        prompt_untrusted=True,
        collection_status=collection_status,
        content_sha256=content_sha256,
        version_sha256=version_sha256,
    )


def canonicalize_https_url(value: str) -> str:
    """fragment/default port/userinfo를 거부하고 stable HTTPS identity를 만든다."""

    if not isinstance(value, str) or len(value) > 2_048:
        raise WorldNewsError("WORLD_NEWS_URL_INVALID")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise WorldNewsError("WORLD_NEWS_URL_INVALID")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _canonical_content(title: str | None, quote: str | None, passage: str | None) -> str:
    fields = tuple(value for value in (title, quote, passage) if value is not None)
    if not fields:
        raise WorldNewsError("WORLD_NEWS_BOUNDED_CONTENT_REQUIRED")
    result = "\n".join(fields)
    if len(result.encode("utf-8")) > 4_096:
        raise WorldNewsError("WORLD_NEWS_CANONICAL_CONTENT_TOO_LARGE")
    return result


def _normalize_bounded(value: str | None, *, maximum: int, field: str) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", " ".join(value.split()))
    if not normalized or len(normalized) > maximum or _CONTROL.search(normalized):
        # 긴 quote를 자르면 부정이나 조건절이 사라질 수 있으므로 caller가 완결 구간을 다시 선택한다.
        raise WorldNewsError(f"WORLD_NEWS_{field}_NOT_SAFELY_BOUNDED")
    return normalized


def _validate_bounded_text(value: str | None, *, maximum: int) -> None:
    if value is not None and (
        not value
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
    ):
        raise WorldNewsError("WORLD_NEWS_BOUNDED_CONTENT_INVALID")


def _validate_https(value: str) -> None:
    if canonicalize_https_url(value) != value:
        raise WorldNewsError("WORLD_NEWS_URL_NOT_CANONICAL")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _time(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
