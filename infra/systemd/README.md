# S5 자율 tick 스케줄러

`s5-tick`은 상태를 읽고 그 단계의 남은 승인 작업만 하고 상태를 쓰고 끝난다. 멱등하며 예산을
인지한다. 중간 종료가 안전한 것은 progress journal이 이미 query 단위 멱등성을 보장하기 때문이다.

## 설치 (WSL systemd user)

```bash
mkdir -p ~/.config/systemd/user ~/.config/s5-tick
cp infra/systemd/s5-tick.service infra/systemd/s5-tick.timer ~/.config/systemd/user/
```

`~/.config/s5-tick/tick.env`에 승인된 root와 quota backend credential을 둔다. 이 파일은 저장소
밖에 있어야 하고 `0600`이어야 한다. provider API key는 저장소 루트 `.env`가 이미 갖고 있으므로
여기에 다시 적지 않는다.

```bash
install -m 600 /dev/null ~/.config/s5-tick/tick.env
cat >> ~/.config/s5-tick/tick.env <<'EOF'
S5_SOURCE_ROOT=/home/<user>/.local/share/capstone-ai-trading-coach/s5-production-main
REDIS_HOST=127.0.0.1
REDIS_PORT=<host port>
REDIS_DB=0
REDIS_PASSWORD=<value>
EOF
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now s5-tick.timer
```

로그아웃 뒤에도 돌게 하려면 linger를 켠다: `loginctl enable-linger $USER`.

## 종료 코드와 watchdog

| 코드 | 뜻 | 스케줄러 행동 |
|---|---|---|
| 0 | 진척 | 다음 주기에 계속 |
| 1 | 무진척(정상) | 다음 주기에 재시도 |
| 2 | 사람이 봐야 함 | watchdog이 알린다 |

unit이 `SuccessExitStatus=0 1`로 1을 정상으로 본다. 무진척은 실패가 아니라 "이번 주기에는 할 일이
없었다"는 뜻이므로 이를 실패로 취급하면 watchdog이 계속 울린다.

```bash
# 상태 확인
systemctl --user list-timers s5-tick.timer
journalctl --user -u s5-tick.service -n 50 --no-pager

# 지금 한 번 돌리기
systemctl --user start s5-tick.service
```

## watchdog

```bash
python3 infra/systemd/s5_tick_watchdog.py --root "$S5_SOURCE_ROOT"
```

`NEEDS_HUMAN`이거나 연속 무진척이 임계치를 넘을 때만 출력하고 종료 코드 1을 낸다. 그 외에는
조용하다. 조용하지 않은 watchdog은 곧 무시된다.

## cron 대안

systemd user가 없는 환경에서는 같은 명령을 cron으로 돌린다. 종료 코드 1이 실패로 보고되지 않도록
감싼다.

```cron
*/30 * * * * cd <repo>/workspaces/decision-platform/python-services && \
  set -a && . $HOME/.config/s5-tick/tick.env && set +a && \
  $HOME/.local/bin/uv run --frozen s5-tick; [ $? -le 1 ]
```
