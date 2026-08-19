"""실행 입력을 손으로 넣지 않고 저장된 실제 증거에서 유도한다.

S5 CLI들은 packet SHA와 code provenance를 환경변수로 받아왔다. 그 값들은 이미 approved root와
저장소 자체에 결정적으로 존재하므로, 사람이 옮겨 적는 단계는 오타와 세대 혼동만 만든다. 이
모듈은 그 값을 실제 증거에서 유도하고, 명시된 값이 있으면 유도값과 일치하는지 대조한다.

유도할 수 없거나 대조가 어긋나면 값을 지어내지 않고 fail-closed 한다. 운영자 승인이 필요한
값(root 경로, DSN, 수동 활성화 의도)은 여기서 유도하지 않는다.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from app.lightgbm.bootstrap_fresh_authority import (
    fresh_bootstrap_authority_exists,
    read_fresh_bootstrap_authority,
)
from app.lightgbm.bootstrap_journal import JOURNAL_FILENAME
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import S5_CALENDAR_CORRECTION_SET_SHA256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_PACKET_PREFIX = "bootstrap-"
_PACKET_SUFFIX = ".json"

# 실행되는 프로젝트의 lock 파일이 provenance 대상이다. 저장소 루트 lock과 혼동하지 않는다.
UV_LOCK_RELATIVE_PATH = "workspaces/decision-platform/python-services/uv.lock"


def resolve_bootstrap_packet_sha256(*, approved_root: Path) -> str:
    """approved root가 이미 확정한 실행 대상 packet SHA를 유도한다.

    fresh authority는 root당 정확히 하나의 packet만 선택하므로 모호성이 없다. recovery root에서는
    현재 correction 세대를 선언하고 아직 다른 recovery가 supersede 하지 않은 adopted packet,
    즉 체인의 head가 유일한 실행 대상이다.
    """

    explicit = os.environ.get("S5_BOOTSTRAP_PACKET_SHA256", "").strip()
    if explicit:
        _require_sha256(explicit, "bootstrap packet")
        return explicit

    if fresh_bootstrap_authority_exists(approved_root=approved_root):
        return read_fresh_bootstrap_authority(approved_root=approved_root).packet.sha256

    # 상한 변경으로 supersede하면 두 packet이 같은 세대를 선언하므로 세대 해시만으로는 부족하다.
    superseded = _superseded_priors(approved_root)
    return _chain_head(
        approved_root,
        [
            digest
            for digest, corrections in _adopted_packets(approved_root).items()
            if corrections == S5_CALENDAR_CORRECTION_SET_SHA256
            and digest not in superseded
        ],
    )


def resolve_recovery_prior_packet_sha256(*, approved_root: Path) -> str:
    """다음 recovery가 체인해야 할 prior packet을 유도한다.

    prior는 다른 recovery가 아직 supersede 하지 않은 소비 run, 즉 체인의 head다. 세대 교체와
    상한 변경 모두 head에서만 이어져야 하므로 세대 해시로 걸러내지 않는다. head가 하나가 아니면
    값을 고르지 않고 멈춘다.
    """

    superseded = _superseded_priors(approved_root)
    return _chain_head(
        approved_root,
        [
            digest
            for digest in _consumed_packets(approved_root)
            if digest not in superseded
        ],
    )


def _consumed_query_attempts(approved_root: Path, digest: str) -> Counter[str]:
    """journal이 기록한 물리 시도를 논리 query별 횟수로 읽는다. chunk는 열지 않는다.

    Ordinal은 세대마다 다시 붙으므로 신원으로 쓸 수 없다. 소비 증거의 본질은 어떤 query를 몇 번
    물리 호출했는지이며, 그 다중집합만이 형제 run 사이의 포함관계를 판정할 수 있다.
    """

    journal = approved_root / f"run-{digest}" / "source" / JOURNAL_FILENAME
    attempts: Counter[str] = Counter()
    try:
        with journal.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("state") == "INTENT":
                    continue
                ordinal = event.get("ordinal")
                query = event.get("querySha256")
                if not isinstance(ordinal, int) or not isinstance(query, str):
                    raise LightGbmContractError("bootstrap progress journal event is invalid")
                attempts[query] += 1
    except (OSError, ValueError) as error:
        raise LightGbmContractError("bootstrap progress journal is unreadable") from error
    return attempts


def _chain_head(approved_root: Path, candidates: list[str]) -> str:
    """소비 증거를 모두 포함하는 유일한 run을 체인 head로 고른다.

    더 적게 소비한 형제에서 이어가면 이미 소비한 호출이 누계에서 빠져 상한이 무의미해진다.
    포함관계가 성립하지 않거나 최대가 유일하지 않으면 값을 고르지 않고 멈춘다.
    """

    if not candidates:
        raise LightGbmContractError("bootstrap chain head is unavailable")
    if len(candidates) == 1:
        return candidates[0]
    consumed = {
        digest: _consumed_query_attempts(approved_root, digest) for digest in candidates
    }
    head = max(candidates, key=lambda digest: sum(consumed[digest].values()))
    total = sum(consumed[head].values())
    for digest in candidates:
        if digest == head:
            continue
        if sum(consumed[digest].values()) == total or any(
            count > consumed[head][query] for query, count in consumed[digest].items()
        ):
            raise LightGbmContractError(
                "bootstrap chain head is not unique in the approved root"
            )
    return head


def _consumed_packets(approved_root: Path) -> dict[str, str | None]:
    """journal이 존재하는 packet과 그 packet이 선언한 correction 세대를 모은다."""

    packets: dict[str, str | None] = {}
    for name in sorted(os.listdir(approved_root)):
        if not name.startswith(_PACKET_PREFIX) or not name.endswith(_PACKET_SUFFIX):
            continue
        digest = name[len(_PACKET_PREFIX) : -len(_PACKET_SUFFIX)]
        if not _SHA256.fullmatch(digest):
            continue
        if not (approved_root / f"run-{digest}" / "source" / JOURNAL_FILENAME).exists():
            continue
        packets[digest] = _declared_correction_set(approved_root / name)
    return packets


def _adopted_packets(approved_root: Path) -> dict[str, str | None]:
    """recovery adoption lineage가 봉인된 packet만 모은다."""

    return {
        digest: corrections
        for digest, corrections in _consumed_packets(approved_root).items()
        if (approved_root / f"run-{digest}" / "source" / "recovery-lineage.json").exists()
    }


def _superseded_priors(approved_root: Path) -> set[str]:
    """이미 다른 recovery가 prior로 소비한 packet 집합이다."""

    priors: set[str] = set()
    for name in sorted(os.listdir(approved_root)):
        if not name.startswith("run-"):
            continue
        lineage = approved_root / name / "source" / "recovery-lineage.json"
        if not lineage.exists():
            continue
        try:
            payload = json.loads(lineage.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LightGbmContractError("recovery lineage is unreadable") from error
        prior = payload.get("priorPacketSha256")
        if isinstance(prior, str):
            priors.add(prior)
    return priors


def _declared_correction_set(packet_path: Path) -> str | None:
    """packet이 선언한 correction 세대 해시를 읽는다. 전수 검증은 호출자가 이어서 한다."""

    try:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LightGbmContractError("bootstrap packet is unreadable") from error
    digest = payload.get("calendarCorrectionSetSha256")
    return digest if isinstance(digest, str) else None


def resolve_code_provenance(*, repository_root: Path) -> tuple[str, str, str]:
    """release manifest에 봉인할 code/lock provenance를 실제 저장소 상태에서 유도한다.

    명시된 환경변수가 있으면 유도값과 정확히 같아야 한다. 형식만 맞는 임의 값이 manifest에
    들어가는 경로를 닫는다.
    """

    head = _git_output(repository_root, "rev-parse", "HEAD")
    tree = _git_output(repository_root, "rev-parse", "HEAD^{tree}")
    _require_sha1(head, "code head")
    _require_sha1(tree, "code tree")

    lock_path = repository_root / UV_LOCK_RELATIVE_PATH
    try:
        lock_sha256 = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    except OSError as error:
        raise LightGbmContractError("uv lock provenance is unavailable") from error

    for name, derived, label in (
        ("S5_CODE_HEAD_SHA", head, "code head"),
        ("S5_CODE_TREE_SHA", tree, "code tree"),
        ("S5_UV_LOCK_SHA256", lock_sha256, "uv lock"),
    ):
        explicit = os.environ.get(name, "").strip()
        if explicit and explicit != derived:
            raise LightGbmContractError(f"{label} provenance does not match the repository")
    return head, tree, lock_sha256


def resolve_repository_root() -> Path:
    """실행 중인 패키지 위치에서 저장소 루트를 유도한다."""

    root = Path(__file__).resolve().parents[5]
    if not (root / ".git").exists() or not (root / UV_LOCK_RELATIVE_PATH).exists():
        raise LightGbmContractError("repository root for provenance is unavailable")
    return root


def _git_output(repository_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - 고정 인자만 쓰는 read-only git 조회다.
            ["git", "-C", str(repository_root), *args],
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LightGbmContractError("code provenance is unavailable") from error
    return completed.stdout.strip()


def _require_sha256(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise LightGbmContractError(f"{label} digest is invalid")


def _require_sha1(value: str, label: str) -> None:
    if not _SHA1.fullmatch(value):
        raise LightGbmContractError(f"{label} digest is invalid")
