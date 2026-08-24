# experience-dashboard

담당: 팀원 A

이 계정(개인 레포)에서는 구조 자리만 확보해 둔다. 실제 구현은 팀원 A의 워크스페이스에서 진행되며, 팀 공용 레포로 합류할 때 이 폴더에 병합한다. `.gitignore`에 의해 이 폴더의 `README.md`를 제외한 파일은 이 레포에 커밋되지 않는다.

예상 구조 (최종 프로젝트 명세서 6.1):

```
src/
  app/                    # Next.js routes
  features/
    model-evaluation/     # Model Evaluation ViewModel + UI
    backtest-report/      # equity/drawdown/monthly/scenario views
    order-review/         # Risk Result Display
    rag-source/           # RAG Source Display
  shared/                 # API client, chart primitives, UI helpers
```
