# #232 RAG와 Agent 필요성 평가

네 방식의 실제 모델 비교에서 RAG는 필수 출처 4개를 모두 전달했다. 종합 통과는 2/8로
원문 입력·공식 조회와 같았다. 별도 Agent 비교에서는 규칙 2단계가 3/3을 통과했고
단일 조회와 모델 라우팅 대역은 각각 1/3이었다.

## 실행 기준

2026-11-28 KST에 깨끗한 작업 트리와 `HEAD = origin/develop`을 확인했다.
시작 SHA는 `6b417050c540f440acca68810ee361c3a4bebd0f`이며 #228이 들어간 #243 병합 커밋이다.
팀 제출 PR #7과 병합 커밋 `2dbc66503d4f85ac40736445676f4907ea78c8f8`은 GitHub에서
비교 기준으로 확인했다. [팀 제출 PR #7](https://github.com/pnucse-capstone2026/capstone-2026-team-33/pull/7)

가상의 문서 12개와 영어 질문 8개, 정답·출처·보류 조건을 최초 실행 전에 고정했다.
2026-11-27 19:19:47 UTC에 고정한 커밋은 `94b7a7b80f16335c0de99979973b0b23a77ad89f`다.
대역 실행 후 mypy가 평가 코드의 타입 누락을 잡았다. 타입과 서식을 고친
`c64767572a81c80951ac38e8452f7b44c44eb198`에서 회귀 검사를 다시 돌렸다. 가설과 정답, 분모, 질문은 유지했다. 이후 Product Impact가 평가셋과 프로토콜의 경로를 미분류로 보고해
`9534db114781233e9a191bd6d835f7b75e889a0c`에서 담당 테스트·문서 경로로 옮겼다.
평가셋 해시는 일치한다. 프로토콜은 평가셋 링크 한 곳만 바뀌었다.
[동결 프로토콜](issue-232-pilot-protocol.md)

| 고정 항목 | SHA256 |
| --- | --- |
| 평가셋 | `50b6d58a6b7d64b1dc629a41c5704e35220b827c2633c1206f1702e5ee487447` |
| 프로토콜 | `eebaf5bdbe4331293d94a774961fbf70360788f5cae385227cdc76e29d0a53d4` |
| 실제 실행용 평가 코드 | `7268a2c75bc35ce2ee90a3fd421a268240e89560ea56a286e492b44893b6f578` |

## 합성 대역

대역은 정답표를 읽지만 문서가 필요한 질문에서는 해당 출처를 받았을 때만 답한다.
대역 32건은 판정 코드와 문서 전달 경로를 확인하는 데 썼다. 모델 호출과 추론 시간 측정은 0건이다.

| 방식 | 종합 통과 / 8 | 필수 출처 전달 / 4 | 보류 / 2 | 제한 표식 노출 |
| --- | ---: | ---: | ---: | ---: |
| 모델 단독 | 4 | 0 | 2 | 0 |
| 원문 직접 입력 | 7 | 3 | 2 | 0 |
| 공식 문서 조회 | 7 | 3 | 2 | 0 |
| RAG v2 평가용 경로 | 8 | 4 | 2 | 0 |

T2에서 직접 입력에는 `VEGA-RULE`만 주어졌다. 공식 조회는 지정된 문서 ID의 최신본을 가져왔다. 별도 ID인 `VEGA-ALERT`를 찾지 못했다. RAG 평가용 경로는 두 문서를 모두 넘겼다.
H1의 제한 문서와 H2의 미래 문서는 모든 프롬프트에서 제외했다.
운영 RLS와 외부 처리 동의는 별도 검증 대상이다.

## 실제 로컬 모델

WSL Ubuntu의 Intel Core Ultra 5 228V에서 `Qwen2.5-1.5B-Instruct Q4_K_M` 한 종을 실행했다.
모델은 Apache-2.0이며 공식 revision은 `91cad51170dc346986eccefdc2dd33a9da36ead9`다.
GGUF 1,117,320,736바이트를 해당 revision으로 내려받아 공식 SHA256과 대조했다.
[공식 GGUF](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/blob/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf)

공식 Linux x86-64 CPU 릴리스 `llama.cpp b12774`를 상태 폴더에 풀었다.
`llama-server --version`은 `1.1.0-dev (build 12774, commit 171527522)`를 반환했다.
`docker compose ls`에 스택이 없음을 확인한 뒤 루프백, CPU 0·1, 생성·배치 각 2스레드,
논리 배치 128, 물리 배치 64, 컨텍스트 2,048, 동시 슬롯 1로 실행했다.
[llama.cpp b12774](https://github.com/ggml-org/llama.cpp/releases/tag/b12774)

| 검증 자산 | SHA256 |
| --- | --- |
| GGUF | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| 공식 릴리스 압축 파일 | `ab5837cca2317cfb7ecb5f85b550934e7355cd9df74cdde6e106581457034dbb` |
| 압축 해제 후 실행기 | `2e383eede72501203426da5e447e11eedce0b405dce47a9a9bd9ba2e820645d1` |

32개 응답을 모두 받았다. 네 방식 모두 같은 프롬프트, 출력 스키마, seed 232,
temperature 0, 최대 출력 토큰 100을 썼고 prompt cache를 끈 상태에서 한 건씩 실행했다.

| 방식 | 종합 / 8 | 정답 / 6 | 출처 전달 / 4 | 인용 적합 / 4 | 보류 / 2 | 표식 노출 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 모델 단독 | 1 | 0 | 0 | 0 | 1 | 0 |
| 원문 입력 | 2 | 1 | 3 | 2 | 1 | 0 |
| 공식 조회 | 2 | 1 | 3 | 3 | 1 | 0 |
| RAG v2 평가용 경로 | 2 | 1 | 4 | 3 | 1 | 0 |

| 문항 | 모델 단독 | 원문 입력 | 공식 조회 | RAG v2 평가용 경로 |
| --- | ---: | ---: | ---: | ---: |
| G1 | 값 불일치 | 값 불일치 | 값 불일치 | 값 불일치 |
| G2 | 보류 | 보류 | 보류 | 보류 |
| O1 | 값·출처 불일치 | 값 불일치 | 값 불일치 | 값 불일치 |
| O2 | 보류 | 통과 | 통과 | 통과 |
| T1 | 값·출처 불일치 | 값·출처 불일치 | 값 불일치 | 값 불일치 |
| T2 | 보류 | 값·출처 불일치 | 값·출처 불일치 | 값·출처 불일치 |
| H1 | 통과 | 보류 실패 | 보류 실패 | 보류 실패 |
| H2 | 보류 실패 | 통과 | 통과 | 통과 |

G1에서는 값에 `100` 이외의 문자가 붙었고 G2에서는 네 방식 모두 보류했다. O2는 공식 원문을
받은 세 방식에서 정답과 출처를 맞혔다. T1의 정정 공지는 원문 입력, 공식 조회, RAG에 들어갔다.
세 방식 모두 정정값 `37`을 놓쳤다. RAG는 T2의 예외 공지도 찾았지만 값과 인용 ID가 어긋났다.
H1에서 문서를 받은 세 방식은 코드가 공개되지 않았다고 답했지만 상태를 `answer`로 보냈다.
판정 기준은 `hold`와 빈 값·인용을 요구하므로 세 응답은 보류 실패다. 제한 표식 노출은 0건이었다.

| 방식 | 추론 호출 | 입력 토큰 | 출력 토큰 | 문서 준비 중앙값 ms | 추론 중앙값 ms | 합계 중앙값 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 모델 단독 | 8 | 1533 | 162 | 0.007 | 3262.9 | 3263.0 |
| 원문 입력 | 8 | 2067 | 181 | 0.008 | 5010.6 | 5010.6 |
| 공식 조회 | 8 | 1894 | 176 | 0.012 | 5176.9 | 5176.9 |
| RAG v2 평가용 경로 | 8 | 2196 | 200 | 0.376 | 5296.3 | 5296.7 |

합계 중앙값은 각 요청의 문서 준비와 추론 시간을 더한 뒤 구했다. 다운로드와 서버 기동은
시간에서 제외했다. 문서 준비는 메모리에서 실행했으므로 운영 DB나 외부 조회 지연은 알 수 없다.

## 별도 Agent 비교

별도 합성 과제는 공식 문서 9개와 질문 3개로 구성했다. 커밋
`db48e088f5270063a1cd29bf9f758a73e69a1d00`에서 2026-11-27 19:29:59 UTC에
평가 기준을 고정했다. 첫 문서의 링크에는 질문에 필요한 문서와 관련 없는 공지를 함께 넣었다.
규칙 2단계는 질문에 표시된 관계를 따른다. 모델 라우터는 같은 모델을 한 번 더 써서 문서 키를
고른다. 첫 문서에서 허용한 키만 한 번 더 조회한다. 기준일 뒤에 공개되는 문서는 넘기지 않는다.
[Agent 비교 프로토콜](issue-232-agent-protocol.md)

| 고정 항목 | SHA256 |
| --- | --- |
| Agent 평가셋 | `8d854698dbfe07b9ac51a904f5dc47b28f753788aa24c4dd1633ceb00eb29018` |
| Agent 프로토콜 | `f6ee6c7f86f3650ca6cec9fc16fd04cedbe69f4a163c1af61a81bfeee65ad37c` |
| Agent 실행 코드 | `814e5a25232ba98b97b5d2c567f79d20bcb999c75ccd75f749ba18f79aec8914` |

대역은 단일 조회 1/3, 규칙에 따른 2단계 조회 3/3, 모델 라우팅 대역 3/3이었다.
세 방식 모두 공개 전 가격 질문에서 보류했다. 대역은 추론을 호출하지 않았다.

같은 GGUF로 최종 응답 9회와 라우팅 응답 3회를 받았다. 최종 응답의 JSON 구조 오류는 0건이다.

| 방식 | 종합 / 3 | 정답 / 2 | 출처 전달 / 2 | 인용 적합 / 2 | 보류 / 1 | 출력 형식 오류 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 단일 조회 | 1 | 0 | 0 | 0 | 1 | 0 |
| 규칙 2단계 | 3 | 2 | 2 | 2 | 1 | 0 |
| 모델 라우팅 대역 | 1 | 0 | 0 | 0 | 1 | 0 |

| 문항 | 단일 조회 | 규칙 2단계 | 모델 라우팅 대역 |
| --- | ---: | ---: | ---: |
| A1 | 값·출처 불일치 | 통과 | 값·출처 불일치 |
| A2 | 값·출처 불일치 | 통과 | 값·출처 불일치 |
| A3 | 통과 | 통과 | 통과 |

규칙 2단계는 A1에서 예외 공지의 `12`, A2에서 정정 공지의 `29`와 해당 출처를 맞혔다.
단일 조회와 모델 라우팅 대역은 A2에서 과거 수치 `23`으로 답했다. 모델 라우팅 대역은
A1·A2에서 필요한 문서 키를 인용 배열에 넣었다. 세 라우팅 출력 모두 검증을 통과하지 못해
후속 조회는 0건이었다. 검증을 통과한 잘못된 라우팅도 0건이었다.
규칙 2단계가 A3의 공개 예정 문서를 조회했을 때 기준일 필터가 문서를 제외했다.
A3는 세 방식 모두 보류했다.

| 방식 | 문서 조회 시도 | 추론 호출 | 입력 토큰 | 출력 토큰 | 합계 중앙값 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 단일 조회 | 3 | 3 | 786 | 65 | 5541.1 |
| 규칙 2단계 | 6 | 3 | 909 | 62 | 6147.3 |
| 모델 라우팅 대역 | 3 | 6 | 1833 | 144 | 11323.0 |

모델 라우팅 대역의 시간과 토큰에는 문서 키를 고르는 호출이 포함된다. 두 실험의 추론 44회는
모두 루프백에서 끝났다. Prompt cache 재사용 토큰은 0개였고 오류 출력, 요청 실패,
토큰 한도 종료도 없었다. 외부 추론 과금은 0이며 전력·장비 비용은 측정하지 않았다.

## 경로별 판단

일반 지식 문항에서는 모든 방식이 0/2였다. 공식 문항에서는 원문을 제공한 세 방식이
각각 1/2였다. 날짜·정정 문항에서는 RAG가 검색 범위를 넓혔지만 정확한 답과 인용까지 맞힌
방식은 없었다. RAG의 추가 통과와 보류 2/2를 함께 요구한 사전 기준은 충족하지 못했다.
이 모델과 고정 질문에서는 RAG를 확대할 근거가 부족하다. 출처가 지정된 질문은 단일 공식 조회를
기준으로 두고, 관계가 표시된 정정·예외 문서에는 규칙 2단계를 추가 비교 대상으로 둔다.

별도 Agent 실험에서 규칙 2단계는 단일 조회보다 2문항을 더 맞혔다. 모델 라우팅 대역은
호출과 토큰을 더 쓰고도 추가로 맞힌 문항이 없었다. 문서 관계가 정해진 이 표본에는
규칙 2단계를 우선 시험할 근거가 있다. 제품 Agent의 다단계 추론, 실제 문서 검색,
한국어 응답 품질, 운영 환경의 지연·비용은 이 비교 범위 밖이다.

## 재현

검증은 WSL Ubuntu에서 실행했다. `uv`는 기존 설치를 쓰고 평가용 가상환경은 상태 폴더에 뒀다.
Ruff 검사 범위는 Python workspace 전체였으며 mypy로 `app`의 409개 파일을 확인했다.

```bash
export PATH="$HOME/.local/bin:$PATH"
state="$HOME/.local/state/capstone-232-rag-eval"
mkdir -p "$state"
export UV_PROJECT_ENVIRONMENT="$state/venv"
cd workspaces/decision-platform/python-services
uv run --frozen pytest -q tests/rag/test_issue_232_pilot.py \
  tests/rag/test_issue_232_agent_pilot.py tests/rag/test_authorized_retrieval.py \
  tests/rag/test_rag_v2_authorized_retrieval.py \
  tests/rag/test_rag_v2_authorized_retrieval_adapter.py tests/rag/test_s4_5_evaluation.py
uv run --frozen ruff check .
uv run --frozen ruff format --check app/rag/issue_232*.py tests/rag/test_issue_232*.py
uv run --frozen mypy app
uv run --frozen python -m app.rag.issue_232_pilot > "$state/fixture-oracle.jsonl"
uv run --frozen python -m app.rag.issue_232_agent_pilot > "$state/fixture-agent.jsonl"
```

기대값은 pytest 통과, Ruff·mypy 오류 0, 4방식 대역 32행과 Agent 대역 9행이다.
pytest 52개가 통과했고 Ruff·mypy 오류도 없었다. 대역 출력 행 수는 기대값과 일치했다. `git diff --check`, `git check-ignore -v .env .env.local
http-client.private.env.json`, Python 메타데이터 TOML 파싱과 Repo hygiene의 합성 변수로
`docker compose --env-file "$state/hygiene.env" -f infra/docker-compose.infra.yml config --quiet`
검사를 통과했다. Docker 서비스 기동은 포함하지 않았다. 화면·JVM 변경이 없어 Dashboard
typecheck·lint·unit·Playwright 및 Gradle 검사는 실행하지 않았다. Node v22.22.1과 Temurin
25.0.3을 확인했다. `CODEX_SECURITY_DEEP_SCAN=NOT_RUN_USER_SCOPED_OUT`.

공식 파일을 위 revision과 SHA256으로 준비한 뒤 아래 명령으로 두 실험을 순서대로 실행했다.
실행 전 `docker compose ls`로 겹치는 Compose를 확인하고 서버는 평가 직후 종료했다.

```bash
server="$state/llama-b12774/llama-b12774/llama-server"
model="$state/qwen2.5-1.5b-instruct-q4_k_m.gguf"
nice -n 10 taskset -c 0,1 "$server" -m "$model" --host 127.0.0.1 --port 19322 \
  -ngl 0 -t 2 -tb 2 -b 128 -ub 64 -np 1 -c 2048 --threads-http 1 --no-cache-prompt \
  > "$state/llama-server.log" 2>&1 &
server_pid=$!
trap 'kill -TERM "$server_pid" 2>/dev/null || true; wait "$server_pid" || true' EXIT
# Run the evaluators after /health reports {"status":"ok"}.
curl --noproxy '*' -fsS http://127.0.0.1:19322/health
uv run --frozen python -m app.rag.issue_232_pilot --llama-port 19322 > "$state/actual-model.jsonl"
uv run --frozen python -m app.rag.issue_232_agent_pilot --llama-port 19322 > "$state/actual-agent.jsonl"
```

실행 명령과 로그는 `$state`에 보관했다. 두 실제 실행은
`actual-environment.log`, `llama-server.log`, `actual-model.jsonl`,
`actual-agent.jsonl`에서 확인할 수 있다. 동결 해시와 대역 출력도 같은 위치에 남겼다.

## 검토 질문

- T2처럼 별도 문서가 기존 규칙을 바꿀 때, 정해진 연관 문서 조회를 다음 비교군에 넣을 것인가?
- 운영 반영을 판단하려면 어떤 규모의 한국어 질문과 어느 범위의 날짜·권한 회귀가 필요한가?
