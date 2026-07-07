# PR 체크리스트

## 한영 요약 / Bilingual Summary

KR:

EN:

## 연결 항목 / Links

- Issue:
- Related PR:
- Commit references:

## 변경 범위

- [ ] 이 PR의 범위가 `AGENTS.md`에 명시된 단계별 허용 변경과 일치한다.
- [ ] Stage 2 PR이라면 현재 세션(S1~S8)의 DoD와 구현 범위를 PR 설명에 명시했다.
- [ ] S1.1 KIS 작업이라면 OAuth/cache/current price/daily itemchartprice/backfill 같은 시장데이터 읽기 범위만 포함하고 주문·계좌 변경 API를 구현하지 않았다.
- [ ] `workspaces/return-engine/` 또는 `workspaces/experience-dashboard/`의 placeholder 경계를 침범하지 않았다.
- [ ] 계약 변경이 있다면 `contracts/changes/`에 이유와 영향 범위를 기록했다.
- [ ] 관련 Issue/PR/commit을 `#<번호>` 형식으로 연결했다. 닫는 이슈가 있으면 `Closes #<번호>`를 사용했다.

## 문서와 규칙

- [ ] `AGENTS.md` 규칙을 확인했다.
- [ ] Issue와 PR 제목/본문을 한국어와 영어로 모두 작성했다.
- [ ] 관련 기준 문서(`최종_프로젝트_명세서`, `API_명세서`)를 확인했다.
- [ ] README 또는 docs 링크가 깨지지 않는다.

## 보안

- [ ] `.env`, API key, JWT secret, 계좌번호, 토큰, private HTTP env 파일을 포함하지 않았다.
- [ ] `private-reference/` 아래 파일을 포함하지 않았다.
- [ ] KIS app key/secret, OAuth access token, account number, raw response header/body, 주문/잔고 원본 로그를 포함하지 않았다.
- [ ] raw KIS 응답, generated parquet/csv/jsonl, 로컬 `data/` 산출물을 포함하지 않았다. 테스트 fixture는 마스킹된 offline fixture만 사용했다.
- [ ] KIS Live 시장데이터 조회와 KIS Live 주문 기능을 분리했다. Live 주문·정정·취소는 명시적 gate와 사용자 확인 없이는 비활성이다.
- [ ] secret scan을 통과했다.

## 검증

- [ ] `git status --short --branch`
- [ ] `git check-ignore -v .env .env.local http-client.private.env.json`
- [ ] `python3`로 `pyproject.toml` 파싱 확인
- [ ] Docker 사용 가능 환경에서 `POSTGRES_PASSWORD=dummy docker compose -f infra/docker-compose.infra.yml config --quiet`
