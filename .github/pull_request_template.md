# PR 체크리스트

## 한영 요약 / Bilingual Summary

KR:

EN:

## 연결 항목 / Links

- Issue:
- Related PR:
- Commit references:

## 변경 범위

- [ ] 이 PR의 범위가 `AGENTS.md`의 "현재 단계"에서 허용하는 변경과 일치한다.
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
- [ ] secret scan을 통과했다.

## 검증

- [ ] `git status --short --branch`
- [ ] `git check-ignore -v .env .env.local http-client.private.env.json`
- [ ] `python3`로 `pyproject.toml` 파싱 확인
- [ ] Docker 사용 가능 환경에서 `POSTGRES_PASSWORD=dummy docker compose -f infra/docker-compose.infra.yml config --quiet`
