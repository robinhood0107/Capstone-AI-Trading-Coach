# experience-dashboard

담당: 팀원 A / P1 full-app v2 integration owner

README-only placeholder 경계는 `HISTORICAL_SUPERSEDED`다. 수신 원본은 ignored
`dev/upstream-intake/<manifest-sha256>`에 보존하고, 검토·보완된 same-origin production source, lockfile,
테스트와 Dockerfile만 이 workspace에 승격한다. 현재 수신본에는 lockfile, 테스트, Dockerfile, RAG v2,
계정/백업/시장데이터 UI가 없어 `DASHBOARD_UI=PARTIAL_TEAM_A_ACTION_REQUIRED`다.

예상 구조 (최종 프로젝트 명세서 6.1):

```

완료 요구사항은 [Team A 완료 요청서](../../docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md)를
따른다. mock transport와 dev intake는 production image와 release archive에서 제외한다.
src/
  app/                    # Next.js routes
  features/
    model-evaluation/     # Model Evaluation ViewModel + UI
    backtest-report/      # equity/drawdown/monthly/scenario views
    order-review/         # Risk Result Display
    rag-source/           # RAG Source Display
  shared/                 # API client, chart primitives, UI helpers
```
