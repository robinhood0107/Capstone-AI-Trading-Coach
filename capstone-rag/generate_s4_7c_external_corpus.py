#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPSTONE_RAG_ROOT = Path(__file__).resolve().parent
PYTHON_SERVICE_ROOT = REPO_ROOT / "workspaces/decision-platform/python-services"
if str(PYTHON_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE_ROOT))

from app.rag.external_processing_corpus import (  # noqa: E402
    S4_7C_CORPUS_MANIFEST_PATH,
    S4_7C_SOURCE_CARD_ROOT,
    build_external_processing_manifest,
)
from app.rag.source_card_corpus import load_frozen_source_card_corpus  # noqa: E402
from app.rag.source_card_v2_contract import validate_source_card_v2_payload  # noqa: E402
from app.rag.safe_io import (  # noqa: E402
    list_approved_regular_files,
    read_approved_regular_file,
    write_approved_generated_file,
)

_MAX_CARD_BYTES = 2 * 1024 * 1024
_MAX_CARD_ENTRIES = 30
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024

_EXTERNAL_LICENSE_NOTE = (
    "approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731. "
    "project-authored sanitized claim과 public locator/attribution만 external processing 대상으로 "
    "검토했다. upstream PDF, HTML, screenshot, API response, provider payload, raw/reference evidence는 "
    "복제하거나 전송하지 않는다. 사용자 동의와 third-party restriction review를 각각 확인했다."
)


def render_external_cards() -> dict[str, bytes]:
    """old exact-30 body를 바꾸지 않고 external consent front matter만 versioning한다."""

    rendered: dict[str, bytes] = {}
    for card in load_frozen_source_card_corpus().cards:
        payload = dict(card.front_matter)
        payload["licenseNote"] = _EXTERNAL_LICENSE_NOTE
        payload["externalProcessingAllowed"] = True
        payload["externalProcessingGate"] = "LICENSE_AND_CONSENT_VERIFIED"
        validate_source_card_v2_payload(payload)
        front_matter = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )
        text = unicodedata.normalize(
            "NFC", f"---\n{front_matter}---\n{card.canonical_body}"
        )
        rendered[f"{card.source_id}.md"] = text.encode("utf-8")
    if len(rendered) != 30:
        raise ValueError("S4.7C generator requires exact 30 cards.")
    return rendered


def _artifact_relative_path(path: Path) -> str:
    """external corpus artifact는 owned `capstone-rag` subtree 안에서만 생성한다."""

    try:
        return path.relative_to(CAPSTONE_RAG_ROOT).as_posix()
    except ValueError as error:
        raise ValueError("S4.7C artifact path escapes the approved capstone-rag root.") from error


def _list_cards() -> dict[str, bytes]:
    return list_approved_regular_files(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_directory=_artifact_relative_path(S4_7C_SOURCE_CARD_ROOT),
        max_entries=_MAX_CARD_ENTRIES,
        max_bytes=_MAX_CARD_BYTES,
    )


def _read_manifest() -> bytes:
    return read_approved_regular_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(S4_7C_CORPUS_MANIFEST_PATH),
        max_bytes=_MAX_MANIFEST_BYTES,
    ).content


def _write_artifact(path: Path, content: bytes, *, max_bytes: int) -> None:
    write_approved_generated_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(path),
        content=content,
        max_bytes=max_bytes,
    )


def _write() -> dict[str, object]:
    rendered = render_external_cards()
    existing = set(_list_cards())
    unexpected = existing - set(rendered)
    if unexpected:
        raise ValueError(f"Refusing unexpected S4.7C artifacts: {sorted(unexpected)}")
    for filename, content in rendered.items():
        _write_artifact(
            S4_7C_SOURCE_CARD_ROOT / filename,
            content,
            max_bytes=_MAX_CARD_BYTES,
        )
    manifest = build_external_processing_manifest()
    _write_artifact(
        S4_7C_CORPUS_MANIFEST_PATH,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    return manifest


def _check() -> dict[str, object]:
    rendered = render_external_cards()
    try:
        existing = _list_cards()
    except ValueError as error:
        raise ValueError("Tracked S4.7C card root is missing.") from error
    if existing != rendered:
        raise ValueError("Tracked S4.7C card bytes are stale.")
    expected = build_external_processing_manifest()
    try:
        tracked = json.loads(_read_manifest().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Tracked S4.7C manifest is missing or invalid.") from error
    if tracked != expected:
        raise ValueError("Tracked S4.7C manifest is stale.")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify S4.7C external corpus."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        manifest = _write() if args.write else _check()
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S4_7C_EXTERNAL_PROCESSING_CORPUS_VERIFIED")
    print(f"externalProcessingCards={manifest['externalProcessingCardCount']}")
    print(f"corpusManifestSha256={manifest['corpusManifestSha256']}")
    print("providerPhysicalCalls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
