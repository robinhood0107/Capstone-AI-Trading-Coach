import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def posix_tmp_path() -> Iterator[Path]:
    """mode bit 보안 검증은 Windows mount가 아닌 WSL native `/tmp`에서 실행한다."""

    path = Path(tempfile.mkdtemp(prefix="s4-2a-rag-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture(autouse=True)
def _bge_opt_in_for_rag_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """BGE 경로는 제품에서 꺼져 있다. 코드를 검증하는 이 계층에서만 켠다.

    공개 코퍼스를 Voyage 하나로 통일했고 정책도 voyage_only_v1 로 고정돼 있다(V178).
    컨테이너에는 CAPSTONE_RAG_BGE_ENABLED 를 넣지 않으므로 운영 경로는 계속 거부된다.
    코드를 지우지 않고 남겨 두는 이상 그 코드가 도는지는 계속 검증한다.
    """

    monkeypatch.setenv("CAPSTONE_RAG_BGE_ENABLED", "1")
