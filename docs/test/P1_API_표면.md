# 관통 밖 REST 표면

`tests/e2e/api_surface_e2e.py` · 증거 `artifacts/decision-platform/e2e/api-surface.json`

| 기능 | 방식 | 결과 |
|---|---|---|
| 시스템 상태 | stack | PASS |
| ADMIN 관측 3종 | stack | PASS — 소유자 403 셋, 관리자 200 셋 |
| 없는 비동기 작업 조회 | stack | PASS — 404 |
| 원칙 프리셋·생성·조회·목록 | stack | PASS |
| 원칙 수정과 버전 적재 | stack | PASS — 버전 1→2, 목록 2건 |
| 일지 생성·목록·수정·삭제 | stack | PASS — 삭제 후 목록에서 사라짐 |
| v1 동의 기록 | stack | PASS |
| kill switch 읽기·켜기·되돌리기 | stack | PASS — **소유자 해제 403, 관리자 해제 200** |
| 포트폴리오 위험 조회 | stack | PASS |
| 신호 조회 | stack | PASS |
| 주문 판단 생성·단건·감사 | stack | PASS |
| 없는 판단 조회 | stack | PASS — 404 |
| 대시보드 ViewModel 4종 | stack | PASS |
| v1 RAG 출처·이력 | stack | PASS |
| 정리 | stack | PASS — 잔여 0 |

## 이 실행에서 드러난 것

- **kill switch는 비대칭이다.** 켜는 것은 소유자가 할 수 있고 끄는 것은 ADMIN만 할 수 있다.
  켜진 동안 모든 주문 판단은 `RISK_BLOCKED`로 닫힌다. runner는 이 순서를 지키려고 kill switch를
  판단보다 먼저 확인하고 반드시 되돌린다.
- **일지 삭제도 낙관적 잠금을 요구한다.** DELETE에 `expectedVersion` 본문과 멱등키가 모두 필요하다.

## 아직 이 증거가 의미하지 않는 것

- 실제 Team B 산출물이 아니다. 여기서 쓴 번들은 합성 golden이고 `evidence_mode`가 그 사실을 말한다.
- 실제 Team A UI가 아니다. 대시보드는 현재 tree의 화면이며 최종본이 아니다.
- 실계좌 거래가 아니다. KIS Live 호출은 영구 금지이고 이 기록 어디에도 그 경로가 없다.
- 시장이 열린 시간의 실체결이 아니다. 그 항목은 `P1_장시간_의존_항목.md`가 따로 다룬다.
