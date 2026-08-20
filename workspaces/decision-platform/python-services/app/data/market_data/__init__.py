"""S5.7 neutral, provider-free market-data archive and readers."""

from app.data.market_data.archive import (
    MarketDataArchive,
    MarketDataArtifact,
    MarketDataArchiveError,
    read_market_data_archive,
)
from app.data.market_data.reader import (
    CloseObservation,
    MarketDataOperationalReader,
    ParquetMarketDataOperationalReader,
    ParquetResearchMarketHistoryReader,
    PostgresMarketDataOperationalReader,
    PostgresResearchMarketHistoryReader,
    ResearchMarketHistoryReader,
)
from app.data.market_data.repository import SeedAdoptionResult, stage_seed_archive

__all__ = [
    "CloseObservation",
    "MarketDataArchive",
    "MarketDataArchiveError",
    "MarketDataArtifact",
    "MarketDataOperationalReader",
    "ParquetMarketDataOperationalReader",
    "ParquetResearchMarketHistoryReader",
    "PostgresMarketDataOperationalReader",
    "PostgresResearchMarketHistoryReader",
    "ResearchMarketHistoryReader",
    "SeedAdoptionResult",
    "read_market_data_archive",
    "stage_seed_archive",
]
