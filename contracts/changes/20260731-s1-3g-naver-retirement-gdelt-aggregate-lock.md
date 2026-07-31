# S1.3G Naver 퇴역과 GDELT aggregate 계약 고정

상태: `APPROVED_CONTRACT_LOCK`  
관련 Issue: #20  
승인 표식: `AUTH_NAVER_RETIREMENT_GDELT_AGGREGATE_CONTRACT=APPROVED`

## KR: 변경 이유

Naver Search 결과의 제목·설명·URL을 영속화하던 active provider/runtime/storage 경계를
퇴역한다. 기존 project-authored Naver discovery boundary source card와 과거 감사 기록은
보존하지만, 2026-07-14 S1.3 계약은 active 뉴스 입력 권한을 더 이상 부여하지 않는다.

후속 뉴스 입력은 GDELT `TimelineTone`과 `TimelineVolRaw`에서 만든 bounded aggregate만
허용한다. 기사 본문·제목·URL·domain·article ID·raw query는 schema와 fixture에서 거부한다.
GDELT 출처는 `The GDELT Project`, 프로젝트 URL과 Terms of Use가 있는 공식 About URL을
항상 함께 기록한다.

## EN: Rationale

The active provider/runtime/storage boundary that persisted Naver Search titles,
descriptions, and URLs is retired. The project-authored Naver discovery boundary source
card and historical audit records remain, but the 2026-07-14 S1.3 contract no longer grants
active news-input authority.

Future news input is limited to bounded aggregates derived from GDELT `TimelineTone` and
`TimelineVolRaw`. Schemas and fixtures reject article bodies, titles, URLs, domains, article
identifiers, and raw queries. Every artifact carries the GDELT Project citation, project URL,
and the official About page containing the Terms of Use.

## 계약 / Contracts

- `gdelt_news_tone_observation.v1`: `AVAILABLE | ABSTAIN`, exact two aggregate modes,
  canonical UTC chronology, at most 512 points, finite tone/count/norm/coverage values,
  mandatory attribution, and no article metadata.
- `news_sentiment_summary.v2`: `AVAILABLE | ABSTAIN`, aggregate-only explanation payload,
  no fake zero on abstention, `decisionAuthority=NONE`, RiskDecision hash exclusion, and
  `s5FeatureEligible=false`.
- `news_sentiment_summary` v1 prose/example is superseded. v2 is a breaking, closed-object
  contract and does not silently widen v1.

## 권한과 영향 / Authority and impact

```text
NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED
GDELT_PROVIDER_PHYSICAL_CALLS=0
GDELT_ARTICLE_METADATA_STORAGE=0
GDELT_DECISION_AUTHORITY=NONE
GDELT_S5_FEATURE_ELIGIBLE=FALSE
SECURITY_SCAN_TIMING=FINAL_CONSOLIDATED_CAMPAIGN
```

Wave A1 changes contracts, fixtures, generator tests, ADR, and public specifications only.
Python/Kotlin runtime removal, the exact approved local deletion, GDELT producer code, DB
migrations, and provider activation belong to later implementation waves. External calls,
account calls, order calls, and model calls remain zero.

## 호환성 / Compatibility

- ECOS snapshot and retention contracts remain active and unchanged.
- The exact 30-card RAG corpus and Naver policy-boundary card remain immutable.
- Return Engine may consume v2 only after a separate cross-workspace implementation; its
  placeholder workspace is unchanged here.
- News/GDELT artifacts cannot alter RiskDecision, semantic hashes, orders, or S5 model input.
- The final consolidated security campaign runs only after all approved offline implementation
  and general gates finish; secret scanning and ordinary regression tests continue meanwhile.
