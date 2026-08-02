from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from app.rag.oa_release_manifest import OA_TRACK_IDS, canonical_release_digest
from app.rag.oa_release_manifest_cli import main


def _write_manifest(path: Path) -> None:
    sources: list[dict[str, object]] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        slug = track_id.lower().replace("_", "-")
        for item_index in range(8):
            role = (
                "PUBLIC_TEACHING_MATERIAL",
                "ORIGINAL_RESEARCH",
                "MODERN_REVIEW_REPLICATION_CORRECTION",
            )[item_index % 3]
            sources.append(
                {
                    "canonicalUrl": f"https://ocw.mit.edu/courses/{slug}/resources/{item_index}/",
                    "curriculumRoles": [role],
                    "downloadUrl": (
                        f"https://ocw.mit.edu/courses/{slug}/resources/{item_index}/download.pdf"
                    ),
                    "fallbackAllowed": False,
                    "localProcessingAllowed": True,
                    "machineFetchAllowed": True,
                    "qualityScore": 88,
                    "rawContentSha256": hashlib.sha256(
                        f"{track_id}:{item_index}".encode()
                    ).hexdigest(),
                    "sourceId": f"src_oa_cli_{track_index:02d}_{item_index:02d}_{slug.replace('-', '_')}",
                    "sourceRevisionId": f"srv_oa_cli_{track_index:02d}_{item_index:02d}_{slug.replace('-', '_')}",
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


def test_manifest_cli_validates_without_network_or_raw_hash_output(
    tmp_path: Path,
    capsys: object,
) -> None:
    manifest_path = tmp_path / "oa-release.json"
    _write_manifest(manifest_path)

    assert main(["--manifest", str(manifest_path)]) == 0

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output == {
        "code": "OA_RELEASE_MANIFEST_VALID",
        "fetchHashes": False,
        "publicCorpusVersion": "exact30-v1+oa140_s4_7d_release_v1",
        "sourceCount": 112,
    }
    assert "rawContentSha256" not in json.dumps(output)


def test_operator_entrypoint_is_registered_for_release_validation() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        pyproject["project"]["scripts"]["rag-oa-release-validate"]
        == "app.rag.oa_release_manifest_cli:main"
    )


def test_tracked_remote_hash_receipt_matches_release_manifest_without_payloads() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    manifest = json.loads(
        (
            repository_root / "capstone-rag/manifests/s4-7d-oa140-release.v1.json"
        ).read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (
            repository_root / "capstone-rag/reports/s4-7d-oa140-remote-hash-receipt.v1.json"
        ).read_text(encoding="utf-8")
    )

    assert receipt["contractId"] == "rag-oa-remote-hash-receipt-v1"
    assert receipt["manifestId"] == manifest["manifestId"]
    assert receipt["manifestDigest"] == manifest["releaseDigest"]
    assert receipt["sourceCount"] == manifest["sourceCount"] == 112
    assert receipt["fetchPolicy"] == {
        "maxSourceBytes": 268435456,
        "redirectAllowed": False,
        "timeoutSeconds": 30.0,
    }
    expected = {
        item["sourceId"]: (item["downloadUrl"], item["rawContentSha256"])
        for item in manifest["sources"]
    }
    actual = {
        item["sourceId"]: (item["downloadUrl"], item["rawContentSha256"])
        for item in receipt["sources"]
    }
    assert actual == expected
    serialized = json.dumps(receipt, ensure_ascii=False)
    for forbidden in ("%PDF", "<!doctype", "providerPayload", "/home/", "C:\\"):
        assert forbidden not in serialized


def test_distribution_metadata_uses_same_manifest_digest_and_metadata_only_assets() -> None:
    repository_root = Path(__file__).resolve().parents[5]
    manifest = json.loads(
        (
            repository_root / "capstone-rag/manifests/s4-7d-oa140-release.v1.json"
        ).read_text(encoding="utf-8")
    )
    distribution = json.loads(
        (
            repository_root / "capstone-rag/manifests/s4-7d-oa140-distribution.v1.json"
        ).read_text(encoding="utf-8")
    )
    checksum_lines = (
        repository_root / "capstone-rag/manifests/s4-7d-oa140-checksums.sha256"
    ).read_text(encoding="utf-8").splitlines()

    assert distribution["contractId"] == "s4-7d-oa140-distribution/v1"
    assert distribution["manifestId"] == manifest["manifestId"]
    assert distribution["manifestDigest"] == manifest["releaseDigest"]
    assert distribution["sourceCount"] == 112
    assert distribution["rawRedistributed"] is False
    assert distribution["extractedTextRedistributed"] is False
    assert distribution["embeddingsRedistributed"] is False
    assert distribution["githubRelease"]["publicationStatus"] == (
        "READY_NOT_PUBLISHED_NO_CREDENTIAL"
    )
    assert distribution["huggingFaceDataset"]["publicationStatus"] == (
        "READY_NOT_PUBLISHED_NO_CREDENTIAL"
    )
    assert len(distribution["artifacts"]) == 3

    checksums = {}
    for line in checksum_lines:
        digest, path = line.split("  ", maxsplit=1)
        checksums[path] = digest
    for artifact in distribution["artifacts"]:
        path = artifact["path"]
        digest = hashlib.sha256((repository_root / path).read_bytes()).hexdigest()
        assert artifact["sha256"] == checksums[path] == digest
