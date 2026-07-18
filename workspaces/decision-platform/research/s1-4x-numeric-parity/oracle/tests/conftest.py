"""호출 cwd와 무관하게 script-only oracle modules를 test import path에 고정한다."""

from __future__ import annotations

import sys
from pathlib import Path

ORACLE_ROOT = Path(__file__).resolve().parents[1]
if str(ORACLE_ROOT) not in sys.path:
    sys.path.insert(0, str(ORACLE_ROOT))
