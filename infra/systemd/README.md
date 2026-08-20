# S5 자율 tick 스케줄러 — RETIRED

LightGBM production은 연구 전용으로 종결됐다. unit 파일은 historical runtime 재현을 위해
보존하지만 새로 설치·활성화하지 않는다. 현재 `s5-tick`은 root, quota credential, provider를
읽기 전에 `S5_TICK=RESEARCH_ONLY`와 종료 코드 1을 반환한다.

## 기존 설치 제거

```bash
systemctl --user disable --now s5-tick.timer
systemctl --user stop s5-tick.service
systemctl --user daemon-reload
```

환경 파일은 자동으로 삭제하지 않는다. operator가 더 이상 필요하지 않음을 확인한 뒤 owner-private
경로에서 별도로 제거한다. 저장소가 credential 파일을 삭제하거나 내용을 출력하지 않는다.

## 보존된 종료 코드

| 코드 | 뜻 | 스케줄러 행동 |
|---|---|---|
| 0 | historical 진척 | 현재 command는 반환하지 않음 |
| 1 | 연구 전용 무진척(정상) | 재시도·provider 호출 금지 |
| 2 | historical 사람 확인 | 현재 command는 반환하지 않음 |

unit의 `SuccessExitStatus=0 1`은 보존된 historical 파일을 실수로 한 번 실행해도 연구 전용 종료를
실패 폭주로 해석하지 않게 한다. timer를 다시 enable하는 권한은 아니다.

```bash
# 상태 확인
systemctl --user list-timers s5-tick.timer
journalctl --user -u s5-tick.service -n 50 --no-pager

# 설치돼 있지 않거나 inactive여야 한다
systemctl --user is-enabled s5-tick.timer
systemctl --user is-active s5-tick.timer
```

## watchdog

```bash
python3 infra/systemd/s5_tick_watchdog.py --root "$S5_SOURCE_ROOT"
```

`NEEDS_HUMAN`이거나 연속 무진척이 임계치를 넘을 때만 출력하고 종료 코드 1을 낸다. 그 외에는
조용하다. 조용하지 않은 watchdog은 곧 무시된다.

cron 대안도 퇴역했다. 향후 data-only collector와 health monitor는 S7.3의 별도 계약·승인 전에는
예약하지 않는다.
