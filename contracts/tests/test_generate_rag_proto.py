from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2


_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _ROOT / "contracts/generate_rag_proto.py"
_SPEC = importlib.util.spec_from_file_location("generate_rag_proto", _GENERATOR)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_rag_proto_generator_outputs_match_tracked_files(tmp_path: Path) -> None:
    generated = _MODULE._run_protoc(tmp_path)

    assert set(generated) == set(_MODULE.OUTPUTS)
    assert all(path.read_bytes() == generated[path] for path in _MODULE.OUTPUTS)


def test_rag_proto_breaking_field_number_is_rejected() -> None:
    descriptor = descriptor_pb2.FileDescriptorSet.FromString(
        (_ROOT / "contracts/proto/rag.descriptor.pb").read_bytes()
    )
    request = next(
        item for item in descriptor.file[0].message_type if item.name == "RagAskRequest"
    )
    next(item for item in request.field if item.name == "question").number = 99

    with pytest.raises(_MODULE.RagProtoGenerationError, match="field name/number"):
        _MODULE._validate_descriptor(descriptor.SerializeToString(deterministic=True))
