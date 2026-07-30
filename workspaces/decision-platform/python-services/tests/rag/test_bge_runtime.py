from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from app.rag.bge_runtime import (
    BgeEncodedBatch,
    BgeOnnxEmbedder,
    BgeRuntimeError,
    BgeStaticTokenizer,
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
    def __init__(self, *, providers: tuple[str, ...] = ("CPUExecutionProvider",)) -> None:
        self._providers = providers
        self.last_inputs: dict[str, np.ndarray] | None = None

    def get_providers(self) -> list[str]:
        return list(self._providers)

    def get_inputs(self) -> list[Any]:
        return [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
        ]

    def get_outputs(self) -> list[Any]:
        return [SimpleNamespace(name="last_hidden_state")]

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
