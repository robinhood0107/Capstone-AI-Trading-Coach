from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

KRX_EXPORT_RANKING_RULE = "market cap desc, trading value desc, symbol asc"
UNIVERSE_MANIFEST_SCHEMA_VERSION = 1


class UniverseExportError(ValueError):
    pass


@dataclass(frozen=True)
class UniverseSymbol:
    rank: int
    symbol: str
    name: str


DEFAULT_KOSPI_LARGECAP30: tuple[UniverseSymbol, ...] = (
    UniverseSymbol(1, "005930", "Samsung Electronics"),
    UniverseSymbol(2, "000660", "SK hynix"),
    UniverseSymbol(3, "373220", "LG Energy Solution"),
    UniverseSymbol(4, "207940", "Samsung Biologics"),
    UniverseSymbol(5, "005380", "Hyundai Motor"),
    UniverseSymbol(6, "000270", "Kia"),
    UniverseSymbol(7, "068270", "Celltrion"),
    UniverseSymbol(8, "105560", "KB Financial Group"),
    UniverseSymbol(9, "035420", "NAVER"),
    UniverseSymbol(10, "005490", "POSCO Holdings"),
    UniverseSymbol(11, "012330", "Hyundai Mobis"),
    UniverseSymbol(12, "055550", "Shinhan Financial Group"),
    UniverseSymbol(13, "028260", "Samsung C&T"),
    UniverseSymbol(14, "006400", "Samsung SDI"),
    UniverseSymbol(15, "035720", "Kakao"),
    UniverseSymbol(16, "032830", "Samsung Life"),
    UniverseSymbol(17, "086790", "Hana Financial Group"),
    UniverseSymbol(18, "066570", "LG Electronics"),
    UniverseSymbol(19, "000810", "Samsung Fire & Marine"),
    UniverseSymbol(20, "015760", "KEPCO"),
    UniverseSymbol(21, "033780", "KT&G"),
    UniverseSymbol(22, "138040", "Meritz Financial Group"),
    UniverseSymbol(23, "009540", "HD Korea Shipbuilding & Offshore Engineering"),
    UniverseSymbol(24, "010130", "Korea Zinc"),
    UniverseSymbol(25, "034020", "Doosan Enerbility"),
    UniverseSymbol(26, "051910", "LG Chem"),
    UniverseSymbol(27, "329180", "HD Hyundai Heavy Industries"),
    UniverseSymbol(28, "017670", "SK Telecom"),
    UniverseSymbol(29, "003550", "LG Corp"),
    UniverseSymbol(30, "096770", "SK Innovation"),
)

DEFAULT_UNIVERSE_SOURCE = (
    "KOSPI large-cap seed list for S1.1 smoke/backfill. "
    "For audited ranking, pass --symbols-file exported from KRX market-cap data."
)


@dataclass(frozen=True)
class UniverseManifestSymbol:
    rank: int
    symbol: str
    name: str
    market: str
    market_cap: int
    trading_value: int

    def to_json(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "marketCap": self.market_cap,
            "tradingValue": self.trading_value,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "UniverseManifestSymbol":
        return cls(
            rank=int(payload["rank"]),
            symbol=_normalize_symbol(str(payload["symbol"])),
            name=str(payload.get("name") or ""),
            market=str(payload.get("market") or ""),
            market_cap=_to_int(payload.get("marketCap")),
            trading_value=_to_int(payload.get("tradingValue")),
        )


@dataclass(frozen=True)
class UniverseManifest:
    schema_version: int
    generated_at: datetime
    as_of_date: date
    source: str
    source_sha256: str
    ranking_rule: str
    limit: int
    symbols: tuple[UniverseManifestSymbol, ...]

    @property
    def symbol_codes(self) -> list[str]:
        return [item.symbol for item in self.symbols]

    @property
    def source_label(self) -> str:
        return (
            f"Universe manifest {self.as_of_date.isoformat()} from {self.source}; "
            f"ranking rule: {self.ranking_rule}"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at.isoformat(),
            "asOfDate": self.as_of_date.isoformat(),
            "source": self.source,
            "sourceSha256": self.source_sha256,
            "rankingRule": self.ranking_rule,
            "limit": self.limit,
            "symbols": [item.to_json() for item in self.symbols],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "UniverseManifest":
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise UniverseExportError("universe manifest symbols must be a list")
        return cls(
            schema_version=int(payload["schemaVersion"]),
            generated_at=datetime.fromisoformat(str(payload["generatedAt"])),
            as_of_date=date.fromisoformat(str(payload["asOfDate"])),
            source=str(payload["source"]),
            source_sha256=str(payload["sourceSha256"]),
            ranking_rule=str(payload["rankingRule"]),
            limit=int(payload["limit"]),
            symbols=tuple(
                UniverseManifestSymbol.from_json(item) for item in symbols if isinstance(item, dict)
            ),
        )


def parse_symbols(symbols_text: str | None) -> list[str]:
    if not symbols_text:
        return [item.symbol for item in DEFAULT_KOSPI_LARGECAP30]
    raw_parts = symbols_text.replace(",", " ").split()
    return [_normalize_symbol(part) for part in raw_parts if part.strip()]


def load_symbols_file(path: Path) -> list[str]:
    symbols: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        symbols.append(_normalize_symbol(stripped.split(",")[0].split()[0]))
    return symbols


def _normalize_symbol(value: str) -> str:
    return value.strip().zfill(6)


def refresh_universe_from_krx_export(
    export_path: Path,
    as_of: date,
    limit: int = 30,
    manifest_path: Path | None = None,
    generated_at: datetime | None = None,
) -> UniverseManifest:
    # KRX 원본 export는 커밋하지 않으므로 manifest에 기준일·해시·랭킹 규칙을 남겨 감사 가능성을 확보한다.
    rows = _read_krx_export(export_path)
    ranked = _rank_krx_rows(rows, limit=limit)
    manifest = UniverseManifest(
        schema_version=UNIVERSE_MANIFEST_SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(UTC),
        as_of_date=as_of,
        source=str(export_path),
        source_sha256=_sha256(export_path),
        ranking_rule=KRX_EXPORT_RANKING_RULE,
        limit=limit,
        symbols=tuple(ranked),
    )
    if manifest_path is not None:
        write_universe_manifest(manifest_path, manifest)
    return manifest


def write_universe_manifest(path: Path, manifest: UniverseManifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_json(), ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_universe_manifest(path: Path) -> UniverseManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UniverseExportError("universe manifest must be a JSON object")
    return UniverseManifest.from_json(payload)


def write_universe_markdown_report(path: Path, manifest: UniverseManifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# KIS S1.1b Universe Refresh Report",
        "",
        f"- Generated at: `{manifest.generated_at.isoformat()}`",
        f"- As-of date: `{manifest.as_of_date.isoformat()}`",
        f"- Source: `{manifest.source}`",
        f"- Source SHA-256: `{manifest.source_sha256}`",
        f"- Ranking rule: {manifest.ranking_rule}",
        f"- Limit: `{manifest.limit}`",
        "",
        "## Symbols",
        "",
        "| Rank | Symbol | Name | Market | Market Cap | Trading Value |",
        "|---:|---|---|---|---:|---:|",
    ]
    for item in manifest.symbols:
        lines.append(
            "| "
            f"{item.rank} | {item.symbol} | {item.name} | {item.market} | "
            f"{item.market_cap} | {item.trading_value} |"
        )
    lines.extend(
        [
            "",
            "## Safety Notes",
            "",
            "- This refresh reads an exported KRX ranking file only.",
            "- Generated manifest and reports stay in ignored local data paths.",
            "- S1.1b does not call order, balance, correction, cancellation, or live trading APIs.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _read_krx_export(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        # XLSX 파서는 의존성과 시트 구조 변동이 커서, S1.1b는 재현 가능한 CSV/TSV export만 계약으로 고정한다.
        raise UniverseExportError("KRX universe refresh expects CSV/TSV/TXT export, not XLSX. Export CSV first.")
    text = _read_text_with_fallback(path)
    sample = text[:4096]
    delimiter = "\t" if path.suffix.lower() == ".tsv" else _sniff_delimiter(sample)
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def _read_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UniverseExportError("KRX export must be readable as UTF-8-SIG or CP949")


def _sniff_delimiter(sample: str) -> str:
    if "\t" in sample and sample.count("\t") >= sample.count(","):
        return "\t"
    return ","


def _rank_krx_rows(rows: list[dict[str, str]], limit: int) -> list[UniverseManifestSymbol]:
    candidates: list[UniverseManifestSymbol] = []
    for row in rows:
        symbol = _extract(row, "종목코드", "단축코드", "code", "symbol")
        if not symbol or not symbol.strip().isdigit():
            continue
        symbol = _normalize_symbol(symbol)
        if len(symbol) != 6:
            continue
        market_cap = _optional_int(_extract(row, "시가총액", "marketCap", "market_cap"))
        trading_value = _optional_int(_extract(row, "거래대금", "tradingValue", "trading_value"))
        if market_cap is None or trading_value is None:
            continue
        candidates.append(
            UniverseManifestSymbol(
                rank=0,
                symbol=symbol,
                name=_extract(row, "종목명", "한글 종목명", "name") or "",
                market=_extract(row, "시장구분", "시장", "market") or "",
                market_cap=market_cap,
                trading_value=trading_value,
            )
        )
    # universe는 백필·모델 비교의 기준이므로 결측/비유동 후보를 제외하고 안정적인 tie-break를 둔다.
    ranked = sorted(candidates, key=lambda item: (-item.market_cap, -item.trading_value, item.symbol))
    return [
        UniverseManifestSymbol(
            rank=index,
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            market_cap=item.market_cap,
            trading_value=item.trading_value,
        )
        for index, item in enumerate(ranked[:limit], start=1)
    ]


def _extract(row: dict[str, str], *names: str) -> str | None:
    # KRX export 헤더는 화면/언어 설정에 따라 달라질 수 있어 의미가 같은 열 이름을 흡수한다.
    normalized = {_normalize_header(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_normalize_header(name))
        if value not in (None, ""):
            return value.strip()
    return None


def _normalize_header(value: str) -> str:
    return "".join(value.lower().split()).replace("_", "")


def _optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return _to_int(value)


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    text = str(value).strip().replace(",", "")
    return int(text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
