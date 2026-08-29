"""레포 루트를 깊이 상수가 아니라 표식으로 찾는다.

레포 체크아웃에서 이 패키지는 `workspaces/decision-platform/python-services/app/rag/` 아래라
루트가 다섯 단계 위지만, 배포 이미지에서는 `/app/app/rag/`라 두 단계 위다. 깊이를 상수로 두면
이미지 안에서 모듈 import 자체가 `IndexError`로 죽고, 그 예외가 RAG v2 프로세스를 기동에서
쓰러뜨린다. `app/p1_owner/vertex_veto.py`가 같은 이유로 쓰는 방식과 맞춘다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repository_root() -> Path:
    """`contracts/`를 가진 가장 가까운 조상을 레포 루트로 본다."""

    for candidate in Path(__file__).resolve().parents:
        if (candidate / "contracts").is_dir():
            return candidate
    raise RuntimeError("RAG repository root is unavailable")
