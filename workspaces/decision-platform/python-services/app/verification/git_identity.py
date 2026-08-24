from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

_HEAD = re.compile(r"^[0-9a-f]{40}$")


class VerificationGitError(RuntimeError):
    """Current checkout cannot be bound to a clean verification packet/report."""


def current_clean_git_identity(repository_root: Path) -> tuple[str, str]:
    """Return clean HEAD and SHA-256 of its exact Git tree object bytes."""

    if not repository_root.is_absolute():
        raise VerificationGitError("P1 verification repository root must be absolute")
    commands = (
        ("status", "--porcelain=v1", "--untracked-files=all"),
        ("rev-parse", "--verify", "HEAD"),
        ("cat-file", "tree", "HEAD^{tree}"),
    )
    outputs: list[bytes] = []
    for command in commands:
        try:
            completed = subprocess.run(
                ("git", "-C", str(repository_root), *command),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise VerificationGitError("P1 verification Git identity is unavailable") from error
        if completed.returncode != 0:
            raise VerificationGitError("P1 verification Git identity is unavailable")
        outputs.append(completed.stdout)
    if outputs[0]:
        raise VerificationGitError("P1 verification requires a clean worktree")
    try:
        head = outputs[1].decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise VerificationGitError("P1 verification HEAD is invalid") from error
    if _HEAD.fullmatch(head) is None or not outputs[2]:
        raise VerificationGitError("P1 verification Git identity is invalid")
    return head, hashlib.sha256(outputs[2]).hexdigest()
