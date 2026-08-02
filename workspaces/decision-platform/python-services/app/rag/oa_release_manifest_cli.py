from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from email.message import Message
from pathlib import Path
from typing import Final, IO

from app.rag.oa_release_manifest import (
    MAX_OA_MANIFEST_BYTES,
    OaReleaseManifestError,
    load_oa_release_manifest,
)

MAX_SOURCE_BYTES: Final[int] = 256 * 1024 * 1024
FETCH_TIMEOUT_SECONDS: Final[float] = 30.0
FETCH_PAUSE_SECONDS: Final[float] = 3.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        raise urllib.error.HTTPError(req.full_url, code, "redirect forbidden", headers, fp)


def main(argv: Sequence[str] | None = None) -> int:
    """OA release manifest를 네트워크 0 기본 모드 또는 explicit hash-fetch 모드로 검증한다."""

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
    if args.receipt is None:
        _emit({"code": "OA_RELEASE_RECEIPT_REQUIRED", "state": "FAILED"})
        return 2
    try:
        receipt = _fetch_hash_receipt(args.manifest)
        _write_receipt(args.receipt, receipt)
    except OaReleaseManifestError as error:
        _emit({"code": "OA_RELEASE_REMOTE_HASH_FAILED", "reason": str(error)})
        return 2
    _emit(
        {
            "code": "OA_RELEASE_REMOTE_HASH_VERIFIED",
            "fetchHashes": True,
            "publicCorpusVersion": release.public_corpus_version,
            "sourceCount": release.source_count,
        }
    )
    return 0


def _fetch_hash_receipt(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise OaReleaseManifestError("OA release manifest must be an object")
    opener = urllib.request.build_opener(_NoRedirect)
    receipts: list[dict[str, object]] = []
    for index, source in enumerate(manifest["sources"]):
        if not isinstance(source, dict):
            raise OaReleaseManifestError("OA release source must be an object")
        if index:
            time.sleep(FETCH_PAUSE_SECONDS)
        observed = _download_sha256(opener, str(source["downloadUrl"]))
        expected = source["rawContentSha256"]
        if observed["sha256"] != expected:
            raise OaReleaseManifestError(f"raw hash mismatch for {source['sourceId']}")
        receipts.append(
            {
                "bytes": observed["bytes"],
                "downloadUrl": source["downloadUrl"],
                "rawContentSha256": observed["sha256"],
                "sourceId": source["sourceId"],
                "sourceRevisionId": source["sourceRevisionId"],
            }
        )
    return {
        "contractId": "rag-oa-remote-hash-receipt-v1",
        "fetchPolicy": {
            "maxSourceBytes": MAX_SOURCE_BYTES,
            "redirectAllowed": False,
            "timeoutSeconds": FETCH_TIMEOUT_SECONDS,
        },
        "manifestDigest": manifest["releaseDigest"],
        "manifestId": manifest["manifestId"],
        "sourceCount": len(receipts),
        "sources": receipts,
    }


def _download_sha256(
    opener: urllib.request.OpenerDirector,
    url: str,
) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": "capstone-rag-oa-verifier/1"})
    digest = hashlib.sha256()
    total = 0
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise OaReleaseManifestError("OA source exceeds byte limit")
                digest.update(chunk)
    except (OaReleaseManifestError, urllib.error.URLError, TimeoutError) as error:
        if isinstance(error, OaReleaseManifestError):
            raise
        raise OaReleaseManifestError("OA source fetch failed") from error
    return {"bytes": total, "sha256": digest.hexdigest()}


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_OA_MANIFEST_BYTES:
        raise OaReleaseManifestError("OA release receipt exceeds byte limit")
    if path.name in {"", ".", ".."} or "\x00" in path.name:
        raise OaReleaseManifestError("OA release receipt path is unsafe")
    parent = path.parent
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(parent, directory_flags)
    except OSError as error:
        raise OaReleaseManifestError("OA release receipt parent is unsafe") from error
    try:
        try:
            fd = os.open(path.name, flags, 0o644, dir_fd=directory_fd)
        except OSError as error:
            raise OaReleaseManifestError("OA release receipt already exists or is unsafe") from error
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
