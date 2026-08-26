# P1 Owner input pack·synthetic golden 운영 가이드

## 목적

Team B가 provider나 Spring을 호출하지 않고 one-shot 학습·예측·백테스트를 구현할 수 있도록 Owner가
검증된 local market-data archive에서 sealed input pack을 만든다. 같은 input contract로 Owner adapter를
먼저 검증할 synthetic golden exact-10 bundle도 함께 만든다.

## 생성

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  p1-owner-assets input-pack \
  --archive-root <verified-market-data-archive-root> \
  --archive-manifest-sha256 <exact-sha256> \
  --output-root <owner-private-input-pack-root>

uv run --project workspaces/decision-platform/python-services --frozen \
  p1-owner-assets golden \
  --input-pack-manifest <owner-private-input-pack-root>/manifest.json \
  --output-root <owner-private-golden-root>
```

output root는 기존 파일을 덮어쓰지 않는 새 directory여야 한다. directory는 `0700`, file은 `0600`이며
manifest가 마지막에 게시된다. 다른 manifest로 같은 root를 재사용하지 않는다.

## 검증

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  p1-owner-assets verify-input-pack --root <owner-private-input-pack-root>

uv run --project workspaces/decision-platform/python-services --frozen \
  p1-owner-assets verify-golden --root <owner-private-golden-root>
```

동일한 생성 명령의 두 번째 실행은 같은 manifest SHA와 `noOp=true`를 반환해야 한다. golden은 실제
Team B 결과가 아니며 다음 marker를 바꿀 수 없다.

2026-08-27 content-free local verification은 sealed market-data manifest `e3f26485...`에서 provider
호출 0으로 input manifest `220d2a89...`, synthetic golden manifest `4f00835b...`를 생성했고 두 번째
실행이 모두 no-op임을 확인했다. owner-private 절대경로와 payload는 공개 문서에 기록하지 않는다.

```text
TEAM_B_REAL_ARTIFACT=PENDING_EXTERNAL_TEAM
P1_FINAL=NOT_READY
P1_1_0_0_RELEASED=FALSE
```

## 금지 경계

- source archive와 owner-private output의 Git 추적
- provider/account/order/GDELT/Vertex/KIS Live 호출
- synthetic 결과의 성과 주장 또는 production pointer 활성화
- pickle/PTH/joblib/code-loading artifact 생성
- symlink/hardlink/path traversal과 기존 다른 manifest overwrite
