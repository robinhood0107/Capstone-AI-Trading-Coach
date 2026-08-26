from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "deploy/p1/full-app-release-manifest.v2.schema.json"
CATALOG_PATH = ROOT / "contracts/catalogs/p1-full-app-release-contract.v2.json"
MAX_JSON_BYTES = 4 * 1024 * 1024


class ReleaseManifestValidationError(ValueError):
    pass


def _load_regular_json(path: Path) -> Mapping[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReleaseManifestValidationError(f"MISSING_REGULAR_JSON:{path}") from error
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_JSON_BYTES:
        raise ReleaseManifestValidationError(f"INVALID_REGULAR_JSON:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseManifestValidationError(f"INVALID_JSON:{path}") from error
    if not isinstance(payload, Mapping):
        raise ReleaseManifestValidationError(f"JSON_OBJECT_REQUIRED:{path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_repo_file(root: Path, relative: str) -> Path | None:
    candidate = root / relative
    try:
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
        metadata = candidate.lstat()
    except OSError:
        return None
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError:
        return None
    if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        return None
    return resolved


def _git_output(root: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def semantic_errors(payload: Mapping[str, Any], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    catalog = _load_regular_json(root / CATALOG_PATH.relative_to(ROOT))

    images = payload.get("images")
    if isinstance(images, Sequence) and not isinstance(images, (str, bytes)):
        components = [item.get("component") for item in images if isinstance(item, Mapping)]
        if len(components) != len(set(components)):
            errors.append("DUPLICATE_IMAGE_COMPONENT")
        required = set(catalog["requiredImageComponents"])
        optional = set(catalog["optionalImageComponents"])
        observed = set(components)
        if not required.issubset(observed) or observed - required - optional:
            errors.append("IMAGE_COMPONENT_SET")
        for item in images:
            if not isinstance(item, Mapping):
                continue
            reference = item.get("reference")
            digest = item.get("digest")
            if not isinstance(reference, str) or not isinstance(digest, str) or not reference.endswith(f"@{digest}"):
                errors.append(f"IMAGE_REFERENCE_DIGEST_MISMATCH:{item.get('component', 'UNKNOWN')}")

    model_assets = payload.get("modelAssets")
    if isinstance(model_assets, Sequence) and not isinstance(model_assets, (str, bytes)):
        model_components = [item.get("component") for item in model_assets if isinstance(item, Mapping)]
        if len(model_components) != len(set(model_components)):
            errors.append("DUPLICATE_MODEL_COMPONENT")
        expected_models = catalog["modelAssets"]
        if set(model_components) != set(expected_models):
            errors.append("MODEL_COMPONENT_SET")
        for item in model_assets:
            if not isinstance(item, Mapping):
                continue
            expected = expected_models.get(item.get("component"))
            if expected is not None and item.get("revision") != expected["revision"]:
                errors.append(f"MODEL_REVISION_MISMATCH:{item.get('component')}")

    seed = payload.get("publicRagSeed")
    if isinstance(seed, Mapping):
        manifest_relative = seed.get("manifestPath")
        manifest_path = _regular_repo_file(root, manifest_relative) if isinstance(manifest_relative, str) else None
        if manifest_path is None:
            errors.append("PUBLIC_RAG_SEED_MANIFEST_BOUNDARY")
        else:
            if _sha256(manifest_path) != seed.get("manifestSha256"):
                errors.append("PUBLIC_RAG_SEED_MANIFEST_HASH")
            try:
                seed_manifest = _load_regular_json(manifest_path)
            except ReleaseManifestValidationError:
                errors.append("PUBLIC_RAG_SEED_MANIFEST_JSON")
            else:
                seed_pairs = (
                    ("archiveSha256", "archiveSha256"),
                    ("sourceFlywaySchemaVersion", "sourceSchemaVersion"),
                    ("targetFlywaySchemaVersion", "targetSchemaVersion"),
                    ("expectedSourceCount", "sources"),
                    ("expectedChunkCount", "chunks"),
                    ("embeddingDimension", "dimensions"),
                )
                for source_key, release_key in seed_pairs:
                    if seed_manifest.get(source_key) != seed.get(release_key):
                        errors.append(f"PUBLIC_RAG_SEED_BINDING:{release_key}")
                manifest_parts = seed_manifest.get("parts")
                release_parts = seed.get("parts")
                if not isinstance(manifest_parts, list) or not isinstance(release_parts, list) or len(manifest_parts) != len(release_parts):
                    errors.append("PUBLIC_RAG_SEED_PART_COUNT")
                else:
                    for manifest_part, release_part in zip(manifest_parts, release_parts, strict=True):
                        expected_name = Path(str(release_part.get("path", ""))).name
                        if (
                            manifest_part.get("file") != expected_name
                            or manifest_part.get("sizeBytes") != release_part.get("size")
                            or manifest_part.get("sha256") != release_part.get("sha256")
                        ):
                            errors.append(f"PUBLIC_RAG_SEED_PART_BINDING:{expected_name or 'UNKNOWN'}")
                        part_path = _regular_repo_file(root, str(release_part.get("path", "")))
                        if part_path is None:
                            errors.append(f"PUBLIC_RAG_SEED_PART_BOUNDARY:{expected_name or 'UNKNOWN'}")
                        elif part_path.stat().st_size != release_part.get("size") or _sha256(part_path) != release_part.get("sha256"):
                            errors.append(f"PUBLIC_RAG_SEED_PART_HASH:{expected_name or 'UNKNOWN'}")

    supply_chain = payload.get("supplyChain")
    license_path = _regular_repo_file(root, "LICENSE")
    if not isinstance(supply_chain, Mapping) or license_path is None or _sha256(license_path) != supply_chain.get("licenseSha256"):
        errors.append("LICENSE_HASH_BINDING")

    head = _git_output(root, "rev-parse", "HEAD")
    tree = _git_output(root, "rev-parse", "HEAD^{tree}")
    if head is None or payload.get("commitSha") != head:
        errors.append("COMMIT_SHA_BINDING")
    if tree is None or payload.get("treeSha") != tree:
        errors.append("TREE_SHA_BINDING")
    return errors


def validate_manifest(path: Path, root: Path = ROOT) -> list[str]:
    try:
        payload = _load_regular_json(path)
        schema = _load_regular_json(root / SCHEMA_PATH.relative_to(ROOT))
        schema_errors = sorted(
            Draft202012Validator(schema).iter_errors(payload),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        errors = [f"SCHEMA:{'/'.join(str(item) for item in error.absolute_path)}:{error.message}" for error in schema_errors]
        if not errors:
            errors.extend(semantic_errors(payload, root))
        return errors
    except ReleaseManifestValidationError as error:
        return [str(error)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a P1 full-app release manifest against repository evidence.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    errors = validate_manifest(arguments.manifest.absolute(), arguments.root.absolute())
    if errors:
        for error in errors:
            print(f"P1_FULL_APP_MANIFEST_ERROR={error}", file=sys.stderr)
        return 1
    print("P1_FULL_APP_MANIFEST=VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
