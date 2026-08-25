from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.rag.oa_release_manifest import (
    OA_TRACK_IDS,
    OaReleaseManifestError,
    canonical_release_digest,
    load_oa_release_manifest,
    validate_oa_release_manifest,
)


def _remove_required_roles(value: dict[str, object]) -> None:
    sources = value["sources"]
    assert isinstance(sources, list)
    for source in sources[:8]:
        assert isinstance(source, dict)
        source["curriculumRoles"] = ["PUBLIC_TEACHING_MATERIAL"]


def _released_manifest() -> dict[str, object]:
    sources: list[dict[str, object]] = []
    roles = (
        "PUBLIC_TEACHING_MATERIAL",
        "ORIGINAL_RESEARCH",
        "MODERN_REVIEW_REPLICATION_CORRECTION",
    )
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        slug = track_id.lower().replace("_", "-")
        for item_index in range(8):
            role = roles[item_index % len(roles)]
            source_suffix = f"{track_index:02d}_{item_index:02d}"
            sources.append(
                {
                    "canonicalUrl": f"https://ocw.mit.edu/courses/{slug}/resources/{source_suffix}/",
                    "curriculumRoles": [role],
                    "downloadUrl": f"https://ocw.mit.edu/courses/{slug}/resources/{source_suffix}/download.pdf",
                    "fallbackAllowed": False,
                    "localProcessingAllowed": True,
                    "machineFetchAllowed": True,
                    "qualityScore": 80 + (item_index % 5),
                    "rawContentSha256": f"{track_index:02x}{item_index:02x}" + "a" * 60,
                    "sourceId": f"src_oa_{source_suffix}_{slug.replace('-', '_')}",
                    "sourceRevisionId": f"srv_oa_{source_suffix}_{slug.replace('-', '_')}",
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
    return manifest


def test_released_manifest_requires_exact_tracks_roles_unique_sources_and_digest(
    tmp_path: Path,
) -> None:
    manifest = _released_manifest()
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    loaded = load_oa_release_manifest(path=path)

    assert loaded.manifest_id == "oa140_s4_7d_release_v1"
    assert loaded.source_count == 112
    assert loaded.release_digest == manifest["releaseDigest"]
    assert loaded.track_counts == dict.fromkeys(OA_TRACK_IDS, 8)


def test_tracked_oa112_release_manifest_is_default_install_candidate() -> None:
    loaded = load_oa_release_manifest()

    assert loaded.manifest_id == "oa140_s4_7d_release_v1"
    assert loaded.source_count == 112
    assert len(loaded.release_digest) == 64
    assert loaded.track_counts == dict.fromkeys(OA_TRACK_IDS, 8)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value.update({"releaseDigest": "0" * 64}),
            "release digest",
        ),
        (
            lambda value: value.update({"rawRedistributed": True}),
            "redistribution",
        ),
        (
            lambda value: value["tracks"][0].update({"trackId": OA_TRACK_IDS[1]}),  # type: ignore[index,union-attr]
            "track order",
        ),
        (
            lambda value: value["sources"][0].update(  # type: ignore[index,union-attr]
                {"downloadUrl": "https://127.0.0.1/private.pdf"}
            ),
            "public HTTPS",
        ),
        (
            lambda value: value["sources"][0].update(  # type: ignore[index,union-attr]
                {"fallbackAllowed": True}
            ),
            "fallback",
        ),
        (
            lambda value: value["sources"][0].update(  # type: ignore[index,union-attr]
                {"qualityScore": 79}
            ),
            "quality",
        ),
        (
            _remove_required_roles,
            "roles",
        ),
        (
            lambda value: value["sources"][1].update(  # type: ignore[index,union-attr]
                {"downloadUrl": value["sources"][0]["downloadUrl"]}  # type: ignore[index]
            ),
            "duplicate",
        ),
    ],
)
def test_released_manifest_rejects_unsafe_or_unverified_drift(
    mutate: object,
    message: str,
) -> None:
    manifest = _released_manifest()
    candidate = copy.deepcopy(manifest)
    assert callable(mutate)
    mutate(candidate)

    with pytest.raises(OaReleaseManifestError, match=message):
        validate_oa_release_manifest(candidate, require_released=True)
