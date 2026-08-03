from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.rag.oa_release_manifest import (
    OaReleaseManifestError,
    load_oa_release_manifest,
)

def main(argv: Sequence[str] | None = None) -> int:
    """historical OA release manifest를 네트워크 없이 검증한다.

    이 entrypoint의 manifest는 현재 active OA112 registry가 아닌 byte-stable historical
    artifact다. 따라서 `--fetch-hashes`는 exact approval packet으로도 활성화할 수 없고,
    새 active downloader만 source-card rights evidence와 packet을 함께 검증해 physical call을
    만들 수 있다.
    """

    parser = argparse.ArgumentParser(description="Validate S4.7D OA release manifest.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fetch-hashes", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(tuple(sys.argv[1:] if argv is None else argv))

    try:
        release = load_oa_release_manifest(path=args.manifest)
    except OaReleaseManifestError as error:
        _emit({"code": "OA_RELEASE_MANIFEST_INVALID", "reason": str(error)})
        return 2

    if not args.fetch_hashes:
        _emit(
            {
                "code": "OA_RELEASE_MANIFEST_VALID",
                "fetchHashes": False,
                "publicCorpusVersion": release.public_corpus_version,
                "sourceCount": release.source_count,
            }
        )
        return 0
    _emit(
        {
            "code": "OA_RELEASE_HISTORICAL_FETCH_DISABLED",
            "fetchHashes": False,
            "state": "HISTORICAL_SUPERSEDED",
        }
    )
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
