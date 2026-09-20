# ADR-027: S1.4X 격리 Scala/Haskell 수치 parity 연구

- 상태: `accepted`
- 제안일: 2026-07-18
- 범위: `workspaces/decision-platform/research/s1-4x-numeric-parity/`
- 추적: GitHub Issue `#26`

## KR 요약

S1.4X는 S1.4의 production Python 구현과 S1.4R의 NumPy/JAX 연구 공식을 바꾸지 않고,
동결된 20개 계산을 Scala와 Haskell의 독립 process로 재현하는 비생산 연구다. Gate 0은
governance와 toolchain provenance만 제안하며, Gate 1 contract가 별도로 병합되기 전에는
언어별 구현·fixture·workflow를 추가하지 않는다. correctness 전체 통과 전에는 성능을
평가하지 않으며, 결과가 좋아도 production migration이나 언어 선택을 승인하지 않는다.

## EN summary

S1.4X is a non-production study that reproduces the 20 frozen S1.4 and S1.4R
calculations in independent Scala and Haskell processes without changing the
production Python implementation or the NumPy/JAX research formulas. Gate 0
proposes governance and toolchain provenance only. Language implementations,
fixtures, and workflows require a separately merged Gate 1 contract. Performance
is evaluated only after all correctness gates pass, and the result does not
authorize a production migration or language selection.

## 맥락

S1.4의 11개 수익률·리스크 함수와 S1.4R의 9개 고급 리스크 함수는 Python/NumPy를
기준 구현으로 사용한다. Scala와 Haskell은 순수 코어의 경계, 오류 의미론, 재현성,
same-host 성능을 비교할 가치가 있지만, 이를 production 경로에 직접 연결하면
API·의존성·배포·workspace 계약이 함께 확대된다.

따라서 계산 공식과 fixture가 먼저 동결된 뒤, production과 분리된 process protocol로만
두 언어를 비교한다. S1.4 11개는 production Python/NumPy, S1.4R 9개는
Python/NumPy reference와 JAX CPU+x64 parity를 oracle로 사용한다. 이 oracle들은
Scala/Haskell candidate가 실행 중 호출하거나 embed하는 runtime dependency가 아니다.

## 결정

### 1. 승인 상태와 gate

상태 전이는 다음 순서를 따른다.

```text
proposed PR
→ 사용자 review
→ 별도 exact 승인
→ accepted 상태 commit
→ required checks 재통과
→ exact merge 승인
→ main merge
```

이 문서가 `proposed`인 동안 bootstrap 승인은 Issue, 문서 변경, branch, commit,
non-force push와 proposed PR 생성까지만 허용한다. `accepted` 전환과 merge는 별도
사용자 승인 없이는 수행하지 않는다.

Gate 0이 `accepted` 상태로 병합된 뒤에도 구현을 바로 시작하지 않는다. 별도 Gate 1 PR이
language-neutral exchange schema, 20개 함수 ID, 19+13 error registry, reference lock,
oracle, canonical fixture, property plan, benchmark plan과 provenance validator를
동결해 병합한 뒤에만 Scala/Haskell source와 correctness workflow를 추가할 수 있다.

### 2. 격리 경계와 exchange

- 실험은 `workspaces/decision-platform/research/s1-4x-numeric-parity/` 안에만 둔다.
- Scala와 Haskell은 각각 독립 process로 실행하며 같은 fixture root를 읽는다.
- Gate 1 exchange는 canonical JSON과 Float64 little-endian binary 및 SHA-256 manifest를
  함께 사용한다.
- S1.4 11개의 Python/NumPy와 S1.4R 9개의 Python/NumPy·JAX CPU+x64 결과가 각 범위의
  oracle이다. candidate가 Python/JAX를 호출하거나 Python embedding, FFI, JNI,
  native extension, gRPC 또는 HTTP service로 계산을 위임하는 것은 금지한다.
- root `contracts/`, `contracts/changes/`, OpenAPI/JSON Schema, public REST/gRPC/proto,
  RiskEngine API, production dependency graph와 다른 팀원 workspace는 변경하지 않는다.
  S1.4X exchange는 Decision Platform 내부 연구 protocol이며 workspace 간 계약이 아니다.

#### 2.1 upstream project runtime lock amendment

`pyproject.toml` 경로는 production/research reference source와 workflow trigger에 계속 포함하되,
파일 전체 bytes를 수치 oracle identity로 사용하지 않는다. reference source와 source-tree manifest의
해당 entry는 다음 필드만 canonical JSON으로 투영한 SHA-256을 사용한다.

- `project.requires-python`, `project.dependencies`, `project.optional-dependencies`,
  dependency와 무관한 항목만 허용하는 `project.dynamic`
- `build-system`, `dependency-groups`, `tool.uv`, `tool.hatch.build`
- dependency 배열은 순서가 의미를 바꾸지 않으므로 canonical byte 순서로 정규화

`project.scripts`·`project.gui-scripts`·`project.entry-points`와 description/version 같은 package
metadata, Ruff/Pytest/mypy 설정은 수치 runtime projection에서 제외한다. 따라서 CLI entrypoint나
정적 분석 설정만 추가해도 frozen numeric reference가 바뀐 것으로 판정하지 않는다. 반면 dependency,
Python 범위, build backend, uv/hatch runtime 설정 변경은 fail-closed한다. resolved production/research
`uv.lock`과 실제 Python/NumPy/JAX source·fixture는 계속 byte-exact SHA-256으로 잠근다.

### 3. correctness와 stable error

비교 대상은 S1.4 11개와 S1.4R 9개, 총 20개 함수다. acceptance는 다음과 같다.

- candidate-vs-oracle과 Scala-vs-Haskell correctness를 모두 통과한다.
- 부호, stable error code와 precedence, finite 여부는 exact로 비교한다.
- 출력의 `-0.0`은 `0.0`으로 canonicalize하고 NaN/Inf는 허용하지 않는다.
- small/paper fixture는 `rtol=1e-12`, `atol=1e-12`를 사용한다.
- large/property fixture는 `rtol=1e-10`, `atol=1e-12`를 사용한다.
- mismatch가 하나라도 있으면 해당 candidate는 성능 평가 전에 탈락한다.

S1.4 공개 SSOT·emitted set의 19개와 S1.4R registry의 13개 stable error를 Gate 1의
language-neutral registry로 materialize하고 이름·의미·precedence를 모두 동결한다.
20개 process endpoint가 표현할 수 있는 모든 public semantic error는 exact code로
비교한다. defensive-internal case와 Python object-model 전용 case는
registry/static/reference test로 검증한다. 이를 억지로 동적 trigger하기 위한 21번째
endpoint, tagged fake input 또는 요청한 `errorCode`를 그대로 반환하는 shortcut은 만들지
않는다. 32개 모두를 process에서 trigger해야 한다는 요구가 생기면 Gate 1 구현으로
우회하지 않고 이 contract를 다시 review한다.

### 4. 순수 코어와 shell

- stable Scala가 언어 전체의 purity를 보증한다고 주장하지 않는다. Scala numeric core는
  외부에서 관찰 가능한 I/O, network, clock, random, logging, mutable global/cache와
  input mutation을 금지하고 process shell과 분리한다.
- Haskell은 pure numeric core와 JSON/binary/process를 담당하는 `IO` shell을 module
  경계로 분리한다.
- candidate-authored source에서 inter-language FFI/JNI, native loading, Python embedding과
  계산 위임을 금지한다. dependency의 native edge와 module safety는 Gate 1의 명시적
  manifest와 감사 규칙으로 검증한다.
- Scala nightly, preview/experimental capture checking, LLVM backend, fast-math와
  architecture-specific SIMD는 correctness 또는 authoritative benchmark의 필수 조건이
  아니다.

### 5. 동결 toolchain

- JVM: Temurin JDK `25.0.3+9` LTS를 Scala authoritative runtime으로 사용한다.
- Scala: Scala `3.8.4`, Scala CLI `1.15.0`을 사용한다. Scala 3.8.4는 current stable
  feature release이며 LTS로 표현하지 않는다.
- Scala 품질 도구: Scalafmt `3.11.4`, Scalafix `0.14.7`을 exact hard gate로 사용한다.
- Haskell authoritative: Stackage `LTS 24.50`, GHC `9.10.3`, Stack `3.11.1`을
  2026-07-18 source audit 기준 latest tested-together Stackage stable baseline으로
  사용한다.
- Haskell 품질 도구: HLint `3.10`, stylish-haskell `0.15.1.0`을 exact hard gate로
  사용한다.
- Haskell compatibility: GHC `9.14.1`은 non-scoring이며 dependency solve 뒤 full
  correctness만 수행한다.
- 배포 관리자: GHCup `0.2.6.2`의 exact-version Stack 설치 channel을 사용한다.

authoritative Haskell lane은 “최신 GHC”가 아니라 위 날짜에 동결한 latest
tested-together Stackage stable baseline으로 표현한다. GHC 9.14.1 lane은 호환성 정보만
제공하며 authoritative score에 섞지 않는다.

#### GHCup 관리 Stack provenance

```text
stackPolicy: GHCup-managed exact-version installation
stackInstallCommand: ghcup install stack 3.11.1

ghcupToolId: GHCUP_0_2_6_2_LINUX_X86_64
ghcupVersion: 0.2.6.2
ghcupReleaseUri: https://github.com/haskell/ghcup-hs/releases/tag/v0.2.6.2
ghcupAssetUri: https://github.com/haskell/ghcup-hs/releases/download/v0.2.6.2/x86_64-linux-ghcup-0.2.6.2
ghcupAssetSha256: 9ed5da5449b48043a0d17e767c05d2ef585e25a639bb934329496c6d2fad9cf8

ghcupMetadataCommit: 0341867f2d419567cf42ea6931e031b00ab3a922
ghcupMetadataUri: https://github.com/haskell/ghcup-metadata/commit/0341867f2d419567cf42ea6931e031b00ab3a922
ghcupMetadataRawUri: https://raw.githubusercontent.com/haskell/ghcup-metadata/0341867f2d419567cf42ea6931e031b00ab3a922/ghcup-0.1.0.yaml
ghcupMetadataRawSha256: 49c8a036ce399587205a11ac24e73465cadc5f3232e9418a9d87f4b7f746c4ec

stackArchiveUri: https://downloads.haskell.org/~ghcup/unofficial-bindists/stack/3.11.1/stack-3.11.1-linux-x86_64.tar.gz
stackArchiveSha256: ca3cc5e89d87d1b85594a866de4062671d19ec039cd2401df70d4ccff03ffed9

stackBinPathId: GHCUP_STACK_3_11_1
stackBinResolver: ghcup whereis stack 3.11.1
stackBinSha256: 923dbd137756652c67b376e2447c655b87fcc373f4d104b5073bca913471ecbe
stackNumericVersion: 3.11.1

upstreamStandaloneAssetUri: https://github.com/commercialhaskell/stack/releases/download/v3.11.1/stack-3.11.1-linux-x86_64-bin
upstreamStandaloneAssetSha256: 67c66e918801c41ae4d286b1c91f9124f691c1c7d56071b53889cf4a5c667550
upstreamStandaloneAssetRole: comparison-only-not-installed-provenance

verifiedAt: 2026-07-18T05:52:12Z
```

검증된 설치 chain은 `GHCup metadata snapshot → GHCup Stack archive → installed Stack
binary → stack --numeric-version`이다. metadata raw document와 각 artifact를 SHA-256으로
hash-lock했다. 로컬 절대경로는 계약이 아니며 `GHCUP_STACK_3_11_1`과
`ghcup whereis stack 3.11.1`로 해석한다.

archive URI의 `unofficial-bindists`는 Stack upstream asset이 아니라 GHCup distribution
channel이 관리하는 별도 bindist라는 뜻이다. upstream standalone SHA는 비교 자료일 뿐
설치 provenance로 사용하지 않는다. 이 기록은 immutable snapshot과 hash 검증이며
GPG-strict 검증을 주장하지 않는다. 같은 upstream version의 bindist revision이 생겨도
자동 채택하지 않고 별도 contract review를 거친다.

### 6. benchmark와 scorecard

full benchmark는 모든 correctness gate가 통과한 뒤 continuous run 승인 packet 범위
안에서 별도 중간 승인 없이 local quiet-host full matrix로 자동 실행한다. 반복 benchmark
GitHub workflow를 Gate 1 이후 추가한다면 `workflow_dispatch` 전용이며 PR required
check가 아니다.

- 같은 host, fixture, CPU affinity와 single-thread 조건을 사용한다.
- numeric kernel과 JSON/binary decode·encode·report I/O shell을 분리해 측정한다.
- Kupiec/Christoffersen 통계는 batch 단위로만 측정한다.
- cross-host speedup, 서로 다른 compiler/fixture/thread 조건의 직접 우열과 benchmark
  결과에 따른 자동 migration을 주장하지 않는다.

scorecard 가중치는 다음과 같이 고정한다.

| 항목 | 점수 |
| --- | ---: |
| correctness | 35 |
| purity/auditability | 20 |
| reproducibility | 15 |
| performance | 15 |
| maintainability | 10 |
| integration fit | 5 |

## 제외 범위

Gate 0에는 다음을 포함하지 않는다.

- Scala/Haskell source, Gate 1 schema·registry·oracle·fixture·benchmark plan 또는
  workflow
- production Python 교체와 production import/runtime/dependency 변경
- root API/OpenAPI/cross-workspace schema 변경
- candidate-authored/inter-language FFI/JNI/native extension/Python embedding/gRPC/HTTP
- Scala nightly/capture-checking gate, LLVM 필수화와 cross-host 성능 주장
- correctness 전 full benchmark와 장시간 PR-required benchmark

## 결과와 트레이드오프

canonical fixture와 exact error contract로 언어별 drift를 빠르게 탐지할 수 있고,
process 격리 덕분에 production coupling을 만들지 않는다. 반면 같은 공식을 여러 언어로
유지하고 exact toolchain을 보존하는 비용이 생긴다. scorecard는 연구 비교 자료일 뿐
production migration 또는 언어 선택 승인이 아니다. production 전환이 필요하면 API,
운영, dependency, 배포와 팀 계약을 포함한 별도 ADR과 승인을 거쳐야 한다.

## Rollback

`workspaces/decision-platform/research/s1-4x-numeric-parity/` subtree와 S1.4X 전용
workflow를 제거해도 production behavior, API, dependency graph, root contract와 다른
workspace 산출물이 바뀌지 않아야 한다. 이 조건을 만족하지 못하면 격리 경계 위반으로
판정한다.

## 근거

- [Scala 3.8.4 release](https://www.scala-lang.org/news/3.8.4/)
- [Scala CLI 1.15.0 release](https://github.com/VirtusLab/scala-cli/releases/tag/v1.15.0)
- [Scalafmt 3.11.4 release](https://github.com/scalameta/scalafmt/releases/tag/v3.11.4)
- [Scalafix 0.14.7 release](https://github.com/scalacenter/scalafix/releases/tag/v0.14.7)
- [Temurin JDK 25.0.3+9 release](https://github.com/adoptium/temurin25-binaries/releases/tag/jdk-25.0.3%2B9)
- [Haskell downloads](https://www.haskell.org/downloads/)
- [GHCup installation and distribution policy](https://www.haskell.org/ghcup/install/)
- [GHCup and Stack integration](https://www.haskell.org/ghcup/guide/stack/)
- [GHCup metadata snapshot](https://github.com/haskell/ghcup-metadata/commit/0341867f2d419567cf42ea6931e031b00ab3a922)
- [Stackage LTS 24.50](https://www.stackage.org/lts-24.50)
- [GHC 9.10.3 release](https://www.haskell.org/ghc/download_ghc_9_10_3.html)
- [GHC 9.14.1 release](https://www.haskell.org/ghc/download_ghc_9_14_1.html)
- [Stack 3.11.1 release](https://github.com/commercialhaskell/stack/releases/tag/v3.11.1)
- [HLint 3.10 package](https://hackage.haskell.org/package/hlint-3.10)
- [stylish-haskell 0.15.1.0 in LTS 24.50](https://www.stackage.org/lts-24.50/package/stylish-haskell-0.15.1.0)
