# Capstone 1.0.0 handoff 시작

## 1. 최종 프로그램 목표

한 명의 사용자가 투자 원칙을 정하고, 실제 근거를 확인하며, 모의 주문과 자동운용을 안전하게 검토하고,
결과를 학습일지로 남길 수 있는 재현 가능한 로컬 프로그램을 완성합니다.

## 2. Owner가 이미 준비한 것

Owner는 exact-56 Spring API, Team A exact-33 acceptance, Team B exact-31 입력과 exact-10 결과 계약,
provider-free 기본 Compose, synthetic golden과 실제 결과의 구분을 준비했습니다.

## 3. 수정할 것

Team A는 Dashboard production UI만, Team B는 Return Engine 결과 코드만 수정합니다. 각 팀의 상세 범위는
[Team A](team-a/README.md)와 [Team B](team-b/README.md) 문서를 따릅니다.

## 4. 실행 명령

```bash
git fetch origin main
git switch main
git pull --ff-only origin main
./capstone doctor
./capstone up
./capstone smoke
./capstone status
```

## 5. 완료 테스트

Team A는 live Spring exact-33과 Playwright skip 0을, Team B는 exact-10·두 번 실행 결정성·독립 metric을
각자 증명합니다. Owner는 결과 수신 뒤 코드 보강 없이 계약 검증과 통합만 수행합니다.

## 6. 제출할 파일·commit·OCI digest

각 팀은 PR URL, commit SHA, dependency lock SHA-256, 테스트 결과를 제출합니다. Team A는 production image
digest, Team B는 restricted GHCR immutable OCI digest와 artifact manifest SHA-256도 제출합니다.

## 7. 하지 말아야 할 것

credential이나 원본 provider 응답을 커밋하지 말고, 새 API를 임의로 만들거나 synthetic 결과를 실제
성과로 표시하지 마세요. Team A/B에 대한 메시지는 사용자가 직접 전달하며 이 저장소가 자동 전송하지 않습니다.
