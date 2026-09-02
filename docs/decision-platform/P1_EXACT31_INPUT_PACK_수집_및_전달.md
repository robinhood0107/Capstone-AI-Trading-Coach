# P1 exact-31 입력 수집과 Team B 전달

이 문서는 Team B 학습용 `p1-return-engine-input-pack.v1.zip`을 실제 KIS 일봉 시세에서 만들고,
Windows Desktop으로 전달하는 운영 절차다. 모든 명령은 WSL Ubuntu terminal에서
`/home/pjjpj/projects/Capstone-AI-Trading-Coach`를 현재 폴더로 두고 실행한다. Windows CMD나
PowerShell에서는 실행하지 않는다.

## 현재 검증된 receipt

2026-09-02에 아래 결과를 provider-free 검증까지 완료했다.

```text
source bootstrap manifest SHA-256=96c85243de967c63d6d39e43b5b31c20a9600a9d04fc3dcb9de58e8540689c00
normalized bars=23,436 (31 symbols x 756 XKRX sessions)
input manifest SHA-256=8ba0b439c5ff4e39b3136c17d31d648178d7e0064c35adead46837f953c5fafd
ZIP SHA-256=baac0ec2a3ea0df13a9451a65ee7128c69ecdbd59456eca638fbee6dd9695d42
ZIP file count=7
```

Team B에는 ZIP과 **input manifest SHA-256**을 함께 전달한다. ZIP SHA-256은 전송 파일 자체를
확인하는 보조 값이다. provider credential, raw response, account, balance, order 데이터는 ZIP과
Git에 넣지 않는다.

## 1. provider-free 계획 확인

아래 명령은 외부 호출이 없다. 현재 exact-30 universe와 고정 ETF `132030`을 결합해 exact-31,
756-session 요청 창과 cap을 계산한다.

```bash
cd /home/pjjpj/projects/Capstone-AI-Trading-Coach

UNIVERSE="$PWD/deploy/p1/.state-app/artifacts/universe-20260828/universe_manifest.json"

./capstone market-data bootstrap plan \
  --universe-manifest "$UNIVERSE" \
  --end-session 2026-09-01 \
  --session-count 756
```

정상 계획은 31개 membership, 248개 일봉 window, KIS daily 최대 496회, token 최대 1회,
retry 1회를 출력한다. `planSha256`은 아래 approval packet과 정확히 같아야 한다.

## 2. 명시 승인과 KIS read-only 수집

이 단계부터 KIS Live 일봉 조회 `FHKST03010100`를 사용한다. 실행 전 operator는 아래 범위를
명시적으로 승인한다.

```text
KIS daily maximum=496 physical calls
KIS token maximum=1 physical call
KRX membership physical calls=0 (sealed universe 재사용)
retry=transient failure마다 최대 1회
account/balance/order/GDELT=0 calls
```

`.env`에는 local-only `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`이 있어야 한다. 값은 terminal,
문서, Git에 출력하거나 기록하지 않는다. packet은 canonical JSON **마지막 newline 포함** 조건을
강제하므로 `printf`로 직접 만들지 말고 다음 Python block을 사용한다.

```bash
cd /home/pjjpj/projects/Capstone-AI-Trading-Coach

STATE=/home/pjjpj/.local/share/capstone-p1
PACKET="$STATE/p1-kis-input-approval-20260902.json"
ARCHIVE="$STATE/p1-exact31-bootstrap-20260901"
UNIVERSE="$PWD/deploy/p1/.state-app/artifacts/universe-20260828/universe_manifest.json"

mkdir -p "$STATE"
chmod 700 "$STATE"

python3 - "$PACKET" <<'PY'
import json
import sys
from pathlib import Path

packet = {
    "approvalId": "P1-KIS-INPUT-20260902",
    "contractId": "p1-automation-market-bootstrap-execution.v1",
    "kisMode": "live",
    "krxMembershipPhysicalCalls": 0,
    "planSha256": "7d20eea73f22efa67b3604a37224516c4e4c44193e7bd109245429d81ebed688",
    "providerCaps": {
        "kisDaily": 496,
        "kisToken": 1,
        "krxMembership": 5,
        "retry": 1,
    },
}
Path(sys.argv[1]).write_text(
    json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

chmod 600 "$PACKET"

./capstone market-data bootstrap run \
  --packet "$PACKET" \
  --universe-manifest "$UNIVERSE" \
  --end-session 2026-09-01 \
  --session-count 756 \
  --output-root "$ARCHIVE"
```

성공 operation은 재호출하지 않는다. 실패하면 같은 command를 반복하지 말고 terminal 오류와
content-free receipt만 확인한다. `automation bootstrap packet must be canonical JSON`은 packet의
마지막 newline이 없는 경우이며, 위 Python block으로 packet만 다시 만든 뒤 재시도할 수 있다.
이 오류는 KIS client 생성 전의 local validation 오류이므로 KIS physical call은 0이다.

## 3. archive 검증과 Desktop ZIP 생성

수집이 성공한 뒤에는 아래 명령이 KIS를 호출하지 않는다. input pack directory는 Linux owner-only
state root에 남기고, 전달용 ZIP만 Windows Desktop에 쓴다.

```bash
cd /home/pjjpj/projects/Capstone-AI-Trading-Coach

STATE=/home/pjjpj/.local/share/capstone-p1
ARCHIVE="$STATE/p1-exact31-bootstrap-20260901"
PACK="$STATE/p1-return-engine-input-pack-20260901"
ZIP=/mnt/c/Users/pjjpj/Desktop/P1-TeamB-Input/p1-return-engine-input-pack.v1.zip
ARCHIVE_SHA="$(sha256sum "$ARCHIVE/manifest.json" | awk '{print $1}')"

./capstone market-data bootstrap validate \
  "$ARCHIVE" \
  --manifest-sha256 "$ARCHIVE_SHA"

uv run --project workspaces/decision-platform/python-services --frozen \
  python -m app.p1_owner.assets input-pack \
  --bootstrap-archive-root "$ARCHIVE" \
  --archive-manifest-sha256 "$ARCHIVE_SHA" \
  --output-root "$PACK" \
  --zip-output "$ZIP"

uv run --project workspaces/decision-platform/python-services --frozen \
  python -m app.p1_owner.assets verify-input-pack \
  --root "$PACK"

echo "INPUT_MANIFEST_SHA256=$(sha256sum "$PACK/manifest.json" | awk '{print $1}')"
echo "ZIP_SHA256=$(sha256sum "$ZIP" | awk '{print $1}')"
```

`input-pack`은 approved automation bootstrap archive를 읽어 7개 file ZIP을 만든다.
`daily_ohlcv.parquet`은 RAW_CLOSE OHLCV, `universe.parquet`은 exact-31, macro snapshot은 empty
bounded snapshot이다. 모델 feature는 price-only이며 macro/news는 학습 feature가 아니다.

## 4. Team B 전달과 확인

Windows Desktop의 `P1-TeamB-Input/p1-return-engine-input-pack.v1.zip`을 Git 밖 local folder로
전달한다. 수신자는 [Team B exact-31 최소 구현 요청서](../handoff/P1_TEAM_B_최종_통합_요청서.md)의
input manifest SHA-256과 ZIP SHA-256을 먼저 대조한다.

수신자가 전달할 결과는 `deploy/p1/seed/team-b/`의 exact-10 artifact와
`p1-return-engine-manifest.v3.json`이다. Owner는 수신 뒤에만 `./capstone up`의 one-shot import,
daily Rule+LSTM inference, KIS Mock gate를 진행한다. synthetic golden 또는 legacy preview는 real Team B
artifact를 대체하지 않는다.

## 문제 해결

| 오류 또는 상태 | 조치 |
|---|---|
| `automation bootstrap packet must be canonical JSON` | 2절 Python block으로 packet을 다시 만들고 `chmod 600`을 적용한다. 마지막 newline이 필요하다. |
| archive output root exists | 다른 새 owner-only output directory를 선택한다. 기존 archive를 덮어쓰지 않는다. |
| KIS 수집 중 실패 | 동일 command를 반복하지 않는다. 첫 실패의 typed error를 보존하고 새 승인 또는 원인 확인 뒤 진행한다. |
| `verify-input-pack` 실패 | ZIP을 전달하지 않는다. manifest SHA와 각 file hash를 먼저 다시 확인한다. |
| Team B seed 없음 | `CAPSTONE_ACTIVE_MODELS=0`을 유지한다. synthetic 결과를 production daily pointer로 쓰지 않는다. |
