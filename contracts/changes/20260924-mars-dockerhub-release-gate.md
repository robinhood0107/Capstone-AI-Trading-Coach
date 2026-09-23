# MARS 공개 이미지 발행 게이트

공개 Docker Hub 저장소는 `pjjpjj111/mars-demo`와
`pjjpjj111/mars-full`이다. 두 저장소는 외부에서 읽을 수 있어야 한다.
`MARS Docker Hub release`는 같은 저장소의 `develop → main` PR이 실제 병합된
이벤트에서만 실행한다. 병합 커밋이 현재 `main` HEAD이고 두 번째 부모가 PR의
`develop` head인지, 주요 main 승격 CI가 모두 성공했는지 확인한 뒤에만 빌드한다.

`deploy/p1/mars-release-gate.json`의 `ready`는 제품 기동·권한·비용·KIS
사용자별 운용·대사·두 사용자 격리·부하 게이트가 끝날 때까지 `false`다.
`false`인 동안 Docker Hub 로그인과 발행에 도달할 수 없다. 마지막 승격 PR에서
증거를 갖추고 `true`로 바꾼다. 현재 `version`은 첫 MARS 버전 후보이며,
기존 P1의 `1.0.0` 릴리스 권위를 바꾸지 않는다.

릴리스는 한 병합 커밋에서 API, PostgreSQL, Redis와 제품별 웹 이미지를 빌드한다.
각 제품 저장소의 불변 태그는 `v<version>-<merge SHA 12자리>-{api,web,postgres,redis}`다.
스캔과 SBOM 생성 후에만 Docker Hub PAT를 사용한다. 업로드한 digest를 다시 받아
로컬 이미지 ID와 source revision을 검증하고, 두 제품의 digest manifest와 SBOM을
GitHub Release에 첨부한다. 재시도 시 기존 태그가 동일 이미지 ID가 아니면 중단한다.
NAS에서 이미지를 pull하거나 Compose를 바꾸는 단계는 포함하지 않는다.

`develop → main` PR에는 별도의 `MARS product image build` 검사를 둔다. 같은
후보 커밋에서 API·PostgreSQL·Redis와 DEMO/FULL 웹 번들을 실제로 빌드하고
제품 표시값과 source revision을 확인한 뒤 로컬 이미지 ID 목록을 Actions artifact로
남긴다. 이 단계는 Docker Hub에 로그인하거나 이미지를 발행하지 않는다.
발행 워크플로는 이 빌드 검사의 성공도 요구한다.
