from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


SCHEMA_VERSION = 1
METRIC_POLICY_VERSION = "s1-5-quality-report-v1"
CANONICAL_DAILY_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
)
METRIC_IDS = (
    "requiredSchemaIntegrity",
    "canonicalDuplicate",
    "ingestDuplicate",
    "currentUniverseHistoricalCoverage",
    "listingAdjustedCompleteness",
    "datasetFreshness",
    "perSymbolStale",
    "returnOutlier",
    "abruptPrice",
    "shareVolumeSpike",
    "logicalApiFailure",
    "physicalAttemptFailure",
)

MAX_SYMBOLS = 500
MAX_SESSIONS = 3_000
MAX_FILES = 500
MAX_ROWS = 2_000_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 512 * 1024 * 1024
MAX_REPORT_JSON_BYTES = 2 * 1024 * 1024
MAX_REPORT_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_MANIFEST_BYTES = 256 * 1024
MAX_INPUT_MANIFEST_BYTES = 256 * 1024
MAX_SAMPLES_PER_RULE = 20
MAX_SAMPLES = 100
WALL_DEADLINE_SECONDS = 120.0
TRAILING_OBSERVATIONS = 60
MINIMUM_OUTLIER_HISTORY = 20
MODIFIED_Z_THRESHOLD = 3.5
ABRUPT_RETURN_THRESHOLD = 0.30


def rate_ppm(numerator: int, denominator: int) -> int | None:
    """분모가 있는 rate만 Decimal ROUND_HALF_UP 정수 ppm으로 고정한다."""
    if numerator < 0 or denominator < 0:
        raise ValueError("rate counts must be non-negative")
    if numerator > denominator:
        raise ValueError("rate numerator cannot exceed denominator")
    if denominator == 0:
        return None
    value = Decimal(numerator) * Decimal(1_000_000) / Decimal(denominator)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
