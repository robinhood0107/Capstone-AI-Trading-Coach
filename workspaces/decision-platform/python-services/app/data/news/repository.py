"""세계 뉴스 v2의 최소권한 PostgreSQL writer와 bounded lookup reader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.data.news.world_news import WorldNewsDocument

_WRITER_ROLE: Final = "decision_market_writer"
_APPEND_SIGNATURE: Final = "public.append_world_news_document_v2(jsonb)"
_COLLECTION_SIGNATURE: Final = "public.append_world_news_collection_v2(jsonb)"
_COMPLETED_SIGNATURE: Final = "public.world_news_collection_completed_v1(text,text)"
_FILE_BATCH_SIGNATURE: Final = "public.append_world_news_file_batch_v1(jsonb,jsonb)"
_TABLES: Final = ("public.world_news_documents_v2", "public.world_news_document_versions_v2")
#: 미게시 cursor 를 한 번에 묻는 최대 개수. DB 함수의 상한(4096)과 같아야 한다.
_CURSOR_BATCH: Final = 4_000
_RETENTION_ROLE: Final = "decision_worker"
_RETENTION_SIGNATURE: Final = (
    "public.p1_world_news_retention_v1(boolean,timestamp with time zone,integer)"
)


@dataclass(frozen=True, slots=True)
class WorldNewsLookup:
    document_id: str
    document_version_id: str
    source_id: str
    provider: str
    provider_document_id: str | None
    canonical_url: str
    republication_of_document_id: str | None
    identity_status: str
    title: str | None
    bounded_quote: str | None
    bounded_passage: str | None
    language: str
    published_at: datetime | None
    publication_status: str
    provider_observed_at: datetime
    first_seen_at: datetime
    available_at: datetime
    rights_profile: str
    external_llm_allowed: bool
    lookup_allowed: bool
    rag_retrieval_allowed: bool
    prompt_untrusted: bool
    collection_status: str
    content_sha256: str
    version_sha256: str


@dataclass(frozen=True, slots=True)
class WorldNewsCollection:
    collection_id: str
    provider: str
    collection_status: str
    started_at: datetime
    completed_at: datetime | None
    observed_through: datetime | None
    item_count: int
    cursor_sha256: str | None
    error_code: str | None
    previous_collection_id: str | None = None

    def projection(self) -> dict[str, object]:
        return {
            "collectionId": self.collection_id,
            "provider": self.provider,
            "collectionStatus": self.collection_status,
            "startedAt": _time(self.started_at),
            "completedAt": _time(self.completed_at),
            "observedThrough": _time(self.observed_through),
            "itemCount": self.item_count,
            "cursorSha256": self.cursor_sha256,
            "errorCode": self.error_code,
            "previousCollectionId": self.previous_collection_id,
        }


def _scalar(row: Any, name: str) -> Any:
    """행 형식(tuple/dict)과 무관하게 단일 값을 읽는다.

    같은 검증 함수를 tuple row 호출자와 dict_row 호출자가 함께 쓴다. 튜플 인덱싱만
    하면 dict_row 쪽에서 KeyError 로 터지고, 그 경로의 적재가 통째로 실패한다.
    """

    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(name)
    return row[0]


class PostgresWorldNewsRepository:
    """definer function 전용 DSN으로만 bounded metadata를 append/read한다."""

    def __init__(self, database_dsn: str) -> None:
        if not database_dsn.strip() or len(database_dsn) > 4_096:
            raise ValueError("world-news database DSN is invalid")
        self._database_dsn = database_dsn

    def append(self, document: WorldNewsDocument) -> str:
        """동일 version 관측은 firstSeenAt을 바꾸지 않고 observation만 보충한다."""

        with psycopg.connect(self._database_dsn, autocommit=False) as connection:
            self._attest_writer(connection)
            row = connection.execute(
                "SELECT public.append_world_news_document_v2(%s::jsonb)",
                (Jsonb(document.projection()),),
            ).fetchone()
        disposition = str(row[0]) if row else ""
        if disposition not in {"INSERTED", "OBSERVED", "NO_OP", "IDENTITY_CONFLICT"}:
            raise ValueError("world-news append disposition is invalid")
        return disposition

    def append_batch(self, documents: tuple[WorldNewsDocument, ...]) -> dict[str, int]:
        """한 파일의 bounded 문서를 한 connection/transaction에서 append해 per-row 접속을 피한다."""

        counts = {"INSERTED": 0, "OBSERVED": 0, "NO_OP": 0, "IDENTITY_CONFLICT": 0}
        with psycopg.connect(self._database_dsn, autocommit=False) as connection:
            self._attest_writer(connection)
            for document in documents:
                row = connection.execute(
                    "SELECT public.append_world_news_document_v2(%s::jsonb)",
                    (Jsonb(document.projection()),),
                ).fetchone()
                disposition = str(row[0]) if row else ""
                if disposition not in counts:
                    raise ValueError("world-news append disposition is invalid")
                counts[disposition] += 1
        return counts

    def append_file_batch(
        self,
        documents: tuple[WorldNewsDocument, ...],
        collection: WorldNewsCollection,
    ) -> dict[str, int]:
        """한 분 파일의 문서와 COMPLETE/PARTIAL cursor를 하나의 DB transaction에 commit한다."""

        with psycopg.connect(
            self._database_dsn, autocommit=False, row_factory=dict_row
        ) as connection:
            self._attest_writer(connection)
            row = connection.execute(
                "SELECT * FROM public.append_world_news_file_batch_v1(%s::jsonb,%s::jsonb)",
                (
                    Jsonb([document.projection() for document in documents]),
                    Jsonb(collection.projection()),
                ),
            ).fetchone()
        if row is None:
            raise ValueError("world-news file batch result is unavailable")
        return {
            "INSERTED": int(row["inserted_count"]),
            "OBSERVED": int(row["observed_count"]),
            "NO_OP": int(row["no_op_count"]),
            "IDENTITY_CONFLICT": int(row["identity_conflict_count"]),
        }

    def was_completed(self, *, provider: str, cursor_sha256: str) -> bool:
        """완료 file cursor만 재사용한다. PARTIAL/실패는 gap으로 남겨 다시 처리할 수 있다."""

        with psycopg.connect(self._database_dsn) as connection:
            self._attest_writer(connection)
            row = connection.execute(
                "SELECT public.world_news_collection_completed_v1(%s,%s)",
                (provider, cursor_sha256),
            ).fetchone()
        return bool(row and row[0] is True)

    def unpublished_cursors(self, *, provider: str, cursors: tuple[str, ...]) -> frozenset[str]:
        """앞으로도 생기지 않을 분(cursor)을 한 번에 걸러낸다.

        GQG/GEMG 는 15분 heartbeat 라 대부분의 분에 파일이 없다. 그 빈 분들을 매 실행마다
        다시 물으면 호출만 태우고 공백 구간을 건널 예산이 남지 않는다. 게시 유예가 지난
        404 는 확정이므로 다음부터 건너뛴다. 유예 판정은 호출자가 한다.
        """

        if not cursors:
            return frozenset()
        found: set[str] = set()
        with psycopg.connect(self._database_dsn) as connection:
            self._attest_writer(connection)
            # 30일 lookback 이면 후보가 수만 개다. 함수는 한 번에 4096 개까지만 받으므로
            # 나눠 묻는다. 한 번에 다 보내면 그 자리에서 수집이 멈춘다.
            for start in range(0, len(cursors), _CURSOR_BATCH):
                batch = list(cursors[start : start + _CURSOR_BATCH])
                try:
                    row = connection.execute(
                        "SELECT public.world_news_unpublished_cursors_v1(%s,%s::text[]) AS settled",
                        (provider, batch),
                    ).fetchone()
                except psycopg.errors.UndefinedFunction:
                    # V168 이전 스키마다. 거르지 않으면 예전처럼 다시 물을 뿐 수집은
                    # 계속된다. 이 최적화를 필수 검증에 넣으면 구 스키마에서 전체가 막힌다.
                    return frozenset()
                settled = _scalar(row, "settled")
                if settled:
                    found.update(settled)
        return frozenset(found)

    def append_collection(self, collection: WorldNewsCollection) -> str:
        """정상 empty/partial/failure/resume를 문서 행과 독립된 append-only 상태로 남긴다."""

        with psycopg.connect(self._database_dsn, autocommit=False) as connection:
            self._attest_writer(connection)
            row = connection.execute(
                "SELECT public.append_world_news_collection_v2(%s::jsonb)",
                (Jsonb(collection.projection()),),
            ).fetchone()
        disposition = str(row[0]) if row else ""
        if disposition not in {"INSERTED", "NO_OP"}:
            raise ValueError("world-news collection disposition is invalid")
        return disposition

    def lookup(
        self,
        *,
        query: str = "",
        limit: int = 20,
        as_of: datetime | None = None,
    ) -> tuple[WorldNewsLookup, ...]:
        """publishedAt null도 조회하되 availableAt 이후에만 보인다."""

        instant = as_of or datetime.now(UTC)
        with psycopg.connect(self._database_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT * FROM public.read_world_news_documents_v2(%s,%s,%s)",
                (query, instant, limit),
            ).fetchall()
        fields = WorldNewsLookup.__dataclass_fields__
        return tuple(WorldNewsLookup(**{key: row[key] for key in fields}) for row in rows)

    def _attest_writer(self, connection: psycopg.Connection[Any]) -> None:
        # 호출자마다 row_factory 가 다르다. append_file_batch 는 dict_row 라 튜플 인덱싱이
        # KeyError 로 터졌고, 그래서 파일 단위 적재는 한 번도 성공한 적이 없다.
        # 결과 이름을 고정해 행 형식과 무관하게 읽는다.
        role = connection.execute("SELECT current_user AS role_name").fetchone()
        if _scalar(role, "role_name") != _WRITER_ROLE:
            raise ValueError("world-news DSN must use decision_market_writer")
        for signature in (
            _APPEND_SIGNATURE,
            _COLLECTION_SIGNATURE,
            _COMPLETED_SIGNATURE,
            _FILE_BATCH_SIGNATURE,
        ):
            allowed = connection.execute(
                "SELECT has_function_privilege(current_user,%s,'EXECUTE') AS granted",
                (signature,),
            ).fetchone()
            if _scalar(allowed, "granted") is not True:
                raise ValueError("world-news writer function is unavailable")
        direct = connection.execute(
            """
            SELECT coalesce(bool_or(has_table_privilege(current_user,table_name::regclass,privilege)),false) AS granted
            FROM unnest(%s::text[]) table_name
            CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) privilege
            """,
            (list(_TABLES),),
        ).fetchone()
        if _scalar(direct, "granted") is not False:
            raise ValueError("world-news writer has direct table privilege")


class PostgresWorldNewsRetention:
    """worker capability가 가진 bounded 함수 하나로만 30일 보관 정책을 dry-run/apply한다."""

    def __init__(self, database_dsn: str) -> None:
        if not database_dsn.strip() or len(database_dsn) > 4_096:
            raise ValueError("world-news retention DSN is invalid")
        self._database_dsn = database_dsn

    def run(
        self, *, apply: bool = False, now: datetime | None = None, limit: int = 1000
    ) -> dict[str, int]:
        instant = now or datetime.now(UTC)
        with psycopg.connect(self._database_dsn, row_factory=dict_row) as connection:
            role = connection.execute("SELECT current_user").fetchone()
            allowed = connection.execute(
                "SELECT has_function_privilege(current_user,%s,'EXECUTE') AS allowed",
                (_RETENTION_SIGNATURE,),
            ).fetchone()
            if (
                role is None
                or role["current_user"] != _RETENTION_ROLE
                or not allowed
                or allowed["allowed"] is not True
            ):
                raise ValueError("world-news retention capability is unavailable")
            row = connection.execute(
                "SELECT * FROM public.p1_world_news_retention_v1(%s,%s,%s)",
                (apply, instant, limit),
            ).fetchone()
        if row is None:
            raise ValueError("world-news retention result is unavailable")
        return {
            "scanned": int(row["scanned_count"]),
            "eligible": int(row["eligible_count"]),
            "pinned": int(row["pinned_count"]),
            "deleted": int(row["deleted_count"]),
        }


def _time(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
