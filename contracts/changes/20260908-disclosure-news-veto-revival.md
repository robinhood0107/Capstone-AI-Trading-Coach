# 뉴스 거부권을 공시 근거로 되살린다

작성일: 2026-09-08

## 무엇이 죽어 있었나

`NEWS_VETOED` 는 계약 전체(스키마·OpenAPI·생성 클라이언트·E2E)에 있고 DB 전이 화이트리스트
(`V112` 의 `('NEWS_CHECKING','NEWS_VETOED')`)에도 있는데, 엔진이 판정을 버렸다 -
`automation.py` 가 `verdict = transport.vertex(...)` 뒤에 `del verdict` 했다.

왜 그렇게 됐나. 근거 코퍼스에 구현체가 없었다. `CorpusDocumentSource` 는 Protocol 만 있고
기본값이 `EmptyCorpusDocumentSource` 여서 `build_public_evidence` 가 항상 0개를 냈고, 모든
세션이 `VERTEX_NO_REGISTERED_EVIDENCE` 로 ABSTAIN 했고, 당시 계약이 ABSTAIN 을 차단으로
봤으므로 매수가 영구히 막혔다. 그래서 2026-09-04 에 판정을 자문으로 격하했다
(`20260904-p1-news-advisory-and-intraday-buy-window.md`).

즉 죽은 것은 상태가 아니라 근거였다.

## 계획이 과소평가한 것

계획은 "빠진 건 클래스 하나다"라고 적었다. 실제로는 그 뒤의 공시 레인 전체가 잠들어
있었다. 실측:

| 층 | 조사 전 상태 |
|---|---|
| 표·투영·역할·SELECT 권한 | 있다 |
| 위험 매핑 16개 활성 코드 + 한국어 라벨 | 있다 |
| 구조화 이벤트 정규화기 | 있으나 호출부 0건 |
| quota 예약·collector lock·degradation·페이지 원자 게시 | 있다 |
| endpoint 우선순위 허용목록 | 있다 |
| 실제 데이터 | `calendar_events` 0행, 투영 0행, 법인 registry 0행, quota 0행 |
| 리더 DSN 배포 | compose·entrypoint 어디에도 없다 |
| `CorpusDocumentSource` 구현체 | 없다 |

## 실측 조사

OpenDART 를 실제로 호출해 재고 근거를 잡았다(총 30회).

* 31종목 중 **29종목**이 DART `corp_code` 에 매핑된다(나머지는 DART 법인이 없는 종목).
* 그중 **23종목**이 최근 7일 창에 어떤 공시든 갖는다.
* `rcept_dt`(host 가 아는 발행일)가 58건 전부 온다.
* **제목 58개 중 45개가 20자 미만**이라 인용 하한(`_MIN_QUOTE_CHARACTERS = 20`)에 걸린다.

## 설계 결정: 공시목록 제목이 아니라 구조화 이벤트

계획은 공시목록의 제목을 인용으로 쓰자고 적었다. 실측과 계약을 보고 바꿨다.

1. **런 안에서 provider 를 부를 여유가 없다.** `V100` 이 `p_provider_call_count` 를 0~16 으로
   못박는다. 근거를 세션 중에 가져오면 후보 몇 개로 상한을 넘는다. `vertex_corpus_evidence`
   모듈 주석도 "이미 수집·색인한 코퍼스"를 전제한다 - 수집은 런 밖의 일이다.
2. **제목 문자열은 이 레포가 이미 거절했다.** `disclosure_risk_mapping.yaml` 이 "공시검색
   제목 문자열 매칭 없이는 공식 구조화 코드가 부족함"이라며 그 부류를 `blocked` 로 두고,
   `client.disclosure_list` 의 docstring 도 제목을 점수 근거로 쓰지 않는다고 적었다.
3. **제목이 인용 하한에 걸린다.** 위 실측 그대로다.

그래서 인용을 자유 텍스트가 아니라 공식 구조화 값으로 만든다 - 커밋된 매핑의 활성 코드
라벨, 14자리 접수번호, 접수일. 실제 산출 예:

    OPENDART 공식 공시 유상증자 결정. 접수번호 20260828000001, 접수일 2026-08-28.

URI 는 접수번호로 결정적으로 만든다(`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...`).
투영의 `source_ref` 는 설계된 불투명 해시라 URI 로 쓸 수 없고, `dart.fss.or.kr` 은 이미
등록된 `OFFICIAL_PRIMARY` 다.

## 무엇을 만들었나

* `app/data/opendart/disclosure_event_collector.py` - 순수 task/commit 조립
* `app/data/opendart/disclosure_event_collector_cli.py` - 수집 실행, 표식, 법인 registry 기록
* `app/p1_owner/disclosure_corpus.py` - 투영을 근거 문서로 (network 호출 0)
* compose 서비스 `disclosure-collector` + secret `disclosure-collector.env` + entrypoint 항목
* `full-appctl` 의 `up` 단계와 `CAPSTONE_DISCLOSURE_CORPUS` 표식
* 자동운용 런타임의 `DECISION_DISCLOSURE_READER_DATABASE_DSN` 배포와 배선

새 표, 새 role, 새 정규화기, 새 quota 회계, 새 스케줄러, 새 계약은 만들지 않았다.

## 무엇을 되살렸나

`del verdict` 를 없애고 `VETO_BUY` 에서 `NEWS_VETOED` 로 닫는다. **되살린 것은 `VETO_BUY`
하나다.** `ABSTAIN` 은 여전히 통과다 - 그것이 9/4 실패를 만든 조건이고, 근거를 못 읽는
것으로 매수를 막으면 조회 실패가 곧 매매 중단이 된다. 결정적 위험 규칙이 이미 주문 권한을
갖는다. 이 성질을 `test_only_an_explicit_veto_stops_the_buy` 가 이유와 함께 고정한다.

v3 정책도 이 상태를 지나게 했다. v3 가 건너뛰던 이유는 앞의 `NEWS_SCREENING` 이 후보 집합에
이미 거부권을 행사하기 때문인데, 그 판정은 Spring bridge 가 자체 grounding 으로 내리고
요청 payload 에 근거를 넣을 자리가 없다(`_evidence_candidates_payload`). `NEWS_CHECKING` 은
등록 도메인 공시와 host 가 아는 접수일을 쓰므로 다른 층이고 서로를 대체하지 않는다. 둘 다
매수를 막을 수만 있으므로 층을 더하는 방향은 보수적이다.

다만 v3 에서는 **거부권 provider 가 결속돼 있을 때만** 지난다. 결속되지 않으면 transport 가
fail-closed 라 판정이 ABSTAIN 으로 고정된 호출 하나를 버리고, 무엇보다 소유자가 AI 를 끈
세션에서도 provider 경로가 열린다. "AI 를 끄면 provider 호출이 0"은 실제 불변식이다
(`test_ai_off_v3_uses_no_screen_or_judge_and_unlimited_fill_snapshots_peak`).

## 코퍼스를 켜는 순간 발화한 잠든 버그 둘

근거가 항상 0개였던 동안 실행되지 않던 줄들이다. 계획이 320/240 인용 길이 불일치를 같은
부류로 미리 찾아 뒀고, 실제로 둘이 더 있었다.

1. **`calendar_source_health.status_code`** - OpenDART 응답 코드 `'000'` 을 그대로 넣어
   `^[A-Z][A-Z0-9_]{0,63}$` 에 거부됐다. source health 는 provider 코드가 아니라 우리
   판정이므로 `HEALTHY` 로 고쳤다. `confidence_bps` 상한 9900 도 같은 실행에서 걸렸다.
   두 부류를 실 Postgres 테스트로 닫았다 - 순수 함수 테스트는 표의 CHECK 를 볼 수 없다.
2. **등록 출처 카탈로그 경로** - `repository_root(__file__, 5)` 가 컨테이너에서
   `/app/app/p1_owner` 를 가리켜 카탈로그를 찾지 못하고 `VertexSourceRegistryError` 를
   던졌다. 근거가 있으면 매 판정마다 터진다. 깊이 상수 대신 위로 걸어 올라가는 공용
   해석기(`repository_artifact`)를 만들어 세 곳을 함께 닫았다.

## 실측 검증

수집기:

    P1_DISCLOSURE_EVENTS=ADOPTED symbols=29 registryRows=29 completeSymbols=29
      pages=464 events=1 window=2026-08-18~2026-09-08 operations=16 providerCalls=465

자동운용이 쓰는 경로 그대로 컨테이너에서 읽은 결과:

    SYMBOL=207940 SESSION=2026-09-01 documents=1 evidence=1
       src_official_dart OFFICIAL_PRIMARY 2026-08-28
       OPENDART 공식 공시 유상증자 결정. 접수번호 20260828000001, 접수일 2026-08-28.
    SYMBOL=207940 SESSION=2026-09-08 documents=0 evidence=0
    SYMBOL=005930 SESSION=2026-09-08 documents=0 evidence=0

## 정직하게 남기는 것

* **이것은 "뉴스"가 아니라 "공시"다.** 화면과 문서에 그렇게 적는다.
* **평시의 정상 경로는 ABSTAIN 이다.** 21일 창에서 29종목 x 16 endpoint 를 훑어 구조화
  이벤트가 1건이었고, 그마저 7일 신선도 창 밖이다. 주요사항보고서는 드물고 신호가 강한
  사건이므로 이것이 정상이다 - 거부권은 평시에 통과하고 사건이 있을 때만 막는다.
* **완결성은 거짓으로 남는다.** `accnutAdtorNmNdAdtOpinion`(감사의견)은 정기보고서의
  구조화 필드이고 이벤트 스트림이 아니라서 온라인 허용목록에 없다. 거부권에는 안전한
  방향이다 - 놓친 공시는 거짓 음성이고, 없는 공시로 매수를 막는 일은 생기지 않는다.
  완결성이 필요한 결정적 위험 점수 경로는 이 수집기의 권한이 아니다.
* **아직 남은 것: 거부권 transport 를 Spring bridge 로 보낸다.** 지금
  `newsVetoProviderBound` 는 `VERTEX_*` env 로 만든 서비스 계정 transport 가 있을 때만
  참이다. 설정 화면이 고른 provider 를 쓰게 하려면 형제 경로(`_news_screen`)처럼 bridge
  operation 을 지나야 하고, 그것은 Spring 쪽 변경이다. 그때까지 v3 에서는 이 층이 켜지지
  않는다. 이 파일이 그 사실의 근거다.

## 호출 예산

종목당 16 endpoint, 29종목 = 464 호출이 한 바퀴다. compose 가 하루 예산 2000, 실행당 600 으로
선언한다(비밀이 아니므로 리뷰되는 자리에 둔다). 예약은 lower-only(`LEAST`)이므로 계정 상한을
실제로 확인해 더 낮추면 그 값이 이긴다. 원 응답은 저장하지 않는다.
