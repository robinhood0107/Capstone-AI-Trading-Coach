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


def repository_artifact(module_file: str, relative: str) -> Path | None:
    """커밋된 산출물의 실제 경로를 위로 걸어 올라가며 찾는다. 없으면 None.

    `repository_root(depth)` 는 깊이가 맞지 않으면 module 디렉터리를 돌려준다. "없으면 없는
    것으로 본다"는 dotenv 에는 맞는 완화지만, 반드시 있어야 하는 커밋된 카탈로그에는 그
    완화가 잘못된 경로를 만들어 FileNotFoundError 를 엉뚱한 자리에서 터뜨린다.

    실측: 리포 체크아웃과 production 이미지의 깊이가 다르다. 이미지는 `app` 패키지와
    `contracts` 를 각각 `/app/app`, `/app/contracts` 로 복사하므로 module 기준 상대 깊이가
    체크아웃과 일치하지 않는다. 그래서 상수 대신 위로 걸어 올라가며 찾는다.

    이 부류는 실제로 두 번 물었다 - 일봉 수집기가 유니버스 카탈로그에서, 뉴스 거부권의 등록
    출처 카탈로그가 컨테이너에서. 후자는 근거가 항상 0개였던 동안 그 줄이 실행되지 않아
    잠들어 있었다.
    """

    for parent in Path(module_file).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return candidate
    return None
