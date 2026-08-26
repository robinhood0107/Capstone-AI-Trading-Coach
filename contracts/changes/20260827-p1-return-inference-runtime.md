# P1 Return inference runtime

## KR

검증된 Return Engine v2 bundle의 fixed ABI를 실행하는 provider-free runtime을 추가한다.

- NumPy로 exact 3-layer LSTM, hidden 128, window 20, feature 9, per-symbol namespace를 실행한다.
- scaler의 train-only mean/scale과 safetensors F32 tensor만 로드하며 pickle/PTH/joblib/code loading은 0이다.
- request는 canonical JSON exact-31 × 20 × 9이며 artifact/bundle/session/current-close binding을 검증한다.
- response는 forecast, expected return, BUY/HOLD/SELL과 `orderAuthority=NONE`, `providerCalls=0`만 반환한다.
- gRPC는 numeric loopback, shared secret, 5초 이하 deadline, bounded message, concurrency 2를 강제한다.
- reflection을 등록하지 않고 inference process를 Spring·async worker와 같은 supervisor에 둔다.
- active model이 없을 때 process/health는 유지하되 Infer는 `FAILED_PRECONDITION`으로 fail-closed한다.
- synthetic bundle load는 명시적 test profile에서만 허용하고 production 기본값은 false다.

DB DML, provider/account/order/Vertex/GDELT/KIS Live 호출과 public API 변경은 0이다. 실제 REAL_TEAM_B
pointer가 없으므로 production prediction availability를 주장하지 않는다.

## EN

Adds a provider-free fixed-ABI Return inference kernel and loopback gRPC service. The runtime loads only the
validated scaler, config, and F32 safetensors ABI, accepts one canonical exact-31 batch, and emits bounded
predictions without order authority. Authentication, deadline, message size, and concurrency are closed.
With no verified real pointer the process remains healthy while inference fails closed.
