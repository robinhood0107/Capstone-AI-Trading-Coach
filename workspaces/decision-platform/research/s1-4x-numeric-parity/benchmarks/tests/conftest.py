"""benchmarks 디렉터리의 독립 실행 스크립트를 테스트 import 경로에 둔다."""

from __future__ import annotations

import sys
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARKS_DIR))
