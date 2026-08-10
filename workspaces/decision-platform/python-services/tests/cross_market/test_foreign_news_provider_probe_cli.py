from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.cross_market import foreign_news_provider_probe_cli
from app.cross_market.foreign_news_evaluation_cli import ForeignNewsEvaluationCliError
from app.cross_market.foreign_news import ForeignNewsTransientLaneAggregate
from app.cross_market.foreign_news_provider_probe import ForeignNewsProviderProbeError
from app.cross_market.foreign_news_repository import ForeignNewsWriterAuthorityError


def test_execute_without_packet_fails_closed_before_model_or_provider_transport(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(foreign_news_provider_probe_cli, "_repository_root", lambda: tmp_path)

    assert foreign_news_provider_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_PROBE_PACKET_UNAVAILABLE",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_cli_rejects_nonleaf_packet_selector_without_loading_control_file(capsys) -> None:
    assert foreign_news_provider_probe_cli.main(("execute", "--packet", "../approval.json")) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_PROBE_ARGUMENT_INVALID",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_model_gate_blocks_before_execution_evidence_or_transport(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        foreign_news_provider_probe_cli.ForeignNewsProviderProbePacket,
        "load_from_control_root",
        lambda **_: object(),
    )

    def no_selected_model() -> object:
        raise ForeignNewsEvaluationCliError("FOREIGN_NEWS_RUNTIME_MODEL_NOT_VERIFIED")

    monkeypatch.setattr(
        foreign_news_provider_probe_cli,
        "load_verified_selected_local_candidate",
        no_selected_model,
    )

    assert foreign_news_provider_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_MODEL_NOT_VERIFIED",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_owner_scope_gate_blocks_before_writer_preflight_or_provider_transport(monkeypatch, capsys) -> None:
    packet = type("Packet", (), {"symbol": "005930.KS"})()
    monkeypatch.setattr(
        foreign_news_provider_probe_cli.ForeignNewsProviderProbePacket,
        "load_from_control_root",
        lambda **_: packet,
    )
    monkeypatch.setattr(
        foreign_news_provider_probe_cli,
        "load_verified_selected_local_candidate",
        lambda: object(),
    )

    def no_owner_scope(**_: object) -> object:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_UNAVAILABLE")

    monkeypatch.setattr(foreign_news_provider_probe_cli, "_load_owner_scope", no_owner_scope)

    assert foreign_news_provider_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_PROBE_OWNER_SCOPE_UNAVAILABLE",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_owner_scope_is_canonical_local_binding_and_rejects_other_packet_symbol(tmp_path: Path) -> None:
    control_root = tmp_path / "foreign-news-control"
    control_root.mkdir(mode=0o700)
    control_root.chmod(0o700)
    scope = control_root / "foreign-news-provider-owner-scope.v1.json"
    scope.write_bytes(
        json.dumps(
            {
                "contractId": "foreign-news-provider-owner-scope-v1",
                "ownerUserId": "usr_demo_user",
                "schemaVersion": 1,
                "symbol": "005930.KS",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    scope.chmod(0o600)
    packet = type("Packet", (), {"symbol": "005930.KS"})()

    loaded = foreign_news_provider_probe_cli._load_owner_scope(
        control_root=control_root,
        packet=packet,
    )

    assert loaded.owner_user_id == "usr_demo_user"
    assert loaded.symbol == "005930.KS"
    with pytest.raises(ForeignNewsProviderProbeError, match="OWNER_SCOPE_PACKET_MISMATCH"):
        foreign_news_provider_probe_cli._load_owner_scope(
            control_root=control_root,
            packet=type("Packet", (), {"symbol": "AAPL"})(),
        )


def test_writer_preflight_blocks_before_provider_executor(monkeypatch, capsys) -> None:
    packet = type("Packet", (), {"operation": "SEC_OFFICIAL_RELEASES", "symbol": "005930"})()
    monkeypatch.setattr(
        foreign_news_provider_probe_cli.ForeignNewsProviderProbePacket,
        "load_from_control_root",
        lambda **_: packet,
    )
    monkeypatch.setattr(foreign_news_provider_probe_cli, "load_verified_selected_local_candidate", lambda: object())
    monkeypatch.setattr(
        foreign_news_provider_probe_cli,
        "_load_owner_scope",
        lambda **_: foreign_news_provider_probe_cli._ForeignNewsOwnerScope(
            owner_user_id="usr_demo_user",
            symbol="005930",
        ),
    )
    monkeypatch.setenv("DECISION_MARKET_WRITER_DATABASE_DSN", "postgresql://fixture.invalid/decision")

    class _FailingRepository:
        def __init__(self, _dsn: str) -> None:
            pass

        def preflight(self) -> None:
            raise ForeignNewsWriterAuthorityError("writer unavailable")

    monkeypatch.setattr(foreign_news_provider_probe_cli, "PostgresForeignNewsSentimentRepository", _FailingRepository)

    assert foreign_news_provider_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_PROBE_WRITER_PREFLIGHT_FAILED",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_successful_probe_materializes_only_sanitized_owner_record(monkeypatch, capsys) -> None:
    packet = type("Packet", (), {"operation": "SEC_OFFICIAL_RELEASES", "symbol": "005930"})()
    monkeypatch.setattr(
        foreign_news_provider_probe_cli.ForeignNewsProviderProbePacket,
        "load_from_control_root",
        lambda **_: packet,
    )
    monkeypatch.setattr(foreign_news_provider_probe_cli, "load_verified_selected_local_candidate", lambda: object())
    monkeypatch.setattr(
        foreign_news_provider_probe_cli,
        "_load_owner_scope",
        lambda **_: foreign_news_provider_probe_cli._ForeignNewsOwnerScope(
            owner_user_id="usr_demo_user",
            symbol="005930",
        ),
    )
    monkeypatch.setattr(foreign_news_provider_probe_cli, "_load_execution_binding", lambda **_: object())
    monkeypatch.setenv("DECISION_MARKET_WRITER_DATABASE_DSN", "postgresql://fixture.invalid/decision")

    appended: list[object] = []

    class _Repository:
        def __init__(self, _dsn: str) -> None:
            pass

        def preflight(self) -> None:
            return None

        def append(self, record: object) -> str:
            appended.append(record)
            return "INSERTED"

    result = SimpleNamespace(
        aggregate=ForeignNewsTransientLaneAggregate(
            lane_id="SEC_OFFICIAL",
            state="AVAILABLE",
            content_hash="a" * 64,
            official_release_locator="SEC_OFFICIAL_RELEASES",
        ),
        receipt=SimpleNamespace(
            started_at=foreign_news_provider_probe_cli.datetime(2026, 8, 10, 2, 3, 4, tzinfo=foreign_news_provider_probe_cli.UTC),
            physical_call_count=1,
            outcome="SUCCESS",
            provider_family="SEC_OFFICIAL",
            provider_status_class="HTTP_2XX",
        ),
    )

    class _Executor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(self, **_: object) -> object:
            return result

    monkeypatch.setattr(foreign_news_provider_probe_cli, "PostgresForeignNewsSentimentRepository", _Repository)
    monkeypatch.setattr(foreign_news_provider_probe_cli, "ForeignNewsProviderProbeExecutor", _Executor)

    assert foreign_news_provider_probe_cli.main(("execute",)) == 0

    assert len(appended) == 1
    record = appended[0]
    assert getattr(record, "owner_user_id") == "usr_demo_user"
    assert getattr(record, "symbol") == "005930"
    assert getattr(record, "to_storage_payload")()["lanes"] == [
        {"laneId": "FINNHUB_PERSONAL_LOCAL", "state": "NOT_ACTIVATED"},
        {"laneId": "SEC_OFFICIAL", "state": "AVAILABLE"},
        {"laneId": "FED_OFFICIAL", "state": "NOT_ACTIVATED"},
        {"laneId": "GDELT_OFFLINE_REFERENCE", "state": "NOT_ACTIVATED"},
    ]
    assert json.loads(capsys.readouterr().out) == {
        "code": "FOREIGN_NEWS_PROBE_EXECUTED",
        "materializationDisposition": "INSERTED",
        "outcome": "SUCCESS",
        "providerFamily": "SEC_OFFICIAL",
        "providerPhysicalCalls": 1,
        "providerStatusClass": "HTTP_2XX",
        "state": "COMPLETE",
    }


def test_git_identity_hashes_the_binary_tree_object_without_text_decoding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_tree = b"100644 README.md\x00\xff\x00\x81\x7f"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        calls.append((command, kwargs))
        if command[-3:] == ["cat-file", "tree", "HEAD^{tree}"]:
            assert kwargs.get("text", False) is False
            return subprocess.CompletedProcess(command, 0, stdout=raw_tree, stderr=b"")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(foreign_news_provider_probe_cli.subprocess, "run", fake_run)

    head_sha, tree_sha256 = foreign_news_provider_probe_cli._current_clean_git_identity(tmp_path)

    assert head_sha == "a" * 40
    assert tree_sha256 == hashlib.sha256(raw_tree).hexdigest()
    assert len(calls) == 3
