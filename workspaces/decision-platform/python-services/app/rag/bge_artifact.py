from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal
from urllib.parse import SplitResult, unquote, urlsplit

_SHA256_HEX_LENGTH = 64
_MAX_ONNX_PROTO_BYTES = 2 * 1024 * 1024
_MAX_PROTO_FIELDS = 250_000
_ALLOWED_REDIRECT_HOSTS = frozenset(
    {
        "cas-bridge.xethub.hf.co",
        "cdn-lfs.huggingface.co",
        "us.aws.cdn.hf.co",
    }
)
_ALLOWED_ONNX_DOMAINS = frozenset({"", "ai.onnx"})
_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".bin",
        ".joblib",
        ".pickle",
        ".pkl",
        ".pt",
        ".pth",
        ".safetensors",
    }
)


class BgeArtifactError(ValueError):
    """승인된 BGE packet의 공급망·파일·graph 계약 위반을 안전한 marker로 보고한다."""


@dataclass(frozen=True)
class BgeArtifactFile:
    """승인 packet의 한 파일에 대한 immutable size/SHA-256 계약."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BgeArtifactSpec:
    """repository/revision/license와 exact 파일 membership을 함께 고정한다."""

    repository: str
    revision: str
    license_id: str
    artifact_type: Literal["ONNX_DATA_ONLY"]
    files: tuple[BgeArtifactFile, ...]


@dataclass(frozen=True)
class BgeVerifiedPacket:
    """실행 전에 검증된 packet의 공개 가능한 bounded receipt."""

    revision: str
    file_count: int
    total_bytes: int
    file_manifest_sha256: str


@dataclass(frozen=True)
class OnnxGraphContract:
    """exact-hash ONNX graph에서 추출한 실행 전 구조 receipt."""

    external_data_locations: tuple[str, ...]
    node_domains: tuple[str, ...]
    input_names: tuple[str, ...]
    output_names: tuple[str, ...]
    output_dtype: str
    output_dimension: int
    dynamic_batch: bool
    dynamic_sequence: bool
    external_data_bytes: int = 0


APPROVED_BGE_ARTIFACT_SPEC = BgeArtifactSpec(
    repository="BAAI/bge-m3",
    revision="5617a9f61b028005a4858fdac845db406aefb181",
    license_id="MIT",
    artifact_type="ONNX_DATA_ONLY",
    files=(
        BgeArtifactFile(
            "onnx/model.onnx",
            724_923,
            "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b",
        ),
        BgeArtifactFile(
            "onnx/model.onnx_data",
            2_266_820_608,
            "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4",
        ),
        BgeArtifactFile(
            "onnx/Constant_7_attr__value",
            65_552,
            "cdf16f72c5d07b36484056e601ed9687f78477e5d85cee85a34f2406b7fb5906",
        ),
        BgeArtifactFile(
            "onnx/config.json",
            698,
            "f24afd5de914fba8c668426c43d208a1a54022500c63b2c160be20891686fce8",
        ),
        BgeArtifactFile(
            "onnx/tokenizer.json",
            17_082_821,
            "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790",
        ),
        BgeArtifactFile(
            "onnx/tokenizer_config.json",
            1_173,
            "7e4c1cc848840aeccdd763458c18dd525eb0f795c992e00ebe9c28554e7db2d4",
        ),
        BgeArtifactFile(
            "onnx/special_tokens_map.json",
            964,
            "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
        ),
        BgeArtifactFile(
            "onnx/sentencepiece.bpe.model",
            5_069_051,
            "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
        ),
        BgeArtifactFile(
            "1_Pooling/config.json",
            191,
            "e54c164a07274f2eb45bb724f54a79d1efcc90c41573887cd9a29aeee0597352",
        ),
        BgeArtifactFile(
            "README.md",
            15_822,
            "0b81ccf9134e5874d620a86e6905062ea999e779c34eb1a7e65eaeb7fe00e450",
        ),
    ),
)


def verify_bge_packet(
    packet_root: Path,
    *,
    spec: BgeArtifactSpec = APPROVED_BGE_ARTIFACT_SPEC,
) -> BgeVerifiedPacket:
    """exact membership·regular file·mode·link·size·SHA-256를 실행 전에 검증한다.

    검증 대상 root와 하위 파일은 symlink를 따라가지 않는다. manifest는 검증된 상대경로,
    size, SHA-256만 canonical JSON으로 직렬화해 host path가 identity에 들어가지 않게 한다.
    """

    validate_bge_artifact_spec(spec)
    root_stat = packet_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise BgeArtifactError("NON_REGULAR_ARTIFACT_ROOT")
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise BgeArtifactError("MODE_MISMATCH:ARTIFACT_ROOT")

    expected = {entry.relative_path: entry for entry in spec.files}
    actual_paths: set[str] = set()
    for path in packet_root.rglob("*"):
        relative_path = path.relative_to(packet_root).as_posix()
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            raise BgeArtifactError("NON_REGULAR_ARTIFACT")
        if stat.S_ISDIR(path_stat.st_mode):
            if stat.S_IMODE(path_stat.st_mode) != 0o700:
                raise BgeArtifactError("MODE_MISMATCH:ARTIFACT_DIRECTORY")
            continue
        if not stat.S_ISREG(path_stat.st_mode):
            raise BgeArtifactError("NON_REGULAR_ARTIFACT")
        actual_paths.add(relative_path)

    if actual_paths != set(expected):
        raise BgeArtifactError("UNEXPECTED_ARTIFACT")

    manifest_rows: list[dict[str, int | str]] = []
    total_bytes = 0
    for relative_path in sorted(expected, key=lambda value: value.encode("utf-8")):
        entry = expected[relative_path]
        path = packet_root / PurePosixPath(relative_path)
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
            raise BgeArtifactError("NON_REGULAR_ARTIFACT")
        if path_stat.st_nlink != 1:
            raise BgeArtifactError("HARDLINK_ARTIFACT")
        if stat.S_IMODE(path_stat.st_mode) != 0o600:
            raise BgeArtifactError("MODE_MISMATCH:ARTIFACT_FILE")
        if path_stat.st_size != entry.size_bytes:
            raise BgeArtifactError("SIZE_MISMATCH")
        digest = _sha256_regular_file(path, expected_size=entry.size_bytes)
        if digest != entry.sha256:
            raise BgeArtifactError("SHA256_MISMATCH")
        total_bytes += path_stat.st_size
        manifest_rows.append(
            {
                "path": relative_path,
                "sha256": digest,
                "sizeBytes": path_stat.st_size,
            }
        )

    manifest_payload = {
        "artifactType": spec.artifact_type,
        "files": manifest_rows,
        "license": spec.license_id,
        "repository": spec.repository,
        "revision": spec.revision,
    }
    manifest_bytes = (
        json.dumps(
            manifest_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return BgeVerifiedPacket(
        revision=spec.revision,
        file_count=len(manifest_rows),
        total_bytes=total_bytes,
        file_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def validate_download_redirect(location: str) -> SplitResult:
    """수동 redirect hop을 Hugging Face의 bounded HTTPS CAS host로만 제한한다."""

    if not isinstance(location, str) or not location or len(location) > 8_192:
        raise BgeArtifactError("UNSAFE_DOWNLOAD_REDIRECT")
    parsed = urlsplit(location)
    hostname = parsed.hostname
    decoded_path = unquote(parsed.path)
    try:
        if hostname is not None:
            ipaddress.ip_address(hostname)
            is_ip_literal = True
        else:
            is_ip_literal = False
    except ValueError:
        is_ip_literal = False
    path_parts = PurePosixPath(decoded_path).parts
    if (
        parsed.scheme != "https"
        or hostname is None
        or hostname.lower() not in _ALLOWED_REDIRECT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or is_ip_literal
        or not decoded_path.startswith("/")
        or "\\" in decoded_path
        or ".." in path_parts
        or "." in path_parts
        or "//" in decoded_path
    ):
        raise BgeArtifactError("UNSAFE_DOWNLOAD_REDIRECT")
    return parsed


def validate_onnx_graph_contract(contract: OnnxGraphContract) -> None:
    """external-data path와 standard-domain/1024-float32 graph 경계를 실행 전에 고정한다."""

    if not contract.external_data_locations:
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_MISSING")
    if len(set(contract.external_data_locations)) != len(contract.external_data_locations):
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_DUPLICATE")
    allowed_external_names = {
        PurePosixPath(entry.relative_path).name
        for entry in APPROVED_BGE_ARTIFACT_SPEC.files
        if entry.relative_path
        in {
            "onnx/Constant_7_attr__value",
            "onnx/model.onnx_data",
        }
    }
    for location in contract.external_data_locations:
        path = PurePosixPath(location)
        if (
            not location
            or path.is_absolute()
            or len(path.parts) != 1
            or location not in allowed_external_names
            or "\\" in location
            or "://" in location
        ):
            raise BgeArtifactError("ONNX_EXTERNAL_DATA_PATH")
    if not contract.node_domains or any(
        domain not in _ALLOWED_ONNX_DOMAINS for domain in contract.node_domains
    ):
        raise BgeArtifactError("ONNX_CUSTOM_DOMAIN")
    if (
        set(contract.input_names) not in (
            {"input_ids", "attention_mask"},
            {"input_ids", "attention_mask", "token_type_ids"},
        )
        or not contract.output_names
        or contract.output_dtype != "float32"
        or (
            contract.output_dimension != 1024
            and not (
                contract.output_dimension == -1
                and "sentence_embedding" in contract.output_names
            )
        )
        or not contract.dynamic_batch
        or not contract.dynamic_sequence
        or contract.external_data_bytes <= 0
    ):
        raise BgeArtifactError("ONNX_GRAPH_IO_CONTRACT")


def inspect_onnx_graph_contract(
    model_path: Path,
    *,
    expected_sha256: str,
    external_file_sizes: dict[str, int],
) -> OnnxGraphContract:
    """exact-hash ONNX protobuf에서 graph I/O·domain·external-data 경계를 추출한다.

    ONNX session을 열기 전에 bounded wire parser만 사용한다. tensor raw payload를 해석하거나
    custom library를 로드하지 않고, 알려진 Graph/Node/Attribute/Tensor/ValueInfo field만 읽는다.
    """

    raw = _read_regular_file(
        model_path,
        expected_size_limit=_MAX_ONNX_PROTO_BYTES,
        expected_sha256=expected_sha256,
    )
    graph_fields = [
        field
        for field in _iter_proto_fields(raw)
        if field.number == 7 and field.wire_type == 2
    ]
    if len(graph_fields) != 1:
        raise BgeArtifactError("ONNX_GRAPH_PROTO_MISSING")

    node_domains: list[str] = []
    input_values: list[_OnnxValueInfo] = []
    output_values: list[_OnnxValueInfo] = []
    external_segments: list[_OnnxExternalSegment] = []
    _inspect_graph_proto(
        _field_bytes(graph_fields[0]),
        node_domains=node_domains,
        input_values=input_values,
        output_values=output_values,
        external_segments=external_segments,
    )
    if not input_values or not output_values or not node_domains or not external_segments:
        raise BgeArtifactError("ONNX_GRAPH_PROTO_INCOMPLETE")

    _validate_external_segments(external_segments, external_file_sizes=external_file_sizes)
    output = _select_graph_output(output_values)
    contract = OnnxGraphContract(
        external_data_locations=tuple(
            sorted(
                {segment.location for segment in external_segments},
                key=lambda value: value.encode("utf-8"),
            )
        ),
        node_domains=tuple(
            sorted(set(node_domains), key=lambda value: value.encode("utf-8"))
        ),
        input_names=tuple(value.name for value in input_values),
        output_names=tuple(value.name for value in output_values),
        output_dtype=_onnx_dtype_name(output.element_type),
        output_dimension=output.dimensions[-1]
        if output.dimensions and isinstance(output.dimensions[-1], int)
        else -1,
        dynamic_batch=all(
            len(value.dimensions) >= 1 and isinstance(value.dimensions[0], str)
            for value in input_values
        ),
        dynamic_sequence=all(
            len(value.dimensions) >= 2 and isinstance(value.dimensions[1], str)
            for value in input_values
        ),
        external_data_bytes=sum(segment.length for segment in external_segments),
    )
    validate_onnx_graph_contract(contract)
    return contract


@dataclass(frozen=True)
class _ProtoField:
    number: int
    wire_type: int
    value: int | bytes


@dataclass(frozen=True)
class _OnnxValueInfo:
    name: str
    element_type: int
    dimensions: tuple[int | str, ...]


@dataclass(frozen=True)
class _OnnxExternalSegment:
    location: str
    offset: int
    length: int


def _inspect_graph_proto(
    payload: bytes,
    *,
    node_domains: list[str],
    input_values: list[_OnnxValueInfo],
    output_values: list[_OnnxValueInfo],
    external_segments: list[_OnnxExternalSegment],
) -> None:
    for field in _iter_proto_fields(payload):
        if field.number == 1 and field.wire_type == 2:
            _inspect_node_proto(
                _field_bytes(field),
                node_domains=node_domains,
                external_segments=external_segments,
            )
        elif field.number == 5 and field.wire_type == 2:
            external_segments.extend(_inspect_tensor_proto(_field_bytes(field)))
        elif field.number == 11 and field.wire_type == 2:
            input_values.append(_inspect_value_info_proto(_field_bytes(field)))
        elif field.number == 12 and field.wire_type == 2:
            output_values.append(_inspect_value_info_proto(_field_bytes(field)))
        elif field.number == 15:
            raise BgeArtifactError("ONNX_SPARSE_INITIALIZER_FORBIDDEN")


def _inspect_node_proto(
    payload: bytes,
    *,
    node_domains: list[str],
    external_segments: list[_OnnxExternalSegment],
) -> None:
    domain = ""
    domain_seen = False
    for field in _iter_proto_fields(payload):
        if field.number == 5 and field.wire_type == 2:
            _inspect_attribute_proto(
                _field_bytes(field),
                node_domains=node_domains,
                external_segments=external_segments,
            )
        elif field.number == 7 and field.wire_type == 2:
            if domain_seen:
                raise BgeArtifactError("ONNX_NODE_DOMAIN_DUPLICATE")
            domain = _decode_proto_text(_field_bytes(field), marker="ONNX_NODE_DOMAIN")
            domain_seen = True
    node_domains.append(domain)


def _inspect_attribute_proto(
    payload: bytes,
    *,
    node_domains: list[str],
    external_segments: list[_OnnxExternalSegment],
) -> None:
    for field in _iter_proto_fields(payload):
        if field.number in {7, 12} and field.wire_type == 2:
            external_segments.extend(_inspect_tensor_proto(_field_bytes(field)))
        elif field.number in {8, 13} and field.wire_type == 2:
            _inspect_graph_proto(
                _field_bytes(field),
                node_domains=node_domains,
                input_values=[],
                output_values=[],
                external_segments=external_segments,
            )
        elif field.number in {16, 17}:
            raise BgeArtifactError("ONNX_SPARSE_TENSOR_FORBIDDEN")


def _inspect_tensor_proto(payload: bytes) -> tuple[_OnnxExternalSegment, ...]:
    external_values: dict[str, str] = {}
    data_location: int | None = None
    for field in _iter_proto_fields(payload):
        if field.number == 13 and field.wire_type == 2:
            key, value = _inspect_string_entry_proto(_field_bytes(field))
            if key in external_values:
                raise BgeArtifactError("ONNX_EXTERNAL_DATA_KEY_DUPLICATE")
            external_values[key] = value
        elif field.number == 14 and field.wire_type == 0:
            if data_location is not None:
                raise BgeArtifactError("ONNX_DATA_LOCATION_DUPLICATE")
            data_location = _field_varint(field)
    if data_location != 1:
        if external_values:
            raise BgeArtifactError("ONNX_EXTERNAL_DATA_LOCATION_FLAG")
        return ()
    if set(external_values) != {"location", "offset", "length"}:
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_FIELDS")
    try:
        offset = int(external_values["offset"], 10)
        length = int(external_values["length"], 10)
    except ValueError as error:
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_RANGE") from error
    if offset < 0 or length <= 0:
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_RANGE")
    return (
        _OnnxExternalSegment(
            location=external_values["location"],
            offset=offset,
            length=length,
        ),
    )


def _inspect_string_entry_proto(payload: bytes) -> tuple[str, str]:
    values: dict[int, str] = {}
    for field in _iter_proto_fields(payload):
        if field.number in {1, 2} and field.wire_type == 2:
            if field.number in values:
                raise BgeArtifactError("ONNX_EXTERNAL_DATA_ENTRY_DUPLICATE")
            values[field.number] = _decode_proto_text(
                _field_bytes(field),
                marker="ONNX_EXTERNAL_DATA_TEXT",
            )
    if set(values) != {1, 2}:
        raise BgeArtifactError("ONNX_EXTERNAL_DATA_ENTRY")
    return values[1], values[2]


def _inspect_value_info_proto(payload: bytes) -> _OnnxValueInfo:
    name: str | None = None
    type_payload: bytes | None = None
    for field in _iter_proto_fields(payload):
        if field.number == 1 and field.wire_type == 2:
            if name is not None:
                raise BgeArtifactError("ONNX_VALUE_INFO_NAME_DUPLICATE")
            name = _decode_proto_text(_field_bytes(field), marker="ONNX_VALUE_INFO_NAME")
        elif field.number == 2 and field.wire_type == 2:
            if type_payload is not None:
                raise BgeArtifactError("ONNX_VALUE_INFO_TYPE_DUPLICATE")
            type_payload = _field_bytes(field)
    if not name or type_payload is None:
        raise BgeArtifactError("ONNX_VALUE_INFO_INCOMPLETE")
    element_type, dimensions = _inspect_type_proto(type_payload)
    return _OnnxValueInfo(
        name=name,
        element_type=element_type,
        dimensions=dimensions,
    )


def _inspect_type_proto(payload: bytes) -> tuple[int, tuple[int | str, ...]]:
    tensor_payloads = [
        _field_bytes(field)
        for field in _iter_proto_fields(payload)
        if field.number == 1 and field.wire_type == 2
    ]
    if len(tensor_payloads) != 1:
        raise BgeArtifactError("ONNX_NON_TENSOR_VALUE_INFO")
    element_type: int | None = None
    shape_payload: bytes | None = None
    for field in _iter_proto_fields(tensor_payloads[0]):
        if field.number == 1 and field.wire_type == 0:
            if element_type is not None:
                raise BgeArtifactError("ONNX_TENSOR_TYPE_DUPLICATE")
            element_type = _field_varint(field)
        elif field.number == 2 and field.wire_type == 2:
            if shape_payload is not None:
                raise BgeArtifactError("ONNX_TENSOR_SHAPE_DUPLICATE")
            shape_payload = _field_bytes(field)
    if element_type is None or shape_payload is None:
        raise BgeArtifactError("ONNX_TENSOR_TYPE_INCOMPLETE")
    return element_type, _inspect_shape_proto(shape_payload)


def _inspect_shape_proto(payload: bytes) -> tuple[int | str, ...]:
    dimensions: list[int | str] = []
    for field in _iter_proto_fields(payload):
        if field.number != 1 or field.wire_type != 2:
            continue
        dimension_values: list[int | str] = []
        for dimension_field in _iter_proto_fields(_field_bytes(field)):
            if dimension_field.number == 1 and dimension_field.wire_type == 0:
                dimension_values.append(_field_varint(dimension_field))
            elif dimension_field.number == 2 and dimension_field.wire_type == 2:
                dimension_values.append(
                    _decode_proto_text(
                        _field_bytes(dimension_field),
                        marker="ONNX_DIMENSION_PARAM",
                    )
                )
        if len(dimension_values) != 1:
            raise BgeArtifactError("ONNX_DIMENSION_INCOMPLETE")
        dimensions.append(dimension_values[0])
    if not dimensions or len(dimensions) > 8:
        raise BgeArtifactError("ONNX_SHAPE_BOUND")
    return tuple(dimensions)


def _validate_external_segments(
    segments: Iterable[_OnnxExternalSegment],
    *,
    external_file_sizes: dict[str, int],
) -> None:
    allowed_locations = {
        "Constant_7_attr__value",
        "model.onnx_data",
    }
    if set(external_file_sizes) != allowed_locations or any(
        type(size) is not int or size <= 0 for size in external_file_sizes.values()
    ):
        raise BgeArtifactError("ONNX_EXTERNAL_FILE_SIZE_CONTRACT")
    for segment in segments:
        if segment.location not in allowed_locations:
            raise BgeArtifactError("ONNX_EXTERNAL_DATA_PATH")
        if segment.offset + segment.length > external_file_sizes[segment.location]:
            raise BgeArtifactError("ONNX_EXTERNAL_DATA_RANGE")


def _select_graph_output(outputs: list[_OnnxValueInfo]) -> _OnnxValueInfo:
    by_name = {output.name: output for output in outputs}
    if len(by_name) != len(outputs):
        raise BgeArtifactError("ONNX_OUTPUT_NAME_DUPLICATE")
    for expected_name in ("sentence_embedding", "last_hidden_state"):
        if expected_name in by_name:
            return by_name[expected_name]
    if len(outputs) == 1:
        return outputs[0]
    raise BgeArtifactError("ONNX_GRAPH_OUTPUT_UNKNOWN")


def _onnx_dtype_name(element_type: int) -> str:
    if element_type == 1:
        return "float32"
    return f"onnx_dtype_{element_type}"


def _iter_proto_fields(payload: bytes) -> Iterable[_ProtoField]:
    position = 0
    field_count = 0
    while position < len(payload):
        field_count += 1
        if field_count > _MAX_PROTO_FIELDS:
            raise BgeArtifactError("ONNX_PROTO_FIELD_BOUND")
        key, position = _read_proto_varint(payload, position)
        number = key >> 3
        wire_type = key & 0x07
        value: int | bytes
        if number <= 0:
            raise BgeArtifactError("ONNX_PROTO_FIELD_NUMBER")
        if wire_type == 0:
            value, position = _read_proto_varint(payload, position)
        elif wire_type == 1:
            end = position + 8
            if end > len(payload):
                raise BgeArtifactError("ONNX_PROTO_TRUNCATED")
            value = payload[position:end]
            position = end
        elif wire_type == 2:
            length, position = _read_proto_varint(payload, position)
            end = position + length
            if length < 0 or end > len(payload):
                raise BgeArtifactError("ONNX_PROTO_TRUNCATED")
            value = payload[position:end]
            position = end
        elif wire_type == 5:
            end = position + 4
            if end > len(payload):
                raise BgeArtifactError("ONNX_PROTO_TRUNCATED")
            value = payload[position:end]
            position = end
        else:
            raise BgeArtifactError("ONNX_PROTO_WIRE_TYPE")
        yield _ProtoField(number=number, wire_type=wire_type, value=value)


def _read_proto_varint(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(payload):
            raise BgeArtifactError("ONNX_PROTO_TRUNCATED")
        byte = payload[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, position
    raise BgeArtifactError("ONNX_PROTO_VARINT_BOUND")


def _field_bytes(field: _ProtoField) -> bytes:
    if field.wire_type != 2 or not isinstance(field.value, bytes):
        raise BgeArtifactError("ONNX_PROTO_FIELD_TYPE")
    return field.value


def _field_varint(field: _ProtoField) -> int:
    if field.wire_type != 0 or not isinstance(field.value, int):
        raise BgeArtifactError("ONNX_PROTO_FIELD_TYPE")
    return field.value


def _decode_proto_text(payload: bytes, *, marker: str) -> str:
    try:
        value = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise BgeArtifactError(marker) from error
    if not value or len(value) > 1_024 or "\x00" in value:
        raise BgeArtifactError(marker)
    return value


def _read_regular_file(
    path: Path,
    *,
    expected_size_limit: int,
    expected_sha256: str,
) -> bytes:
    try:
        path_stat = path.lstat()
    except FileNotFoundError as error:
        raise BgeArtifactError("ARTIFACT_NOT_FOUND") from error
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or stat.S_ISLNK(path_stat.st_mode)
        or path_stat.st_nlink != 1
        or stat.S_IMODE(path_stat.st_mode) != 0o600
        or path_stat.st_size <= 0
        or path_stat.st_size > expected_size_limit
    ):
        raise BgeArtifactError("ARTIFACT_FILE_BOUNDARY")
    file_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or descriptor_stat.st_ino != path_stat.st_ino
            or descriptor_stat.st_dev != path_stat.st_dev
            or descriptor_stat.st_size != path_stat.st_size
        ):
            raise BgeArtifactError("ARTIFACT_RACE_OR_LINK")
        raw = bytearray()
        while len(raw) <= expected_size_limit:
            chunk = os.read(
                file_descriptor,
                min(1024 * 1024, expected_size_limit + 1 - len(raw)),
            )
            if not chunk:
                break
            raw.extend(chunk)
    finally:
        os.close(file_descriptor)
    if len(raw) != path_stat.st_size:
        raise BgeArtifactError("ARTIFACT_FILE_SIZE")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise BgeArtifactError("SHA256_MISMATCH")
    return bytes(raw)


def validate_bge_artifact_spec(spec: BgeArtifactSpec) -> None:
    """download 전에 repository/revision/license/path/hash packet drift를 차단한다."""

    if (
        spec.repository != "BAAI/bge-m3"
        or spec.revision != "5617a9f61b028005a4858fdac845db406aefb181"
        or spec.license_id != "MIT"
        or spec.artifact_type != "ONNX_DATA_ONLY"
        or not spec.files
    ):
        raise BgeArtifactError("BGE_ARTIFACT_SPEC_DRIFT")
    seen: set[str] = set()
    for entry in spec.files:
        path = PurePosixPath(entry.relative_path)
        suffix = path.suffix.lower()
        if (
            entry.relative_path != unicodedata.normalize("NFC", entry.relative_path)
            or path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in entry.relative_path
            or entry.relative_path in seen
            or entry.size_bytes <= 0
            or len(entry.sha256) != _SHA256_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in entry.sha256)
            or suffix in _FORBIDDEN_SUFFIXES
        ):
            raise BgeArtifactError("UNSAFE_ARTIFACT_PATH")
        seen.add(entry.relative_path)


def _sha256_regular_file(path: Path, *, expected_size: int) -> str:
    digest = hashlib.sha256()
    bytes_read = 0
    file_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(descriptor_stat.st_mode)
            or descriptor_stat.st_nlink != 1
            or descriptor_stat.st_size != expected_size
        ):
            raise BgeArtifactError("ARTIFACT_RACE_OR_LINK")
        while True:
            chunk = os.read(file_descriptor, 4 * 1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > expected_size:
                raise BgeArtifactError("SIZE_MISMATCH")
            digest.update(chunk)
        if bytes_read != expected_size:
            raise BgeArtifactError("SIZE_MISMATCH")
    finally:
        os.close(file_descriptor)
    return digest.hexdigest()
