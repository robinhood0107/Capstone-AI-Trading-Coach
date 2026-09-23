# MARS 데모 provider의 무이력 경계

기존 S4.9 gRPC Agent는 인증된 owner의 RLS·도구·Google Search·사용량 원장을
사용한다. 데모는 방문자 로그인과 계좌가 없으므로 이 원장을 가짜 사용자 세션으로
열지 않는다. 같은 provider transport를 재사용하되 `mars-demo`에서는 공개 예제
근거만 허용하고 Google Search·웹 도구를 끄며 Python tool round를 0으로 보낸다.

Kotlin host는 provider ID가 Vertex인지 확인하고, owner 기반 usage/history 및
grounding provenance 쓰기를 건너뛴다. provider permit 직전 V201의 `DEMO_AGENT`
예약만 content-free로 남긴다. 모델이 예제 밖 지식만으로 답하거나 외부 grounding을
반환하면 답을 거부한다. 일일 총액이 끝나면 제한된 실패 코드로 반환해 익명 API가
429로 안내할 수 있다.

이 변경은 provider runtime 경계다. 익명 HTTP ask, 입력·분당 제한, 공개 인용 응답,
전용 이미지/DB/secret과 무이력 end-to-end 검증은 뒤이은 변경에서 연결한다.
