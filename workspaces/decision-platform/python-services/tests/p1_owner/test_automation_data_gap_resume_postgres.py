"""데이터 공백 fallback 의 거부 경로를 실 PostgreSQL 로 확인한다.

무엇을 지키려는 테스트인가. 이 fallback 은 `SKIPPED_DATA_UNAVAILABLE` 로 닫힌 세션을
되살린다. 그 권한이 넓으면 fallback 이 상한 우회 통로가 된다 - 이미 주문을 낸 세션을
되살리거나, 예산을 새로 얻거나, 다른 역할이 부를 수 있게 되면 그렇다.

**무엇이 결정론적으로 검증되는가.** 두 함수가 `statement_timestamp() AT TIME ZONE
'Asia/Seoul'` 을 쓰고 `09:30~15:20` 창과 `session_date=오늘` 을 요구하는데 **주입 가능한
clock 이 없다.** 그래서 성공 경로는 벽시계가 KST 장중일 때만 초록이다. 그 사실을 `skipif`
로 감추지 않는다 - 감추면 "왜 이 테스트가 아무것도 확인하지 않는가"를 아무도 모른다.
여기서 확인하는 것은 **거부 경로**이고, 그것은 시각과 무관하게 결정론적이다.

거부 케이스는 SQLSTATE 를 고정한다. 지난 세션에 RLS 때문에 거부 케이스가 잘못된 이유로
초록불이 된 일이 있었다 - 권한 오류(42501)와 상태 충돌(40001)은 다른 사실이다.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import PostgresTestCluster

_OWNER = "usr_data_gap_resume_0001"


def _call_resume(dsn: str, *, user_id: str, control_version: int) -> Any:
    with psycopg.connect(dsn, autocommit=True) as connection:
        return connection.execute(
            "select p1_resume_automation_data_gap_v1(%s,%s)", (user_id, control_version)
        ).fetchone()


def _call_retry_at(dsn: str, *, user_id: str, session: str) -> Any:
    with psycopg.connect(dsn, autocommit=True) as connection:
        return connection.execute(
            "select p1_automation_data_gap_retry_at_v1(%s,%s)", (user_id, session)
        ).fetchone()


def test_a_non_runtime_role_cannot_resume_a_data_gap(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    """상주 런타임 role 이 아니면 42501 이다.

    `SECURITY DEFINER` 함수의 첫 줄이 `session_user` 를 본다. 이 경계가 무너지면 앱 role 이
    닫힌 세션을 되살릴 수 있다.
    """

    for key in ("app_dsn", "worker_dsn", "collector_dsn"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            _call_resume(isolated_postgres_cluster[key], user_id=_OWNER, control_version=1)
        assert error.value.sqlstate == "42501"


def test_a_malformed_owner_identifier_is_refused_before_any_row_is_read(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    """소유자 식별자 형태가 어긋나면 42501 이다.

    같은 42501 로 닫는 이유는 이것이 범위 판정이기 때문이다 - 형태가 아닌 값은 이 함수의
    대상이 아니다. 42501 과 40001 을 섞으면 "권한이 없다"와 "상태가 어긋난다"가 구별되지
    않는다.
    """

    dsn = isolated_postgres_cluster["automation_runtime_dsn"]
    for identifier in ("", "usr_short", "not-a-user", "usr_" + "x" * 200):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            _call_resume(dsn, user_id=identifier, control_version=1)
        assert error.value.sqlstate == "42501"


def test_a_null_owner_identifier_is_refused(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
        _call_resume(
            isolated_postgres_cluster["automation_runtime_dsn"],
            user_id=None,  # type: ignore[arg-type]
            control_version=1,
        )
    assert error.value.sqlstate == "42501"


def test_resuming_without_an_armed_control_row_is_a_serialization_conflict(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    """무장한 control 행이 없으면 40001 이다.

    42501 이 아니라 40001 인 것이 중요하다 - 범위는 맞고 상태가 아니라는 뜻이고, 상주
    런타임이 그 둘을 다르게 처리한다(권한 오류는 표식만 남기고, 충돌은 재시도 대상이다).

    이 테스트는 KST 장중에만 여기까지 내려온다. 창 밖이면 첫 관문이 42501 로 닫는다. 두
    경우를 모두 허용하되 **다른 어떤 오류도 허용하지 않는다** - 그래야 창 밖에서도 이
    테스트가 무언가를 지킨다.
    """

    dsn = isolated_postgres_cluster["automation_runtime_dsn"]

    with pytest.raises(psycopg.Error) as error:
        _call_resume(dsn, user_id=_OWNER, control_version=1)

    assert error.value.sqlstate in {"40001", "42501"}


def test_the_retry_boundary_is_readable_by_the_runtime_and_null_without_a_run(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    """재시도 시각 함수는 run 이 없으면 NULL 이다.

    NULL 이 곧 "fallback 하지 않는다"이고, 상주 런타임이 그 값으로 다음 개장까지 잔다.
    예외를 던지면 그 분기가 죽는다.
    """

    row = _call_retry_at(
        isolated_postgres_cluster["automation_runtime_dsn"],
        user_id=_OWNER,
        session="2026-09-07",
    )

    assert row is not None
    assert row[0] is None


def test_a_non_runtime_role_cannot_read_the_retry_boundary(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    """경계 함수도 상주 런타임 role 에만 EXECUTE 가 있다.

    V142 가 `GRANT EXECUTE ... TO decision_automation_runtime` 만 준다. 다른 role 이 읽으면
    fallback 타이밍이 밖으로 새고, 무엇보다 권한 경계가 함수 단위라는 전제가 깨진다.
    """

    for key in ("app_dsn", "worker_dsn"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            _call_retry_at(isolated_postgres_cluster[key], user_id=_OWNER, session="2026-09-07")
        assert error.value.sqlstate == "42501"
