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

## Release rule

`FINAL` is schema-valid only when every hard gate is `PASS`. Dashboard UI, CUDA hardware verification, and
SearXNG may use their explicitly disclosed non-blocking states. LightGBM remains research-only and live order
remains unimplemented. Provider authority is read-only, one-shot, retry zero, and has account, balance, and
order caps of zero.

The receipt intake and this contract do not prove Team B real artifacts, Seed export, backend E2E, accelerator
execution, provider execution, security closure, Compose E2E, image publication, a tag, or a GitHub Release.
