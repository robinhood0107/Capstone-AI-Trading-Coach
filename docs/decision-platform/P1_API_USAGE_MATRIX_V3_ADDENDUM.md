# P1 API usage matrix V3 addendum

기존 `P1_API_USAGE_MATRIX.md` 69개 행은 역사적 회귀로 그대로 보존한다. 아래
6개 additive operation을 합치면 현재 root는 exact-75다. 여섯 행은 모두
`p1-team-a-acceptance.v3`의 `Team A 필수`다.

| 번호 | Method | Path | 분류 | 용도 |
|---:|---|---|---|---|
| 70 | GET | `/api/v3/automation/status` | `Team A 필수` | 사용자 청산정책, AI enable/thinking snapshot, 시장이력과 arm blocker를 읽는다 |
| 71 | PUT | `/api/v3/automation/policy` | `Team A 필수` | 보유 만기·ATR·MODEL_SELL을 포함한 versioned 정책을 CAS로 저장한다 |
| 72 | POST | `/api/v3/automation/arm` | `Team A 필수` | legacy position·시장데이터·AI provider readiness를 모두 통과한 KIS Mock V3만 arm한다 |
| 73 | GET | `/api/v3/automation/runs` | `Team A 필수` | evidence/grounding/judge count와 settings/evidence hash를 포함한 run 목록을 읽는다 |
| 74 | GET | `/api/v3/automation/runs/{runId}` | `Team A 필수` | 소유자 run의 bounded verified evidence 상세를 읽는다 |
| 75 | GET | `/api/v3/automation/positions` | `Team A 필수` | entry snapshot, nullable expiry, peak와 trailing stop을 포함한 활성 V3 position을 읽는다 |
