"""Team B 지연과 실제 dataset 부재를 위한 deterministic FAKE_CONTRACT bundle."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from io import BytesIO

import lightgbm as lgb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes

SIGNAL_ROW_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("producer", pa.string(), nullable=False),
        pa.field("sourceWorkspace", pa.string(), nullable=False),
        pa.field("sessionDate", pa.date32(), nullable=False),
        pa.field("asOf", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=True),
        pa.field("signal", pa.string(), nullable=True),
        pa.field("confidence", pa.float32(), nullable=True),
        pa.field("predictedReturn", pa.float32(), nullable=True),
        pa.field("featureSummary", pa.list_(pa.string()), nullable=False),
        pa.field("evaluationId", pa.string(), nullable=False),
        pa.field("modelVersion", pa.string(), nullable=False),
        pa.field("modelReportId", pa.string(), nullable=False),
        pa.field("fixture", pa.bool_(), nullable=False),
        pa.field("provenanceClass", pa.string(), nullable=False),
        pa.field("payloadSha256", pa.string(), nullable=False),
        pa.field("provenanceSha256", pa.string(), nullable=False),
    ]
)


def generate_fake_contract_bundle() -> dict[str, bytes]:
    """외부 호출 없이 rule/LSTM/LightGBM/HMM all-ABSTAIN rows와 valid text model을 만든다.

    이 출력은 schema/I/O 테스트 전용이며 성능 증거나 production pointer 후보가 아니다.
    """

    rows: list[dict[str, object]] = []
    mappings = sorted(
        (
            ("RULE_BASELINE", "return-engine"),
            ("LSTM", "return-engine"),
            ("LIGHTGBM", "decision-platform"),
            ("HMM", "decision-platform"),
        )
    )
    for producer, workspace in mappings:
        payload = {
            "producer": producer,
            "sourceWorkspace": workspace,
            "symbol": "005930",
            "sessionDate": date(2026, 8, 14),
            "timeframe": "1d",
            "status": "ABSTAIN",
            "reason": "MISSING_EVIDENCE",
            "evaluationId": f"fake-{producer.lower().replace('_', '-')}-005930-20260814",
        }
        row: dict[str, object] = {
            **payload,
            "asOf": None,
            "signal": None,
            "confidence": None,
            "predictedReturn": None,
            "featureSummary": [],
            "modelVersion": "fake-contract-v1",
            "modelReportId": "mrp-fake-contract-v1",
            "fixture": True,
            "provenanceClass": "FAKE_CONTRACT",
        }
        row["payloadSha256"] = signal_row_payload_sha256(row)
        row["provenanceSha256"] = signal_row_provenance_sha256(row)
        rows.append(row)
    table = pa.Table.from_pylist(rows, schema=SIGNAL_ROW_SCHEMA)
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        table,
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        row_group_size=65_536,
        write_statistics=True,
        write_page_checksum=True,
    )
    signals = sink.getvalue()
    model = _fake_model_text()
    calibrator = canonical_json_bytes(
        {
            "calibratorVersion": "ovr-platt-v1",
            "classOrder": ["SELL", "HOLD", "BUY"],
            "fixture": True,
            "parameters": [{"a": 0.0, "b": 0.0, "classIndex": index} for index in range(3)],
        }
    )
    report = canonical_json_bytes(
        {
            "fixture": True,
            "performanceEvidence": False,
            "provenanceClass": "FAKE_CONTRACT",
            "reportVersion": "s5-fake-contract-report-v1",
            "rowCount": len(rows),
            "status": "DATASET_UNAVAILABLE",
        }
    )
    lightgbm_row = next(row for row in rows if row["producer"] == "LIGHTGBM")
    lightgbm_signal = canonical_json_bytes(
        {
            "artifactVersion": "lightgbm-signal-artifact-v1",
            "schemaVersion": "signal-v2-runtime-v1",
            "producer": "LIGHTGBM",
            "sourceWorkspace": "decision-platform",
            "symbol": "005930",
            "sessionDate": "2026-08-14",
            "evaluationId": "fake-lightgbm-005930-20260814",
            "timeframe": "1d",
            "modelVersion": f"lgbm-v1-{hashlib.sha256(model).hexdigest()[:12]}",
            "modelReportId": "mrp-fake-contract-v1",
            "fixture": True,
            "provenanceClass": "FAKE_CONTRACT",
            "datasetSha256": hashlib.sha256(b"s5-fake-contract-dataset-unavailable-v1").hexdigest(),
            "modelSha256": hashlib.sha256(model).hexdigest(),
            "reportSha256": hashlib.sha256(report).hexdigest(),
            "payloadSha256": lightgbm_row["payloadSha256"],
            "provenanceSha256": lightgbm_row["provenanceSha256"],
            "status": "ABSTAIN",
            "reason": "MISSING_EVIDENCE",
        }
    )
    files = {
        "signals.parquet": signals,
        "model.txt": model,
        "calibrator.json": calibrator,
        "report.json": report,
        "lightgbm-signal.json": lightgbm_signal,
    }
    manifest = canonical_json_bytes(
        {
            "manifestVersion": "s5-signal-fake-bundle-v1",
            "schemaVersion": "signal-v2-runtime-v1",
            "fixture": True,
            "provenanceClass": "FAKE_CONTRACT",
            "files": [
                {
                    "name": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
                for name, content in sorted(files.items())
            ],
        }
    )
    return {"manifest.json": manifest, **files}


def signal_row_payload_sha256(row: dict[str, object]) -> str:
    """DB exact DML에 전달할 canonical row payload digest를 계산한다."""

    return hashlib.sha256(signal_row_payload_bytes(row)).hexdigest()


def signal_row_payload_bytes(row: dict[str, object]) -> bytes:
    """DB가 다시 hash할 exact canonical UTF-8 payload text를 만든다."""

    payload = {
        "contractVersion": "signal-v2-runtime-v1",
        **{
            field: row.get(field)
            for field in (
                "producer",
                "sourceWorkspace",
                "symbol",
                "sessionDate",
                "timeframe",
                "status",
                "reason",
                "signal",
                "confidence",
                "predictedReturn",
                "evaluationId",
                "modelVersion",
                "modelReportId",
                "fixture",
                "provenanceClass",
            )
        },
    }
    return canonical_json_bytes(_normalize_temporal(payload))


def signal_row_provenance_sha256(row: dict[str, object]) -> str:
    """fake/production provenance class와 producer identity를 한 digest로 묶는다."""
    payload = {
        field: row[field]
        for field in (
            "producer",
            "sourceWorkspace",
            "evaluationId",
            "modelVersion",
            "modelReportId",
            "fixture",
            "provenanceClass",
        )
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _normalize_temporal(value: object) -> object:
    """Arrow가 복원한 date/datetime을 생성 시점과 같은 ISO-8601 digest preimage로 만든다."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize_temporal(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize_temporal(child) for child in value]
    return value


def _fake_model_text() -> bytes:
    random = np.random.default_rng(20260729)
    features = random.normal(size=(90, 3)).astype(np.float32)
    labels = np.tile(np.asarray([0, 1, 2], dtype=np.int64), 30)
    dataset = lgb.Dataset(features, label=labels, free_raw_data=False)
    booster = lgb.train(
        {
            "objective": "multiclass",
            "num_class": 3,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_data_in_leaf": 10,
            "deterministic": True,
            "force_col_wise": True,
            "seed": 20260729,
            "num_threads": 1,
            "verbosity": -1,
        },
        dataset,
        num_boost_round=3,
    )
    return booster.model_to_string().encode("utf-8")
