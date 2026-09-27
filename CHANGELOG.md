# 변경 이력

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따릅니다. 버전 하나가 GitHub Release `vX.Y.Z` 하나와 Docker Hub 이미지 태그 `vX.Y.Z-<part>` 한 벌에 대응합니다.

## [1.0.0] - 2026-09-27

첫 정식 버전입니다.

### 추가

- 아이디·이메일 비밀번호 로그인과 최소 회원가입(정규화 이메일과 BCrypt 해시만 저장)
- 로그인 후 설정에서 Google·Kakao 계정을 직접 연결·해제. 이메일로 자동 병합하지 않음
- 개인 서버 배포 오버레이 `deploy/p1/compose.server.yml`(TLS 443/80, 재시작 정책)
- 제출용 README(보고서 8장 구조)와 변경 이력, 버전 관리 규칙

- 사용자별 Vertex 키의 암호화 저장, 공용 키 허용 설정, 개인·공용 사용량 기록
- 읽기 전용 KIS 잔고 확인으로 FULL 자동운용 준비 상태를 증명하는 경로. 1주 모의주문 시험은 선택 사항
- 계좌별 자동운용 기록의 출처 보존, demo-user의 INTERNAL_PAPER 재생·Team A 수용 이력 분리, 미대사 KIS 주문의 시작 차단

### 변경

- 정책 저장·자동운용 시작·정지·예약 상태를 같은 계좌·정책 버전으로 대사

- 릴리스 태그를 `v<버전>-<커밋>`에서 `v<버전>`으로 바꾸고, 같은 버전의 재발행을 막음
- 기여 규약을 `CONTRIBUTING.md`로 옮기고 커밋 위생 검사를 일반 규칙으로 정리

## [1.0.1] - 2026-09-28

### 수정

- 소유자가 정리를 확인한 미대사 레거시 주문에 `LOCAL_RETIRED` 상태를 적용. 화면은 로컬 이력 종료와 KIS 결과 미확인을 분리해 보여 주며, 실제 KIS 취소가 확인된 `CANCELLED`와 구분. 관리자 대사는 해당 행에서 명확한 409로 멈추며 원 주문·감사 이력은 보존. demo-user는 자동 시작되지 않음

## [1.0.2] - 2026-09-28

### 추가

- 설정에서 이메일 계정의 비밀번호 변경. 현재 비밀번호를 확인하고, 바꾸면 다른 기기의 로그인을 모두 해제. demo-user는 운영자 서명 번들로만 회전

### 수정

- FULL에서 "자동운용 시작"이 "이 자료에 접근할 권한이 없습니다"로 거부되던 문제. 무장 응답이 같은 트랜잭션에서 자격증명 상태를 다시 읽을 때 스코프가 막혔음(V220)
- 전략 검증·보고서의 Sharpe 차이를 %가 아닌 소수로 표시하고, "수익률 대가"를 부호대로 읽히는 "CAGR 차이"로 바꿈

## [1.0.3] - 2026-09-28

### 추가

- NAS에서 Portainer 스택 하나로 FULL을 운영하는 템플릿과 안내(`deploy/p1/portainer/`). 이미지 버전·공개 주소·비밀 폴더·포트를 스택 변수로 두고, 기존 배포의 비밀 폴더와 DB 볼륨을 옮기는 스크립트를 포함

### 수정

- 느린 NAS 디스크에서 기동 시 Hibernate 스키마 검사가 `decision_app` statement_timeout(2s)을 넘겨 API가 unhealthy가 되던 문제. 스택에서 검사를 끄고 스키마는 Flyway가 맞춤. 시놀로지 커널이 거부하는 `cpus:` 제한 제거

## [1.0.4] - 2026-09-28

### 수정

- 시놀로지 NAS에서 API가 `BROKERAGE_KEK_UNAVAILABLE`로 기동하지 못하던 문제. 공유 폴더 ACL이 KEK 모드(0700/0600)를 깨뜨렸음. Portainer 스택은 KEK를 외부 볼륨 `mars-full_brokerage-kek`에 두고, `kek-permissions`가 매 기동마다 소유자·모드를 맞추며 키가 없으면 `KEK_MISSING`으로 먼저 멈춤
- 느린 NAS CPU에서 헬스체크가 앱보다 먼저 포기하던 문제. 기동 유예(start_period)를 넉넉히 두고 헬스체크 응답 제한을 10초로 늘림. 먼저 healthy가 되면 즉시 통과

## [1.0.5] - 2026-09-28

### 문서

- README에 산업체 자문 의견과 반영 사항, 최종 발표자료·자문의견서 링크, 참여 후기를 추가

[1.0.0]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.0
[1.0.1]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.1
[1.0.2]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.2
[1.0.3]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.3
[1.0.4]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.4
[1.0.5]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.5
