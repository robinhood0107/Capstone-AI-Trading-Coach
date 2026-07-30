from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from app.rag.register_sources_cli import main as register_sources_main
from app.rag.source_registry import (
    RAG_SOURCE_OWNER,
    RagSourceRegistryError,
    load_default_source_registry,
    load_source_registry,
    validate_resolved_addresses,
)


def test_default_rag_source_seed_locks_twenty_reference_only_sources() -> None:
    registry = load_default_source_registry()

    assert registry.schema_version == "1"
    assert registry.registry_version == "s4-rag-p0-upstream-v1"
    assert len(registry.sources) == 20
    assert list(registry.sources)[:3] == [
        "src_kis_openapi_overview_001",
        "src_kis_marketdata_daily_001",
        "src_kis_marketdata_price_001",
    ]
    assert list(registry.sources)[-1] == "src_naver_legacy_sunset_001"
    for source in registry.sources.values():
        assert source.owner == RAG_SOURCE_OWNER
        assert source.retention.owner == RAG_SOURCE_OWNER
        assert source.retention.mode == "REFERENCE_METADATA_ONLY"
        assert source.initial_processing == "REFERENCE_ONLY"
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
    exit_code = register_sources_main(["--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sourceCount"] == 20
    assert output["sourceIds"][0] == "src_kis_openapi_overview_001"
    assert output["sourceIds"][-1] == "src_naver_legacy_sunset_001"


def test_register_sources_cli_fails_closed_without_role_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    monkeypatch.delenv("RAG_SOURCE_WRITER_DATABASE_DSN", raising=False)

    exit_code = register_sources_main(["--register-db"])

    assert exit_code == 3
    assert "RAG_SOURCE_WRITER_DATABASE_DSN is required" in capsys.readouterr().out


def test_register_sources_cli_writes_seed_with_rag_writer_only(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_rag_seed_tables(postgres_cluster["admin_dsn"])
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    monkeypatch.setenv("RAG_SOURCE_WRITER_DATABASE_DSN", postgres_cluster["rag_writer_dsn"])

    exit_code = register_sources_main(["--register-db", "--json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sourceCount"] == 20
    assert output["insertedSources"] == 20
    assert output["insertedRevisions"] == 20
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        assert _scalar(connection, "select count(*) from rag_sources") == 20
        assert _scalar(connection, "select count(*) from rag_source_revisions") == 20
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from rag_sources").fetchone()
    with psycopg.connect(postgres_cluster["rag_writer_dsn"]) as connection:
        assert _scalar(connection, "select count(*) from rag_sources") == 20
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from rag_chunks").fetchone()

    second_exit = register_sources_main(["--register-db", "--json"])

    assert second_exit == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["insertedSources"] == 0
    assert replay["insertedRevisions"] == 0


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


def _scalar(
    connection: psycopg.Connection[Any],
    sql: str,
) -> int:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return int(row[0])
