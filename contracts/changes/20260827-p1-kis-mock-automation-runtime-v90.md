# P1 KIS Mock automation runtime V90

## KR

Owner-First full-app v3의 control plane을 실제 activation과 분리된 persistent runtime 경계로 확장한다.

- KIS 매수가능 projection은 무미수 `nrcvb_buy_qty`와 `nrcvb_buy_amt`만 사용한다.
- V90은 `decision_automation_runtime`, owner/session claim, checkpoint, processed tick, sanitized reservation, append-only event와 CAS transition을 추가한다.
- `./capstone mock readiness|start|stop`은 default DISARMED, certification/source/REAL_TEAM_B/account/Kill Switch gate를 서버에서 재검증한다.
- next-session script는 pinned XKRX 08:55 KST의 transient user-systemd one-shot만 생성한다.
- root OpenAPI exact-56과 Team A exact-33, 기존 V1/V2 bytes는 변경하지 않는다.
- provider/account/order, KIS Live와 GDELT outbound physical call은 이 변경에서 0이다.

production Vertex BUY-veto transport는 아직 `ABSTAIN_NOT_CONFIGURED`다. 따라서 runtime infrastructure 구현은 KIS Mock certification, automatic BUY E2E 또는 1.0.0 release 증거가 아니다.

## EN

This change extends the Owner-First v3 control plane with a persistent runtime boundary while keeping physical activation separate.

- The buyable projection uses only the official no-margin fields.
- V90 adds a dedicated runtime role, owner/session claim, checkpoint, processed-tick identity, sanitized reservation, append-only event, and CAS transitions.
- Content-free readiness/start/stop revalidate certification, source, REAL_TEAM_B, account, and Kill Switch gates.
- The next-session helper creates only a transient user-systemd one-shot at 08:55 KST on the pinned XKRX calendar.
- Root OpenAPI exact-56, Team A exact-33, and historical V1/V2 bytes remain unchanged.
- Provider, account, order, KIS Live, and GDELT outbound physical calls are zero in this change.

The production Vertex BUY-veto transport remains `ABSTAIN_NOT_CONFIGURED`; this is not certification, automatic-BUY E2E, or release evidence.
