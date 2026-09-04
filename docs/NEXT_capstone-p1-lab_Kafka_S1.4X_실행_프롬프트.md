# NEXT — capstone-p1-lab Kafka/S1.4X 실행 프롬프트

아래 내용을 새 작업에 그대로 사용한다. 현재 P1 production Compose와 보존 worktree는 수정하지 않는다.

```text
Capstone-AI-Trading-Coach 저장소 루트에서 capstone-p1-lab을 독립 검증한다.

목표:
- Kafka async adapter와 DB async adapter의 correctness와 처리량을 같은 입력으로 비교한다.
- S1.4X 구현과 production Python/NumPy S1.4의 수치 패리티와 실행비용을 비교한다.
- 연구 결과를 production runtime에 연결하거나 hot-swap하지 않는다.

제약:
- provider/account/order/GDELT outbound 물리 호출은 0이다.
- 기존 deploy/p1/compose.yml, frozen contract/evidence, 보존 S1.4X worktree bytes는 변경하지 않는다.
- 새 파일은 deploy/lab/compose.yml과 docs의 신규 benchmark 문서에 한정한다.
- 기존 DB/volume/port/project name을 재사용하지 않는다. Compose project는 capstone-p1-lab이다.
- fixture와 manifest SHA를 실행 전에 고정하고 결과에 함께 기록한다.

Kafka 검증:
1. 동일한 synthetic event manifest를 DB adapter와 Kafka adapter에 입력한다.
2. at-least-once 중복, 순서 뒤바뀜, worker 재시작, poison/DLQ, broker 재시작을 각각 재현한다.
3. 최종 상태와 event hash가 DB 기준 결과와 같은지 검증한다.
4. warm-up 뒤 최소 5회 측정하고 median/p95 throughput, end-to-end latency, CPU, peak RSS를 기록한다.

S1.4X 검증:
1. production Python/NumPy golden vector를 단일 기준으로 사용한다.
2. NaN/Inf, 경계값, 순서 변화, 반복 실행을 포함해 output bytes 또는 승인 tolerance를 검증한다.
3. warm-up, 반복 수, CPU affinity, runtime version을 고정한다.
4. median/p95 latency, throughput, peak RSS, 시작시간, image 크기를 측정한다.

종료 판정:
- correctness가 하나라도 다르면 NO-GO다.
- 성능 개선은 절대값과 백분율을 함께 쓰고 raw result와 명령을 남긴다.
- 실험 성공도 P1 production 채택을 의미하지 않는다.
- CODEX_SECURITY_DEEP_SCAN은 실행하지 않는다.
```
