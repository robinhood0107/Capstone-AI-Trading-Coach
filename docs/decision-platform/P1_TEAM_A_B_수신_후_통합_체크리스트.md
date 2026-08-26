# P1 Team A/B 수신 후 통합 체크리스트

## Team A와 Team B가 모두 끝낸 뒤 owner가 할 일

### 1. 받은 내용 고정

- [ ] Team A commit SHA와 `package-lock.json` SHA-256 기록
- [ ] Team B commit SHA와 `uv.lock`, input manifest, artifact manifest SHA-256 기록
- [ ] secret, symlink, 개인 경로, provider raw response 여부 확인
- [ ] 현재 receipt CSV/PTH/legacy JSON과 새 `REAL_TEAM_B` bundle을 혼동하지 않음

### 2. clean 재현

```bash
git pull --ff-only
./capstone preview
```

- [ ] Dashboard image가 `npm ci`로 clean build됨
- [ ] Return Engine image가 `uv sync --frozen`으로 clean build됨
- [ ] DB migration, public Seed, identity bootstrap one-shot이 모두 exit 0
- [ ] PostgreSQL, Redis, Python, Spring, api-edge, Dashboard, Team B JSON 서버가 healthy
- [ ] volume 삭제 없이 재시작 후 같은 상태 재현

### 3. Team B 실물 독립 검증

- [ ] exact artifact 10개 basename/size/SHA-256 확인
- [ ] manifest schema와 `REAL_TEAM_B` evidence mode 확인
- [ ] 동일 one-shot 2회 재현성 확인
- [ ] trade/equity log로 수익률, Sharpe, MDD, 거래 수 독립 재계산
- [ ] BASELINE/GUIDE/STRICT 비용·세금·slippage 반영 확인
- [ ] Spring ingest 후 signal, model-evaluation, backtest, ingest-status API 확인

### 4. Team A live 화면 검증

- [ ] production image가 `NEXT_PUBLIC_API_MODE=live`
- [ ] 브라우저 요청은 same-origin `/api/...`만 사용
- [ ] 로그인 → 홈 → 원칙 → 주문 검토 → 모델 평가 → 백테스트 → RAG Playwright 통과
- [ ] 현재 15개와 추가 20개가 OpenAPI method/path와 일치
- [ ] 운영·대사 6개와 `/error` 7개를 억지로 제품 화면에 연결하지 않음
- [ ] `OWNER_API_MISSING` endpoint를 Team A가 임의 생성하지 않음

### 5. 최종 순서

1. 단일 `deploy/p1/compose.yml` E2E
2. 계약·unit·integration·Playwright·supply-chain CI
3. hard gate 9개 최종 판정
4. 구현 commit과 문서 commit 검토
5. owner가 merge 승인
6. merge 후 CI 재확인
7. owner가 push/release를 별도 승인

Team B exact 10개가 오기 전에는 `TEAM_B_REAL_ARTIFACT_MISSING`을 유지합니다. preview 성공, synthetic
artifact, 화면 스크린샷만으로 `FINAL` 또는 release를 선언하지 않습니다. provider·실계좌·실주문 호출은
항상 0입니다.
