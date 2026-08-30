# KIS OpenAPI 전체 카탈로그 (자동 생성)

기준 자료: `한국투자증권_오픈API_전체문서_20260707` XLSX `API 목록` sheet (KIS 공식 배포, 모의 지원 경계의 단일 진실 소스)

이 문서는 `scripts/generate_kis_api_catalog.py`로 생성한다. 직접 수정하지 않고, XLSX 배포본이 갱신되면 재생성해서 커밋한다.

| 요약 | 값 |
|---|---|
| 전체 API | 338 (REST 278, WebSocket 60) |
| 모의투자 Domain 지원 | 46 |
| 명시적 모의 TR_ID 보유 | 43 |
| 모의투자 미지원 | 292 |

모의 지원 판정: `모의 Domain`에 모의투자용 URL이 있으면 지원으로 보고, `모의 TR_ID`가 실전과 같은지/분리인지/없는지(OAuth 계열)를 구분해 표기한다.

## OAuth인증 (3개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 1 | 실시간 (웹소켓) 접속키 발급 | 실시간-000 | WS | `/oauth2/Approval` | - | - | 지원(Domain 전환) |
| 2 | 접근토큰폐기(P) | 인증-002 | POST | `/oauth2/revokeP` | - | - | 지원(Domain 전환) |
| 3 | 접근토큰발급(P) | 인증-001 | POST | `/oauth2/tokenP` | - | - | 지원(Domain 전환) |

## [국내주식] 주문/계좌 (23개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 4 | 기간별계좌권리현황조회 | 국내주식-211 | GET | `/uapi/domestic-stock/v1/trading/period-rights` | CTRGA011R | 모의투자 미지원 | 미지원 |
| 5 | 투자계좌자산현황조회 | v1_국내주식-048 | GET | `/uapi/domestic-stock/v1/trading/inquire-account-balance` | CTRP6548R | 모의투자 미지원 | 미지원 |
| 6 | 퇴직연금 예수금조회 | v1_국내주식-035 | GET | `/uapi/domestic-stock/v1/trading/pension/inquire-deposit` | TTTC0506R | 모의투자 미지원 | 미지원 |
| 7 | 주식예약주문정정취소 | v1_국내주식-018,019 | POST | `/uapi/domestic-stock/v1/trading/order-resv-rvsecncl` | (예약취소) CTSC0009U (예약정정) CTSC0013U | 모의투자 미지원 | 미지원 |
| 8 | 신용매수가능조회 | v1_국내주식-042 | GET | `/uapi/domestic-stock/v1/trading/inquire-credit-psamount` | TTTC8909R | 모의투자 미지원 | 미지원 |
| 9 | 주식통합증거금 현황 | 국내주식-191 | GET | `/uapi/domestic-stock/v1/trading/intgr-margin` | TTTC0869R | 모의투자 미지원 | 미지원 |
| 10 | 퇴직연금 미체결내역 | v1_국내주식-033 | GET | `/uapi/domestic-stock/v1/trading/pension/inquire-daily-ccld` | TTTC2201R(기존 KRX만 가능), TTTC2210R (KRX,NXT/SOR) | 모의투자 미지원 | 미지원 |
| 11 | 기간별매매손익현황조회 | v1_국내주식-060 | GET | `/uapi/domestic-stock/v1/trading/inquire-period-trade-profit` | TTTC8715R | 모의투자 미지원 | 미지원 |
| 12 | 주식주문(정정취소) | v1_국내주식-003 | POST | `/uapi/domestic-stock/v1/trading/order-rvsecncl` | TTTC0013U | VTTC0013U | 지원(모의 TR 분리) |
| 13 | 주식예약주문조회 | v1_국내주식-020 | GET | `/uapi/domestic-stock/v1/trading/order-resv-ccnl` | CTSC0004R | 모의투자 미지원 | 미지원 |
| 14 | 퇴직연금 매수가능조회 | v1_국내주식-034 | GET | `/uapi/domestic-stock/v1/trading/pension/inquire-psbl-order` | TTTC0503R | 모의투자 미지원 | 미지원 |
| 15 | 주식잔고조회 | v1_국내주식-006 | GET | `/uapi/domestic-stock/v1/trading/inquire-balance` | TTTC8434R | VTTC8434R | 지원(모의 TR 분리) |
| 16 | 퇴직연금 체결기준잔고 | v1_국내주식-032 | GET | `/uapi/domestic-stock/v1/trading/pension/inquire-present-balance` | TTTC2202R | 모의투자 미지원 | 미지원 |
| 17 | 매수가능조회 | v1_국내주식-007 | GET | `/uapi/domestic-stock/v1/trading/inquire-psbl-order` | TTTC8908R | VTTC8908R | 지원(모의 TR 분리) |
| 18 | 기간별손익일별합산조회 | v1_국내주식-052 | GET | `/uapi/domestic-stock/v1/trading/inquire-period-profit` | TTTC8708R | 모의투자 미지원 | 미지원 |
| 19 | 주식주문(현금) | v1_국내주식-001 | POST | `/uapi/domestic-stock/v1/trading/order-cash` | (매도) TTTC0011U (매수) TTTC0012U | (매도) VTTC0011U (매수) VTTC0012U | 지원(모의 TR 분리) |
| 20 | 매도가능수량조회 | 국내주식-165 | GET | `/uapi/domestic-stock/v1/trading/inquire-psbl-sell` | TTTC8408R | 모의투자 미지원 | 미지원 |
| 21 | 주식일별주문체결조회 | v1_국내주식-005 | GET | `/uapi/domestic-stock/v1/trading/inquire-daily-ccld` | (3개월이내) TTTC0081R (3개월이전) CTSC9215R | (3개월이내) VTTC0081R (3개월이전) VTSC9215R | 지원(모의 TR 분리) |
| 22 | 주식정정취소가능주문조회 | v1_국내주식-004 | GET | `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` | TTTC0084R | 모의투자 미지원 | 미지원 |
| 23 | 주식예약주문 | v1_국내주식-017 | POST | `/uapi/domestic-stock/v1/trading/order-resv` | CTSC0008U | 모의투자 미지원 | 미지원 |
| 24 | 주식주문(신용) | v1_국내주식-002 | POST | `/uapi/domestic-stock/v1/trading/order-credit` | (매도) TTTC0051U (매수) TTTC0052U | 모의투자 미지원 | 미지원 |
| 25 | 퇴직연금 잔고조회 | v1_국내주식-036 | GET | `/uapi/domestic-stock/v1/trading/pension/inquire-balance` | TTTC2208R | 모의투자 미지원 | 미지원 |
| 26 | 주식잔고조회_실현손익 | v1_국내주식-041 | GET | `/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl` | TTTC8494R | 모의투자 미지원 | 미지원 |

## [국내주식] 기본시세 (22개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 27 | 주식현재가 일자별 | v1_국내주식-010 | GET | `/uapi/domestic-stock/v1/quotations/inquire-daily-price` | FHKST01010400 | FHKST01010400 | 지원(동일 TR) |
| 28 | 주식현재가 시세 | v1_국내주식-008 | GET | `/uapi/domestic-stock/v1/quotations/inquire-price` | FHKST01010100 | FHKST01010100 | 지원(동일 TR) |
| 29 | 국내주식 시간외현재가 | 국내주식-076 | GET | `/uapi/domestic-stock/v1/quotations/inquire-overtime-price` | FHPST02300000 | 모의투자 미지원 | 미지원 |
| 30 | ETF 구성종목시세 | 국내주식-073 | GET | `/uapi/etfetn/v1/quotations/inquire-component-stock-price` | FHKST121600C0 | 모의투자 미지원 | 미지원 |
| 31 | 주식현재가 시간외시간별체결 | v1_국내주식-025 | GET | `/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion` | FHPST02310000 | FHPST02310000 | 지원(동일 TR) |
| 32 | NAV 비교추이(종목) | v1_국내주식-069 | GET | `/uapi/etfetn/v1/quotations/nav-comparison-trend` | FHPST02440000 | 모의투자 미지원 | 미지원 |
| 33 | 주식현재가 시간외일자별주가 | v1_국내주식-026 | GET | `/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice` | FHPST02320000 | FHPST02320000 | 지원(동일 TR) |
| 34 | 국내주식 시간외호가 | 국내주식-077 | GET | `/uapi/domestic-stock/v1/quotations/inquire-overtime-asking-price` | FHPST02300400 | 모의투자 미지원 | 미지원 |
| 35 | 주식현재가 당일시간대별체결 | v1_국내주식-023 | GET | `/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion` | FHPST01060000 | FHPST01060000 | 지원(동일 TR) |
| 36 | 주식현재가 시세2 | v1_국내주식-054 | GET | `/uapi/domestic-stock/v1/quotations/inquire-price-2` | FHPST01010000 | 모의투자 미지원 | 미지원 |
| 37 | ETF 현재가 호가 | ETF 현재가 호가 | GET | `/uapi/etfetn/v1/quotations/inquire-asking-price` | FHPST02400200 | - | 미지원 |
| 38 | 주식일별분봉조회 | 국내주식-213 | GET | `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` | FHKST03010230 | 모의투자 미지원 | 미지원 |
| 39 | 국내주식기간별시세(일/주/월/년) | v1_국내주식-016 | GET | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | FHKST03010100 | FHKST03010100 | 지원(동일 TR) |
| 40 | NAV 비교추이(일) | v1_국내주식-071 | GET | `/uapi/etfetn/v1/quotations/nav-comparison-daily-trend` | FHPST02440200 | 모의투자 미지원 | 미지원 |
| 41 | 주식현재가 호가/예상체결 | v1_국내주식-011 | GET | `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | FHKST01010200 | FHKST01010200 | 지원(동일 TR) |
| 42 | 주식현재가 체결 | v1_국내주식-009 | GET | `/uapi/domestic-stock/v1/quotations/inquire-ccnl` | FHKST01010300 | FHKST01010300 | 지원(동일 TR) |
| 43 | 주식현재가 회원사 | v1_국내주식-013 | GET | `/uapi/domestic-stock/v1/quotations/inquire-member` | FHKST01010600 | FHKST01010600 | 지원(동일 TR) |
| 44 | NAV 비교추이(분) | v1_국내주식-070 | GET | `/uapi/etfetn/v1/quotations/nav-comparison-time-trend` | FHPST02440100 | 모의투자 미지원 | 미지원 |
| 45 | 주식현재가 투자자 | v1_국내주식-012 | GET | `/uapi/domestic-stock/v1/quotations/inquire-investor` | FHKST01010900 | FHKST01010900 | 지원(동일 TR) |
| 46 | ETF/ETN 현재가 | v1_국내주식-068 | GET | `/uapi/etfetn/v1/quotations/inquire-price` | FHPST02400000 | 모의투자 미지원 | 미지원 |
| 47 | 국내주식 장마감 예상체결가 | 국내주식-120 | GET | `/uapi/domestic-stock/v1/quotations/exp-closing-price` | FHKST117300C0 | 모의투자 미지원 | 미지원 |
| 48 | 주식당일분봉조회 | v1_국내주식-022 | GET | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | FHKST03010200 | FHKST03010200 | 지원(동일 TR) |

## [국내주식] ELW 시세 (22개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 49 | ELW 현재가 시세 | v1_국내주식-014 | GET | `/uapi/domestic-stock/v1/quotations/inquire-elw-price` | FHKEW15010000 | FHKEW15010000 | 지원(동일 TR) |
| 50 | ELW 신규상장종목 | 국내주식-181 | GET | `/uapi/elw/v1/quotations/newly-listed` | FHKEW154800C0 | 모의투자 미지원 | 미지원 |
| 51 | ELW 투자지표추이(일별) | 국내주식-173 | GET | `/uapi/elw/v1/quotations/indicator-trend-daily` | FHPEW02740200 | 모의투자 미지원 | 미지원 |
| 52 | ELW 민감도 순위 | 국내주식-170 | GET | `/uapi/elw/v1/ranking/sensitivity` | FHPEW02850000 | 모의투자 미지원 | 미지원 |
| 53 | ELW 기초자산별 종목시세 | 국내주식-186 | GET | `/uapi/elw/v1/quotations/udrl-asset-price` | FHKEW154101C0 | 모의투자 미지원 | 미지원 |
| 54 | ELW 종목검색 | 국내주식-166 | GET | `/uapi/elw/v1/quotations/cond-search` | FHKEW15100000 | 모의투자 미지원 | 미지원 |
| 55 | ELW 변동성 추이(분별) | 국내주식-179 | GET | `/uapi/elw/v1/quotations/volatility-trend-minute` | FHPEW02840300 | 모의투자 미지원 | 미지원 |
| 56 | ELW 변동성추이(체결) | 국내주식-177 | GET | `/uapi/elw/v1/quotations/volatility-trend-ccnl` | FHPEW02840100 | 모의투자 미지원 | 미지원 |
| 57 | ELW 당일급변종목 | 국내주식-171 | GET | `/uapi/elw/v1/ranking/quick-change` | FHPEW02870000 | 모의투자 미지원 | 미지원 |
| 58 | ELW 투자지표추이(분별) | 국내주식-174 | GET | `/uapi/elw/v1/quotations/indicator-trend-minute` | FHPEW02740300 | 모의투자 미지원 | 미지원 |
| 59 | ELW 기초자산 목록조회 | 국내주식-185 | GET | `/uapi/elw/v1/quotations/udrl-asset-list` | FHKEW154100C0 | 모의투자 미지원 | 미지원 |
| 60 | ELW 변동성 추이(일별) | 국내주식-178 | GET | `/uapi/elw/v1/quotations/volatility-trend-daily` | FHPEW02840200 | 모의투자 미지원 | 미지원 |
| 61 | ELW 거래량순위 | 국내주식-168 | GET | `/uapi/elw/v1/ranking/volume-rank` | FHPEW02780000 | 모의투자 미지원 | 미지원 |
| 62 | ELW 지표순위 | 국내주식-169 | GET | `/uapi/elw/v1/ranking/indicator` | FHPEW02790000 | 모의투자 미지원 | 미지원 |
| 63 | ELW 투자지표추이(체결) | 국내주식-172 | GET | `/uapi/elw/v1/quotations/indicator-trend-ccnl` | FHPEW02740100 | 모의투자 미지원 | 미지원 |
| 64 | ELW 상승률순위 | 국내주식-167 | GET | `/uapi/elw/v1/ranking/updown-rate` | FHPEW02770000 | 모의투자 미지원 | 미지원 |
| 65 | ELW 민감도 추이(일별) | 국내주식-176 | GET | `/uapi/elw/v1/quotations/sensitivity-trend-daily` | FHPEW02830200 | 모의투자 미지원 | 미지원 |
| 66 | ELW 비교대상종목조회 | 국내주식-183 | GET | `/uapi/elw/v1/quotations/compare-stocks` | FHKEW151701C0 | 모의투자 미지원 | 미지원 |
| 67 | ELW 만기예정/만기종목 | 국내주식-184 | GET | `/uapi/elw/v1/quotations/expiration-stocks` | FHKEW154700C0 | 모의투자 미지원 | 미지원 |
| 68 | ELW LP매매추이 | 국내주식-182 | GET | `/uapi/elw/v1/quotations/lp-trade-trend` | FHPEW03760000 | - | 미지원 |
| 69 | ELW 민감도 추이(체결) | 국내주식-175 | GET | `/uapi/elw/v1/quotations/sensitivity-trend-ccnl` | FHPEW02830100 | 모의투자 미지원 | 미지원 |
| 70 | ELW 변동성 추이(틱) | 국내주식-180 | GET | `/uapi/elw/v1/quotations/volatility-trend-tick` | FHPEW02840400 | 모의투자 미지원 | 미지원 |

## [국내주식] 업종/기타 (14개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 71 | 국내주식 예상체결지수 추이 | 국내주식-121 | GET | `/uapi/domestic-stock/v1/quotations/exp-index-trend` | FHPST01840000 | 모의투자 미지원 | 미지원 |
| 72 | 국내주식업종기간별시세(일/주/월/년) | v1_국내주식-021 | GET | `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` | FHKUP03500100 | FHKUP03500100 | 지원(동일 TR) |
| 73 | 국내업종 시간별지수(분) | 국내주식-119 | GET | `/uapi/domestic-stock/v1/quotations/inquire-index-timeprice` | FHPUP02110200 | 모의투자 미지원 | 미지원 |
| 74 | 국내업종 구분별전체시세 | v1_국내주식-066 | GET | `/uapi/domestic-stock/v1/quotations/inquire-index-category-price` | FHPUP02140000 | 모의투자 미지원 | 미지원 |
| 75 | 업종 분봉조회 | v1_국내주식-045 | GET | `/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice` | FHKUP03500200 | 모의투자 미지원 | 미지원 |
| 76 | 국내휴장일조회 | 국내주식-040 | GET | `/uapi/domestic-stock/v1/quotations/chk-holiday` | CTCA0903R | 모의투자 미지원 | 미지원 |
| 77 | 국내주식 예상체결 전체지수 | 국내주식-122 | GET | `/uapi/domestic-stock/v1/quotations/exp-total-index` | FHKUP11750000 | 모의투자 미지원 | 미지원 |
| 78 | 국내업종 현재지수 | v1_국내주식-063 | GET | `/uapi/domestic-stock/v1/quotations/inquire-index-price` | FHPUP02100000 | 모의투자 미지원 | 미지원 |
| 79 | 국내선물 영업일조회 | 국내주식-160 | GET | `/uapi/domestic-stock/v1/quotations/market-time` | HHMCM000002C0 | 모의투자 미지원 | 미지원 |
| 80 | 국내업종 시간별지수(초) | 국내주식-064 | GET | `/uapi/domestic-stock/v1/quotations/inquire-index-tickprice` | FHPUP02110100 | 모의투자 미지원 | 미지원 |
| 81 | 국내업종 일자별지수 | v1_국내주식-065 | GET | `/uapi/domestic-stock/v1/quotations/inquire-index-daily-price` | FHPUP02120000 | 모의투자 미지원 | 미지원 |
| 82 | 금리 종합(국내채권/금리) | 국내주식-155 | GET | `/uapi/domestic-stock/v1/quotations/comp-interest` | FHPST07020000 | 모의투자 미지원 | 미지원 |
| 83 | 변동성완화장치(VI) 현황 | v1_국내주식-055 | GET | `/uapi/domestic-stock/v1/quotations/inquire-vi-status` | FHPST01390000 | 모의투자 미지원 | 미지원 |
| 84 | 종합 시황/공시(제목) | 국내주식-141 | GET | `/uapi/domestic-stock/v1/quotations/news-title` | FHKST01011800 | 모의투자 미지원 | 미지원 |

## [국내주식] 종목정보 (26개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 85 | 상품기본조회 | v1_국내주식-029 | GET | `/uapi/domestic-stock/v1/quotations/search-info` | CTPF1604R | 모의투자 미지원 | 미지원 |
| 86 | 예탁원정보(상장정보일정) | 국내주식-150 | GET | `/uapi/domestic-stock/v1/ksdinfo/list-info` | HHKDB669107C0 | 모의투자 미지원 | 미지원 |
| 87 | 예탁원정보(공모주청약일정) | 국내주식-151 | GET | `/uapi/domestic-stock/v1/ksdinfo/pub-offer` | HHKDB669108C0 | 모의투자 미지원 | 미지원 |
| 88 | 국내주식 재무비율 | v1_국내주식-080 | GET | `/uapi/domestic-stock/v1/finance/financial-ratio` | FHKST66430300 | 모의투자 미지원 | 미지원 |
| 89 | 예탁원정보(자본감소일정) | 국내주식-149 | GET | `/uapi/domestic-stock/v1/ksdinfo/cap-dcrs` | HHKDB669106C0 | 모의투자 미지원 | 미지원 |
| 90 | 예탁원정보(무상증자일정) | 국내주식-144 | GET | `/uapi/domestic-stock/v1/ksdinfo/bonus-issue` | HHKDB669101C0 | 모의투자 미지원 | 미지원 |
| 91 | 국내주식 증권사별 투자의견 | 국내주식-189 | GET | `/uapi/domestic-stock/v1/quotations/invest-opbysec` | FHKST663400C0 | 모의투자 미지원 | 미지원 |
| 92 | 국내주식 당사 신용가능종목 | 국내주식-111 | GET | `/uapi/domestic-stock/v1/quotations/credit-by-company` | FHPST04770000 | 모의투자 미지원 | 미지원 |
| 93 | 예탁원정보(주식매수청구일정) | 국내주식-146 | GET | `/uapi/domestic-stock/v1/ksdinfo/purreq` | HHKDB669103C0 | 모의투자 미지원 | 미지원 |
| 94 | 예탁원정보(액면교체일정) | 국내주식-148 | GET | `/uapi/domestic-stock/v1/ksdinfo/rev-split` | HHKDB669105C0 | 모의투자 미지원 | 미지원 |
| 95 | 예탁원정보(배당일정) | 국내주식-145 | GET | `/uapi/domestic-stock/v1/ksdinfo/dividend` | HHKDB669102C0 | 모의투자 미지원 | 미지원 |
| 96 | 국내주식 종목투자의견 | 국내주식-188 | GET | `/uapi/domestic-stock/v1/quotations/invest-opinion` | FHKST663300C0 | 모의투자 미지원 | 미지원 |
| 97 | 국내주식 안정성비율 | v1_국내주식-083 | GET | `/uapi/domestic-stock/v1/finance/stability-ratio` | FHKST66430600 | 모의투자 미지원 | 미지원 |
| 98 | 국내주식 수익성비율 | v1_국내주식-081 | GET | `/uapi/domestic-stock/v1/finance/profit-ratio` | FHKST66430400 | 모의투자 미지원 | 미지원 |
| 99 | 예탁원정보(실권주일정) | 국내주식-152 | GET | `/uapi/domestic-stock/v1/ksdinfo/forfeit` | HHKDB669109C0 | 모의투자 미지원 | 미지원 |
| 100 | 예탁원정보(의무예치일정) | 국내주식-153 | GET | `/uapi/domestic-stock/v1/ksdinfo/mand-deposit` | HHKDB669110C0 | 모의투자 미지원 | 미지원 |
| 101 | 국내주식 손익계산서 | v1_국내주식-079 | GET | `/uapi/domestic-stock/v1/finance/income-statement` | FHKST66430200 | 모의투자 미지원 | 미지원 |
| 102 | 당사 대주가능 종목 | 국내주식-195 | GET | `/uapi/domestic-stock/v1/quotations/lendable-by-company` | CTSC2702R | 모의투자 미지원 | 미지원 |
| 103 | 주식기본조회 | v1_국내주식-067 | GET | `/uapi/domestic-stock/v1/quotations/search-stock-info` | CTPF1002R | 모의투자 미지원 | 미지원 |
| 104 | 예탁원정보(유상증자일정) | 국내주식-143 | GET | `/uapi/domestic-stock/v1/ksdinfo/paidin-capin` | HHKDB669100C0 | 모의투자 미지원 | 미지원 |
| 105 | 예탁원정보(주주총회일정) | 국내주식-154 | GET | `/uapi/domestic-stock/v1/ksdinfo/sharehld-meet` | HHKDB669111C0 | 모의투자 미지원 | 미지원 |
| 106 | 국내주식 성장성비율 | v1_국내주식-085 | GET | `/uapi/domestic-stock/v1/finance/growth-ratio` | FHKST66430800 | 모의투자 미지원 | 미지원 |
| 107 | 국내주식 대차대조표 | v1_국내주식-078 | GET | `/uapi/domestic-stock/v1/finance/balance-sheet` | FHKST66430100 | 모의투자 미지원 | 미지원 |
| 108 | 예탁원정보(합병/분할일정) | 국내주식-147 | GET | `/uapi/domestic-stock/v1/ksdinfo/merger-split` | HHKDB669104C0 | 모의투자 미지원 | 미지원 |
| 109 | 국내주식 종목추정실적 | 국내주식-187 | GET | `/uapi/domestic-stock/v1/quotations/estimate-perform` | HHKST668300C0 | 모의투자 미지원 | 미지원 |
| 110 | 국내주식 기타주요비율 | v1_국내주식-082 | GET | `/uapi/domestic-stock/v1/finance/other-major-ratios` | FHKST66430500 | 모의투자 미지원 | 미지원 |

## [국내주식] 시세분석 (29개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 111 | 프로그램매매 종합현황(시간) | 국내주식-114 | GET | `/uapi/domestic-stock/v1/quotations/comp-program-trade-today` | FHPPG04600101 | 모의투자 미지원 | 미지원 |
| 112 | 국내주식 신용잔고 일별추이 | 국내주식-110 | GET | `/uapi/domestic-stock/v1/quotations/daily-credit-balance` | FHPST04760000 | 모의투자 미지원 | 미지원 |
| 113 | 시장별 투자자매매동향(일별) | 국내주식-075 | GET | `/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market` | FHPTJ04040000 | 모의투자 미지원 | 미지원 |
| 114 | 국내주식 공매도 일별추이 | 국내주식-134 | GET | `/uapi/domestic-stock/v1/quotations/daily-short-sale` | FHPST04830000 | 모의투자 미지원 | 미지원 |
| 115 | 종목별 투자자매매동향(일별) | 종목별 투자자매매동향(일별) | GET | `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily` | FHPTJ04160001 | 모의투자 미지원 | 미지원 |
| 116 | 종목조건검색 목록조회 | 국내주식-038 | GET | `/uapi/domestic-stock/v1/quotations/psearch-title` | HHKST03900300 | 모의투자 미지원 | 미지원 |
| 117 | 국내주식 상하한가 포착 | 국내주식-190 | GET | `/uapi/domestic-stock/v1/quotations/capture-uplowprice` | FHKST130000C0 | 모의투자 미지원 | 미지원 |
| 118 | 프로그램매매 종합현황(일별) | 국내주식-115 | GET | `/uapi/domestic-stock/v1/quotations/comp-program-trade-daily` | FHPPG04600001 | 모의투자 미지원 | 미지원 |
| 119 | 종목별 일별 대차거래추이 | 국내주식-135 | GET | `/uapi/domestic-stock/v1/quotations/daily-loan-trans` | HHPST074500C0 | 모의투자 미지원 | 미지원 |
| 120 | 종목조건검색조회 | 국내주식-039 | GET | `/uapi/domestic-stock/v1/quotations/psearch-result` | HHKST03900400 | 모의투자 미지원 | 미지원 |
| 121 | 국내주식 매물대/거래비중 | 국내주식-196 | GET | `/uapi/domestic-stock/v1/quotations/pbar-tratio` | FHPST01130000 | 모의투자 미지원 | 미지원 |
| 122 | 국내기관_외국인 매매종목가집계 | 국내주식-037 | GET | `/uapi/domestic-stock/v1/quotations/foreign-institution-total` | FHPTJ04400000 | 모의투자 미지원 | 미지원 |
| 123 | 관심종목 그룹별 종목조회 | 국내주식-203 | GET | `/uapi/domestic-stock/v1/quotations/intstock-stocklist-by-group` | HHKCM113004C6 | 모의투자 미지원 | 미지원 |
| 124 | 주식현재가 회원사 종목매매동향 | 국내주식-197 | GET | `/uapi/domestic-stock/v1/quotations/inquire-member-daily` | FHPST04540000 | 모의투자 미지원 | 미지원 |
| 125 | 종목별 프로그램매매추이(일별) | 국내주식-113 | GET | `/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily` | FHPPG04650201 | 모의투자 미지원 | 미지원 |
| 126 | 관심종목 그룹조회 | 국내주식-204 | GET | `/uapi/domestic-stock/v1/quotations/intstock-grouplist` | HHKCM113004C7 | 모의투자 미지원 | 미지원 |
| 127 | 종목별 외인기관 추정가집계 | v1_국내주식-046 | GET | `/uapi/domestic-stock/v1/quotations/investor-trend-estimate` | HHPTJ04160200 | 모의투자 미지원 | 미지원 |
| 128 | 종목별일별매수매도체결량 | v1_국내주식-056 | GET | `/uapi/domestic-stock/v1/quotations/inquire-daily-trade-volume` | FHKST03010800 | 모의투자 미지원 | 미지원 |
| 129 | 국내주식 체결금액별 매매비중 | 국내주식-192 | GET | `/uapi/domestic-stock/v1/quotations/tradprt-byamt` | FHKST111900C0 | 모의투자 미지원 | 미지원 |
| 130 | 프로그램매매 투자자매매동향(당일) | 국내주식-116 | GET | `/uapi/domestic-stock/v1/quotations/investor-program-trade-today` | HHPPG046600C1 | 모의투자 미지원 | 미지원 |
| 131 | 국내 증시자금 종합 | 국내주식-193 | GET | `/uapi/domestic-stock/v1/quotations/mktfunds` | FHKST649100C0 | 모의투자 미지원 | 미지원 |
| 132 | 국내주식 예상체결가 추이 | 국내주식-118 | GET | `/uapi/domestic-stock/v1/quotations/exp-price-trend` | FHPST01810000 | 모의투자 미지원 | 미지원 |
| 133 | 회원사 실시간 매매동향(틱) | 국내주식-163 | WS | `/uapi/domestic-stock/v1/quotations/frgnmem-trade-trend` | FHPST04320000 | 모의투자 미지원 | 미지원 |
| 134 | 시장별 투자자매매동향(시세) | v1_국내주식-074 | GET | `/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market` | FHPTJ04030000 | 모의투자 미지원 | 미지원 |
| 135 | 종목별 프로그램매매추이(체결) | v1_국내주식-044 | GET | `/uapi/domestic-stock/v1/quotations/program-trade-by-stock` | FHPPG04650101 | 모의투자 미지원 | 미지원 |
| 136 | 외국계 매매종목 가집계 | 국내주식-161 | GET | `/uapi/domestic-stock/v1/quotations/frgnmem-trade-estimate` | FHKST644100C0 | 모의투자 미지원 | 미지원 |
| 137 | 국내주식 시간외예상체결등락률 | 국내주식-140 | GET | `/uapi/domestic-stock/v1/ranking/overtime-exp-trans-fluct` | FHKST11860000 | 모의투자 미지원 | 미지원 |
| 138 | 종목별 외국계 순매수추이 | 국내주식-164 | GET | `/uapi/domestic-stock/v1/quotations/frgnmem-pchs-trend` | FHKST644400C0 | 모의투자 미지원 | 미지원 |
| 139 | 관심종목(멀티종목) 시세조회 | 국내주식-205 | GET | `/uapi/domestic-stock/v1/quotations/intstock-multprice` | FHKST11300006 | 모의투자 미지원 | 미지원 |

## [국내주식] 순위분석 (22개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 140 | 국내주식 예상체결 상승/하락상위 | v1_국내주식-103 | GET | `/uapi/domestic-stock/v1/ranking/exp-trans-updown` | FHPST01820000 | 모의투자 미지원 | 미지원 |
| 141 | 국내주식 호가잔량 순위 | 국내주식-089 | GET | `/uapi/domestic-stock/v1/ranking/quote-balance` | FHPST01720000 | 모의투자 미지원 | 미지원 |
| 142 | 국내주식 신용잔고 상위 | 국내주식-109 | GET | `/uapi/domestic-stock/v1/ranking/credit-balance` | FHKST17010000 | 모의투자 미지원 | 미지원 |
| 143 | 국내주식 시간외거래량순위 | 국내주식-139 | GET | `/uapi/domestic-stock/v1/ranking/overtime-volume` | FHPST02350000 | 모의투자 미지원 | 미지원 |
| 144 | 국내주식 배당률 상위 | 국내주식-106 | GET | `/uapi/domestic-stock/v1/ranking/dividend-rate` | HHKDB13470100 | 모의투자 미지원 | 미지원 |
| 145 | 국내주식 시간외잔량 순위 | v1_국내주식-093 | GET | `/uapi/domestic-stock/v1/ranking/after-hour-balance` | FHPST01760000 | 모의투자 미지원 | 미지원 |
| 146 | 국내주식 공매도 상위종목 | 국내주식-133 | GET | `/uapi/domestic-stock/v1/ranking/short-sale` | FHPST04820000 | 모의투자 미지원 | 미지원 |
| 147 | 국내주식 이격도 순위 | v1_국내주식-095 | GET | `/uapi/domestic-stock/v1/ranking/disparity` | FHPST01780000 | 모의투자 미지원 | 미지원 |
| 148 | HTS조회상위20종목 | 국내주식-214 | GET | `/uapi/domestic-stock/v1/ranking/hts-top-view` | HHMCM000100C0 | 모의투자 미지원 | 미지원 |
| 149 | 거래량순위 | v1_국내주식-047 | GET | `/uapi/domestic-stock/v1/quotations/volume-rank` | FHPST01710000 | 모의투자 미지원 | 미지원 |
| 150 | 국내주식 수익자산지표 순위 | v1_국내주식-090 | GET | `/uapi/domestic-stock/v1/ranking/profit-asset-index` | FHPST01730000 | 모의투자 미지원 | 미지원 |
| 151 | 국내주식 신고/신저근접종목 상위 | v1_국내주식-105 | GET | `/uapi/domestic-stock/v1/ranking/near-new-highlow` | FHPST01870000 | 모의투자 미지원 | 미지원 |
| 152 | 국내주식 우선주/괴리율 상위 | v1_국내주식-094 | GET | `/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio` | FHPST01770000 | 모의투자 미지원 | 미지원 |
| 153 | 국내주식 대량체결건수 상위 | 국내주식-107 | GET | `/uapi/domestic-stock/v1/ranking/bulk-trans-num` | FHKST190900C0 | 모의투자 미지원 | 미지원 |
| 154 | 국내주식 재무비율 순위 | v1_국내주식-092 | GET | `/uapi/domestic-stock/v1/ranking/finance-ratio` | FHPST01750000 | 모의투자 미지원 | 미지원 |
| 155 | 국내주식 시가총액 상위 | v1_국내주식-091 | GET | `/uapi/domestic-stock/v1/ranking/market-cap` | FHPST01740000 | 모의투자 미지원 | 미지원 |
| 156 | 국내주식 당사매매종목 상위 | v1_국내주식-104 | GET | `/uapi/domestic-stock/v1/ranking/traded-by-company` | FHPST01860000 | 모의투자 미지원 | 미지원 |
| 157 | 국내주식 등락률 순위 | v1_국내주식-088 | GET | `/uapi/domestic-stock/v1/ranking/fluctuation` | FHPST01700000 | 모의투자 미지원 | 미지원 |
| 158 | 국내주식 시장가치 순위 | v1_국내주식-096 | GET | `/uapi/domestic-stock/v1/ranking/market-value` | FHPST01790000 | 모의투자 미지원 | 미지원 |
| 159 | 국내주식 관심종목등록 상위 | v1_국내주식-102 | GET | `/uapi/domestic-stock/v1/ranking/top-interest-stock` | FHPST01800000 | 모의투자 미지원 | 미지원 |
| 160 | 국내주식 체결강도 상위 | v1_국내주식-101 | GET | `/uapi/domestic-stock/v1/ranking/volume-power` | FHPST01680000 | 모의투자 미지원 | 미지원 |
| 161 | 국내주식 시간외등락율순위 | 국내주식-138 | GET | `/uapi/domestic-stock/v1/ranking/overtime-fluctuation` | FHPST02340000 | 모의투자 미지원 | 미지원 |

## [국내주식] 실시간시세 (29개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 162 | 국내지수 실시간예상체결 | 실시간-027 | WS | `/tryitout/H0UPANC0` | H0UPANC0 | 모의투자 미지원 | 미지원 |
| 163 | 국내주식 장운영정보 (통합) | 국내주식 장운영정보 (통합) | POST | `/tryitout/H0UNMKO0` | H0UNMKO0 | 모의투자 미지원 | 미지원 |
| 164 | 국내주식 실시간회원사 (NXT) | 국내주식 실시간회원사 (NXT) | WS | `/tryitout/H0NXMBC0` | H0NXMBC0 | 모의투자 미지원 | 미지원 |
| 165 | 국내주식 실시간체결통보 | 실시간-005 | WS | `/tryitout/H0STCNI0` | H0STCNI0 | H0STCNI9 | 지원(모의 TR 분리) |
| 166 | 국내주식 시간외 실시간예상체결 (KRX) | 실시간-024 | WS | `/tryitout/H0STOAC0` | H0STOAC0 | 모의투자 미지원 | 미지원 |
| 167 | 국내주식 시간외 실시간호가 (KRX) | 실시간-025 | WS | `/tryitout/H0STOAA0` | H0STOAA0 | 모의투자 미지원 | 미지원 |
| 168 | 국내주식 실시간프로그램매매 (통합) | 국내주식 실시간프로그램매매 (통합) | WS | `/tryitout/H0UNPGM0` | H0UNPGM0 | 모의투자 미지원 | 미지원 |
| 169 | 국내주식 실시간호가 (통합) | 국내주식 실시간호가 (통합) | WS | `/tryitout/H0UNASP0` | H0UNASP0 | 모의투자 미지원 | 미지원 |
| 170 | 국내주식 실시간프로그램매매 (KRX) | 실시간-048 | WS | `/tryitout/H0STPGM0` | H0STPGM0 | 모의투자 미지원 | 미지원 |
| 171 | 국내주식 장운영정보 (KRX) | 실시간-049 | WS | `/tryitout/H0STMKO0` | H0STMKO0 | 모의투자 미지원 | 미지원 |
| 172 | 국내주식 실시간체결가 (KRX) | 실시간-003 | WS | `/tryitout/H0STCNT0` | H0STCNT0 | H0STCNT0 | 지원(동일 TR) |
| 173 | 국내지수 실시간프로그램매매 | 실시간-028 | WS | `/tryitout/H0UPPGM0` | H0UPPGM0 | 모의투자 미지원 | 미지원 |
| 174 | 국내주식 실시간회원사 (통합) | 국내주식 실시간회원사 (통합) | WS | `/tryitout/H0UNMBC0` | H0UNMBC0 | 모의투자 미지원 | 미지원 |
| 175 | 국내지수 실시간체결 | 실시간-026 | WS | `/tryitout/H0UPCNT0` | H0UPCNT0 | 모의투자 미지원 | 미지원 |
| 176 | 국내주식 실시간예상체결 (KRX) | 실시간-041 | WS | `/tryitout/H0STANC0` | H0STANC0 | 모의투자 미지원 | 미지원 |
| 177 | ELW 실시간호가 | 실시간-062 | WS | `/tryitout/H0EWASP0` | H0EWASP0 | 모의투자 미지원 | 미지원 |
| 178 | 국내주식 실시간호가 (KRX) | 실시간-004 | WS | `/tryitout/H0STASP0` | H0STASP0 | H0STASP0 | 지원(동일 TR) |
| 179 | 국내주식 실시간체결가 (통합) | 국내주식 실시간체결가 (통합) | WS | `/tryitout/H0UNCNT0` | H0UNCNT0 | 모의투자 미지원 | 미지원 |
| 180 | 국내주식 실시간호가 (NXT) | 국내주식 실시간호가 (NXT) | WS | `/tryitout/H0NXASP0` | H0NXASP0 | 모의투자 미지원 | 미지원 |
| 181 | 국내주식 실시간프로그램매매 (NXT) | 국내주식 실시간프로그램매매 (NXT) | WS | `/tryitout/H0NXPGM0` | H0NXPGM0 | 모의투자 미지원 | 미지원 |
| 182 | 국내주식 실시간체결가 (NXT) | 국내주식 실시간체결가 (NXT) | WS | `/tryitout/H0NXCNT0` | H0NXCNT0 | 모의투자 미지원 | 미지원 |
| 183 | ELW 실시간체결가 | 실시간-061 | WS | `/tryitout/H0EWCNT0` | H0EWCNT0 | 모의투자 미지원 | 미지원 |
| 184 | ELW 실시간예상체결 | 실시간-063 | WS | `/tryitout/H0EWANC0` | H0EWANC0 | 모의투자 미지원 | 미지원 |
| 185 | 국내주식 실시간예상체결 (NXT) | 국내주식 실시간예상체결 (NXT) | WS | `/tryitout/H0NXANC0` | H0NXANC0 | 모의투자 미지원 | 미지원 |
| 186 | 국내주식 실시간회원사 (KRX) | 실시간-047 | WS | `/tryitout/H0STMBC0` | H0STMBC0 | 모의투자 미지원 | 미지원 |
| 187 | 국내주식 실시간예상체결 (통합) | 국내주식 실시간예상체결 (통합) | WS | `/tryitout/H0UNANC0` | H0UNANC0 | 모의투자 미지원 | 미지원 |
| 188 | 국내주식 장운영정보 (NXT) | 국내주식 장운영정보 (NXT) | POST | `/tryitout/H0NXMKO0` | H0NXMKO0 | 모의투자 미지원 | 미지원 |
| 189 | 국내ETF NAV추이 | 실시간-051 | WS | `/tryitout/H0STNAV0` | H0STNAV0 | 모의투자 미지원 | 미지원 |
| 190 | 국내주식 시간외 실시간체결가 (KRX) | 실시간-042 | WS | `/tryitout/H0STOUP0` | H0STOUP0 | 모의투자 미지원 | 미지원 |

## [국내선물옵션] 주문/계좌 (15개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 191 | (야간)선물옵션 증거금 상세 | 국내선물-024 | GET | `/uapi/domestic-futureoption/v1/trading/ngt-margin-detail` | (구) JTCE6003R (신) CTFN7107R | 모의투자 미지원 | 미지원 |
| 192 | 선물옵션 총자산현황 | v1_국내선물-014 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-deposit` | CTRP6550R | 모의투자 미지원 | 미지원 |
| 193 | 선물옵션기간약정수수료일별 | v1_국내선물-017 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-daily-amount-fee` | CTFO6119R | 모의투자 미지원 | 미지원 |
| 194 | (야간)선물옵션 잔고현황 | 국내선물-010 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-ngt-balance` | (구) JTCE6001R (신) CTFN6118R | 모의투자 미지원 | 미지원 |
| 195 | 선물옵션 잔고현황 | v1_국내선물-004 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-balance` | CTFO6118R | VTFO6118R | 지원(모의 TR 분리) |
| 196 | 선물옵션 주문 | v1_국내선물-001 | POST | `/uapi/domestic-futureoption/v1/trading/order` | (주간 매수/매도) TTTO1101U (야간 매수/매도) (구) JTCE1001U (신) STTN1101U | (주간 매수/매도) VTTO1101U (야간은 모의투자 미제공) | 지원(모의 TR 분리) |
| 197 | 선물옵션 잔고평가손익내역 | v1_국내선물-015 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-balance-valuation-pl` | CTFO6159R | 모의투자 미지원 | 미지원 |
| 198 | 선물옵션 증거금률 | 선물옵션 증거금률 | GET | `/uapi/domestic-futureoption/v1/quotations/margin-rate` | TTTO6032R | 미지원 | 미지원 |
| 199 | 선물옵션 정정취소주문 | v1_국내선물-002 | POST | `/uapi/domestic-futureoption/v1/trading/order-rvsecncl` | (주간 정정/취소) TTTO1103U (야간 정정/취소) (구) JTCE1002U (신) STTN1103U | (주간 정정/취소) VTTO1103U (야간은 모의투자 미제공) | 지원(모의 TR 분리) |
| 200 | 선물옵션 주문체결내역조회 | v1_국내선물-003 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-ccnl` | TTTO5201R | VTTO5201R | 지원(모의 TR 분리) |
| 201 | (야간)선물옵션 주문체결 내역조회 | 국내선물-009 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-ngt-ccnl` | (구) JTCE5005R (신) STTN5201R | 모의투자 미지원 | 미지원 |
| 202 | (야간)선물옵션 주문가능 조회 | 국내선물-011 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-psbl-ngt-order` | (구) JTCE1004R (신) STTN5105R | 모의투자 미지원 | 미지원 |
| 203 | 선물옵션 잔고정산손익내역 | v1_국내선물-013 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-balance-settlement-pl` | CTFO6117R | 모의투자 미지원 | 미지원 |
| 204 | 선물옵션 주문가능 | v1_국내선물-005 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-psbl-order` | TTTO5105R | VTTO5105R | 지원(모의 TR 분리) |
| 205 | 선물옵션 기준일체결내역 | v1_국내선물-016 | GET | `/uapi/domestic-futureoption/v1/trading/inquire-ccnl-bstime` | CTFO5139R | 모의투자 미지원 | 미지원 |

## [국내선물옵션] 기본시세 (9개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 206 | 선물옵션 시세 | v1_국내선물-006 | GET | `/uapi/domestic-futureoption/v1/quotations/inquire-price` | FHMIF10000000 | FHMIF10000000 | 지원(동일 TR) |
| 207 | 국내선물 기초자산 시세 | 국내선물-021 | GET | `/uapi/domestic-futureoption/v1/quotations/display-board-top` | FHPIF05030000 | 모의투자 미지원 | 미지원 |
| 208 | 선물옵션 일중예상체결추이 | 국내선물-018 | GET | `/uapi/domestic-futureoption/v1/quotations/exp-price-trend` | FHPIF05110100 | 모의투자 미지원 | 미지원 |
| 209 | 선물옵션기간별시세(일/주/월/년) | v1_국내선물-008 | GET | `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice` | FHKIF03020100 | FHKIF03020100 | 지원(동일 TR) |
| 210 | 국내옵션전광판_선물 | 국내선물-023 | GET | `/uapi/domestic-futureoption/v1/quotations/display-board-futures` | FHPIF05030200 | 모의투자 미지원 | 미지원 |
| 211 | 선물옵션 분봉조회 | v1_국내선물-012 | GET | `/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice` | FHKIF03020200 | 모의투자 미지원 | 미지원 |
| 212 | 국내옵션전광판_옵션월물리스트 | 국내선물-020 | GET | `/uapi/domestic-futureoption/v1/quotations/display-board-option-list` | FHPIO056104C0 | 모의투자 미지원 | 미지원 |
| 213 | 선물옵션 시세호가 | v1_국내선물-007 | GET | `/uapi/domestic-futureoption/v1/quotations/inquire-asking-price` | FHMIF10010000 | FHMIF10010000 | 지원(동일 TR) |
| 214 | 국내옵션전광판_콜풋 | 국내선물-022 | GET | `/uapi/domestic-futureoption/v1/quotations/display-board-callput` | FHPIF05030100 | 모의투자 미지원 | 미지원 |

## [국내선물옵션] 실시간시세 (20개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 215 | 주식옵션 실시간호가 | 실시간-045 | WS | `/tryitout/H0ZOASP0` | H0ZOASP0 | 모의투자 미지원 | 미지원 |
| 216 | 선물옵션 실시간체결통보 | 실시간-012 | WS | `/tryitout/H0IFCNI0` | H0IFCNI0 | H0IFCNI9 | 지원(모의 TR 분리) |
| 217 | KRX야간선물 실시간종목체결 | 실시간-064 | WS | `/tryitout/H0MFCNT0` | H0MFCNT0 | 모의투자 미지원 | 미지원 |
| 218 | KRX야간선물 실시간호가 | 실시간-065 | WS | `/tryitout/H0MFASP0` | H0MFASP0 | 모의투자 미지원 | 미지원 |
| 219 | KRX야간옵션 실시간체결가 | 실시간-032 | WS | `/tryitout/H0EUCNT0` | H0EUCNT0 | 모의투자 미지원 | 미지원 |
| 220 | KRX야간옵션실시간예상체결 | 실시간-034 | WS | `/tryitout/H0EUANC0` | H0EUANC0 | 모의투자 미지원 | 미지원 |
| 221 | 지수선물 실시간체결가 | 실시간-010 | WS | `/tryitout/H0IFCNT0` | H0IFCNT0 | 모의투자 미지원 | 미지원 |
| 222 | 주식선물 실시간예상체결 | 실시간-031 | WS | `/tryitout/H0ZFANC0` | H0ZFANC0 | 모의투자 미지원 | 미지원 |
| 223 | KRX야간옵션실시간체결통보 | 실시간-067 | WS | `/tryitout/H0EUCNI0` | H0MFCNI0 | 모의투자 미지원 | 미지원 |
| 224 | KRX야간선물 실시간체결통보 | 실시간-066 | WS | `/tryitout/H0MFCNI0` | H0MFCNI0 | 모의투자 미지원 | 미지원 |
| 225 | 상품선물 실시간체결가 | 실시간-022 | WS | `/tryitout/H0CFCNT0` | H0CFCNT0 | 모의투자 미지원 | 미지원 |
| 226 | 지수선물 실시간호가 | 실시간-011 | WS | `/tryitout/H0IFASP0` | H0IFASP0 | 모의투자 미지원 | 미지원 |
| 227 | 지수옵션  실시간체결가 | 실시간-014 | WS | `/tryitout/H0IOCNT0` | H0IOCNT0 | 모의투자 미지원 | 미지원 |
| 228 | KRX야간옵션 실시간호가 | 실시간-033 | WS | `/tryitout/H0EUASP0` | H0EUASP0 | 모의투자 미지원 | 미지원 |
| 229 | 상품선물 실시간호가 | 실시간-023 | WS | `/tryitout/H0CFASP0` | H0CFASP0 | 모의투자 미지원 | 미지원 |
| 230 | 주식옵션 실시간예상체결 | 실시간-046 | WS | `/tryitout/H0ZOANC0` | H0ZOANC0 | 모의투자 미지원 | 미지원 |
| 231 | 주식선물 실시간호가 | 실시간-030 | WS | `/tryitout/H0ZFASP0` | H0ZFASP0 | 모의투자 미지원 | 미지원 |
| 232 | 주식옵션 실시간체결가 | 실시간-044 | WS | `/tryitout/H0ZOCNT0` | H0ZOCNT0 | 모의투자 미지원 | 미지원 |
| 233 | 지수옵션 실시간호가 | 실시간-015 | WS | `/tryitout/H0IOASP0` | H0IOASP0 | 모의투자 미지원 | 미지원 |
| 234 | 주식선물 실시간체결가 | 실시간-029 | WS | `/tryitout/H0ZFCNT0` | H0ZFCNT0 | 모의투자 미지원 | 미지원 |

## [해외주식] 주문/계좌 (18개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 235 | 해외주식 잔고 | v1_해외주식-006 | GET | `/uapi/overseas-stock/v1/trading/inquire-balance` | TTTS3012R | VTTS3012R | 지원(모의 TR 분리) |
| 236 | 해외주식 체결기준현재잔고 | v1_해외주식-008 | GET | `/uapi/overseas-stock/v1/trading/inquire-present-balance` | CTRP6504R | VTRP6504R | 지원(모의 TR 분리) |
| 237 | 해외주식 지정가체결내역조회 | 해외주식-070 | GET | `/uapi/overseas-stock/v1/trading/inquire-algo-ccnl` | TTTS6059R | 모의투자 미지원 | 미지원 |
| 238 | 해외주식 기간손익 | v1_해외주식-032 | GET | `/uapi/overseas-stock/v1/trading/inquire-period-profit` | TTTS3039R | 모의투자 미지원 | 미지원 |
| 239 | 해외주식 매수가능금액조회 | v1_해외주식-014 | GET | `/uapi/overseas-stock/v1/trading/inquire-psamount` | TTTS3007R | VTTS3007R | 지원(모의 TR 분리) |
| 240 | 해외주식 정정취소주문 | v1_해외주식-003 | POST | `/uapi/overseas-stock/v1/trading/order-rvsecncl` | (미국 정정·취소) TTTT1004U (아시아 국가 하단 규격서 참고) | (미국 정정·취소) VTTT1004U (아시아 국가 하단 규격서 참고) | 지원(모의 TR 분리) |
| 241 | 해외주식 예약주문접수 | v1_해외주식-002 | POST | `/uapi/overseas-stock/v1/trading/order-resv` | (미국예약매수) TTTT3014U  (미국예약매도) TTTT3016U   (중국/홍콩/일본/베트남 예약주문) TTTS3013U | (미국예약매수) VTTT3014U  (미국예약매도) VTTT3016U   (중국/홍콩/일본/베트남 예약주문) VTTS3013U | 지원(모의 TR 분리) |
| 242 | 해외주식 미체결내역 | v1_해외주식-005 | GET | `/uapi/overseas-stock/v1/trading/inquire-nccs` | TTTS3018R | 모의투자 미지원 | 미지원 |
| 243 | 해외주식 미국주간정정취소 | v1_해외주식-027 | POST | `/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl` | TTTS6038U | 모의투자 미지원 | 미지원 |
| 244 | 해외주식 주문체결내역 | v1_해외주식-007 | GET | `/uapi/overseas-stock/v1/trading/inquire-ccnl` | TTTS3035R | VTTS3035R | 지원(모의 TR 분리) |
| 245 | 해외주식 결제기준잔고 | 해외주식-064 | GET | `/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance` | CTRP6010R | 모의투자 미지원 | 미지원 |
| 246 | 해외주식 일별거래내역 | 해외주식-063 | GET | `/uapi/overseas-stock/v1/trading/inquire-period-trans` | CTOS4001R | 모의투자 미지원 | 미지원 |
| 247 | 해외주식 미국주간주문 | v1_해외주식-026 | POST | `/uapi/overseas-stock/v1/trading/daytime-order` | (주간매수) TTTS6036U (주간매도) TTTS6037U | 모의투자 미지원 | 미지원 |
| 248 | 해외주식 예약주문조회 | v1_해외주식-013 | GET | `/uapi/overseas-stock/v1/trading/order-resv-list` | (미국) TTTT3039R (일본/중국/홍콩/베트남) TTTS3014R | 모의투자 미지원 | 미지원 |
| 249 | 해외주식 주문 | v1_해외주식-001 | POST | `/uapi/overseas-stock/v1/trading/order` | (미국매수) TTTT1002U  (미국매도) TTTT1006U (아시아 국가 하단 규격서 참고) | (미국매수) VTTT1002U  (미국매도) VTTT1001U  (아시아 국가 하단 규격서 참고) | 지원(모의 TR 분리) |
| 250 | 해외주식 예약주문접수취소 | v1_해외주식-004 | POST | `/uapi/overseas-stock/v1/trading/order-resv-ccnl` | (미국 예약주문 취소접수) TTTT3017U (아시아국가 미제공) | (미국 예약주문 취소접수) VTTT3017U (아시아국가 미제공) | 지원(모의 TR 분리) |
| 251 | 해외주식 지정가주문번호조회 | 해외주식-071 | GET | `/uapi/overseas-stock/v1/trading/algo-ordno` | TTTS6058R | 모의투자 미지원 | 미지원 |
| 252 | 해외증거금 통화별조회 | 해외주식-035 | GET | `/uapi/overseas-stock/v1/trading/foreign-margin` | TTTC2101R | 모의투자 미지원 | 미지원 |

## [해외주식] 기본시세 (14개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 253 | 해외주식 체결추이 | 해외주식-037 | GET | `/uapi/overseas-price/v1/quotations/inquire-ccnl` | HHDFS76200300 | 모의투자 미지원 | 미지원 |
| 254 | 해외주식 기간별시세 | v1_해외주식-010 | GET | `/uapi/overseas-price/v1/quotations/dailyprice` | HHDFS76240000 | HHDFS76240000 | 지원(동일 TR) |
| 255 | 해외결제일자조회 | 해외주식-017 | GET | `/uapi/overseas-stock/v1/quotations/countries-holiday` | CTOS5011R | 모의투자 미지원 | 미지원 |
| 256 | 해외주식 현재체결가 | v1_해외주식-009 | GET | `/uapi/overseas-price/v1/quotations/price` | HHDFS00000300 | HHDFS00000300 | 지원(동일 TR) |
| 257 | 해외주식 복수종목 시세조회 | 해외주식 복수종목 시세조회 | GET | `/uapi/overseas-price/v1/quotations/multprice` | HHDFS76220000 | 미지원 | 미지원 |
| 258 | 해외주식조건검색 | v1_해외주식-015 | GET | `/uapi/overseas-price/v1/quotations/inquire-search` | HHDFS76410000 | HHDFS76410000 | 지원(동일 TR) |
| 259 | 해외주식 상품기본정보 | v1_해외주식-034 | GET | `/uapi/overseas-price/v1/quotations/search-info` | CTPF1702R | 모의투자 미지원 | 미지원 |
| 260 | 해외지수분봉조회 | v1_해외주식-031 | GET | `/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice` | FHKST03030200 | 모의투자 미지원 | 미지원 |
| 261 | 해외주식분봉조회 | v1_해외주식-030 | GET | `/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice` | HHDFS76950200 | 모의투자 미지원 | 미지원 |
| 262 | 해외주식 현재가상세 | v1_해외주식-029 | GET | `/uapi/overseas-price/v1/quotations/price-detail` | HHDFS76200200 | 모의투자 미지원 | 미지원 |
| 263 | 해외주식 업종별코드조회 | 해외주식-049 | GET | `/uapi/overseas-price/v1/quotations/industry-price` | HHDFS76370100 | 모의투자 미지원 | 미지원 |
| 264 | 해외주식 종목/지수/환율기간별시세(일/주/월/년) | v1_해외주식-012 | GET | `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` | FHKST03030100 | FHKST03030100 | 지원(동일 TR) |
| 265 | 해외주식 업종별시세 | 해외주식-048 | GET | `/uapi/overseas-price/v1/quotations/industry-theme` | HHDFS76370000 | 모의투자 미지원 | 미지원 |
| 266 | 해외주식 현재가 호가 | 해외주식-033 | GET | `/uapi/overseas-price/v1/quotations/inquire-asking-price` | HHDFS76200100 | 모의투자 미지원 | 미지원 |

## [해외주식] 시세분석 (15개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 267 | 해외주식 거래증가율순위 | 해외주식-045 | GET | `/uapi/overseas-stock/v1/ranking/trade-growth` | HHDFS76330000 | 모의투자 미지원 | 미지원 |
| 268 | 해외주식 기간별권리조회 | 해외주식-052 | GET | `/uapi/overseas-price/v1/quotations/period-rights` | CTRGT011R | 모의투자 미지원 | 미지원 |
| 269 | 해외주식 가격급등락 | 해외주식-038 | GET | `/uapi/overseas-stock/v1/ranking/price-fluct` | HHDFS76260000 | 모의투자 미지원 | 미지원 |
| 270 | 해외주식 거래대금순위 | 해외주식-044 | GET | `/uapi/overseas-stock/v1/ranking/trade-pbmn` | HHDFS76320010 | 모의투자 미지원 | 미지원 |
| 271 | 해외주식 거래량급증 | 해외주식-039 | GET | `/uapi/overseas-stock/v1/ranking/volume-surge` | HHDFS76270000 | 모의투자 미지원 | 미지원 |
| 272 | 해외주식 신고/신저가 | 해외주식-042 | GET | `/uapi/overseas-stock/v1/ranking/new-highlow` | HHDFS76300000 | 모의투자 미지원 | 미지원 |
| 273 | 해외주식 매수체결강도상위 | 해외주식-040 | GET | `/uapi/overseas-stock/v1/ranking/volume-power` | HHDFS76280000 | 모의투자 미지원 | 미지원 |
| 274 | 해외주식 거래회전율순위 | 해외주식-046 | GET | `/uapi/overseas-stock/v1/ranking/trade-turnover` | HHDFS76340000 | 모의투자 미지원 | 미지원 |
| 275 | 해외뉴스종합(제목) | 해외주식-053 | GET | `/uapi/overseas-price/v1/quotations/news-title` | HHPSTH60100C1 | 모의투자 미지원 | 미지원 |
| 276 | 당사 해외주식담보대출 가능 종목 | 해외주식-051 | GET | `/uapi/overseas-price/v1/quotations/colable-by-company` | CTLN4050R | 모의투자 미지원 | 미지원 |
| 277 | 해외주식 시가총액순위 | 해외주식-047 | GET | `/uapi/overseas-stock/v1/ranking/market-cap` | HHDFS76350100 | 모의투자 미지원 | 미지원 |
| 278 | 해외속보(제목) | 해외주식-055 | GET | `/uapi/overseas-price/v1/quotations/brknews-title` | FHKST01011801 | 모의투자 미지원 | 미지원 |
| 279 | 해외주식 상승율/하락율 | 해외주식-041 | GET | `/uapi/overseas-stock/v1/ranking/updown-rate` | HHDFS76290000 | 모의투자 미지원 | 미지원 |
| 280 | 해외주식 권리종합 | 해외주식-050 | GET | `/uapi/overseas-price/v1/quotations/rights-by-ice` | HHDFS78330900 | 모의투자 미지원 | 미지원 |
| 281 | 해외주식 거래량순위 | 해외주식-043 | GET | `/uapi/overseas-stock/v1/ranking/trade-vol` | HHDFS76310010 | 모의투자 미지원 | 미지원 |

## [해외주식] 실시간시세 (4개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 282 | 해외주식 실시간호가 | 실시간-021 | WS | `/tryitout/HDFSASP0` | HDFSASP0 | 모의투자 미지원 | 미지원 |
| 283 | 해외주식 지연호가(아시아) | 실시간-008 | WS | `/tryitout/HDFSASP1` | HDFSASP1 | 모의투자 미지원 | 미지원 |
| 284 | 해외주식 실시간지연체결가 | 실시간-007 | WS | `/tryitout/HDFSCNT0` | HDFSCNT0 | 모의투자 미지원 | 미지원 |
| 285 | 해외주식 실시간체결통보 | 실시간-009 | WS | `/tryitout/H0GSCNI0` | H0GSCNI0 | H0GSCNI9 | 지원(모의 TR 분리) |

## [해외선물옵션] 주문/계좌 (11개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 286 | 해외선물옵션 주문 | v1_해외선물-001 | POST | `/uapi/overseas-futureoption/v1/trading/order` | OTFM3001U | 모의투자 미지원 | 미지원 |
| 287 | 해외선물옵션 정정취소주문 | v1_해외선물-002, 003 | POST | `/uapi/overseas-futureoption/v1/trading/order-rvsecncl` | (정정) OTFM3002U (취소) OTFM3003U | 모의투자 미지원 | 미지원 |
| 288 | 해외선물옵션 당일주문내역조회 | v1_해외선물-004 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-ccld` | OTFM3116R | 모의투자 미지원 | 미지원 |
| 289 | 해외선물옵션 미결제내역조회(잔고) | v1_해외선물-005 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-unpd` | OTFM1412R | 모의투자 미지원 | 미지원 |
| 290 | 해외선물옵션 주문가능조회 | v1_해외선물-006 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-psamount` | OTFM3304R | 모의투자 미지원 | 미지원 |
| 291 | 해외선물옵션 기간계좌손익 일별 | 해외선물-010 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-period-ccld` | OTFM3118R | 모의투자 미지원 | 미지원 |
| 292 | 해외선물옵션 일별 체결내역 | 해외선물-011 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-daily-ccld` | OTFM3122R | 모의투자 미지원 | 미지원 |
| 293 | 해외선물옵션 예수금현황 | 해외선물-012 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-deposit` | OTFM1411R | 모의투자 미지원 | 미지원 |
| 294 | 해외선물옵션 일별 주문내역 | 해외선물-013 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-daily-order` | OTFM3120R | 모의투자 미지원 | 미지원 |
| 295 | 해외선물옵션 기간계좌거래내역 | 해외선물-014 | GET | `/uapi/overseas-futureoption/v1/trading/inquire-period-trans` | OTFM3114R | 모의투자 미지원 | 미지원 |
| 296 | 해외선물옵션 증거금상세 | 해외선물-032 | GET | `/uapi/overseas-futureoption/v1/trading/margin-detail` | OTFM3115R | 모의투자 미지원 | 미지원 |

## [해외선물옵션] 기본시세 (20개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 297 | 해외선물종목현재가 | v1_해외선물-009 | GET | `/uapi/overseas-futureoption/v1/quotations/inquire-price` | HHDFC55010000 | 모의투자 미지원 | 미지원 |
| 298 | 해외선물종목상세 | v1_해외선물-008 | GET | `/uapi/overseas-futureoption/v1/quotations/stock-detail` | HHDFC55010100 | 모의투자 미지원 | 미지원 |
| 299 | 해외선물 호가 | 해외선물-031 | GET | `/uapi/overseas-futureoption/v1/quotations/inquire-asking-price` | HHDFC86000000 | 모의투자 미지원 | 미지원 |
| 300 | 해외선물 분봉조회 | 해외선물-016 | GET | `/uapi/overseas-futureoption/v1/quotations/inquire-time-futurechartprice` | HHDFC55020400 | 모의투자 미지원 | 미지원 |
| 301 | 해외선물 체결추이(틱) | 해외선물-019 | GET | `/uapi/overseas-futureoption/v1/quotations/tick-ccnl` | HHDFC55020200 | 모의투자 미지원 | 미지원 |
| 302 | 해외선물 체결추이(주간) | 해외선물-017 | GET | `/uapi/overseas-futureoption/v1/quotations/weekly-ccnl` | HHDFC55020000 | 모의투자 미지원 | 미지원 |
| 303 | 해외선물 체결추이(일간) | 해외선물-018 | GET | `/uapi/overseas-futureoption/v1/quotations/daily-ccnl` | HHDFC55020100 | 모의투자 미지원 | 미지원 |
| 304 | 해외선물 체결추이(월간) | 해외선물-020 | GET | `/uapi/overseas-futureoption/v1/quotations/monthly-ccnl` | HHDFC55020300 | 모의투자 미지원 | 미지원 |
| 305 | 해외선물 상품기본정보 | 해외선물-023 | GET | `/uapi/overseas-futureoption/v1/quotations/search-contract-detail` | HHDFC55200000 | 모의투자 미지원 | 미지원 |
| 306 | 해외선물 미결제추이 | 해외선물-029 | GET | `/uapi/overseas-futureoption/v1/quotations/investor-unpd-trend` | HHDDB95030000 | 모의투자 미지원 | 미지원 |
| 307 | 해외옵션종목현재가 | 해외선물-035 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-price` | HHDFO55010000 | 모의투자 미지원 | 미지원 |
| 308 | 해외옵션종목상세 | 해외선물-034 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-detail` | HHDFO55010100 | 모의투자 미지원 | 미지원 |
| 309 | 해외옵션 호가 | 해외선물-033 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-asking-price` | HHDFO86000000 | 모의투자 미지원 | 미지원 |
| 310 | 해외옵션 분봉조회 | 해외선물-040 | GET | `/uapi/overseas-futureoption/v1/quotations/inquire-time-optchartprice` | HHDFO55020400 | 모의투자 미지원 | 미지원 |
| 311 | 해외옵션 체결추이(틱) | 해외선물-038 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-tick-ccnl` | HHDFO55020200 | 모의투자 미지원 | 미지원 |
| 312 | 해외옵션 체결추이(일간) | 해외선물-037 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-daily-ccnl` | HHDFO55020100 | 모의투자 미지원 | 미지원 |
| 313 | 해외옵션 체결추이(주간) | 해외선물-036 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-weekly-ccnl` | HHDFO55020000 | 모의투자 미지원 | 미지원 |
| 314 | 해외옵션 체결추이(월간) | 해외선물-039 | GET | `/uapi/overseas-futureoption/v1/quotations/opt-monthly-ccnl` | HHDFO55020300 | 모의투자 미지원 | 미지원 |
| 315 | 해외옵션 상품기본정보 | 해외선물-041 | GET | `/uapi/overseas-futureoption/v1/quotations/search-opt-detail` | HHDFO55200000 | 모의투자 미지원 | 미지원 |
| 316 | 해외선물옵션 장운영시간 | 해외선물-030 | GET | `/uapi/overseas-futureoption/v1/quotations/market-time` | OTFM2229R | 모의투자 미지원 | 미지원 |

## [해외선물옵션]실시간시세 (4개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 317 | 해외선물옵션 실시간체결가 | 실시간-017 | WS | `/tryitout/HDFFF020` | HDFFF020 | 모의투자 미지원 | 미지원 |
| 318 | 해외선물옵션 실시간호가 | 실시간-018 | WS | `/tryitout/HDFFF010` | HDFFF010 | 모의투자 미지원 | 미지원 |
| 319 | 해외선물옵션 실시간주문내역통보 | 실시간-019 | WS | `/tryitout/HDFFF1C0` | HDFFF1C0 | 모의투자 미지원 | 미지원 |
| 320 | 해외선물옵션 실시간체결내역통보 | 실시간-020 | WS | `/tryitout/HDFFF2C0` | HDFFF2C0 | 모의투자 미지원 | 미지원 |

## [장내채권] 주문/계좌 (7개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 321 | 장내채권 매수주문 | 국내주식-124 | POST | `/uapi/domestic-bond/v1/trading/buy` | TTTC0952U | 모의투자 미지원 | 미지원 |
| 322 | 장내채권 매도주문 | 국내주식-123 | POST | `/uapi/domestic-bond/v1/trading/sell` | TTTC0958U | 모의투자 미지원 | 미지원 |
| 323 | 장내채권 정정취소주문 | 국내주식-125 | POST | `/uapi/domestic-bond/v1/trading/order-rvsecncl` | TTTC0953U | 모의투자 미지원 | 미지원 |
| 324 | 채권정정취소가능주문조회 | 국내주식-126 | GET | `/uapi/domestic-bond/v1/trading/inquire-psbl-rvsecncl` | CTSC8035R | 모의투자 미지원 | 미지원 |
| 325 | 장내채권 주문체결내역 | 국내주식-127 | GET | `/uapi/domestic-bond/v1/trading/inquire-daily-ccld` | CTSC8013R | 모의투자 미지원 | 미지원 |
| 326 | 장내채권 잔고조회 | 국내주식-198 | GET | `/uapi/domestic-bond/v1/trading/inquire-balance` | CTSC8407R | 모의투자 미지원 | 미지원 |
| 327 | 장내채권 매수가능조회 | 국내주식-199 | GET | `/uapi/domestic-bond/v1/trading/inquire-psbl-order` | TTTC8910R | 모의투자 미지원 | 미지원 |

## [장내채권] 기본시세 (8개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 328 | 장내채권현재가(호가) | 국내주식-132 | GET | `/uapi/domestic-bond/v1/quotations/inquire-asking-price` | FHKBJ773401C0 | 모의투자 미지원 | 미지원 |
| 329 | 장내채권현재가(시세) | 국내주식-200 | GET | `/uapi/domestic-bond/v1/quotations/inquire-price` | FHKBJ773400C0 | 모의투자 미지원 | 미지원 |
| 330 | 장내채권현재가(체결) | 국내주식-201 | GET | `/uapi/domestic-bond/v1/quotations/inquire-ccnl` | FHKBJ773403C0 | 모의투자 미지원 | 미지원 |
| 331 | 장내채권현재가(일별) | 국내주식-202 | GET | `/uapi/domestic-bond/v1/quotations/inquire-daily-price` | FHKBJ773404C0 | 모의투자 미지원 | 미지원 |
| 332 | 장내채권 기간별시세(일) | 국내주식-159 | GET | `/uapi/domestic-bond/v1/quotations/inquire-daily-itemchartprice` | FHKBJ773701C0 | 모의투자 미지원 | 미지원 |
| 333 | 장내채권 평균단가조회 | 국내주식-158 | GET | `/uapi/domestic-bond/v1/quotations/avg-unit` | CTPF2005R | 모의투자 미지원 | 미지원 |
| 334 | 장내채권 발행정보 | 국내주식-156 | GET | `/uapi/domestic-bond/v1/quotations/issue-info` | CTPF1101R | 모의투자 미지원 | 미지원 |
| 335 | 장내채권 기본조회 | 국내주식-129 | GET | `/uapi/domestic-bond/v1/quotations/search-bond-info` | CTPF1114R | 모의투자 미지원 | 미지원 |

## [장내채권] 실시간시세 (3개)

| 순번 | API 명 | API ID | Method | URL | 실전 TR_ID | 모의 TR_ID | 모의 지원 |
|---|---|---|---|---|---|---|---|
| 336 | 일반채권 실시간체결가 | 실시간-052 | WS | `/tryitout/H0BJCNT0` | H0BJCNT0 | 모의투자 미지원 | 미지원 |
| 337 | 일반채권 실시간호가 | 실시간-053 | WS | `/tryitout/H0BJASP0` | H0BJCNT0 | 모의투자 미지원 | 미지원 |
| 338 | 채권지수 실시간체결가 | 실시간-060 | WS | `/tryitout/H0BICNT0` | H0BICNT0 | 모의투자 미지원 | 미지원 |

## 부록 A. Market Calendar/Event Aggregator 후보 태그 (스크립트 관리)

API 명세서 12A(계획)의 수집 후보를 URL 기준으로 태그한다. 본문 수치/분류에는 영향이 없으며, 선정 기준은 `scripts/generate_kis_api_catalog.py`의 `CALENDAR_EVENT_CANDIDATES` 상수로만 관리한다. 아래 항목은 전부 모의투자 미지원이므로 최종_프로젝트_명세서 12.5의 live read-only 경계에서만 호출할 수 있다.

| 순번 | API 명 | URL | 이벤트 매핑 | 비고 |
|---|---|---|---|---|
| 76 | 국내휴장일조회 | `/uapi/domestic-stock/v1/quotations/chk-holiday` | TradingSession(XKRX) | 1일 1회 이하 보수 호출(공식 예제 주의사항) |
| 79 | 국내선물 영업일조회 | `/uapi/domestic-stock/v1/quotations/market-time` | TradingSession 보조(영업일) | 선물 영업일 관점 교차 검증용 |
| 84 | 종합 시황/공시(제목) | `/uapi/domestic-stock/v1/quotations/news-title` | DISCLOSURE(제목) | 제목/메타만 저장, 본문 저장 금지 |
| 95 | 예탁원정보(배당일정) | `/uapi/domestic-stock/v1/ksdinfo/dividend` | DIVIDEND_RECORD/DIVIDEND_PAY | record_date/divi_pay_dt 제공 |
| 104 | 예탁원정보(유상증자일정) | `/uapi/domestic-stock/v1/ksdinfo/paidin-capin` | RIGHTS_ISSUE | 유상증자 일정 |
| 90 | 예탁원정보(무상증자일정) | `/uapi/domestic-stock/v1/ksdinfo/bonus-issue` | BONUS_ISSUE | 무상증자 일정 |
| 108 | 예탁원정보(합병/분할일정) | `/uapi/domestic-stock/v1/ksdinfo/merger-split` | MERGER_SPLIT | 합병/분할 일정 |
| 94 | 예탁원정보(액면교체일정) | `/uapi/domestic-stock/v1/ksdinfo/rev-split` | SPLIT | 액면교체 일정 |
| 89 | 예탁원정보(자본감소일정) | `/uapi/domestic-stock/v1/ksdinfo/cap-dcrs` | CAPITAL_REDUCTION | 자본감소 일정 |
| 86 | 예탁원정보(상장정보일정) | `/uapi/domestic-stock/v1/ksdinfo/list-info` | IPO_LISTING | 상장정보 일정 |
| 87 | 예탁원정보(공모주청약일정) | `/uapi/domestic-stock/v1/ksdinfo/pub-offer` | IPO_SUBSCRIPTION | 공모주 청약 일정 |
| 105 | 예탁원정보(주주총회일정) | `/uapi/domestic-stock/v1/ksdinfo/sharehld-meet` | SHAREHOLDER_MEETING | 주주총회 일정 |
| 93 | 예탁원정보(주식매수청구일정) | `/uapi/domestic-stock/v1/ksdinfo/purreq` | MERGER_SPLIT 보조 | 주식매수청구 일정 |
| 99 | 예탁원정보(실권주일정) | `/uapi/domestic-stock/v1/ksdinfo/forfeit` | RIGHTS_ISSUE 보조 | 실권주 일정 |
| 100 | 예탁원정보(의무예치일정) | `/uapi/domestic-stock/v1/ksdinfo/mand-deposit` | 참고 | 의무예치 일정 |
| 109 | 국내주식 종목추정실적 | `/uapi/domestic-stock/v1/quotations/estimate-perform` | EARNINGS_EXPECTED 보조 | 추정실적 — 확정 아님, TENTATIVE 유지 |
| 255 | 해외결제일자조회 | `/uapi/overseas-stock/v1/quotations/countries-holiday` | 해외 결제일/휴장 참고 | TradingSession 보조 교차 검증 |
| 275 | 해외뉴스종합(제목) | `/uapi/overseas-price/v1/quotations/news-title` | 해외 DISCLOSURE(제목) | 제목/메타만 저장 |
