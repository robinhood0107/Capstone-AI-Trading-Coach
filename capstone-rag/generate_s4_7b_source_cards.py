#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPSTONE_RAG_ROOT = Path(__file__).resolve().parent
PYTHON_SERVICE_ROOT = REPO_ROOT / "workspaces/decision-platform/python-services"
if str(PYTHON_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_SERVICE_ROOT))

from app.rag.source_card_corpus import (  # noqa: E402
    S4_7B_CORPUS_MANIFEST_PATH,
    S4_7B_SOURCE_CARD_ROOT,
    build_source_card_corpus_manifest,
)
from app.rag.source_card_v2_contract import (  # noqa: E402
    validate_source_card_v2_payload,
)
from app.rag.safe_io import (  # noqa: E402
    list_approved_regular_files,
    read_approved_regular_file,
    write_approved_generated_file,
)

_MAX_CARD_BYTES = 2 * 1024 * 1024
_MAX_CARD_ENTRIES = 30
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024

VERIFIED_AT = "2026-07-31T00:00:00Z"
DEFAULT_ACCESS_NOTE = (
    "primary DOI metadata 또는 공식 institution page의 bounded locator와 "
    "claim-supporting metadata만 읽기 전용으로 확인했다."
)
DEFAULT_LICENSE_NOTE = (
    "서지 metadata, official locator와 project-authored bounded claim만 사용하며 "
    "원문 PDF, HTML, screenshot 또는 provider payload는 corpus에 복제하지 않는다."
)
DEFAULT_ATTRIBUTION_SUFFIX = "공식 locator metadata"
DEFAULT_ALLOWED_USE = "sanitized offline retrieval citation과 경계 설명"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scholarly(
    *,
    source_id: str,
    card_id: str,
    title: str,
    institution: str,
    topic: str,
    claim: str,
    canonical_url: str,
    evidence_class: str,
    attribution: str,
    bibliography: dict[str, Any],
    question: str,
    scope: str,
    application: str,
    limitation: str,
    forbidden: str,
    evidence_summary: str,
    assumption_key: str | None = None,
    assumption_statement: str | None = None,
) -> dict[str, Any]:
    return {
        "sourceId": source_id,
        "cardId": card_id,
        "title": title,
        "institution": institution,
        "topic": topic,
        "claim": claim,
        "cardVariant": "SCHOLARLY_PRIMARY_CARD",
        "evidenceClass": evidence_class,
        "canonicalUrl": canonical_url,
        "attribution": attribution,
        "bibliographicLocator": bibliography["locator"],
        "bibliographicMetadata": bibliography["metadata"],
        "upstreamSourceIds": [],
        "question": question,
        "scope": scope,
        "application": application,
        "limitations": [limitation],
        "allowedUses": [DEFAULT_ALLOWED_USE],
        "forbiddenInferences": [forbidden],
        "evidenceSummary": evidence_summary,
        "assumptionKey": assumption_key,
        "assumptionStatement": assumption_statement,
        "verifiedAt": VERIFIED_AT,
        "adoptedSession": "S4.7B",
        "accessNote": DEFAULT_ACCESS_NOTE,
        "licenseNote": DEFAULT_LICENSE_NOTE,
    }


def _official(
    *,
    source_id: str,
    card_id: str,
    title: str,
    institution: str,
    topic: str,
    claim: str,
    canonical_url: str,
    evidence_class: str,
    attribution: str,
    upstream_source_ids: list[str],
    question: str,
    scope: str,
    application: str,
    limitations: list[str],
    allowed_uses: list[str],
    forbidden_inferences: list[str],
    evidence_summary: str,
    verified_at: str = VERIFIED_AT,
    adopted_session: str = "S4.7B",
    access_note: str = DEFAULT_ACCESS_NOTE,
    license_note: str = DEFAULT_LICENSE_NOTE,
    canonical_url_sha256: str | None = None,
    evidence_content_sha256: str | None = None,
    assumption_key: str | None = None,
    assumption_statement: str | None = None,
) -> dict[str, Any]:
    return {
        "sourceId": source_id,
        "cardId": card_id,
        "title": title,
        "institution": institution,
        "topic": topic,
        "claim": claim,
        "cardVariant": "OFFICIAL_UPSTREAM_CARD",
        "evidenceClass": evidence_class,
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": canonical_url_sha256,
        "evidenceContentSha256": evidence_content_sha256,
        "attribution": attribution,
        "upstreamSourceIds": upstream_source_ids,
        "question": question,
        "scope": scope,
        "application": application,
        "limitations": limitations,
        "allowedUses": allowed_uses,
        "forbiddenInferences": forbidden_inferences,
        "evidenceSummary": evidence_summary,
        "assumptionKey": assumption_key,
        "assumptionStatement": assumption_statement,
        "verifiedAt": verified_at,
        "adoptedSession": adopted_session,
        "accessNote": access_note,
        "licenseNote": license_note,
    }


def _doi_bibliography(
    *,
    doi: str,
    authors: list[str],
    title: str,
    year: int,
    venue: str,
    edition_or_version: str,
) -> dict[str, Any]:
    return {
        "locator": {
            "authorityType": "DOI_REGISTRY",
            "locatorType": "DOI",
            "value": doi,
        },
        "metadata": {
            "authors": authors,
            "editionOrVersion": edition_or_version,
            "title": title,
            "venue": venue,
            "year": year,
        },
    }


def _official_bibliography(
    *,
    canonical_url: str,
    authors: list[str],
    title: str,
    year: int,
    venue: str,
    edition_or_version: str,
) -> dict[str, Any]:
    return {
        "locator": {
            "authorityType": "OFFICIAL_INSTITUTION",
            "locatorType": "OFFICIAL_URL",
            "value": canonical_url,
        },
        "metadata": {
            "authors": authors,
            "editionOrVersion": edition_or_version,
            "title": title,
            "venue": venue,
            "year": year,
        },
    }


BLACK_SCHOLES = _doi_bibliography(
    doi="10.1086/260062",
    authors=["Fischer Black", "Myron Scholes"],
    title="The Pricing of Options and Corporate Liabilities",
    year=1973,
    venue="Journal of Political Economy",
    edition_or_version="Volume 81, Number 3, pages 637-654",
)
MERTON = _doi_bibliography(
    doi="10.2307/3003143",
    authors=["Robert C. Merton"],
    title="Theory of Rational Option Pricing",
    year=1973,
    venue="The Bell Journal of Economics and Management Science",
    edition_or_version="Volume 4, Number 1, page 141",
)


CARD_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _scholarly(
        source_id="src_project_bsm_risk_neutral_001",
        card_id="card_bsm_risk_neutral_001",
        title="BSM risk-neutral 가격과 physical 확률의 경계",
        institution="university_of_chicago_press",
        topic="bsm_risk_neutral",
        claim=(
            "BSM은 무차익·복제 가격식이며 physical 상승확률 예측기가 아니다."
        ),
        canonical_url="https://doi.org/10.1086/260062",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Fischer Black and Myron Scholes, Journal of Political Economy",
        bibliography=BLACK_SCHOLES,
        question="BSM 가격을 실제 주가 상승확률로 읽으면 왜 안 되나요?",
        scope=(
            "Black과 Scholes의 무차익·복제 가격결정 metadata와 risk-neutral "
            "해석 경계에만 적용한다."
        ),
        application=(
            "옵션 valuation 결과와 physical forecast를 별도 output과 evidence로 다룬다."
        ),
        limitation=(
            "모델 가정 밖 실제 시장 확률이나 미래 가격을 보장하지 않는다."
        ),
        forbidden=(
            "risk-neutral measure를 physical 상승확률로 해석하지 않는다."
        ),
        evidence_summary=(
            "Black; Scholes|The Pricing of Options and Corporate Liabilities|"
            "1973|Journal of Political Economy|10.1086/260062"
        ),
        assumption_key="RISK_NEUTRAL_NOT_PHYSICAL_PROBABILITY",
        assumption_statement=(
            "복제와 무차익 가격결정의 measure를 실제 상승확률로 치환하지 않는다."
        ),
    ),
    _scholarly(
        source_id="src_project_bsm_time_to_expiry_001",
        card_id="card_bsm_time_to_expiry_001",
        title="BSM time-to-expiry와 보유기간의 경계",
        institution="jstor",
        topic="bsm_time_to_expiry",
        claim=(
            "BSM의 T는 valuation 시점부터 계약의 actual last-trading/expiry "
            "instant까지다."
        ),
        canonical_url="https://doi.org/10.2307/3003143",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Robert C. Merton, The Bell Journal of Economics and Management Science",
        bibliography=MERTON,
        question="BSM의 T를 임의 보유기간으로 바꾸면 왜 안 되나요?",
        scope=(
            "계약의 actual expiry instant를 사용하는 option-pricing horizon에만 적용한다."
        ),
        application=(
            "valuation timestamp와 exchange contract의 last-trading 또는 expiry instant를 "
            "같은 timezone 기준으로 계산한다."
        ),
        limitation=(
            "calendar convention과 product별 settlement 규칙은 별도 official evidence가 필요하다."
        ),
        forbidden="T를 투자자의 임의 holding period로 대체하지 않는다.",
        evidence_summary=(
            "Robert C. Merton|Theory of Rational Option Pricing|1973|"
            "The Bell Journal of Economics and Management Science|10.2307/3003143"
        ),
        assumption_key="TIME_TO_EXPIRY_NOT_HOLDING_PERIOD",
        assumption_statement=(
            "time-to-expiry는 valuation instant와 실제 계약 expiry instant의 차이다."
        ),
    ),
    _scholarly(
        source_id="src_project_bsm_continuous_hedge_assumptions_001",
        card_id="card_bsm_continuous_hedge_assumptions_001",
        title="BSM continuous hedge 가정의 적용 한계",
        institution="university_of_chicago_press",
        topic="bsm_continuous_hedge_assumptions",
        claim=(
            "continuous delta hedge 결론은 연속거래·무마찰 등 모델 가정 아래 성립한다."
        ),
        canonical_url="https://doi.org/10.1086/260062",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Fischer Black and Myron Scholes, Journal of Political Economy",
        bibliography=BLACK_SCHOLES,
        question="continuous delta hedge를 실제 시장의 무손실 보장으로 볼 수 있나요?",
        scope="Black–Scholes replication argument의 명시된 idealized assumptions에만 적용한다.",
        application=(
            "실제 hedge 설명에는 rebalance interval, liquidity와 transaction cost를 함께 표시한다."
        ),
        limitation="불연속 가격, 비용과 체결 제약이 있는 실제 hedge 오차를 정량 보장하지 않는다.",
        forbidden="continuous hedge 결론을 실제 시장의 무손실 보장으로 확대하지 않는다.",
        evidence_summary=(
            "Black; Scholes|The Pricing of Options and Corporate Liabilities|"
            "continuous trading assumptions|10.1086/260062"
        ),
    ),
    _scholarly(
        source_id="src_project_delta_hedge_residual_cost_001",
        card_id="card_delta_hedge_residual_cost_001",
        title="Discrete delta hedge의 residual risk와 거래비용",
        institution="wiley",
        topic="delta_hedge_residual_cost",
        claim="discrete hedge와 transaction cost는 residual risk를 만든다.",
        canonical_url="https://doi.org/10.1111/j.1540-6261.1985.tb02383.x",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Hayne E. Leland, The Journal of Finance",
        bibliography=_doi_bibliography(
            doi="10.1111/j.1540-6261.1985.tb02383.x",
            authors=["Hayne E. Leland"],
            title="Option Pricing and Replication with Transactions Costs",
            year=1985,
            venue="The Journal of Finance",
            edition_or_version="Volume 40, Number 5, pages 1283-1301",
        ),
        question="discrete delta hedge 뒤에도 위험과 비용이 남는 이유는 무엇인가요?",
        scope="Leland의 transaction-cost replication 연구가 다루는 discrete hedge 경계에 적용한다.",
        application="hedge 결과에는 rebalance frequency, cost model과 residual PnL을 분리 기록한다.",
        limitation="특정 시장의 현재 거래비용이나 최적 hedge 주기를 이 카드가 산출하지 않는다.",
        forbidden="모델 delta가 residual risk와 비용을 제거한다고 추론하지 않는다.",
        evidence_summary=(
            "Hayne E. Leland|Option Pricing and Replication with Transactions Costs|"
            "1985|The Journal of Finance|10.1111/j.1540-6261.1985.tb02383.x"
        ),
        assumption_key="DELTA_HEDGE_RESIDUAL_RISK",
        assumption_statement=(
            "discrete rebalance와 transaction cost 뒤의 residual risk를 0으로 두지 않는다."
        ),
    ),
    _scholarly(
        source_id="src_project_notional_not_exposure_001",
        card_id="card_notional_not_exposure_001",
        title="Derivative notional과 exposure의 구분",
        institution="bis",
        topic="notional_not_exposure",
        claim=(
            "notional은 market value·credit exposure·amount at risk와 동일하지 않다."
        ),
        canonical_url="https://data.bis.org/topics/OTC_DER",
        evidence_class="OFFICIAL_REPORT",
        attribution="Bank for International Settlements, BIS Data Portal",
        bibliography=_official_bibliography(
            canonical_url="https://data.bis.org/topics/OTC_DER",
            authors=["Bank for International Settlements"],
            title="OTC derivatives statistics",
            year=2026,
            venue="BIS Data Portal",
            edition_or_version="Overview and methodology verified 2026-07-31",
        ),
        question="파생상품 notional을 실제 위험 노출액으로 그대로 써도 되나요?",
        scope=(
            "BIS OTC statistics가 notional, market value와 credit exposure를 별도 measure로 "
            "제시하는 범위에 적용한다."
        ),
        application="risk report에서 notional과 valuation 또는 exposure measure를 별도 column으로 보존한다.",
        limitation="계약별 netting, collateral과 future exposure 계산은 별도 모델이 필요하다.",
        forbidden="notional 전액을 현재 손실액이나 credit exposure로 단정하지 않는다.",
        evidence_summary=(
            "BIS Data Portal|OTC derivatives statistics|notional value|"
            "market value|credit exposure|verified 2026-07-31"
        ),
        assumption_key="NOTIONAL_NOT_EXPOSURE",
        assumption_statement=(
            "notional, market value, credit exposure와 amount at risk를 서로 다른 measure로 유지한다."
        ),
    ),
    _scholarly(
        source_id="src_project_hmm_latent_state_boundary_001",
        card_id="card_hmm_latent_state_boundary_001",
        title="HMM latent state의 인식론적 경계",
        institution="ieee",
        topic="hmm_latent_state_boundary",
        claim=(
            "HMM state는 관측모형이 추정한 latent label이지 시장 원인의 사실 선언이 아니다."
        ),
        canonical_url="https://doi.org/10.1109/5.18626",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Lawrence R. Rabiner, Proceedings of the IEEE",
        bibliography=_doi_bibliography(
            doi="10.1109/5.18626",
            authors=["Lawrence R. Rabiner"],
            title=(
                "A tutorial on hidden Markov models and selected applications "
                "in speech recognition"
            ),
            year=1989,
            venue="Proceedings of the IEEE",
            edition_or_version="Volume 77, Number 2, pages 257-286",
        ),
        question="HMM regime label을 시장 원인의 확정 사실로 말해도 되나요?",
        scope="hidden state가 observation model과 transition assumptions 아래 추론되는 범위에 적용한다.",
        application="regime output에는 model version, posterior와 latent-label wording을 함께 보존한다.",
        limitation="latent label은 경제 사건의 causal identification을 제공하지 않는다.",
        forbidden="HMM state 이름을 확인된 시장 원인이나 사건으로 선언하지 않는다.",
        evidence_summary=(
            "Lawrence R. Rabiner|A tutorial on hidden Markov models and selected "
            "applications in speech recognition|1989|10.1109/5.18626"
        ),
        assumption_key="HMM_STATE_NOT_CAUSAL_FACT",
        assumption_statement=(
            "관측모형이 추정한 latent label을 확인된 causal market fact로 바꾸지 않는다."
        ),
    ),
    _scholarly(
        source_id="src_project_finance_diffusion_not_ddpm_001",
        card_id="card_finance_diffusion_not_ddpm_001",
        title="금융 SDE diffusion과 learned DDPM의 구분",
        institution="neurips",
        topic="finance_diffusion_not_ddpm",
        claim=(
            "금융 SDE diffusion과 learned DDPM은 수학적 연결이 있어도 같은 "
            "알고리즘·목적함수가 아니다."
        ),
        canonical_url=(
            "https://proceedings.neurips.cc/paper/2020/hash/"
            "4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html"
        ),
        evidence_class="PRIMARY_RESEARCH",
        attribution="Jonathan Ho, Ajay Jain and Pieter Abbeel, NeurIPS 2020",
        bibliography=_official_bibliography(
            canonical_url=(
                "https://proceedings.neurips.cc/paper/2020/hash/"
                "4c5bcfec8584af0d967f1ab10179ca4b-Abstract.html"
            ),
            authors=["Jonathan Ho", "Ajay Jain", "Pieter Abbeel"],
            title="Denoising Diffusion Probabilistic Models",
            year=2020,
            venue="Advances in Neural Information Processing Systems",
            edition_or_version="Volume 33, NeurIPS 2020",
        ),
        question="금융 SDE의 diffusion과 DDPM을 같은 모델로 불러도 되나요?",
        scope="Ho 등의 learned reverse diffusion objective와 금융 SDE terminology 구분에 적용한다.",
        application="설계서에 stochastic process, training objective와 sampler를 각각 명시한다.",
        limitation="수학적 연결 가능성 자체를 부정하지 않으며 구현 equivalence만 금지한다.",
        forbidden="diffusion이라는 단어만으로 SDE와 DDPM의 목적함수와 algorithm을 동일시하지 않는다.",
        evidence_summary=(
            "Jonathan Ho; Ajay Jain; Pieter Abbeel|Denoising Diffusion "
            "Probabilistic Models|NeurIPS 2020|official proceedings"
        ),
        assumption_key="FINANCE_DIFFUSION_NOT_DDPM",
        assumption_statement=(
            "금융 SDE와 learned DDPM은 각각의 state dynamics와 training objective로 식별한다."
        ),
    ),
    _scholarly(
        source_id="src_project_var_es_coherence_001",
        card_id="card_var_es_coherence_001",
        title="VaR와 Expected Shortfall coherence 경계",
        institution="wiley",
        topic="var_es_coherence",
        claim=(
            "VaR와 ES는 서로 다른 tail-risk functional이며 coherence 성질도 구분한다."
        ),
        canonical_url="https://doi.org/10.1111/1467-9965.00068",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Philippe Artzner, Freddy Delbaen, Jean-Marc Eber and David Heath",
        bibliography=_doi_bibliography(
            doi="10.1111/1467-9965.00068",
            authors=[
                "Philippe Artzner",
                "Freddy Delbaen",
                "Jean-Marc Eber",
                "David Heath",
            ],
            title="Coherent Measures of Risk",
            year=1999,
            venue="Mathematical Finance",
            edition_or_version="Volume 9, Number 3, pages 203-228",
        ),
        question="VaR와 ES를 같은 tail-risk 숫자로 취급하면 왜 안 되나요?",
        scope="risk functional의 정의와 coherence axioms를 구분하는 설명에 적용한다.",
        application="risk report에 measure name, confidence level, horizon과 estimator를 명시한다.",
        limitation="특정 portfolio 분포에서 두 measure의 수치 관계를 보장하지 않는다.",
        forbidden="VaR와 ES의 정의 또는 coherence 성질을 서로 바꾸지 않는다.",
        evidence_summary=(
            "Artzner; Delbaen; Eber; Heath|Coherent Measures of Risk|1999|"
            "Mathematical Finance|10.1111/1467-9965.00068"
        ),
    ),
    _scholarly(
        source_id="src_project_threshold_cvar_not_exact_es_001",
        card_id="card_threshold_cvar_not_exact_es_001",
        title="Threshold CVaR 평균과 exact finite-sample ES의 경계",
        institution="wiley",
        topic="threshold_cvar_not_exact_es",
        claim=(
            "threshold 이하 단순평균은 fractional boundary를 다루는 exact finite-sample "
            "ES와 항상 같지 않다."
        ),
        canonical_url="https://doi.org/10.1111/1468-0300.00091",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Carlo Acerbi and Dirk Tasche, Economic Notes",
        bibliography=_doi_bibliography(
            doi="10.1111/1468-0300.00091",
            authors=["Carlo Acerbi", "Dirk Tasche"],
            title="Expected Shortfall: A Natural Coherent Alternative to Value at Risk",
            year=2002,
            venue="Economic Notes",
            edition_or_version="Volume 31, Number 2, pages 379-388",
        ),
        question="VaR threshold 아래 표본 평균을 exact ES라고 불러도 되나요?",
        scope="finite sample에서 quantile boundary mass를 다루는 ES 정의 구분에 적용한다.",
        application="estimator에 tail count, quantile convention과 fractional boundary policy를 기록한다.",
        limitation="연속분포의 큰 표본 근사와 finite-sample exact 계산은 별도로 평가한다.",
        forbidden="threshold 이하 단순평균을 모든 표본의 exact ES로 단정하지 않는다.",
        evidence_summary=(
            "Carlo Acerbi; Dirk Tasche|Expected Shortfall: A Natural Coherent "
            "Alternative to Value at Risk|2002|10.1111/1468-0300.00091"
        ),
        assumption_key="THRESHOLD_CVAR_NOT_EXACT_ES",
        assumption_statement=(
            "finite-sample ES는 quantile boundary의 fractional mass convention을 명시한다."
        ),
    ),
    _scholarly(
        source_id="src_project_expected_payoff_measure_discount_001",
        card_id="card_expected_payoff_measure_discount_001",
        title="Expected payoff의 measure와 discounting 경계",
        institution="jstor",
        topic="expected_payoff_measure_discount",
        claim=(
            "expected payoff에는 measure·conditioning·horizon·numeraire/discounting이 필요하다."
        ),
        canonical_url="https://doi.org/10.2307/3003143",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Robert C. Merton, The Bell Journal of Economics and Management Science",
        bibliography=MERTON,
        question="expected payoff만 적고 measure와 할인 기준을 생략해도 되나요?",
        scope="rational option pricing에서 expectation과 discounting을 해석하는 범위에 적용한다.",
        application="모든 expected payoff output에 measure, information set, horizon과 numeraire를 기록한다.",
        limitation="특정 asset의 physical expected return을 이 카드가 추정하지 않는다.",
        forbidden="measure나 discounting이 없는 expectation을 valuation으로 확정하지 않는다.",
        evidence_summary=(
            "Robert C. Merton|Theory of Rational Option Pricing|expectation|"
            "discounting|10.2307/3003143"
        ),
        assumption_key="EXPECTED_PAYOFF_REQUIRES_MEASURE_AND_DISCOUNTING",
        assumption_statement=(
            "expected payoff에는 measure, conditioning, horizon과 discounting convention을 결합한다."
        ),
    ),
    _scholarly(
        source_id="src_project_monte_carlo_not_stress_probability_001",
        card_id="card_monte_carlo_not_stress_probability_001",
        title="Monte Carlo 확률과 deterministic stress의 구분",
        institution="elsevier",
        topic="monte_carlo_not_stress_probability",
        claim=(
            "stochastic simulation probability와 deterministic severe scenario loss를 "
            "같은 확률로 합치지 않는다."
        ),
        canonical_url="https://doi.org/10.1016/0304-405X(77)90005-8",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Phelim P. Boyle, Journal of Financial Economics",
        bibliography=_doi_bibliography(
            doi="10.1016/0304-405X(77)90005-8",
            authors=["Phelim P. Boyle"],
            title="Options: A Monte Carlo approach",
            year=1977,
            venue="Journal of Financial Economics",
            edition_or_version="Volume 4, Number 3, pages 323-338",
        ),
        question="stress scenario 손실을 Monte Carlo 발생확률과 같은 숫자로 합쳐도 되나요?",
        scope="specified stochastic law의 simulation estimate와 designed severe scenario를 구분한다.",
        application="결과에 stochastic probability estimate와 scenario severity label을 별도 field로 둔다.",
        limitation="scenario plausibility나 실제 발생빈도를 이 카드가 정량화하지 않는다.",
        forbidden="deterministic stress loss에 sampling probability를 임의 부여하지 않는다.",
        evidence_summary=(
            "Phelim P. Boyle|Options: A Monte Carlo approach|1977|"
            "Journal of Financial Economics|10.1016/0304-405X(77)90005-8"
        ),
        assumption_key="STOCHASTIC_PROBABILITY_NOT_STRESS_PROBABILITY",
        assumption_statement=(
            "stochastic sampling probability와 designed stress severity를 별도 evidence로 유지한다."
        ),
    ),
    _scholarly(
        source_id="src_project_valuation_delta_not_guard_delta_001",
        card_id="card_valuation_delta_not_guard_delta_001",
        title="Valuation delta와 deterministic hard-guard delta의 권한 분리",
        institution="university_of_chicago_press",
        topic="valuation_delta_not_guard_delta",
        claim=(
            "model valuation delta는 운영상 conservative hard-guard delta의 authority가 아니다."
        ),
        canonical_url="https://doi.org/10.1086/260062",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Fischer Black and Myron Scholes; project deterministic rule catalog",
        bibliography=BLACK_SCHOLES,
        question="option valuation delta로 deterministic risk guard를 대체해도 되나요?",
        scope="option sensitivity 설명과 project rule-catalog authority 분리에 적용한다.",
        application="valuation output은 advisory input으로만 전달하고 hard guard는 versioned rule catalog가 결정한다.",
        limitation="이 카드는 project rule threshold의 현재 숫자를 복제하지 않는다.",
        forbidden="valuation delta로 deterministic hard-risk decision을 우회하지 않는다.",
        evidence_summary=(
            "Black; Scholes|option valuation delta|10.1086/260062|"
            "project s2-2-system-rule-catalog.v2 authority boundary"
        ),
        assumption_key="VALUATION_DELTA_NOT_HARD_RISK_DELTA",
        assumption_statement=(
            "valuation sensitivity와 deterministic hard-guard authority를 서로 다른 versioned input으로 유지한다."
        ),
    ),
    _scholarly(
        source_id="src_project_mean_reversion_stationarity_001",
        card_id="card_mean_reversion_stationarity_001",
        title="Mean reversion과 stationarity evidence 경계",
        institution="taylor_francis",
        topic="mean_reversion_stationarity",
        claim=(
            "OU 형태를 썼다는 사실만으로 관측 가격의 mean reversion이 입증되지 않으며 "
            "unit-root/stability 검증이 필요하다."
        ),
        canonical_url="https://doi.org/10.1080/01621459.1979.10482531",
        evidence_class="PRIMARY_RESEARCH",
        attribution="David A. Dickey and Wayne A. Fuller, JASA",
        bibliography=_doi_bibliography(
            doi="10.1080/01621459.1979.10482531",
            authors=["David A. Dickey", "Wayne A. Fuller"],
            title=(
                "Distribution of the Estimators for Autoregressive Time Series "
                "with a Unit Root"
            ),
            year=1979,
            venue="Journal of the American Statistical Association",
            edition_or_version="Volume 74, Number 366a, pages 427-431",
        ),
        question="OU 식을 적었다는 이유만으로 market price가 mean reverting이라고 말할 수 있나요?",
        scope="observed series의 unit-root and stability evidence 필요성을 설명하는 범위에 적용한다.",
        application="calibration 전에 transform, sample window, unit-root와 stability diagnostics를 기록한다.",
        limitation="단일 test 통과가 모든 regime의 structural stability를 보장하지 않는다.",
        forbidden="모델 식의 형태만으로 관측 가격의 stationarity를 확정하지 않는다.",
        evidence_summary=(
            "David A. Dickey; Wayne A. Fuller|Distribution of the Estimators "
            "for Autoregressive Time Series with a Unit Root|1979|"
            "10.1080/01621459.1979.10482531"
        ),
    ),
    _scholarly(
        source_id="src_project_sharpe_drawdown_partial_metrics_001",
        card_id="card_sharpe_drawdown_partial_metrics_001",
        title="Sharpe와 maximum drawdown의 부분 위험 관점",
        institution="wiley",
        topic="sharpe_drawdown_partial_metrics",
        claim=(
            "Sharpe와 maximum drawdown은 서로 다른 위험 단면이며 한 지표가 전체 위험을 "
            "대표하지 않는다."
        ),
        canonical_url="https://doi.org/10.1111/joes.12520",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Hans Geboers, Benoît Depaire and Jan Annaert, Journal of Economic Surveys",
        bibliography=_doi_bibliography(
            doi="10.1111/joes.12520",
            authors=["Hans Geboers", "Benoît Depaire", "Jan Annaert"],
            title=(
                "A review on drawdown risk measures and their implications "
                "for risk management"
            ),
            year=2022,
            venue="Journal of Economic Surveys",
            edition_or_version="Volume 37, Number 3, pages 865-889",
        ),
        question="Sharpe가 높으면 drawdown risk도 충분히 설명됐다고 볼 수 있나요?",
        scope="return-dispersion ratio와 path-dependent drawdown measure의 차이에 적용한다.",
        application="strategy report에 return, dispersion, drawdown과 tail metric을 별도로 표시한다.",
        limitation="어떤 단일 metric 조합도 미래 손실을 완전히 설명하지 않는다.",
        forbidden="Sharpe 또는 maximum drawdown 하나를 전체 risk의 충분통계로 선언하지 않는다.",
        evidence_summary=(
            "Hans Geboers; Benoît Depaire; Jan Annaert|A review on drawdown "
            "risk measures and their implications for risk management|2022|"
            "10.1111/joes.12520"
        ),
    ),
    _scholarly(
        source_id="src_project_backtest_overfitting_001",
        card_id="card_backtest_overfitting_001",
        title="Backtest 반복 선택과 false discovery 경계",
        institution="wiley",
        topic="backtest_overfitting",
        claim=(
            "반복 선택·동일 history 재사용은 backtest false discovery를 키우므로 "
            "temporal/OOS와 multiple-testing evidence가 필요하다."
        ),
        canonical_url="https://doi.org/10.1111/1468-0262.00152",
        evidence_class="PRIMARY_RESEARCH",
        attribution="Halbert White, Econometrica",
        bibliography=_doi_bibliography(
            doi="10.1111/1468-0262.00152",
            authors=["Halbert White"],
            title="A Reality Check for Data Snooping",
            year=2000,
            venue="Econometrica",
            edition_or_version="Volume 68, Number 5, pages 1097-1126",
        ),
        question="같은 history에서 반복 선택한 최고 backtest를 그대로 채택하면 왜 안 되나요?",
        scope="data snooping과 repeated model selection의 false-discovery risk에 적용한다.",
        application="temporal split, untouched OOS, candidate count와 selection history를 evidence로 남긴다.",
        limitation="특정 correction 하나가 모든 adaptive research bias를 제거하지 않는다.",
        forbidden="반복 선택된 최고 in-sample 결과를 독립 OOS evidence로 표현하지 않는다.",
        evidence_summary=(
            "Halbert White|A Reality Check for Data Snooping|2000|"
            "Econometrica|10.1111/1468-0262.00152"
        ),
    ),
    _official(
        source_id="src_project_kis_adjusted_price_001",
        card_id="card_kis_adjusted_price_001",
        title="KIS 기간별시세 조정주가 provenance",
        institution="kis",
        topic="kis_adjusted_price",
        claim=(
            "KIS 국내주식 기간별시세를 수집할 때 FID_ORG_ADJ_PRC의 "
            "0(수정주가)·1(원주가) 선택값을 시계열 provenance에 기록한다."
        ),
        canonical_url=(
            "https://github.com/koreainvestment/open-trading-api/blob/"
            "b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/"
            "inquire_daily_itemchartprice/inquire_daily_itemchartprice.py"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국투자증권 Open Trading API 공식 GitHub sample",
        upstream_source_ids=["src_kis_marketdata_daily_001"],
        question="KIS 기간별시세에서 조정주가 선택값은 provenance에 어떻게 기록하나요?",
        scope=(
            "commit-pinned 공식 sample의 기간별시세 endpoint와 FID_ORG_ADJ_PRC 의미에만 적용한다."
        ),
        application="snapshot에는 실제 요청 선택값과 source revision을 함께 남긴다.",
        limitations=[
            "확인 범위는 pinned sample의 endpoint와 field 의미에 한정한다.",
            "미래 API 변경이나 실제 응답 값은 이 카드가 보장하지 않는다.",
        ],
        allowed_uses=[
            "일봉 시계열의 조정주가 선택 provenance 설명",
            DEFAULT_ALLOWED_USE,
        ],
        forbidden_inferences=[
            "현재가, 미래 수익률, 매수·매도 판단을 추론하지 않는다.",
            "sample의 credential 또는 실행 예시를 채택하지 않는다.",
        ],
        evidence_summary="existing S4.7A bounded evidence digest",
        verified_at="2026-07-30T05:07:41Z",
        adopted_session="S4.7A",
        access_note=(
            "공식 GitHub의 commit-pinned 국내주식 기간별시세 sample을 읽기 전용으로 확인했다."
        ),
        license_note=(
            "공식 sample 원문과 실행 예시는 corpus로 복제하지 않고 bounded evidence hash와 "
            "attribution만 보존한다."
        ),
        canonical_url_sha256=(
            "d2ff26041b01ef258e2a43310f79293631c415f3339484df04887b21fac2ee74"
        ),
        evidence_content_sha256=(
            "dd8fab24d99f359eb7e983b40e37db54187967bbd3d498c169f432f885ac2d3d"
        ),
    ),
    _official(
        source_id="src_project_opendart_status_quota_001",
        card_id="card_opendart_status_quota_001",
        title="OpenDART status 020과 가변 요청 제한",
        institution="opendart",
        topic="opendart_status_quota",
        claim=(
            "OpenDART status 020은 요청 제한 초과로 처리하되 20,000건을 영구 한도로 "
            "간주하지 않고 자동 재시도 없는 typed failure로 기록한다."
        ),
        canonical_url=(
            "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="금융감독원 전자공시 OpenDART 공식 개발가이드",
        upstream_source_ids=["src_opendart_major_report_001"],
        question="OpenDART status 020을 만나면 왜 20,000건 고정 한도로 처리하면 안 되나요?",
        scope="공식 개발가이드의 status 020 의미와 가변 제한 문구에만 적용한다.",
        application="수집기는 status 020을 typed quota failure로 보존하고 자동 재호출하지 않는다.",
        limitations=[
            "20,000건은 일반적 설명이며 계정별 영구 보장값이 아니다.",
            "현재 잔여 quota나 credential 상태를 증명하지 않는다.",
        ],
        allowed_uses=[
            "status 020의 typed failure 분류와 운영 runbook 설명",
            DEFAULT_ALLOWED_USE,
        ],
        forbidden_inferences=[
            "status 020을 성공이나 빈 결과로 바꾸지 않는다.",
            "API key의 유효성이나 현재 한도를 추론하지 않는다.",
        ],
        evidence_summary="existing S4.7A bounded evidence digest",
        verified_at="2026-07-30T05:07:41Z",
        adopted_session="S4.7A",
        access_note=(
            "OpenDART 공식 개발가이드의 메시지 설명에서 status 020과 가변 제한 문구를 확인했다."
        ),
        license_note=(
            "공식 가이드의 bounded 메시지 설명만 evidence로 보존하고 credential과 요청 예시는 "
            "저장하지 않는다."
        ),
        canonical_url_sha256=(
            "af9a8a642d8761a7df6c25fa4d7625f85e97321382dbdfedb293f52e5afccc41"
        ),
        evidence_content_sha256=(
            "61c0335c33cf77a1143c31ef6566152bf5115314214f266115c91e0f60102fc6"
        ),
    ),
    _official(
        source_id="src_project_ecos_pit_availability_001",
        card_id="card_ecos_pit_availability_001",
        title="ECOS StatisticSearch의 PIT 보수 경계",
        institution="ecos",
        topic="ecos_pit_availability",
        claim=(
            "ECOS StatisticSearch 결과를 historical PIT로 간주하지 않으며 ingestion-time "
            "availableAt와 provenance가 없으면 leakage-sensitive feature에 사용하지 않는다."
        ),
        canonical_url="https://ecos.bok.or.kr/api/",
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국은행 경제통계시스템 ECOS Open API 공식 개발명세",
        upstream_source_ids=[
            "src_ecos_api_overview_001",
            "src_ecos_statistic_search_001",
        ],
        question="ECOS StatisticSearch 값을 leakage-sensitive feature에 바로 쓰면 왜 안 되나요?",
        scope="공식 출력 필드 표에서 publication, revision, vintage semantics가 입증되지 않은 범위다.",
        application="ingestion-time availableAt와 source revision이 없으면 feature를 fail-closed한다.",
        limitations=[
            "확인한 출력 필드 표만으로 historical publication semantics는 입증되지 않았다.",
            "PIT 기능이 없다고 단정하지 않는다.",
        ],
        allowed_uses=[
            "leakage-sensitive feature의 fail-closed provenance 정책 설명",
            DEFAULT_ALLOWED_USE,
        ],
        forbidden_inferences=[
            "TIME을 publication time이나 revision vintage와 동일시하지 않는다.",
            "ECOS가 historical PIT를 지원하지 않는다고 단정하지 않는다.",
        ],
        evidence_summary="existing S4.7A bounded evidence digest",
        verified_at="2026-07-30T05:07:41Z",
        adopted_session="S4.7A",
        access_note=(
            "한국은행 ECOS 공식 Open API 개발명세의 StatisticSearch 출력 필드 표를 확인했다."
        ),
        license_note=(
            "공식 화면이나 응답 데이터를 복제하지 않고 출력 field와 bounded evidence hash만 보존한다."
        ),
        canonical_url_sha256=(
            "c096c3653729cd41e63fa5040bf8471cc95f9b0c71bd8a024788380e8f8439a4"
        ),
        evidence_content_sha256=(
            "abc45643d72a947eeefd66ebb72b3425299b851aa94a86709165e7d2c0b1b130"
        ),
    ),
    _official(
        source_id="src_project_krx_service_coverage_001",
        card_id="card_krx_service_coverage_001",
        title="KRX OpenAPI 서비스별 제공기간 범위",
        institution="krx",
        topic="krx_service_coverage",
        claim=(
            "KRX OpenAPI의 2010년 이후 제공 범위는 서비스 목록의 개별 항목별 시작일에 "
            "한정해 해석하고 전체 시장·상품에 일반화하지 않는다."
        ),
        canonical_url=(
            "https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd"
        ),
        evidence_class="OFFICIAL_SERVICE_DOCUMENTATION",
        attribution="한국거래소 KRX Data Marketplace Open API 공식 서비스 목록",
        upstream_source_ids=[
            "src_krx_openapi_service_catalog_001",
            "src_krx_openapi_terms_001",
        ],
        question="KRX OpenAPI의 2010년 이후 범위는 모든 서비스에 똑같이 적용되나요?",
        scope="공식 서비스 목록의 대상기간과 개별 서비스 시작일에만 적용한다.",
        application="registry에는 조회 서비스 ID와 해당 항목의 명시된 시작일을 함께 기록한다.",
        limitations=[
            "한 서비스의 시작일은 다른 서비스의 시작일을 대신하지 않는다.",
            "현재 entitlement 보유 여부를 증명하지 않는다.",
        ],
        allowed_uses=[
            "KRX source coverage를 서비스별로 검증하는 provenance 설명",
            DEFAULT_ALLOWED_USE,
        ],
        forbidden_inferences=[
            "모든 시장·상품·field가 같은 날짜부터 제공된다고 일반화하지 않는다.",
            "현재 entitlement나 API key 보유를 주장하지 않는다.",
        ],
        evidence_summary="existing S4.7A bounded evidence digest",
        verified_at="2026-07-30T05:07:41Z",
        adopted_session="S4.7A",
        access_note=(
            "KRX Data Marketplace Open API 공식 서비스 목록의 대상기간 안내를 확인했다."
        ),
        license_note=(
            "서비스 목록의 bounded 문구만 보존하며 API key와 응답 원문은 저장하지 않는다."
        ),
        canonical_url_sha256=(
            "c7677f6db761f5a209f209df993ca6e84a96ab013dcccac707d96c497c70cd35"
        ),
        evidence_content_sha256=(
            "016eb702d7f587c6ae7a50f76c7ec24a1e2fe80b2d8a10709230b0ef9b3567ed"
        ),
    ),
    _official(
        source_id="src_project_gold_futures_etf_132030_001",
        card_id="card_gold_futures_etf_132030_001",
        title="132030 금선물 ETF의 선물·환헤지·롤오버 경계",
        institution="samsungfund",
        topic="gold_futures_etf_132030",
        claim=(
            "132030 KODEX 골드선물(H)은 S&P GSCI Gold Index Total Return을 추종하는 "
            "COMEX 금선물 기반 환헤지 상품이므로 현물 금과 동일한 성과로 간주하지 않는다."
        ),
        canonical_url="https://www.samsungfund.com/etf/product/view.do?id=2ETF24",
        evidence_class="OFFICIAL_PRODUCT_DOCUMENTATION",
        attribution="삼성자산운용 Kodex 공식 KODEX 골드선물(H) 상품 페이지",
        upstream_source_ids=["src_samsungfund_gold_futures_etf_001"],
        question="132030 KODEX 골드선물(H)을 현물 금과 같은 투자로 보면 왜 안 되나요?",
        scope="공식 상품 페이지의 종목코드, 기초지수, 선물 exposure, 환헤지와 rollover에 적용한다.",
        application="설명에는 futures exposure, 환헤지와 roll cost 가능성을 함께 제시한다.",
        limitations=[
            "선물 rollover와 환헤지 효과로 현물 금과 성과가 달라질 수 있다.",
            "live NAV, 가격, 수익률과 미래 성과는 범위 밖이다.",
        ],
        allowed_uses=[
            "132030 상품 구조와 basis, rollover, hedge 한계 설명",
            DEFAULT_ALLOWED_USE,
        ],
        forbidden_inferences=[
            "투자 권유, 수익 보장, live 가격을 추론하지 않는다.",
            "현물 금과 동일한 성과라고 표현하지 않는다.",
        ],
        evidence_summary="existing S4.7A bounded evidence digest",
        verified_at="2026-07-30T05:07:41Z",
        adopted_session="S4.7A",
        access_note=(
            "삼성자산운용 공식 상품 페이지에서 상품 구조의 bounded 항목을 확인했다."
        ),
        license_note=(
            "live 가격과 장문 상품 원문은 저장하지 않고 안정적인 구조의 bounded evidence만 보존한다."
        ),
        canonical_url_sha256=(
            "7262d88ba2d97eb2378a5240d4e2fc43f4d40264ff7d2f62ede809760ac35364"
        ),
        evidence_content_sha256=(
            "c10e221942fb845c8ae8aa8a04240e5c6cc717cf098a5328b58911c266c34a7d"
        ),
    ),
    _official(
        source_id="src_project_kis_current_price_snapshot_001",
        card_id="card_kis_current_price_snapshot_001",
        title="KIS 현재가의 bounded snapshot 경계",
        institution="kis",
        topic="kis_current_price_snapshot",
        claim=(
            "KIS current-price는 bounded snapshot이며 historical/PIT series를 대신하지 않는다."
        ),
        canonical_url=(
            "https://github.com/koreainvestment/open-trading-api/blob/"
            "b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/"
            "inquire_price/inquire_price.py"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국투자증권 Open Trading API 공식 현재가 sample",
        upstream_source_ids=["src_kis_marketdata_price_001"],
        question="KIS 현재가 응답을 historical PIT series로 사용해도 되나요?",
        scope="commit-pinned 공식 current-price sample의 snapshot endpoint 의미에만 적용한다.",
        application="snapshot에는 observedAt, ingestedAt, source revision과 request scope를 기록한다.",
        limitations=["단일 snapshot은 revision history나 historical availability를 제공하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "현재가 snapshot provenance 설명"],
        forbidden_inferences=["현재 snapshot을 과거 시점의 PIT observation으로 소급하지 않는다."],
        evidence_summary=(
            "KIS official pinned inquire_price sample|snapshot endpoint|"
            "commit b093e42ba32d1df5f5ddad7a71cb715cbc800832"
        ),
    ),
    _official(
        source_id="src_project_kis_market_calendar_001",
        card_id="card_kis_market_calendar_001",
        title="KIS 휴장일과 거래일 as-of 경계",
        institution="kis",
        topic="kis_market_calendar",
        claim="휴장일·거래일 판정은 공식 calendar source와 as-of를 보존한다.",
        canonical_url=(
            "https://github.com/koreainvestment/open-trading-api/blob/"
            "b093e42ba32d1df5f5ddad7a71cb715cbc800832/examples_llm/domestic_stock/"
            "chk_holiday/chk_holiday.py"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국투자증권 Open Trading API 공식 휴장일 sample",
        upstream_source_ids=["src_kis_market_calendar_001"],
        question="거래일 판정에 어떤 provenance를 남겨야 하나요?",
        scope="commit-pinned 공식 holiday endpoint와 조회 as-of의 조합에 적용한다.",
        application="calendar snapshot에 market, date, as-of, source revision과 판정값을 보존한다.",
        limitations=["향후 임시휴장이나 provider contract 변경은 새 snapshot 검증이 필요하다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "휴장일 fail-closed 정책 설명"],
        forbidden_inferences=["오래된 calendar snapshot을 현재 거래 가능성의 보장으로 쓰지 않는다."],
        evidence_summary=(
            "KIS official pinned chk_holiday sample|calendar endpoint|"
            "commit b093e42ba32d1df5f5ddad7a71cb715cbc800832"
        ),
    ),
    _official(
        source_id="src_project_kis_rate_limit_token_001",
        card_id="card_kis_rate_limit_token_001",
        title="KIS REST와 token issuance 유량 분리",
        institution="kis",
        topic="kis_rate_limit_token",
        claim=(
            "REST와 token issuance budget을 분리하고 공식 상한 아래 fail-closed한다."
        ),
        canonical_url=(
            "https://apiportal.koreainvestment.com/community/"
            "10000000-0000-0011-0000-000000000001/post/"
            "d0d1a83f-6f8d-4437-9700-6d26702fd989"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국투자증권 Open API 공식 호출 유량 공지",
        upstream_source_ids=[
            "src_kis_rate_limit_001",
            "src_kis_openapi_overview_001",
        ],
        question="KIS REST와 token 발급을 하나의 retry budget으로 합쳐도 되나요?",
        scope="공식 REST, 모의, token issuance 유량 공지와 project fail-closed policy에 적용한다.",
        application="credential mode별 opaque scope에서 REST limiter와 token limiter를 분리한다.",
        limitations=["공식 상한은 현재 잔여 quota나 성공 가능성을 보장하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "rate-limit fail-closed 정책 설명"],
        forbidden_inferences=["유량 상한을 자동 재시도 또는 burst 허가로 해석하지 않는다."],
        evidence_summary=(
            "KIS official rate-limit notice|production REST 18 per second|"
            "mock REST 1 per second|token issuance 1 per second"
        ),
    ),
    _official(
        source_id="src_project_kis_discovery_write_boundary_001",
        card_id="card_kis_discovery_write_boundary_001",
        title="KIS discovery와 write activation 권한 분리",
        institution="kis",
        topic="kis_discovery_write_boundary",
        claim=(
            "read/discovery 지원은 order/cancel/reconcile write 활성화가 아니다."
        ),
        canonical_url="https://apiportal.koreainvestment.com/about-open-api",
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="한국투자증권 Open API 공식 소개와 pinned endpoint registry",
        upstream_source_ids=[
            "src_kis_openapi_overview_001",
            "src_kis_trading_cash_order_001",
            "src_kis_account_balance_001",
        ],
        question="read endpoint를 확인한 것이 write 기능 승인도 뜻하나요?",
        scope="공식 API surface discovery와 project의 별도 live-write gate 분리에 적용한다.",
        application="read allowlist와 write capability를 별도 policy, credential과 activation state로 둔다.",
        limitations=["공식 write endpoint 존재는 project 배포의 write 권한을 생성하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "capability boundary 설명"],
        forbidden_inferences=["read 지원을 live write activation이나 account authority로 확대하지 않는다."],
        evidence_summary=(
            "KIS official overview|read and trading surfaces are distinct|"
            "project write gate remains disabled"
        ),
        assumption_key="DISCOVERY_NOT_WRITE_ACTIVATION",
        assumption_statement=(
            "API surface discovery와 live write activation은 별도 승인 상태로 유지한다."
        ),
    ),
    _official(
        source_id="src_project_opendart_corporation_code_001",
        card_id="card_opendart_corporation_code_001",
        title="OpenDART 고유번호 join과 revision 경계",
        institution="opendart",
        topic="opendart_corporation_code",
        claim="corp code join·revision/as-of 경계를 보존한다.",
        canonical_url=(
            "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="금융감독원 OpenDART 공식 고유번호 개발가이드",
        upstream_source_ids=["src_opendart_corporation_code_001"],
        question="OpenDART corp code mapping에는 어떤 revision 경계가 필요한가요?",
        scope="공식 고유번호 source와 기업 identifier join에 적용한다.",
        application="mapping snapshot에 corp code, source revision, retrievedAt와 valid-as-of를 기록한다.",
        limitations=["회사명만으로 영구 동일 identity를 보장하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "corp code join provenance 설명"],
        forbidden_inferences=["현재 mapping을 모든 과거 시점의 identity로 소급하지 않는다."],
        evidence_summary="OpenDART official corporation code guide|identifier mapping|revision boundary",
    ),
    _official(
        source_id="src_project_opendart_financial_statement_scope_001",
        card_id="card_opendart_financial_statement_scope_001",
        title="OpenDART 재무제표 endpoint와 account scope",
        institution="opendart",
        topic="opendart_financial_statement_scope",
        claim="statement endpoint·period/account scope와 status semantics를 보존한다.",
        canonical_url=(
            "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016"
        ),
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="금융감독원 OpenDART 공식 단일회사 주요계정 개발가이드",
        upstream_source_ids=["src_opendart_financial_statement_001"],
        question="OpenDART 주요계정 값을 비교할 때 어떤 scope를 고정해야 하나요?",
        scope="공식 statement endpoint의 corporation, business year, report code와 account scope에 적용한다.",
        application="snapshot에 endpoint, period, report code, account identifier와 status를 함께 보존한다.",
        limitations=["주요계정 endpoint가 전체 주석과 모든 연결 범위를 대신하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "financial statement scope provenance 설명"],
        forbidden_inferences=["서로 다른 period, report code 또는 account scope를 같은 series로 합치지 않는다."],
        evidence_summary="OpenDART official financial statement guide|period|report code|account scope",
    ),
    _official(
        source_id="src_project_krx_etf_etn_structure_001",
        card_id="card_krx_etf_etn_structure_001",
        title="KRX ETF fund와 ETN issuer-credit 구조 구분",
        institution="krx",
        topic="krx_etf_etn_structure",
        claim="ETF fund와 ETN issuer-credit structure를 혼동하지 않는다.",
        canonical_url="https://open.krx.co.kr/contents/OPN/01/01030100/OPN01030100.jsp",
        evidence_class="OFFICIAL_SERVICE_DOCUMENTATION",
        attribution="한국거래소 정보데이터시스템 공식 증권상품 소개",
        upstream_source_ids=["src_krx_etf_etn_structure_001"],
        question="ETF와 ETN을 같은 법적·credit 구조로 설명해도 되나요?",
        scope="KRX 공식 상품 구조 설명에서 fund와 issuer note의 차이에 적용한다.",
        application="상품 설명에 vehicle type, issuer 또는 fund structure와 credit-risk boundary를 표시한다.",
        limitations=["개별 상품의 현재 credit quality나 투자 적합성을 산출하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "ETF와 ETN 구조 차이 설명"],
        forbidden_inferences=["ETN을 ETF fund 지분과 같은 구조로 표현하지 않는다."],
        evidence_summary="KRX official securities product overview|ETF fund|ETN issuer credit structure",
    ),
    _official(
        source_id="src_project_krx_etn_risk_indicator_001",
        card_id="card_krx_etn_risk_indicator_001",
        title="KRX ETN risk indicator의 정의와 한계",
        institution="krx",
        topic="krx_etn_risk_indicator",
        claim="ETN risk indicator의 공식 정의·한계를 보존한다.",
        canonical_url="https://open.krx.co.kr/contents/OPN/01/01030302/OPN01030302.jsp",
        evidence_class="OFFICIAL_SERVICE_DOCUMENTATION",
        attribution="한국거래소 정보데이터시스템 공식 ETN 투자지표",
        upstream_source_ids=["src_krx_etn_risk_indicator_001"],
        question="KRX ETN risk indicator 하나로 전체 위험을 판단해도 되나요?",
        scope="공식 페이지에 정의된 ETN indicator의 이름, 산식 의미와 표시 범위에 적용한다.",
        application="indicator value에는 definition version, as-of와 product identity를 함께 표시한다.",
        limitations=["단일 indicator는 liquidity, issuer credit와 future loss를 완전히 설명하지 않는다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "ETN indicator bounded 설명"],
        forbidden_inferences=["indicator 하나를 투자 적합성 또는 전체 위험 보장으로 확대하지 않는다."],
        evidence_summary="KRX official ETN indicator page|definition|scope limitation",
    ),
    _official(
        source_id="src_project_krx_last_trading_settlement_001",
        card_id="card_krx_last_trading_settlement_001",
        title="KRX last trading instant와 final settlement date 구분",
        institution="krx",
        topic="krx_last_trading_settlement",
        claim="last trading instant와 final settlement date는 별도 값이다.",
        canonical_url=(
            "https://global.krx.co.kr/contents/GLB/02/0201/0201040202/"
            "GLB0201040202.jsp"
        ),
        evidence_class="OFFICIAL_SERVICE_DOCUMENTATION",
        attribution="한국거래소 Global KRX 공식 KOSPI 200 Options specification",
        upstream_source_ids=[
            "src_krx_etf_etn_structure_001",
            "src_krx_openapi_service_catalog_001",
        ],
        question="last trading day와 final settlement day를 같은 expiry 값으로 써도 되나요?",
        scope="KRX product specification이 두 날짜와 last-day trading hours를 별도 field로 제시하는 범위다.",
        application="contract snapshot에 lastTradingAt과 finalSettlementDate를 별도 timezone-aware field로 둔다.",
        limitations=["product별 날짜와 settlement 방식은 해당 contract specification을 다시 확인해야 한다."],
        allowed_uses=[DEFAULT_ALLOWED_USE, "derivatives contract time boundary 설명"],
        forbidden_inferences=["settlement date를 last trading instant로 대체하지 않는다."],
        evidence_summary=(
            "Global KRX KOSPI 200 Options|Last Trading Day|Final Settlement Day|"
            "official product specification"
        ),
        assumption_key="LAST_TRADING_AT_NOT_SETTLEMENT_DATE",
        assumption_statement=(
            "lastTradingAt과 finalSettlementDate를 서로 다른 contract field로 유지한다."
        ),
    ),
    _official(
        source_id="src_project_naver_news_discovery_boundary_001",
        card_id="card_naver_news_discovery_boundary_001",
        title="Naver News Search discovery와 원문 authority 경계",
        institution="naver",
        topic="naver_news_discovery_boundary",
        claim="search metadata는 discovery/reference-only이며 원문 authority·영속 corpus가 아니다.",
        canonical_url="https://developers.naver.com/docs/serviceapi/search/news/news.md",
        evidence_class="OFFICIAL_API_DOCUMENTATION",
        attribution="네이버 개발자센터 공식 뉴스 검색 API 문서와 legacy 종료 공지",
        upstream_source_ids=[
            "src_naver_news_search_001",
            "src_naver_legacy_sunset_001",
        ],
        question="Naver Search API metadata를 기사 원문 corpus로 저장해도 되나요?",
        scope=(
            "current Search API의 discovery metadata 범위와 legacy API sunset limitation을 "
            "서로 분리해 적용한다."
        ),
        application="검색 결과는 locator discovery로만 쓰고 기사 원문 authority와 retention을 별도 검증한다.",
        limitations=[
            "current Search API 지원은 legacy API 지속을 의미하지 않는다.",
            "기사 본문 fetch, 재배포 또는 provider 전송 권한을 만들지 않는다.",
        ],
        allowed_uses=[DEFAULT_ALLOWED_USE, "뉴스 locator discovery 경계 설명"],
        forbidden_inferences=["검색 metadata를 기사 원문 또는 영속 content license로 해석하지 않는다."],
        evidence_summary=(
            "Naver Developers current News Search API documentation|"
            "legacy API sunset notice|discovery metadata only"
        ),
    ),
)


def _payload(definition: dict[str, Any]) -> dict[str, Any]:
    canonical_url = definition["canonicalUrl"]
    assumption_key = definition.get("assumptionKey")
    assumption_statement = definition.get("assumptionStatement")
    if bool(assumption_key) != bool(assumption_statement):
        raise ValueError("Stable assumption key and statement must be paired.")
    payload: dict[str, Any] = {
        "schemaVersion": "2",
        "cardVariant": definition["cardVariant"],
        "sourceId": definition["sourceId"],
        "cardId": definition["cardId"],
        "title": definition["title"],
        "institution": definition["institution"],
        "topic": definition["topic"],
        "sourceType": "PROJECT_SOURCE_CARD",
        "tier": "PROJECT",
        "accessLevel": "PUBLIC",
        "claim": definition["claim"],
        "evidenceClass": definition["evidenceClass"],
        "status": "VERIFIED",
        "verifiedAt": definition["verifiedAt"],
        "accessNote": definition["accessNote"],
        "licenseNote": definition["licenseNote"],
        "attribution": definition["attribution"],
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": (
            definition.get("canonicalUrlSha256") or _sha256_text(canonical_url)
        ),
        "evidenceContentSha256": (
            definition.get("evidenceContentSha256")
            or _sha256_text(definition["evidenceSummary"])
        ),
        "upstreamSourceIds": definition["upstreamSourceIds"],
        "retentionOwner": "python-rag-corpus-privacy",
        "retentionDays": 365,
        "contentClass": "PROJECT_AUTHORED_SANITIZED_CARD",
        "externalProcessingAllowed": False,
        "externalProcessingGate": "NOT_GRANTED",
        "adoptedSession": definition["adoptedSession"],
        "contradicts": [],
        "modelSensitive": assumption_key is not None,
        "modelAssumptions": (
            [{"key": assumption_key, "statement": assumption_statement}]
            if assumption_key is not None
            else []
        ),
        "limitations": definition["limitations"],
        "allowedUses": definition["allowedUses"],
        "forbiddenInferences": definition["forbiddenInferences"],
        "representativeQuestions": [definition["question"]],
    }
    if definition["cardVariant"] == "SCHOLARLY_PRIMARY_CARD":
        payload["bibliographicLocator"] = definition["bibliographicLocator"]
        payload["bibliographicMetadata"] = definition["bibliographicMetadata"]
    validate_source_card_v2_payload(payload)
    return payload


def _render_body(definition: dict[str, Any]) -> str:
    limitations = "\n".join(
        f"- {item}" for item in definition["limitations"]
    )
    allowed_uses = "\n".join(
        f"- {item}" for item in definition["allowedUses"]
    )
    forbidden = "\n".join(
        f"- {item}" for item in definition["forbiddenInferences"]
    )
    return (
        f"# Source Card: {definition['title']}\n\n"
        f"## 핵심 claim\n{definition['claim']}\n\n"
        f"## 적용 범위와 전제\n{definition['scope']}\n\n"
        f"## 프로젝트 적용\n{definition['question']}\n"
        f"{definition['application']}\n\n"
        f"## 한계와 반례\n{limitations}\n\n"
        f"## 허용 사용\n{allowed_uses}\n\n"
        f"## 금지 추론\n{forbidden}\n\n"
        "## 근거 위치\n"
        "front matter의 primary/official canonicalUrl, locator SHA-256과 "
        "bounded evidenceContentSha256을 확인한다.\n"
    )


def render_cards() -> dict[str, bytes]:
    """exact definitions를 deterministic v2 Markdown bytes로 렌더링한다."""

    if len(CARD_DEFINITIONS) != 30:
        raise ValueError("S4.7B source-card generator requires exact 30 definitions.")
    rendered: dict[str, bytes] = {}
    for definition in CARD_DEFINITIONS:
        payload = _payload(definition)
        front_matter = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )
        text = f"---\n{front_matter}---\n{_render_body(definition)}"
        normalized = unicodedata.normalize("NFC", text)
        filename = f"{definition['sourceId']}.md"
        if filename in rendered:
            raise ValueError("S4.7B source-card generator produced a duplicate filename.")
        rendered[filename] = normalized.encode("utf-8")
    return rendered


def _artifact_relative_path(path: Path) -> str:
    """source-card artifacts는 owned `capstone-rag` subtree 밖으로 나갈 수 없다."""

    try:
        return path.relative_to(CAPSTONE_RAG_ROOT).as_posix()
    except ValueError as error:
        raise ValueError("S4.7B artifact path escapes the approved capstone-rag root.") from error


def _list_cards() -> dict[str, bytes]:
    return list_approved_regular_files(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_directory=_artifact_relative_path(S4_7B_SOURCE_CARD_ROOT),
        max_entries=_MAX_CARD_ENTRIES,
        max_bytes=_MAX_CARD_BYTES,
    )


def _read_manifest() -> bytes:
    return read_approved_regular_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(S4_7B_CORPUS_MANIFEST_PATH),
        max_bytes=_MAX_MANIFEST_BYTES,
    ).content


def _write_artifact(path: Path, content: bytes, *, max_bytes: int) -> None:
    write_approved_generated_file(
        approved_root=CAPSTONE_RAG_ROOT,
        relative_path=_artifact_relative_path(path),
        content=content,
        max_bytes=max_bytes,
    )


def _write_artifacts() -> dict[str, Any]:
    rendered = render_cards()
    existing = set(_list_cards())
    unexpected = existing - set(rendered)
    if unexpected:
        raise ValueError(
            f"Refusing to remove unexpected source-card artifacts: {sorted(unexpected)}"
    )
    for filename, content in rendered.items():
        _write_artifact(
            S4_7B_SOURCE_CARD_ROOT / filename,
            content,
            max_bytes=_MAX_CARD_BYTES,
        )
    manifest = build_source_card_corpus_manifest()
    _write_artifact(
        S4_7B_CORPUS_MANIFEST_PATH,
        (
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    return manifest


def _check_artifacts() -> dict[str, Any]:
    rendered = render_cards()
    try:
        existing = _list_cards()
    except ValueError as error:
        raise ValueError("Tracked S4.7B source-card root is missing.") from error
    if existing != rendered:
        raise ValueError("Tracked S4.7B source-card artifacts are stale.")
    expected_manifest = build_source_card_corpus_manifest()
    try:
        tracked_manifest = json.loads(_read_manifest().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Tracked S4.7B corpus manifest is missing or invalid.") from error
    if tracked_manifest != expected_manifest:
        raise ValueError("Tracked S4.7B corpus manifest is stale.")
    return expected_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the exact S4.7B project source-card corpus."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        manifest = _write_artifacts() if args.write else _check_artifacts()
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S4_7B_PROJECT_SOURCE_CARDS_30_CORPUS_FROZEN")
    print(f"financeCards={manifest['financeCards']}")
    print(f"officialCards={manifest['officialCards']}")
    print(
        "upstreamReferenceCardsExcluded="
        f"{manifest['upstreamReferenceCardsExcluded']}"
    )
    print(f"corpusManifestSha256={manifest['corpusManifestSha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
