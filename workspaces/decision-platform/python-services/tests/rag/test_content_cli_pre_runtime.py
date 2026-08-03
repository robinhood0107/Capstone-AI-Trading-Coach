from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.rag.content_cli import main
from app.rag.oa_release_manifest import OA_TRACK_IDS, canonical_release_digest


def _release_manifest(path: Path) -> None:
    sources: list[dict[str, object]] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        slug = track_id.lower().replace("_", "-")
        for item_index in range(8):
            role = (
                "PUBLIC_TEACHING_MATERIAL",
                "ORIGINAL_RESEARCH",
                "MODERN_REVIEW_REPLICATION_CORRECTION",
            )[item_index % 3]
            payload = f"{track_id}:{item_index}".encode()
            sources.append(
                {
                    "canonicalUrl": f"https://arxiv.org/abs/26{track_index:02d}.{item_index:05d}",
                    "curriculumRoles": [role],
                    "downloadUrl": f"https://arxiv.org/pdf/26{track_index:02d}.{item_index:05d}v1",
                    "fallbackAllowed": False,
                    "localProcessingAllowed": True,
                    "machineFetchAllowed": True,
                    "qualityScore": 84,
                    "rawContentSha256": hashlib.sha256(payload).hexdigest(),
                    "sourceId": f"src_oa_{track_index:02d}_{item_index:02d}_{slug.replace('-', '_')}",
                    "sourceRevisionId": f"srv_oa_{track_index:02d}_{item_index:02d}_{slug.replace('-', '_')}",
                    "trackId": track_id,
                }
            )
    manifest: dict[str, object] = {
        "contractId": "rag-oa-manifest-v1",
        "embeddingsRedistributed": False,
        "extractedTextRedistributed": False,
        "manifestId": "oa140_s4_7d_release_v1",
        "rawRedistributed": False,
        "releaseDigest": None,
        "releaseStatus": "RELEASED",
        "signedManifest": True,
        "sourceCount": len(sources),
        "sources": sources,
        "tracks": [
            {
                "maximumSources": 10,
                "minimumSources": 8,
                "sourceCount": 8,
                "trackId": track_id,
            }
            for track_id in OA_TRACK_IDS
        ],
    }
    manifest["releaseDigest"] = canonical_release_digest(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_status_with_tracked_release_manifest_is_core_ready_without_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.delenv("CAPSTONE_RAG_OA_MANIFEST_PATH", raising=False)

    assert main(["status"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "code": "OA_RELEASE_MANIFEST_AVAILABLE",
        "progressPercent": 0,
        "publicCorpusVersion": "exact30-v1+oa140_s4_7d_release_v1",
        "sourceCount": 112,
        "state": "CORE_READY",
    }
    assert str(tmp_path) not in json.dumps(value)


def test_status_without_release_manifest_remains_stable_setup_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("CAPSTONE_RAG_OA_MANIFEST_PATH", str(tmp_path / "missing.json"))

    assert main(["status"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "code": "CONTENT_SETUP_REQUIRED",
        "progressPercent": 0,
        "state": "BUILDING",
    }
    assert str(tmp_path) not in json.dumps(value)


def test_setup_verifies_oa_release_manifest_without_echoing_runtime_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "oa-release.json"
    _release_manifest(manifest_path)
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CAPSTONE_RAG_OA_MANIFEST_PATH", str(manifest_path))

    assert main(["setup"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "code": "OA_RELEASE_MANIFEST_VERIFIED",
        "progressPercent": 1,
        "publicCorpusVersion": "exact30-v1+oa140_s4_7d_release_v1",
        "sourceCount": 112,
        "state": "BUILDING",
    }
    serialized = json.dumps(value, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "rawContentSha256" not in serialized


def test_default_setup_uses_tracked_release_manifest_without_private_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CAPSTONE_RAG_LOCAL_ROOT", str(tmp_path))
    monkeypatch.delenv("CAPSTONE_RAG_OA_MANIFEST_PATH", raising=False)

    assert main(["setup"]) == 0

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "code": "OA_RELEASE_MANIFEST_VERIFIED",
        "progressPercent": 1,
        "publicCorpusVersion": "exact30-v1+oa140_s4_7d_release_v1",
        "sourceCount": 112,
        "state": "BUILDING",
    }
    assert str(tmp_path) not in json.dumps(value)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        (["import-cpu", "C:/Users/owner/private.pdf"], "CONTENT_COMMAND_INVALID"),
        (["import-intel-gpu", "C:/Users/owner/private.pdf"], "CONTENT_COMMAND_INVALID"),
        (["import-nvidia-gpu", "C:/Users/owner/private.pdf"], "CONTENT_COMMAND_INVALID"),
        (["import-auto", "C:/Users/owner/private.pdf"], "CONTENT_COMMAND_INVALID"),
        (["remove-document", "doc_owner_fixture_001"], "CONTENT_COMMAND_INVALID"),
        (["cache-clean"], "LOCAL_CACHE_CLEAN_UNAVAILABLE"),
    ],
)
def test_pre_runtime_commands_fail_closed_without_echoing_private_arguments(
    arguments: list[str],
    code: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(arguments) == 2

    value = json.loads(capsys.readouterr().out)
    assert value == {"code": code, "state": "FAILED"}
    assert "private.pdf" not in json.dumps(value)
