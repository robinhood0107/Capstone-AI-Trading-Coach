from __future__ import annotations

import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path

from google.protobuf import descriptor_pb2


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "contracts/generate_rag_v2_proto.py"
SPEC = importlib.util.spec_from_file_location("generate_rag_v2_proto", GENERATOR)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RagV2ProtoGenerationTest(unittest.TestCase):
    def test_v1_proto_and_descriptor_bytes_are_unchanged(self) -> None:
        self.assertEqual(
            "d9e4182d5479f27f479187e912d0db02814474dd00306e78b7ef03fb53afc13c",
            hashlib.sha256((ROOT / "contracts/proto/rag.proto").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            "633a1214b48221eeaf3d96734353f86dfaf1a20f9e74852511c10b372bf399f4",
            hashlib.sha256(
                (ROOT / "contracts/proto/rag.descriptor.pb").read_bytes()
            ).hexdigest(),
        )

    def test_generator_outputs_match_tracked_v2_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s47d-rag-v2-proto-test-") as directory:
            generated = MODULE._run_protoc(Path(directory))

        self.assertEqual(set(MODULE.OUTPUTS), set(generated))
        self.assertTrue(
            all(path.read_bytes() == generated[path] for path in MODULE.OUTPUTS)
        )

    def test_descriptor_locks_unary_v2_service_and_tagged_citation_union(self) -> None:
        descriptor = descriptor_pb2.FileDescriptorSet.FromString(
            (ROOT / "contracts/proto/rag_v2.descriptor.pb").read_bytes()
        )
        file_descriptor = descriptor.file[0]
        self.assertEqual("rag_v2.proto", file_descriptor.name)
        self.assertEqual("capstone.decision.v2", file_descriptor.package)

        service = next(item for item in file_descriptor.service if item.name == "RagService")
        self.assertEqual(["Ask"], [method.name for method in service.method])
        self.assertFalse(service.method[0].client_streaming)
        self.assertFalse(service.method[0].server_streaming)

        citation = next(
            item for item in file_descriptor.message_type if item.name == "RagCitation"
        )
        self.assertEqual(1, len(citation.oneof_decl))
        self.assertEqual("citation", citation.oneof_decl[0].name)
        citation_fields = {field.name: field for field in citation.field}
        self.assertEqual(0, citation_fields["public_web"].oneof_index)
        self.assertEqual(0, citation_fields["local_document"].oneof_index)

        request = next(
            item for item in file_descriptor.message_type if item.name == "RagAskRequest"
        )
        request_fields = {field.name for field in request.field}
        self.assertNotIn("corpus", request_fields)
        self.assertNotIn("profile", request_fields)
        self.assertNotIn("top_k", request_fields)

    def test_breaking_field_number_is_rejected(self) -> None:
        descriptor = descriptor_pb2.FileDescriptorSet.FromString(
            (ROOT / "contracts/proto/rag_v2.descriptor.pb").read_bytes()
        )
        request = next(
            item for item in descriptor.file[0].message_type if item.name == "RagAskRequest"
        )
        next(item for item in request.field if item.name == "question").number = 99

        with self.assertRaisesRegex(MODULE.RagV2ProtoGenerationError, "field name/number"):
            MODULE._validate_descriptor(
                descriptor.SerializeToString(deterministic=True)
            )


if __name__ == "__main__":
    unittest.main()
