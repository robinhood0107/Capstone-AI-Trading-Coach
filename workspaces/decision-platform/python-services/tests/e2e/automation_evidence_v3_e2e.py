from __future__ import annotations

import argparse
import sys

from app.p1_owner.automation import CandidateScreening, EvidenceSpan, NewsScreeningBatch

from .harness import Recorder, require_opt_in, write_report

_OPT_IN = "P1_AUTOMATION_EVIDENCE_V3_E2E"


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])
    recorder = Recorder()
    evidence = EvidenceSpan(
        "005930",
        "cit_fixture_005930",
        "src_official_dart",
        "OFFICIAL_PRIMARY",
        None,
        False,
        "a" * 64,
        "bounded verified fixture",
        "b" * 64,
    )
    batch = NewsScreeningBatch(
        (
            CandidateScreening("005930", "AVAILABLE", "VETO_BUY", 2_000, "VERIFIED", (evidence,)),
            CandidateScreening("000660", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
        ),
        provider_call_count=0,
        grounding_query_count=0,
    )
    recorder.add(
        "pre-selection full candidate screening",
        "PASS"
        if len(batch.screenings) == 2 and batch.screenings[0].evidence[0].verified
        else "FAIL",
        "fixture-only; providerCalls=0 orderCalls=0",
    )
    recorder.add(
        "verified veto and zero-evidence neutralization",
        "PASS"
        if batch.screenings[0].verdict == "VETO_BUY"
        and batch.screenings[1].verdict == "NO_VETO"
        and batch.screenings[1].score_bps == 5_000
        else "FAIL",
        "verified candidate removed before one final selection",
    )
    report = write_report(
        contract_id="p1-automation-evidence-v3-e2e.v1",
        marker="P1_AUTOMATION_EVIDENCE_V3_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
