"""Local-only operator CLI for packet-gated Finnhub/SEC/Fed foreign-news probes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from app.cross_market.foreign_news_evaluation_cli import (
    ForeignNewsEvaluationCliError,
    load_verified_selected_local_candidate,
)
from app.cross_market.foreign_news_evaluator import ForeignNewsLocalCandidate, ForeignNewsPrediction
from app.cross_market.foreign_news import ForeignNewsSentimentError, ForeignNewsSentimentMaterializer
from app.cross_market.foreign_news_provider_probe import (
    ForeignNewsProviderProbeError,
    ForeignNewsProviderProbeExecutionBinding,
    ForeignNewsProviderProbeExecutor,
    ForeignNewsProviderProbePacket,
    foreign_news_provider_credential_environment_variable,
    StdlibForeignNewsProviderProbeTransport,
)
from app.cross_market.foreign_news_repository import (
    ForeignNewsWriterAuthorityError,
    PostgresForeignNewsSentimentRepository,
)
from app.rag.oa112_downloader import Oa112DownloadError, _read_private_control_file


_CONTROL_ROOT_RELATIVE: Final[Path] = Path("capstone-rag/secrets/foreign-news-probes")
_EVIDENCE_FILE: Final[str] = "foreign-news-provider-probe-execution-evidence.v1.json"
_DEFAULT_PACKET_FILE: Final[str] = "foreign-news-provider-probe-approval.v1.json"
_OWNER_SCOPE_FILE: Final[str] = "foreign-news-provider-owner-scope.v1.json"
_WRITER_DSN_ENV: Final[str] = "DECISION_MARKET_WRITER_DATABASE_DSN"
_LEAF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_OWNER = re.compile(r"^[A-Za-z0-9._:-]{3,128}$")
_SYMBOL = re.compile(r"^[0-9A-Z._:-]{1,20}$")


@dataclass(frozen=True, slots=True)
class _SelectedLocalModelAnalyzer:
    """verified selected model이 raw text를 transient prediction으로만 소비하도록 좁힌 adapter다."""

    candidate: ForeignNewsLocalCandidate

    def analyze(self, *, lane_id: str, texts: tuple[str, ...]) -> None:
        """label/confidence는 input validation 뒤 즉시 폐기하며 output receipt에 포함하지 않는다."""

        if lane_id not in {"FINNHUB_PERSONAL_LOCAL", "SEC_OFFICIAL", "FED_OFFICIAL"} or not texts:
            raise ValueError("foreign-news analyzer input is invalid")
        for text in texts:
            prediction = self.candidate.classifier.predict(text)
            if not isinstance(prediction, ForeignNewsPrediction):
                raise ValueError("foreign-news analyzer output is invalid")


@dataclass(frozen=True, slots=True)
class _ForeignNewsOwnerScope:
    """trusted local control root가 제공하는 owner/symbol binding이다.

    Owner selector는 argv나 packet에 넣지 않는다. The market-writer process reads it only after the
    model gate and before the one physical provider attempt, then binds it to the fixed packet symbol.
    """

    owner_user_id: str
    symbol: str

    def __post_init__(self) -> None:
        if _OWNER.fullmatch(self.owner_user_id) is None or _SYMBOL.fullmatch(self.symbol) is None:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_INVALID")

    def require_packet(self, packet: ForeignNewsProviderProbePacket) -> None:
        """control scope가 다른 owner/symbol record로 provider result를 쓰지 못하게 한다."""

        if self.symbol != packet.symbol:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_PACKET_MISMATCH")


def main(argv: Sequence[str] | None = None) -> int:
    """exact packet/evidence/model/key가 모두 맞을 때만 one provider socket을 연다.

    Owner identity, raw text, key, query, provider body/header는 argv/stdout/local receipt에 넣지 않는다.
    `FOREIGN_NEWS_PROVIDER_USER_AGENT`는 SEC official origin policy에도 쓸 operator contact string이다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "execute":
        _emit("FOREIGN_NEWS_PROBE_COMMAND_INVALID", provider_physical_calls=0)
        return 2
    packet_name = _packet_name(arguments[1:])
    if packet_name is None:
        _emit("FOREIGN_NEWS_PROBE_ARGUMENT_INVALID", provider_physical_calls=0)
        return 2
    control_root = _repository_root() / _CONTROL_ROOT_RELATIVE
    now = datetime.now(UTC)
    try:
        packet = ForeignNewsProviderProbePacket.load_from_control_root(
            control_root=control_root,
            relative_path=packet_name,
            now=now,
        )
    except ForeignNewsProviderProbeError:
        _emit("FOREIGN_NEWS_PROBE_PACKET_UNAVAILABLE", provider_physical_calls=0)
        return 2
    try:
        candidate = load_verified_selected_local_candidate()
    except ForeignNewsEvaluationCliError:
        _emit("FOREIGN_NEWS_MODEL_NOT_VERIFIED", provider_physical_calls=0)
        return 2
    try:
        owner_scope = _load_owner_scope(control_root=control_root, packet=packet)
        database_dsn = os.environ.get(_WRITER_DSN_ENV, "").strip()
        if not database_dsn:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_WRITER_DATABASE_DSN_UNAVAILABLE")
        repository = PostgresForeignNewsSentimentRepository(database_dsn)
        # DB 권한 실패는 packet consume/provider socket보다 먼저 멈춰 single-use call을 낭비하지 않는다.
        repository.preflight()
        binding = _load_execution_binding(control_root=control_root, repository_root=_repository_root())
        credential_name = foreign_news_provider_credential_environment_variable(operation=packet.operation)
        api_key = os.environ.get(credential_name, "") if credential_name is not None else None
        result = ForeignNewsProviderProbeExecutor(
            control_root=control_root,
            transport=StdlibForeignNewsProviderProbeTransport(),
        ).execute(
            packet=packet,
            binding=binding,
            api_key=api_key,
            analyzer=_SelectedLocalModelAnalyzer(candidate=candidate),
            now=now,
            user_agent=os.environ.get("FOREIGN_NEWS_PROVIDER_USER_AGENT", ""),
        )
    except (ForeignNewsProviderProbeError, ForeignNewsWriterAuthorityError, ValueError) as error:
        code = (
            error.code
            if isinstance(error, ForeignNewsProviderProbeError)
            else "FOREIGN_NEWS_PROBE_WRITER_PREFLIGHT_FAILED"
        )
        _emit(
            code,
            provider_physical_calls=(
                error.physical_call_count
                if isinstance(error, ForeignNewsProviderProbeError)
                else 0
            ),
        )
        return 2
    try:
        record = ForeignNewsSentimentMaterializer().materialize(
            owner_user_id=owner_scope.owner_user_id,
            symbol=packet.symbol,
            as_of=result.receipt.started_at,
            aggregates=(result.aggregate,),
        )
        disposition = repository.append(record)
    except (ForeignNewsSentimentError, ForeignNewsWriterAuthorityError, ValueError):
        # receipt/claim은 already durable하다. 같은 packet을 재시도하지 않고 operator가 receipt를 보존한다.
        _emit(
            "FOREIGN_NEWS_PROBE_PERSISTENCE_FAILED",
            provider_physical_calls=result.receipt.physical_call_count,
        )
        return 2
    receipt = result.receipt
    _emit(
        "FOREIGN_NEWS_PROBE_EXECUTED",
        provider_physical_calls=receipt.physical_call_count,
        materialization_disposition=disposition,
        outcome=receipt.outcome,
        provider_family=receipt.provider_family,
        provider_status_class=receipt.provider_status_class,
    )
    return 0 if receipt.outcome == "SUCCESS" else 2


def _packet_name(arguments: tuple[str, ...]) -> str | None:
    if not arguments:
        return _DEFAULT_PACKET_FILE
    if len(arguments) != 2 or arguments[0] != "--packet" or _LEAF.fullmatch(arguments[1]) is None:
        return None
    return arguments[1]


def _load_owner_scope(
    *,
    control_root: Path,
    packet: ForeignNewsProviderProbePacket,
) -> _ForeignNewsOwnerScope:
    """0600 local owner scope를 load하고 packet symbol과 bind한다.

    This is a trusted local operator configuration, not a public API selector. It contains no provider
    credential, raw article data, or path supplied by the command line.
    """

    try:
        content = _read_private_control_file(
            root=control_root,
            name=_OWNER_SCOPE_FILE,
            maximum=8 * 1024,
            error_code="FOREIGN_NEWS_PROBE_OWNER_SCOPE_UNSAFE",
        )
    except Oa112DownloadError as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_UNAVAILABLE") from error
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_INVALID") from error
    expected = {"contractId", "ownerUserId", "schemaVersion", "symbol"}
    if (
        not isinstance(document, Mapping)
        or set(document) != expected
        or document.get("contractId") != "foreign-news-provider-owner-scope-v1"
        or document.get("schemaVersion") != 1
        or _canonical_bytes(document) != content
    ):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_INVALID")
    owner_user_id = document.get("ownerUserId")
    symbol = document.get("symbol")
    if not isinstance(owner_user_id, str) or not isinstance(symbol, str):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_OWNER_SCOPE_INVALID")
    scope = _ForeignNewsOwnerScope(owner_user_id=owner_user_id, symbol=symbol)
    scope.require_packet(packet)
    return scope


def _load_execution_binding(
    *,
    control_root: Path,
    repository_root: Path,
) -> ForeignNewsProviderProbeExecutionBinding:
    """private CI/security evidence와 actual clean Git object를 independently bind한다."""

    try:
        content = _read_private_control_file(
            root=control_root,
            name=_EVIDENCE_FILE,
            maximum=8 * 1024,
            error_code="FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_UNSAFE",
        )
    except Oa112DownloadError as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_UNAVAILABLE") from error
    document = _parse_canonical_evidence(content)
    binding = ForeignNewsProviderProbeExecutionBinding(
        ci_digest=_required_hash(document, "ciDigest"),
        head_sha=_required_head_sha(document, "headSha"),
        security_digest=_required_hash(document, "securityDigest"),
        tree_sha256=_required_hash(document, "treeSha256"),
    )
    head_sha, tree_sha256 = _current_clean_git_identity(repository_root)
    if binding.head_sha != head_sha or binding.tree_sha256 != tree_sha256:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_GIT_DRIFT")
    return binding


def _parse_canonical_evidence(content: bytes) -> Mapping[str, object]:
    try:
        document = json.loads(content.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_INVALID") from error
    if (
        not content
        or len(content) > 8 * 1024
        or not isinstance(document, Mapping)
        or set(document) != {"ciDigest", "headSha", "securityDigest", "treeSha256"}
        or _canonical_bytes(document) != content
    ):
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_INVALID")
    return document


def _current_clean_git_identity(repository_root: Path) -> tuple[str, str]:
    """ignored local cache/secret만 허용하고 tracked drift 중에는 provider transport를 금지한다."""

    if not repository_root.is_absolute():
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REPOSITORY_INVALID")
    try:
        status = subprocess.run(
            ["git", "-C", str(repository_root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status.stdout:
            raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_GIT_DIRTY")
        head_sha = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "tree", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REPOSITORY_UNAVAILABLE") from error
    if _HEAD_SHA.fullmatch(head_sha) is None or not isinstance(tree, bytes) or not tree:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_REPOSITORY_INVALID")
    return head_sha, hashlib.sha256(tree).hexdigest()


def _required_hash(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_INVALID")
    return value


def _required_head_sha(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or _HEAD_SHA.fullmatch(value) is None:
        raise ForeignNewsProviderProbeError("FOREIGN_NEWS_PROBE_EXECUTION_EVIDENCE_INVALID")
    return value


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _emit(
    code: object,
    *,
    provider_physical_calls: int,
    materialization_disposition: str | None = None,
    outcome: str | None = None,
    provider_family: str | None = None,
    provider_status_class: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "code": code if isinstance(code, str) else "FOREIGN_NEWS_PROBE_UNAVAILABLE",
        "providerPhysicalCalls": provider_physical_calls,
        "state": "COMPLETE" if outcome is not None else "FAILED",
    }
    if outcome is not None:
        payload["outcome"] = outcome
    if materialization_disposition is not None:
        payload["materializationDisposition"] = materialization_disposition
    if provider_family is not None:
        payload["providerFamily"] = provider_family
    if provider_status_class is not None:
        payload["providerStatusClass"] = provider_status_class
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
