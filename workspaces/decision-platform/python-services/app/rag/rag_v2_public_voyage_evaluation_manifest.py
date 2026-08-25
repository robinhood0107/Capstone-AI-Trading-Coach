"""Prepare ignored, corpus-bound Voyage evaluation manifests without provider access.

The tracked exact-30 smoke fixture remains byte-stable.  This module projects its ten questions onto
the current external-processing corpus digest and derives one title-bound question per OA112 source.
Only the ignored local corpus receives these inputs; no provider socket, credential, vector, or raw
document is used or persisted here.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.rag.benchmark_receipt_io import BenchmarkReceiptIoError, write_benchmark_receipt
from app.rag.oa112_active_registry import Oa112ActiveRegistry

_REPO_ROOT = Path(__file__).resolve().parents[5]
_TRACKED_EXACT30_FIXTURE = _REPO_ROOT / "capstone-rag/eval/s4-2b-30-card-smoke.v1.json"
_INPUT_DIRECTORY = "evaluation-inputs"
_EXACT30_FILENAME = "exact30-evaluation-manifest.v1.json"
_OA112_FILENAME = "oa112-evaluation-manifest.v1.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PublicVoyageEvaluationManifestError(ValueError):
    """Local evaluation inputs could not be projected without changing their frozen membership."""


@dataclass(frozen=True, slots=True)
class PublicVoyageEvaluationManifestPreparation:
    """Content-free identities of the two local evaluation input files."""

    exact30_file_sha256: str
    oa112_file_sha256: str
    exact30_query_count: int
    oa112_query_count: int


def prepare_public_voyage_evaluation_manifests(
    *,
    local_root: Path,
    exact30_source_card_corpus_manifest_sha256: str,
    exact30_source_ids: Sequence[str],
    registry: Oa112ActiveRegistry,
) -> PublicVoyageEvaluationManifestPreparation:
    """Write deterministic 10+112 ignored manifests bound to the current public source identities.

    OA questions use title and author metadata, never the expected source ID, so the evaluator cannot
    pass merely by echoing its gold label.  Re-running with identical inputs atomically reproduces the
    same bytes and performs zero provider calls.
    """

    source_ids = tuple(exact30_source_ids)
    if (
        not local_root.is_absolute()
        or ".." in local_root.parts
        or _SHA256.fullmatch(exact30_source_card_corpus_manifest_sha256) is None
        or len(source_ids) != 30
        or len(set(source_ids)) != 30
        or not isinstance(registry, Oa112ActiveRegistry)
        or registry.active_source_count != 112
    ):
        raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_EVALUATION_MANIFEST_CONTEXT")
    exact_payload = _exact30_projection(
        corpus_manifest_sha256=exact30_source_card_corpus_manifest_sha256,
        source_ids=frozenset(source_ids),
    )
    oa_payload = _oa112_projection(registry)
    try:
        exact_write = write_benchmark_receipt(
            approved_root=local_root,
            relative_directory=_INPUT_DIRECTORY,
            filename=_EXACT30_FILENAME,
            payload=_canonical_json(exact_payload),
        )
        oa_write = write_benchmark_receipt(
            approved_root=local_root,
            relative_directory=_INPUT_DIRECTORY,
            filename=_OA112_FILENAME,
            payload=_canonical_json(oa_payload),
        )
    except BenchmarkReceiptIoError as error:
        raise PublicVoyageEvaluationManifestError(
            "PUBLIC_VOYAGE_EVALUATION_MANIFEST_WRITE"
        ) from error
    return PublicVoyageEvaluationManifestPreparation(
        exact30_file_sha256=exact_write.payload_sha256,
        oa112_file_sha256=oa_write.payload_sha256,
        exact30_query_count=10,
        oa112_query_count=112,
    )


def public_voyage_evaluation_input_root(local_root: Path) -> Path:
    """Return the fixed ignored input directory; caller-controlled path selectors are not accepted."""

    if not local_root.is_absolute() or ".." in local_root.parts:
        raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_EVALUATION_MANIFEST_CONTEXT")
    return local_root / _INPUT_DIRECTORY


def _exact30_projection(
    *, corpus_manifest_sha256: str, source_ids: frozenset[str]
) -> dict[str, object]:
    payload = _read_tracked_fixture()
    raw_queries = payload.get("queries")
    if (
        set(payload) != {"corpusManifestSha256", "datasetId", "queries", "schemaVersion"}
        or payload.get("datasetId") != "s4-2b-30-card-smoke/v1"
        or payload.get("schemaVersion") != "s4-2b-30-card-smoke/v1"
        or not isinstance(raw_queries, list)
        or len(raw_queries) != 10
    ):
        raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_EXACT30_FIXTURE_INVALID")
    copied_queries: list[dict[str, object]] = []
    for index, item in enumerate(raw_queries, start=1):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"expectedSourceIds", "id", "text"}
            or item.get("id") != f"q{index:02d}"
            or not isinstance(item.get("text"), str)
            or not isinstance(item.get("expectedSourceIds"), list)
            or len(item["expectedSourceIds"]) != 1
            or item["expectedSourceIds"][0] not in source_ids
        ):
            raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_EXACT30_FIXTURE_INVALID")
        copied_queries.append(
            {
                "expectedSourceIds": list(item["expectedSourceIds"]),
                "id": item["id"],
                "text": item["text"],
            }
        )
    return {
        "corpusManifestSha256": corpus_manifest_sha256,
        "datasetId": "s4-2b-30-card-smoke/v1",
        "queries": copied_queries,
        "schemaVersion": "s4-2b-30-card-smoke/v1",
    }


def _oa112_projection(registry: Oa112ActiveRegistry) -> dict[str, object]:
    queries: list[dict[str, object]] = []
    for index, entry in enumerate(registry.active_entries, start=1):
        authors = entry.source_card.get("authors")
        author_label = (
            authors[0]
            if isinstance(authors, list) and authors and isinstance(authors[0], str)
            else "the listed authors"
        )
        question = (
            f'What methodology, assumptions, evidence, and limitations are presented in "{entry.title}" '
            f"by {author_label}?"
        )
        if entry.source_id in question:
            raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_OA112_FIXTURE_INVALID")
        queries.append(
            {
                "expectedSourceId": entry.source_id,
                "id": f"oa112-q{index:03d}",
                "question": question,
                "topics": list(entry.retrieval_topics),
                "trackId": entry.track_id,
            }
        )
    payload: dict[str, object] = {
        "contractId": "rag-v2-oa112-evaluation-manifest-v1",
        "evaluationManifestDigest": None,
        "queries": queries,
        "queryCount": 112,
        "registryDigest": registry.registry_digest,
        "schemaVersion": 1,
    }
    payload["evaluationManifestDigest"] = _manifest_digest(payload)
    return payload


def _read_tracked_fixture() -> dict[str, object]:
    try:
        metadata = _TRACKED_EXACT30_FIXTURE.lstat()
        raw = _TRACKED_EXACT30_FIXTURE.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicVoyageEvaluationManifestError(
            "PUBLIC_VOYAGE_EXACT30_FIXTURE_INVALID"
        ) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= 512 * 1024
        or metadata.st_size != len(raw)
        or not isinstance(value, dict)
    ):
        raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_EXACT30_FIXTURE_INVALID")
    return value


def _manifest_digest(payload: Mapping[str, object]) -> str:
    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(detached, dict):
        raise PublicVoyageEvaluationManifestError("PUBLIC_VOYAGE_OA112_FIXTURE_INVALID")
    detached["evaluationManifestDigest"] = None
    return hashlib.sha256(
        json.dumps(detached, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )
