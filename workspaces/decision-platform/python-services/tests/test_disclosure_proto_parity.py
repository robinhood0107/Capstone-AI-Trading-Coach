from __future__ import annotations

import hashlib
from pathlib import Path

from google.protobuf import descriptor_pb2

from app.generated import disclosure_observation_pb2


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    while current.parent != current:
        if (current / "AGENTS.md").is_file():
            return current
        current = current.parent
    raise AssertionError("repository root was not found")


def test_python_descriptor_matches_tracked_proto_descriptor_and_hash() -> None:
    root = _repo_root()
    descriptor_path = root / "contracts/proto/disclosure_observation.descriptor.pb"
    expected_hash = (
        (root / "contracts/proto/disclosure_observation.descriptor.sha256")
        .read_text(encoding="ascii")
        .strip()
    )
    descriptor_bytes = descriptor_path.read_bytes()
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor_bytes)

    assert hashlib.sha256(descriptor_bytes).hexdigest() == expected_hash
    assert len(descriptor_set.file) == 1
    assert (
        disclosure_observation_pb2.DESCRIPTOR.serialized_pb
        == descriptor_set.file[0].SerializeToString()
    )
