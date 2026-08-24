from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.rag.pre_s5_voyage_tokenizer import (
    LocalPreS5VoyageContext4Tokenizer,
    PreS5VoyageTokenizerError,
)


def test_local_voyage_tokenizer_requires_hash_pinned_0700_0600_artifact_and_counts_without_network(
    tmp_path: Path,
) -> None:
    raw = _write_tokenizer(tmp_path)
    tokenizer = LocalPreS5VoyageContext4Tokenizer.from_local_root(
        local_root=tmp_path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert tokenizer.model == "voyage-context-4"
    assert tokenizer.tokenizer_sha256 == hashlib.sha256(raw).hexdigest()
    assert tokenizer.count_texts(texts=("alpha beta", "alpha"), token_cap=8) == 3


def test_local_voyage_tokenizer_rejects_packet_hash_or_permissions_before_a_count(
    tmp_path: Path,
) -> None:
    raw = _write_tokenizer(tmp_path)
    with pytest.raises(PreS5VoyageTokenizerError, match="PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_SHA256"):
        LocalPreS5VoyageContext4Tokenizer.from_local_root(
            local_root=tmp_path,
            expected_sha256="0" * 64,
        )

    tokenizer_path = tmp_path / "artifacts" / "voyage-context-4" / "tokenizer.json"
    os.chmod(tokenizer_path, 0o644)
    with pytest.raises(
        PreS5VoyageTokenizerError, match="PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_BOUNDARY"
    ):
        LocalPreS5VoyageContext4Tokenizer.from_local_root(
            local_root=tmp_path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_local_voyage_tokenizer_rejects_token_cap_without_using_a_byte_approximation(
    tmp_path: Path,
) -> None:
    raw = _write_tokenizer(tmp_path)
    tokenizer = LocalPreS5VoyageContext4Tokenizer.from_local_root(
        local_root=tmp_path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )

    # 10 ASCII bytes are not a token-count authorization. The local official tokenizer produces 3 tokens.
    with pytest.raises(PreS5VoyageTokenizerError, match="PRE_S5_VOYAGE_OFFICIAL_TOKENIZER_CAP"):
        tokenizer.count_texts(texts=("alpha beta alpha",), token_cap=2)


def _write_tokenizer(local_root: Path) -> bytes:
    """Create a minimal local tokenizer.json fixture under the same permission boundary as production."""

    os.chmod(local_root, 0o700)
    artifact_root = local_root / "artifacts"
    artifact_root.mkdir(mode=0o700)
    model_root = artifact_root / "voyage-context-4"
    model_root.mkdir(mode=0o700)
    raw = json.dumps(
        {
            "added_tokens": [],
            "decoder": None,
            "model": {
                "type": "WordLevel",
                "unk_token": "[UNK]",
                "vocab": {"[UNK]": 0, "alpha": 1, "beta": 2},
            },
            "normalizer": None,
            "padding": None,
            "post_processor": None,
            "pre_tokenizer": {"type": "Whitespace"},
            "truncation": None,
            "version": "1.0",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    tokenizer_path = model_root / "tokenizer.json"
    tokenizer_path.write_bytes(raw)
    os.chmod(tokenizer_path, 0o600)
    return raw
