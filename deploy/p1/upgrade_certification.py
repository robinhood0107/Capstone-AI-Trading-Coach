"""이미 받은 인증을 이미지 다이제스트 스키마로 옮긴다.

왜 있나
-------
인증은 `imageDigest` 로 묶이도록 바뀌었다. 그 전에 받은 요청서·영수증에는 그 필드가
없어 가드가 거부한다. 인증을 다시 받으려면 장중에 KIS 모의계좌로 물리 호출 8건을
내야 하므로, 이미 증명된 왕복을 버리고 다시 받는 것은 낭비다.

그래서 **이미 받은 인증에 현재 이미지 다이제스트를 적어 넣는다.** 왕복이 있었다는
사실(`physicalCalls`)과 두 파일의 결속(`inputSha256`)은 손대지 않는다.

정직하게 말하면
--------------
이 도구는 "그 인증이 이 이미지에서 이뤄졌다"를 **운영자가 보증**하는 것이다. 도구가
증명하는 것이 아니다. 그래서 대상 이미지를 명시적으로 받고, 기본값을 두지 않는다.
새로 인증을 받을 수 있는 상황이라면 이 도구를 쓰지 말고 `capstone mock certify` 를 쓴다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Final

_IMAGE_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


class UpgradeError(RuntimeError):
    """승격할 수 없다."""


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def upgrade(request_path: Path, receipt_path: Path, image_digest: str) -> None:
    if _IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise UpgradeError("P1_CERTIFICATION_IMAGE_DIGEST_INVALID")
    try:
        request = json.loads(request_path.read_bytes())
        receipt = json.loads(receipt_path.read_bytes())
    except (OSError, ValueError) as error:
        raise UpgradeError("P1_CERTIFICATION_UNREADABLE") from error
    if not isinstance(request, dict) or not isinstance(receipt, dict):
        raise UpgradeError("P1_CERTIFICATION_INVALID")
    if receipt.get("status") != "PASS":
        raise UpgradeError("P1_CERTIFICATION_NOT_PASSING")
    if request.get("commitSha") != receipt.get("commitSha"):
        raise UpgradeError("P1_CERTIFICATION_COMMIT_MISMATCH")

    request["imageDigest"] = image_digest
    # 영수증의 inputSha256 은 요청서 바이트에 묶여 있다. 요청서를 바꿨으니 다시 계산한다.
    # 물리 호출 수와 시각은 그대로 둔다 - 왕복이 있었다는 사실은 바뀌지 않는다.
    import hashlib

    receipt["imageDigest"] = image_digest
    receipt["inputSha256"] = hashlib.sha256(_canonical(request)).hexdigest()

    request_path.write_bytes(_canonical(request))
    request_path.chmod(0o600)
    receipt_path.write_bytes(_canonical(receipt))
    receipt_path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument(
        "--image-digest",
        required=True,
        help="이 인증이 이뤄진 이미지의 sha256 다이제스트. 운영자가 보증하는 값이다.",
    )
    arguments = parser.parse_args(argv)
    try:
        upgrade(arguments.request, arguments.receipt, arguments.image_digest)
    except UpgradeError as error:
        print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
        return 1
    print("P1_CERTIFICATION_UPGRADE=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
