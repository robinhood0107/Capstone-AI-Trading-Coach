"""Provider-free CLI for inserting a verified neutral seed with the writer role."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.data.market_data.repository import stage_seed_archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stage a verified S5.7B market-data seed")
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    database_dsn = os.environ.get("MARKET_DATA_WRITER_DSN")
    if not database_dsn:
        parser.error("MARKET_DATA_WRITER_DSN is required")
    result = stage_seed_archive(
        database_dsn=database_dsn,
        archive_root=args.archive_root,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    print(
        json.dumps(
            {
                "bars": result.bars,
                "indices": result.indices,
                "macro": result.macro,
                "manifestSha256": result.manifest_sha256,
                "outcome": result.outcome,
                "providerCalls": result.provider_calls,
                "universes": result.universes,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0
