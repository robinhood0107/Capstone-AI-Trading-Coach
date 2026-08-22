# S8 offline demo runner

이 runner는 기존 개발 DB/RAG namespace와 물리적으로 다른 Compose project
`capstone-s8-demo`를 사용한다. provider, live account, live order, external RAG는 모두 꺼져 있고
기존 volume을 삭제하거나 reset하지 않는다.

DB 기본 경로:

```bash
workspaces/decision-platform/demo/s8/run-demo.sh \
  --prepare --adapter=db --brokerage-mode=INTERNAL_PAPER
```

Kafka 선택 경로:

```bash
workspaces/decision-platform/demo/s8/run-demo.sh \
  --prepare --adapter=kafka --brokerage-mode=INTERNAL_PAPER
```

중지는 volume을 보존한다.

```bash
workspaces/decision-platform/demo/s8/run-demo.sh \
  --stop --adapter=db --brokerage-mode=INTERNAL_PAPER
```

`--brokerage-mode=INTERNAL_PAPER`는 운영자의 명시 선택 증거다. 누락하거나 다른 값을 쓰면
인프라를 시작하기 전에 종료한다. Kafka profile은 adapter를 암묵적으로 변경하지 않는다.

생성물은 Git에서 제외되는 `artifacts/decision-platform/s8-demo/`에만 기록한다. 같은 seed는
no-op이고 기존 파일의 bytes가 다르면 충돌로 중단한다. synthetic 수치는 실제 Return Engine
artifact 또는 투자 성과가 아니다.
