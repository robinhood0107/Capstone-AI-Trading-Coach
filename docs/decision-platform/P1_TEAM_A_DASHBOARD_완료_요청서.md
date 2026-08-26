# P1 Team A Dashboard 완료 요청서

## 팀원 A에게 한 문장으로 부탁할 내용

> 최신 `main`을 받은 뒤 `workspaces/experience-dashboard/` 안에서 실제 Dashboard를 완성하고,
> lockfile·Dockerfile·테스트까지 함께 PR로 올려 주세요. mock 화면만 보내거나 빌드 결과 폴더를
> 보내지 말고, 다른 사람이 `git pull` 후 같은 이미지를 다시 만들 수 있는 소스를 보내 주세요.

## 언제 전달하는 문서인가

이 요청서는 [우리 쪽 선행 체크리스트](P1_OWNER_선행_완료_체크리스트.md)가 `OWNER_HANDOFF_READY=TRUE`가
된 뒤 전달한다. 현재는 요청 내용과 수신 기준을 미리 고정한 문서이며, Team A 완료를 뜻하지 않는다.

## Team A가 시작하는 방법

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/p1-team-a-dashboard
```

작업 위치는 `workspaces/experience-dashboard/`이다. 공통 API 계약을 바꿔야 하면 먼저 변경 이유를
알리고, 임의로 `contracts/`, DB migration 또는 다른 팀 workspace를 수정하지 않는다.

## 해 달라고 할 일

1. `package-lock.json`처럼 정확한 dependency lockfile을 추가하고 `npm ci`가 성공하게 한다.
2. linux/amd64 production Dockerfile을 추가한다. container는 non-root로 실행하고 개발용 파일을
   production image에 넣지 않는다.
3. 브라우저는 same-origin API만 호출하게 한다. 내부 Docker 주소, API key, token 또는 secret을
   화면·소스·로그에 넣지 않는다.
4. 로그인 뒤 실제 인증 health를 다시 확인하고 token은 브라우저 영구 저장소에 남기지 않는다.
5. 아래 화면을 실제 Spring API 계약에 연결한다.
   - RAG 문서 업로드, 진행 상태, 삭제, embedding profile 변경
   - 사용자 생성, 비활성화, 비밀번호 reset
   - backup 생성·검증·다운로드와 restore 진행 상태
   - 종목 검색, 관심종목, 시세·일봉·ECOS, provider 상태
   - 모델 평가, 백테스트, Risk 결과, RAG 출처
6. `evidenceMode`, Kill Switch, 주문 상태를 서로 다른 뜻으로 표시한다. LightGBM production 화면은
   숨기고 `ABSTAIN/MISSING_EVIDENCE`를 정상적인 근거 부족 상태로 표시한다.
7. typecheck, lint, unit, API contract, Playwright, production build를 모두 통과시킨다.

백엔드 endpoint가 아직 없으면 mock으로 완료처럼 보이게 하지 말고 `현재 사용할 수 없음` 상태로
표시한다. Team A는 DB 파일이나 공개 Seed를 따로 만들 필요가 없다.

## PR에 반드시 들어갈 것

- `workspaces/experience-dashboard/`의 production source
- exact dependency lockfile
- Dockerfile과 필요한 runtime 설정 예시
- unit·contract·Playwright 테스트
- README의 한 번에 실행하는 명령과 테스트 결과
- 최종 image에 source map, `.env`, credential, mock, test, `dev/`, cache가 없다는 확인

`.next/`, `node_modules/`, 개인 `.env`, screenshot cache와 빌드 결과물은 커밋하지 않는다. zip 파일이나
개인 PC 경로 대신 PR commit으로 전달한다.

## Team A가 완료 여부를 확인하는 명령

정확한 script 이름은 lockfile과 함께 README에 적되, 최소한 아래 흐름이 새 checkout에서 성공해야 한다.

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

Playwright 명령과 Docker build/smoke 명령도 README에 추가한다. CI와 로컬 결과가 다르면 완료가 아니다.

## 우리가 PR을 받은 뒤 확인할 것

1. Team A가 기록한 commit과 lockfile hash를 확인한다.
2. production image를 다시 build하고 내부 URL·secret·mock 포함 여부를 검사한다.
3. API contract와 Playwright를 실행한다.
4. digest-pinned Dashboard image를 full Compose manifest에 결속한다.
5. 새 checkout에서 `./capstone doctor`, `./capstone install`, `./capstone start`를 다시 검증한다.

Team A 부분만 합쳐진 동안에는 `DASHBOARD_UI=READY` 후보일 뿐이다. Team B 실물 artifact와 나머지 hard
gate가 통과하기 전에는 전체 P1 완료나 Release라고 말하지 않는다.

## Team A에게 그대로 보내는 짧은 메시지

```text
최신 main을 pull한 뒤 workspaces/experience-dashboard/만 작업해 주세요.
실제 API에 연결된 Dashboard source, lockfile, production Dockerfile, unit/contract/Playwright 테스트를
한 PR로 주세요. mock, node_modules, .next, 개인 env, secret은 보내지 마세요.
자세한 완료 기준은 docs/decision-platform/P1_TEAM_A_DASHBOARD_완료_요청서.md에 있습니다.
```
