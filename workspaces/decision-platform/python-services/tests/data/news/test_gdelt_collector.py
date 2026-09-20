"""GDELT 분별 파일 수집기.

픽스처 URL 이 `/markets/` 인 이유: 수집기는 이제 금융과 무관한 기사를 저장 전에 거른다
(`financial_relevance.py`). 이 파일이 확인하는 것은 파싱 경계·실패 격리·커서 재사용이지
주제 판정이 아니므로, 픽스처가 그 관문을 통과하도록 금융면 경로를 쓴다.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx

from app.data.news.gdelt_collector import (
    GdeltFileTarget,
    backfill_targets,
    latest_targets,
    parse_gdelt_file,
    run_cycle,
)
from app.data.news.gdelt_transport import GdeltHttpClient
from app.data.news.gdelt_collector_cli import _select_targets


NOW = datetime(2026, 9, 8, 3, 6, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.completed: set[tuple[str, str]] = set()
        self.collections = []

    def was_completed(self, *, provider: str, cursor_sha256: str) -> bool:
        return (provider, cursor_sha256) in self.completed

    def append_batch(self, documents):
        return {"INSERTED": len(documents), "OBSERVED": 0, "NO_OP": 0, "IDENTITY_CONFLICT": 0}

    def append_collection(self, collection):
        self.collections.append(collection)
        if collection.collection_status == "COMPLETE":
            self.completed.add((collection.provider, collection.cursor_sha256))
        return "INSERTED"

    def append_file_batch(self, documents, collection):
        counts = self.append_batch(documents)
        self.append_collection(collection)
        return counts


def test_latest_and_backfill_are_minute_aligned_and_latest_first() -> None:
    assert [item.stamp for item in latest_targets(NOW)] == ["20260908030100"] * 2
    targets = list(backfill_targets(NOW, lookback=timedelta(minutes=2)))
    assert [(item.dataset, item.stamp) for item in targets] == [
        ("GQG", "20260908030100"),
        ("GEMG", "20260908030100"),
        ("GQG", "20260908030000"),
        ("GEMG", "20260908030000"),
    ]


def test_gqg_and_gemg_parse_bounded_metadata_without_raw_body() -> None:
    gqg = (
        json.dumps(
            {
                "url": "https://example.com/markets/story",
                "lang": "Korean",
                "quotes": [
                    {"pre": "관계자는", "quote": "공급은 안정적이다.", "post": "라고 말했다."}
                ],
            },
            ensure_ascii=False,
        ).encode()
        + b"\nnot-json\n"
    )
    result = parse_gdelt_file(GdeltFileTarget("GQG", NOW.replace(minute=1)), gqg, received_at=NOW)
    assert result.rows_seen == 2 and result.malformed_rows == 1
    assert result.documents[0].bounded_quote == "공급은 안정적이다."
    assert "raw" not in result.documents[0].projection()
    gemg = json.dumps(
        {
            "url": "https://example.com/markets/story",
            "lang": "English",
            "metatags": [
                {"key": "og:title", "value": "Supply remains stable"},
                {"key": "article:published_time", "value": "2026-09-08T03:00:00Z"},
            ],
        }
    ).encode()
    metadata = parse_gdelt_file(
        GdeltFileTarget("GEMG", NOW.replace(minute=1)), gemg, received_at=NOW
    ).documents[0]
    assert metadata.title == "Supply remains stable" and metadata.publication_status == "VERIFIED"


def test_cycle_reuses_completed_cursor_and_stops_after_first_failure() -> None:
    target = latest_targets(NOW)[0]
    body = gzip.compress(
        json.dumps(
            {
                "url": "https://example.com/markets/story",
                "lang": "English",
                "quotes": [{"quote": "Supply remains stable."}],
            }
        ).encode()
    )
    repository = Repository()
    with GdeltHttpClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"Content-Length": str(len(body)), "Content-Encoding": "gzip"},
                stream=httpx.ByteStream(body),
            )
        ),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=1,
    ) as client:
        first = run_cycle(targets=(target,), client=client, repository=repository, started_at=NOW)
    assert first.completed_files == 1 and first.stored_documents == 1 and first.physical_calls == 1
    with GdeltHttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=1,
    ) as client:
        replay = run_cycle(targets=(target,), client=client, repository=repository, started_at=NOW)
    assert replay.reused_files == 1 and replay.physical_calls == 0

    fresh = GdeltFileTarget("GEMG", target.minute)
    with GdeltHttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=1,
    ) as client:
        failed = run_cycle(targets=(fresh,), client=client, repository=repository, started_at=NOW)
    assert failed.failure_codes == ("GDELT_HTTP_STATUS",)
    assert repository.collections[-1].error_code == "GDELT_HTTP_STATUS"


def test_completed_latest_targets_do_not_consume_the_bounded_recovery_slice() -> None:
    repository = Repository()
    latest = latest_targets(NOW)
    repository.completed.update((target.provider, target.cursor_sha256) for target in latest)
    selected = _select_targets(repository, now=NOW, lookback_hours=1, max_files=2)
    assert [(target.dataset, target.minute) for target in selected] == [
        ("GQG", latest[0].minute - timedelta(minutes=1)),
        ("GEMG", latest[0].minute - timedelta(minutes=1)),
    ]


def _published_body() -> bytes:
    return gzip.compress(
        json.dumps(
            {
                "url": "https://example.com/markets/story",
                "lang": "English",
                "quotes": [{"quote": "Supply remains stable."}],
            }
        ).encode()
    )


def test_an_unpublished_newest_file_does_not_stop_the_recovery_of_older_files() -> None:
    """아직 게시되지 않은 최신 파일이 dataset 전체를 멈추면 수집이 영구 0 이 된다.

    대상 순서가 "최신 먼저"이고 404 를 terminal 로 다루면, 게시 지연이 가정한 5분보다
    길기만 해도 매 cycle 이 첫 대상에서 멈춰 backfill 에 영영 도달하지 못한다.
    2026-09-14 실측에서 GQG/GEMG 최신 두 파일이 모두 404 였고 lookback 을 줘도
    한 건도 받지 못했다.
    """

    body = _published_body()
    newest = GdeltFileTarget("GQG", NOW.replace(minute=1))
    older = GdeltFileTarget("GQG", NOW.replace(minute=0))
    repository = Repository()

    def handler(request: httpx.Request) -> httpx.Response:
        if newest.stamp in str(request.url):
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(body)), "Content-Encoding": "gzip"},
            stream=httpx.ByteStream(body),
        )

    with GdeltHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=2,
    ) as client:
        receipt = run_cycle(
            targets=(newest, older),
            client=client,
            repository=repository,
            started_at=NOW,
        )

    assert receipt.failure_codes == ("GDELT_NOT_PUBLISHED",)
    assert receipt.stopped_after_failure is False
    # 미게시를 건너뛰고 더 오래된 파일을 실제로 받아야 한다.
    assert receipt.completed_files == 1
    assert receipt.stored_documents == 1


def test_a_real_transport_failure_still_stops_the_dataset() -> None:
    """미게시만 예외다. 진짜 실패는 여전히 남은 호출을 만들지 않는다."""

    repository = Repository()
    targets = (
        GdeltFileTarget("GQG", NOW.replace(minute=1)),
        GdeltFileTarget("GQG", NOW.replace(minute=0)),
    )

    with GdeltHttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=2,
    ) as client:
        receipt = run_cycle(targets=targets, client=client, repository=repository, started_at=NOW)

    assert receipt.failure_codes == ("GDELT_HTTP_STATUS",)
    assert receipt.stopped_after_failure is True
    assert receipt.physical_calls == 1


class SettlingRepository(Repository):
    """유예가 지난 미게시 cursor 를 기억하는 저장소."""

    def __init__(self, unpublished: set[str] | None = None) -> None:
        super().__init__()
        self.unpublished = unpublished or set()
        self.asked: list[tuple[str, int]] = []

    def unpublished_cursors(self, *, provider: str, cursors: tuple[str, ...]) -> frozenset[str]:
        self.asked.append((provider, len(cursors)))
        return frozenset(item for item in cursors if item in self.unpublished)


def test_settled_unpublished_minutes_are_not_probed_again() -> None:
    """15분 heartbeat 라 대부분의 분은 비어 있다. 그 분들을 매번 다시 물으면
    같은 예산으로 공백 구간을 건널 수 없다."""

    repository = SettlingRepository()
    # 유예(30분)가 지난 과거 분 하나를 확정 미게시로 기록한다.
    stale = GdeltFileTarget("GQG", NOW.replace(hour=2, minute=0))
    repository.unpublished.add(stale.cursor_sha256)

    selected = _select_targets(repository, now=NOW, lookback_hours=2, max_files=200)

    assert stale.cursor_sha256 not in {target.cursor_sha256 for target in selected}
    # 유예 안쪽 분은 아직 확정이 아니므로 질문 대상에서 빠진다.
    assert all(count > 0 for _, count in repository.asked)


def test_minutes_inside_the_publication_settle_window_stay_eligible() -> None:
    """방금 지난 분은 아직 게시될 수 있다. 404 한 번으로 영구 제외하지 않는다."""

    repository = SettlingRepository()
    recent = GdeltFileTarget("GQG", NOW.replace(minute=1))
    repository.unpublished.add(recent.cursor_sha256)

    selected = _select_targets(repository, now=NOW, lookback_hours=1, max_files=200)

    assert recent.cursor_sha256 in {target.cursor_sha256 for target in selected}


def test_an_old_schema_without_the_filter_still_collects() -> None:
    """V168 이전 스키마에서도 수집이 막히지 않는다 - 거르지 않을 뿐이다."""

    class OldSchemaRepository(Repository):
        def unpublished_cursors(self, *, provider: str, cursors: tuple[str, ...]) -> frozenset[str]:
            return frozenset()

    selected = _select_targets(OldSchemaRepository(), now=NOW, lookback_hours=1, max_files=6)

    assert len(selected) == 6


def test_one_oversized_file_does_not_stop_the_rest_of_the_dataset() -> None:
    """크기 경계는 그 파일 하나의 성질이다. dataset 전체를 멈추면 나머지에 도달 못 한다.

    2026-09-14 실측에서 GQG 한 파일이 해제 한도를 넘어 24개 중 그 지점에서 끊겼다.
    """

    body = _published_body()
    oversized = GdeltFileTarget("GQG", NOW.replace(minute=1))
    normal = GdeltFileTarget("GQG", NOW.replace(minute=0))

    huge = gzip.compress(b"x" * 200_000)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = huge if oversized.stamp in str(request.url) else body
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(payload)), "Content-Encoding": "gzip"},
            stream=httpx.ByteStream(payload),
        )

    with GdeltHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda _: ("8.8.8.8",),
        # 정상 파일은 통과하고 큰 파일만 걸리는 한도.
        compressed_limit=100_000,
        decompressed_limit=100_000,
        physical_call_cap=2,
    ) as client:
        receipt = run_cycle(
            targets=(oversized, normal),
            client=client,
            repository=Repository(),
            started_at=NOW,
        )

    assert receipt.stopped_after_failure is False
    assert receipt.completed_files == 1


def test_a_transport_boundary_failure_is_still_terminal() -> None:
    """전송 경계가 무너지면(DNS 미검증) 남은 호출을 만들지 않는다."""

    repository = Repository()
    targets = (
        GdeltFileTarget("GQG", NOW.replace(minute=1)),
        GdeltFileTarget("GQG", NOW.replace(minute=0)),
    )

    with GdeltHttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        resolver=lambda _: (),
        physical_call_cap=2,
    ) as client:
        receipt = run_cycle(targets=targets, client=client, repository=repository, started_at=NOW)

    assert receipt.failure_codes == ("GDELT_DNS_TLS_UNVERIFIED",)
    assert receipt.stopped_after_failure is True
    assert receipt.physical_calls == 0


def test_a_thirty_day_lookback_does_not_exceed_the_cursor_batch_limit() -> None:
    """30일 lookback 이면 후보가 수만 개다. 한 번에 다 물으면 수집이 그 자리에서 멈춘다.

    실제로 상시 가동을 켰을 때 `world-news cursor batch is too large` 로 컨테이너가
    재시작 루프에 빠졌다.
    """

    seen: list[int] = []

    class BatchingRepository(Repository):
        def unpublished_cursors(self, *, provider: str, cursors: tuple[str, ...]) -> frozenset[str]:
            seen.append(len(cursors))
            return frozenset()

    _select_targets(BatchingRepository(), now=NOW, lookback_hours=24 * 30, max_files=4)

    # 후보가 실제로 수만 개라는 사실을 고정한다. 저장소 구현은 DB 함수 상한(4096) 에
    # 맞춰 나눠 물어야 하며, 그 분할은 PostgresWorldNewsRepository 가 책임진다.
    assert seen, "저장소에 한 번도 묻지 않았다"
    assert max(seen) > 4096


def test_one_file_rejected_by_the_store_does_not_stop_the_rest_of_the_dataset() -> None:
    """DB 제약 위반은 그 파일 하나의 성질이다.

    잡지 않으면 프로세스가 죽고, 컨테이너가 재시작하며 같은 파일을 다시 집어 수집이
    영구히 멈춘다. 2026-09-14 실측에서 26회 재시작했다.
    """

    import psycopg

    body = _published_body()
    rejected = GdeltFileTarget("GQG", NOW.replace(minute=1))
    normal = GdeltFileTarget("GQG", NOW.replace(minute=0))

    class RejectingRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def append_file_batch(self, documents, collection):
            self.attempts += 1
            if self.attempts == 1:
                raise psycopg.errors.CheckViolation("world_news_passage_check")
            return super().append_file_batch(documents, collection)

    repository = RejectingRepository()

    with GdeltHttpClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": str(len(body)), "Content-Encoding": "gzip"},
                stream=httpx.ByteStream(body),
            )
        ),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=2,
    ) as client:
        receipt = run_cycle(
            targets=(rejected, normal),
            client=client,
            repository=repository,
            started_at=NOW,
        )

    assert repository.attempts == 2, "첫 파일에서 멈추면 안 된다"
    assert receipt.stopped_after_failure is False
    assert receipt.failure_codes == ("GDELT_ROW_REJECTED_BY_STORE",)
    assert receipt.completed_files == 1
    assert any(item.error_code == "GDELT_ROW_REJECTED_BY_STORE" for item in repository.collections)
