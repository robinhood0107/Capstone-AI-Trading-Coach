# 팀에 요청하기 전 통합 담당자 확인표

## 현재 결론

Owner 코드 선행 작업은 완료됐고, 실제 input pack 물리 수집 승인만 남았습니다.

- Team A 요청 내용은 코드와 OpenAPI 기준으로 정리됐습니다.
- Team B 요청 내용과 입력 파일명은 고정됐지만, 실제 manifest SHA-256은 승인된 756-session 수집 뒤 채웁니다.
- confidence-free artifact v3, Git Seed auto import, V116 daily inference는 구현·focused 검증됐습니다.
- KIS 모의투자 수동 인증은 실행하지 않았습니다.
- 자동 주문 scheduler는 없으며 지금 추가하면 안 됩니다.

## 이미 끝낸 것

- 단일 `deploy/p1/compose.yml`과 기본 5개/모델 포함 7개 컨테이너 구조
- Spring과 Python worker를 하나의 통합 백엔드 컨테이너로 실행
- 공개 Seed DB의 migration·재현 흐름
- Team A production image, lockfile, `/healthz`, same-origin `/api` 기반 구조
- Team B 받은 CSV/PTH를 네트워크 없이 실행하는 미리보기
- 공식 BAAI BGE-M3 컨테이너와 공식 llama.cpp PaddleOCR-VL 컨테이너
- Linux/WSL `./capstone`과 Windows PowerShell `.\capstone.ps1`
- OpenAPI 48개 전수 분류와 Team A/Team B 요청서

## Team A에게 보내기 전에

- [ ] 통합 PR을 main에 병합하고 main 자동검사 통과 확인
- [ ] 새 폴더에서 `git pull` 후 `./capstone up` 재현
- [ ] Team A 요청서 링크가 main에서 열리는지 확인
- [ ] Team A는 15개 기존 연결 검증 + 12개 필수 추가, 총 27개만 요구한다고 확인
- [ ] 선택 기능 8개와 운영자 API 6개를 억지로 요구하지 않는다고 확인
- [ ] 자동매매 예약 API는 없으며 임의 생성하지 말라고 안내

## Team B에게 보내기 전에

- [ ] KIS 가격 입력 파일명, schema, 기간, 종목과 각 SHA-256을 manifest로 고정
- [ ] ECOS 입력 파일과 manifest를 고정
- [ ] 뉴스 감성을 사용할지 결정하고, 사용할 때만 승인된 계약 제공
- [ ] 세 비용 시나리오의 수수료, 세금, slippage 값을 고정
- [ ] 결과 manifest의 producer SHA-256 필드별 계산 대상을 고정
- [ ] 동일 실행 두 번은 11개 파일 SHA-256 완전 일치가 기준이라고 확인
- [ ] Team B 결과를 Spring/Dashboard로 바꾸는 adapter는 Owner 후속 작업이라고 안내

위 Team B 항목을 고정하기 전에는 Team B가 임의 입력·비용·해시를 만들게 해서는 안 됩니다.

## 두 팀 결과를 받은 뒤 Owner가 할 일

1. Team A exact-45 UI와 Team B manifest v3·exact-10을 독립 검증
2. `./capstone up`의 V116 Git Seed importer로 `REAL_TEAM_B` bundle 자동 적재
3. 단일 Compose·Team A acceptance·provider-free 관통 E2E·fresh checkout 재현
4. 장외 historical replay를 중간 증거로 확인
5. 별도 승인 후 KIS read-only·Vertex grounding·장중 KIS Mock 실행
6. 연속 3 XKRX session soak와 대사
7. 16개 release hard gate 모두 PASS 후만 1.0.0 승인

자동운용 V3 runtime과 adapter는 이미 구현됐으며 기본값은 `DISARMED`입니다. Team B 실제 신호,
readiness, certification과 account baseline이 없으면 활성화하지 않습니다. KIS 실계좌 주문은 계속 0입니다.

상세 순서는 [Team A·B 완료 후 Owner 최종 실행표](P1_TEAM_A_B_완료_후_OWNER_최종_실행.md)를 따릅니다.
