# S1.2 OpenDART 공시 위험 점수 근거

이 문서는 S1.2 OpenDART client와 `disclosure_risk_score`가 무엇을 신뢰하고, 무엇을 아직 신뢰하지 않는지 정리한다. 결론은 간단하다. S1.2 점수는 투자수익 예측 모델이 아니라, RiskEngine이 위험 검토를 강제하기 위한 보수적 정책 점수다.

## 결론

S1.2는 OpenDART 공식 API에서 읽은 구조화 이벤트만 점수화한다. 공시 제목인 `report_nm` 문자열은 점수 근거로 쓰지 않는다. 같은 입력이면 같은 점수가 나와야 하므로 agent나 LLM이 production 점수를 직접 매기지 않는다.

현재 점수표는 “정답 확률”이 아니다. 감사의견, 자본조달, 소송처럼 위험 검토가 필요한 공시를 거칠게 분류하는 guardrail이다. 따라서 주문 판단에서는 이 점수를 단독으로 쓰지 않고, RiskEngine 원칙·가격 데이터·포트폴리오 상태·뉴스/거시 feature와 함께 본다.

엄격한 표현은 다음과 같다.

| 주장 | 현재 말할 수 있는가 | 이유 |
|---|---|---|
| OpenDART 공식 endpoint를 사용한다 | 예 | 공시검색, 주요사항보고서, 감사의견 endpoint가 공식 guide에 존재한다 |
| `report_nm` 문자열을 점수 근거로 쓰지 않는다 | 예 | endpoint identity와 `adt_opinion` 구조화 필드만 risk event로 쓴다 |
| 위험 이벤트 선정에는 문헌 근거가 있다 | 예 | 증자, 비적정 감사의견, disclosure risk, litigation 관련 event study와 규제 근거가 있다 |
| `1.0`, `0.8`, `0.6`, `0.4`가 한국시장 통계로 보정된 수치다 | 아니오 | 아직 한국 universe에서 event study/backtest를 수행하지 않았다 |
| 현재 점수는 주문 차단을 위한 최종 근거다 | 아니오 | v1에서는 WARN/BLOCK 후보 입력 중 하나이며 단독 차단 근거가 아니다 |

## OpenDART 전체(DS001~DS006 85개) 대비 S1.2 지원 범위

이 문서는 “OpenDART 전체 85개 구현”을 목표로 하지 않는다. S1.2는 주문 판단에 직접 필요한 최소 read-only 공식 원천만 선별한다. “사용할 수 있는 데이터”와 “지금 써야 하는 데이터”를 구분한 결과는 다음과 같다.

| 구분 | 그룹 | 항목 | 판단 이유 |
|---|---|---|---|
| 지원 | DS001 | 공시검색, 기업개황, 고유번호 | 회사 식별과 allowlisted 공시 metadata·sanitized observation의 기반 |
| 지원 | DS002 | 회계감사인의 명칭 및 감사의견(`adt_opinion`) | 비적정/한정/의견거절은 강한 위험 검토 트리거 |
| 지원 | DS003 | 단일회사 주요계정(`fnlttSinglAcnt`), 단일회사 주요 재무지표(`fnlttSinglIndx`), (S1.2c) 다중회사 주요 재무지표(`fnlttCmpnyIndx`) | 재무위험 기준·RAG 카드·백테스트 feature 후보. 다중회사는 universe batch |
| 지원 | DS004 (S1.2c) | 대량보유 상황보고(`majorstock`), 임원ㆍ주요주주 소유보고(`elestock`) | 지분변동/주요주주 ownership 축. **수집·파싱만** — 즉시 주문 차단 1순위가 아니라 종목 설명·후속 feature 용도(주문 차단 점수 미연결) |
| 지원 | DS005 | 유상증자·전환사채·소송 + 부도·회생·해산·관리절차·영업정지·감자 + (S1.2b) 신주인수권부사채·교환사채·합병·분할·분할합병·영업양도 | 자본조달·법적·distress에 더해 희석·복잡상품·reorg 위험을 endpoint identity로 점수화 |
| 후속 필수 (**S1.6**) | — | 공시위험 지속 상태 추적(open/close, collector), DS004 ownership을 canonical 이벤트로 승격 | 유형별 유효기간 근사를 정확 모델로 대체 |
| 후속 검토 | DS002/DS006 | 배당·자기주식·자금사용·증권신고서 요약 등 | RAG/feature/timeline에 유용하나 즉시 RiskEngine 핵심은 아님 |
| 의도적 제외 | DS001/DS003/DS002 | 공시서류원본파일, 재무제표 원본파일(XBRL), 단일회사 전체 재무제표, 개인별 보수류 | 저장·파싱·재배포·개인정보 정책이 정해지기 전에는 위험이 더 큼. 저장/보안 정책 문서화 후 별도 세션 |

의도적 제외 근거를 다시 정리하면, 원본파일/XBRL/전체 재무제표/개인별 보수는 (1) 대용량 저장 비용과 retention 정책, (2) 원문 재배포·저작권 제한, (3) 개인정보성 노출, (4) parser timeout·fixture 정책이 함께 필요하다. 이 정책 없이 무작정 수집하면 신뢰성이 아니라 리스크가 커진다. 신뢰성은 “많이 긁기”가 아니라 “공식 원천·수집 시각·hash·재현 가능한 parser·교차검증”에서 나온다.

## 후속 세션 로드맵 (확정)

“후속 필수 축”은 아래 세션에 **확정 배정**한다. 백로그로 두지 않는다(중간보고서 M1 범위와 별개로 로드맵을 고정한다).

| 세션 | 범위 | 상태 / DoD | 근거 |
|---|---|---|---|
| **S1.2b** | DS005 확장: 신주인수권부사채(`bdwtIsDecsn` 2020034)·교환사채(`exbdIsDecsn` 2020035)·회사합병(`cmpMgDecsn` 2020050)·회사분할(`cmpDvDecsn` 2020051)·회사분할합병(`cmpDvmgDecsn` 2020052)·영업양도(`bsnTrfDecsn` 2020043) | **완료(지원으로 이동)**. BW/EB=0.6·window 30, 합병/분할/분할합병/영업양도=0.6·window 90, `policy_v1_unvalidated`. endpoint path/params + event_code + mapping validation + scorer 테스트 통과 | 기존 `MAIN_MATTER_ENDPOINTS` 패턴 재사용 |
| **S1.2c** | DS003 다중회사 주요 재무지표(`fnlttCmpnyIndx`, apiId 2022002) + DS004 대량보유 상황보고(`majorstock`, 2019021)·임원ㆍ주요주주 소유보고(`elestock`, 2019022) | **완료(지원으로 이동)**. 다중회사 corp_code comma-join 복수조회, ownership row 파싱, `OwnershipDisclosureEvent`(event_code `OPENDART:OWNERSHIP_CHANGE`) 내부 모델. client/parser 테스트 통과. 주문 차단 점수 미연결 | 재무 feature·지분변동 축 |
| **S1.6** | Market Calendar/Event Aggregator + 공시위험 지속 상태 추적 + DS004 ownership canonical 승격 | **내부 offline 구현**. v1 structured state pair는 `bnkMngtPcbg` open / `bnkMngtPcsp` close만 승인하고 DS004는 PII-free projection만 저장한다. provider online/scheduler와 REST/gRPC/Dashboard는 비활성이며 별도 승인이 필요하다 | 관리절차는 append-only transition/current active view로 구현했지만 기존 S1.2 RiskEngine 소비 전환과 나머지 유형은 후속 승인 범위다 |

- S1.2b·S1.2c endpoint 경로명은 구현 시 OpenDART 상세페이지로 확인 완료(apiId ↔ path 일치). 경로명을 미확정 상태로 지어내지 않는다.
- 실제 주문 판단 소비(riskItems 방출)는 S2.2 `DisclosureRiskPort` + S2.3 Decision API에서 이뤄진다(위 로드맵과 독립적으로 진행).

### S1.2c 누적 보안 재검증 (2026-07-10)

S0 최초 구현부터 S1.2c까지 누적 코드를 다시 검사했다. OpenDART는 응답 byte/JSON depth/list/ZIP member·ratio/XML field 상한을 두고 DTD/entity를 parser 단계에서 금지한다. 당시 ignored observation projection은 source id를 제한하고 directory fd+`O_NOFOLLOW`+exclusive create로 symlink 경계 이탈을 차단했다. S1.6은 이 historical file을 import하지 않고 provider raw persistence를 0으로 고정하며 allowlisted sanitized observation만 DB에 허용한다. mapping window와 scorer 입력/date 범위도 bounded 처리한다. 이 검사는 offline fixture와 mock transport로 수행했으며 기존 승인 smoke 이후 새로운 OpenDART 호출을 만들지 않았다.

같은 누적 검사에서 KIS credential의 private fixed-origin transport 격리, Spring 인증 전 body limit·로그인 원자 rate limit·멱등성 owner fencing, loopback gRPC, Redis/PostgreSQL 최소권한도 함께 보강했다. contracts/proto와 S1.6 quota migration/collector는 변경하지 않았다.

## RiskEngine/Decision API 계약 연결

S1.2의 두 번째 핵심은 점수를 계산만 하지 않고, 판단 결과 계약에 근거를 드러내는 것이다.

- Principle 계약(`contracts/schemas/principle.schema.json`)에는 이미 `disclosure_risk_guard`/`disclosure_risk_score`가 있다(입력 축).
- 판단 결과 계약(`contracts/schemas/risk_decision.schema.json`)에는 disclosure 근거가 없었다. 이를 범용 `riskItems[]`로 추가했다. OpenDART 전용 거대 객체 대신, 다른 원천(뉴스/거시 등)도 같은 형태로 표현할 수 있는 구조다.
- `riskItems[]` 항목: `metric`(예 `disclosure_risk_score`), `value`, `severity`, `source`(예 `OPENDART`), `eventCodes`(예 `OPENDART:dfOcr`), `mappingVersion`, `sourceRefs`(sanitized observation/citation의 opaque 참조).
- 서비스 경계는 `docs/API_명세서.md` 13.5 `MarketDataService.GetDisclosureEvents`의 request/response 계약으로 문서화했다. 실제 gRPC proto 파일은 아직 없고, 구현은 컨트롤러 도입 세션에서 계약을 따라 붙인다.

이 연결로 “점수는 계산되지만 어디에도 안 쓰인다”는 감사 지적(P1)을 계약 수준에서 먼저 해소한다. 실제 Spring RiskEngine 소비 구현은 Decision/Risk 컨트롤러 세션의 과제로 남는다.

## 프로젝트 문서와의 정합성

| 근거 문서 | 확인한 내용 | S1.2 판단 |
|---|---|---|
| `docs/최종_프로젝트_명세서.md` 11.1, 11.5 | OpenDART는 재무제표, 공시, 기업 기본정보의 필수 원천 | OpenDART read-only client를 둔다 |
| `docs/최종_프로젝트_명세서.md` 11.1.2 | sanitized observation과 canonical event를 분리하고 provider raw persistence를 금지 | S1.2 historical ignored projection은 S1.6 input으로 승격하지 않는다. S1.6은 allowlisted projection, 정규화·집계만 저장한다 |
| `docs/API_명세서.md` 4장 | `disclosure_risk_guard`는 `disclosure_risk_score`를 소비 | Python 서비스가 점수를 계산하고 Spring RiskEngine은 snapshot을 소비 |
| `docs/API_명세서.md` 5장 Decision API | 판단 결과는 `violations`/`riskItems`로 위험 근거를 표현 | `disclosure_risk_score`를 `risk_decision.riskItems[]`로 결과 계약에 노출 |
| `contracts/schemas/risk_decision.schema.json` | disclosure 근거를 담을 범용 `riskItems` 구조 | 유형별 유효기간 내 복수 이벤트 max score·mapping 버전을 결과 계약에 재현 가능하게 남긴다 |

## 데이터 수집 범위

S1.2는 “재무제표와 모든 공시를 전부 긁는 단계”가 아니다. 대상 universe와 날짜 window에 대해 필요한 공식 endpoint를 호출하는 단계다.

| 항목 | S1.2에서 하는 일 | S1.2에서 하지 않는 일 |
|---|---|---|
| 고유번호 | `corpCode.xml` ZIP/XML을 파싱해 `stock_code -> corp_code` 매핑 확보 | 전체 종목 universe 운영 정책 확정 |
| 기업개황 | 기업 기본정보 조회와 parser 제공 | 기업 설명 RAG 카드 생성 |
| 재무 주요계정 | 단일회사 주요계정 endpoint parser 제공 | XBRL 원문, 주석, 전체 재무제표 대량 다운로드 |
| 재무지표 | 단일회사(`fnlttSinglIndx`)와 (S1.2c) 다중회사 batch(`fnlttCmpnyIndx`) parser 제공 | 재무비율 임계값 원칙 연결(후속), 재무제표 원문 |
| 지분공시 | (S1.2c) 대량보유(`majorstock`)·임원ㆍ주요주주 소유(`elestock`) 수집·파싱, `OwnershipDisclosureEvent` 내부 모델 | 주문 차단 점수 연결(후속), ownership canonical 승격(S1.6) |
| 공시목록 | 대상 기간(호출부 지정) 공시목록 조회와 allowlisted sanitized observation projection | provider raw 저장, 공시 제목 문자열 기반 이벤트 확정 |
| 주요사항 | 자본조달·법적 위험(유상증자·전환사채·소송)과 going-concern distress(부도·회생·해산·관리절차·영업정지·감자) 전용 endpoint identity를 risk event로 사용 | 주요사항보고서 36종 전체 점수화, 자기주식/양수도/해외상장 등 저위험·중립 이벤트 |
| 감사의견 | `adt_opinion` 구조화 필드로 비적정/한정/의견거절만 점수화 | 감사보고서 본문 NLP 판단 |

전체 공시·전체 재무제표를 무조건 긁는 방식은 신뢰성을 높이지 않는다. 호출 한도, 저장 비용, 중복 정정공시, 원문 저작권/재배포 이슈, look-ahead bias가 함께 커진다. 신뢰성을 높이려면 “많이 긁기”보다 “공식 원천, 수집 시각, hash, 재현 가능한 parser, 교차검증”을 갖춰야 한다.

## API key와 quota 운영 경계

- OpenDART API key는 Decision Platform 서버 운영자가 배포 환경에 주입하는 secret이다. 앱 사용자는 회원가입·Dashboard·설정·REST/gRPC 요청에서 key를 입력하거나 사용자별 quota를 관리하지 않는다.
- key는 환경변수/배포 secret store에서만 읽고 DB·Redis·YAML·Git에 저장하지 않으며, 로그·예외·metric label·응답·sanitized observation에 노출하지 않는다. key 누락·오류 시 collector는 fail-closed로 중단한다. 공시 rule이 해당 주문의 활성 principle에서 optional이면 Decision 경로는 snapshot이 없거나 stale이어도 그 값을 최신으로 사용하지 않고 마지막 `asOf`와 누락 사유를 표시한 뒤 skip+WARN할 수 있다. rule이 required이면 missing/stale에서 `HOLD`한다.
- S1.2에서 공개 settings와 `OpenDARTClient`/`OpenDARTHttpClient`가 인증 필드나 값을 보관하지 않도록 분리했다. private transport만 TLS 검증을 강제한 고정 OpenDART HTTPS origin의 실제 send 구간에서 루트 `.env`/process env 값을 일시 로드·첨부하고, inner transport 반환·예외 직후 request URL을 원복한다. redirect, ambient proxy/`.netrc`(`trust_env`), caller proxy/CA override와 상위 caller의 인증성 파라미터·절대 URL은 outbound 전에 거부한다.
- response body/header/extensions echo, transport exception chain, log, raw payload/fingerprint에는 인증정보 값과 민감 필드명을 남기지 않는다. 실제 API 사용에는 OpenDART 서버로의 TLS 전송이 필수지만 앱 계층의 객체·저장소·관측 채널에는 전파하지 않는다.
- DS004 ownership canonical을 만드는 S1.6에서는 자연인 성명·주소·등록 식별자 등 개인 식별 필드를 저장하지 않는다. corpCode, role/category, 공시·변동일, 주식 수·비율과 sanitized source reference처럼 위험 feature에 필요한 최소 필드만 observation 생성 전에 projection하고, 개인 식별 필드는 observation/canonical/log/metric/artifact/Kafka에서 제거한다. provider raw object를 먼저 저장한 뒤 삭제하는 방식도 금지한다.
- OpenDART FAQ(2020-01-18)의 개인 계정 일일한도 `20,000건`은 현재 배포 ceiling의 검증 기준값이며 모든 계정의 불변 hard cap으로 단정하지 않는다. S1.6은 실제 계정 한도를 `effective limit`으로 다시 확인하고 `daily limit<=effective limit`, 12.5% reserve의 `daily budget<=min(17,500, floor(effective limit*0.875))`, `per-run charged reservation/physical_attempts cap<=min(8,000, daily budget)`으로 세 값을 함께 낮추며 코드 기본값으로 고정하지 않는다. actual HTTP sends는 charged reservations와 별도 보고하고 항상 그 이하여야 한다. 계정 화면에서도 20,000건을 확인한 경우에만 17,500/8,000 예시를 그대로 사용한다. 개발가이드는 `020`이 일반적으로 20,000건 이상에서 발생하되 계정 설정에 따라 달라질 수 있다고 명시하므로 `status=020`과 실제 계정 상태가 최종 권위다. [OpenDART FAQ](https://opendart.fss.or.kr/cop/bbs/selectArticleList.do?bbsId=B0000000000000000002), [OpenDART 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002)
- S1.2는 HTTP 200 body의 `status=020`을 전용 비재시도 quota 신호로 구분하는 데까지만 구현한다. 일일 limit/budget과 실행별 charged reservation/`physical_attempts` cap의 durable enforcement는 운영 설정을 필수로 받는 S1.6 collector에서 구현하고 actual HTTP sends는 별도 집계한다.
- PostgreSQL atomic quota ledger와 단일 collector의 durable enforcement는 S1.6에서 구현한다. Redis와 Kafka는 OpenDART quota accounting에 사용하지 않는다.

### S1.6 OpenDART collector offline 구현과 online gate

S1.6 후속 변경은 아래 quota/priority/retry/state/privacy 경계를 offline fixture와 PostgreSQL 16
통합 테스트로 구현한다. 실제 provider 호출, 주기 scheduler와 운영 활성화는 별도 승인 전까지 0이다.

- online에는 `OPENDART_DAILY_CALL_LIMIT`, `OPENDART_DAILY_CALL_BUDGET`,
  `OPENDART_MAX_CALLS_PER_RUN`, `OPENDART_MAX_SYMBOLS_PER_RUN` 네 positive operator 설정이
  모두 필요하다. budget은 `min(17,500, floor(limit*0.875))`, run cap은
  `min(8,000, budget)` 이하여야 하며 코드 기본값은 없다.
- 모든 safe GET attempt는 TokenBucket 뒤 send 직전 PostgreSQL conditional UPSERT
  reservation을 새로 얻는다. DB deny/error/ambiguous commit은 HTTP 0이고 refund는 없다.
  호환 column `physical_attempts`는 charged reservation이므로 actual HTTP sends와 별도
  집계한다.
- retry 0은 HTTP 429, body `status=020`, auth/permission, invalid argument, schema drift,
  PII projection failure, pagination/continuation mismatch다. timeout/transport와
  500/502/503/504만 safe GET에서 총 3 attempts까지 허용한다. 첫 020은 durable exhausted를
  기록하고 같은 KST date의 남은 queue와 reservation을 0으로 만든다.
- daily usage 70%부터 P4 company/financial enrichment만 중단하고 90%부터 P1 DS001,
  필수 corp-code refresh와 active safety state만 허용한다. P2는 v1 canonical enum에 직접
  mapping되는 structured DS005, P3는 DS004 PII-free projection이며 unmapped operation은
  online 거부한다. 70%/90% 임계치와 priority degradation은 provider 계약이
  아니라 프로젝트의 보수적 운영 정책이다.
- v1 state pair는 `bnkMngtPcbg` open과 `bnkMngtPcsp` close뿐이다. duplicate는 idempotent,
  close-before-open은 stable `DISCLOSURE_STATE_CLOSE_WITHOUT_OPEN`, correction은 새 transition/
  revision이다. scorer는 active-state view만 읽고 provider HTTP를 만들지 않으며 close 뒤 해당
  contribution은 0이다.
- provider raw body/header/request URL/raw hash 저장은 0이다. online persistent sanitized
  observation은 source별 positive retention과 nonempty owner가 operator config에 모두 있을
  때만 허용하며 offline/Testcontainers ephemeral write만 예외다.

## 점수표의 근거와 한계

| 이벤트 | 현재 점수 | 근거 | 한계 |
|---|---:|---|---|
| 감사의견 한정/부적정/의견거절 | 1.0 | OpenDART 감사의견 endpoint는 `adt_opinion` 구조화 필드를 제공한다. 감사의견과 going concern 관련 연구는 시장 반응과 재무위험 신호성을 반복해서 다룬다. | 모든 부실기업이 사전에 비적정 의견을 받는 것은 아니다. 점수는 강한 위험 검토 트리거일 뿐 부도 예측이 아니다. |
| 부도발생 (`dfOcr`) | 1.0 | OpenDART 주요사항보고서(부도발생) 전용 endpoint(apiId 2020019). 부도는 going-concern을 직접 위협하는 사건이다. | 정정·후속 공시가 이어질 수 있어 단발 점수는 상태 변화를 반영하지 못한다. |
| 회생절차 개시신청 (`ctrcvsBgrq`) | 1.0 | OpenDART 주요사항보고서(회생절차 개시신청) 전용 endpoint(apiId 2020021). 도산 절차 진입 신호다. | 개시신청과 실제 개시·기각은 다르다. 상태 추적은 S1.6 aggregator 과제다. |
| 해산사유 발생 (`dsRsOcr`) | 1.0 | OpenDART 주요사항보고서(해산사유 발생) 전용 endpoint(apiId 2020022). 법인 해산 신호다. | 합병형 해산 등 맥락에 따라 위험 성격이 달라질 수 있다. |
| 채권은행 등의 관리절차 개시 (`bnkMngtPcbg`) | 1.0 | OpenDART 주요사항보고서(채권은행 등의 관리절차 개시) 전용 endpoint(apiId 2020027). 채권단 관리 진입은 재무 distress 신호다. | 현재 S1.2 scorer는 lookback 근사다. S1.6은 `bnkMngtPcsp` close와의 state pair 계약을 동결했지만 production state transition은 후속 구현 전까지 미가용이다. |
| 영업정지 (`bsnSp`) | 0.8 | OpenDART 주요사항보고서(영업정지) 전용 endpoint(apiId 2020020). 사업 정지는 현금흐름·존속성에 직접 영향을 준다. | 정지 범위(일부/전부)와 기간 편차가 커서 distress 최상위보다 한 단계 낮게 둔다. |
| 감자 결정 (`crDecsn`) | 0.8 | OpenDART 주요사항보고서(감자 결정) 전용 endpoint(apiId 2020026). 결손보전형 무상감자는 자본잠식 신호다. | 액면병합·구조조정 목적 감자도 있어 목적별 세분화 전에는 과대해석을 피한다. |
| 소송 등의 제기 | 0.4 | OpenDART `lwstLg` 전용 endpoint가 있다. 기업 소송은 직접 비용, 평판 비용, 주주가치 반응의 이질성이 크다. | 소송 금액, 원고/피고 지위, 승소 가능성에 따라 위험도가 갈린다. 그래서 낮은 점수로 시작한다. |
| 유상증자 결정 | 0.6 | OpenDART `piicDecsn` 전용 endpoint가 있다. Seasoned equity offering 연구는 공모/증자 발표의 평균 음의 주가 반응을 보고한다. | 증자 목적이 성장투자이면 위험 의미가 약해질 수 있다. 현재 v1은 목적별 세분화를 하지 않는다. |
| 전환사채권 발행결정 | 0.6 | OpenDART `cvbdIsDecsn` 전용 endpoint가 있다. 해외 연구에는 음의 issuer return 근거가 있고, 국내 규제는 사모 전환사채 등 일반주주 보호 이슈를 명시한다. | 한국 연구에는 단기 양의 공시반응도 있다. 따라서 이 점수는 수익예측 근거가 아니라 희석·리픽싱·복잡상품 검토 트리거다. |
| 신주인수권부사채권 발행결정 (`bdwtIsDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020034). CB와 같은 mezzanine 희석·리픽싱 위험. | 한국 공시효과 연구가 엇갈려 수익예측이 아니라 검토 트리거로만 쓴다. |
| 교환사채권 발행결정 (`exbdIsDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020035). 교환대상 주식 수급·복잡상품 위험. | 교환 조건별 편차가 커 정책 등급으로만 둔다. |
| 회사합병 결정 (`cmpMgDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020050). 지배구조·주주가치 변동이 큰 reorg. | 합병 시너지/조건에 따라 반응이 이질적이라 검토 트리거로만 쓴다. |
| 회사분할 결정 (`cmpDvDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020051). 물적/인적 분할은 사업·자본 구조를 바꾼다. | 분할 방식별 주주가치 영향이 갈려 정책 등급으로 둔다. |
| 회사분할합병 결정 (`cmpDvmgDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020052). 분할과 합병이 겹치는 복합 reorg. | 구조가 복잡해 사건별 검토가 필요하다. |
| 영업양도 결정 (`bsnTrfDecsn`, S1.2b) | 0.6 | OpenDART 전용 endpoint(apiId 2020043). 핵심 사업부 양도는 사업구조 변화 신호. | 양도 대상·규모별 편차가 커 검토 트리거로만 쓴다. |
| 관리종목/상장폐지, 불성실공시법인, 최대주주 변경 | blocked | OpenDART 공시검색 제목이나 넓은 거래소공시 유형만으로 안정 분류하기 어렵다. | KRX/FSS 구조화 source가 정해지면 별도 세션에서 승격한다. |

점수 간격은 엄밀한 확률 보정값이 아니다. v1에서는 “going-concern을 직접 위협하는 강한 위험 검토 = 1.0”, “심각하지만 범위·목적 편차가 큰 distress/자본 이벤트 = 0.8”, “자본구조/희석 위험 = 0.6”, “사건별 편차가 큰 법적 위험 = 0.4”라는 정책 등급이다. 따라서 이 값은 백테스트와 실제 수집 데이터가 쌓이면 version을 올려 조정해야 한다.

going-concern distress 이벤트는 endpoint identity만으로 이벤트가 성립한다(발생 자체가 신호). 반면 감사의견은 `adt_opinion` 같은 구조화 필드 조건이 있어야 점수화한다. 두 경우 모두 `report_nm` 문자열은 점수 근거가 아니다. distress endpoint는 이벤트별 날짜 필드명이 제각각이라, 구조 규약인 접수번호(`rcept_no`) 앞 8자리로 접수일자를 복원해 window를 판정한다.

### 이벤트 유형별 유효기간(window)

이벤트 성격에 따라 "얼마나 오래된 공시까지 점수에 반영하는가"가 달라야 한다. 이를 무시하고 모든 이벤트에 30일을 일괄 적용하면, 31일 전에 회생절차를 신청한 기업이나 비적정 감사의견을 받은 기업이 여전히 위험 상태인데도 점수가 0이 되어 P1(고위험 주문 검토)을 훼손한다. 그래서 mapping에 `effective_window_days`를 두고 유형별로 다르게 판정한다.

| 유형 | 유효기간 | 이유 |
|---|---:|---|
| 공시효과형 (유상증자·전환사채·소송·신주인수권부사채·교환사채) | 30일 | 발표 시점 충격이 시간이 지나며 감쇠하는 사건 |
| reorg·사업구조 (회사합병·분할·분할합병·영업양도, S1.2b) | 90일 | 결정 공시 후 절차·이사회/주총·완료까지 수주~수개월 이어져 위험 검토가 더 오래 유효 |
| 상태 지속형 (부도·회생·해산·관리절차·영업정지·감자·비적정 감사의견) | 365일 | 사건이 끝난 게 아니라 위험 상태가 지속됨. 30일 뒤 조용히 사라지면 안 됨 |

- 유효기간 값도 점수와 마찬가지로 `policy_v1_unvalidated`다. 365일은 "상태가 지속된다"는 성질의 v1 근사이지 검증된 상수가 아니다.
- 더 정확한 모델은 고정 일수가 아니라 구조화된 상태 해제 공시로 푸는 것이다. S1.6 v1은
  채권은행 관리절차 `bnkMngtPcbg` open → `bnkMngtPcsp` close만 승인한다. 회생절차 종결은
  승인된 structured endpoint/field가 없으므로 `report_nm`으로 추론하지 않고 기존 lookback을
  유지한다.
- active mapping은 `effective_window_days`를 필수로 요구한다. 신규 고위험 이벤트가 실수로 30일 기본값에 묶이는 회귀를 validation으로 막는다.

`effective_window_days`는 scorer의 "점수 반영 여부" 판정에만 쓰이며, endpoint에서 실제로 데이터를 긁는 수집 기간과는 별개다. 수집은 호출부가 지정하는 임의 기간으로 이뤄지고 30일에 묶이지 않는다.

## 공시위험 감시 모델 (v1 판단시점 조회 / 목표 상태추적)

"공시위험을 계속 감시하는가?"에 대한 정확한 답을 고정한다. **v1은 백그라운드 상시 감시가 아니다.**

| 모델 | 설명 | 상태 |
|---|---|---|
| 판단 시점 조회 (on-demand lookback) | RiskEngine은 PostgreSQL에 저장된 관측치 또는 snapshot을 lookback해 `disclosure_risk_score`를 계산한다. 주문 판단 경로에서 OpenDART HTTP 요청을 직접 fan-out하지 않는다. 유효기간은 "판단 시점에 이 위험 상태가 아직 유효한가"를 근사하는 lookback 창이다. | v1 (S1.2~S1.2c 현재) |
| 지속 상태 추적 (continuous/stateful) | 승인된 구조화 pair로 상태를 open/close하고 판단 시점에는 current active view만 읽는다. v1 pair는 `bnkMngtPcbg`/`bnkMngtPcsp`뿐이다. | 내부 state machine·append-only transition/current view 구현, online 수집과 RiskEngine 소비 전환은 미활성 |
| 백그라운드 상시 감시/알림 | 새 공시를 계속 폴링해 능동적으로 경보/차단한다. | 현재 범위 아님 |

핵심은 다음과 같다.

- 이벤트 유형별 유효기간(공시효과형 30일 / reorg·사업구조 90일 / 상태 지속형 365일)은 **지속 상태 추적의 v1 근사**다. 30일 일괄로 되돌리면 상태 지속형이 31일 뒤 0점이 되는 회귀가 되살아나고, 되돌려도 감시 방식(판단 시점 조회)은 그대로이므로 손해만 있다.
- 정확한 모델은 고정 일수 lookback이 아니라 승인된 structured open/close 상태 머신이다.
  v1은 관리절차 pair만 구현하고, 다른 close를 문자열로 추론하지 않는다. collector와 transition은
  S1.6 production PR에서 구현하며 이를 노출하는 REST/gRPC/Dashboard는 S1.6 이후 명시적
  contract-change 세션에서 별도로 승인한다.
- 실제 배선(주문 판단이 이 점수를 소비)은 S2.2 `DisclosureRiskPort` + S2.3 Decision API에서 이뤄진다. S1.2는 결정적 점수 산출과 계약까지가 범위다.

YAML에는 `calibration_status: policy_v1_unvalidated`를 명시한다. 이 값이 `korea_market_calibrated`로 바뀌려면 한국시장 대상 event study나 백테스트 결과 문서가 같이 있어야 한다. 특히 전환사채는 국내 연구가 엇갈리므로, 향후 보정 전에는 “0.6이 손실 기대값을 의미한다”고 말하면 안 된다.

## 증명 가능성 체크리스트

누군가 “이 점수가 왜 맞냐”고 물으면 다음 순서로 답한다.

1. 공식 source 증명: OpenDART guide의 endpoint URL과 응답 필드를 제시한다.
2. 구현 증명: YAML mapping과 scorer test를 제시해 같은 입력이 같은 출력을 낸다는 점을 보인다.
3. 문헌 증명: 이벤트가 시장위험/정보위험과 연결된다는 선행연구를 제시한다.
4. 한계 고지: 숫자는 아직 한국시장 통계 보정값이 아니라 정책 등급이라고 명시한다.
5. 향후 증명 계획: S1.6 event aggregator 이후 allowlisted sanitized observation과 가격 데이터를 결합해 event study를 수행한다.

이 체크리스트의 1~3은 S1.2에서 충족한다. 4는 문서상 고지한다. 5는 아직 미완료다.

## 신뢰성 단계

| 단계 | 신뢰 수준 | 완료 조건 |
|---|---|---|
| L1 fixture 결정성 | 같은 입력이면 같은 점수 | offline fixture와 unit test 통과 |
| L2 공식 endpoint 정합성 | 문자열이 아니라 endpoint/구조화 필드 기반 | OpenDART 공식 guide와 endpoint URL 대조 |
| L3 sanitized 감사 가능성 | 같은 canonical projection의 결정 경로를 재검증 가능 | observation ID, allowlisted projection hash, 수집시각, mapping version과 offline fixture. provider raw persistence 0 |
| L4 online smoke | 실제 키와 live endpoint가 동작 | **완료(2026-07-10)**. 대표 5종목 소량 조회와 당시 ignored observation projection 확인. S1.6 persistent input으로 raw를 승격하지 않음 |
| L5 교차검증 | DART 외 원천과 충돌 확인 | S1.6 event aggregator에서 KRX/FSS/KIND 등 official source와 canonical merge |
| L6 보정 검증 | 점수 threshold가 실제 위험 통제에 맞음 | 과거 이벤트 study와 백테스트로 WARN/BLOCK 임계값 조정 |

현재 S1.2~S1.2c 계열은 L1~L4까지 충족했다. “점수 기준이 한국시장에 맞다”고 말하려면 L6까지 가야 한다.

### 2026-07-10 live smoke 결과

- 사용자 승인 후 실제 OpenDART API를 총 16 physical attempts 호출했다. `corpCode.xml` 2회, `company.json` 9회, `list.json` 5회이며 모든 호출은 retry를 1 attempt로 제한했다. 마지막 `company.json` 1회는 env-only private transport 전환 후 고정 HTTPS origin·parser 동작을 검증한 hardened smoke다.
- corp code 매핑과 대표 5종목 기업개황 계약을 검증했고, 대표 5종목 공시목록 observation projection 5개를 ignored `data/opendart` 경로에 저장했다. 저장 파일에서 API key가 없음을 다시 확인했다. 이 historical artifact는 S1.6 DB input이 아니며 S1.6은 provider raw persistence를 0으로 고정한다.
- 응답은 rate/limit/quota/reset 관련 header를 제공하지 않았다. 따라서 이 smoke는 인증·endpoint·parser와 민감 필드 제거를 검증하지만 해당 계정의 실효 threshold나 reset timezone을 실측하지는 않는다. 공식 개인 한도 20,000 적용과 별개로 두 값은 S1.6 collector 활성화 전 운영자가 계정 화면에서 확인하는 gate로 유지한다.

## 금융사 리포트는 어떻게 쓸 것인가

금융사 리포트는 production risk score의 1차 근거로 쓰지 않는 것이 맞다. 이유는 세 가지다.

1. 라이선스와 재배포 제한이 있다.
2. 리포트 coverage가 종목별로 불균형하다.
3. 애널리스트 의견은 설명·해석에는 유용하지만, 주문 차단 점수처럼 재현 가능해야 하는 값에는 부적합하다.

대신 금융사 리포트는 다음 범위에서만 쓴다.

| 용도 | 사용 여부 |
|---|---|
| RiskEngine `disclosure_risk_score` 직접 산출 | 사용하지 않음 |
| RAG 설명 보조 | 라이선스가 허용될 때 요약/인용 범위 내 사용 |
| 사람이 mapping 조정할 때 참고 | 가능 |
| 백테스트 feature | 원문 저장/재배포 권리 확인 후 별도 세션 |

## 주석 정책

“모든 함수에 주석 1개”는 처음에는 좋아 보이지만, 단순 getter나 변환 함수에도 뻔한 주석이 붙어 오히려 유지보수를 해친다. 이 프로젝트에는 아래 지시가 더 맞다.

```text
설정 파일을 제외한 코드에서 public 함수, class, client method, 외부 API wrapper, parser, scorer, storage writer에는 한글 docstring 또는 주석을 남긴다.
주석은 '무엇을 하는가'를 반복하지 말고 입력/출력 계약, 공식 원천, 보안/운영 주의점, 왜 이 방식이어야 하는지를 1~2문장으로 설명한다.
private helper는 계약·보안·운영상 실수하기 쉬운 이유가 있을 때만 주석을 단다.
```

## 검증 명령

```bash
cd workspaces/decision-platform/python-services
uv run pytest tests/data/opendart
uv run pytest
uv run ruff check .
uv run mypy app

cd ../spring-api
./gradlew test
```

## 외부 근거

- OpenDART 공시검색 guide: `pblntf_ty`, `pblntf_detail_ty`는 요청 필터이고 응답은 `report_nm`, `rcept_no`, `rcept_dt` 중심이다. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
- OpenDART 기업개황 endpoint: `GET /api/company.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002
- OpenDART 고유번호 endpoint: `GET /api/corpCode.xml`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018
- OpenDART 유상증자 결정 endpoint: `GET /api/piicDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020023
- OpenDART 전환사채권 발행결정 endpoint: `GET /api/cvbdIsDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020033
- OpenDART 감사의견 endpoint: `GET /api/accnutAdtorNmNdAdtOpinion.json`, `adt_opinion` 필드 제공. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020009
- OpenDART 단일회사 주요 재무지표 endpoint: `GET /api/fnlttSinglIndx.json`, `idx_cl_code`/`idx_nm`/`idx_val` 구조화 재무지표 제공. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2022001
- OpenDART 부도발생 endpoint: `GET /api/dfOcr.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020019
- OpenDART 영업정지 endpoint: `GET /api/bsnSp.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020020
- OpenDART 회생절차 개시신청 endpoint: `GET /api/ctrcvsBgrq.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020021
- OpenDART 해산사유 발생 endpoint: `GET /api/dsRsOcr.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020022
- OpenDART 감자 결정 endpoint: `GET /api/crDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020026
- OpenDART 채권은행 등의 관리절차 개시 endpoint: `GET /api/bnkMngtPcbg.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020027
- OpenDART 신주인수권부사채권 발행결정 endpoint(S1.2b): `GET /api/bdwtIsDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020034
- OpenDART 교환사채권 발행결정 endpoint(S1.2b): `GET /api/exbdIsDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020035
- OpenDART 회사합병 결정 endpoint(S1.2b): `GET /api/cmpMgDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020050
- OpenDART 회사분할 결정 endpoint(S1.2b): `GET /api/cmpDvDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020051
- OpenDART 회사분할합병 결정 endpoint(S1.2b): `GET /api/cmpDvmgDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052
- OpenDART 영업양도 결정 endpoint(S1.2b): `GET /api/bsnTrfDecsn.json`. https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020043
- OpenDART 주요사항보고서 주요정보조회: 주요사항보고서 36종 범위. https://opendart.fss.or.kr/disclosureinfo/mainMatter/main.do
- Asquith and Mullins, 1986, "Equity issues and offering dilution", Journal of Financial Economics. 증자 발표가 평균적으로 음의 주가 반응과 연결됨. https://www.sciencedirect.com/science/article/pii/0304405X86900504
- Masulis and Korwar, 1986, "Seasoned equity offerings: An empirical investigation", Journal of Financial Economics. underwritten common stock offering 발표의 평균 음의 조정 보고. https://www.sciencedirect.com/science/article/pii/0304405X86900516
- Duca, Dutordoir, Veld, and Verwijmeren, 2012, "Why are convertible bond announcements associated with increasingly negative issuer stock returns?", Journal of Banking & Finance. 전환사채 발표의 음의 issuer return 근거. https://www.sciencedirect.com/science/article/pii/S0378426612000817
- Kim and Song, 2018, "Convertible bond announcement returns, capital expenditures, and investment opportunities: Evidence from Korea", Pacific-Basin Finance Journal. 한국 전환사채 발행공시에서 양의 CAR와 투자기회 조건부 효과를 보고하므로 CB 점수는 수익예측 근거가 아니라 정책 검토 트리거로 제한해야 한다. https://www.sciencedirect.com/science/article/abs/pii/S0927538X18301185
- 금융위원회, 2025, 사모 전환사채 등 공시의무 강화 보도자료. 일반주주 보호와 납입기일 전 충분한 공시 필요성을 명시한다. https://www.fsc.go.kr/no010101/84627
- Kothari, Li, and Short, 2009, "The Effect of Disclosures by Management, Analysts, and Business Press on Cost of Capital, Return Volatility, and Analyst Forecasts", The Accounting Review. 부정적 disclosure가 위험 측정치와 연결됨. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1113337
- Hutton, Marcus, and Tehranian, 2009, "Opaque financial reports, R2, and crash risk", Journal of Financial Economics. 재무보고 불투명성과 crash risk 연결. https://www.sciencedirect.com/science/article/abs/pii/S0304405X09000993
- Bhagat, Bizjak, and Coles, 1998, "The Shareholder Wealth Implications of Corporate Lawsuits", Financial Management. 기업 소송의 직접·간접 비용과 주주가치 영향 분석. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=8129
