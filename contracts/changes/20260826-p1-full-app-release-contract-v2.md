# P1 full-app release contract v2

## Status

`CONTRACT_LOCKED_IMPLEMENTATION_IN_PROGRESS`

## Scope

The historical `p1-offline-demo-release-manifest.v1` remains byte-stable and continues as a regression
contract. The new `p1-full-app-release-manifest.v2` is the only contract that may authorize the public
GitHub `1.0.0` release after full application integration.

The v2 manifest binds the exact commit and tree, digest-pinned amd64 images, the public RAG Seed split
manifest, pinned BGE-M3 and PaddleOCR-VL model revisions, content-free provider receipt, capability status,
SBOM, provenance, signatures, source archive, and the unchanged repository license digest.

Schema validation is necessary but not sufficient. `contracts/validate_p1_full_app_release.py` also binds
each image reference to its separately declared digest, the Seed manifest and both tracked parts to their
actual repository bytes, the current Git commit/tree, and the unchanged LICENSE. It also binds each model to
the exact official runtime image component. The Team B top-level manifest is recursively verified by
`contracts/verify_p1_full_app_assets.py`; an empty or existence-only marker cannot satisfy the installer
preflight.

BGE-M3 now uses the official Hugging Face Text Embeddings Inference CPU container with the exact
`BAAI/bge-m3` revision. PaddleOCR-VL uses the official `llama.cpp` server container and PaddlePaddle's official
`PaddleOCR-VL-1.6-GGUF` repository; both GGUF and multimodal projector size/SHA-256 values are pinned. The
Compose overlay stores downloaded weights in named volumes, exposes no host model port, and never uses a
community BGE conversion. The model services stay on the private service network and also receive a dedicated
outbound-only bridge for the exact-revision first download; that bridge publishes no port. Runtime health,
1024-dimensional BGE output, OCR quality, and CPU/Intel performance remain hard-gate evidence and are not
implied by static Compose validation.

The current full-app runtime is composed only from `deploy/p1/compose.yml`. It health-gates PostgreSQL, Redis,
the shared application namespace, actor authority, async worker, Spring API edge, BGE-M3, and PaddleOCR-VL;
one-shot role bootstrap, migration, Seed import, identity bootstrap, and model fetch use successful-completion
dependencies. The fragmented Core-only Compose files remain historical v1 release inputs and are not layered
into the current full-app runtime.

## Release rule

`FINAL` is schema-valid only when every hard gate is `PASS`. Dashboard UI, CUDA hardware verification, and
SearXNG may use their explicitly disclosed non-blocking states. LightGBM remains research-only and live order
remains unimplemented. Provider authority is read-only, one-shot, retry zero, and has account, balance, and
order caps of zero.

The receipt intake and this contract do not prove Team B real artifacts, Seed export, backend E2E, accelerator
execution, provider execution, security closure, Compose E2E, image publication, a tag, or a GitHub Release.
