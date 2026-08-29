# 두 팀 결과를 받은 뒤 통합 확인표

## 팀에 요청서를 보내기 전에 통합 담당자가 할 일

- [ ] 통합 PR이 main에 병합됐고 팀원이 최신 main을 받을 수 있음
- [ ] Team B에 전달할 KIS 가격·ECOS 거시 입력 manifest와 파일 해시를 고정함
- [ ] Team B 세 비용 시나리오의 수수료·세금·slippage 값을 고정함
- [ ] Team B 결과 manifest의 producer SHA-256 계산 대상을 문서로 고정함
- [ ] 자동매매 예약 API는 아직 없다고 Team A에 알리고 임의 endpoint 생성을 금지함
- [ ] Team A/B는 외부 서비스, 계좌와 주문 인증을 실행하지 않는다고 확인함

위 Team B 입력 계약 세 항목이 없으면 실제 Team B 결과를 요구할 수 없습니다. 먼저
`OWNER_INPUT_MISSING`을 해소합니다.

## 1. 받은 내용과 해시 기록

- [ ] Team A PR, commit SHA, `package-lock.json` SHA-256 기록
- [ ] Team B PR, commit SHA, `uv.lock`, 입력 manifest, 결과 manifest SHA-256 기록
- [ ] secret, symlink, 개인 절대경로, 외부 서비스 원본 응답이 포함되지 않았는지 확인
- [ ] 받은 CSV/PTH/legacy JSON 미리보기와 새 Team B 실제 결과를 구분
- [ ] Team A의 API 27개 성공 표와 Playwright `skip 0` 증거 확인
- [ ] Team B의 두 번 실행 SHA-256 비교표 확인

## 2. 빈 폴더에서 다시 실행

기존 작업 폴더가 아닌 새 폴더에 clone해서 검증합니다.

```bash
git clone <repository-url> Capstone-AI-Trading-Coach-clean
cd Capstone-AI-Trading-Coach-clean
git switch main
git pull --ff-only origin main
./capstone doctor
./capstone up
./capstone smoke
```

- [ ] Dashboard image가 `npm ci`로 새로 build됨
- [ ] Return Engine image가 `uv sync --frozen`으로 새로 build됨
- [ ] 역할 준비, DB migration, 공개 Seed, 사용자 준비, Dashboard Seed, Team B preview 준비가 모두 exit 0
- [ ] 장기 컨테이너가 PostgreSQL, Redis, 권한 서비스, 통합 백엔드, Dashboard의 정확히 5개임
- [ ] `./capstone up --models`에서는 BGE-M3, PaddleOCR-VL이 추가된 정확히 7개임
- [ ] 종료된 준비 컨테이너가 남지 않음
- [ ] `./capstone down` 뒤에도 DB·Redis·모델 volume이 보존됨

## 3. Team B 실제 결과 검증

- [ ] 입력 manifest가 KIS 가격, ECOS 거시, 조건부 뉴스 입력을 각각 해시로 묶음
- [ ] 고정된 결과 파일 10개의 이름, 크기, SHA-256 확인
- [ ] `p1-return-engine-artifact-manifest.v1` schema와 `REAL_TEAM_B` 확인
- [ ] 동일한 network-disabled Docker 작업 두 번의 11개 파일 SHA-256이 모두 같음
- [ ] 거래·자산 로그로 수익률, Sharpe, MDD, 거래 수를 독립 재계산
- [ ] 세 비용 시나리오에 승인된 수수료·세금·slippage가 반영됨
- [ ] 다음 XKRX session과 예상 수익률 공식을 독립 계산
- [ ] Team B가 Spring REST, KIS, ECOS, yfinance와 주문 API를 호출하지 않았음

Team B 결과를 Spring/Dashboard 데이터로 바꾸는 production adapter는 **이미 있습니다.** V88의
`import_p1_return_bundle_v1`이 그 어댑터이고, V90/V91 런타임이 그 신호를 자동운용으로 잇습니다.
아래 네 항목은 구현 대기가 아니라 실물 번들을 받았을 때 **확인할 목록**입니다. 확인 방법은
`workspaces/decision-platform/python-services/tests/e2e/full_pipeline_e2e.py`가 계약 형태의
번들로 같은 경로를 한 번 관통시킨 것과 같습니다.

- [ ] Team B manifest와 10개 파일을 검증한 뒤에만 atomic ingest (`validate_artifact_bundle`)
- [ ] Team B 신호를 Signal v2 형식으로 변환하되 주문 권한은 부여하지 않음
      (`p1_return_signal_projection` → `read_p1_return_signal_v2`)
- [ ] 모델 평가와 백테스트 ViewModel을 실제 runId로 생성
- [ ] 아래 네 API에서 같은 run과 source hash가 보임

```text
GET /api/v2/signals/{symbol}
GET /api/v1/dashboard/model-evaluations/{runId}
GET /api/v1/dashboard/backtests/{runId}
GET /api/v1/artifacts/ingest-status
```

## 4. Team A 실제 화면 검증

- [ ] production image가 프론트 가짜 응답이 아닌 로컬 Spring 모드임
- [ ] 브라우저가 Dashboard와 같은 주소의 `/api/...`만 호출함
- [ ] 로그인 → 홈 → 원칙 → 주문 검토 → 모델 평가 → 백테스트 → RAG 흐름 통과
- [ ] 현재 15개와 명세상 추가 12개, 총 27개의 method/path/성공 상태가 OpenAPI와 일치
- [ ] 4xx와 5xx가 모두 E2E 실패로 처리되고 Playwright skip이 0개임
- [ ] “자동주문 작동 중”처럼 현재 사실과 다른 문구가 없음
- [ ] KIS 모의투자, 내부 가상거래, 백테스트가 화면에서 구분됨
- [ ] 선택 기능 8개와 운영자 API 6개를 필수 화면에 억지로 붙이지 않음
- [ ] OpenAPI에 없는 기능을 임의 구현하지 않고 `OWNER_API_MISSING`으로 제출함

## 5. KIS 모의투자 수동 인증

Team A/B 통합 검증은 외부 호출 0으로 끝냅니다. 별도로 통합 담당자만 KIS 모의투자 자격증명을
설정하고, XKRX 거래일 09:10~15:00 KST에 한 번 인증합니다.

```bash
./capstone up
./capstone mock configure
./capstone mock doctor
./capstone mock certify --symbol 005930 --quantity 1
```

- [ ] 인증 요청 해시, 인증 당시 Git tree, 현재 clean 상태가 다르면 `up --mock`이 거부됨
- [ ] 현재 HEAD, clean worktree, 원격 브랜치, 열린 PR, 필수 CI가 모두 일치
- [ ] 삼성전자 1주 지정가 모의 매수 후 즉시 전량취소
- [ ] 체결 0, 미체결 0, 주문 전후 잔고 digest 동일
- [ ] 실패·부분체결·취소 실패 시 자동 매도·재주문 없이 KIS 모의투자 화면 직접 확인
- [ ] 실계좌 origin과 실계좌 주문 TR ID 호출은 0

## 6. 자동매매를 구현할지 결정

최종 프로젝트 목적에는 자동 **모의투자**가 포함되므로 나중에는 주문 스케줄러가 필요합니다. 하지만
24시간 주문은 만들지 않습니다. 다음 조건이 모두 끝난 뒤 별도 계약과 PR로 구현합니다.

- [ ] Team B 실제 신호가 검증돼 Signal API에 반영됨
- [ ] Team A의 주문 전 판정, 주문 제출, 주문 상태·취소 화면이 완료됨
- [ ] KIS 모의투자 수동 인증이 PASS
- [ ] 거래일·거래시간, 활성/비활성, 전략, 일일 주문 상한을 저장하는 Owner API가 확정됨
- [ ] 시작 시 자동 활성화 금지, Kill Switch, 멱등성, write retry 0, 미확정 결과 대사가 확정됨
- [ ] 휴장일과 장외시간 테스트, 24시간 서비스 soak, 여러 거래일 모의 시뮬레이션이 통과함

## 7. GitHub와 최종 배포 순서

1. 각 팀 branch push와 PR 생성
2. PR 자동검사 통과
3. 통합 담당자 코드·문서·민감정보 검토
4. 통합 branch에서 단일 Compose와 전체 화면 E2E
5. KIS 모의투자 수동 인증 한 번
6. 통합 담당자가 main 병합
7. main 자동검사 재확인
8. 빈 폴더 clone과 `./capstone up` 재현
9. 별도 최종 배포 승인

Team B 실제 결과 10개가 오기 전에는 `TEAM_B_REAL_ARTIFACT_MISSING`을 유지합니다. 미리보기 성공,
synthetic 결과, 화면 캡처만으로 최종 배포를 선언하지 않습니다. 기본 실행과 Team A/B 검증의 외부 호출은
0이며, 별도 통합 담당자 인증에서만 KIS 모의투자 호출을 허용합니다. 실계좌 호출은 항상 0입니다.
