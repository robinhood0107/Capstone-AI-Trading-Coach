# S4.7D OA112 curriculum map

이 문서는 `s4-7d-oa140-release.v1.json`의 공개 curriculum projection이다. manifest는 총
112개 source를 고정한다. 각 track은 OCW/공식 HTML teaching·review source 2개와 arXiv
fixed PDF research source 6개로 시작한다. 원문·추출 text·embedding은 Git에 없으며, 설치자가
공식 HTTPS 원천에서 다시 hash 검증한 뒤 local cache에만 구축한다.

RAG는 근거 검색·설명·인용만 담당한다. 이 curriculum의 어떤 source도 Signal, RiskDecision,
주문 intent, order hash 또는 feature에 직접 연결하지 않는다.

| 순서 | Track | 선수 개념 | 학습 목표 | 대표 질문 |
|---:|---|---|---|---|
| 1 | `MICRO_GAME_INFO_MARKET_DESIGN` | 수요·공급, 효용, 균형 | 시장 가격·정보·전략적 상호작용을 투자 설명의 배경으로 쓴다 | “공개 정보가 가격에 반영된다는 말의 한계는 무엇인가?” |
| 2 | `MACRO_MONETARY_INTERNATIONAL` | GDP, 금리, 환율, 중앙은행 | 통화정책·국제경제 shock이 자산가격 설명에 주는 제약을 이해한다 | “금리 인상이 모든 주식에 같은 방향으로 작동하지 않는 이유는?” |
| 3 | `PROBABILITY_STATISTICS_OPTIMIZATION` | 확률변수, 추정, 최적화 | 수익률·손실분포·최적화 결과를 확정 예측으로 오해하지 않는다 | “표본 평균과 기대수익률을 같은 것으로 보면 왜 위험한가?” |
| 4 | `ECONOMETRICS_CAUSAL_EVENT_STUDY` | 회귀, 식별, p-value | event study와 인과추론의 식별 가정을 citation과 함께 노출한다 | “이벤트 전후 수익률 차이를 바로 원인 효과라고 할 수 있나?” |
| 5 | `TIME_SERIES_REGIME_VOLATILITY` | 정상성, 자기상관, 변동성 | 시계열·국면·변동성 모델의 forecast와 smoothing 경계를 구분한다 | “HMM state는 실제 시장 상태인가, 통계적 label인가?” |
| 6 | `ACCOUNTING_CORPORATE_FINANCE_VALUATION` | 재무제표, 현금흐름, 할인율 | 기업가치평가·재무관리 개념을 투자추천이 아닌 설명 근거로 쓴다 | “DCF의 할인율과 성장률 가정이 바뀌면 결론은 어떻게 흔들리나?” |
| 7 | `ASSET_PRICING_FACTOR_PORTFOLIO` | CAPM, factor, 분산투자 | factor exposure와 portfolio risk를 성과 보장식이 아닌 가정 있는 설명으로 다룬다 | “factor premium은 왜 항상 먹히는 법칙이 아닌가?” |
| 8 | `FIXED_INCOME_RATES_CREDIT` | 할인채, duration, credit spread | 금리·채권·신용 위험을 주식/ETF 리스크 설명과 연결한다 | “duration이 큰 채권 ETF는 금리 변화에 왜 민감한가?” |
| 9 | `DERIVATIVES_STOCHASTIC_NUMERICS` | payoff, no-arbitrage, 확률과정 | BSM·수치해석·파생상품을 교육/리스크 설명으로만 제한한다 | “`N(d1)`과 `N(d2)`를 실제 상승확률로 말하면 왜 틀릴 수 있나?” |
| 10 | `MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY` | 주문장, bid-ask, 체결 | slippage·liquidity·execution risk를 백테스트/실행 괴리 설명에 반영한다 | “백테스트 수익률이 실제 체결 수익률과 달라지는 이유는?” |
| 11 | `RISK_STRESS_BACKTEST_MODEL_RISK` | VaR, drawdown, backtest | stress와 model risk를 확률분포 예측과 분리한다 | “과최적화된 전략을 어떻게 의심할 수 있나?” |
| 12 | `BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING` | EMH, anomaly, 행동편향 | anomaly·crowding을 단정적 매매공식이 아닌 불안정한 근거로 표현한다 | “시장 anomaly가 알려진 뒤 약해질 수 있는 이유는?” |
| 13 | `FINANCIAL_ML_PIT_DATA_PROVENANCE` | train/test split, leakage, provenance | ML feature와 PIT data boundary를 설명하고 look-ahead를 차단한다 | “모델 성능이 좋아 보여도 leakage일 수 있는 신호는?” |
| 14 | `CROSS_MARKET_COMMODITIES_POLICY_KOREA` | 국제무역, 정책전달, 교차시장 | 원자재·정책·교차시장 설명을 국내시장 직접 주문 근거와 분리한다 | “원자재 가격 변화가 국내 주식에 항상 같은 영향을 주지 않는 이유는?” |

권장 학습 순서는 `경제 기초 → 계량·시장 → 금융공학·퀀트 → 통합 검증`이다.

1. 경제 기초: 1~3
2. 계량·시장: 4~5, 10, 12
3. 금융공학·퀀트: 6~9, 11, 13
4. 통합 검증: 14와 전체 track adversarial question

Release gate는 다음을 요구한다.

- track별 source 8개 이상, 전체 112개 이상
- track별 `PUBLIC_TEACHING_MATERIAL`, `ORIGINAL_RESEARCH`,
  `MODERN_REVIEW_REPLICATION_CORRECTION` role coverage
- fixed HTTPS source, redirect/fallback 0
- raw/extracted/embedding redistribution 0
- external LLM 전송은 source evidence와 owner opt-in이 모두 충분할 때만 허용
