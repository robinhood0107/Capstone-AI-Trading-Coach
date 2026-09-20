/**
 * 금융 Agent 답변 캐시.
 *
 * 이 파일의 답변은 지어낸 것이 아니라 이 시스템이 실제로 낸 응답을 그대로 담은 것이다.
 * 2026-09-20 에 돌아가는 스택에서 받아 저장했고, 인용과 연결률도 그때 받은 값이다.
 *
 * 왜 두는가. 설명 생성은 외부 provider 를 거치므로 네트워크나 일일 상한 같은 이유로
 * 그 자리에서 닫힐 수 있다. 시연 중에 그러면 화면이 비어 버린다. 실시간 호출이 실패하면
 * 마지막으로 성공했던 같은 질문의 답을 내보내 흐름이 끊기지 않게 한다.
 *
 * 질문 문자열은 공백을 지우고 비교해 오타 없는 같은 질문만 맞춘다. 모르는 질문에는
 * 아무것도 돌려주지 않는다 - 없는 답을 만들어 내지 않는다는 원칙은 그대로다.
 */

export interface CachedRagAnswer {
  readonly question: string;
  readonly answer: string;
  readonly citationCoverage: number;
  readonly citations: readonly unknown[];
  readonly guardrailFlags: readonly string[];
}

const CACHE: readonly CachedRagAnswer[] = [
  {
    "question": "금 ETF의 롤오버 위험은 무엇인가요?",
    "answer": "선물 가격을 추종하는 ETF는 만기가 다가오는 선물 계약(근월물)을 매도하고, 만기가 더 많이 남은 다음 선물 계약(원월물)을 매수하는 '롤오버' 과정을 주기적으로 거쳐야 합니다.\n이때 차기 월물의 가격이 현재 월물보다 높은 콘탱고(Contango) 상태일 경우, 더 비싼 가격에 계약을 갈아타야 하므로 롤오버 비용(손실)이 발생합니다.\n이러한 롤오버 비용과 환헤지 비용 등이 누적되면, 금 선물 ETF의 수익률은 실제 현물 금 가격의 상승률을 온전히 따라가지 못하고 성과가 달라질 수 있습니다.\n이 때문에 장기 투자 시 현물 금 투자에 비해 성과가 저조해지는 원인이 됩니다.\n따라서 금 선물 ETF는 롤오버와 환헤지 효과로 인해 현물 금과 성과가 달라질 수 있으므로 투자 시 유의해야 합니다.",
    "citationCoverage": 0.8,
    "citations": [
      {
        "title": "newstomato.com",
        "locator": {
          "section": "newstomato.com"
        },
        "sourceId": "src_web_8866b687b84f027eb5415e42",
        "citationId": "cit_1",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQHkO4ULzTWPlRvD0iZqiHzSn7EXBOxRhZ1MFysj9MhsViwKUOeMnbaN1TjEgaL_H5qicl9qoNs-7qDqKs0IJeR1HunSHmcVF3Cv1KnlcqotBacwORn-zp5IJVzL7v8-AhMc2IFLj-7QpfXWQvh0Qw==",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "youtube.com",
        "locator": {
          "section": "youtube.com"
        },
        "sourceId": "src_web_8983da26c875f0bdafb61aef",
        "citationId": "cit_2",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFiEtdMGkK8nzzNQaBVJme6Qe0_aFDbmuk-8QC173xW9MOUp0vQRDm8XyihU3cIxcXlgv6ZLxgVhCbcCslqzXs8gsiM2AJUAD3W8umWDkGiW-rznMn0bQVMtaYLB8p6zyyxGKyVDDU=",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "frism.io",
        "locator": {
          "section": "frism.io"
        },
        "sourceId": "src_web_017c21f7e988db79505142b3",
        "citationId": "cit_3",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQETkSK5bSFX15nk3r7CJiPBYqkzMXr5Ik-Yg_TFnkA1BRt875_C3SolDRtVekxdAX3LNXA2X7ghCrE7BpqVh8IOpZCEDOQUvgpJkS7Y0cdm9Y0K2BkZ",
        "citationKind": "PUBLIC_WEB"
      }
    ],
    "guardrailFlags": [
      "REASONING_SENTENCES_PRESENT",
      "GOOGLE_GROUNDING_ONLY"
    ]
  },
  {
    "question": "MDD와 Sharpe는 각각 무엇을 말해주나요?",
    "answer": "MDD(Maximum Drawdown, 최대 낙폭)는 특정 투자 기간 동안 포트폴리오의 고점 대비 저점까지의 최대 손실 비율을 나타내며, 투자자가 겪을 수 있는 최악의 손실 위험(리스크)을 측정하는 지표입니다.\nSharpe(샤프 지수)는 위험 자산에 투자하여 얻은 초과 수익률을 포트폴리오의 변동성(표준편차)으로 나눈 값으로, 감수한 리스크 단위당 얼마나 효율적으로 수익을 냈는지를 평가하는 투자 효율성 지표입니다.",
    "citationCoverage": 0,
    "citations": [],
    "guardrailFlags": [
      "MODEL_KNOWLEDGE_ONLY"
    ]
  },
  {
    "question": "삼성전자 지금 사도 되나요?",
    "answer": "삼성전자 주가는 최근 미국 반도체 업종의 반등과 금리 불확실성 완화 등의 영향으로 상승세를 보이며 26만 원대를 기록했습니다.\n그러나 외국인 투자자들의 순매도세가 지속되면서 주가 상승을 제약하고 있으며, 자사주 매입 등 기타법인 수급이 하방을 지지하고 있는 상황입니다.\n일부 글로벌 투자은행(IB)과 자산운용사들은 현재 삼성전자의 주가가 고점 대비 크게 조정받은 상태로, 메모리 수요 대비 공급 부족 상황을 고려할 때 심각한 저평가 국면에 있다고 분석합니다.\n장기적인 실적 상승 전망을 바탕으로 투자의견을 '매수'로 유지하는 시각도 존재합니다.\n인공지능(AI) 모멘텀의 선반영 우려와 외국인 수급 개선 지연이 변수로 작용하고 있습니다.\n또한, 최근 노조의 독자 노선 구축 및 집회 등 내부적인 노사 갈등 이슈도 지속되고 있습니다.",
    "citationCoverage": 1,
    "citations": [
      {
        "title": "radiokorea.com",
        "locator": {
          "section": "radiokorea.com"
        },
        "sourceId": "src_web_cf734d293f2051699b8136ed",
        "citationId": "cit_1",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH5kFYAfXM4W38aLwXGLVRDmnpyscDfCKHXMds8re50-fmAWPfFARnqmp80rUMIS0ro0opIHB4xwUdTzQZpY4dXYTd0cR3l1GHZotaWp0Qxhy3DYyBACzRI-Me5Jqtg7YeL-J1cwGwdH8ImxZHJ",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "daum.net",
        "locator": {
          "section": "daum.net"
        },
        "sourceId": "src_web_7c2eb9a4630aa21d78266e3a",
        "citationId": "cit_2",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEIdvyImzyW9v_laMl5sXDVcLW4q0mDqDp0r1FksuZRtp7WMVgQs4lWQFGlyCnfdW1OJHRVK8rE_nxZ6wW-I05_7qF2S1mnjUMrAdy1BVA9aMVePi87vs4ryGM=",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "investing.com",
        "locator": {
          "section": "investing.com"
        },
        "sourceId": "src_web_cd8fbaa32ada9e802ab8b6ab",
        "citationId": "cit_3",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE5Qgajlz1qFyo3-L7WZU7HberJqeG5jPUlUosZeGYrQKFpwtoOrPWyuutc2EkhzjGe2AZsh2qLRElGuprmii03vDf2QtsylEtaCRnqJCX9ZIFrwDebdp1EUagDm_bODG9Jiy5NbWEwIL7fVMCbIamjB5g1YsAQAwNjqg==",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "newsworks.co.kr",
        "locator": {
          "section": "newsworks.co.kr"
        },
        "sourceId": "src_web_61a091c5618e530a803d16f2",
        "citationId": "cit_4",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH1lKdue1JW7bMTiLH_qi2SwUnvSrGYKo0hmEXHo7hzdkPnGmbq2NGc9fZ0J9YoBNagNekrcekRl-HJcYq0T5uUe6I-GmO-S5DxZrmbTHitP2Gtt7sO-tCcAG3S71F8RFCQaxcgCiJbNM0Yz8XEJ0cvjEqOdRLan4qU",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "alphasquare.co.kr",
        "locator": {
          "section": "alphasquare.co.kr"
        },
        "sourceId": "src_web_ca4de991ee52161eb63b50cd",
        "citationId": "cit_5",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFrIZvREwxi12LYi2sjUuG76OU2jSUi3gGVFA9vvHT63fagwNpd4X1UCcsRMTyx_l1OvxNfwqRG7xmrvifGP5ANIADANULcAp8qQghrzn0O2hDHiTFWxLycLSxQZB0Pebmyr7Q1pz12P2Eultq_Ucp06RC-",
        "citationKind": "PUBLIC_WEB"
      }
    ],
    "guardrailFlags": [
      "GOOGLE_GROUNDING_ONLY"
    ]
  },
  {
    "question": "변동성 돌파 전략이 뭔가요?",
    "answer": "변동성 돌파 전략(Volatility Breakout Strategy)은 전설적인 트레이더 래리 윌리엄스(Larry Williams)가 고안한 단기 매매 기법입니다.\n가격이 일정 범위를 돌파할 때 발생하는 강한 상승 모멘텀(추세)을 활용하여 단기 수익을 추구하는 것이 핵심 원리입니다.\n**레인지(Range) 계산**: 전일 고가에서 전일 저가를 뺀 값으로, 하루 동안의 가격 변동 폭을 의미합니다.\n* $Range = 전일 고가 - 전일 저가$\n**매수 목표가 설정**: 당일 시가에 '전일 레인지'와 특정 상수 $k$(보통 0.4~0.6 사이)를 곱한 값을 더해 매수 기준 가격을 정합니다.\n* $매수 목표가 = 당일 시가 + (전일 레인지 \\times k)$\n**진입(매수)**: 장중에 주가가 이 매수 목표가를 돌파하는 순간 즉시 매수합니다.\n**청산(매도)**: 매수한 포지션은 다음 날 아침 장이 시작할 때(시가) 전량 매도하여 하루 단위로 빠르게 수익을 실현합니다.\n이 전략은 규칙이 매우 단순하여 자동매매(알고리즘 트레이딩)로 구현하기 쉽고, 당일 매수 후 다음 날 바로 매도하므로 밤사이 발생하는 시장의 변동성 리스크를 최소화할 수 있다는 장점이 있습니다.",
    "citationCoverage": 0.8888888888888888,
    "citations": [
      {
        "title": "tistory.com",
        "locator": {
          "section": "tistory.com"
        },
        "sourceId": "src_web_1b351d0de71bfc2999a7677d",
        "citationId": "cit_1",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFXPrskyyuMrfRV2zr2Wij63nlkx5Klj7t5DnqBJFUO070dny-lbZEzQ-jZMziDcZF4YEVMy2rC-YbR6y2ukhGHu27m5EF3nxGvH-16rjJ1YvJik11xIEfb3ZR4",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "github.io",
        "locator": {
          "section": "github.io"
        },
        "sourceId": "src_web_619e3c0659d81edc6457962e",
        "citationId": "cit_2",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH83b9mTQnFr3DqZsUTJjHNNMkhcIXLoDn010Ap9ghnvGWKMLG_dUJ5krjeFDuzCuYCfq1ktUIk9sJbaUkmErjdedk8W6xOc6BKsCVFAcsk22EBvAm5l4NlGsdDVC57YftEA2P5f-H00cTx06OO76igMZtaAA==",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "tistory.com",
        "locator": {
          "section": "tistory.com"
        },
        "sourceId": "src_web_d6b9ce437dd8b45d2a0e3688",
        "citationId": "cit_3",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE-NpVns5L6x5e6hJh0ZEVXJp_Jl0fn3sSBiVHMf8rXiYP4zgY7DLxqWBfCGhSnu8Ofcw_mNhlbqsmVPbF0eDoxJHFpnJUoUCxX8RtLyD0ARV1SqD8NBwxk",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "tistory.com",
        "locator": {
          "section": "tistory.com"
        },
        "sourceId": "src_web_b6616ab57be470383e397c4f",
        "citationId": "cit_4",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQFMkTz_E-GnBJkjoBVVy_b1lMAm0ey2nKprfAyPbkbOCluUJvpZk_-FP5xXLviugiaGUzwbqAoj7__TrP4DzxyRvR9nh5XWW-3SE3dneRSP7ilqz3NxlhaCs4FUd-U=",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "tistory.com",
        "locator": {
          "section": "tistory.com"
        },
        "sourceId": "src_web_f9a6971fac58860ac5cf8c29",
        "citationId": "cit_5",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQH2WCkOB69V19zHUiUQI4BMWIxUAyL6zJ3Sc92yvohat0Y-3-DyxwA_5ximnN7yHM1PkVteetn3gPcV_DuwX1ECQEFfnwxgD5varTb69h-nI0grtdK7JjBYB_LpwoA=",
        "citationKind": "PUBLIC_WEB"
      }
    ],
    "guardrailFlags": [
      "REASONING_SENTENCES_PRESENT",
      "GOOGLE_GROUNDING_ONLY"
    ]
  },
  {
    "question": "손절선을 5%로 두면 어떤 점이 좋고 나쁜가요?",
    "answer": "손절선을 5%로 설정하는 것은 장단점이 뚜렷한 리스크 관리 방법입니다.\n장점으로는 예상치 못한 급격한 시장 변동으로부터 투자 원금을 효과적으로 보호할 수 있으며, 감정에 치우치지 않고 기계적으로 손실을 제한하여 투자 원칙을 고수하는 데 도움을 줍니다.\n단점으로는 주가의 일시적인 미세한 흔들림(노이즈)에도 쉽게 손절매가 실행되어 시장에서 조기 퇴출당하는 '휩소(Whipsaw)' 현상을 겪을 수 있으며, 장기적인 상승 추세의 자산인 경우 반등의 기회를 놓치고 손실만 확정 지을 위험이 있습니다.",
    "citationCoverage": 0,
    "citations": [],
    "guardrailFlags": [
      "MODEL_KNOWLEDGE_ONLY"
    ]
  },
  {
    "question": "ETF와 ETN은 무엇이 다른가요?",
    "answer": "ETF(상장지수펀드)와 ETN(상장지수증권)은 거래소에 상장되어 실시간으로 거래되는 인덱스 상품이라는 공통점이 있으나, 법적 성격과 신용위험 구조 등에서 명확한 차이가 있습니다.\n* **법적 성격 및 발행 주체**: ETF는 자산운용사가 발행하는 집합투자증권(펀드)인 반면, ETN은 증권회사가 발행하는 파생결합증권(채권 형태의 증권)입니다.\n* **신용위험 (Credit Risk)**: ETF는 펀드가 보유한 자산을 별도의 신탁기관에 보관하므로 발행회사의 신용위험에 노출되지 않습니다.\n반면, ETN은 발행 증권사의 신용으로 발행하는 무보증·무담보 사채와 동일하여 발행자의 신용위험(신용 사건 발생 시 원금 손실 가능성)이 존재합니다.\n* **추적오차**: ETF는 실제 자산을 운용하여 지수를 추종하므로 추적오차가 발생할 수 있습니다.\n반면, ETN은 발행 증권사가 기초지수 수익률 지급을 약속하므로 추적오차가 발생하지 않습니다.\n* **만기 여부**: ETF는 만기가 없으나, ETN은 일정 기간(예: 1년 이상 20년 이내)의 만기가 존재합니다.",
    "citationCoverage": 1,
    "citations": [
      {
        "title": "trueetn.com",
        "locator": {
          "section": "trueetn.com"
        },
        "sourceId": "src_web_aa7f410b105eee480d7fab5b",
        "citationId": "cit_1",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQGSRzFbHl1r2aINAF51Nlz6ahNVd7ALE5lDB7n7WnCzxW6waBCZ4tFM84DHfYUQDXyT-ZEVQ5l0OVeejS4a36K0OyjHakAwyfYaUmzbitH0jyl4C7JtSt8ZwrCj4CLZ5lv1IDJEGVLsQvQl031m0nkVn_bdIjECNqMq-g==",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "kcie.or.kr",
        "locator": {
          "section": "kcie.or.kr"
        },
        "sourceId": "src_web_2f152e335c4295104af9375e",
        "citationId": "cit_2",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQEFo66qYOhUB-pLhX3dUBL394lD581H5kNEZV4SlciIyZo7mA1tcPLxchETDowDvhvsmdIxfxxy7dnbGttuQrVrvTAouvpHy5kard-ZZaWm8Hh2fOeKpSTkJCU_CvsP7EskUzyGEKjlnyNcj6c3VNRZBj1dfmf9WnsftC-AO4eY7EddB-LTBxo0pA==",
        "citationKind": "PUBLIC_WEB"
      },
      {
        "title": "miraeasset.com",
        "locator": {
          "section": "miraeasset.com"
        },
        "sourceId": "src_web_66b1eb8013cf1509a792db1b",
        "citationId": "cit_3",
        "canonicalUrl": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQG7aTi7eGcytTLM8xVTGjRoyWD72ragFl_I43g3wIaUamIlnmRUDAN79qH2DZDVH80nAb7UnwutFbqHRRUTH8kxIAKvG3cfV9xREPq95MgTIPYxWuklHMUZD_lySGGjHR6HKFhoIkPm5OMWXOesYpM=",
        "citationKind": "PUBLIC_WEB"
      }
    ],
    "guardrailFlags": [
      "GOOGLE_GROUNDING_ONLY"
    ]
  }
] as const;

const normalize = (value: string): string => value.replace(/\s+/g, '');

/** 같은 질문의 저장된 답을 찾는다. 없으면 undefined 다. */
export function findCachedRagAnswer(question: string): CachedRagAnswer | undefined {
  const key = normalize(question);
  return CACHE.find((entry) => normalize(entry.question) === key);
}

export const CACHED_RAG_QUESTIONS: readonly string[] = CACHE.map((entry) => entry.question);
