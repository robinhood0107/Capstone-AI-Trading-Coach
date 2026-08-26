from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.release.public_rag_seed import (
    PublicRagSeedError,
    export_public_seed,
    import_public_seed,
    manifest_summary,
    verify_seed_parts,
)

_DSN_ENV = "P1_SEED_DATABASE_DSN"


def main() -> None:
    parser = argparse.ArgumentParser(description="P1 public RAG Seed export/import verifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output-dir", type=Path, required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--manifest", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)

    arguments = parser.parse_args()
    try:
        if arguments.command == "export":
            manifest = export_public_seed(
                database_dsn=_database_dsn(),
                output_dir=arguments.output_dir,
            )
            result = {"status": "EXPORTED", **manifest_summary(manifest)}
        elif arguments.command == "import":
            manifest = verify_seed_parts(manifest_path=arguments.manifest)
            status = import_public_seed(
                database_dsn=_database_dsn(), manifest_path=arguments.manifest
            )
            result = {"status": status, **manifest_summary(manifest)}
        else:
            manifest = verify_seed_parts(manifest_path=arguments.manifest)
            result = {"status": "VERIFIED", **manifest_summary(manifest)}
    except PublicRagSeedError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


def _database_dsn() -> str:
    value = os.environ.get(_DSN_ENV, "")
    if not value:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_DSN")
    return value


if __name__ == "__main__":
    main()
