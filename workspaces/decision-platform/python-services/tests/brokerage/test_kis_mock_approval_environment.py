from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.brokerage import kis_mock_approval_environment as approval_environment


@pytest.fixture
def operator_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """테스트마다 repository root의 ignored operator `.env` 경계를 임시 regular file로 고정한다."""

    directory = Path(tempfile.mkdtemp(prefix="kis-mock-approval-env-", dir="/tmp"))
    path = directory / ".env"
    path.write_text(
        "\n".join(
            (
                "KIS_MOCK_BOUND_ACCOUNT_ID=acct_" + "a" * 32,
                "KIS_MOCK_ORDER_REFERENCE_KEY=synthetic-fernet-key",
                "S3_KIS_MOCK_EXACT_APPROVAL_ID=approval-s3-online-test",
                "S3_KIS_MOCK_EXACT_APPROVAL_SHA256=" + "b" * 64,
                "UNRELATED_RUNTIME_VALUE=ignored",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    monkeypatch.setattr(approval_environment, "_OPERATOR_ENV_FILE", path)
    try:
        yield path
    finally:
        shutil.rmtree(directory)


def test_operator_loader_reads_only_requested_private_env_values_without_process_mutation(
    operator_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """approval latch는 raw process env가 아니라 owner-only ignored file에서만 읽는다."""

    monkeypatch.setenv("KIS_MOCK_BOUND_ACCOUNT_ID", "acct_" + "c" * 32)

    values = approval_environment.load_kis_mock_approval_environment(
        "KIS_MOCK_BOUND_ACCOUNT_ID",
        "S3_KIS_MOCK_EXACT_APPROVAL_SHA256",
    )

    assert values == {
        "KIS_MOCK_BOUND_ACCOUNT_ID": "acct_" + "a" * 32,
        "S3_KIS_MOCK_EXACT_APPROVAL_SHA256": "b" * 64,
    }
    assert os.environ["KIS_MOCK_BOUND_ACCOUNT_ID"] == "acct_" + "c" * 32
    assert stat.S_IMODE(operator_env_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o640, 0o644, 0o700])
def test_operator_loader_rejects_non_0600_env_file_without_disclosing_contents(
    operator_env_file: Path,
    mode: int,
) -> None:
    operator_env_file.chmod(mode)

    with pytest.raises(
        approval_environment.KISMockApprovalEnvironmentRejected,
        match="operator approval environment is unavailable",
    ):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")


def test_operator_loader_rejects_symlink_and_hardlink_before_reading_operator_values(
    operator_env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """leaf substitution으로 account/key/latch를 바꾸지 못하게 link count와 no-follow를 함께 쓴다."""

    outside = operator_env_file.parent / "outside.env"
    outside.write_text(operator_env_file.read_text(encoding="utf-8"), encoding="utf-8")
    outside.chmod(0o600)
    symlink = operator_env_file.parent / "operator-link.env"
    symlink.symlink_to(outside)
    monkeypatch.setattr(approval_environment, "_OPERATOR_ENV_FILE", symlink)

    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")

    monkeypatch.setattr(approval_environment, "_OPERATOR_ENV_FILE", operator_env_file)
    linked_parent = operator_env_file.parent / "linked-parent"
    linked_parent.symlink_to(operator_env_file.parent, target_is_directory=True)
    monkeypatch.setattr(approval_environment, "_OPERATOR_ENV_FILE", linked_parent / ".env")
    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")

    monkeypatch.setattr(approval_environment, "_OPERATOR_ENV_FILE", operator_env_file)
    hardlink = operator_env_file.parent / "operator-hardlink.env"
    os.link(operator_env_file, hardlink)
    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")


def test_operator_loader_rejects_duplicate_or_missing_required_latch(
    operator_env_file: Path,
) -> None:
    operator_env_file.write_text(
        "\n".join(
            (
                "KIS_MOCK_BOUND_ACCOUNT_ID=acct_" + "a" * 32,
                "KIS_MOCK_BOUND_ACCOUNT_ID=acct_" + "b" * 32,
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")

    operator_env_file.write_text("UNRELATED_RUNTIME_VALUE=ignored\n", encoding="utf-8")
    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_BOUND_ACCOUNT_ID")


def test_operator_loader_rejects_unknown_variable_request(operator_env_file: Path) -> None:
    with pytest.raises(approval_environment.KISMockApprovalEnvironmentRejected):
        approval_environment.load_kis_mock_approval_environment("KIS_MOCK_APP_SECRET")
