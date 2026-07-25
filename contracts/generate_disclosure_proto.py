from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from google.protobuf import descriptor_pb2

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_PATH = REPO_ROOT / "contracts/proto/disclosure_observation.proto"
PYTHON_GENERATED_DIR = (
    REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
)
DESCRIPTOR_PATH = REPO_ROOT / "contracts/proto/disclosure_observation.descriptor.pb"
DESCRIPTOR_HASH_PATH = (
    REPO_ROOT / "contracts/proto/disclosure_observation.descriptor.sha256"
)
OUTPUTS: Final[tuple[Path, ...]] = (
    PYTHON_GENERATED_DIR / "__init__.py",
    PYTHON_GENERATED_DIR / "disclosure_observation_pb2.py",
    PYTHON_GENERATED_DIR / "disclosure_observation_pb2.pyi",
    PYTHON_GENERATED_DIR / "disclosure_observation_pb2_grpc.py",
    DESCRIPTOR_PATH,
    DESCRIPTOR_HASH_PATH,
)


class ProtoGenerationError(RuntimeError):
    """proto codegen 또는 compatibility 계약이 어긋났을 때 생성 단계를 닫는다."""


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    python_dir = output_dir / "python"
    python_dir.mkdir(parents=True)
    descriptor_path = output_dir / "disclosure_observation.descriptor.pb"
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_PATH.parent}",
        f"--python_out={python_dir}",
        f"--pyi_out={python_dir}",
        f"--grpc_python_out={python_dir}",
        f"--descriptor_set_out={descriptor_path}",
        str(PROTO_PATH),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProtoGenerationError("grpc_tools.protoc failed without exposing raw request data")
    pb2 = (python_dir / "disclosure_observation_pb2.py").read_bytes()
    pb2_pyi = (python_dir / "disclosure_observation_pb2.pyi").read_bytes()
    pb2_grpc = (
        python_dir / "disclosure_observation_pb2_grpc.py"
    ).read_text(encoding="utf-8")
    pb2_grpc = pb2_grpc.replace(
        "import disclosure_observation_pb2 as disclosure__observation__pb2",
        "from app.generated import disclosure_observation_pb2 as disclosure__observation__pb2",
    )
    if "from app.generated import disclosure_observation_pb2" not in pb2_grpc:
        raise ProtoGenerationError("Python gRPC generated import rewrite did not apply")
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(
        descriptor_path.read_bytes()
    )
    # protoc descriptor-set은 기본 json_name도 materialize하지만 Python runtime
    # descriptor는 이를 생략한다. 비교 가능한 canonical descriptor로 정규화한다.
    for descriptor_file in descriptor_set.file:
        for message in descriptor_file.message_type:
            for field in message.field:
                field.ClearField("json_name")
    descriptor = descriptor_set.SerializeToString(deterministic=True)
    _validate_descriptor(descriptor)
    descriptor_hash = hashlib.sha256(descriptor).hexdigest().encode("ascii") + b"\n"
    return {
        OUTPUTS[0]: (
            '"""S2.3 disclosure proto의 tracked Python codegen package."""\n'
        ).encode("utf-8"),
        OUTPUTS[1]: pb2,
        OUTPUTS[2]: pb2_pyi,
        OUTPUTS[3]: pb2_grpc.encode("utf-8"),
        OUTPUTS[4]: descriptor,
        OUTPUTS[5]: descriptor_hash,
    }


def _validate_descriptor(payload: bytes) -> None:
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(payload)
    if len(descriptor_set.file) != 1:
        raise ProtoGenerationError("descriptor set must contain exactly one proto")
    descriptor = descriptor_set.file[0]
    if (
        descriptor.name != "disclosure_observation.proto"
        or descriptor.package != "capstone.decision.v1"
        or descriptor.syntax != "proto3"
    ):
        raise ProtoGenerationError("proto file identity drifted")
    services = {service.name: service for service in descriptor.service}
    service = services.get("DisclosureObservationService")
    if service is None or len(service.method) != 1:
        raise ProtoGenerationError("stored-observation service surface drifted")
    method = service.method[0]
    if (
        method.name != "GetDisclosureEvents"
        or method.input_type
        != ".capstone.decision.v1.GetDisclosureEventsRequest"
        or method.output_type
        != ".capstone.decision.v1.GetDisclosureEventsResponse"
        or method.client_streaming
        or method.server_streaming
    ):
        raise ProtoGenerationError("GetDisclosureEvents signature drifted")
    expected_fields = {
        "GetDisclosureEventsRequest": {
            "symbol": 1,
            "corp_code": 2,
            "as_of": 3,
            "window_from": 4,
            "window_to": 5,
        },
        "GetDisclosureEventsResponse": {
            "symbol": 1,
            "corp_code": 2,
            "as_of": 3,
            "window_from": 4,
            "window_to": 5,
            "score": 6,
            "mapping_version": 7,
            "events": 8,
            "warnings": 9,
            "source_refs": 10,
            "observed_at": 11,
            "complete": 12,
        },
        "DisclosureRiskEvent": {
            "event_code": 1,
            "receipt_no": 2,
            "occurred_on": 3,
        },
        "DisclosureRiskWarning": {
            "code": 1,
            "event_code": 2,
            "receipt_no": 3,
            "message": 4,
        },
    }
    actual = {
        message.name: {field.name: field.number for field in message.field}
        for message in descriptor.message_type
    }
    if actual != expected_fields:
        raise ProtoGenerationError("proto field name/number compatibility contract drifted")


def _write(outputs: dict[Path, bytes]) -> None:
    for path, payload in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"WROTE {path.relative_to(REPO_ROOT).as_posix()}")


def _check(outputs: dict[Path, bytes]) -> int:
    failures = 0
    for path, expected in outputs.items():
        try:
            actual = path.read_bytes()
        except OSError:
            failures += 1
            print(
                f"FAIL missing generated proto artifact {path.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
            continue
        if actual != expected:
            failures += 1
            print(
                f"FAIL generated proto drift {path.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
        else:
            print(f"PASS generated proto {path.relative_to(REPO_ROOT)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and verify S2.3 Python stubs and descriptor parity."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="s23-proto-") as raw_dir:
            outputs = _run_protoc(Path(raw_dir))
        if arguments.write:
            _write(outputs)
            return 0
        return 1 if _check(outputs) else 0
    except (OSError, ProtoGenerationError) as error:
        print(f"S2.3 proto generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
