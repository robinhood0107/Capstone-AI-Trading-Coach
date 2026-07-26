from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

from google.protobuf import descriptor_pb2

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_PATH = REPO_ROOT / "contracts/proto/brokerage.proto"
DESCRIPTOR_PATH = REPO_ROOT / "contracts/proto/brokerage.descriptor.pb"
DESCRIPTOR_SHA_PATH = REPO_ROOT / "contracts/proto/brokerage.descriptor.sha256"
PYTHON_GENERATED_DIR = REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
OUTPUTS = {
    DESCRIPTOR_PATH: b"",
    DESCRIPTOR_SHA_PATH: b"",
    PYTHON_GENERATED_DIR / "__init__.py": b'"""Tracked Python gRPC codegen package for Decision Platform contracts."""\n',
    PYTHON_GENERATED_DIR / "brokerage_pb2.py": b"",
    PYTHON_GENERATED_DIR / "brokerage_pb2.pyi": b"",
    PYTHON_GENERATED_DIR / "brokerage_pb2_grpc.py": b"",
}


class ProtoGenerationError(RuntimeError):
    """Generated brokerage proto artifacts drifted from the committed contract."""


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    python_dir = output_dir / "python"
    python_dir.mkdir(parents=True)
    descriptor_path = output_dir / "brokerage.descriptor.pb"
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_PATH.parent}",
        f"--python_out={python_dir}",
        f"--pyi_out={python_dir}",
        f"--grpc_python_out={python_dir}",
        f"--descriptor_set_out={descriptor_path}",
        "--include_imports",
        PROTO_PATH.name,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ProtoGenerationError("grpc_tools.protoc failed without exposing raw provider data")

    pb2 = (python_dir / "brokerage_pb2.py").read_text(encoding="utf-8")
    pb2_pyi = (python_dir / "brokerage_pb2.pyi").read_bytes()
    pb2_grpc = (python_dir / "brokerage_pb2_grpc.py").read_text(encoding="utf-8")
    pb2_grpc = pb2_grpc.replace("import brokerage_pb2 as brokerage__pb2", "from app.generated import brokerage_pb2 as brokerage__pb2")
    if "from app.generated import brokerage_pb2" not in pb2_grpc:
        raise ProtoGenerationError("Generated gRPC imports are not package-safe")

    descriptor_bytes = descriptor_path.read_bytes()
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_bytes)
    if len(descriptor_set.file) != 1:
        raise ProtoGenerationError("descriptor set must contain exactly one proto")
    descriptor = descriptor_set.file[0]
    if (
        descriptor.name != "brokerage.proto"
        or descriptor.package != "capstone.decision.v1"
        or descriptor.syntax != "proto3"
    ):
        raise ProtoGenerationError("brokerage proto identity drifted")
    service_names = [service.name for service in descriptor.service]
    if service_names != ["BrokerageService"]:
        raise ProtoGenerationError("brokerage service contract drifted")
    method_names = [method.name for method in descriptor.service[0].method]
    if method_names != [
        "SubmitMockCashOrder",
        "CancelMockCashOrder",
        "GetMockBalance",
        "GetMockBuyable",
    ]:
        raise ProtoGenerationError("brokerage rpc method order drifted")

    descriptor_sha = hashlib.sha256(descriptor_bytes).hexdigest().encode("ascii") + b"\n"
    return {
        DESCRIPTOR_PATH: descriptor_bytes,
        DESCRIPTOR_SHA_PATH: descriptor_sha,
        PYTHON_GENERATED_DIR / "__init__.py": OUTPUTS[PYTHON_GENERATED_DIR / "__init__.py"],
        PYTHON_GENERATED_DIR / "brokerage_pb2.py": pb2.encode("utf-8"),
        PYTHON_GENERATED_DIR / "brokerage_pb2.pyi": pb2_pyi,
        PYTHON_GENERATED_DIR / "brokerage_pb2_grpc.py": pb2_grpc.encode("utf-8"),
    }


def generate(*, check: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="s31-brokerage-proto-") as raw_dir:
        outputs = _run_protoc(Path(raw_dir))
    failures = 0
    for path, expected in outputs.items():
        if check:
            if not path.is_file():
                print(f"FAIL missing generated proto artifact {path.relative_to(REPO_ROOT)}", file=sys.stderr)
                failures += 1
                continue
            if path.read_bytes() != expected:
                print(f"FAIL generated proto drift {path.relative_to(REPO_ROOT)}", file=sys.stderr)
                failures += 1
                continue
            print(f"PASS generated proto {path.relative_to(REPO_ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
            print(f"WROTE {path.relative_to(REPO_ROOT)}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        return generate(check=args.check)
    except ProtoGenerationError as error:
        print(f"S3.1 brokerage proto generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
