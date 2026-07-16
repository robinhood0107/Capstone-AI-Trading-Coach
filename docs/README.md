# docs

팀 배포 기준 문서. 원본 관리처는 로컬(개인 정리 폴더)이며, 갱신 시 이 폴더에 같은 파일명으로 동기화한다.

## 현재 완료 상태

S1.3 ECOS/Naver 내부 source snapshot은 PR #16, S1.3K KRX OPEN API universe 자동화는 PR #17로 2026-07-16 `main`에 병합됐다. 완료 상태와 단계형 provider 복구 규칙은 [최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) 11.1.3~11.1.5, 내부 API/CLI 경계는 [API_명세서.md](API_명세서.md) 13.5, 실제 운영 명령과 검증 결과는 [Decision Platform README](../workspaces/decision-platform/README.md)를 따른다.

| 문서 | 내용 |
|---|---|
| [최종_프로젝트_명세서.md](최종_프로젝트_명세서.md) | 프로젝트 전체 명세 — 방향, 시스템 구조, 모노레포 설계, 역할분담, 팀별 축소 계획(18.A) |
| [API_명세서.md](API_명세서.md) | Decision Platform API 전체 명세 — REST/gRPC 계약, 오류 코드, fail-closed 정책 |
| [S0_2_P0_계약_통합_필드명_결정.md](S0_2_P0_계약_통합_필드명_결정.md) | S0.2 P0 contracts 통합 기준 — order intent, signal artifact/API view, HMM regime, Return Engine export 필드명 결정 |
| [decision-platform/S1_2_OpenDART_공시위험점수_근거.md](decision-platform/S1_2_OpenDART_공시위험점수_근거.md) | S1.2 OpenDART client와 disclosure_risk_score 설계 근거 — 공식 endpoint, 점수 한계, 신뢰성 단계 |
| [중간보고서_작성용_초기설계.md](중간보고서_작성용_초기설계.md) | 중간보고서 작성 가이드 — 문장/표/그림 초안, RISE 심사기준 대응 |
| [선물옵션_모의주문_확장_시나리오.md](선물옵션_모의주문_확장_시나리오.md) | P2 국내선물옵션 확장 설계 (v1 범위 아님, 기본 OFF) |
| [선물옵션_모의주문_확장_시나리오_API_명세서.md](선물옵션_모의주문_확장_시나리오_API_명세서.md) | P2 확장 API 계약 + KIS TR_ID 매핑 |
| `adr/` | 공개 가능한 아키텍처 결정 요약을 이관하는 자리 (현재 비어 있음 — 상세 ADR은 개인 노트에서 관리) |
| `decision-platform/` | Decision Platform 공개 기술 문서 — 현재 S1.2 OpenDART 공시위험점수 근거 문서를 포함 |
