from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Final


_IMPORT_COMMANDS: Final = {
    "import-auto",
    "import-cpu",
    "import-intel-gpu",
    "import-nvidia-gpu",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Windows BAT가 호출하는 content command를 stable JSON으로 중계한다.

    PR2에서는 선택된 PADDLE_VL launcher와 fail-closed 경계만 활성화한다. 실제 generation
    저장·활성화는 PR3 runtime이 설치된 뒤 같은 command surface에 연결되며 private path는
    출력하지 않는다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _failure("CONTENT_COMMAND_INVALID")
    command = arguments[0]
    if command == "status" and len(arguments) == 1:
        _emit(
            {
                "code": "CONTENT_SETUP_REQUIRED",
                "progressPercent": 0,
                "state": "BUILDING",
            }
        )
        return 0
    if command == "setup" and len(arguments) == 1:
        return _failure("CONTENT_RELEASE_NOT_INSTALLED")
    if command in _IMPORT_COMMANDS and len(arguments) == 2:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    if command == "remove-document" and len(arguments) == 2:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    if command == "cache-clean" and len(arguments) == 1:
        return _failure("CORPUS_RUNTIME_NOT_INSTALLED")
    return _failure("CONTENT_COMMAND_INVALID")


def _failure(code: str) -> int:
    _emit({"code": code, "state": "FAILED"})
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
