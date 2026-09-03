import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from return_engine import main


def test_train_production_reads_parquet_input_and_writes_seed_bundle(tmp_path: Path) -> None:
    input_root = tmp_path / "p1-return-engine-input-pack.v1"
    input_root.mkdir()

    manifest = {
        "contractId": "p1-return-engine-input-pack.v1",
        "coverage": [{"symbol": "005930", "firstSession": "2023-01-02", "lastSession": "2026-08-31"}],
        "costModel": {"roundTripCostBps": 35},
    }
    manifest_path = input_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    start_date = date(2025, 1, 1)
    df = pd.DataFrame(
        {
            "symbol": "005930",
            "date": [start_date + timedelta(days=index) for index in range(300)],
            "open": [100.0 + index for index in range(300)],
            "high": [110.0 + index for index in range(300)],
            "low": [90.0 + index for index in range(300)],
            "raw_close": [105.0 + index for index in range(300)],
            "volume": [1234 + index for index in range(300)],
        }
    )
    df.to_parquet(input_root / "daily_ohlcv.parquet", index=False)

    expected_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    output_root = tmp_path / "seed-output"

    exit_code = main(
        [
            "train-production",
            "--input-root",
            str(input_root),
            "--manifest-sha256",
            expected_sha,
            "--output-root",
            str(output_root),
            # 파서가 --stock-code 를 required 로 선언하는데 이 호출이 넘기지 않아 SystemExit(2)
            # 였다. 검증 내용은 그대로 두고 누락된 인자만 채운다.
            "--stock-code",
            "005930",
        ]
    )

    assert exit_code == 0
    assert (output_root / "model.safetensors").exists()
    assert (output_root / "p1-return-engine-manifest.v3.json").exists()
    assert (output_root / "config.json").exists()
