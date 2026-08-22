from __future__ import annotations

import hashlib
from pathlib import Path

from google.protobuf import descriptor_pb2

from app.generated import async_worker_pb2


def test_async_worker_descriptor_and_generated_python_are_byte_locked() -> None:
    root = Path(__file__).resolve().parents[5]
    descriptor = (root / "contracts/proto/async_worker.descriptor.pb").read_bytes()
    expected_hash = (
        root / "contracts/proto/async_worker.descriptor.sha256"
    ).read_text(encoding="ascii").strip()
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor)

    assert hashlib.sha256(descriptor).hexdigest() == expected_hash
    assert len(descriptor_set.file) == 1
    assert (
        async_worker_pb2.DESCRIPTOR.serialized_pb
        == descriptor_set.file[0].SerializeToString()
    )
    service = async_worker_pb2.DESCRIPTOR.services_by_name["AsyncWorkerService"]
    assert [method.name for method in service.methods] == ["Process"]
