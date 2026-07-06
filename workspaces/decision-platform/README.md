# decision-platform

담당: 박종진 (`robinhood0107`)

투자 원칙(Principle) → 평가(Decision/RiskEngine) → 모의 주문(Brokerage) → RAG 설명까지를 담당하는 워크스페이스. Kotlin(Spring) API 서버와 Python(gRPC/FastAPI) 서비스 두 축으로 구성된다.

## 구조

```
spring-api/            # Gradle Kotlin 프로젝트 — Controller/Application/Domain/Infrastructure
python-services/        # uv 프로젝트 — LightGBM/RAG/금융공학/데이터클라이언트/브로커리지 어댑터
```

## 세팅

공개 레포에는 최종 명세/API 계약과 초기 스캐폴드만 둔다. 상세 개인 참고 노트는 루트의 ignored `private-reference/` 폴더에서만 관리한다. 요약:

1. `docker compose -f ../../infra/docker-compose.infra.yml up -d`
2. `cp ../../.env.example ../../.env` 후 값 채우기
3. `spring-api/`는 아직 [start.spring.io](https://start.spring.io) 생성물이 비어 있는 상태다. IntelliJ에서 Gradle-Kotlin / `com.capstone` / `spring-api` / Spring Boot 4.1.0 / Java 25 + (Web, Validation, JPA, Redis, Security, Actuator, Flyway, Kafka, Testcontainers)로 생성한 zip을 이 폴더에 풀고, `build.gradle.kts`/`application.yml`은 이미 커밋된 버전으로 교체한다 (gradle wrapper는 네트워크가 있는 IDE에서 생성해야 하므로 여기서는 커밋하지 않음).
4. `python-services/`는 `uv sync`로 바로 실행 가능.
