# Team B 수신 경로

`tests/e2e/team_intake_e2e.py` · 증거 `artifacts/decision-platform/e2e/team-intake.json`

관통 테스트는 **검증을 통과한 뒤의 import packet부터** 시작한다. 그 앞 구간 — Team B가 실제로
건네는 10개 파일이 검증을 통과하는가 — 은 여기서 본다.

| 기능 | 방식 | 결과 |
|---|---|---|
| 번들 목록 | stack | PASS — 매니페스트 1 + 산출물 10 |
| 틀린 매니페스트 해시는 닫힌다 | stack | PASS — 적재 열리지 않음, 번들 수 불변 |
| production 적재기 실행 | stack | PASS — 번들 0→1 |
| 무결성 영수증과 신호 투영 | stack | PASS — 해시 일치, 신호 투영 62행 |
| 정리 | stack | PASS — 잔여 0 |

적재는 `artifact-importer` compose 서비스가 production 적재기(`app.p1_owner.importer`)를 그대로
실행한다. 테스트가 행을 손으로 쓰지 않는다.

관측된 표식: `evidence_mode=SYNTHETIC_GOLDEN`, `real_team_b=false`,
`model_quality=NOT_EVALUATED_SYNTHETIC`. **이 runner가 증명하는 것은 경로이지 산출물의 품질이
아니다.**

## 아직 이 증거가 의미하지 않는 것

- 실제 Team B 산출물이 아니다. 여기서 쓴 번들은 합성 golden이고 `evidence_mode`가 그 사실을 말한다.
- 실제 Team A UI가 아니다. 대시보드는 현재 tree의 화면이며 최종본이 아니다.
- 실계좌 거래가 아니다. KIS Live 호출은 영구 금지이고 이 기록 어디에도 그 경로가 없다.
- 시장이 열린 시간의 실체결이 아니다. 그 항목은 `P1_장시간_의존_항목.md`가 따로 다룬다.
