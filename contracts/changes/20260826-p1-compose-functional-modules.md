# P1 Compose 기능 모듈 통합

## 결정

P1의 사용자 실행 권위는 `deploy/p1/compose.yml` 하나다. 기본 장기 컨테이너는 5개, 모델 profile은
7개로 고정한다.

```text
postgres
redis
actor-authority
decision-platform
experience-dashboard
+ bge-m3-tei
+ paddleocr-vl-llama
```

## 변경 이유

기존 gateway, runtime netns, Spring, Python, Team B artifact server와 bootstrap 서비스가 각각 별도
장기·종료 컨테이너로 남아 사용자가 정상 상태를 판단하기 어려웠다. Spring과 Python은 같은
Decision Platform 수명주기이므로 supervisor와 aggregate health를 가진 기능 컨테이너로 합친다.
서명 개인키를 가진 actor authority만 mTLS 경계로 분리한다.

## 불변식

- Dashboard는 app network만 사용한다.
- PostgreSQL, Redis, actor authority는 internal data network만 사용한다.
- decision-platform만 두 network를 연결한다.
- Team B preview와 모든 bootstrap은 `docker compose run --rm`이다.
- model fetch도 `run --rm`이며 cache volume을 보존한다.
- `down`은 volume을 삭제하지 않는다.
- KIS Live brokerage origin/TR은 존재하지 않는다.

기존 v1 Compose 파일은 historical regression으로 남지만 `./capstone`은 읽지 않는다.
