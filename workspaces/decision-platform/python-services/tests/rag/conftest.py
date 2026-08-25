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
