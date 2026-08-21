from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO = REPO_ROOT / "contracts/proto/financial_engineering.proto"
GENERATED = REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
DESCRIPTOR = REPO_ROOT / "contracts/proto/financial_engineering.descriptor.pb"
HASH = REPO_ROOT / "contracts/proto/financial_engineering.descriptor.sha256"


def generate(root: Path) -> dict[Path, bytes]:
    python_dir = root / "python"
    python_dir.mkdir(parents=True)
    descriptor = root / DESCRIPTOR.name
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO.parent}",
        f"--python_out={python_dir}",
        f"--pyi_out={python_dir}",
        f"--grpc_python_out={python_dir}",
        f"--descriptor_set_out={descriptor}",
        str(PROTO),
    ]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("financial engineering proto generation failed")
    grpc_path = python_dir / "financial_engineering_pb2_grpc.py"
    grpc_text = grpc_path.read_text(encoding="utf-8").replace(
        "import financial_engineering_pb2 as financial__engineering__pb2",
        "from app.generated import financial_engineering_pb2 as financial__engineering__pb2",
    )
    descriptor_bytes = descriptor.read_bytes()
    return {
        GENERATED / "financial_engineering_pb2.py": (python_dir / "financial_engineering_pb2.py").read_bytes(),
        GENERATED / "financial_engineering_pb2.pyi": (python_dir / "financial_engineering_pb2.pyi").read_bytes(),
        GENERATED / "financial_engineering_pb2_grpc.py": grpc_text.encode(),
        DESCRIPTOR: descriptor_bytes,
        HASH: (hashlib.sha256(descriptor_bytes).hexdigest() + "\n").encode(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="s64-proto-") as value:
        outputs = generate(Path(value))
    failed = False
    for path, payload in outputs.items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            print(f"WROTE {path.relative_to(REPO_ROOT)}")
        elif not path.exists() or path.read_bytes() != payload:
            failed = True
            print(f"FAIL {path.relative_to(REPO_ROOT)}")
        else:
            print(f"PASS {path.relative_to(REPO_ROOT)}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
