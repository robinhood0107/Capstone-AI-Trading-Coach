# S5 LightGBM research-only runtime closure

## KR

실측 qualification과 제한된 calibrator probe가 동시에 모든 production gate를 통과하지 못했으므로
LightGBM을 연구·재현 모델로 유지하고 프로그램의 production Signal, stage, activation, daily
inference/publication, RiskDecision, order 경계에서 분리한다.

- 기존 source/feature/diagnostics와 학습 코드는 연구 재현을 위해 보존한다.
- Signal v2의 LightGBM component는 production DB row가 존재하더라도 `ABSTAIN/MISSING_EVIDENCE`다.
- V73의 immutable audit tables/functions는 삭제하지 않고 writer/scheduler/admin 실행 권한만 회수한다.
- production bootstrap, autonomous tick, stage, daily inference/publication, rollback CLI는 credential,
  root, DB, provider 접근 전에
  `RESEARCH_ONLY`로 종료한다.
- 보존된 systemd unit은 retired이며 설치·활성화하지 않는다.
- KRX/KIS/ECOS data-only daily collector와 Market/Data projection은 모델 publication과 분리된 후속
  계약이다. 이 변경은 새 provider 호출이나 public market-data API를 승인하지 않는다.

## EN

The measured qualification and bounded calibrator probes did not satisfy every production gate
simultaneously. LightGBM therefore remains a research and reproducibility model and is disconnected
from production Signal, staging, activation, daily inference/publication, RiskDecision, and orders.

- Existing source, feature, diagnostic, and training code remains available for research reproduction.
- Signal v2 projects the LightGBM component as `ABSTAIN/MISSING_EVIDENCE` even if a production DB row exists.
- V73 immutable audit tables and functions remain, but writer, scheduler, and admin execution grants are revoked.
- Production bootstrap, autonomous tick, staging, daily inference/publication, and rollback CLIs stop as
  `RESEARCH_ONLY` before roots, credentials, databases, or providers are accessed.
- Retained systemd units are retired and must not be installed or enabled.
- A KRX/KIS/ECOS data-only daily collector and Market/Data projection require a separate contract. This change
  authorizes no new provider call or public market-data API.
