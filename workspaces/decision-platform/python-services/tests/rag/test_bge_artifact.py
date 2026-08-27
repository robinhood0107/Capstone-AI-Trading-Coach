from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from app.rag.bge_artifact import (
    APPROVED_BGE_ARTIFACT_SPEC,
    BgeArtifactError,
    BgeArtifactFile,
    BgeArtifactSpec,
    OnnxGraphContract,
    inspect_onnx_graph_contract,
    validate_download_redirect,
    validate_onnx_graph_contract,
    verify_bge_packet,
)

_TOKENIZER_SHA256 = "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
_CORE6_HISTORICAL_FIXTURE_SHA256 = frozenset(
    {
        "b913ab4231d917a9a28a7213fcf66257f8f8d79ea092356d506daeeaddabf36e",
        "3651313ea6ae0ee2f1e7af839e0f2a4aa96672a9e15b412cb3d2914f4bd55977",
        "31139997140feb5fb61c0924950da502f0f6c1a347e3cc8e764f0d8d25d1bdfb",
        "610b231832ed334bf541e4581066f3a0644a5c2750cc62c1d37e2564c4034b26",
        "697855056eac950ae6ff755be604015aa20b627ab75369d657921ab72f802ce7",
        "9aef7a439b432717704b6097a29d39e7007ff73b0809392643a30a7ed4e04f3d",
    }
)
_B86_PUBLIC_SOURCE_CARD_SHA256 = frozenset(
    {
        "4281e92a878cdf08ab9cd3d52cfd4a564fef3509e85f689f03922464380d98cc",
        "3137be113762703bbf5632ee8bdc317c182391ed04476a12a5b7b93d481db952",
    }
)
_UNAPPROVED_SECRET_LIKE_SHA256 = "f" * 64
_COLLECTOR_DOC_FALSE_POSITIVE_COMMIT = "0c5b64823a9a30c733817a861d34bb980234fac2"
_TEAM_B_HANDOFF_FALSE_POSITIVE_COMMIT = "2c604e750c3da25f5ab7184991b6a6cd480e1f1a"


def test_runtime_sbom_binds_the_current_production_lockfile() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    lockfile = repo_root / "workspaces/decision-platform/python-services/uv.lock"
    sbom_path = repo_root / "huggingface_model/manifests/bge-m3-onnx-runtime-sbom.v1.json"

    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))

    assert sbom["lockfileSha256"] == hashlib.sha256(lockfile.read_bytes()).hexdigest()


def test_public_digest_allowlist_keeps_only_the_exact_approved_values() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    with (repo_root / ".gitleaks.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    allowlists = config["allowlists"]
    expected_digests = _CORE6_HISTORICAL_FIXTURE_SHA256 | {_TOKENIZER_SHA256}
    expected_regexes = {f"^{digest}$" for digest in expected_digests}
    b86_expected_regexes = {f"^{digest}$" for digest in _B86_PUBLIC_SOURCE_CARD_SHA256}

    assert config["extend"]["useDefault"] is True
    assert len(allowlists) == 6
    digest_allowlists = allowlists[:2]
    assert all(set(allowlist) == {"description", "regexes"} for allowlist in digest_allowlists)
    assert all("regexTarget" not in allowlist for allowlist in digest_allowlists)
    assert set(digest_allowlists[0]["regexes"]) == expected_regexes
    assert set(digest_allowlists[1]["regexes"]) == b86_expected_regexes
    assert all(
        re.fullmatch(r"\^[0-9a-f]{64}\$", pattern)
        for allowlist in digest_allowlists
        for pattern in allowlist["regexes"]
    )
    assert all(
        re.fullmatch(pattern, _UNAPPROVED_SECRET_LIKE_SHA256) is None
        for allowlist in digest_allowlists
        for pattern in allowlist["regexes"]
    )
    collector_doc_allowlist = allowlists[4]
    assert collector_doc_allowlist == {
        "description": "Historical P1 collector documentation phrase misclassified as a generic API key",
        "condition": "AND",
        "targetRules": ["generic-api-key"],
        "commits": [_COLLECTOR_DOC_FALSE_POSITIVE_COMMIT],
        "paths": ["^docs/decision-platform/P1_DATA_ONLY_DAILY_COLLECTOR_운영_가이드[.]md$"],
        "regexTarget": "secret",
        "regexes": ["^Signal/Risk/order$"],
    }
    team_b_handoff_allowlist = allowlists[5]
    assert team_b_handoff_allowlist == {
        "description": "Historical Team B handoff phrase misclassified as a generic API key",
        "condition": "AND",
        "targetRules": ["generic-api-key"],
        "commits": [_TEAM_B_HANDOFF_FALSE_POSITIVE_COMMIT],
        "paths": ["^docs/decision-platform/P1_TEAM_B_RETURN_ENGINE_완료_요청서[.]md$"],
        "regexTarget": "secret",
        "regexes": ["^OCI/SBOM/provenance/signature$"],
    }


def test_b86_public_digest_allowlist_is_copied_from_historical_source_cards() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    migration_root = (
        repo_root
        / "workspaces"
        / "decision-platform"
        / "spring-api"
        / "src"
        / "main"
        / "resources"
        / "db"
        / "migration"
    )
    baseline = (migration_root.parent / "baseline" / "B86__p1_offline_demo_baseline.sql").read_text(
        encoding="utf-8"
    )
    historical = "\n".join(
        (migration_root / filename).read_text(encoding="utf-8")
        for filename in (
            "V36__s4_7d_public_bge_staging_writer.sql",
            "V37__s4_7d_external_exact30_voyage_staging_writer.sql",
        )
    )

    for digest in _B86_PUBLIC_SOURCE_CARD_SHA256:
        assert historical.count(digest) == 1
        assert baseline.count(digest) == 1
        assert "src_project_kis_discovery_write_boundary_001" in historical
        assert "src_project_kis_discovery_write_boundary_001" in baseline


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
    with pytest.raises(BgeArtifactError, match=r"UNEXPECTED_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)
    forbidden.unlink()

    model_path = packet_root / "onnx/model.onnx"
    model_path.chmod(0o644)
    with pytest.raises(BgeArtifactError, match=r"MODE_MISMATCH"):
        verify_bge_packet(packet_root, spec=spec)


def test_packet_verifier_rejects_symlink_hardlink_hash_and_path_escape(
    posix_tmp_path: Path,
) -> None:
    spec = _tiny_spec()
    packet_root = _write_tiny_packet(posix_tmp_path, spec)
    model_path = packet_root / "onnx/model.onnx"

    model_path.unlink()
    model_path.symlink_to(posix_tmp_path / "outside.onnx")
    with pytest.raises(BgeArtifactError, match=r"NON_REGULAR_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)

    model_path.unlink()
    model_path.write_bytes(b"onnx")
    model_path.chmod(0o600)
    hardlink = posix_tmp_path / "outside-hardlink.onnx"
    os.link(model_path, hardlink)
    with pytest.raises(BgeArtifactError, match=r"HARDLINK_ARTIFACT"):
        verify_bge_packet(packet_root, spec=spec)
    hardlink.unlink()

    model_path.write_bytes(b"drift")
    with pytest.raises(BgeArtifactError, match=r"SIZE_MISMATCH|SHA256_MISMATCH"):
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
    with pytest.raises(BgeArtifactError, match=r"UNSAFE_ARTIFACT_PATH"):
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
    with pytest.raises(BgeArtifactError, match=r"UNSAFE_DOWNLOAD_REDIRECT"):
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
    assert (
        validate_download_redirect(
            "https://us.aws.cdn.hf.co/repos/approved-object?signature=abc"
        ).hostname
        == "us.aws.cdn.hf.co"
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
        external_data_bytes=11,
    )

    validate_onnx_graph_contract(valid)
    validate_onnx_graph_contract(
        replace(
            valid,
            output_names=("token_embeddings", "sentence_embedding"),
            output_dimension=-1,
        )
    )

    for invalid in (
        replace(valid, external_data_locations=("../model.onnx_data",)),
        replace(valid, external_data_locations=("https://example.com/model.onnx_data",)),
        replace(valid, external_data_locations=("/tmp/model.onnx_data",)),
        replace(valid, external_data_locations=("model.onnx_data", "model.onnx_data")),
        replace(valid, node_domains=("", "com.example.custom")),
        replace(valid, output_dimension=768),
        replace(valid, output_dimension=-1),
        replace(valid, output_dtype="float64"),
    ):
        with pytest.raises(BgeArtifactError):
            validate_onnx_graph_contract(invalid)


def test_onnx_protobuf_is_inspected_before_runtime_session(posix_tmp_path: Path) -> None:
    model_path = posix_tmp_path / "model.onnx"
    model_bytes = _minimal_onnx_model()
    model_path.write_bytes(model_bytes)
    model_path.chmod(0o600)

    contract = inspect_onnx_graph_contract(
        model_path,
        expected_sha256=hashlib.sha256(model_bytes).hexdigest(),
        external_file_sizes={
            "Constant_7_attr__value": 4,
            "model.onnx_data": 7,
        },
    )

    assert contract.external_data_locations == (
        "Constant_7_attr__value",
        "model.onnx_data",
    )
    assert contract.external_data_bytes == 11
    assert contract.node_domains == ("",)
    assert contract.input_names == ("input_ids", "attention_mask")
    assert contract.output_names == ("last_hidden_state",)
    assert contract.output_dtype == "float32"
    assert contract.output_dimension == 1024
    assert contract.dynamic_batch is True
    assert contract.dynamic_sequence is True
    validate_onnx_graph_contract(contract)


@pytest.mark.parametrize(
    ("location", "domain"),
    [
        ("../model.onnx_data", ""),
        ("model.onnx_data", "com.example.custom"),
    ],
)
def test_onnx_protobuf_inspector_rejects_external_traversal_and_custom_domain(
    posix_tmp_path: Path,
    location: str,
    domain: str,
) -> None:
    model_path = posix_tmp_path / "model.onnx"
    model_bytes = _minimal_onnx_model(location=location, domain=domain)
    model_path.write_bytes(model_bytes)
    model_path.chmod(0o600)

    with pytest.raises(BgeArtifactError):
        contract = inspect_onnx_graph_contract(
            model_path,
            expected_sha256=hashlib.sha256(model_bytes).hexdigest(),
            external_file_sizes={
                "Constant_7_attr__value": 4,
                "model.onnx_data": 7,
            },
        )
        validate_onnx_graph_contract(contract)


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


def _minimal_onnx_model(
    *,
    location: str = "model.onnx_data",
    domain: str = "",
) -> bytes:
    node = _bytes_field(4, b"Identity")
    if domain:
        node += _bytes_field(7, domain.encode("utf-8"))
    initializers = (
        _external_tensor("constant", "Constant_7_attr__value", length=4),
        _external_tensor("weights", location, length=7),
    )
    graph = _bytes_field(1, node)
    graph += b"".join(_bytes_field(5, tensor) for tensor in initializers)
    graph += _bytes_field(11, _value_info("input_ids", 7, ("batch", "sequence")))
    graph += _bytes_field(11, _value_info("attention_mask", 7, ("batch", "sequence")))
    graph += _bytes_field(
        12,
        _value_info("last_hidden_state", 1, ("batch", "sequence", 1024)),
    )
    return _bytes_field(7, graph)


def _external_tensor(name: str, location: str, *, length: int) -> bytes:
    tensor = _bytes_field(8, name.encode("utf-8"))
    tensor += _bytes_field(13, _string_entry("location", location))
    tensor += _bytes_field(13, _string_entry("offset", "0"))
    tensor += _bytes_field(13, _string_entry("length", str(length)))
    tensor += _varint_field(14, 1)
    return tensor


def _string_entry(key: str, value: str) -> bytes:
    return _bytes_field(1, key.encode("utf-8")) + _bytes_field(2, value.encode("utf-8"))


def _value_info(name: str, element_type: int, dimensions: tuple[str | int, ...]) -> bytes:
    shape = b""
    for dimension in dimensions:
        encoded = (
            _varint_field(1, dimension)
            if isinstance(dimension, int)
            else _bytes_field(2, dimension.encode("utf-8"))
        )
        shape += _bytes_field(1, encoded)
    tensor_type = _varint_field(1, element_type) + _bytes_field(2, shape)
    type_proto = _bytes_field(1, tensor_type)
    return _bytes_field(1, name.encode("utf-8")) + _bytes_field(2, type_proto)


def _bytes_field(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def _varint_field(field_number: int, value: int) -> bytes:
    return _varint(field_number << 3) + _varint(value)


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)
