"""한 approved root에서 exact FRESH bootstrap packet 하나만 선택하는 CAS 경계."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.bootstrap_packet import BootstrapPacket, validate_bootstrap_packet
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.private_root import require_private_regular_file, require_private_root
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)

FRESH_AUTHORITY_FILENAME = "fresh-bootstrap-authority.v1.json"
FRESH_AUTHORITY_VERSION = "s5-bootstrap-fresh-authority-v1"
_MAX_AUTHORITY_BYTES = 16 * 1024
_MAX_ROOT_ENTRIES = 256


@dataclass(frozen=True, slots=True)
class FreshBootstrapAuthority:
    """Immutable pointer와 그 pointer가 선택한 current-policy FRESH packet."""

    content: bytes
    sha256: str
    packet: BootstrapPacket


def publish_fresh_bootstrap_authority(
    *, approved_root: Path, packet: BootstrapPacket
) -> FreshBootstrapAuthority:
    """고정 packet 파일을 먼저 봉인하고 fixed pointer를 absent→SHA CAS로 한 번만 publish한다.

    호출자는 root lock을 보유해야 한다. 원자 new-file publish도 중복 호출 경쟁에서 기존 pointer를
    덮어쓰지 않으므로, 서로 다른 packet SHA가 각자 provider budget을 여는 것을 막는다.
    """

    require_private_root(approved_root)
    _require_fresh_packet(packet)
    if fresh_bootstrap_authority_exists(approved_root=approved_root):
        selected = read_fresh_bootstrap_authority(approved_root=approved_root)
    else:
        selected = None
    if selected is not None:
        if selected.packet.sha256 != packet.sha256:
            raise LightGbmContractError("fresh bootstrap authority already selected another packet")
        return selected

    entries = os.listdir(approved_root)
    if len(entries) > _MAX_ROOT_ENTRIES:
        raise LightGbmContractError("fresh bootstrap root entry bound exceeded")
    packet_filename = f"bootstrap-{packet.sha256}.json"
    other_packets = sorted(
        name
        for name in entries
        if name.startswith("bootstrap-") and name.endswith(".json") and name != packet_filename
    )
    if other_packets:
        raise LightGbmContractError("fresh bootstrap root contains another packet")

    if _fixed_leaf_exists(approved_root, packet_filename):
        packet_file = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=packet_filename,
            max_bytes=1 * 1024 * 1024,
        )
        require_private_regular_file(
            packet_file.absolute_path,
            expected_device=packet_file.device,
            expected_inode=packet_file.inode,
        )
        if packet_file.content != packet.content:
            raise LightGbmContractError("fresh bootstrap packet file conflicts")
        validate_bootstrap_packet(
            packet_file.content,
            expected_sha256=packet.sha256,
        )
    else:
        written_packet = write_approved_new_file(
            approved_root=approved_root,
            relative_path=packet_filename,
            content=packet.content,
            max_bytes=1 * 1024 * 1024,
        )
        os.chmod(written_packet.absolute_path, 0o600, follow_symlinks=False)

    authority_content = _authority_content(packet)
    try:
        written_authority = write_approved_new_file(
            approved_root=approved_root,
            relative_path=FRESH_AUTHORITY_FILENAME,
            content=authority_content,
            max_bytes=_MAX_AUTHORITY_BYTES,
        )
        os.chmod(written_authority.absolute_path, 0o600, follow_symlinks=False)
    except (FileExistsError, RagSafeIoError):
        # CAS 경쟁에서 이미 생긴 pointer는 exact same selection일 때만 idempotent하다.
        selected = read_fresh_bootstrap_authority(approved_root=approved_root)
        if selected.packet.sha256 != packet.sha256:
            raise LightGbmContractError(
                "fresh bootstrap authority CAS selected another packet"
            ) from None
        return selected
    return read_fresh_bootstrap_authority(approved_root=approved_root)


def fresh_bootstrap_authority_exists(*, approved_root: Path) -> bool:
    """고정 authority leaf의 부재만 구분하며 symlink/비정규 leaf는 이후 validator가 거부한다."""

    require_private_root(approved_root)
    return _fixed_leaf_exists(approved_root, FRESH_AUTHORITY_FILENAME)


def read_fresh_bootstrap_authority(*, approved_root: Path) -> FreshBootstrapAuthority:
    """고정 pointer와 선택 packet을 bounded/no-follow로 함께 검증한다."""

    require_private_root(approved_root)
    authority_file = read_approved_regular_file(
        approved_root=approved_root,
        relative_path=FRESH_AUTHORITY_FILENAME,
        max_bytes=_MAX_AUTHORITY_BYTES,
    )
    require_private_regular_file(
        authority_file.absolute_path,
        expected_device=authority_file.device,
        expected_inode=authority_file.inode,
    )
    value = _parse_authority(authority_file.content)
    packet_sha256 = cast(str, value["packetSha256"])
    packet_file = read_approved_regular_file(
        approved_root=approved_root,
        relative_path=f"bootstrap-{packet_sha256}.json",
        max_bytes=1 * 1024 * 1024,
    )
    require_private_regular_file(
        packet_file.absolute_path,
        expected_device=packet_file.device,
        expected_inode=packet_file.inode,
    )
    packet = validate_bootstrap_packet(
        packet_file.content,
        expected_sha256=packet_sha256,
    )
    _require_fresh_packet(packet)
    if authority_file.content != _authority_content(packet):
        raise LightGbmContractError("fresh bootstrap authority binding is invalid")
    return FreshBootstrapAuthority(
        content=authority_file.content,
        sha256=authority_file.content_sha256,
        packet=packet,
    )


def validate_fresh_bootstrap_execution_authority(
    *, approved_root: Path, packet: BootstrapPacket
) -> FreshBootstrapAuthority:
    """실행 packet이 root의 immutable selection과 정확히 같을 때만 provider handoff를 허용한다."""

    _require_fresh_packet(packet)
    selected = read_fresh_bootstrap_authority(approved_root=approved_root)
    if selected.packet.sha256 != packet.sha256 or selected.packet.content != packet.content:
        raise LightGbmContractError("fresh bootstrap packet does not match selected authority")
    return selected


def _authority_content(packet: BootstrapPacket) -> bytes:
    return canonical_json_bytes(
        {
            "authorityVersion": FRESH_AUTHORITY_VERSION,
            "packetSha256": packet.sha256,
            "packetVersion": packet.packet_version,
            "lineageMode": packet.lineage_mode,
            "calendarPolicyVersion": packet.calendar_policy_version,
            "calendarCorrectionSetSha256": packet.calendar_correction_set_sha256,
        }
    )


def _parse_authority(content: bytes) -> dict[str, object]:
    try:
        value = parse_bounded_json_bytes(
            content,
            limits=BoundedJsonLimits(
                max_bytes=_MAX_AUTHORITY_BYTES,
                max_depth=2,
                max_list_items=1,
                max_object_keys=6,
                max_text_codepoints=256,
                max_text_bytes=512,
                max_number_characters=8,
            ),
        )
    except BoundedJsonError as error:
        raise LightGbmContractError("fresh bootstrap authority JSON is invalid") from error
    expected_keys = {
        "authorityVersion",
        "packetSha256",
        "packetVersion",
        "lineageMode",
        "calendarPolicyVersion",
        "calendarCorrectionSetSha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or canonical_json_bytes(value) != content
        or value.get("authorityVersion") != FRESH_AUTHORITY_VERSION
        or value.get("packetVersion") != "s5-production-bootstrap-packet-v2"
        or value.get("lineageMode") != "FRESH"
    ):
        raise LightGbmContractError("fresh bootstrap authority is not canonical closed JSON")
    packet_sha256 = value.get("packetSha256")
    if (
        not isinstance(packet_sha256, str)
        or len(packet_sha256) != 64
        or any(character not in "0123456789abcdef" for character in packet_sha256)
    ):
        raise LightGbmContractError("fresh bootstrap authority packet SHA-256 is invalid")
    return cast(dict[str, object], value)


def _require_fresh_packet(packet: BootstrapPacket) -> None:
    if (
        packet.packet_version != "s5-production-bootstrap-packet-v2"
        or packet.lineage_mode != "FRESH"
        or packet.recovery_binding_sha256 is not None
        or packet.calendar_policy_version is None
        or packet.calendar_correction_set_sha256 is None
        or hashlib.sha256(packet.content).hexdigest() != packet.sha256
    ):
        raise LightGbmContractError("fresh bootstrap packet authority is invalid")


def _fixed_leaf_exists(root: Path, filename: str) -> bool:
    try:
        (root / filename).lstat()
    except FileNotFoundError:
        return False
    return True
