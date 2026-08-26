from __future__ import annotations

import argparse
from pathlib import Path

from preview_contract import (
    RECEIVED_CSV_SHA256,
    RECEIVED_PTH_SHA256,
    load_and_verify_preview,
    mark_preview,
    sha256_file,
    verify_received_file,
)


def run(args: argparse.Namespace) -> None:
    # Verification/server paths must not import the model stack or need a writable cache.
    from return_engine import ReturnEngine

    csv_path = Path(args.csv)
    pth_path = Path(args.pth)
    output_path = Path(args.output)
    pth_hash = verify_received_file(pth_path, args.expected_pth_sha256, "received PTH")
    csv_hash = verify_received_file(csv_path, args.expected_csv_sha256, "received CSV")
    engine = ReturnEngine(
        stock_name=args.stock_name,
        stock_code=args.stock_code,
        stock_path=csv_path,
        model_path=pth_path,
        artifact_path=output_path,
    )
    engine.run(refresh=False)
    mark_preview(output_path, pth_hash, csv_hash)
    print("TEAM_B_RECEIVED_PREVIEW=PASS")
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
    runner = commands.add_parser("run")
    runner.add_argument("--stock-name", default="삼성전자")
    runner.add_argument("--stock-code", default="005930.KS")
    runner.add_argument("--csv", default="/app/data/stock/005930.KS.csv")
    runner.add_argument("--pth", default="/app/data/model/005930.KS_lstm.pth")
    runner.add_argument("--output", default="/output/005930.KS.json")
    runner.add_argument("--expected-pth-sha256", default=RECEIVED_PTH_SHA256)
    runner.add_argument("--expected-csv-sha256", default=RECEIVED_CSV_SHA256)
    runner.set_defaults(handler=run)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--input", default="/output/005930.KS.json")
    verifier.set_defaults(handler=verify)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.handler(arguments)
