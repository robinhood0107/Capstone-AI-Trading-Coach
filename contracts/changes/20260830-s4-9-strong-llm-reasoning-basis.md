# S4.9 Strong LLM reasoning basis

## KR

답에 추론 문장을 허용하는 basis `EVIDENCE_WITH_REASONING`을 더하고, 생성 출력 예산을
32,768 토큰으로 올린다.

`EVIDENCE`는 모든 문장에 정확 인용을 요구했다. 그래서 모델이 근거를 잇거나 비교하거나 한계를
말하는 문장을 쓸 수 없었고, 답이 인용의 나열이 됐다. Strong LLM을 쓰는 이유가 거기서 사라진다.

새 basis는 그 문장을 허용하되 검증 가능한 성질을 지킨다.

- 근거 문장은 기존 `EVIDENCE` 규칙 그대로다. 정확 인용, 숫자 대조, citation 결속이 모두 남는다.
- 추론 문장은 `citationIds`·`evidenceSpans`·`numericSpans`를 모두 비운다.
- 추론 문장은 "현재·최근·오늘" 같은 시점을 주장하지 못한다. 인용이 없으면 그것을 확인할 방법이
  없고, 근거가 오래됐는지조차 말할 수 없다.
- 추론 문장의 숫자는 **같은 답의 근거 문장이 이미 인용으로 증명한 값만** 다시 쓸 수 있다.
  인용 없는 문장에 새 숫자를 허용하면 그것이 곧 조작된 수치가 답으로 들어오는 통로가 된다.
- 근거 문장이 하나도 없으면 이 basis를 고를 수 없다. 그것은 추론이 아니라 `MODEL_KNOWLEDGE`이고,
  그 basis의 숫자·시점 금지는 그대로다.

화면은 `guardrailFlags`의 `REASONING_SENTENCES_PRESENT`로 두 종류를 구분한다. 이 표식은 원장에
필수로 남으며, 반대로 `EVIDENCE`에는 붙을 수 없다. 원장이 basis를 뭉뚱그리면 "인용 없는 문장이
섞인 답"과 "전부 인용된 답"을 사후에 구분할 수 없고, 그러면 이 완화를 받아들일 근거였던 감사
가능성이 사라진다.

출력 예산은 답을 길게 하려고 올린 것이 아니라 답이 잘리지 않게 하려고 올렸다. 잘린 JSON은
계약 위반으로 통째로 버려졌고, 그 예산의 대부분은 본문이 아니라 인용 span과 thinking이 먹는다.
그래서 응답 바이트 상한과 JSON 토큰 상한만 그만큼 넓히고 **답 본문 상한 8,192는 그대로 둔다** —
그 값은 여섯 개 마이그레이션의 저장 제약이 함께 들고 있어서, 여기서만 올리면 긴 답이 저장
단계에서 거부된다. 통제는 상한이 아니라 호출 횟수로 한다.

`p_citation_coverage` 하한은 `EVIDENCE` 0.8을 유지하고 새 basis만 0.2다. 인용이 하나도 없는
답은 validator가 이 basis로 통과시키지 않으므로 0은 여전히 아니다.

DB DML, provider 호출, KIS Live 호출, public OpenAPI 변경은 0이다. `guardrailFlags`는 이미
문자열 배열이라 계약을 넓히지 않는다.

## EN

Adds an `EVIDENCE_WITH_REASONING` basis so an answer may carry sentences that connect, compare, or
qualify its grounded ones, and raises the generation budget to 32,768 tokens. Grounded sentences keep
the exact-quote, numeric, and citation rules unchanged. Reasoning sentences carry no citations, may not
claim what is true now, and may reuse only numbers a grounded sentence in the same answer already
proved, so no new fact enters without evidence. The ledger records the basis and a mandatory
`REASONING_SENTENCES_PRESENT` flag, keeping the two answer shapes distinguishable after the fact. The
answer body cap stays at 8,192 bytes because six migrations pin it; the raised budget widens only the
response and token limits that were truncating valid answers mid-JSON.
