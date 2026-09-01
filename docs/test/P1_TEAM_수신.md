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

## 통합 체크리스트에 대해

`docs/decision-platform/P1_TEAM_A_B_수신_후_통합_체크리스트.md`는 `HISTORICAL_SUPERSEDED`로
동결돼 있어 본문을 고칠 수 없다. 그 문서가 "production adapter는 없습니다. 통합 담당자가 다음
작업을 구현하고 테스트해야 합니다"라고 적은 대목은 지금 사실과 다르다. 사실은 이렇다.

현재 Owner 통합 순서는
[Team A·B 완료 후 Owner 최종 실행표](../decision-platform/P1_TEAM_A_B_완료_후_OWNER_최종_실행.md)가
소유한다.

- 어댑터는 **이미 있다.** V88의 `import_p1_return_bundle_v1`이 그것이고, V90/V91 런타임이 그
  신호를 자동운용으로 잇는다.
- 그 문서의 네 항목은 구현 대기가 아니라 실물 번들을 받았을 때 **확인할 목록**이다.
- 확인 방법은 이 runner와 `tests/e2e/full_pipeline_e2e.py`가 합성 번들로 같은 경로를 한 번
  관통시킨 것과 같다. `validate_artifact_bundle`이 10파일을 검증하고,
  `p1_return_signal_projection`이 신호로 투영되며, `read_p1_return_signal_v2`가 그것을 읽는다.

동결된 문서를 고치는 대신 여기에 적는다. 두 기록이 다를 때는 위 최종 실행표와 이쪽이 최신이다.
