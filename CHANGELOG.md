# 변경 이력

이 문서는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따릅니다. 버전 하나가 GitHub Release `vX.Y.Z` 하나와 Docker Hub 이미지 태그 `vX.Y.Z-<part>` 한 벌에 대응합니다.

## [1.0.0] - 2026-09-26

첫 정식 버전입니다.

### 추가

- 아이디·이메일 비밀번호 로그인과 최소 회원가입(정규화 이메일과 BCrypt 해시만 저장)
- 로그인 후 설정에서 Google·Kakao 계정을 직접 연결·해제. 이메일로 자동 병합하지 않음
- 개인 서버 배포 오버레이 `deploy/p1/compose.server.yml`(TLS 443/80, 재시작 정책)
- 제출용 README(보고서 8장 구조)와 변경 이력, 버전 관리 규칙

### 변경

- 릴리스 태그를 `v<버전>-<커밋>`에서 `v<버전>`으로 바꾸고, 같은 버전의 재발행을 막음
- 기여 규약을 `CONTRIBUTING.md`로 옮기고 커밋 위생 검사를 일반 규칙으로 정리

[1.0.0]: https://github.com/robinhood0107/Capstone-AI-Trading-Coach/releases/tag/v1.0.0
