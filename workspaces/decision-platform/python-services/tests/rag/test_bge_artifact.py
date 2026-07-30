from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app.rag.bge_artifact import (
    APPROVED_BGE_ARTIFACT_SPEC,
    BgeArtifactError,
    BgeArtifactFile,
    BgeArtifactSpec,
    OnnxGraphContract,
    validate_download_redirect,
    validate_onnx_graph_contract,
    verify_bge_packet,
)


def test_approved_bge_packet_is_exactly_pinned_to_ten_files() -> None:
    spec = APPROVED_BGE_ARTIFACT_SPEC

    assert spec.repository == "BAAI/bge-m3"
    assert spec.revision == "5617a9f61b028005a4858fdac845db406aefb181"
    assert spec.license_id == "MIT"
    assert spec.artifact_type == "ONNX_DATA_ONLY"
    assert len(spec.files) == 10
    assert sum(item.size_bytes for item in spec.files) == 2_289_781_803
    assert {item.relative_path for item in spec.files} == {
        "onnx/model.onnx",
        "onnx/model.onnx_data",
        "onnx/Constant_7_attr__value",
        "onnx/config.json",
        "onnx/tokenizer.json",
        "onnx/tokenizer_config.json",
        "onnx/special_tokens_map.json",
        "onnx/sentencepiece.bpe.model",
        "1_Pooling/config.json",
        "README.md",
    }


def test_packet_verifier_requires_exact_hash_size_mode_and_membership(
    posix_tmp_path: Path,
) -> None:
    spec = _tiny_spec()
    packet_root = _write_tiny_packet(posix_tmp_path, spec)

    verified = verify_bge_packet(packet_root, spec=spec)

    assert verified.file_count == 2
    assert verified.total_bytes == 11
    assert len(verified.file_manifest_sha256) == 64

    forbidden = packet_root / "pytorch_model.bin"
    forbidden.write_bytes(b"pickle-like payload")
    forbidden.chmod(0o600)
    with pytest.raises(BgeArtifactError, match="UNEXPECTED_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)
    forbidden.unlink()

    model_path = packet_root / "onnx/model.onnx"
    model_path.chmod(0o644)
    with pytest.raises(BgeArtifactError, match="MODE_MISMATCH"):
        verify_bge_packet(packet_root, spec=spec)


def test_packet_verifier_rejects_symlink_hardlink_hash_and_path_escape(
    posix_tmp_path: Path,
) -> None:
    spec = _tiny_spec()
    packet_root = _write_tiny_packet(posix_tmp_path, spec)
    model_path = packet_root / "onnx/model.onnx"

    model_path.unlink()
    model_path.symlink_to(posix_tmp_path / "outside.onnx")
    with pytest.raises(BgeArtifactError, match="NON_REGULAR_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)

    model_path.unlink()
    model_path.write_bytes(b"onnx")
    model_path.chmod(0o600)
    hardlink = posix_tmp_path / "outside-hardlink.onnx"
    os.link(model_path, hardlink)
    with pytest.raises(BgeArtifactError, match="HARDLINK_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)
    hardlink.unlink()

    model_path.write_bytes(b"drift")
    with pytest.raises(BgeArtifactError, match="SIZE_MISMATCH|SHA256_MISMATCH"):
        verify_bge_packet(packet_root, spec=spec)

    escaped_spec = replace(
        spec,
        files=(
            BgeArtifactFile(
                relative_path="../outside",
                size_bytes=1,
                sha256="0" * 64,
            ),
        ),
    )
    with pytest.raises(BgeArtifactError, match="UNSAFE_ARTIFACT_PATH"):
        verify_bge_packet(packet_root, spec=escaped_spec)


@pytest.mark.parametrize(
    "location",
    [
        "http://cas-bridge.xethub.hf.co/xet-bridge-us/object",
        "https://user@cas-bridge.xethub.hf.co/xet-bridge-us/object",
        "https://127.0.0.1/object",
        "https://example.com/object",
        "https://cas-bridge.xethub.hf.co/../private",
    ],
)
def test_download_redirect_rejects_unsafe_or_unapproved_targets(location: str) -> None:
    with pytest.raises(BgeArtifactError, match="UNSAFE_DOWNLOAD_REDIRECT"):
        validate_download_redirect(location)


def test_download_redirect_accepts_only_the_bounded_hugging_face_cas_hosts() -> None:
    assert (
        validate_download_redirect(
            "https://cas-bridge.xethub.hf.co/xet-bridge-us/approved-object?X-Amz-Signature=abc"
        ).hostname
        == "cas-bridge.xethub.hf.co"
    )
    assert (
        validate_download_redirect(
            "https://cdn-lfs.huggingface.co/repos/approved-object?signature=abc"
        ).hostname
        == "cdn-lfs.huggingface.co"
    )


def test_onnx_graph_contract_rejects_external_path_and_custom_domain() -> None:
    valid = OnnxGraphContract(
        external_data_locations=("Constant_7_attr__value", "model.onnx_data"),
        node_domains=("",),
        input_names=("input_ids", "attention_mask"),
        output_names=("last_hidden_state",),
        output_dtype="float32",
        output_dimension=1024,
        dynamic_batch=True,
        dynamic_sequence=True,
    )

    validate_onnx_graph_contract(valid)

    for invalid in (
        replace(valid, external_data_locations=("../model.onnx_data",)),
        replace(valid, external_data_locations=("https://example.com/model.onnx_data",)),
        replace(valid, external_data_locations=("/tmp/model.onnx_data",)),
        replace(valid, external_data_locations=("model.onnx_data", "model.onnx_data")),
        replace(valid, node_domains=("", "com.example.custom")),
        replace(valid, output_dimension=768),
        replace(valid, output_dtype="float64"),
    ):
        with pytest.raises(BgeArtifactError):
            validate_onnx_graph_contract(invalid)


def _tiny_spec() -> BgeArtifactSpec:
    files = (
        BgeArtifactFile(
            relative_path="onnx/model.onnx",
            size_bytes=4,
            sha256=hashlib.sha256(b"onnx").hexdigest(),
        ),
        BgeArtifactFile(
            relative_path="onnx/model.onnx_data",
            size_bytes=7,
            sha256=hashlib.sha256(b"weights").hexdigest(),
        ),
    )
    return BgeArtifactSpec(
        repository="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        license_id="MIT",
        artifact_type="ONNX_DATA_ONLY",
        files=files,
    )


def _write_tiny_packet(tmp_path: Path, spec: BgeArtifactSpec) -> Path:
    packet_root = tmp_path / "packet"
    packet_root.mkdir(mode=0o700)
    for item, content in zip(spec.files, (b"onnx", b"weights"), strict=True):
        path = packet_root / item.relative_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)
    return packet_root
