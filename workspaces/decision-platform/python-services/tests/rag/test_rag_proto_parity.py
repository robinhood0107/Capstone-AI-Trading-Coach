from __future__ import annotations

import hashlib
from pathlib import Path

from google.protobuf import descriptor_pb2

from app.generated import rag_pb2


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    while current.parent != current:
        if (current / "AGENTS.md").is_file():
            return current
        current = current.parent
    raise AssertionError("repository root was not found")


def test_rag_python_descriptor_matches_tracked_canonical_descriptor() -> None:
    root = _repo_root()
    descriptor_bytes = (root / "contracts/proto/rag.descriptor.pb").read_bytes()
    expected_hash = (root / "contracts/proto/rag.descriptor.sha256").read_text(
        encoding="ascii"
    ).strip()
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor_bytes)

    assert hashlib.sha256(descriptor_bytes).hexdigest() == expected_hash
    assert len(descriptor_set.file) == 1
    assert rag_pb2.DESCRIPTOR.serialized_pb == descriptor_set.file[0].SerializeToString()


def test_rag_descriptor_locks_service_method_and_field_numbers() -> None:
    descriptor = rag_pb2.DESCRIPTOR
    service = descriptor.services_by_name["RagService"]

    assert service.full_name == "capstone.decision.v1.RagService"
    assert list(service.methods_by_name) == ["Ask"]
    method = service.methods_by_name["Ask"]
    assert method.input_type.full_name == "capstone.decision.v1.RagAskRequest"
    assert method.output_type.full_name == "capstone.decision.v1.RagAskResponse"
    assert {
        field.name: field.number for field in descriptor.message_types_by_name["RagAskRequest"].fields
    } == {
        "request_id": 1,
        "owner_scope_claim": 2,
        "question": 3,
        "answer_mode": 4,
        "related_symbols": 5,
        "topics": 6,
        "consent_context": 7,
        "policy_context": 8,
    }
    assert {
        field.name: field.number for field in descriptor.message_types_by_name["RagAskResponse"].fields
    } == {
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
    }
