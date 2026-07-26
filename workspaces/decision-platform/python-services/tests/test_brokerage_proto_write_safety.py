from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_GENERATOR_PATH = _REPO_ROOT / "contracts" / "generate_brokerage_proto.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_brokerage_proto_safety", _GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("brokerage proto generator module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_mode_rejects_symlink_output_without_clobbering_target() -> None:
    generator = _load_generator()
    with tempfile.TemporaryDirectory(prefix="s31-proto-symlink-") as raw_dir:
        root = Path(raw_dir)
        marker = root / "outside-marker.txt"
        marker.write_bytes(b"preserve-me")
        generated = root / "generated"
        generated.mkdir()
        output = generated / "brokerage_pb2.py"
        output.symlink_to(marker)

        with (
            patch.object(generator, "REPO_ROOT", root),
            patch.object(generator, "_run_protoc", return_value={output: b"generated-bytes"}),
        ):
            with pytest.raises(generator.ProtoGenerationError):
                generator.generate(check=False)

        assert marker.read_bytes() == b"preserve-me"
        assert output.is_symlink()


def test_write_mode_rejects_symlink_ancestor_without_writing_outside_checkout() -> None:
    generator = _load_generator()
    with tempfile.TemporaryDirectory(prefix="s31-proto-ancestor-") as raw_dir:
        root = Path(raw_dir)
        outside = root / "outside"
        outside.mkdir()
        linked_parent = root / "generated"
        linked_parent.symlink_to(outside, target_is_directory=True)
        output = linked_parent / "brokerage_pb2.py"

        with (
            patch.object(generator, "REPO_ROOT", root),
            patch.object(generator, "_run_protoc", return_value={output: b"generated-bytes"}),
        ):
            with pytest.raises(generator.ProtoGenerationError):
                generator.generate(check=False)

        assert not (outside / "brokerage_pb2.py").exists()


def test_check_mode_rejects_symlink_even_when_target_bytes_match() -> None:
    generator = _load_generator()
    with tempfile.TemporaryDirectory(prefix="s31-proto-check-symlink-") as raw_dir:
        root = Path(raw_dir)
        marker = root / "outside-marker.txt"
        marker.write_bytes(b"expected")
        output = root / "brokerage.descriptor.pb"
        output.symlink_to(marker)

        with (
            patch.object(generator, "REPO_ROOT", root),
            patch.object(generator, "_run_protoc", return_value={output: b"expected"}),
        ):
            with pytest.raises(generator.ProtoGenerationError):
                generator.generate(check=True)

        assert marker.read_bytes() == b"expected"
