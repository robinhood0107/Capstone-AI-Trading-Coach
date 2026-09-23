# S1.4X 승격 CI의 역사적 reference 조회

MARS `develop → main` 초안 PR에서 S1.4X workflow가 역사적 `referenceBaseCommit`을
현재 main의 조상으로 가정해 실패했다. 그 SHA는 저장소 원격에서 직접 가져올 수
있지만 현재 main의 조상은 아니다. 기준 lock과 S1.4X 소스·fixture는 바꾸지 않는다.

workflow는 lock에 기록된 정확한 SHA를 원격에서 가져와 그 커밋의 좁은 변경 경로와
reference snapshot 자체를 검증한다. 현재 생산 Python 잠금 파일의 전체 hash를
이번 MARS dependency 전환 값으로 갱신하고, numpy 2.5.1/scipy 1.18.0 고정도
같은 자리에서 재확인한다. 연구 reference의 JAX/fixture/기준 함수 검사는 그대로다.

S1.4R의 host-only 두 테스트는 연구 브랜치가 제품 코드를 바꾸지 않는지를 본다.
제품 변경을 묶어 승격하는 `develop → main`에서는 해당 두 테스트를 제외하고
나머지 native correctness를 실행한다. OCI snapshot job은 원래의 전체 테스트를
계속 실행한다. 연구 소스나 테스트 파일은 수정하지 않는다.
