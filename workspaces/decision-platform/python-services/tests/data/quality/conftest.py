import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def posix_tmp_path() -> Iterator[Path]:
    """Windows mount가 POSIX mode bit를 흉내 내지 못하므로 보안 mode 검증은 /tmp에서 실행한다."""
    path = Path(tempfile.mkdtemp(prefix="s1-5-quality-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path)
