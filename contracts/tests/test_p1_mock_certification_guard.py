from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "deploy/p1/mock_certification_guard.py"


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_mock_certification_guard", GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("mock certification guard cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _canonical(value: object) -> bytes:
    """서비스가 쓰는 canonical form과 같이 끝에 개행을 포함한다."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


class MockCertificationGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        _git(self.repository, "init")
        _git(self.repository, "config", "user.name", "Certification Test")
        _git(self.repository, "config", "user.email", "certification@example.invalid")
        (self.repository / "tracked.txt").write_text("certified\n", encoding="utf-8")
        _git(self.repository, "add", "tracked.txt")
        _git(self.repository, "commit", "-m", "certified source")
        self.certified_commit = _git(self.repository, "rev-parse", "HEAD")
        self.request_path = self.root / "request.json"
        self.receipt_path = self.root / "receipt.json"
        self.request = {
            "branch": "feature/p1-full-app-v2",
            "commitSha": self.certified_commit,
            "pullRequest": 163,
            "quantity": 1,
            "requiredChecks": sorted(GUARD._REQUIRED_CHECKS),
            "securityEvidenceDigest": "a" * 64,
            "symbol": "005930",
        }
        request_bytes = _canonical(self.request)
        self.request_path.write_bytes(request_bytes)
        self.request_path.chmod(0o600)
        self.receipt = {
            "commitSha": self.certified_commit,
            "inputSha256": hashlib.sha256(request_bytes).hexdigest(),
            "physicalCalls": {"brokerage": 7, "quote": 1, "token": 1},
            "status": "PASS",
            "timestamp": "2026-08-26T06:00:00Z",
        }
        self._write_receipt()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_receipt(self) -> None:
        self.receipt_path.write_bytes(_canonical(self.receipt))
        self.receipt_path.chmod(0o600)

    def _verify(self) -> None:
        GUARD.verify_mock_certification(
            self.repository,
            self.request_path,
            self.receipt_path,
            now=datetime(2026, 8, 26, 7, 0, tzinfo=UTC),
        )

    def test_exact_certified_tree_and_clean_checkout_pass(self) -> None:
        self._verify()

    def test_new_commit_with_the_same_tree_passes(self) -> None:
        _git(self.repository, "commit", "--allow-empty", "-m", "main merge identity")
        self.assertNotEqual(self.certified_commit, _git(self.repository, "rev-parse", "HEAD"))
        self._verify()

    def test_dirty_worktree_no_longer_blocks(self) -> None:
        """worktree가 더러워도 영수증은 유효하다.

        e2e 러너들이 git에 추적되는 판정표 JSON을 갱신하므로, clean worktree를 요구하면
        인증을 받은 직후 e2e를 한 번만 돌려도 그 인증이 무효가 됐다. 영수증이 말하는 것은
        "그 시각 KIS 모의 원장에서 왕복이 일어났다"이고 그 사실은 worktree 상태와 무관하다.
        """

        (self.repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        self._verify()

    def test_committed_source_drift_no_longer_blocks(self) -> None:
        """인증 뒤 커밋이 더 쌓여도 영수증은 유효하다.

        영수증에 남는 commitSha는 "어느 코드에서 왕복을 받았는지"를 가리키는 꼬리표로
        계속 남는다. 그 커밋이 최신 HEAD여야 한다는 요구만 뺐다.
        """

        (self.repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        _git(self.repository, "add", "tracked.txt")
        _git(self.repository, "commit", "-m", "source drift")
        self._verify()

    def test_forged_request_or_receipt_link_is_rejected(self) -> None:
        self.receipt["inputSha256"] = "b" * 64
        self._write_receipt()
        with self.assertRaisesRegex(
            GUARD.MockCertificationGuardError,
            "KIS_MOCK_CERTIFICATION_RECEIPT_INVALID",
        ):
            self._verify()

    def test_noncanonical_or_insecure_file_is_rejected(self) -> None:
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")
        self.receipt_path.chmod(0o644)
        with self.assertRaisesRegex(
            GUARD.MockCertificationGuardError,
            "KIS_MOCK_CERTIFICATION_FILE_INVALID",
        ):
            self._verify()


if __name__ == "__main__":
    unittest.main()
