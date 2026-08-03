# Capstone RAG local root

이 디렉터리는 Decision Platform RAG의 저장소 상대 approved root다. 애플리케이션과
테스트는 저장소 루트를 먼저 확정한 뒤 아래 상대 경로를 해석하며, `/mnt/c`, 홈 디렉터리,
현재 작업 디렉터리 추론 또는 사용자 입력 절대경로를 대신 사용하지 않는다.

| 경로 | Git | 용도 |
|---|---|---|
| `manifests/` | 추적 | corpus/model/evidence의 bounded metadata, SHA-256, provenance |
| `source-cards/` | 추적 | 프로젝트가 직접 작성하고 공개 가능한 source card |
| `eval/` | 추적 | 공개·합성·비식별 평가 fixture와 gold metadata |
| `reports/` | 추적 | secret/PII/raw 질문이 없는 bounded 평가 보고서 |
| `evidence/` | payload 제외 | 공식 원천의 bounded 캡처·PDF·스크린샷; manifest만 추적 |
| `secrets/` | payload 제외 | 배포 전에 별도 승인된 secret-file 위치; 값·존재 여부 비노출 |
| `runtime/` | payload 제외 | parse/index/cache/temporary generation 산출물 |
| `tmp/` | 제외 | 원자 write 중간 파일 |

모든 파일 I/O는 repository root canonicalization 뒤 이 경계 안의 정규 파일만
directory-fd, `O_NOFOLLOW`, 크기 상한과 exclusive-create로 처리한다. symlink, hardlink,
magic-link, path traversal, 기존 파일 덮어쓰기와 broad recursive delete는 허용하지 않는다.

이 디렉터리가 Git 안에 있다는 사실은 evidence 원문, 모델 가중치, API key 또는 provider
응답을 커밋해도 된다는 뜻이 아니다.

## S4.7D 로컬 문서와 OCR 경계

S4.7D는 기존 exact-30을 바꾸지 않고 OA corpus와 owner-private 문서를 후속 generation으로
추가한다. RAG는 출처 검색·설명·인용만 담당하며 Signal, RiskDecision, 주문 feature/hash에는
연결하지 않는다. 현재 safe parser/OCR, v2 DB/RLS/API skeleton, owner-local BGE staging과
control-record import/delete/cache-clean 경계는 구현됐다. OA112 materializer, public bundle pointer,
authorized retrieval와 Vertex generation은 아직 활성화되지 않았다. 따라서
`S4_7D_RUNTIME=PARTIAL_LOCAL_OWNER_STAGING`; full bundle 전 v2 ask는 `CORPUS_NOT_READY`로,
full bundle 뒤 Vertex live gate가 닫혀 있으면 `GENERATION_UNAVAILABLE`로 fail-closed한다.

`PRE_S5_RAG_GLOBAL_NEWS_CONTRACT_LOCKED=1`의 current policy는
`OA112_ACTIVE_CONTRACT_LOCKED`(14 track × 8)와
`S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`를 함께 뜻한다. 이 README의 historical
OA112/OA140 metadata 설명은 새 physical corpus, raw cache 또는 active generation의 증거가 아니다.

공식 입력 형식은 다음 아홉 family다.

| family | 확장자/MIME | 기본 처리 |
|---|---|---|
| PDF | `.pdf` | born-digital text·block 우선, text layer 없는 page만 OCR |
| Word | `.docx` | paragraph·heading·list·table native parse |
| PowerPoint | `.pptx` | slide text·table·picture locator native parse |
| Excel | `.xlsx` | formula를 실행하지 않고 cell value·formula 문자열만 parse |
| HTML | `.html`, `.htm` | script·외부 resource·form을 제거한 local DOM parse |
| Markdown | `.md`, `.markdown` | heading·list·table·formula를 local parse |
| Text | `.txt` | bounded UTF-8 text parse |
| Raster image | `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff` | selected OCR backend |

HWP/HWPX는 직접 지원하지 않는다. 사용자가 신뢰할 수 있는 도구로 PDF로 변환한 뒤 import한다.
Office macro·외부 link, PDF JavaScript/attachment, HTML external resource, XLSX formula 실행,
XXE, MIME spoof, zip bomb, symlink·junction·reparse point·hardlink alias와 경로 이탈은
fail-closed한다. credential/API key 탐지 결과는 항상 quarantine하며 일반 PII는 owner-private
local retrieval에만 남고 외부 LLM 후보가 되지 않는다.

처리 순서는 고정돼 있다.

```text
magic/보안 검사
→ born-digital native parse
→ text layer가 없는 page만 OCR
→ Document IR 정규화
→ secret·PII·prompt-injection 분류
→ chunk/hash
→ Voyage contextual embedding 또는 BGE whole-generation fallback
→ staging generation 평가
→ atomic activation
```

개인 원본은 복사하지 않고 사용자가 보유한 위치에서 read-only로 읽는다. 원본 경로는
Document IR, API, history, log에 넣지 않는다. 동일 raw hash는 parse를, 동일 chunk hash는
embedding을 재사용한다. future owner import는 5분 single-use owner-bound ticket을 요구하지만
ticket은 local parse 또는 provider activation의 충분조건이 아니다. local parse 권한과 외부 LLM
전송 권한은 별개이고, DRM·login·paywall 우회나 무단 crawling은 지원하지 않는다.

## Production OCR 선택

동일한 한국은행 금융안정보고서와 옵션가격 arXiv 300 DPI fixture로 세 family를 비교했다.

| 후보 | 결과 | production 상태 |
|---|---|---|
| `PADDLE_STRUCTURED` | 영어 CER·표·수식·중요 숫자 gate 실패 | research only |
| `PADDLE_VL` | 한국어/영어 CER 0, 표·수식·읽기 순서 1.0, 중요 숫자 오류·환각 0 | selected |
| `UNLIMITED_GGUF` | CER·수식·읽기 순서·중요 숫자 gate 실패 | research only |

현재 Intel Arc 130V에서는 OpenVINO 2026.2.1로 변환한 `PADDLE_VL`의 LLM, embedding,
vision 구성요소가 모두 `GPU.0`에서 compile/infer됐다. CPU와 Intel 실측 및 결과 digest는
[`benchmark-summary.v1.json`](ocr/benchmark/receipts/benchmark-summary.v1.json)에 있다.
NVIDIA 장비가 없어 NVIDIA lane은 unit/contract/container smoke까지만 구현하고
`NOT_RUN_NO_NVIDIA`로 남긴다. production BAT에서 research backend를 선택할 수 없다.

## Windows BAT 명령

명령은 repository checkout의 `capstone-rag\tools\windows`에서 실행한다. owner import는
경로·owner ID·ticket을 명령행으로 받지 않는다. 인증된 local control plane만 0600의
`control/owner-import.json`을 만들 수 있고, import BAT는 그 fixed record를 소비할 때만 동작한다.
수동 BAT 사용자는 원본 경로나 ticket을 추가 인수로 전달해서는 안 된다.

```bat
setup-rag-content.bat
rag-import-auto.bat
rag-import-cpu.bat
rag-import-intel-gpu.bat
rag-import-nvidia-gpu.bat
rag-import-status.bat
rag-remove-document.bat
rag-cache-clean.bat
```

`rag-import-auto.bat`는 NVIDIA, Intel Arc, CPU 순으로 hardware를 판별하지만 선택한 lane이
실패할 때 다른 lane으로 조용히 전환하지 않는다. Intel 명령은 OpenVINO execution device가
`GPU`임을 확인해야 성공한다. NVIDIA 하드웨어가 없으면 stable
`NOT_RUN_NO_NVIDIA`를 반환한다. 각 lane은 독립 `uv.lock`과 venv를 사용하고 공통 application
parser·Document IR·generation 코드를 호출한다.

`owner-import.json`은 owner-bound 5분 single-use import ticket, approved root 아래의 relative
path, opaque document/source identifiers만 담는다. import 결과에는 path, owner ID, ticket,
DB DSN 또는 raw text를 출력하지 않는다. 이 local control record는 OA download, Voyage/Vertex
activation 또는 provider call의 승인 packet을 대체하지 않는다.

문서 삭제도 명령행 `documentId`를 받지 않는다. 인증된 local control plane은 별도 0600
`control/owner-delete.json`에 owner-bound 5분 deletion selector만 만들며, BAT는 admin DB
credential을 environment에서만 읽어 immutable hard-delete function을 실행한다. public base가
활성화된 문서는 replacement bundle이 준비되지 않으면 `OWNER_DOCUMENT_DELETE_BLOCKED`로
fail-closed한다.

설치본의 기본 root는 `%LOCALAPPDATA%\CapstoneAITradingCoach\rag`, 개발 checkout은
`capstone-rag/runtime/local-corpus`다. 두 위치의 원문·parse·embedding·cache는 Git 배포물이
아니며, 개발 runtime은 `.gitignore`로 보호된다. OA 원문은 후속 signed manifest가 허용한
공식 fixed HTTPS source에서 각 사용자가 받는 cache이고 프로젝트 release에 재배포하지 않는다.

## Historical OA112 manifest and active logical OA112 selection

`s4-7d-oa140-release.v1.json`은 `OA112_HISTORICAL` metadata release다. 이름은 OA140
프로그램을 따르지만 현재 source 수는 14개 curriculum track × 8개 = 112개다. 이는 source의
fixed HTTPS `canonicalUrl`, `downloadUrl`, `rawContentSha256`, track role,
`fallbackAllowed=false`를 Git에 남긴 manifest일 뿐 installed corpus가 아니다. active release
policy는 14개 track × 8개의 `OA112_ACTIVE_CONTRACT_LOCKED`이며 physical activation은
`S4_7D_OA112_PHYSICAL_ACTIVATION=NOT_MATERIALIZED`다. actual raw-hash/rights revalidation과
네 activation permission이 모두 통과하기 전에는 active generation으로 사용할 수 없다.
원문 PDF/HTML, 추출 text, Document IR, chunk, embedding, cache는 GitHub Release나 Hugging
Face Dataset에 재배포하지 않는다.

검증 명령은 다음과 같다.

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  python -m app.rag.oa_release_manifest_cli \
  --manifest capstone-rag/manifests/s4-7d-oa140-release.v1.json
```

공식 원천의 raw SHA-256을 다시 확인해야 할 때만 네트워크 검증을 명시적으로 실행한다. 이 모드는
redirect를 금지하고 source 사이를 3초씩 쉬며, receipt에는 URL·byte 수·SHA-256만 남긴다.

```bash
uv run --project workspaces/decision-platform/python-services --frozen \
  python -m app.rag.oa_release_manifest_cli \
  --manifest capstone-rag/manifests/s4-7d-oa140-release.v1.json \
  --fetch-hashes \
  --receipt capstone-rag/manifests/s4-7d-oa140-remote-hash-receipt.v1.json
```

historical `rag-content status`와 `setup-rag-content.bat`의 manifest validation은 active physical
OA112 activation 증거가 아니다. 이 상태는 아직 원문 download/parse/embed/eval이 끝난
`FULL_READY`가 아니라, public OA generation을 구축하기 위한 `BUILDING` 시작점이다. 현재
setup은 historical manifest validation만 수행한다. import는 trusted local control record의
owner document를 local BGE immutable `STAGED` graph로 만들고, remove는 unreferenced staged
document만 hard-delete하며, cache-clean은 fixed local cache directory만 비운다. 이 명령들은
OA112/public bundle activation이나 owner-derived active index를 주장하지 않는다.
`FULL_READY`는 runtime cache에서 모든 source hash, parser/OCR receipt, chunk hash, BGE
embedding, staging evaluation이 통과하고 서버 active pointer가 원자적으로 pin된 뒤에만 가능하다.

배포용 metadata artifact set은
`manifests/s4-7d-oa140-distribution.v1.json`과
`manifests/s4-7d-oa140-checksums.sha256`이 고정한다. 현재 repository에는 GitHub Release와
Hugging Face publication credential이 없으므로 publication status는
`READY_NOT_PUBLISHED_NO_CREDENTIAL`이다. 실제 게시 단계에서도 OA 원문·추출 text·embedding은
올리지 않고, release manifest·curriculum map·remote hash receipt·checksums만 동일 digest로
게시한다.
