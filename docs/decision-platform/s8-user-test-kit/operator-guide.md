# 운영자 가이드

## 수집 전 gate

1. 학교 IRB의 심의 또는 면제 판단 문서가 있어야 한다. 없으면 참가자 모집과 응답 수집은 0이다.
2. 승인된 연구책임자와 최소 접근자만 지정한다.
3. demo가 `capstone-s8-demo`, `INTERNAL_PAPER`, provider/live/account/order 0인지 확인한다.
4. `questionnaire.v1.json`과 응답 JSON Schema의 hash를 동결한다.
5. 참가자에게 실패 결과는 교육 개선에만 쓰며 주문 권한이나 개인 투자성향에 사용하지 않음을 알린다.

## 참가자 진행

- 동의 전에는 opaque participant ID도 발급하지 않는다.
- 동의 뒤 random `part_` ID와 별도 withdrawal code를 발급한다. 저장에는 withdrawal code 원문 대신
  SHA-256만 둔다.
- boolean, enum, 1~7/1~5 bounded score 외 입력 UI를 제공하지 않는다.
- 네 안전 과업을 모두 수행한다. 정답 실패는 교육 개선 tag로만 기록한다.
- 화면, 음성, 영상 녹화는 이 킷의 수집 범위가 아니며 별도 IRB amendment 없이 켜지 않는다.

## 중단·철회·삭제

- 참가자는 언제든 중단할 수 있고, withdrawal code로 삭제를 요청할 수 있다.
- operator는 hash를 매칭한 뒤 응답 row와 접근 가능한 파생 export를 삭제하고 삭제 audit에는
  participant ID나 응답 내용을 넣지 않는다.
- retention 만료 시 원응답과 연결표를 삭제하고 집계된 비식별 count만 보존한다.

## 금지

- 응답 조작, 가상 참가자 생성, 동의/IRB 결과 fabrication
- 자유서술 PII, account/order/provider identifier, 투자성향 profiling
- 교차시장의 현재 `WARN_ONLY` 또는 자동매매 성과 주장
- 실패 결과를 권한 제한, 개인 평가, 투자 추천에 사용하는 행위
