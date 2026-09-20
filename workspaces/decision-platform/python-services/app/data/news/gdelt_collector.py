"""GQG/GEMG 분별 파일의 최신 우선 one-cycle 수집기. raw response는 메모리 밖에 남기지 않는다."""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Literal, Protocol

import psycopg

from app.data.news.gdelt_transport import GdeltHttpClient, GdeltWorldNewsTransportError
from app.data.news.repository import WorldNewsCollection
from app.data.news.financial_relevance import is_financially_relevant
from app.data.news.world_news import WorldNewsDocument, WorldNewsError, normalize_world_news

Dataset = Literal["GQG", "GEMG"]

#: 원격 JSON 을 훑는 최대 깊이. 정상 GQG/GEMG 는 2~3 단계다.
_MAX_METADATA_DEPTH = 12

#: 그 파일 하나의 성질이라 건너뛰고 다음 대상으로 가는 실패.
#:
#: GQG/GEMG 는 15분 heartbeat 라 대부분의 분에 파일이 없고(404), 가끔 한 파일이 크기·형식
#: 경계를 넘는다. 이것들을 terminal 로 다루면 첫 하나에서 dataset 전체가 멈춰 나머지
#: 파일에 영영 도달하지 못한다 - 수집이 매 cycle 0 이 되고 화면에는 실패만 남는다.
#:
#: 반대로 전송 경계가 무너진 경우(DNS/TLS 미검증, rebinding, 예산 소진, 네트워크 오류)는
#: 계속 terminal 이다. 그때 남은 호출을 만드는 것은 안전하지 않다.
_SKIPPABLE_FILE_FAILURES = frozenset(
    {
        "GDELT_NOT_PUBLISHED",
        "GDELT_COMPRESSED_SIZE",
        "GDELT_DECOMPRESSED_SIZE",
        "GDELT_TRUNCATED",
        "GDELT_CRC_MISMATCH",
        "GDELT_CRC_OR_TRUNCATION",
        "GDELT_HASH_MISMATCH",
        "GDELT_HASH_AMBIGUOUS",
        "GDELT_HASH_INVALID",
        "GDELT_ENCODING_UNSUPPORTED",
        "GDELT_CONTENT_LENGTH_INVALID",
        "GDELT_CONTENT_LENGTH_AMBIGUOUS",
        "GDELT_FRAMING_CONFLICT",
        "GDELT_ROW_REJECTED_BY_STORE",
    }
)


class WorldNewsCollectionRepository(Protocol):
    def was_completed(self, *, provider: str, cursor_sha256: str) -> bool: ...

    def append_batch(self, documents: tuple[WorldNewsDocument, ...]) -> dict[str, int]: ...

    def append_collection(self, collection: WorldNewsCollection) -> str: ...

    def append_file_batch(
        self,
        documents: tuple[WorldNewsDocument, ...],
        collection: WorldNewsCollection,
    ) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True)
class GdeltFileTarget:
    dataset: Dataset
    minute: datetime

    def __post_init__(self) -> None:
        if self.minute.tzinfo is None or self.minute.utcoffset() is None:
            raise ValueError("GDELT target minute must be timezone-aware")
        normalized = self.minute.astimezone(UTC)
        if normalized.second or normalized.microsecond:
            raise ValueError("GDELT target must be minute-aligned")

    @property
    def provider(self) -> str:
        return f"GDELT_{self.dataset}"

    @property
    def stamp(self) -> str:
        return self.minute.astimezone(UTC).strftime("%Y%m%d%H%M00")

    @property
    def url(self) -> str:
        kind = self.dataset.lower()
        return f"https://data.gdeltproject.org/gdeltv3/{kind}/{self.stamp}.{kind}.json.gz"

    @property
    def cursor_sha256(self) -> str:
        return hashlib.sha256(f"gdelt-minute/v1|{self.dataset}|{self.stamp}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GdeltParseResult:
    documents: tuple[WorldNewsDocument, ...]
    rows_seen: int
    rows_excluded: int
    malformed_rows: int


@dataclass(frozen=True, slots=True)
class GdeltCycleReceipt:
    attempted_files: int
    reused_files: int
    completed_files: int
    failed_files: int
    physical_calls: int
    received_raw_bytes: int
    rows_seen: int
    stored_documents: int
    excluded_rows: int
    stopped_after_failure: bool
    failure_codes: tuple[str, ...]


def latest_targets(
    now: datetime, *, publication_lag_minutes: int = 5
) -> tuple[GdeltFileTarget, ...]:
    """공식 예시처럼 현재 UTC minute에서 5분 전 GQG/GEMG를 한 번씩 고른다."""

    if now.tzinfo is None or now.utcoffset() is None or publication_lag_minutes not in range(2, 16):
        raise ValueError("GDELT collection clock is invalid")
    minute = (now.astimezone(UTC) - timedelta(minutes=publication_lag_minutes)).replace(
        second=0, microsecond=0
    )
    return (GdeltFileTarget("GQG", minute), GdeltFileTarget("GEMG", minute))


def backfill_targets(
    now: datetime,
    *,
    lookback: timedelta,
    publication_lag_minutes: int = 5,
) -> Iterable[GdeltFileTarget]:
    """최신 두 파일을 먼저 내고 과거 minute는 가까운 순서로 보충한다."""

    if lookback <= timedelta(0) or lookback > timedelta(days=30):
        raise ValueError("GDELT lookback is invalid")
    latest = latest_targets(now, publication_lag_minutes=publication_lag_minutes)
    end = latest[0].minute
    minute_count = int(lookback.total_seconds() // 60)
    for offset in range(minute_count):
        minute = end - timedelta(minutes=offset)
        yield GdeltFileTarget("GQG", minute)
        yield GdeltFileTarget("GEMG", minute)


def parse_gdelt_file(
    target: GdeltFileTarget,
    content: bytes,
    *,
    received_at: datetime,
    max_rows: int = 100_000,
) -> GdeltParseResult:
    """JSONL을 line-by-line 정규화하고 원문·raw header를 반환값에 포함하지 않는다."""

    if (
        received_at.tzinfo is None
        or received_at.utcoffset() is None
        or max_rows not in range(1, 100_001)
    ):
        raise ValueError("GDELT parse bounds are invalid")
    documents: list[WorldNewsDocument] = []
    seen_urls: set[str] = set()
    rows_seen = rows_excluded = malformed = 0
    # splitlines()는 압축 해제 payload만큼의 line list를 한 번 더 만든다. BytesIO iterator는
    # 한 줄씩만 잡아 실제 분별 파일의 peak memory를 bounded content + current line으로 제한한다.
    for raw_line in io.BytesIO(content):
        if not raw_line.strip():
            continue
        rows_seen += 1
        if rows_seen > max_rows or len(raw_line) > 1_048_576:
            rows_excluded += 1
            continue
        try:
            item = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            malformed += 1
            continue
        if not isinstance(item, dict):
            malformed += 1
            continue
        document = _normalize_item(target, item, received_at=received_at)
        if document is None or document.canonical_url in seen_urls:
            rows_excluded += 1
            continue
        # GQG/GEMG 는 일반 사건 피드다. 거르지 않으면 게임 제목과 웨딩드레스 기사가
        # "세계 뉴스"로 화면에 오른다. 화면이 아니라 여기서 거른다 - 화면이 내용을
        # 골라내기 시작하면 무엇을 숨겼는지 아무도 모르게 된다. 여기서 거르면 그 수가
        # `rows_excluded` 에 남는다.
        if not is_financially_relevant(
            url=document.canonical_url,
            title=document.title,
            quote=document.bounded_quote,
            passage=document.bounded_passage,
        ):
            rows_excluded += 1
            continue
        seen_urls.add(document.canonical_url)
        documents.append(document)
    return GdeltParseResult(tuple(documents), rows_seen, rows_excluded, malformed)


def run_cycle(
    *,
    targets: Iterable[GdeltFileTarget],
    client: GdeltHttpClient,
    repository: WorldNewsCollectionRepository,
    started_at: datetime,
    stop_after_failure: bool = True,
) -> GdeltCycleReceipt:
    """완료 cursor를 provider 앞에서 재사용하며 첫 terminal 실패 뒤 남은 호출을 만들지 않는다."""

    attempted = reused = completed = failed = rows_seen = stored = excluded = 0
    stopped = False
    failure_codes: list[str] = []
    for target in targets:
        cursor = target.cursor_sha256
        if repository.was_completed(provider=target.provider, cursor_sha256=cursor):
            reused += 1
            continue
        attempted += 1
        collection_started = datetime.now(UTC)
        try:
            content = client.fetch(target.url)
            parsed = parse_gdelt_file(target, content, received_at=datetime.now(UTC))
            status = "PARTIAL" if parsed.malformed_rows or parsed.rows_excluded else "COMPLETE"
            collection = _collection(
                target,
                status=status,
                started_at=collection_started,
                completed_at=datetime.now(UTC) if status == "COMPLETE" else None,
                item_count=len(parsed.documents),
                error_code=None,
            )
            counts = repository.append_file_batch(parsed.documents, collection)
            stored += counts["INSERTED"]
            rows_seen += parsed.rows_seen
            excluded += parsed.rows_excluded + parsed.malformed_rows
            completed += int(status == "COMPLETE")
        except psycopg.errors.IntegrityError as error:
            # 한 행이 DB 제약에 걸리면 그 파일의 배치가 통째로 거부된다. 그걸 잡지
            # 않으면 프로세스가 죽고, 컨테이너가 재시작하며 같은 파일을 다시 집어
            # 수집이 영구히 멈춘다(실제로 26회 재시작했다). 그 파일 하나의 성질로
            # 보고 건너뛴다 - 전송 경계는 멀쩡하므로 남은 대상은 계속 처리한다.
            failed += 1
            failure_codes.append("GDELT_ROW_REJECTED_BY_STORE")
            repository.append_collection(
                _collection(
                    target,
                    status="COLLECTION_FAILED",
                    started_at=collection_started,
                    completed_at=datetime.now(UTC),
                    item_count=0,
                    error_code="GDELT_ROW_REJECTED_BY_STORE",
                )
            )
            print(
                "GDELT_FILE=ROW_REJECTED_BY_STORE sqlstate="
                + str(getattr(error, "sqlstate", None)),
                flush=True,
            )
            continue
        except (GdeltWorldNewsTransportError, WorldNewsError, ValueError) as error:
            failed += 1
            error_code = _safe_error_code(error)
            failure_codes.append(error_code)
            repository.append_collection(
                _collection(
                    target,
                    status="COLLECTION_FAILED",
                    started_at=collection_started,
                    completed_at=datetime.now(UTC),
                    item_count=0,
                    error_code=error_code,
                )
            )
            # 그 파일 하나의 성질은 dataset 전체를 멈추지 않는다. 건너뛰고 다음 대상으로
            # 간다. 호출 예산이 범위를 묶으므로 무한히 훑지 않는다.
            #
            # 멈춰야 하는 것은 전송 경계가 무너진 경우다 - DNS/TLS 미검증, rebinding,
            # 예산 소진, 네트워크 오류. 그때는 남은 호출을 만들지 않는다.
            if stop_after_failure and error_code not in _SKIPPABLE_FILE_FAILURES:
                stopped = True
                break
    return GdeltCycleReceipt(
        attempted,
        reused,
        completed,
        failed,
        client.physical_calls,
        client.received_raw_bytes,
        rows_seen,
        stored,
        excluded,
        stopped,
        tuple(failure_codes),
    )


def _normalize_item(
    target: GdeltFileTarget,
    item: dict[str, Any],
    *,
    received_at: datetime,
) -> WorldNewsDocument | None:
    url = item.get("url")
    if not isinstance(url, str):
        return None
    language = _language(item.get("lang"))
    title: str | None = None
    quote: str | None = None
    passage: str | None = None
    published_at: datetime | None = None
    publication_status: Literal["VERIFIED", "MISSING", "CONFLICT"] = "MISSING"
    if target.dataset == "GQG":
        quotes = item.get("quotes")
        if isinstance(quotes, list):
            for candidate in quotes:
                if not isinstance(candidate, dict):
                    continue
                raw_quote = candidate.get("quote")
                if isinstance(raw_quote, str) and 0 < len(" ".join(raw_quote.split())) <= 600:
                    quote = raw_quote
                    context = " ".join(
                        value
                        for value in (candidate.get("pre"), raw_quote, candidate.get("post"))
                        if isinstance(value, str) and value.strip()
                    )
                    passage = (
                        context
                        if context and len(context) <= 1_200 and context != raw_quote
                        else None
                    )
                    break
    else:
        title = _metadata_text(item, {"title", "headline", "og:title", "twitter:title"}, 300)
        published_at = _metadata_time(item)
        if published_at is not None:
            publication_status = "VERIFIED" if published_at <= target.minute else "CONFLICT"
    if title is None and quote is None and passage is None:
        return None
    provider_id = "gdelt:" + hashlib.sha256(f"{target.dataset}|{url}".encode()).hexdigest()[:32]
    try:
        return normalize_world_news(
            provider=target.provider,  # type: ignore[arg-type]
            canonical_url=url,
            provider_document_id=provider_id,
            source_id="src_gdelt_world_news",
            title=title,
            bounded_quote=quote,
            bounded_passage=passage,
            language=language,
            published_at=published_at,
            publication_status=publication_status,
            provider_observed_at=target.minute,
            received_at=received_at,
            available_at=received_at,
            collection_status="COMPLETE",
            external_llm_allowed=False,
        )
    except WorldNewsError:
        return None


def _metadata_text(value: object, names: set[str], maximum: int, depth: int = 0) -> str | None:
    # 원격 파일의 중첩 깊이는 우리가 정하지 않는다. 제한이 없으면 2KB 짜리
    # `[[[[...]]]]` 한 줄로 RecursionError 가 나고, 그것은 `json.JSONDecodeError`
    # 가 아니라서 파싱 루프가 잡지 못해 수집 사이클 전체가 죽는다.
    if depth > _MAX_METADATA_DEPTH:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in names and isinstance(item, str):
                normalized = " ".join(item.split())
                if 0 < len(normalized) <= maximum:
                    return normalized
            if normalized_key == "key" and str(item).lower() in names:
                candidate = value.get("value")
                if isinstance(candidate, str) and 0 < len(" ".join(candidate.split())) <= maximum:
                    return candidate
        for item in value.values():
            found = _metadata_text(item, names, maximum, depth + 1)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _metadata_text(item, names, maximum, depth + 1)
            if found is not None:
                return found
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _metadata_text(json.loads(value), names, maximum, depth + 1)
        except json.JSONDecodeError:
            return None
    return None


def _metadata_time(value: object) -> datetime | None:
    raw = _metadata_text(
        value,
        {"datepublished", "article:published_time", "datecreated", "pubdate"},
        128,
    )
    if raw is None:
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


def _language(value: object) -> str:
    normalized = str(value or "und").strip().lower()
    aliases = {"korean": "ko", "english": "en", "japanese": "ja", "chinese": "zh"}
    language = aliases.get(normalized, normalized[:3])
    return language if language.isalpha() and len(language) in {2, 3} else "und"


def _collection(
    target: GdeltFileTarget,
    *,
    status: str,
    started_at: datetime,
    completed_at: datetime | None,
    item_count: int,
    error_code: str | None,
) -> WorldNewsCollection:
    identity = hashlib.sha256(
        f"world-news-collection/v1|{target.cursor_sha256}|{started_at.isoformat()}|{status}".encode()
    ).hexdigest()
    return WorldNewsCollection(
        collection_id="news_col_" + identity[:32],
        provider=target.provider,
        collection_status=status,
        started_at=started_at,
        completed_at=completed_at,
        observed_through=target.minute,
        item_count=item_count,
        cursor_sha256=target.cursor_sha256,
        error_code=error_code,
    )


def _safe_error_code(error: Exception) -> str:
    """URL/header/body를 버리고 allowlisted uppercase 상태만 collection receipt에 남긴다."""

    value = str(error)
    return value if re.fullmatch(r"[A-Z0-9_]{1,96}", value) else "BOUNDED_COLLECTION_FAILED"
