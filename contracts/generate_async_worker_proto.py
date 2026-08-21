"""Generate deterministic S7 async worker protobuf artifacts."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

from google.protobuf import descriptor_pb2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402

PROTO = ROOT / "contracts/proto/async_worker.proto"
DESCRIPTOR = ROOT / "contracts/proto/async_worker.descriptor.pb"
DESCRIPTOR_HASH = ROOT / "contracts/proto/async_worker.descriptor.sha256"
PYTHON_GENERATED = ROOT / "workspaces/decision-platform/python-services/app/generated"
OUTPUTS = (
    PYTHON_GENERATED / "async_worker_pb2.py",
    PYTHON_GENERATED / "async_worker_pb2.pyi",
    PYTHON_GENERATED / "async_worker_pb2_grpc.py",
    DESCRIPTOR,
    DESCRIPTOR_HASH,
)


class AsyncWorkerProtoError(RuntimeError):
    """Raised when canonical protobuf generation or validation fails."""


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    descriptor = output_dir / DESCRIPTOR.name
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO.parent}",
        f"--python_out={output_dir}",
        f"--pyi_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        f"--descriptor_set_out={descriptor}",
        str(PROTO),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True)
    if completed.returncode != 0:
        raise AsyncWorkerProtoError("grpc_tools.protoc failed")
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor.read_bytes())
    if len(descriptor_set.file) != 1:
        raise AsyncWorkerProtoError("descriptor set must contain exactly one file")
    item = descriptor_set.file[0]
    if item.name != "async_worker.proto" or item.package != "capstone.asyncworker.v1":
        raise AsyncWorkerProtoError("async worker descriptor identity drift")
    if [service.name for service in item.service] != ["AsyncWorkerService"]:
        raise AsyncWorkerProtoError("async worker service drift")
    if [method.name for method in item.service[0].method] != ["Process"]:
        raise AsyncWorkerProtoError("async worker method drift")
    for message in item.message_type:
        for field in message.field:
            field.ClearField("json_name")
    descriptor_bytes = descriptor_set.SerializeToString(deterministic=True)
    grpc_stub = (output_dir / "async_worker_pb2_grpc.py").read_text(encoding="utf-8")
    grpc_stub = grpc_stub.replace(
        "import async_worker_pb2 as async__worker__pb2",
        "from app.generated import async_worker_pb2 as async__worker__pb2",
    )
    if "from app.generated import async_worker_pb2" not in grpc_stub:
        raise AsyncWorkerProtoError("generated gRPC import is not package-safe")
    return {
        PYTHON_GENERATED / "async_worker_pb2.py": (output_dir / "async_worker_pb2.py").read_bytes(),
        PYTHON_GENERATED / "async_worker_pb2.pyi": (output_dir / "async_worker_pb2.pyi").read_bytes(),
        PYTHON_GENERATED / "async_worker_pb2_grpc.py": grpc_stub.encode(),
        DESCRIPTOR: descriptor_bytes,
        DESCRIPTOR_HASH: (hashlib.sha256(descriptor_bytes).hexdigest() + "\n").encode(),
    }


def generate(*, check: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="s7-async-proto-") as raw:
        outputs = _run_protoc(Path(raw))
    for path, payload in outputs.items():
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                raise AsyncWorkerProtoError(f"generated artifact drift: {path.relative_to(ROOT)}")
        else:
            write_generated_path(ROOT, path, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        generate(check=args.check)
    except (AsyncWorkerProtoError, OSError) as error:
        print(f"S7_ASYNC_WORKER_PROTO_FAILED: {error}", file=sys.stderr)
        return 1
    print("S7_ASYNC_WORKER_PROTO_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
