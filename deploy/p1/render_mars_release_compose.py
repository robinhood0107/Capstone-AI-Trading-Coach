#!/usr/bin/env python3
"""Render an installable MARS Compose file pinned to verified registry digests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("demo", "full")
PARTS = ("api", "web", "postgres", "redis")
SHA = re.compile(r"^[0-9a-f]{40}$")
SEMVER_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-[0-9a-f]{12}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_LINE = re.compile(r"^(?P<prefix>\s*image:\s*)(?P<image>.+?)(?P<suffix>\s*(?:#.*)?)$", re.MULTILINE)


def render_compose(product: str, source: str, manifest: dict[str, Any]) -> str:
    if product not in PRODUCTS:
        raise ValueError("product")
    source_sha = manifest.get("sourceSha")
    tag = manifest.get("tag")
    if not isinstance(source_sha, str) or SHA.fullmatch(source_sha) is None:
        raise ValueError("sourceSha")
    if not isinstance(tag, str) or SEMVER_TAG.fullmatch(tag) is None or not tag.endswith(source_sha[:12]):
        raise ValueError("tag")
    images = manifest.get("images")
    if not isinstance(images, dict):
        raise ValueError("images")

    refs: dict[str, str] = {}
    for part in PARTS:
        identity = images.get(f"{product}-{part}")
        if not isinstance(identity, dict) or set(identity) != {"reference", "digest"}:
            raise ValueError(f"{product}-{part}")
        expected_ref = f"pjjpjj111/mars-{product}:{tag}-{part}"
        digest = identity["digest"]
        if identity["reference"] != expected_ref or not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ValueError(f"{product}-{part} identity")
        refs[part] = f"pjjpjj111/mars-{product}@{digest}"

    variable = f"MARS_{product.upper()}_TAG"
    registry_prefix = f"pjjpjj111/mars-{product}:"
    occurrences = {part: 0 for part in PARTS}

    def replace_image(match: re.Match[str]) -> str:
        image = match.group("image")
        if not image.startswith(registry_prefix):
            return match.group(0)
        suffix = image[len(registry_prefix) :]
        part = suffix.rsplit("-", 1)[-1]
        allowed_tags = {f"${{{variable}}}", f"${{{variable}:?set {variable}}}"}
        if part not in refs or suffix[: -(len(part) + 1)] not in allowed_tags:
            raise ValueError("unexpected product image reference")
        occurrences[part] += 1
        return f"{match.group('prefix')}{refs[part]}{match.group('suffix')}"

    rendered = IMAGE_LINE.sub(replace_image, source)
    if any(occurrences[part] == 0 for part in PARTS):
        raise ValueError("compose image inventory")
    image_lines = [line for line in rendered.splitlines() if re.match(r"^\s*image:", line)]
    if any("@sha256:" not in line for line in image_lines):
        raise ValueError("unpinned image reference")
    header = (
        f"# Generated from compose.public-{product}.yml for {tag} ({source_sha}).\n"
        "# Registry digests are the release identity; do not replace with moving tags.\n"
    )
    return header + rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=PRODUCTS, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_path = ROOT / "deploy" / "p1" / f"compose.public-{args.product}.yml"
    manifest = json.loads(args.images.read_text(encoding="utf-8"))
    rendered = render_compose(args.product, source_path.read_text(encoding="utf-8"), manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"MARS_RELEASE_COMPOSE=RENDERED product={args.product}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
