from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from app.rag.register_sources_cli import (
    main as register_sources_main,
    register_source_registry,
)
from app.rag import register_sources_cli
from app.rag.source_registry import (
    RAG_SOURCE_OWNER,
    RagSourceRegistryError,
    load_default_source_registry,
    load_source_registry,
    validate_resolved_addresses,
)

EXPECTED_SOURCE_IDS = [
    "src_kis_openapi_overview_001",
    "src_kis_marketdata_daily_001",
    "src_kis_marketdata_price_001",
    "src_kis_trading_cash_order_001",
    "src_kis_account_balance_001",
    "src_kis_market_calendar_001",
    "src_kis_rate_limit_001",
    "src_opendart_disclosure_search_001",
    "src_opendart_corporation_code_001",
    "src_opendart_financial_statement_001",
    "src_opendart_major_report_001",
    "src_ecos_api_overview_001",
    "src_ecos_statistic_search_001",
    "src_krx_openapi_service_catalog_001",
    "src_krx_openapi_terms_001",
    "src_krx_etf_etn_structure_001",
    "src_krx_etn_risk_indicator_001",
    "src_samsungfund_gold_futures_etf_001",
    "src_naver_news_search_001",
    "src_naver_legacy_sunset_001",
]


def test_default_rag_source_seed_locks_twenty_reference_only_sources() -> None:
    registry = load_default_source_registry()

    assert registry.schema_version == "1"
    assert registry.registry_version == "s4-rag-p0-upstream-v1"
    assert len(registry.sources) == 20
    assert list(registry.sources) == EXPECTED_SOURCE_IDS
    for source in registry.sources.values():
        assert source.owner == RAG_SOURCE_OWNER
        assert source.retention.owner == RAG_SOURCE_OWNER
        assert source.retention.mode == "REFERENCE_METADATA_ONLY"
        assert source.initial_processing == "REFERENCE_ONLY"
        assert source.source_type == "UPSTREAM_REFERENCE"
        assert source.tier == "OFFICIAL"
        assert source.external_processing_allowed is False
        assert source.access_level == "PUBLIC"
        assert source.locator.canonical_url.startswith("https://")


def test_rag_source_seed_rejects_unsafe_url_shapes(tmp_path: Path) -> None:
    baseline = _load_seed_payload()
    unsafe_cases = {
        "http": "http://developers.naver.com/notice/article/32973",
        "userinfo": "https://user:pass@developers.naver.com/notice/article/32973",
        "fragment": "https://developers.naver.com/notice/article/32973#secret",
        "ip-literal": "https://127.0.0.1/notice/article/32973",
        "non-default-port": "https://developers.naver.com:444/notice/article/32973",
        "backslash": "https://developers.naver.com/notice\\article/32973",
        "encoded": "https://developers.naver.com/notice/%2e%2e/secret",
        "parent-dot-segment": "https://developers.naver.com/notice/../secret",
        "current-dot-segment": "https://developers.naver.com/notice/./secret",
        "double-slash": "https://developers.naver.com/notice//secret",
        "mixed-case-authority": "https://Developers.Naver.com/notice/article/32973",
        "decimal-ip": "https://2130706433/notice/article/32973",
        "octal-ip": "https://0177.0.0.1/notice/article/32973",
        "hex-ip": "https://0x7f.0.0.1/notice/article/32973",
    }
    for label, unsafe_url in unsafe_cases.items():
        payload = copy.deepcopy(baseline)
        payload["sources"][0]["locator"]["canonicalUrl"] = unsafe_url
        payload["sources"][0]["locator"]["allowedOrigin"] = "https://developers.naver.com"
        payload["sources"][0]["locator"]["allowedPath"] = "/notice/article/32973"
        path = tmp_path / f"{label}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with pytest.raises(RagSourceRegistryError):
            load_source_registry(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace(
            "schemaVersion: '1'\n",
            "schemaVersion: '1'\nschemaVersion: '1'\n",
            1,
        ),
        lambda text: text.replace("schemaVersion: '1'", "schemaVersion: &version '1'", 1),
        lambda text: text.replace("schemaVersion: '1'", "schemaVersion: *version", 1),
        lambda text: text.replace("schemaVersion: '1'", "schemaVersion: !unsafe '1'", 1),
        lambda text: text.replace("schemaVersion: '1'", "schemaVersion: {<<: '1'}", 1),
    ],
    ids=("duplicate", "anchor", "alias", "tag", "merge"),
)
def test_rag_source_seed_rejects_ambiguous_yaml(
    tmp_path: Path,
    mutate: Any,
) -> None:
    seed_path = load_default_source_registry().seed_path
    baseline = seed_path.read_text(encoding="utf-8")
    path = tmp_path / "ambiguous.yaml"
    path.write_text(mutate(baseline), encoding="utf-8")

    with pytest.raises(RagSourceRegistryError):
        load_source_registry(path)


@pytest.mark.parametrize(
    "suffix",
    ("\r\n", "\ufeff", ""),
    ids=("crlf", "bom", "missing-final-lf"),
)
def test_rag_source_seed_rejects_noncanonical_text(tmp_path: Path, suffix: str) -> None:
    seed_path = load_default_source_registry().seed_path
    baseline = seed_path.read_text(encoding="utf-8")
    if suffix == "\r\n":
        mutated = baseline.replace("\n", "\r\n")
    elif suffix == "\ufeff":
        mutated = suffix + baseline
    else:
        mutated = baseline.rstrip("\n")
    path = tmp_path / "noncanonical.yaml"
    path.write_text(mutated, encoding="utf-8", newline="")

    with pytest.raises(RagSourceRegistryError):
        load_source_registry(path)


def test_rag_source_seed_rejects_allowlist_or_identity_drift(tmp_path: Path) -> None:
    baseline = _load_seed_payload()
    cases = []

    wrong_path = copy.deepcopy(baseline)
    wrong_path["sources"][0]["locator"]["allowedPath"] = "/different"
    cases.append(wrong_path)

    wrong_id = copy.deepcopy(baseline)
    wrong_id["sources"][0]["sourceId"] = "src_kis_openapi_overview_002"
    cases.append(wrong_id)

    external_processing = copy.deepcopy(baseline)
    external_processing["sources"][0]["externalProcessingAllowed"] = True
    cases.append(external_processing)

    wrong_owner = copy.deepcopy(baseline)
    wrong_owner["sources"][0]["retention"]["owner"] = "spring-api"
    cases.append(wrong_owner)

    padded_locator = copy.deepcopy(baseline)
    padded_locator["sources"][0]["locator"]["canonicalUrl"] += " "
    cases.append(padded_locator)

    for index, payload in enumerate(cases):
        path = tmp_path / f"invalid-{index}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with pytest.raises(RagSourceRegistryError):
            load_source_registry(path)


def test_dns_resolution_guard_rejects_private_link_local_and_loopback() -> None:
    validate_resolved_addresses("example.com", ["8.8.8.8", "2001:4860:4860::8888"])
    for address in ("127.0.0.1", "10.1.2.3", "169.254.1.1", "::1", "fc00::1"):
        with pytest.raises(RagSourceRegistryError):
            validate_resolved_addresses("example.com", [address])


def test_register_sources_cli_accepts_manifest_only_and_emits_summary(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = register_sources_main(["--dry-run", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sourceCount"] == 20
    assert len(output["manifestSha256"]) == 64
    assert output["sourceIds"][0] == "src_kis_openapi_overview_001"
    assert output["sourceIds"][-1] == "src_naver_legacy_sunset_001"

    with pytest.raises(SystemExit):
        register_sources_main(["--seed", "/tmp/unapproved.yaml"])
    assert "unrecognized arguments: --seed" in capsys.readouterr().err


def test_register_sources_cli_receipt_uses_the_parsed_seed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_path = tmp_path / "seed.yaml"
    original = Path(register_sources_cli.load_default_source_registry().seed_path).read_bytes()
    seed_path.write_bytes(original)
    parsed = load_source_registry(seed_path)
    seed_path.write_bytes(original + b"\n# changed after parse\n")
    monkeypatch.setattr(register_sources_cli, "load_default_source_registry", lambda: parsed)

    exit_code = register_sources_main(["--dry-run", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["manifestSha256"] == hashlib.sha256(original).hexdigest()
    assert output["manifestSha256"] != hashlib.sha256(seed_path.read_bytes()).hexdigest()


def test_register_sources_cli_fails_closed_without_role_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    monkeypatch.delenv("RAG_SOURCE_WRITER_DATABASE_DSN", raising=False)

    exit_code = register_sources_main(["--register-db"])

    assert exit_code == 3
    assert "RAG_SOURCE_WRITER_DATABASE_DSN is required" in capsys.readouterr().out


def test_register_sources_rejects_target_role_and_privilege_attestation_drift(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = load_default_source_registry()
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "production")
    with pytest.raises(ValueError, match="must be one of"):
        register_source_registry(
            registry,
            database_dsn=postgres_cluster["rag_writer_dsn"],
        )

    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    with pytest.raises(ValueError, match="must use decision_rag_writer"):
        register_source_registry(
            registry,
            database_dsn=postgres_cluster["admin_dsn"],
        )

    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute("grant update on rag_sources to decision_rag_writer")
    try:
        with pytest.raises(ValueError, match="unexpected mutation privilege"):
            register_source_registry(
                registry,
                database_dsn=postgres_cluster["rag_writer_dsn"],
            )
    finally:
        with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
            connection.execute("revoke update on rag_sources from decision_rag_writer")

    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute("revoke insert on rag_source_checks from decision_rag_writer")
    try:
        with pytest.raises(ValueError, match="lacks required append privilege"):
            register_source_registry(
                registry,
                database_dsn=postgres_cluster["rag_writer_dsn"],
            )
    finally:
        with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
            connection.execute("grant insert on rag_source_checks to decision_rag_writer")

    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute("grant select on flyway_schema_history to decision_rag_writer")
    try:
        with pytest.raises(ValueError, match="forbidden table"):
            register_source_registry(
                registry,
                database_dsn=postgres_cluster["rag_writer_dsn"],
            )
    finally:
        with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
            connection.execute("revoke select on flyway_schema_history from decision_rag_writer")


@pytest.mark.parametrize(
    "privilege",
    ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"),
)
def test_register_sources_rejects_every_final_embedding_table_privilege(
    privilege: str,
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = load_default_source_registry()
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        source_count_before = _scalar(connection, "select count(*) from rag_sources")
        revision_count_before = _scalar(connection, "select count(*) from rag_source_revisions")

    assert privilege in {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"}
    with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
        connection.execute(
            f"grant {privilege} on rag_chunk_embeddings to decision_rag_writer"
        )
    try:
        with pytest.raises(ValueError, match="forbidden table"):
            register_source_registry(
                registry,
                database_dsn=postgres_cluster["rag_writer_dsn"],
            )
    finally:
        with psycopg.connect(postgres_cluster["admin_dsn"], autocommit=True) as connection:
            connection.execute(
                f"revoke {privilege} on rag_chunk_embeddings from decision_rag_writer"
            )

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        assert _scalar(connection, "select count(*) from rag_sources") == source_count_before
        assert (
            _scalar(connection, "select count(*) from rag_source_revisions")
            == revision_count_before
        )


def test_register_sources_cli_writes_seed_with_rag_writer_only(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_rag_seed_tables(postgres_cluster["admin_dsn"])
    _insert_project_card_stub(postgres_cluster["admin_dsn"])
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    monkeypatch.setenv("RAG_SOURCE_WRITER_DATABASE_DSN", postgres_cluster["rag_writer_dsn"])

    exit_code = register_sources_main(["--register-db", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sourceCount"] == 20
    assert output["insertedSources"] == 20
    assert output["insertedRevisions"] == 20
    assert output["retiredSources"] == 0
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        assert _scalar(connection, "select count(*) from rag_sources") == 21
        assert _scalar(connection, "select count(*) from rag_source_revisions") == 21
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from rag_sources").fetchone()
    with psycopg.connect(postgres_cluster["rag_writer_dsn"]) as connection:
        assert _scalar(connection, "select count(*) from rag_sources") == 21
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from rag_embedding_policy_state").fetchone()

    second_exit = register_sources_main(["--register-db", "--json"])

    assert second_exit == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["insertedSources"] == 0
    assert replay["insertedRevisions"] == 0
    assert replay["retiredSources"] == 0


def test_register_sources_rejects_same_id_drift_and_requires_new_sequence_id(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_rag_seed_tables(postgres_cluster["admin_dsn"])
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    baseline = load_default_source_registry()
    first = register_source_registry(
        baseline,
        database_dsn=postgres_cluster["rag_writer_dsn"],
    )
    assert first == {
        "insertedSources": 20,
        "insertedRevisions": 20,
        "retiredSources": 0,
    }

    conflicting_payload = _load_seed_payload()
    conflicting = conflicting_payload["sources"][0]
    conflicting["locator"]["canonicalUrl"] = (
        "https://apiportal.koreainvestment.com/about-open-api-conflict"
    )
    conflicting["locator"]["allowedPath"] = "/about-open-api-conflict"
    conflict_path = tmp_path / "conflict.yaml"
    conflict_path.write_text(
        yaml.safe_dump(conflicting_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    conflicting_registry = load_source_registry(conflict_path)
    with pytest.raises(RagSourceRegistryError, match="different canonical revision"):
        register_source_registry(
            conflicting_registry,
            database_dsn=postgres_cluster["rag_writer_dsn"],
        )

    moved_payload = _load_seed_payload()
    moved = moved_payload["sources"][0]
    moved["sourceId"] = "src_kis_openapi_overview_002"
    moved["sequence"] = 2
    moved["locator"]["canonicalUrl"] = "https://apiportal.koreainvestment.com/about-open-api-v2"
    moved["locator"]["allowedPath"] = "/about-open-api-v2"
    moved_path = tmp_path / "moved.yaml"
    moved_path.write_text(
        yaml.safe_dump(moved_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    moved_registry = load_source_registry(moved_path)
    moved_result = register_source_registry(
        moved_registry,
        database_dsn=postgres_cluster["rag_writer_dsn"],
    )
    assert moved_result == {
        "insertedSources": 1,
        "insertedRevisions": 1,
        "retiredSources": 1,
    }
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        retired_at = connection.execute(
            """
            select retired_at
            from rag_sources
            where source_id = 'src_kis_openapi_overview_001'
            """
        ).fetchone()
        replacement_retired_at = connection.execute(
            """
            select retired_at
            from rag_sources
            where source_id = 'src_kis_openapi_overview_002'
            """
        ).fetchone()
        assert retired_at is not None and retired_at[0] is not None
        assert replacement_retired_at == (None,)

    moved_replay = register_source_registry(
        moved_registry,
        database_dsn=postgres_cluster["rag_writer_dsn"],
    )
    assert moved_replay == {
        "insertedSources": 0,
        "insertedRevisions": 0,
        "retiredSources": 0,
    }
    with psycopg.connect(
        postgres_cluster["rag_writer_dsn"],
        autocommit=True,
    ) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                select public.retire_rag_source_for_relocation(
                  'src_kis_openapi_overview_001',
                  'src_kis_marketdata_daily_001'
                )
                """
            ).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                update rag_sources
                set retired_at = transaction_timestamp()
                where source_id = 'src_kis_marketdata_daily_001'
                """
            )

def test_rag_writer_appends_same_locator_revision_and_check_but_rejects_locator_move(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_rag_seed_tables(postgres_cluster["admin_dsn"])
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    register_source_registry(
        load_default_source_registry(),
        database_dsn=postgres_cluster["rag_writer_dsn"],
    )

    with psycopg.connect(postgres_cluster["rag_writer_dsn"], autocommit=True) as connection:
        connection.execute(
            """
            insert into rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              title, tier, access_level, license_decision, license_note, attribution,
              retention_mode, retention_days, retention_owner, external_processing_allowed,
              initial_processing, canonical_url, allowed_origin, allowed_path,
              locator_sha256, metadata_hash
            )
            select
              'src_rev_22222222222222222222222222222222', source_id, 2,
              's4-rag-p0-upstream-v1-check-2', title, tier, access_level,
              license_decision, license_note, attribution, retention_mode, retention_days,
              retention_owner, external_processing_allowed, initial_processing,
              canonical_url, allowed_origin, allowed_path, locator_sha256, repeat('2', 64)
            from rag_source_revisions
            where source_id = 'src_kis_openapi_overview_001' and revision_seq = 1
            """
        )
        connection.execute(
            """
            insert into rag_source_checks (
              source_check_id, source_id, source_revision_id, check_result,
              response_status, bytes_read, content_hash
            )
            values (
              'src_chk_33333333333333333333333333333333',
              'src_kis_openapi_overview_001',
              'src_rev_22222222222222222222222222222222',
              'CHANGED', 200, 2048, repeat('3', 64)
            )
            """
        )

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        assert (
            _scalar(
                connection,
                """
                select count(*) from rag_source_revisions
                where source_id = 'src_kis_openapi_overview_001'
                """,
            )
            == 2
        )
        assert (
            _scalar(
                connection,
                """
                select count(*) from rag_source_checks
                where source_id = 'src_kis_openapi_overview_001'
                  and check_result = 'CHANGED'
                """,
            )
            == 1
        )

    with psycopg.connect(postgres_cluster["rag_writer_dsn"], autocommit=True) as connection:
        with pytest.raises(
            psycopg.errors.CheckViolation,
            match="new source sequence ID",
        ):
            connection.execute(
                """
                insert into rag_source_revisions (
                  source_revision_id, source_id, revision_seq, registry_version,
                  title, tier, access_level, license_decision, license_note, attribution,
                  retention_mode, retention_days, retention_owner, external_processing_allowed,
                  initial_processing, canonical_url, allowed_origin, allowed_path,
                  locator_sha256, metadata_hash
                )
                select
                  'src_rev_44444444444444444444444444444444', source_id, 3,
                  's4-rag-p0-upstream-v1-moved', title, tier, access_level,
                  license_decision, license_note, attribution, retention_mode, retention_days,
                  retention_owner, external_processing_allowed, initial_processing,
                  'https://apiportal.koreainvestment.com/moved',
                  allowed_origin, '/moved', repeat('4', 64), repeat('4', 64)
                from rag_source_revisions
                where source_id = 'src_kis_openapi_overview_001' and revision_seq = 1
                """
            )


def _load_seed_payload() -> dict[str, Any]:
    seed_path = load_default_source_registry().seed_path
    payload = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _clear_rag_seed_tables(database_dsn: str) -> None:
    with psycopg.connect(database_dsn, autocommit=True) as connection:
        connection.execute("delete from rag_source_checks")
        connection.execute("delete from rag_source_revisions")
        connection.execute("delete from rag_sources")


def _insert_project_card_stub(database_dsn: str) -> None:
    with psycopg.connect(database_dsn, autocommit=True) as connection:
        connection.execute(
            """
            insert into rag_sources (
              source_id, source_type, institution, topic, owner_identity
            )
            values (
              'src_project_synthetic_card_001', 'PROJECT_SOURCE_CARD',
              'project', 'synthetic_card', 'python-rag-corpus-privacy'
            )
            """
        )
        connection.execute(
            """
            insert into rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              title, tier, access_level, license_decision, license_note, attribution,
              retention_mode, retention_days, retention_owner, external_processing_allowed,
              initial_processing, canonical_url, allowed_origin, allowed_path,
              locator_sha256, metadata_hash
            )
            values (
              'src_rev_ffffffffffffffffffffffffffffffff',
              'src_project_synthetic_card_001', 1, 'synthetic-project-card-v1',
              'Synthetic project card', 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC',
              'synthetic fixture', 'synthetic fixture', 'PROJECT_CARD', 365,
              'python-rag-corpus-privacy', false, 'PROJECT_AUTHORED_CARD',
              'https://example.com/card', 'https://example.com', '/card',
              repeat('f', 64), repeat('e', 64)
            )
            """
        )


def _scalar(
    connection: psycopg.Connection[Any],
    sql: str,
) -> int:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return int(row[0])
