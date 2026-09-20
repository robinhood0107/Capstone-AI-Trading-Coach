"""합성 history/fake inference와 실제 DB commit/context를 연결한다. 운영 성능 증거가 아니다."""

from datetime import date, datetime
from time import monotonic
from typing import Any, cast
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app.p1_owner.daily_inference import DailyInferenceService, DailySignalRepository
from tests.conftest import PostgresTestCluster
from tests.p1_owner.test_daily_inference import _Client, _Repository


def test_daily_publication_replay_and_incomplete_projection_use_actual_db(
    isolated_postgres_cluster: PostgresTestCluster,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = isolated_postgres_cluster
    source, target = date(2026, 8, 31), date(2026, 9, 1)
    # current-pointer 분기 재현용 합성 DB seed다. 실제 Team B 검증/활성화가 아니다.
    with psycopg.connect(cluster["admin_dsn"]) as db:
        db.execute(
            """INSERT INTO p1_return_artifact_bundle(bundle_sha256,artifact_id,run_id,
          input_pack_sha256,manifest_sha256,packet_sha256,evidence_mode,real_team_b,model_quality,
          mock_runtime_eligible,session_date,as_of,fresh_until,model_projection_sha256,
          backtest_projection_sha256,model_sha256)
          VALUES(repeat('a',64),'artifact_p1_'||repeat('a',24),'run_daily_synthetic_fixture',
          repeat('d',64),repeat('a',64),repeat('e',64),'REAL_TEAM_B',true,'PASS',true,%s,
          '2026-08-31T08:10:00+09','2026-09-01T08:10:00+09',repeat('f',64),repeat('f',64),repeat('c',64))""",
            (source,),
        )
        db.execute(
            """INSERT INTO market_data_manifests(manifest_sha256,manifest_kind,contract_id,
          session_date,as_of,generation,source_manifest_sha256,archive_sha256,calendar_revision,
          calendar_sha256,temporal_quality)
          VALUES(repeat('b',64),'SEED','market-data-seed.v1',%s,'2026-08-31T08:10:00+09',1,
          repeat('b',64),repeat('b',64),'fixture',repeat('b',64),'COLLECTION_ONLY')""",
            (source,),
        )
        symbols = _Repository().context(target)["symbols"]
        with db.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO p1_return_model_seed_signal(bundle_sha256,producer,
              symbol,session_date,as_of,signal,predicted_return,model_version,model_report_id,payload_sha256,fixture)
              VALUES(repeat('a',64),'LSTM',%s,%s,'2026-08-31T08:10:00+09','HOLD',0,
              'synthetic-fixture','mrp_daily_fixture',repeat('d',64),true)""",
                [(s, source) for s in symbols],
            )

    class Repository(DailySignalRepository):
        # 저장/재조회는 production repository이고 시장 history만 합성 입력이다.
        def history(self, symbol: str, target_session: date):
            return _Repository().history(symbol, target_session)

    repository = Repository(cluster["automation_runtime_dsn"])
    client = _Client()
    service = DailyInferenceService(repository, client)  # type: ignore[arg-type]
    from app.p1_owner import automation_runtime as runtime

    now = datetime(2026, 9, 1, 8, 49, tzinfo=ZoneInfo("Asia/Seoul"))
    boundaries: list[datetime] = []

    class Clock(datetime):
        @staticmethod
        def now(tz: Any) -> datetime:
            return now.astimezone(tz)

    class RuntimeRepository:
        def preflight(self) -> None:
            pass

        def claim(self, *args: Any) -> None:
            pytest.fail("장전 게시에는 주문 claim이 없어야 한다")

    runtime_service = runtime.AutomationRuntimeService(
        cast(Any, RuntimeRepository()),
        cast(Any, None),
        "fixture-preparation-secret-00000000",
        daily_inference=service,
    )

    def wait_until(boundary: datetime) -> bool:
        boundaries.append(boundary)
        return boundary > now

    monkeypatch.setattr(runtime, "datetime", Clock)
    monkeypatch.setattr(runtime_service, "_wait_until", wait_until)
    assert repository.context(target)["outcome"] == "MATERIALIZE"
    started = monotonic()
    runtime_service.serve()
    elapsed = monotonic() - started
    assert boundaries[-1] == now.replace(hour=9, minute=30)
    published = service.ensure_daily_signals(target)
    assert published.outcome == "REPLAYED"
    assert client.calls == 1
    assert repository.context(target)["currentContractComplete"] is True
    assert service.ensure_daily_signals(target).outcome == "REPLAYED"
    assert client.calls == 1
    with psycopg.connect(cluster["admin_dsn"]) as db:
        assert db.execute("SELECT count(*) FROM p1_return_daily_signal_projection").fetchone() == (
            62,
        )
        assert db.execute("SELECT count(*) FROM p1_ridge_daily_forecasts").fetchone() == (31,)
        # incomplete historical projection을 재현한다. COMPLETE 원본 행은 보존한다.
        db.execute("DELETE FROM p1_ridge_daily_forecasts WHERE symbol='132030'")
    assert repository.context(target)["currentContractComplete"] is False
    assert service.ensure_daily_signals(target).outcome == "LEGACY_INCOMPLETE"
    assert client.calls == 1
    with psycopg.connect(cluster["admin_dsn"]) as db:
        assert db.execute(
            "SELECT batch_sha256,status FROM p1_return_daily_signal_batch"
        ).fetchone() == (published.batch_sha256, "COMPLETE")
    print(f"SYNTHETIC_DAILY_DB_SECONDS={elapsed:.3f} PROVIDER_CALLS=0")
