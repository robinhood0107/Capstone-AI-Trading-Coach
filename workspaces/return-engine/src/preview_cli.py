from __future__ import annotations

import argparse
import os
from pathlib import Path

from preview_contract import (
    RECEIVED_CSV_SHA256,
    RECEIVED_PTH_SHA256,
    load_and_verify_preview,
    mark_preview,
    sha256_file,
    verify_received_file,
)


def prepare(args: argparse.Namespace) -> None:
    """수신 모델을 실행한 뒤 검증된 preview만 최종 경로에 원자 게시한다."""

    # Verification/server paths must not import the model stack or need a writable cache.
    from return_engine import ReturnEngine

    csv_path = Path(args.csv)
    pth_path = Path(args.pth)
    output_path = Path(args.output)
    pending_path = output_path.with_suffix(output_path.suffix + ".pending")
    pth_hash = verify_received_file(pth_path, args.expected_pth_sha256, "received PTH")
    csv_hash = verify_received_file(csv_path, args.expected_csv_sha256, "received CSV")
    engine = ReturnEngine(
        stock_name=args.stock_name,
        stock_code=args.stock_code,
        stock_path=csv_path,
        model_path=pth_path,
        artifact_path=pending_path,
    )
    try:
        engine.run(refresh=False)
        mark_preview(pending_path, pth_hash, csv_hash)
        load_and_verify_preview(pending_path)
        os.replace(pending_path, output_path)
    finally:
        pending_path.unlink(missing_ok=True)
    print("TEAM_B_RECEIVED_PREVIEW=PASS")
    print("TEAM_B_RECEIVED_PREVIEW_VERIFY=PASS")
    print("TEAM_B_REAL_ARTIFACT=FALSE")
    print("PROVIDER_CALLS=0")
    print(f"PREVIEW_SHA256={sha256_file(output_path)}")


def verify(args: argparse.Namespace) -> None:
    payload = load_and_verify_preview(Path(args.input))
    print("TEAM_B_RECEIVED_PREVIEW_VERIFY=PASS")
    print(f"SYMBOL={payload['stock_code']}")
    print(f"SESSION={payload['date']}")
    print("TEAM_B_REAL_ARTIFACT=FALSE")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Offline legacy Team B received preview")
    commands = root.add_subparsers(dest="command", required=True)

    def add_prepare_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--stock-name", default="삼성전자")
        command.add_argument("--stock-code", default="005930.KS")
        command.add_argument("--csv", default="/app/data/stock/005930.KS.csv")
        command.add_argument("--pth", default="/app/data/model/005930.KS_lstm.pth")
        command.add_argument("--output", default="/output/005930.KS.json")
        command.add_argument("--expected-pth-sha256", default=RECEIVED_PTH_SHA256)
        command.add_argument("--expected-csv-sha256", default=RECEIVED_CSV_SHA256)
        command.set_defaults(handler=prepare)

    runner = commands.add_parser("prepare")
    add_prepare_arguments(runner)
    # 과거 로컬 명령은 동일한 원자 prepare 동작으로만 유지한다.
    legacy_runner = commands.add_parser("run")
    add_prepare_arguments(legacy_runner)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--input", default="/output/005930.KS.json")
    verifier.set_defaults(handler=verify)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.handler(arguments)
