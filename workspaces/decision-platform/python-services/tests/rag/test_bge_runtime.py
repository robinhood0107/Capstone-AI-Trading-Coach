from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Buffer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from app.rag import bge_runtime
from app.rag.bge_artifact import BgeArtifactFile, BgeArtifactSpec
from app.rag.bge_runtime import (
    BgeEncodedBatch,
    BgeOnnxEmbedder,
    BgeRuntimeError,
    BgeStaticTokenizer,
    load_bge_onnx_embedder,
    load_model_dimension,
    validate_embedding_batch,
)


def test_static_tokenizer_loads_only_the_hash_pinned_local_json(
    posix_tmp_path: Path,
) -> None:
    tokenizer_path = posix_tmp_path / "tokenizer.json"
    tokenizer = Tokenizer(
        WordLevel(
            {
                "[UNK]": 0,
                "삼성전자": 1,
                "005930": 2,
                "TR_ID": 3,
                "FHKST01010100": 4,
            },
            unk_token="[UNK]",
        )
    )
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(tokenizer_path))
    tokenizer_path.chmod(0o600)
    expected_sha256 = hashlib.sha256(tokenizer_path.read_bytes()).hexdigest()

    loaded = BgeStaticTokenizer.from_file(
        tokenizer_path,
        expected_sha256=expected_sha256,
    )

    assert loaded.count_tokens("삼성전자 005930 TR_ID FHKST01010100") == 4
    assert loaded.take_prefix("삼성전자 005930 TR_ID FHKST01010100", 2) == "삼성전자 005930"
    assert loaded.take_suffix("삼성전자 005930 TR_ID FHKST01010100", 2) == "TR_ID FHKST01010100"

    with pytest.raises(BgeRuntimeError, match="TOKENIZER_SHA256_MISMATCH"):
        BgeStaticTokenizer.from_file(tokenizer_path, expected_sha256="0" * 64)


def test_static_tokenizer_rejects_unknown_top_level_json_field(
    posix_tmp_path: Path,
) -> None:
    tokenizer_path = posix_tmp_path / "tokenizer.json"
    tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(tokenizer_path))
    payload = json.loads(tokenizer_path.read_text(encoding="utf-8"))
    payload["unexpectedRemoteLoader"] = "forbidden"
    tokenizer_path.write_text(json.dumps(payload), encoding="utf-8")
    tokenizer_path.chmod(0o600)

    with pytest.raises(BgeRuntimeError, match="TOKENIZER_JSON_CONTRACT"):
        BgeStaticTokenizer.from_file(
            tokenizer_path,
            expected_sha256=hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
        )


def test_model_config_pins_float32_xlm_roberta_1024_dimension(
    posix_tmp_path: Path,
) -> None:
    config_path = posix_tmp_path / "config.json"
    payload = {
        "architectures": ["XLMRobertaModel"],
        "hidden_size": 1024,
        "model_type": "xlm-roberta",
        "torch_dtype": "float32",
        "vocab_size": 250002,
    }
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    config_path.chmod(0o600)
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()

    assert (
        load_model_dimension(
            config_path,
            expected_sha256=digest,
            expected_size=config_path.stat().st_size,
        )
        == 1024
    )

    payload["hidden_size"] = 768
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BgeRuntimeError, match="BGE_MODEL_CONFIG_CONTRACT"):
        load_model_dimension(
            config_path,
            expected_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
            expected_size=config_path.stat().st_size,
        )


@pytest.mark.parametrize(
    "embedding",
    [
        np.zeros((1, 1024), dtype=np.float32),
        np.full((1, 1024), np.nan, dtype=np.float32),
        np.full((1, 1024), np.inf, dtype=np.float32),
        np.ones((1, 768), dtype=np.float32),
        np.ones((1, 1024), dtype=np.float64),
    ],
)
def test_embedding_validator_rejects_zero_nonfinite_wrong_dimension_and_dtype(
    embedding: np.ndarray,
) -> None:
    with pytest.raises(BgeRuntimeError):
        validate_embedding_batch(embedding, expected_rows=1)


def test_embedding_validator_requires_finite_l2_normalized_float32() -> None:
    embedding = np.zeros((2, 1024), dtype=np.float32)
    embedding[:, 0] = 1.0

    validated = validate_embedding_batch(embedding, expected_rows=2)

    assert validated.dtype == np.float32
    assert validated.shape == (2, 1024)
    assert np.allclose(np.linalg.norm(validated, axis=1), 1.0, atol=1e-6)


def test_query_embedding_has_no_neighbor_context_and_uses_cpu_only() -> None:
    tokenizer = _RecordingTokenizer()
    session = _FakeSession()
    embedder = BgeOnnxEmbedder(
        tokenizer=tokenizer,
        session=session,
        output_mode="LAST_HIDDEN_STATE_CLS",
    )

    result = embedder.embed_query("금 ETF 롤오버 위험")

    assert tokenizer.seen_texts == ("금 ETF 롤오버 위험",)
    assert session.last_inputs is not None
    assert result.shape == (1024,)
    assert result.dtype == np.float32
    assert result[0] == pytest.approx(1.0)


def test_runtime_supplies_zero_token_type_ids_when_graph_requires_them() -> None:
    session = _FakeSession(requires_token_type_ids=True)
    embedder = BgeOnnxEmbedder(
        tokenizer=_RecordingTokenizer(),
        session=session,
        output_mode="LAST_HIDDEN_STATE_CLS",
    )

    embedder.embed_query("token type fallback")

    assert session.last_inputs is not None
    assert "token_type_ids" in session.last_inputs
    assert np.count_nonzero(session.last_inputs["token_type_ids"]) == 0


def test_runtime_rejects_non_cpu_provider_and_unknown_output_contract() -> None:
    with pytest.raises(BgeRuntimeError, match="CPUExecutionProvider"):
        BgeOnnxEmbedder(
            tokenizer=_RecordingTokenizer(),
            session=_FakeSession(providers=("CUDAExecutionProvider",)),
            output_mode="LAST_HIDDEN_STATE_CLS",
        )

    with pytest.raises(BgeRuntimeError, match="OUTPUT_MODE"):
        BgeOnnxEmbedder(
            tokenizer=_RecordingTokenizer(),
            session=_FakeSession(),
            output_mode="UNKNOWN",
        )


@pytest.mark.parametrize(
    "replaced_relative_path",
    ("onnx/model.onnx", "onnx/model.onnx_data"),
)
def test_onnx_loader_rejects_same_size_replacement_after_packet_verification(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replaced_relative_path: str,
) -> None:
    packet_root, spec, payloads = _tiny_runtime_packet(posix_tmp_path)
    session_calls: list[bytes | str] = []

    def verify_then_replace(_packet_root: Path) -> None:
        target = packet_root / replaced_relative_path
        target.write_bytes(b"x" * len(payloads[replaced_relative_path]))
        target.chmod(0o600)

    _install_fake_onnx_loader(
        monkeypatch,
        spec=spec,
        external_locations=("Constant_7_attr__value", "model.onnx_data"),
        verify=verify_then_replace,
        session_factory=lambda path_or_bytes, **_kwargs: session_calls.append(path_or_bytes),
    )

    with pytest.raises(BgeRuntimeError, match="BGE_ONNX_SNAPSHOT_FAILED"):
        load_bge_onnx_embedder(packet_root)

    assert session_calls == []


def test_onnx_loader_passes_verified_graph_and_external_files_from_memory(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_root, spec, payloads = _tiny_runtime_packet(posix_tmp_path)
    session_options_seen: list[_FakeOrtSessionOptions] = []

    def session_factory(
        path_or_bytes: bytes | str,
        *,
        sess_options: _FakeOrtSessionOptions,
        providers: list[str],
    ) -> _FakeSession:
        # native session 생성 중 원래 경로가 바뀌어도 sink에는 승인 bytes snapshot만 전달돼야 한다.
        for relative_path in (
            "onnx/model.onnx",
            "onnx/model.onnx_data",
            "onnx/Constant_7_attr__value",
        ):
            target = packet_root / relative_path
            target.write_bytes(b"x" * len(payloads[relative_path]))
            target.chmod(0o600)
        assert path_or_bytes == payloads["onnx/model.onnx"]
        assert providers == ["CPUExecutionProvider"]
        assert sess_options.external_files == {
            "Constant_7_attr__value": payloads["onnx/Constant_7_attr__value"],
            "model.onnx_data": payloads["onnx/model.onnx_data"],
        }
        session_options_seen.append(sess_options)
        return _FakeSession(
            output_names=("sentence_embedding",),
            output_shape=("batch", 1024),
        )

    _install_fake_onnx_loader(
        monkeypatch,
        spec=spec,
        external_locations=("Constant_7_attr__value", "model.onnx_data"),
        verify=lambda _packet_root: None,
        session_factory=session_factory,
    )

    embedder = load_bge_onnx_embedder(packet_root)

    assert isinstance(embedder, BgeOnnxEmbedder)
    assert len(session_options_seen) == 1


def test_onnx_loader_rejects_read_only_file_below_runtime_owned_writable_directory(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet_root, spec, _payloads = _tiny_runtime_packet(posix_tmp_path)
    (packet_root / "onnx/model.onnx_data").chmod(0o400)
    session_calls: list[bytes | str] = []
    _install_fake_onnx_loader(
        monkeypatch,
        spec=spec,
        external_locations=("Constant_7_attr__value", "model.onnx_data"),
        verify=lambda _packet_root: None,
        session_factory=lambda path_or_bytes, **_kwargs: session_calls.append(path_or_bytes),
    )

    with pytest.raises(BgeRuntimeError, match="BGE_ONNX_SNAPSHOT_FAILED"):
        load_bge_onnx_embedder(packet_root)

    assert session_calls == []


class _RecordingTokenizer:
    def __init__(self) -> None:
        self.seen_texts: tuple[str, ...] = ()

    def encode_batch(self, texts: tuple[str, ...]) -> BgeEncodedBatch:
        self.seen_texts = texts
        rows = len(texts)
        return BgeEncodedBatch(
            input_ids=np.ones((rows, 3), dtype=np.int64),
            attention_mask=np.ones((rows, 3), dtype=np.int64),
            token_type_ids=None,
        )


class _FakeSession:
    def __init__(
        self,
        *,
        providers: tuple[str, ...] = ("CPUExecutionProvider",),
        requires_token_type_ids: bool = False,
        output_names: tuple[str, ...] = ("last_hidden_state",),
        output_shape: tuple[str | int, ...] = ("batch", "sequence", 1024),
    ) -> None:
        self._providers = providers
        self._requires_token_type_ids = requires_token_type_ids
        self._output_names = output_names
        self._output_shape = output_shape
        self.last_inputs: dict[str, np.ndarray] | None = None

    def get_providers(self) -> list[str]:
        return list(self._providers)

    def get_inputs(self) -> list[Any]:
        inputs = [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
        ]
        if self._requires_token_type_ids:
            inputs.append(SimpleNamespace(name="token_type_ids"))
        return inputs

    def get_outputs(self) -> list[Any]:
        return [
            SimpleNamespace(
                name=name,
                shape=list(self._output_shape),
                type="tensor(float)",
            )
            for name in self._output_names
        ]

    def run(
        self,
        output_names: list[str] | None,
        inputs: dict[str, np.ndarray],
    ) -> list[np.ndarray]:
        del output_names
        self.last_inputs = inputs
        hidden = np.zeros((inputs["input_ids"].shape[0], 3, 1024), dtype=np.float32)
        hidden[:, 0, 0] = 1.0
        return [hidden]


class _FakeOrtSessionOptions:
    def __init__(self) -> None:
        self.intra_op_num_threads = 0
        self.inter_op_num_threads = 0
        self.execution_mode: object | None = None
        self.graph_optimization_level: object | None = None
        self.enable_mem_pattern = True
        self.external_files: dict[str, bytes] = {}

    def add_external_initializers_from_files_in_memory(
        self,
        names: tuple[str, ...],
        buffers: tuple[Buffer, ...],
        lengths: tuple[int, ...],
    ) -> None:
        self.external_files = {
            name: bytes(memoryview(buffer)[:length])
            for name, buffer, length in zip(names, buffers, lengths, strict=True)
        }


def _tiny_runtime_packet(
    parent: Path,
) -> tuple[Path, BgeArtifactSpec, dict[str, bytes]]:
    packet_root = parent / "packet"
    onnx_root = packet_root / "onnx"
    onnx_root.mkdir(parents=True)
    packet_root.chmod(0o700)
    onnx_root.chmod(0o700)
    payloads = {
        "onnx/model.onnx": b"approved-model",
        "onnx/model.onnx_data": b"approved-weights",
        "onnx/Constant_7_attr__value": b"approved-constant",
        "onnx/config.json": b"{}",
    }
    for relative_path, payload in payloads.items():
        path = packet_root / relative_path
        path.write_bytes(payload)
        path.chmod(0o600)
    spec = BgeArtifactSpec(
        repository="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        license_id="MIT",
        artifact_type="ONNX_DATA_ONLY",
        files=tuple(
            BgeArtifactFile(
                relative_path=relative_path,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for relative_path, payload in payloads.items()
        ),
    )
    return packet_root, spec, payloads


def _install_fake_onnx_loader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    spec: BgeArtifactSpec,
    external_locations: tuple[str, ...],
    verify: Any,
    session_factory: Any,
) -> None:
    graph_contract = SimpleNamespace(
        external_data_locations=external_locations,
        input_names=("input_ids", "attention_mask"),
        output_names=("sentence_embedding",),
        output_dtype="float32",
        output_dimension=1024,
    )
    fake_ort = SimpleNamespace(
        SessionOptions=_FakeOrtSessionOptions,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=object()),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_BASIC=object()),
        InferenceSession=session_factory,
    )
    monkeypatch.setattr(bge_runtime, "APPROVED_BGE_ARTIFACT_SPEC", spec)
    monkeypatch.setattr(bge_runtime, "verify_bge_packet", verify)
    monkeypatch.setattr(
        bge_runtime,
        "inspect_onnx_graph_contract",
        lambda *_args, **_kwargs: graph_contract,
    )
    monkeypatch.setattr(bge_runtime, "load_pooling_mode", lambda _packet_root: "CLS")
    monkeypatch.setattr(
        bge_runtime,
        "load_model_dimension",
        lambda *_args, **_kwargs: 1024,
    )
    monkeypatch.setattr(
        bge_runtime,
        "BgeStaticTokenizer",
        SimpleNamespace(from_file=lambda _path: _RecordingTokenizer()),
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
