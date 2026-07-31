from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file
from app.rag.source_card_v2_contract import (
    RagSourceCardV2ContractError,
    parse_source_card_v2_front_matter,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
S4_7B_SOURCE_CARD_ROOT = REPO_ROOT / "capstone-rag/source-cards/s4-7b"
S4_7B_CORPUS_MANIFEST_PATH = (
    REPO_ROOT / "capstone-rag/manifests/s4-7b-project-source-cards-30.v1.json"
)
MAX_SOURCE_CARD_BYTES = 65_536
MAX_CORPUS_MANIFEST_BYTES = 262_144
PARSER_VERSION = "rag-source-card-v2-markdown-v1"
CHUNKER_VERSION = "bge-tokenizer-heading-400-600-v1"
TOKENIZER_SHA256 = (
    "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
)
SOURCE_CARD_HEADINGS = (
    "핵심 claim",
    "적용 범위와 전제",
    "프로젝트 적용",
    "한계와 반례",
    "허용 사용",
    "금지 추론",
    "근거 위치",
)

EXPECTED_FINANCE_SOURCE_IDS = frozenset(
    {
        "src_project_backtest_overfitting_001",
        "src_project_bsm_continuous_hedge_assumptions_001",
        "src_project_bsm_risk_neutral_001",
        "src_project_bsm_time_to_expiry_001",
        "src_project_delta_hedge_residual_cost_001",
        "src_project_expected_payoff_measure_discount_001",
        "src_project_finance_diffusion_not_ddpm_001",
        "src_project_hmm_latent_state_boundary_001",
        "src_project_mean_reversion_stationarity_001",
        "src_project_monte_carlo_not_stress_probability_001",
        "src_project_notional_not_exposure_001",
        "src_project_sharpe_drawdown_partial_metrics_001",
        "src_project_threshold_cvar_not_exact_es_001",
        "src_project_valuation_delta_not_guard_delta_001",
        "src_project_var_es_coherence_001",
    }
)
EXPECTED_OFFICIAL_SOURCE_IDS = frozenset(
    {
        "src_project_ecos_pit_availability_001",
        "src_project_gold_futures_etf_132030_001",
        "src_project_kis_adjusted_price_001",
        "src_project_kis_current_price_snapshot_001",
        "src_project_kis_discovery_write_boundary_001",
        "src_project_kis_market_calendar_001",
        "src_project_kis_rate_limit_token_001",
        "src_project_krx_etf_etn_structure_001",
        "src_project_krx_etn_risk_indicator_001",
        "src_project_krx_last_trading_settlement_001",
        "src_project_krx_service_coverage_001",
        "src_project_naver_news_discovery_boundary_001",
        "src_project_opendart_corporation_code_001",
        "src_project_opendart_financial_statement_scope_001",
        "src_project_opendart_status_quota_001",
    }
)
REQUIRED_STABLE_ASSUMPTIONS = MappingProxyType(
    {
        "src_project_bsm_risk_neutral_001": (
            "RISK_NEUTRAL_NOT_PHYSICAL_PROBABILITY"
        ),
        "src_project_bsm_time_to_expiry_001": (
            "TIME_TO_EXPIRY_NOT_HOLDING_PERIOD"
        ),
        "src_project_delta_hedge_residual_cost_001": (
            "DELTA_HEDGE_RESIDUAL_RISK"
        ),
        "src_project_expected_payoff_measure_discount_001": (
            "EXPECTED_PAYOFF_REQUIRES_MEASURE_AND_DISCOUNTING"
        ),
        "src_project_finance_diffusion_not_ddpm_001": (
            "FINANCE_DIFFUSION_NOT_DDPM"
        ),
        "src_project_hmm_latent_state_boundary_001": (
            "HMM_STATE_NOT_CAUSAL_FACT"
        ),
        "src_project_kis_discovery_write_boundary_001": (
            "DISCOVERY_NOT_WRITE_ACTIVATION"
        ),
        "src_project_krx_last_trading_settlement_001": (
            "LAST_TRADING_AT_NOT_SETTLEMENT_DATE"
        ),
        "src_project_monte_carlo_not_stress_probability_001": (
            "STOCHASTIC_PROBABILITY_NOT_STRESS_PROBABILITY"
        ),
        "src_project_notional_not_exposure_001": "NOTIONAL_NOT_EXPOSURE",
        "src_project_threshold_cvar_not_exact_es_001": (
            "THRESHOLD_CVAR_NOT_EXACT_ES"
        ),
        "src_project_valuation_delta_not_guard_delta_001": (
            "VALUATION_DELTA_NOT_HARD_RISK_DELTA"
        ),
    }
)
PUBLIC_TOPICS_BY_SOURCE_ID = MappingProxyType(
    {
        "src_project_backtest_overfitting_001": ("METHODOLOGY",),
        "src_project_bsm_continuous_hedge_assumptions_001": (
            "FINANCIAL_ENGINEERING",
            "METHODOLOGY",
        ),
        "src_project_bsm_risk_neutral_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
        "src_project_bsm_time_to_expiry_001": (
            "FINANCIAL_ENGINEERING",
            "METHODOLOGY",
        ),
        "src_project_delta_hedge_residual_cost_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
        "src_project_ecos_pit_availability_001": ("API", "DATA"),
        "src_project_expected_payoff_measure_discount_001": (
            "FINANCIAL_ENGINEERING",
            "METHODOLOGY",
        ),
        "src_project_finance_diffusion_not_ddpm_001": (
            "FINANCIAL_ENGINEERING",
            "METHODOLOGY",
        ),
        "src_project_gold_futures_etf_132030_001": (
            "DATA",
            "PRODUCT_RISK",
        ),
        "src_project_hmm_latent_state_boundary_001": ("METHODOLOGY",),
        "src_project_kis_adjusted_price_001": ("API", "DATA"),
        "src_project_kis_current_price_snapshot_001": ("API", "DATA"),
        "src_project_kis_discovery_write_boundary_001": (
            "API",
            "METHODOLOGY",
            "PRODUCT_RISK",
        ),
        "src_project_kis_market_calendar_001": ("API", "DATA"),
        "src_project_kis_rate_limit_token_001": ("API",),
        "src_project_krx_etf_etn_structure_001": ("DATA", "PRODUCT_RISK"),
        "src_project_krx_etn_risk_indicator_001": ("PRODUCT_RISK", "RISK"),
        "src_project_krx_last_trading_settlement_001": (
            "DATA",
            "METHODOLOGY",
            "PRODUCT_RISK",
        ),
        "src_project_krx_service_coverage_001": ("DATA",),
        "src_project_mean_reversion_stationarity_001": ("METHODOLOGY",),
        "src_project_monte_carlo_not_stress_probability_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
        "src_project_naver_news_discovery_boundary_001": ("API", "DATA"),
        "src_project_notional_not_exposure_001": ("RISK",),
        "src_project_opendart_corporation_code_001": ("API", "DATA"),
        "src_project_opendart_financial_statement_scope_001": ("API", "DATA"),
        "src_project_opendart_status_quota_001": ("API", "DATA"),
        "src_project_sharpe_drawdown_partial_metrics_001": (
            "METHODOLOGY",
            "RISK",
        ),
        "src_project_threshold_cvar_not_exact_es_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
        "src_project_valuation_delta_not_guard_delta_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
        "src_project_var_es_coherence_001": (
            "FINANCIAL_ENGINEERING",
            "RISK",
        ),
    }
)
_MIGRATED_S4_7A_SOURCE_IDS = frozenset(
    {
        "src_project_ecos_pit_availability_001",
        "src_project_gold_futures_etf_132030_001",
        "src_project_kis_adjusted_price_001",
        "src_project_krx_service_coverage_001",
        "src_project_opendart_status_quota_001",
    }
)
_RAW_HTML_PATTERN = re.compile(r"<(?:[A-Za-z!/][^>\n]*)>")
_INSTRUCTION_LIKE_PATTERN = re.compile(
    (
        r"(?i)(ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior)\s+instructions"
        r"|system\s+prompt"
        r"|(?:reveal|print|exfiltrate)\b.{0,40}\b(?:secret|token|credential)s?\b"
        r"|(?:execute|run)\b.{0,30}\b(?:shell|command|code)\b"
        r"|(?:call|invoke)\b.{0,30}\b(?:tool|mcp|plugin)\b"
        r"|(?:place|submit|cancel)\b.{0,30}\border\b"
        r"|(?:이전|기존)\s*지시.{0,12}무시"
        r"|시스템\s*프롬프트"
        r"|비밀.{0,20}(?:출력|노출)"
        r"|도구.{0,20}(?:호출|실행))"
    )
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:^|[\s\"'])("
    r"(?:/home|/Users|/mnt/[a-z]|[A-Z]:[\\/]|\\\\wsl(?:\.localhost)?\\)"
    r"[^\s\"']*"
    r"|file:(?://)?[^\s\"']*"
    r")"
)


class RagSourceCardCorpusError(ValueError):
    """S4.7B exact corpus의 card, membership 또는 manifest가 drift했을 때 발생한다."""


@dataclass(frozen=True)
class FrozenSourceCard:
    """검증된 v2 card와 canonical hash projection.

    원문 evidence나 외부 payload는 포함하지 않으며, 모든 경로는 repository-relative
    logical locator로만 보존한다.
    """

    source_id: str
    card_id: str
    relative_path: str
    front_matter: Mapping[str, Any]
    canonical_body: str
    sections: Mapping[str, str]
    content_sha256: str
    front_matter_sha256: str
    body_sha256: str
    card_sha256: str


@dataclass(frozen=True)
class FrozenSourceCardCorpus:
    """exact 30 membership와 tracked manifest가 일치하는 immutable corpus view."""

    cards: tuple[FrozenSourceCard, ...]
    manifest: Mapping[str, Any]
    corpus_manifest_sha256: str


def parse_source_card_v2_markdown(
    raw: bytes,
    *,
    relative_path: str,
) -> FrozenSourceCard:
    """bounded Markdown bytes를 v2 front matter와 one-claim body로 검증한다.

    caller는 safe-I/O로 읽은 bytes를 전달해야 한다. 이 경계는 network나 provider 호출을
    만들지 않고 prompt-like text, raw HTML, private locator를 fail-closed한다.
    """

    if not raw or len(raw) > MAX_SOURCE_CARD_BYTES:
        raise RagSourceCardCorpusError("RAG source card size is invalid.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RagSourceCardCorpusError(
            "RAG source card must be strict UTF-8."
        ) from error
    if (
        "\r" in text
        or unicodedata.normalize("NFC", text) != text
        or not text.endswith("\n")
    ):
        raise RagSourceCardCorpusError(
            "RAG source card must use NFC, LF, and a final newline."
        )
    if not text.startswith("---\n"):
        raise RagSourceCardCorpusError(
            "RAG source card must start with exact YAML front matter."
        )
    closing_index = text.find("\n---\n", 4)
    if closing_index < 0:
        raise RagSourceCardCorpusError(
            "RAG source card front matter closing delimiter is missing."
        )
    front_matter_text = text[4:closing_index]
    body = text[closing_index + 5 :]
    if not front_matter_text or not body:
        raise RagSourceCardCorpusError(
            "RAG source card front matter and body must be non-empty."
        )
    try:
        front_matter = parse_source_card_v2_front_matter(
            front_matter_text.encode("utf-8")
        )
    except RagSourceCardV2ContractError as error:
        raise RagSourceCardCorpusError(
            "RAG source card v2 front matter validation failed."
        ) from error
    _validate_body_safety(body)
    sections = _parse_exact_body_sections(body, front_matter)

    canonical_front_matter = _canonical_json_bytes(dict(front_matter))
    body_bytes = body.encode("utf-8")
    return FrozenSourceCard(
        source_id=_required_text(front_matter, "sourceId"),
        card_id=_required_text(front_matter, "cardId"),
        relative_path=relative_path,
        front_matter=front_matter,
        canonical_body=body,
        sections=MappingProxyType(sections),
        content_sha256=_sha256(raw),
        front_matter_sha256=_sha256(canonical_front_matter),
        body_sha256=_sha256(body_bytes),
        card_sha256=_sha256(canonical_front_matter + b"\n" + body_bytes),
    )


def build_source_card_corpus_manifest(
    *,
    card_root: Path = S4_7B_SOURCE_CARD_ROOT,
) -> dict[str, Any]:
    """repository-approved card root의 exact 30에서 deterministic manifest를 만든다."""

    cards = _load_and_validate_exact_cards(card_root)
    identity = {
        "schemaVersion": "1",
        "orderedCards": [
            {
                "sourceId": card.source_id,
                "cardSha256": card.card_sha256,
            }
            for card in cards
        ],
    }
    corpus_manifest_sha256 = _sha256(_canonical_json_bytes(identity))
    required_keys = sorted(REQUIRED_STABLE_ASSUMPTIONS.values())
    return {
        "schemaVersion": "1",
        "corpusId": "s4-7b-project-source-cards-30",
        "status": "FROZEN",
        "producer": "app.rag.source_card_corpus",
        "ordering": "UTF8_NFC_SOURCE_ID_BYTES",
        "parserVersion": PARSER_VERSION,
        "chunkerVersion": CHUNKER_VERSION,
        "tokenizerSha256": TOKENIZER_SHA256,
        "projectCards": 30,
        "financeCards": 15,
        "officialCards": 15,
        "upstreamReferenceCardsExcluded": 20,
        "upstreamRegistryVersion": "s4-rag-p0-upstream-v1",
        "requiredStableAssumptionKeys": required_keys,
        "stableAssumptionCoverage": 1.0,
        "corpusManifestSha256": corpus_manifest_sha256,
        "cards": [
            _manifest_card_projection(card)
            for card in cards
        ],
    }


def load_frozen_source_card_corpus(
    *,
    card_root: Path = S4_7B_SOURCE_CARD_ROOT,
    manifest_path: Path = S4_7B_CORPUS_MANIFEST_PATH,
) -> FrozenSourceCardCorpus:
    """tracked manifest와 현재 exact 30 card bytes가 같은 경우에만 corpus를 반환한다."""

    cards = _load_and_validate_exact_cards(card_root)
    expected = build_source_card_corpus_manifest(card_root=card_root)
    tracked = _read_manifest(manifest_path)
    if tracked != expected:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus manifest does not match current exact card bytes."
        )
    corpus_manifest_sha256 = tracked.get("corpusManifestSha256")
    if not isinstance(corpus_manifest_sha256, str):
        raise RagSourceCardCorpusError(
            "RAG source-card corpus manifest identity is missing."
        )
    return FrozenSourceCardCorpus(
        cards=cards,
        manifest=MappingProxyType(tracked),
        corpus_manifest_sha256=corpus_manifest_sha256,
    )


def _load_and_validate_exact_cards(
    card_root: Path,
) -> tuple[FrozenSourceCard, ...]:
    try:
        entries = tuple(card_root.iterdir())
    except OSError as error:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus root could not be listed."
        ) from error
    if any(entry.name.startswith(".") or entry.suffix != ".md" for entry in entries):
        raise RagSourceCardCorpusError(
            "RAG source-card corpus root may contain only visible Markdown cards."
        )
    cards: list[FrozenSourceCard] = []
    for entry in entries:
        try:
            result = read_approved_regular_file(
                approved_root=card_root,
                relative_path=entry.name,
                max_bytes=MAX_SOURCE_CARD_BYTES,
            )
        except RagSafeIoError as error:
            raise RagSourceCardCorpusError(
                "RAG source-card safe read failed."
            ) from error
        cards.append(
            parse_source_card_v2_markdown(
                result.content,
                relative_path=(
                    f"capstone-rag/source-cards/s4-7b/{entry.name}"
                ),
            )
        )
    ordered = tuple(
        sorted(cards, key=lambda card: card.source_id.encode("utf-8"))
    )
    _validate_exact_membership(ordered)
    return ordered


def _validate_exact_membership(cards: tuple[FrozenSourceCard, ...]) -> None:
    if len(cards) != 30:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus must contain exact 30 project cards."
        )
    source_ids = [card.source_id for card in cards]
    card_ids = [card.card_id for card in cards]
    if len(set(source_ids)) != 30 or len(set(card_ids)) != 30:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus sourceId/cardId values must be unique."
        )
    expected = EXPECTED_FINANCE_SOURCE_IDS | EXPECTED_OFFICIAL_SOURCE_IDS
    if set(source_ids) != expected:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus membership drifted from the approved exact 30."
        )
    if set(PUBLIC_TOPICS_BY_SOURCE_ID) != expected:
        raise RagSourceCardCorpusError(
            "RAG source-card public topic mapping drifted from the approved exact 30."
        )

    known_card_ids = frozenset(card_ids)
    actual_assumptions: dict[str, str] = {}
    for card in cards:
        payload = card.front_matter
        expected_filename = f"{card.source_id}.md"
        if Path(card.relative_path).name != expected_filename:
            raise RagSourceCardCorpusError(
                "RAG source-card filename must equal its sourceId."
            )
        if card.source_id in EXPECTED_FINANCE_SOURCE_IDS:
            if payload["cardVariant"] != "SCHOLARLY_PRIMARY_CARD":
                raise RagSourceCardCorpusError(
                    "Finance source cards must use scholarly primary lineage."
                )
        elif payload["cardVariant"] != "OFFICIAL_UPSTREAM_CARD":
            raise RagSourceCardCorpusError(
                "Official source cards must use exact upstream lineage."
            )
        expected_session = (
            "S4.7A"
            if card.source_id in _MIGRATED_S4_7A_SOURCE_IDS
            else "S4.7B"
        )
        if payload["adoptedSession"] != expected_session:
            raise RagSourceCardCorpusError(
                "RAG source-card adoption session drifted."
            )
        if (
            payload["status"] != "VERIFIED"
            or payload["contentClass"] != "PROJECT_AUTHORED_SANITIZED_CARD"
            or payload["externalProcessingAllowed"] is not False
            or payload["externalProcessingGate"] != "NOT_GRANTED"
        ):
            raise RagSourceCardCorpusError(
                "RAG source-card activation or external-processing boundary drifted."
            )
        if payload["evidenceContentSha256"] == "0" * 64:
            raise RagSourceCardCorpusError(
                "RAG source-card bounded evidence digest cannot be a placeholder."
            )
        for contradicted_card_id in payload["contradicts"]:
            if contradicted_card_id not in known_card_ids:
                raise RagSourceCardCorpusError(
                    "RAG source-card contradiction must reference this exact corpus."
                )
        assumptions = payload["modelAssumptions"]
        if assumptions:
            if len(assumptions) != 1:
                raise RagSourceCardCorpusError(
                    "Exact S4.7B cards use one stable assumption per sensitive claim."
                )
            actual_assumptions[card.source_id] = assumptions[0]["key"]
    if actual_assumptions != dict(REQUIRED_STABLE_ASSUMPTIONS):
        raise RagSourceCardCorpusError(
            "RAG source-card stable assumption coverage drifted."
        )


def _validate_body_safety(body: str) -> None:
    if _RAW_HTML_PATTERN.search(body):
        raise RagSourceCardCorpusError(
            "RAG source-card body contains raw HTML."
        )
    if _INSTRUCTION_LIKE_PATTERN.search(body):
        raise RagSourceCardCorpusError(
            "RAG source-card body contains instruction-like control text."
        )
    if _PRIVATE_PATH_PATTERN.search(body):
        raise RagSourceCardCorpusError(
            "RAG source-card body contains a private filesystem locator."
        )
    if any(
        ord(character) < 0x20 and character != "\n"
        or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in body
    ):
        raise RagSourceCardCorpusError(
            "RAG source-card body contains control or surrogate characters."
        )


def _parse_exact_body_sections(
    body: str,
    front_matter: Mapping[str, Any],
) -> dict[str, str]:
    lines = body.splitlines()
    expected_title = f"# Source Card: {_required_text(front_matter, 'title')}"
    if not lines or lines[0] != expected_title:
        raise RagSourceCardCorpusError(
            "RAG source-card body title must match front matter."
        )
    headings = [
        (index, line[3:])
        for index, line in enumerate(lines)
        if line.startswith("## ")
    ]
    if tuple(heading for _, heading in headings) != SOURCE_CARD_HEADINGS:
        raise RagSourceCardCorpusError(
            "RAG source-card body headings or ordering drifted."
        )
    sections: dict[str, str] = {}
    for offset, (line_index, heading) in enumerate(headings):
        next_index = (
            headings[offset + 1][0]
            if offset + 1 < len(headings)
            else len(lines)
        )
        content = "\n".join(lines[line_index + 1 : next_index]).strip()
        if not content:
            raise RagSourceCardCorpusError(
                "RAG source-card body section must be non-empty."
            )
        sections[heading] = content
    if sections["핵심 claim"] != _required_text(front_matter, "claim"):
        raise RagSourceCardCorpusError(
            "RAG source-card body must preserve exactly one front-matter claim."
        )
    return sections


def _manifest_card_projection(card: FrozenSourceCard) -> dict[str, Any]:
    payload = card.front_matter
    category = (
        "FINANCE"
        if card.source_id in EXPECTED_FINANCE_SOURCE_IDS
        else "OFFICIAL_API"
    )
    return {
        "sourceId": card.source_id,
        "cardId": card.card_id,
        "category": category,
        "cardVariant": payload["cardVariant"],
        "relativePath": card.relative_path,
        "contentSha256": card.content_sha256,
        "frontMatterSha256": card.front_matter_sha256,
        "bodySha256": card.body_sha256,
        "cardSha256": card.card_sha256,
        "canonicalUrl": payload["canonicalUrl"],
        "locatorSha256": payload["canonicalUrlSha256"],
        "evidenceContentSha256": payload["evidenceContentSha256"],
        "verifiedAt": payload["verifiedAt"],
        "accessLevel": payload["accessLevel"],
        "licenseDecision": "SANITIZED_PROJECT_CARD_REFERENCE_ONLY",
        "retentionDays": payload["retentionDays"],
        "externalProcessingAllowed": False,
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        result = read_approved_regular_file(
            approved_root=path.parent,
            relative_path=path.name,
            max_bytes=MAX_CORPUS_MANIFEST_BYTES,
        )
        text = result.content.decode("utf-8", errors="strict")
        if "\r" in text or unicodedata.normalize("NFC", text) != text:
            raise RagSourceCardCorpusError(
                "RAG source-card corpus manifest must use NFC and LF."
            )
        value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (
        RagSafeIoError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise RagSourceCardCorpusError(
            "RAG source-card corpus manifest could not be read."
        ) from error
    if not isinstance(value, dict):
        raise RagSourceCardCorpusError(
            "RAG source-card corpus manifest must be an object."
        )
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RagSourceCardCorpusError(
                "RAG source-card corpus manifest contains duplicate keys."
            )
        result[key] = value
    return result


def _required_text(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise RagSourceCardCorpusError(
            f"RAG source-card {field} must be non-empty text."
        )
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
