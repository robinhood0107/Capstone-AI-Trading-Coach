"""로컬에서 필수 게이트를 돌리고 그 결과를 영수증으로 남긴다.

왜 있나
-------
KIS 모의주문 인증은 "이 코드가 리뷰·CI 를 통과했다"를 PR 의 검사 6종으로 확인해 왔다.
그 확인이 `gh pr view` 에 묶여 있어 GitHub 계정과 살아 있는 PR 이 없으면 인증을 받을 수
없었다. 레포를 받지 않고 이미지만 올린 서버에서는 둘 다 없다.

같은 보장을 유지하면서 의존만 걷어낸다. 검사 6종을 **로컬에서 실제로 돌리고** 그 결과를
canonical JSON 영수증으로 남긴다. 인증 요청서는 PR 대신 이 영수증을 읽는다.

영수증은 실행한 이미지 다이제스트를 함께 적는다. 그래야 "어떤 코드에서 초록이었나"가
영수증 안에서 닫힌다 - 다른 이미지에 가져다 붙일 수 없다.

무엇을 보장하지 않나
------------------
사람 리뷰는 대체하지 않는다. 이 영수증이 말하는 것은 "그 검사들이 이 이미지에서 통과했다"
하나뿐이다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: 인증 요청서가 요구하는 검사 이름과 그것을 로컬에서 재현하는 명령.
#: 이름은 `mock_certification_guard._REQUIRED_CHECKS` 와 **글자까지 같아야 한다.**
#: 다르면 요청서 검증이 닫힌다.
CHECKS: Final[tuple[tuple[str, tuple[str, ...], str], ...]] = (
    (
        "Contract schema validation",
        ("uv", "run", "--frozen", "python", "-m", "pytest", "-q", "../../../contracts/tests"),
        "workspaces/decision-platform/python-services",
    ),
    (
        "Spring OpenAPI drift",
        ("uv", "run", "--frozen", "python", "contracts/verify_p1_return_signal_v3_openapi_transition.py"),
        ".",
    ),
    (
        "Kotlin ktlint and build",
        ("./gradlew", "ktlintCheck", "test", "--console=plain"),
        "workspaces/decision-platform/spring-api",
    ),
    (
        "Python quality gates",
        ("uv", "run", "--frozen", "python", "-m", "pytest", "-q"),
        "workspaces/decision-platform/python-services",
    ),
    (
        "Repo hygiene",
        ("git", "status", "--porcelain"),
        ".",
    ),
    (
        "P1 full-app security gates",
        ("npm", "test"),
        "workspaces/experience-dashboard",
    ),
)


class GateReceiptError(RuntimeError):
    """게이트를 돌리지 못했거나 영수증을 쓰지 못했다."""


def _image_digest(image: str) -> str:
    """이미지의 콘텐츠 다이제스트를 읽는다. 태그가 아니라 내용에 묶기 위해서다."""

    result = subprocess.run(
        ("docker", "image", "inspect", image, "--format", "{{.Id}}"),
        capture_output=True,
        text=True,
        check=False,
    )
    digest = result.stdout.strip()
    if result.returncode != 0 or not digest.startswith("sha256:"):
        raise GateReceiptError("P1_GATE_RECEIPT_IMAGE_UNAVAILABLE")
    return digest


def _run(command: tuple[str, ...], workdir: str) -> bool:
    result = subprocess.run(
        command,
        cwd=REPO_ROOT / workdir,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def build_receipt(image: str, *, skip_run: bool = False) -> dict[str, object]:
    """검사를 돌리고 영수증 내용을 만든다."""

    digest = _image_digest(image)
    results: list[dict[str, object]] = []
    for name, command, workdir in CHECKS:
        passed = True if skip_run else _run(command, workdir)
        results.append({"name": name, "passed": passed})
        print(f"P1_GATE={name}={'PASS' if passed else 'FAIL'}", flush=True)
    return {
        "contractId": "p1-gate-receipt/v1",
        "imageDigest": digest,
        "results": sorted(results, key=lambda item: str(item["name"])),
        "status": "PASS" if all(item["passed"] for item in results) else "FAIL",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def read_receipt(path: Path) -> dict[str, object]:
    """영수증을 읽고 형태를 확인한다. 통과하지 못한 검사가 있으면 거부한다."""

    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise GateReceiptError("P1_GATE_RECEIPT_UNREADABLE") from error
    if not isinstance(value, dict) or value.get("contractId") != "p1-gate-receipt/v1":
        raise GateReceiptError("P1_GATE_RECEIPT_INVALID")
    if value.get("status") != "PASS":
        raise GateReceiptError("P1_GATE_RECEIPT_NOT_PASSING")
    results = value.get("results")
    if not isinstance(results, list) or not results:
        raise GateReceiptError("P1_GATE_RECEIPT_INVALID")
    names = {str(item.get("name")) for item in results if isinstance(item, dict)}
    if names != {name for name, _, _ in CHECKS}:
        raise GateReceiptError("P1_GATE_RECEIPT_CHECKS_MISMATCH")
    if not all(isinstance(item, dict) and item.get("passed") is True for item in results):
        raise GateReceiptError("P1_GATE_RECEIPT_NOT_PASSING")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="capstone-decision-platform:p1-local")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="검사를 돌리지 않고 통과로 적는다. 시험용이며 인증에는 쓰지 않는다.",
    )
    arguments = parser.parse_args(argv)
    try:
        receipt = build_receipt(arguments.image, skip_run=arguments.skip_run)
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        arguments.out.write_text(payload, encoding="utf-8")
        arguments.out.chmod(0o600)
    except GateReceiptError as error:
        print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
        return 1
    print(f"P1_GATE_RECEIPT={receipt['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
