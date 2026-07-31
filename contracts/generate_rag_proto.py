from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from google.protobuf import descriptor_pb2


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_PATH = REPO_ROOT / "contracts/proto/rag.proto"
PYTHON_GENERATED_DIR = (
    REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
)
DESCRIPTOR_PATH = REPO_ROOT / "contracts/proto/rag.descriptor.pb"
DESCRIPTOR_HASH_PATH = REPO_ROOT / "contracts/proto/rag.descriptor.sha256"
OUTPUTS: Final[tuple[Path, ...]] = (
    PYTHON_GENERATED_DIR / "rag_pb2.py",
    PYTHON_GENERATED_DIR / "rag_pb2.pyi",
    PYTHON_GENERATED_DIR / "rag_pb2_grpc.py",
    DESCRIPTOR_PATH,
    DESCRIPTOR_HASH_PATH,
)


class RagProtoGenerationError(RuntimeError):
    """RAG proto codegen 또는 canonical compatibility drift를 감춘 오류다."""


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    python_dir = output_dir / "python"
    python_dir.mkdir(parents=True)
    descriptor_path = output_dir / "rag.descriptor.pb"
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
        raise RagProtoGenerationError(
            "grpc_tools.protoc failed without exposing request or source content"
        )
    pb2 = (python_dir / "rag_pb2.py").read_bytes()
    pb2_pyi = (python_dir / "rag_pb2.pyi").read_bytes()
    pb2_grpc = (python_dir / "rag_pb2_grpc.py").read_text(encoding="utf-8")
    pb2_grpc = pb2_grpc.replace(
        "import rag_pb2 as rag__pb2",
        "from app.generated import rag_pb2 as rag__pb2",
    )
    if "from app.generated import rag_pb2" not in pb2_grpc:
        raise RagProtoGenerationError("Python gRPC generated import rewrite did not apply")
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(
        descriptor_path.read_bytes()
    )
    for descriptor_file in descriptor_set.file:
        for message in descriptor_file.message_type:
            for field in message.field:
                field.ClearField("json_name")
    descriptor = descriptor_set.SerializeToString(deterministic=True)
    _validate_descriptor(descriptor)
    descriptor_hash = hashlib.sha256(descriptor).hexdigest().encode("ascii") + b"\n"
    return {
        OUTPUTS[0]: pb2,
        OUTPUTS[1]: pb2_pyi,
        OUTPUTS[2]: pb2_grpc.encode("utf-8"),
        OUTPUTS[3]: descriptor,
        OUTPUTS[4]: descriptor_hash,
    }


def _validate_descriptor(payload: bytes) -> None:
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(payload)
    if len(descriptor_set.file) != 1:
        raise RagProtoGenerationError("descriptor set must contain exactly one proto")
    descriptor = descriptor_set.file[0]
    if (
        descriptor.name != "rag.proto"
        or descriptor.package != "capstone.decision.v1"
        or descriptor.syntax != "proto3"
    ):
        raise RagProtoGenerationError("RAG proto file identity drifted")
    services = {service.name: service for service in descriptor.service}
    service = services.get("RagService")
    if service is None or len(service.method) != 1:
        raise RagProtoGenerationError("RagService surface drifted")
    method = service.method[0]
    if (
        method.name != "Ask"
        or method.input_type != ".capstone.decision.v1.RagAskRequest"
        or method.output_type != ".capstone.decision.v1.RagAskResponse"
        or method.client_streaming
        or method.server_streaming
    ):
        raise RagProtoGenerationError("RagService Ask signature drifted")
    expected_fields = {
        "RagAskRequest": {
            "request_id": 1,
            "owner_scope_claim": 2,
            "question": 3,
            "answer_mode": 4,
            "related_symbols": 5,
            "topics": 6,
            "consent_context": 7,
            "policy_context": 8,
        },
        "RagConsentContext": {"granted": 1, "policy_version": 2},
        "RagPolicyContext": {
            "policy_id": 1,
            "policy_version": 2,
            "active_generation_id": 3,
            "embedding_profile_id": 4,
        },
        "RagAskResponse": {
            "request_id": 1,
            "status": 2,
            "answer": 3,
            "citations": 4,
            "citation_coverage": 5,
            "retrieval_failure": 6,
            "guardrail_flags": 7,
            "generation_id": 8,
            "embedding_profile_id": 9,
            "failure_code": 10,
            "provider_physical_counts": 11,
            "authorized_top5_chunk_revision_ids": 12,
            "external_provider_candidate": 13,
            "policy_version": 14,
        },
        "RagCitation": {
            "citation_id": 1,
            "source_id": 2,
            "source_revision_id": 3,
            "chunk_revision_id": 4,
            "generation_id": 5,
            "title": 6,
            "section_title": 7,
            "canonical_url": 8,
        },
        "ProviderPhysicalCounts": {
            "total": 1,
            "gemini": 2,
            "openai": 3,
            "voyage": 4,
        },
    }
    actual = {
        message.name: {field.name: field.number for field in message.field}
        for message in descriptor.message_type
    }
    if actual != expected_fields:
        raise RagProtoGenerationError("RAG proto field name/number contract drifted")
    statuses = {
        value.name: value.number
        for enum in descriptor.enum_type
        if enum.name == "RagResponseStatus"
        for value in enum.value
    }
    if statuses != {
        "RAG_RESPONSE_STATUS_UNSPECIFIED": 0,
        "RAG_RESPONSE_STATUS_ANSWERED": 1,
        "RAG_RESPONSE_STATUS_RETRIEVAL_ONLY": 2,
        "RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE": 3,
        "RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE": 4,
        "RAG_RESPONSE_STATUS_BLOCKED_ADVICE": 5,
        "RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE": 6,
    }:
        raise RagProtoGenerationError("RAG response status numbers drifted")


def _write(outputs: dict[Path, bytes]) -> None:
    for path, payload in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            os.chmod(path, 0o644)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        print(f"WROTE {path.relative_to(REPO_ROOT).as_posix()}")


def _check(outputs: dict[Path, bytes]) -> int:
    failures = 0
    for path, expected in outputs.items():
        try:
            actual = path.read_bytes()
        except OSError:
            failures += 1
            print(f"FAIL missing generated RAG proto artifact {path.relative_to(REPO_ROOT)}")
            continue
        if actual != expected:
            failures += 1
            print(f"FAIL generated RAG proto drift {path.relative_to(REPO_ROOT)}")
    if failures == 0:
        print("S4_6_RAG_PROTO_DESCRIPTOR_VERIFIED")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and verify S4.6 Python stubs and descriptor parity."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="s46-rag-proto-") as raw_dir:
            outputs = _run_protoc(Path(raw_dir))
        if arguments.write:
            _write(outputs)
            return 0
        return 1 if _check(outputs) else 0
    except (OSError, RagProtoGenerationError) as error:
        print(f"S4.6 RAG proto generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
