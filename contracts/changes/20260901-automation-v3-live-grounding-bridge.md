# Automation V3 live grounding bridge

## Reason

The live Vertex probe showed that `gemini-3.5-flash` executed Google queries while returning no
`groundingChunks` or `groundingSupports` when Google Search and `responseSchema` were bound to the
same call. Treating the query count alone as grounded evidence would violate the host citation gate.

## Internal contract change

- Google discovery is now a plain-text, Google-attached call. Its response body has no authority.
- The host projects only provider-observed roots and supports, assigns bounded citation IDs, and then
  performs a tool-free structured final call when an explanation is required.
- Automation `NEWS_SCREEN` sets `grounding_discovery_only=true`, so its physical provider cap remains
  one. `AI_JUDGING` is a separate tool-free call with a cap of two including fallback.
- `CandidateVerdict.evidenceSpans` is required. A quote must equal the complete supplied evidence
  string; Spring still downgrades every unsupported score/veto to `0.5/false`.
- `StartRun.thinking_level` carries the arm-time snapshot (`minimal | low | medium`) to provider
  construction. The new internal fields are additive protobuf fields 15 and 16.
- Spring now provides a non-fixture Automation evidence adapter when Strong LLM is enabled. The
  adapter sends candidate symbols and verified public evidence only; account, balance, holding, and
  order data remain absent.
- Vertex service-account readiness does not require an owner API key. Non-Vertex providers still
  require the owner credential slot, and the application service independently verifies that the
  live provider bean exists.
- Market bootstrap and replay use the approved XKRX+KIS correction set, including the confirmed
  closures on 2026-06-03 and 2026-07-17. The default horizon stays 1,260 sessions; a bounded
  `23..1260` session count permits exact-31/100-session pre-open readiness with a derived KIS cap 31
  and never claims full-1,260 coverage.

## Compatibility

Existing protobuf field numbers and public V1/V2/V3 HTTP contracts are unchanged. Existing hosts that
omit the new fields keep `thinking_level=low` and `grounding_discovery_only=false`.
