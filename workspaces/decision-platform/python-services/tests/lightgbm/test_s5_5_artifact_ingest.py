from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import app.lightgbm.artifact_ingest as ingest_module
from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.artifact_ingest import validate_signal_bundle
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.fake_artifacts import SIGNAL_ROW_SCHEMA, generate_fake_contract_bundle
from app.lightgbm.fake_artifacts import (
    signal_row_payload_sha256,
    signal_row_provenance_sha256,
)
from app.lightgbm.signal_ingest_repository import validate_and_ingest_signal_bundle


def _write_bundle(root: Path, bundle: dict[str, bytes]) -> None:
    for name, content in bundle.items():
        path = root / name
        if path.exists() or path.is_symlink():
            path.unlink()
        path.write_bytes(content)


def _rebind_manifest(root: Path, name: str) -> None:
    manifest = json.loads((root / "manifest.json").read_bytes())
    content = (root / name).read_bytes()
    for entry in manifest["files"]:
        if entry["name"] == name:
            entry["sha256"] = hashlib.sha256(content).hexdigest()
            entry["size"] = len(content)
    (root / "manifest.json").write_bytes(canonical_json_bytes(manifest))


def _rewrite_signal_rows(root: Path, rows: list[dict[str, object]]) -> None:
    for row in rows:
        row["payloadSha256"] = signal_row_payload_sha256(row)
        row["provenanceSha256"] = signal_row_provenance_sha256(row)
    pq.write_table(
        pa.Table.from_pylist(rows, schema=SIGNAL_ROW_SCHEMA),
        root / "signals.parquet",
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        row_group_size=65_536,
        write_statistics=True,
        write_page_checksum=True,
    )
    _rebind_manifest(root, "signals.parquet")


def test_fake_generator_is_deterministic_and_never_claims_production(tmp_path: Path) -> None:
    first = generate_fake_contract_bundle()
    second = generate_fake_contract_bundle()
    assert first == second
    _write_bundle(tmp_path, first)
    validated = validate_signal_bundle(approved_root=tmp_path)
    assert validated.fixture is True
    assert validated.provenance_class == "FAKE_CONTRACT"
    assert len(validated.rows) == 4
    assert all(row["status"] == "ABSTAIN" for row in validated.rows)
    assert all(row["asOf"] is None and row["signal"] is None for row in validated.rows)


def test_manifest_duplicate_key_hash_mismatch_and_oversize_fail_closed(tmp_path: Path) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    (tmp_path / "manifest.json").write_bytes(b'{"manifestVersion":"x","manifestVersion":"y"}')
    with pytest.raises(LightGbmContractError, match="JSON"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    (tmp_path / "model.txt").write_bytes(bundle["model.txt"] + b"drift")
    with pytest.raises(LightGbmContractError, match="hash or size"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    (tmp_path / "manifest.json").write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(LightGbmContractError, match="path, symlink, or size"):
        validate_signal_bundle(approved_root=tmp_path)


def test_symlink_and_unknown_parquet_column_fail_before_rows_are_returned(tmp_path: Path) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    model = tmp_path / "model.txt"
    target = tmp_path / "target.txt"
    target.write_bytes(model.read_bytes())
    model.unlink()
    model.symlink_to(target)
    with pytest.raises(LightGbmContractError, match="path, symlink"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    table = pq.read_table(tmp_path / "signals.parquet")
    table = table.append_column(
        "cross_market_score", pa.array([0.0] * table.num_rows, type=pa.float32())
    )
    pq.write_table(table, tmp_path / "signals.parquet")
    _rebind_manifest(tmp_path, "signals.parquet")
    with pytest.raises(LightGbmContractError, match="schema, type, or unknown"):
        validate_signal_bundle(approved_root=tmp_path)


def test_approved_root_symlink_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    _write_bundle(real_root, generate_fake_contract_bundle())
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(LightGbmContractError, match="path, symlink"):
        validate_signal_bundle(approved_root=alias)


def test_calibrator_report_and_lightgbm_bindings_are_closed(tmp_path: Path) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    calibrator = json.loads((tmp_path / "calibrator.json").read_bytes())
    calibrator["unknown"] = True
    (tmp_path / "calibrator.json").write_bytes(canonical_json_bytes(calibrator))
    _rebind_manifest(tmp_path, "calibrator.json")
    with pytest.raises(LightGbmContractError, match="unknown field"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    report = json.loads((tmp_path / "report.json").read_bytes())
    report["performanceEvidence"] = True
    (tmp_path / "report.json").write_bytes(canonical_json_bytes(report))
    _rebind_manifest(tmp_path, "report.json")
    with pytest.raises(LightGbmContractError, match="performance evidence"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    signal = json.loads((tmp_path / "lightgbm-signal.json").read_bytes())
    signal["symbol"] = "000660"
    (tmp_path / "lightgbm-signal.json").write_bytes(canonical_json_bytes(signal))
    _rebind_manifest(tmp_path, "lightgbm-signal.json")
    with pytest.raises(LightGbmContractError, match="binding"):
        validate_signal_bundle(approved_root=tmp_path)


def test_actual_row_bound_is_rechecked_during_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    monkeypatch.setattr(ingest_module, "MAX_ROWS", 3)
    with pytest.raises(LightGbmContractError, match="row or column cap"):
        validate_signal_bundle(approved_root=tmp_path)


def test_actual_decoded_byte_bound_is_rechecked_during_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    original_parquet_file = ingest_module.pq.ParquetFile

    class _ColumnMetadata:
        total_uncompressed_size = 0

    class _RowGroupMetadata:
        @staticmethod
        def column(_column: int) -> _ColumnMetadata:
            return _ColumnMetadata()

    class _Metadata:
        def __init__(self, source: object) -> None:
            self.num_rows = source.num_rows
            self.num_columns = source.num_columns
            self.num_row_groups = source.num_row_groups

        @staticmethod
        def row_group(_group: int) -> _RowGroupMetadata:
            return _RowGroupMetadata()

    class _ParquetFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._delegate = original_parquet_file(*args, **kwargs)
            self.metadata = _Metadata(self._delegate.metadata)
            self.schema_arrow = self._delegate.schema_arrow

        def iter_batches(self, *args: object, **kwargs: object) -> object:
            return self._delegate.iter_batches(*args, **kwargs)

    monkeypatch.setattr(ingest_module.pq, "ParquetFile", _ParquetFile)
    monkeypatch.setattr(ingest_module, "MAX_DECODED_BYTES", 1)
    with pytest.raises(LightGbmContractError, match="actual decoded"):
        validate_signal_bundle(approved_root=tmp_path)


def test_row_digest_duplicate_identity_and_sort_are_rejected(tmp_path: Path) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    table = pq.read_table(tmp_path / "signals.parquet")
    rows = table.to_pylist()
    rows[0]["payloadSha256"] = "0" * 64
    pq.write_table(
        pa.Table.from_pylist(rows, schema=SIGNAL_ROW_SCHEMA), tmp_path / "signals.parquet"
    )
    _rebind_manifest(tmp_path, "signals.parquet")
    with pytest.raises(LightGbmContractError, match="row digest"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    rows = pq.read_table(tmp_path / "signals.parquet").to_pylist()
    rows[1]["producer"] = rows[0]["producer"]
    rows[1]["sourceWorkspace"] = rows[0]["sourceWorkspace"]
    rows[1]["evaluationId"] = rows[0]["evaluationId"]
    _rewrite_signal_rows(tmp_path, rows)
    with pytest.raises(LightGbmContractError, match="identity is duplicated"):
        validate_signal_bundle(approved_root=tmp_path)

    _write_bundle(tmp_path, bundle)
    rows = list(reversed(pq.read_table(tmp_path / "signals.parquet").to_pylist()))
    _rewrite_signal_rows(tmp_path, rows)
    with pytest.raises(LightGbmContractError, match="canonically sorted"):
        validate_signal_bundle(approved_root=tmp_path)


def test_nested_type_other_than_bounded_feature_summary_is_rejected(tmp_path: Path) -> None:
    bundle = generate_fake_contract_bundle()
    _write_bundle(tmp_path, bundle)
    table = pq.read_table(tmp_path / "signals.parquet")
    fields = list(SIGNAL_ROW_SCHEMA)
    fields[-1] = pa.field("provenanceSha256", pa.list_(pa.string()), nullable=False)
    bad_schema = pa.schema(fields)
    rows = table.to_pylist()
    for row in rows:
        row["provenanceSha256"] = [row["provenanceSha256"]]
    pq.write_table(pa.Table.from_pylist(rows, schema=bad_schema), tmp_path / "signals.parquet")
    _rebind_manifest(tmp_path, "signals.parquet")
    with pytest.raises(LightGbmContractError, match="schema, type"):
        validate_signal_bundle(approved_root=tmp_path)


class _FakeCursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class _FakeConnection:
    def __init__(self, *, fail_at_ingest: int | None = None) -> None:
        self.fail_at_ingest = fail_at_ingest
        self.ingest_parameters: list[tuple[object, ...]] = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        self.committed = error_type is None
        self.rolled_back = error_type is not None

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | None = None,
    ) -> _FakeCursor:
        if "SELECT session_user, current_user" in sql:
            return _FakeCursor(("decision_app", "decision_app"))
        if "ingest_signal_v2_exact" in sql:
            assert parameters is not None
            self.ingest_parameters.append(parameters)
            if self.fail_at_ingest == len(self.ingest_parameters):
                raise psycopg.DatabaseError("typed conflict")
            return _FakeCursor(("INSERTED", f"sigv2_{len(self.ingest_parameters):024d}"))
        return _FakeCursor(None)


def test_validated_bundle_is_ingested_in_one_exact_transaction(tmp_path: Path) -> None:
    _write_bundle(tmp_path, generate_fake_contract_bundle())
    connection = _FakeConnection()

    def factory(*args: object, **kwargs: object) -> Any:
        assert args == ("postgresql://fixture",)
        assert kwargs == {"autocommit": False, "connect_timeout": 2}
        return connection

    outcomes = validate_and_ingest_signal_bundle(
        approved_root=tmp_path,
        database_dsn="postgresql://fixture",
        connection_factory=factory,
    )

    assert len(outcomes) == 4
    assert connection.committed and not connection.rolled_back
    assert len(connection.ingest_parameters) == 4
    for parameters in connection.ingest_parameters:
        payload_text = parameters[20]
        assert isinstance(payload_text, str)
        assert hashlib.sha256(payload_text.encode()).hexdigest() == parameters[16]
        assert parameters[18:20] == (True, "FAKE_CONTRACT")


def test_exact_ingest_conflict_rolls_back_the_whole_validated_bundle(tmp_path: Path) -> None:
    _write_bundle(tmp_path, generate_fake_contract_bundle())
    connection = _FakeConnection(fail_at_ingest=3)

    with pytest.raises(LightGbmContractError, match="transaction failed"):
        validate_and_ingest_signal_bundle(
            approved_root=tmp_path,
            database_dsn="postgresql://fixture",
            connection_factory=lambda *args, **kwargs: connection,
        )

    assert connection.rolled_back and not connection.committed
    assert len(connection.ingest_parameters) == 3
