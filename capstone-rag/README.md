# Capstone RAG local root

이 디렉터리는 Decision Platform RAG의 저장소 상대 approved root다. 애플리케이션과
테스트는 저장소 루트를 먼저 확정한 뒤 아래 상대 경로를 해석하며, `/mnt/c`, 홈 디렉터리,
현재 작업 디렉터리 추론 또는 사용자 입력 절대경로를 대신 사용하지 않는다.

| 경로 | Git | 용도 |
|---|---|---|
| `manifests/` | 추적 | corpus/model/evidence의 bounded metadata, SHA-256, provenance |
| `source-cards/` | 추적 | 프로젝트가 직접 작성하고 공개 가능한 source card |
| `eval/` | 추적 | 공개·합성·비식별 평가 fixture와 gold metadata |
| `reports/` | 추적 | secret/PII/raw 질문이 없는 bounded 평가 보고서 |
| `evidence/` | payload 제외 | 공식 원천의 bounded 캡처·PDF·스크린샷; manifest만 추적 |
| `secrets/` | payload 제외 | 배포 전에 별도 승인된 secret-file 위치; 값·존재 여부 비노출 |
| `runtime/` | payload 제외 | parse/index/cache/temporary generation 산출물 |
| `tmp/` | 제외 | 원자 write 중간 파일 |

모든 파일 I/O는 repository root canonicalization 뒤 이 경계 안의 정규 파일만
directory-fd, `O_NOFOLLOW`, 크기 상한과 exclusive-create로 처리한다. symlink, hardlink,
magic-link, path traversal, 기존 파일 덮어쓰기와 broad recursive delete는 허용하지 않는다.

이 디렉터리가 Git 안에 있다는 사실은 evidence 원문, 모델 가중치, API key 또는 provider
응답을 커밋해도 된다는 뜻이 아니다.
