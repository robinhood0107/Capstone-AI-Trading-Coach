from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SERVICE = ROOT / "workspaces/decision-platform/python-services"
if str(PYTHON_SERVICE) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE))

from app.rag.s4_5_evaluation import (  # noqa: E402
    S4_5_EVAL_MANIFEST_PATH,
    S4_5_REPORT_PATH,
    build_s4_5_manifest,
    evaluate_s4_5_manifest,
)
from app.rag.provider_control_plane import (  # noqa: E402
    S4_5_PROVIDER_REPORT_PATH,
    build_s4_5_provider_report,
)


def _bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the S4.5 exact-60 fixture evaluation artifacts."
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = build_s4_5_manifest()
    artifacts = {
        S4_5_EVAL_MANIFEST_PATH: _bytes(manifest),
        S4_5_REPORT_PATH: _bytes(evaluate_s4_5_manifest(manifest)),
        S4_5_PROVIDER_REPORT_PATH: _bytes(build_s4_5_provider_report()),
    }
    if args.check:
        drifted = [
            str(path.relative_to(ROOT))
            for path, expected in artifacts.items()
            if not path.is_file() or path.read_bytes() != expected
        ]
        if drifted:
            for path in drifted:
                print(f"S4_5_EVALUATION_DRIFT: {path}", file=sys.stderr)
            return 1
        print("S4_5_EXACT_60_FIXTURE_EVALUATION_VERIFIED")
        return 0
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print("S4_5_EXACT_60_FIXTURE_EVALUATION_GENERATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
