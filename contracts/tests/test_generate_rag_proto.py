from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from google.protobuf import descriptor_pb2


_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _ROOT / "contracts/generate_rag_proto.py"
_SPEC = importlib.util.spec_from_file_location("generate_rag_proto", _GENERATOR)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class RagProtoGenerationTest(unittest.TestCase):
    def test_generator_outputs_match_tracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = _MODULE._run_protoc(Path(directory))

        self.assertEqual(set(_MODULE.OUTPUTS), set(generated))
        self.assertTrue(
            all(path.read_bytes() == generated[path] for path in _MODULE.OUTPUTS)
        )

    def test_breaking_field_number_is_rejected(self) -> None:
        descriptor = descriptor_pb2.FileDescriptorSet.FromString(
            (_ROOT / "contracts/proto/rag.descriptor.pb").read_bytes()
        )
        request = next(
            item
            for item in descriptor.file[0].message_type
            if item.name == "RagAskRequest"
        )
        next(item for item in request.field if item.name == "question").number = 99

        with self.assertRaisesRegex(
            _MODULE.RagProtoGenerationError,
            "field name/number",
        ):
            _MODULE._validate_descriptor(
                descriptor.SerializeToString(deterministic=True)
            )


if __name__ == "__main__":
    unittest.main()
