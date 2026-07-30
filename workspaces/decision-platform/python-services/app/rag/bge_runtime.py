from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from tokenizers import Tokenizer

from app.rag.bge_artifact import (
    APPROVED_BGE_ARTIFACT_SPEC,
    BgeArtifactError,
    inspect_onnx_graph_contract,
    verify_bge_packet,
)

_TOKENIZER_SHA256 = "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
_MAX_TOKENIZER_BYTES = 20 * 1024 * 1024
_MAX_TOKENIZER_JSON_DEPTH = 32
_MAX_TOKENIZER_JSON_NODES = 1_000_000
_MAX_TOKENIZER_JSON_STRING_BYTES = 512 * 1024
_TOKENIZER_TOP_LEVEL_FIELDS = frozenset(
    {
        "added_tokens",
        "decoder",
        "model",
        "normalizer",
        "padding",
        "post_processor",
        "pre_tokenizer",
        "truncation",
        "version",
    }
)
_OUTPUT_MODES = frozenset({"LAST_HIDDEN_STATE_CLS", "POOLED_OUTPUT"})
OutputMode = Literal["LAST_HIDDEN_STATE_CLS", "POOLED_OUTPUT"]


class BgeRuntimeError(ValueError):
    """BGE tokenizer/ONNX runtime 계약 위반을 payload나 원문 없이 보고한다."""


@dataclass(frozen=True)
class BgeEncodedBatch:
    """ONNX input name에 매핑하기 전의 bounded integer tensor 묶음."""

    input_ids: NDArray[np.int64]
    attention_mask: NDArray[np.int64]
    token_type_ids: NDArray[np.int64] | None


class BgeTokenizerPort(Protocol):
    def encode_batch(self, texts: tuple[str, ...]) -> BgeEncodedBatch:
        """canonical 또는 query text를 ONNX 정수 tensor로 변환한다."""


class BgeOnnxSessionPort(Protocol):
    def get_providers(self) -> list[str]:
        """이 session에 실제 등록된 execution provider를 반환한다."""

    def get_inputs(self) -> list[Any]:
        """ONNX input metadata를 반환한다."""

    def get_outputs(self) -> list[Any]:
        """ONNX output metadata를 반환한다."""

    def run(
        self,
        output_names: list[str] | None,
        inputs: dict[str, NDArray[np.generic]],
    ) -> list[Any]:
        """network 없는 CPU inference를 실행한다."""


class BgeStaticTokenizer:
    """hash-pinned local tokenizer.json만 읽는 offset/encoding adapter."""

    def __init__(self, *, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer

    @classmethod
    def from_file(
        cls,
        tokenizer_path: Path,
        *,
        expected_sha256: str = _TOKENIZER_SHA256,
    ) -> BgeStaticTokenizer:
        """regular 0600 local JSON의 hash를 확인한 뒤 메모리 문자열에서 tokenizer를 연다."""

        try:
            path_stat = tokenizer_path.lstat()
        except FileNotFoundError as error:
            raise BgeRuntimeError("TOKENIZER_NOT_FOUND") from error
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or path_stat.st_nlink != 1
            or stat.S_IMODE(path_stat.st_mode) != 0o600
            or path_stat.st_size <= 0
            or path_stat.st_size > _MAX_TOKENIZER_BYTES
        ):
            raise BgeRuntimeError("TOKENIZER_FILE_BOUNDARY")

        file_descriptor = os.open(tokenizer_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            descriptor_stat = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or descriptor_stat.st_ino != path_stat.st_ino
                or descriptor_stat.st_dev != path_stat.st_dev
                or descriptor_stat.st_size != path_stat.st_size
            ):
                raise BgeRuntimeError("TOKENIZER_FILE_RACE")
            raw = bytearray()
            while len(raw) <= _MAX_TOKENIZER_BYTES:
                chunk = os.read(file_descriptor, min(1024 * 1024, _MAX_TOKENIZER_BYTES + 1))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) != descriptor_stat.st_size:
                raise BgeRuntimeError("TOKENIZER_FILE_SIZE")
        finally:
            os.close(file_descriptor)

        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise BgeRuntimeError("TOKENIZER_SHA256_MISMATCH")
        try:
            text = raw.decode("utf-8", errors="strict")
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BgeRuntimeError("TOKENIZER_JSON_INVALID") from error
        _validate_tokenizer_json(payload)
        try:
            tokenizer = Tokenizer.from_str(text)
        except Exception as error:
            raise BgeRuntimeError("TOKENIZER_JSON_INVALID") from error
        return cls(tokenizer=tokenizer)

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        """special token을 제외한 exact BGE token의 원문 character offset을 반환한다."""

        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        spans = tuple((start, end) for start, end in encoding.offsets if end > start)
        if len(spans) != len(encoding.ids):
            raise BgeRuntimeError("TOKENIZER_OFFSET_CONTRACT")
        return spans

    def count_tokens(self, text: str) -> int:
        """canonical chunk 정책에 쓰는 special-token 제외 count를 반환한다."""

        return len(self.token_spans(text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        """원문을 재조립하지 않고 앞쪽 token span까지만 자른다."""

        spans = self.token_spans(text)
        if maximum_tokens <= 0 or not spans:
            return ""
        return text[: spans[min(maximum_tokens, len(spans)) - 1][1]].rstrip()

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        """원문을 재조립하지 않고 뒤쪽 token span부터 자른다."""

        spans = self.token_spans(text)
        if maximum_tokens <= 0 or not spans:
            return ""
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :].lstrip()

    def encode_batch(self, texts: tuple[str, ...]) -> BgeEncodedBatch:
        """bounded non-empty text 묶음을 padding한 int64 ONNX input으로 변환한다."""

        if not texts or len(texts) > 64:
            raise BgeRuntimeError("TOKENIZER_BATCH_BOUND")
        if any(not text or len(text.encode("utf-8")) > 65_536 for text in texts):
            raise BgeRuntimeError("TOKENIZER_TEXT_BOUND")
        encodings = self._tokenizer.encode_batch(list(texts), add_special_tokens=True)
        maximum_length = max(len(encoding.ids) for encoding in encodings)
        if maximum_length <= 0 or maximum_length > 8_192:
            raise BgeRuntimeError("TOKENIZER_SEQUENCE_BOUND")
        pad_id = self._tokenizer.token_to_id("<pad>")
        if pad_id is None:
            pad_id = self._tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            raise BgeRuntimeError("TOKENIZER_PAD_TOKEN_MISSING")

        input_ids = np.full((len(encodings), maximum_length), pad_id, dtype=np.int64)
        attention_mask = np.zeros((len(encodings), maximum_length), dtype=np.int64)
        token_type_ids = np.zeros((len(encodings), maximum_length), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            input_ids[row, :length] = np.asarray(encoding.ids, dtype=np.int64)
            attention_mask[row, :length] = 1
            if encoding.type_ids:
                if len(encoding.type_ids) != length:
                    raise BgeRuntimeError("TOKENIZER_TYPE_ID_CONTRACT")
                token_type_ids[row, :length] = np.asarray(encoding.type_ids, dtype=np.int64)
        return BgeEncodedBatch(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )


class BgeOnnxEmbedder:
    """CPU-only ONNX session을 통해 1024-d float32 normalized embedding을 만든다."""

    def __init__(
        self,
        *,
        tokenizer: BgeTokenizerPort,
        session: BgeOnnxSessionPort,
        output_mode: str,
    ) -> None:
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise BgeRuntimeError("BGE runtime requires exact CPUExecutionProvider.")
        if output_mode not in _OUTPUT_MODES:
            raise BgeRuntimeError("BGE_OUTPUT_MODE")
        self._tokenizer = tokenizer
        self._session = session
        self._output_mode = cast(OutputMode, output_mode)
        self._input_names = tuple(item.name for item in session.get_inputs())
        self._output_names = tuple(item.name for item in session.get_outputs())
        if set(self._input_names) not in (
            {"input_ids", "attention_mask"},
            {"input_ids", "attention_mask", "token_type_ids"},
        ):
            raise BgeRuntimeError("BGE_INPUT_CONTRACT")
        if not self._output_names:
            raise BgeRuntimeError("BGE_OUTPUT_CONTRACT")

    def embed_query(self, question: str) -> NDArray[np.float32]:
        """query 원문 하나만 embedding하며 adjacent document context를 만들지 않는다."""

        return cast(NDArray[np.float32], self.embed((question,))[0])

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        """입력 순서를 보존해 batch embedding을 계산하고 finite/norm을 검증한다."""

        encoded = self._tokenizer.encode_batch(texts)
        available: dict[str, NDArray[np.generic]] = {
            "input_ids": encoded.input_ids,
            "attention_mask": encoded.attention_mask,
        }
        if encoded.token_type_ids is not None:
            available["token_type_ids"] = encoded.token_type_ids
        elif "token_type_ids" in self._input_names:
            # 일부 BERT 계열 export는 tokenizer type_ids가 모두 0이어도 tensor 자체를 요구한다.
            available["token_type_ids"] = np.zeros_like(encoded.input_ids, dtype=np.int64)
        if any(name not in available for name in self._input_names):
            raise BgeRuntimeError("BGE_INPUT_TENSOR_MISSING")
        inputs = {name: available[name] for name in self._input_names}
        output_name = _select_output_name(self._output_names, self._output_mode)
        raw_outputs = self._session.run([output_name], inputs)
        if len(raw_outputs) != 1 or not isinstance(raw_outputs[0], np.ndarray):
            raise BgeRuntimeError("BGE_OUTPUT_SHAPE")
        raw = raw_outputs[0]
        if self._output_mode == "LAST_HIDDEN_STATE_CLS":
            if raw.ndim != 3 or raw.shape[0] != len(texts) or raw.shape[2] != 1024:
                raise BgeRuntimeError("BGE_OUTPUT_SHAPE")
            pooled = raw[:, 0, :]
        else:
            if raw.ndim != 2 or raw.shape != (len(texts), 1024):
                raise BgeRuntimeError("BGE_OUTPUT_SHAPE")
            pooled = raw
        if pooled.dtype != np.float32 or not np.isfinite(pooled).all():
            raise BgeRuntimeError("BGE_OUTPUT_NUMERIC")
        norms = np.linalg.norm(pooled, axis=1)
        if not np.isfinite(norms).all() or np.any(norms <= 0):
            raise BgeRuntimeError("BGE_OUTPUT_ZERO_NORM")
        normalized = np.asarray(pooled / norms[:, None], dtype=np.float32)
        return validate_embedding_batch(normalized, expected_rows=len(texts))


def validate_embedding_batch(
    embedding: NDArray[np.generic],
    *,
    expected_rows: int,
) -> NDArray[np.float32]:
    """DB/COPY 전 float32·행수·1024-d·finite·unit norm을 재검증한다."""

    if (
        not isinstance(embedding, np.ndarray)
        or embedding.dtype != np.float32
        or embedding.ndim != 2
        or embedding.shape != (expected_rows, 1024)
        or not np.isfinite(embedding).all()
    ):
        raise BgeRuntimeError("BGE_EMBEDDING_CONTRACT")
    norms = np.linalg.norm(embedding, axis=1)
    if not np.isfinite(norms).all() or np.any(norms <= 0):
        raise BgeRuntimeError("BGE_EMBEDDING_ZERO_NORM")
    if not np.allclose(norms, 1.0, atol=1e-5, rtol=0.0):
        raise BgeRuntimeError("BGE_EMBEDDING_NOT_NORMALIZED")
    return cast(NDArray[np.float32], embedding)


def load_bge_onnx_embedder(packet_root: Path) -> BgeOnnxEmbedder:
    """검증된 packet에서만 ORT CPU session과 static tokenizer를 구성한다.

    session thread 수를 각각 1로 고정해 PoC가 host 전체 CPU를 점유하지 않게 하고, model
    directory는 호출자가 read-only로 전환한 뒤 사용한다.
    """

    try:
        verify_bge_packet(packet_root)
    except BgeArtifactError as error:
        raise BgeRuntimeError("BGE_PACKET_VERIFICATION_FAILED") from error

    model_entry = next(
        item
        for item in APPROVED_BGE_ARTIFACT_SPEC.files
        if item.relative_path == "onnx/model.onnx"
    )
    external_file_sizes = {
        Path(item.relative_path).name: item.size_bytes
        for item in APPROVED_BGE_ARTIFACT_SPEC.files
        if item.relative_path
        in {
            "onnx/Constant_7_attr__value",
            "onnx/model.onnx_data",
        }
    }
    try:
        graph_contract = inspect_onnx_graph_contract(
            packet_root / "onnx/model.onnx",
            expected_sha256=model_entry.sha256,
            external_file_sizes=external_file_sizes,
        )
    except BgeArtifactError as error:
        raise BgeRuntimeError("BGE_ONNX_STRUCTURE_FAILED") from error
    pooling_mode = load_pooling_mode(packet_root)
    model_config_entry = next(
        item
        for item in APPROVED_BGE_ARTIFACT_SPEC.files
        if item.relative_path == "onnx/config.json"
    )
    model_dimension = load_model_dimension(
        packet_root / "onnx/config.json",
        expected_sha256=model_config_entry.sha256,
        expected_size=model_config_entry.size_bytes,
    )
    if model_dimension != 1024:
        raise BgeRuntimeError("BGE_MODEL_CONFIG_CONTRACT")

    try:
        import onnxruntime as ort  # type: ignore[import-untyped]
    except ImportError as error:
        raise BgeRuntimeError("ONNXRUNTIME_UNAVAILABLE") from error

    tokenizer = BgeStaticTokenizer.from_file(packet_root / "onnx/tokenizer.json")
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = 1
    session_options.inter_op_num_threads = 1
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    session_options.enable_mem_pattern = False
    session = ort.InferenceSession(
        str(packet_root / "onnx/model.onnx"),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    output_names = tuple(item.name for item in session.get_outputs())
    if "sentence_embedding" in output_names:
        output_mode: OutputMode = "POOLED_OUTPUT"
    elif "last_hidden_state" in output_names:
        output_mode = "LAST_HIDDEN_STATE_CLS"
    else:
        raise BgeRuntimeError("BGE_OUTPUT_CONTRACT")
    if output_mode == "LAST_HIDDEN_STATE_CLS" and pooling_mode != "CLS":
        raise BgeRuntimeError("BGE_POOLING_CONFIG_CONTRACT")

    output = next(
        item
        for item in session.get_outputs()
        if item.name
        == ("sentence_embedding" if output_mode == "POOLED_OUTPUT" else "last_hidden_state")
    )
    output_shape = tuple(output.shape)
    output_dimension = output_shape[-1] if output_shape and isinstance(output_shape[-1], int) else -1
    expected_output_dimension = (
        graph_contract.output_dimension
        if graph_contract.output_dimension != -1
        else model_dimension
    )
    if (
        tuple(item.name for item in session.get_inputs()) != graph_contract.input_names
        or output_names != graph_contract.output_names
        or ("float32" if output.type == "tensor(float)" else output.type)
        != graph_contract.output_dtype
        or output_dimension != expected_output_dimension
    ):
        raise BgeRuntimeError("BGE_ORT_GRAPH_ATTESTATION")
    return BgeOnnxEmbedder(
        tokenizer=tokenizer,
        session=cast(BgeOnnxSessionPort, session),
        output_mode=output_mode,
    )


def load_pooling_mode(packet_root: Path) -> Literal["CLS"]:
    """pinned pooling config가 CLS 하나만 켰는지 bounded JSON으로 확인한다."""

    pooling_path = packet_root / "1_Pooling/config.json"
    expected = next(
        item
        for item in APPROVED_BGE_ARTIFACT_SPEC.files
        if item.relative_path == "1_Pooling/config.json"
    )
    raw = _read_bounded_regular_file(
        pooling_path,
        expected_sha256=expected.sha256,
        maximum_bytes=expected.size_bytes,
    )
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BgeRuntimeError("BGE_POOLING_CONFIG_JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("pooling_mode_cls_token") is not True
        or any(
            payload.get(key) is True
            for key in (
                "pooling_mode_lasttoken",
                "pooling_mode_max_tokens",
                "pooling_mode_mean_sqrt_len_tokens",
                "pooling_mode_mean_tokens",
                "pooling_mode_weightedmean_tokens",
            )
        )
    ):
        raise BgeRuntimeError("BGE_POOLING_CONFIG_CONTRACT")
    return "CLS"


def load_model_dimension(
    config_path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> int:
    """pinned ONNX model config가 XLM-R float32/1024 계약인지 확인한다."""

    raw = _read_bounded_regular_file(
        config_path,
        expected_sha256=expected_sha256,
        maximum_bytes=expected_size,
    )
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BgeRuntimeError("BGE_MODEL_CONFIG_JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("architectures") != ["XLMRobertaModel"]
        or payload.get("hidden_size") != 1024
        or payload.get("model_type") != "xlm-roberta"
        or payload.get("torch_dtype") != "float32"
        or payload.get("vocab_size") != 250002
    ):
        raise BgeRuntimeError("BGE_MODEL_CONFIG_CONTRACT")
    return 1024


def _select_output_name(output_names: tuple[str, ...], output_mode: OutputMode) -> str:
    expected = "last_hidden_state" if output_mode == "LAST_HIDDEN_STATE_CLS" else "sentence_embedding"
    if expected not in output_names:
        raise BgeRuntimeError("BGE_OUTPUT_CONTRACT")
    return expected


def _validate_tokenizer_json(payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != _TOKENIZER_TOP_LEVEL_FIELDS:
        raise BgeRuntimeError("TOKENIZER_JSON_CONTRACT")
    nodes = 0
    stack: list[tuple[object, int]] = [(payload, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_TOKENIZER_JSON_NODES or depth > _MAX_TOKENIZER_JSON_DEPTH:
            raise BgeRuntimeError("TOKENIZER_JSON_BOUND")
        if isinstance(value, dict):
            if len(value) > 500_000 or any(
                not isinstance(key, str) or len(key.encode("utf-8")) > 8_192
                for key in value
            ):
                raise BgeRuntimeError("TOKENIZER_JSON_CONTRACT")
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            if len(value) > 500_000:
                raise BgeRuntimeError("TOKENIZER_JSON_BOUND")
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if (
                len(value.encode("utf-8")) > _MAX_TOKENIZER_JSON_STRING_BYTES
                or "\x00" in value
            ):
                raise BgeRuntimeError("TOKENIZER_JSON_CONTRACT")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise BgeRuntimeError("TOKENIZER_JSON_CONTRACT")
        elif value is not None and type(value) not in {bool, int}:
            raise BgeRuntimeError("TOKENIZER_JSON_CONTRACT")


def _read_bounded_regular_file(
    path: Path,
    *,
    expected_sha256: str,
    maximum_bytes: int,
) -> bytes:
    try:
        path_stat = path.lstat()
    except FileNotFoundError as error:
        raise BgeRuntimeError("BGE_CONFIG_NOT_FOUND") from error
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_nlink != 1
        or stat.S_IMODE(path_stat.st_mode) != 0o600
        or path_stat.st_size <= 0
        or path_stat.st_size > maximum_bytes
    ):
        raise BgeRuntimeError("BGE_CONFIG_FILE_BOUNDARY")
    file_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(file_descriptor)
        if (
            descriptor_stat.st_ino != path_stat.st_ino
            or descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_size != path_stat.st_size
        ):
            raise BgeRuntimeError("BGE_CONFIG_FILE_RACE")
        raw = bytearray()
        while len(raw) <= maximum_bytes:
            chunk = os.read(file_descriptor, maximum_bytes + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
    finally:
        os.close(file_descriptor)
    if len(raw) != path_stat.st_size:
        raise BgeRuntimeError("BGE_CONFIG_FILE_SIZE")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise BgeRuntimeError("BGE_POOLING_CONFIG_SHA256")
    return bytes(raw)
