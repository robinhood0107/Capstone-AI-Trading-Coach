"""배포 레이아웃에서도 import가 깨지지 않는 repository root 계산."""

from __future__ import annotations

from pathlib import Path


def repository_root(module_file: str, depth: int) -> Path:
    """repo 체크아웃에서는 정확한 root를, 더 얕은 배포 레이아웃에서는 module 디렉터리를 돌려준다.

    production 이미지는 repo 전체가 아니라 `app` 패키지만 복사하므로 repo 기준 상대 깊이가
    존재하지 않는다. 이 경우 module 디렉터리를 돌려주면 root dotenv가 없는 것으로 취급되고,
    secret은 entrypoint가 주입한 OS 환경변수에서만 읽힌다. 상수를 module 최상단에서 계산하는
    기존 호출부가 IndexError로 import 단계에서 죽지 않게 하는 것이 목적이다.
    """

    resolved = Path(module_file).resolve()
    parents = resolved.parents
    if depth < len(parents):
        return parents[depth]
    return resolved.parent
