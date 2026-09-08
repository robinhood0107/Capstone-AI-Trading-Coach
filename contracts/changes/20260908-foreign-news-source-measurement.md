# Finnhub·GDELT 실측과 채택 판정

작성일: 2026-09-08

## 승인과 경계

사용자가 두 원천을 **실제로 호출해 재 보고** 결정하라고 명시했다 - "조사 하고 임시로 전부
구현해서 api 전부 받아봐서 결정한다", "GDELT 는 완전히 성공해야 추가. 먼저 테스트".

측정과 채택을 분리했다. `foreign_news_provider_probe_cli` 의 채택 경로는 control packet 과
검증된 감정 모델을 요구하는데, 커버리지를 *재는* 데는 그 게이트가 필요하지 않다. 그래서
같은 HTTPS 경계만 격리 스크립트로 직접 호출했다. 그 스크립트는 리포에 커밋하지 않는다 -
채택 권한이 없는 측정 도구이고, 채택하면 그때 production 경로를 만든다.

**원문은 남기지 않았다.** headline·summary·body 를 파일로도 표준출력으로도 쓰지 않고 개수,
길이 분포, 필드 이름, 상태 코드만 봤다. 산출물에 기사 텍스트가 없다.

## 호출 수

| 원천 | 물리 호출 | 상한 |
|---|---|---|
| Finnhub | 17 | 스크립트 상한 12/실행 |
| GDELT | 7 | 스크립트 상한 12/실행 |

## Finnhub — 기각 (커버리지 없음)

한국 종목이 이 계정 등급에 없다. 스로틀이 아니라 권한이다.

    symbol=AAPL        status=200  items=245
      fields=[category, datetime, headline, id, image, related, source, summary, url]
    symbol=005930.KS   status=403  body={"error":"You don't have access to this resource."}
    symbol=005930.KQ   status=403  body={"error":"You don't have access to this resource."}
    symbol=SSNLF       status=200  items=42

판독을 두 번 확인했다. `.KS` 는 20초 간격 두 번 모두 같은 403 본문을 냈고, **같은 세션에서
그 직후 AAPL 이 245건을 냈다.** 즉 키는 뉴스 권한이 있고 KRX 만 없다.

계약 관점에서는 필드가 요건을 채운다 - `datetime` 은 host 가 아는 발행 시각이고 `url` 로
등록 도메인 판정도 가능하다. 막는 것은 커버리지다.

`SSNLF`(삼성전자 미국 OTC ADR)로는 42건이 온다. 그래도 채택하지 않는다.

* 유니버스 31종목 중 미국 OTC ADR 을 가진 종목은 소수이고, 종목코드→ADR 티커 매핑은
  우리가 host 로서 아는 사실이 아니다. 그 매핑을 만들면 그것 자체가 검증되지 않은 추론이
  근거 경로에 들어오는 일이다.
* 영어 wire 기사는 KRX 공시보다 늦고, 같은 사건을 공시가 이미 공식 발행일과 함께 준다.

**판정: 기각.** 재검토 조건은 계정 등급 상향(KRX company-news 권한)이다. 그때 커버리지를
다시 재고 나서야 채택을 논한다. FinBERT 게이트는 이 판정과 무관하게 그대로 둔다 - 채택하지
않으므로 게이트를 열 이유가 없다.

## GDELT — 미측정 (이 네트워크에서 200 을 받지 못했다)

    mode=timelinevol / timelinetone / artlist   status=429
    HTTP/1.1 강제, HTTP/2 해제 모두 동일        status=429
    90초 간격 단일 호출도 동일                  status=429
    body="Please limit requests to one every 5 seconds or contact ... for larger queries."

7회 호출 전부 429 다. 5초 간격 제한을 지켜도, 90초를 두고 한 번만 보내도 같다. 이 네트워크의
출구 IP 가 공유 NAT 이라 우리 호출량과 무관하게 제한에 걸리는 것으로 보인다.

**판정: 추가하지 않는다.** 사용자 기준이 "완전히 성공해야 추가"였고 성공한 응답이 0건이다.
정직하게 "실패했다"가 아니라 **"이 네트워크에서 측정하지 못했다"**로 기록한다 - 원천의
품질에 대한 판단이 아니다.

덧붙여, 200 을 받았어도 거부권 근거로는 쓰지 않을 예정이었다. GDELT 는 집계 tone/volume 을
주고 `artlist` 의 날짜 필드는 GDELT 가 문서를 *본* 시각이며 발행사의 발행일이 아니다. 인용할
본문도 주지 않는다. 거부권 계약이 요구하는 것은 host 가 아는 발행일과 경계 인용문 둘 다다.

## 무엇을 쓰기로 했나

거부권의 근거는 **OpenDART 구조화 공시** 하나다. 근거와 실측은
`20260908-disclosure-news-veto-revival.md` 에 있다. 요약하면 31종목 중 29종목이 corp_code 에
매핑되고, 공식 접수일이 전건 오고, 등록 도메인(`dart.fss.or.kr`)이 이미 카탈로그에 있다.

## 감정 모델

쓰지 않는다. 공시 기반이므로 감정 점수가 필요 없고, 거부권은 등록 도메인의 경계 인용문으로
판정한다. 기존 FinBERT 실측(SENTiVENT 321건, ProsusAI macroF1 0.571 / ECE 0.242 / critical 64,
기준 0.80 / 0.10 / 0)과 `FOREIGN_NEWS_TERMINALLY_ABSTAINED` 판정은 기록으로 그대로 남긴다.
한국어 재측정도 하지 않는다 - 채택할 원천이 없으므로 측정할 대상이 없다.
