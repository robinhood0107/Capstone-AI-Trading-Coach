# S1.4X 승격 CI의 역사적 reference 조회

MARS `develop → main` 초안 PR에서 S1.4X workflow가 역사적 `referenceBaseCommit`을
현재 main의 조상으로 가정해 실패했다. 그 SHA는 저장소 원격에서 직접 가져올 수
있지만 현재 main의 조상은 아니다. 기준 lock과 S1.4X 소스·fixture는 바꾸지 않는다.

초기 수정에서는 lock의 정확한 SHA를 원격에서 직접 가져오도록 했지만, GitHub Actions의
새 checkout에서는 그 객체가 제공되지 않았다. 기준 lock의 원문 hash를 고정하고,
역사적 생산 Python `pyproject.toml`과 `uv.lock` 두 파일의 원문을
`contracts/fixtures/s1-4x-reference-runtime/`에 별도 보존한다. CI는 현재 checkout의
격리 복사본에 그 두 파일을 놓고 기준 lock의 31개 source와 4개 source tree의
hash를 검증한 뒤 기존 262개 회귀를 실행한다. 현재 생산 Python 잠금 파일의 전체 hash를
이번 MARS dependency 전환 값으로 갱신하고, numpy 2.5.1/scipy 1.18.0 고정도
같은 자리에서 재확인한다. 연구 reference의 JAX/fixture/기준 함수 검사는 그대로다.

이 복사본은 원래 커밋 SHA를 재현했다고 주장하지 않는다. 원래 SHA는 역사적 식별자로
그대로 두고, 실제 검증 권위는 lock이 기록한 파일별 내용과 fixture hash에 둔다.

S1.4R의 host-only 두 테스트는 연구 브랜치가 제품 코드를 바꾸지 않는지를 본다.
제품 변경을 묶어 승격하는 `develop → main`에서는 해당 두 테스트를 제외하고
나머지 native correctness를 실행한다. OCI snapshot job은 원래의 전체 테스트를
계속 실행한다. 연구 소스나 테스트 파일은 수정하지 않는다.
