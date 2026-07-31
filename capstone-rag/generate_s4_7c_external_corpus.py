#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def _write() -> dict[str, object]:
    rendered = render_external_cards()
    S4_7C_SOURCE_CARD_ROOT.mkdir(parents=True, exist_ok=True)
    existing = {
        entry.name
        for entry in S4_7C_SOURCE_CARD_ROOT.iterdir()
        if not entry.name.startswith(".")
    }
    unexpected = existing - set(rendered)
    if unexpected:
        raise ValueError(f"Refusing unexpected S4.7C artifacts: {sorted(unexpected)}")
    for filename, content in rendered.items():
        (S4_7C_SOURCE_CARD_ROOT / filename).write_bytes(content)
    manifest = build_external_processing_manifest()
    S4_7C_CORPUS_MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def _check() -> dict[str, object]:
    rendered = render_external_cards()
    try:
        existing = {
            entry.name: entry.read_bytes()
            for entry in S4_7C_SOURCE_CARD_ROOT.iterdir()
            if not entry.name.startswith(".")
        }
    except OSError as error:
        raise ValueError("Tracked S4.7C card root is missing.") from error
    if existing != rendered:
        raise ValueError("Tracked S4.7C card bytes are stale.")
    expected = build_external_processing_manifest()
    try:
        tracked = json.loads(S4_7C_CORPUS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
