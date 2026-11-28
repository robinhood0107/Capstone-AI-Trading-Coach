#!/usr/bin/env python3
"""Exercise the staged and CI publication checks with synthetic files."""

from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
SCANNER = os.environ.get("GITLEAKS_BIN", "gitleaks")


def run(root: Path, *args: str, expected: int = 0) -> str:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True)
    assert result.returncode == expected, (args[0], result.returncode, expected)
    return result.stdout + result.stderr


with TemporaryDirectory(prefix="mars-publication-test-") as directory:
    root = Path(directory)
    for name in (".githooks/pre-commit", ".gitleaks.toml", ".github/scripts/check-publication-paths.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)

    run(root, "git", "init", "-q")
    run(root, "git", "config", "user.name", "Fixture")
    run(root, "git", "config", "user.email", "fixture@example.invalid")
    (root / ".env.example").write_text("API_KEY=\n")
    run(root, "git", "add", "--", ".env.example")
    run(root, "sh", ".githooks/pre-commit")
    run(root, "git", "commit", "-qm", "safe template")
    base = run(root, "git", "rev-parse", "HEAD").strip()

    binary = root / "scratch.sqlite"
    binary.write_bytes(b"\0test")
    run(root, "git", "add", "-f", "--", binary.name)
    denied = run(root, "sh", ".githooks/pre-commit", expected=1)
    assert "private path(s)" in denied and binary.name not in denied
    run(root, "git", "commit", "-qm", "introduce test data")
    run(root, "git", "rm", "--", binary.name)
    run(root, "git", "commit", "-qm", "remove test data")
    run(root, sys.executable, ".github/scripts/check-publication-paths.py", "--range", f"{base}..HEAD", expected=1)

    sample = {
        "mars-korean-resident-number": ("resident_id", "000101" + "-8000000"),
        "mars-private-phone": ("personal_phone", "010-" + "0001-0000"),
        "mars-private-email": ("customer_email", "private.fixture" + "@example.invalid"),
        "mars-private-account-number": ("brokerage_account_number", "0001" + "00000000"),
        "github-pat": ("fixture", "ghp_" + hashlib.sha256(b"synthetic github fixture").hexdigest()[:36]),
    }
    (root / "synthetic.txt").write_text("".join(f'{field} = "{value}"\n' for field, value in sample.values()))
    run(root, "git", "add", "--", "synthetic.txt")

    def verify_redaction(output: str) -> None:
        for rule, (_, value) in sample.items():
            assert rule in output, rule
            assert value not in output, rule

    output = run(root, "sh", ".githooks/pre-commit", expected=1)
    assert all(value not in output for _, value in sample.values())
    verify_redaction(run(root, SCANNER, "git", "--staged", "--config", ".gitleaks.toml",
                          "--redact=100", "--verbose", expected=1))
    parent = run(root, "git", "rev-parse", "HEAD").strip()
    run(root, "git", "commit", "-qm", "synthetic sensitive values")
    verify_redaction(run(root, SCANNER, "git", "--config", ".gitleaks.toml", "--redact=100",
                          "--log-opts", f"{parent}..HEAD", "--verbose", expected=1))

print("Publication protection checks passed.")
