# 현재 OpenAPI root 정합

현재 root는 `openapi=3.1.1`이어야 한다는 normalizer·API 명세 계약이 있으나 추적 파일은
`3.1.0`이었다. 동시에 normalizer가 현재 `AutomationPositionV2.expirySession`의
nullable 형식을 과거 문자열 전용 모양으로 되돌려 현재 DTO와 추적 파일이 달라졌다.

추적 root의 버전 필드를 `3.1.1`로 맞추고, 현재 root에 적용되던 과거 V2 투영을 제거한다.
생성물의 `expirySession`은 계속 `string|null`이며 이전 세대 snapshot 파일은 그대로 둔다.
endpoint, 런타임 응답, credential, provider 호출은 바뀌지 않는다. 격리 DB에서 생성한
실제 Spring OpenAPI와 현재 추적 root를 비교해 확인한다.
