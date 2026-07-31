# RAG source cards

프로젝트가 직접 작성하고 license/access/attribution/retention 검증을 통과한 source card만
추적한다. upstream 원문, 교재 본문, 장문 인용, provider 문서 전체 복사본은 두지 않는다.

## S4.7B exact 30 corpus

`s4-7b/`는 v2 closed-union 계약을 통과한 project source card 30개만 보관한다.

- 금융공학 scholarly/primary card: 15개
- 공식자료/API upstream-lineage card: 15개
- upstream reference-only registry 항목: 20개, corpus 수량에서 제외
- 외부 처리: 전 card `false`, gate `NOT_GRANTED`
- stable assumption: 지정된 12개 claim에 exact key 1개씩

card identity는 UTF-8 NFC `sourceId` byte order, canonical JSON front matter, LF body와
per-card SHA-256으로 계산한다. 원문 PDF·HTML·screenshot·provider payload·private path는
이 경계에 넣지 않는다.

아래 명령은 exact card와 manifest를 재생성하거나 drift를 검사한다.

```bash
cd workspaces/decision-platform/python-services
uv run --frozen python ../../../capstone-rag/generate_s4_7b_source_cards.py --write
uv run --frozen python ../../../capstone-rag/generate_s4_7b_source_cards.py --check
uv run --frozen pytest -q tests/rag/test_source_card_corpus.py
```

## S4.7C external-processing revision

`s4-7c-external/`은 S4.7B와 동일한 `sourceId`/`cardId`/canonical body 30개를 새 immutable
front-matter revision으로 보관한다. 각 card는 project-authored sanitized body, public locator,
attribution과 deterministic license/consent receipt를 가져야 한다. upstream 원문, provider
payload, 뉴스 기사 metadata와 private path는 포함하지 않는다.

loader는 `s4_7b_internal_v1` 또는 `s4_7c_external_v1`을 명시적으로 받아야 하며 old/new root와
manifest 혼합을 거부한다. public client는 profile을 선택하지 못한다.

```bash
uv run --frozen python ../../../capstone-rag/generate_s4_7c_external_corpus.py --check
uv run --frozen pytest -q tests/rag/test_external_processing_corpus.py
```
