# MARS 공개 제품의 password bootstrap 경계

## 변경 이유

공개 `mars-demo`와 `mars-full`은 기존 고정 데모 계정의 password 로그인과
credential bundle을 서비스 런타임에 싣지 않는다. `LOCAL`은 내부 전환 중
기존 마이그레이션과 로그인을 유지한다.

## 런타임 계약

- 공개 모드에서 `DEMO_CREDENTIAL_SEPARATION_KEY`,
  `DEMO_USER_CREDENTIAL_BUNDLE`, `DEMO_ADMIN_CREDENTIAL_BUNDLE` 중 하나라도
  주입되면 시작을 거부한다. 공개 password 로그인 경로도 제공하지 않는다.
- 과거 V7 migration이 고정 행을 요구하는 새 DB에서는 공개 모드가 서로 다른
  임시 암호 증거를 프로세스 메모리에서 생성한다. 암호는 운영자에게 출력하거나
  서비스 설정으로 전달하지 않는다. 기존 DB의 불일치 행은 V7 규칙대로 거부한다.
- 이 임시 행은 마이그레이션 호환을 위한 과도기 데이터다. 내부 호출처 교체와
  다음 정방향 마이그레이션에서 고정 계정 의존성을 제거한다.

API의 Google OIDC, KIS 사용자별 키, 주문 경계는 이 변경으로 완화되지 않는다.
