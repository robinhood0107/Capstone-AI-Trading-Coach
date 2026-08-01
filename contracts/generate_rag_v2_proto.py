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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402


PROTO_PATH = REPO_ROOT / "contracts/proto/rag_v2.proto"
PYTHON_GENERATED_DIR = (
    REPO_ROOT / "workspaces/decision-platform/python-services/app/generated"
)
DESCRIPTOR_PATH = REPO_ROOT / "contracts/proto/rag_v2.descriptor.pb"
DESCRIPTOR_HASH_PATH = REPO_ROOT / "contracts/proto/rag_v2.descriptor.sha256"
OUTPUTS: Final[tuple[Path, ...]] = (
    PYTHON_GENERATED_DIR / "rag_v2_pb2.py",
    PYTHON_GENERATED_DIR / "rag_v2_pb2.pyi",
    PYTHON_GENERATED_DIR / "rag_v2_pb2_grpc.py",
    DESCRIPTOR_PATH,
    DESCRIPTOR_HASH_PATH,
)
FROZEN_V1_HASHES: Final[dict[Path, str]] = {
    REPO_ROOT / "contracts/proto/rag.proto": (
        "d9e4182d5479f27f479187e912d0db02814474dd00306e78b7ef03fb53afc13c"
    ),
    REPO_ROOT / "contracts/proto/rag.descriptor.pb": (
        "633a1214b48221eeaf3d96734353f86dfaf1a20f9e74852511c10b372bf399f4"
    ),
}


class RagV2ProtoGenerationError(RuntimeError):
    """RAG v2 proto 생성 또는 서버 선택 계약 drift를 숨기지 않는 오류다."""


def _validate_frozen_v1() -> None:
    for path, expected_hash in FROZEN_V1_HASHES.items():
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise RagV2ProtoGenerationError("frozen RAG v1 artifact is unavailable") from error
        if actual_hash != expected_hash:
            raise RagV2ProtoGenerationError("frozen RAG v1 artifact drifted")


def _run_protoc(output_dir: Path) -> dict[Path, bytes]:
    python_dir = output_dir / "python"
    python_dir.mkdir(parents=True)
    descriptor_path = output_dir / "rag_v2.descriptor.pb"
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
        raise RagV2ProtoGenerationError(
            "grpc_tools.protoc failed without exposing request or source content"
        )

    pb2 = (python_dir / "rag_v2_pb2.py").read_bytes()
    pb2_pyi = (python_dir / "rag_v2_pb2.pyi").read_bytes()
    pb2_grpc = (python_dir / "rag_v2_pb2_grpc.py").read_text(encoding="utf-8")
    pb2_grpc = pb2_grpc.replace("import grpc\nimport warnings\n", "import grpc\n")
    pb2_grpc = pb2_grpc.replace(
        "import rag_v2_pb2 as rag__v2__pb2",
        "from app.generated import rag_v2_pb2 as rag__v2__pb2",
    )
    if "from app.generated import rag_v2_pb2" not in pb2_grpc:
        raise RagV2ProtoGenerationError("Python gRPC generated import rewrite did not apply")

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
        raise RagV2ProtoGenerationError("descriptor set must contain exactly one proto")
    descriptor = descriptor_set.file[0]
    if (
        descriptor.name != "rag_v2.proto"
        or descriptor.package != "capstone.decision.v2"
        or descriptor.syntax != "proto3"
    ):
        raise RagV2ProtoGenerationError("RAG v2 proto file identity drifted")

    services = {service.name: service for service in descriptor.service}
    service = services.get("RagService")
    if service is None or len(service.method) != 1:
        raise RagV2ProtoGenerationError("RAG v2 service surface drifted")
    method = service.method[0]
    if (
        method.name != "Ask"
        or method.input_type != ".capstone.decision.v2.RagAskRequest"
        or method.output_type != ".capstone.decision.v2.RagAskResponse"
        or method.client_streaming
        or method.server_streaming
    ):
        raise RagV2ProtoGenerationError("RAG v2 Ask signature drifted")

    expected_fields = {
        "RagAskRequest": {
            "request_id": 1,
            "owner_scope_claim": 2,
            "question": 3,
            "answer_mode": 4,
            "related_symbols": 5,
            "topics": 6,
            "consent_context": 7,
        },
        "RagConsentContext": {"granted": 1, "policy_version": 2},
        "RagAskResponse": {
            "request_id": 1,
            "status": 2,
            "answer": 3,
            "citations": 4,
            "citation_coverage": 5,
            "retrieval_failure": 6,
            "guardrail_flags": 7,
            "exact30_generation_id": 8,
            "oa_generation_id": 9,
            "owner_generation_id": 10,
            "embedding_profile_id": 11,
            "failure_code": 12,
            "provider_physical_counts": 13,
            "authorized_top5_chunk_revision_ids": 14,
            "external_provider_candidate": 15,
            "policy_version": 16,
        },
        "RagCitation": {
            "citation_id": 1,
            "source_id": 2,
            "source_revision_id": 3,
            "chunk_revision_id": 4,
            "generation_id": 5,
            "public_web": 10,
            "local_document": 11,
        },
        "PublicWebCitation": {"title": 1, "canonical_url": 2, "locator": 3},
        "LocalDocumentCitation": {
            "document_id": 1,
            "display_name": 2,
            "locator": 3,
        },
        "DocumentLocator": {"page": 1, "slide": 2, "sheet": 3, "section": 4},
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
        raise RagV2ProtoGenerationError("RAG v2 proto field name/number contract drifted")

    request_fields = actual["RagAskRequest"]
    if {"corpus", "profile", "top_k"}.intersection(request_fields):
        raise RagV2ProtoGenerationError("RAG v2 request exposes a client selector")
    citation = next(
        message for message in descriptor.message_type if message.name == "RagCitation"
    )
    if len(citation.oneof_decl) != 1 or citation.oneof_decl[0].name != "citation":
        raise RagV2ProtoGenerationError("RAG v2 citation union drifted")
    union_fields = {field.name: field.oneof_index for field in citation.field if field.HasField("oneof_index")}
    if union_fields != {"public_web": 0, "local_document": 0}:
        raise RagV2ProtoGenerationError("RAG v2 citation union fields drifted")

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
        "RAG_RESPONSE_STATUS_CORPUS_NOT_READY": 7,
    }:
        raise RagV2ProtoGenerationError("RAG v2 response status numbers drifted")


def _write(outputs: dict[Path, bytes]) -> None:
    for path, payload in outputs.items():
        write_generated_path(REPO_ROOT, path, payload)
        print(f"WROTE {path.relative_to(REPO_ROOT).as_posix()}")


def _check(outputs: dict[Path, bytes]) -> int:
    failures = 0
    for path, expected in outputs.items():
        try:
            actual = path.read_bytes()
        except OSError:
            failures += 1
            print(
                "FAIL missing generated RAG v2 proto artifact "
                f"{path.relative_to(REPO_ROOT)}"
            )
            continue
        if actual != expected:
            failures += 1
            print(f"FAIL generated RAG v2 proto drift {path.relative_to(REPO_ROOT)}")
    if failures == 0:
        print("S4_7D_RAG_V2_PROTO_DESCRIPTOR_VERIFIED")
    return failures


def main() -> int:
    """RAG v2 proto와 Python stub을 생성하거나 tracked byte parity를 확인한다."""

    parser = argparse.ArgumentParser(
        description="Generate and verify S4.7D RAG v2 Python stubs and descriptor."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        _validate_frozen_v1()
        with tempfile.TemporaryDirectory(prefix="s47d-rag-v2-proto-") as raw_dir:
            outputs = _run_protoc(Path(raw_dir))
        if arguments.write:
            _write(outputs)
            return 0
        return 1 if _check(outputs) else 0
    except (OSError, RagV2ProtoGenerationError) as error:
        print(f"S4.7D RAG v2 proto generation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
