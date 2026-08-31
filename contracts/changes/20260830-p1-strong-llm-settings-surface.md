# P1 Strong LLM settings surface

## KR

Strong LLM의 provider·2차 provider·모델·답변 언어·하루 호출 상한과 API 키를 화면에서 정할 수
있게 한다. root OpenAPI는 operation 하나가 는다 — 69번째다.

**쓰기는 새 operation 하나, 읽기는 기존 상태에 얹는다.** `PUT /api/v2/strong-llm/settings`는
응답 본문이 없다. 키를 담을 수 있는 응답을 아예 만들지 않는 것이 키를 응답에서 지우는 것보다
확실하다. 현재 설정과 키의 마지막 네 글자는 `GET /api/v2/rag/corpus-status`가 이미 돌려주는
`RagV2CorpusStatus`에 함께 실린다.

**계획은 이 설정을 `AutomationPolicyV2` 확장으로 넣으려 했는데 그럴 수 없다.** 그 스키마는
68→61 투영이 걷어내는 집합에 없어 exact-61 해시 안에 그대로 남는다. 필드 하나만 더해도 동결
사슬이 깨진다(`AutomationRunV2`에서 이미 같은 벽을 만났다). `RagV2CorpusStatus`는 그 투영이
통째로 제거하는 스키마라 필드를 늘려도 사슬을 건드리지 않는다. 그래서 읽기가 그쪽에 붙는다.

**이 전이 층에는 자기 byte anchor가 없다.** 68 문서는 `RagV2CorpusStatus`가 자란 만큼 달라지고
그 차이는 다음 층이 그 스키마를 걷어낼 때 사라진다. 이 층이 지키는 것은 둘이다 — operation이
정확히 하나 늘었고, 그것을 걷어낸 문서가 여전히 동결된 exact-61로 수렴한다. 그래서 승인
additive set에는 component schema가 하나도 없다.

**키는 KEK 봉투로 감싼다.** RAG 답변 이력과 같은 키 재료를 쓰되 AAD는 따로 만든다. 이력의 AAD는
answerId와 생성 시각에 묶여 있어 키에 쓰려면 없는 답변 식별자를 지어내야 하고, 지어낸 식별자는
AAD가 무엇을 묶는지를 흐려 봉투를 다른 소유자의 행에 옮겨 붙이는 실수를 막지 못한다. 키의
AAD는 소유자·슬롯·KEK 버전이다.

**키 행은 소유자 세션도 SELECT하지 못한다.** RLS는 definer 함수만 열고, 복호화 재료를 돌려주는
길은 함수 하나뿐이다. 화면이 쓰는 마지막 네 글자는 별도 함수가 준다. 테이블을 직접 열어 두면
언젠가 조인 하나가 키 봉투를 응답에 실어 나른다.

**`null`과 빈 문자열을 나눈다.** 키 필드가 없으면 "그대로 둔다", 빈 문자열이면 "지운다"다. 둘을
하나로 합치면 설정만 바꾸려는 요청이 이미 저장된 키를 조용히 지운다.

DB DML, provider 호출, KIS Live 호출은 0이다. 설정을 저장한 적이 없는 소유자에게는 배포
기본값이 그대로 보인다.

## EN

Adds one root operation — the 69th — so the owner can choose the Strong LLM provider, its fallback,
the answer language, the daily call cap, and the API keys from the screen. The write endpoint returns
no body: never building a response that could carry a key is stronger than removing keys from one.
Reading rides on `RagV2CorpusStatus`, which the 68→61 projection removes wholesale, so extending it
leaves the frozen chain untouched; `AutomationPolicyV2`, which the plan proposed, sits inside that
frozen hash and cannot take the fields. Keys are sealed with the RAG history KEK material under their
own owner/slot/version AAD, and the credential rows are unreadable even to the owner session — one
definer function returns decryption material, another returns only the last four characters.
