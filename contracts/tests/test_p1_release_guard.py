from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GUARD = REPOSITORY_ROOT / "deploy" / "p1" / "release_guard.py"


class P1ReleaseGuardTest(unittest.TestCase):
    def run_guard(
        self,
        *arguments: str,
        input_bytes: bytes | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["python3", str(GUARD), *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=check,
        )

    def test_state_writer_replaces_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            state = root / "state"
            outside = root / "outside"
            outside.write_bytes(b"unchanged")
            self.run_guard("state-init", str(state))
            (state / "runtime.env").symlink_to(outside)

            self.run_guard("state-write", str(state), "runtime.env", "0600", input_bytes=b"safe\n")

            self.assertEqual(outside.read_bytes(), b"unchanged")
            self.assertFalse((state / "runtime.env").is_symlink())
            self.assertEqual((state / "runtime.env").read_bytes(), b"safe\n")
            self.assertEqual((state / "runtime.env").stat().st_mode & 0o777, 0o600)

    def test_release_accept_is_monotonic_and_requires_exact_rollback_approval(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            state = root / "state"
            self.run_guard("state-init", str(state))

            def manifest(version: str, commit: str) -> Path:
                path = root / f"{version}-{commit[0]}.json"
                path.write_text(
                    json.dumps(
                        {
                            "commitSha": commit,
                            "configSha256": "1" * 64,
                            "imagesArchiveSha256": "2" * 64,
                            "licenseSha256": "3" * 64,
                            "releaseVersion": version,
                            "sourceArchiveSha256": "4" * 64,
                            "treeSha": "5" * 40,
                        }
                    ),
                    encoding="utf-8",
                )
                return path

            newer = manifest("1.1.0", "a" * 40)
            older = manifest("1.0.0", "b" * 40)
            collision = manifest("1.1.0", "c" * 40)
            self.run_guard("release-accept", str(state), str(newer))
            rejected = self.run_guard("release-accept", str(state), str(older), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            environment = os.environ.copy()
            environment["P1_ALLOW_ROLLBACK_TO"] = f"1.0.0@{'b' * 40}"
            self.run_guard("release-accept", str(state), str(older), env=environment)
            rejected_collision = self.run_guard(
                "release-accept", str(state), str(collision), check=False
            )
            self.assertNotEqual(rejected_collision.returncode, 0)
            accepted = json.loads((state / "accepted-release.json").read_text(encoding="utf-8"))
            self.assertEqual(accepted["releaseVersion"], "1.1.0")
            self.assertEqual(accepted["commitSha"], "a" * 40)

    def test_archive_inventory_rejects_links_traversal_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)

            def rejected(name: str, members: list[tarfile.TarInfo]) -> None:
                archive = root / f"{name}.tar"
                with tarfile.open(archive, "w") as output:
                    for member in members:
                        output.addfile(member)
                result = self.run_guard(
                    "archive-inventory", str(archive), str(root / f"{name}.json"), check=False
                )
                self.assertNotEqual(result.returncode, 0)

            traversal = tarfile.TarInfo("../escape")
            traversal.size = 0
            rejected("traversal", [traversal])
            link = tarfile.TarInfo("layer-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "manifest.json"
            rejected("link", [link])
            first = tarfile.TarInfo("manifest.json")
            second = tarfile.TarInfo("manifest.json")
            rejected("duplicate", [first, second])

    def test_archive_is_staged_and_compared_before_load(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            archive = root / "images.tar"
            payload = root / "manifest.json"
            payload.write_bytes(b"{}")
            with tarfile.open(archive, "w") as output:
                output.add(payload, arcname="manifest.json")
            inventory = root / "inventory.json"
            self.run_guard("archive-inventory", str(archive), str(inventory))
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            staged = stage / "images.tar"

            self.run_guard("stage-archive", str(archive), digest, str(staged))
            self.run_guard("archive-compare", str(staged), str(inventory))
            self.assertEqual(staged.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
