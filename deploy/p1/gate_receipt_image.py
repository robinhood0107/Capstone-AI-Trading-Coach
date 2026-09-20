"""게이트 영수증에서 이미지 다이제스트만 꺼낸다. 통과하지 못한 영수증은 거부한다."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_receipt import GateReceiptError, read_receipt


def main() -> int:
    if len(sys.argv) != 2:
        print("CAPSTONE_ERROR=P1_GATE_RECEIPT_ARGUMENT", file=sys.stderr)
        return 1
    try:
        print(read_receipt(Path(sys.argv[1]))["imageDigest"])
    except GateReceiptError as error:
        print(f"CAPSTONE_ERROR={error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
