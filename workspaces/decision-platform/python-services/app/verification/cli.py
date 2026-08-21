"""Internal CLI for P1 verification packet authoring and provider-free execution."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from app.verification.artifacts import publish_packet, publish_report, read_packet, read_report
from app.verification.packet import author_provider_read_smoke_packet
from app.verification.runner import run_s0_s5_current


_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p1-verify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    author = subparsers.add_parser("author")
    author.add_argument("--approval-id", required=True)
    author.add_argument("--output-root", type=Path, required=True)
    author.add_argument("--kis-token-cap", type=int, choices=(0, 1), required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--profile", choices=("S0_S5_CURRENT", "PROVIDER_READ_SMOKE"), required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--packet", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("artifact", type=Path)

    args = parser.parse_args(argv)
    if args.command == "author":
        _require_external_output_root(args.output_root)
        packet = author_provider_read_smoke_packet(
            repository_root=_REPOSITORY_ROOT,
            approval_id=args.approval_id,
            now=datetime.now(UTC),
            kis_token_physical_call_cap=args.kis_token_cap,
        )
        path = publish_packet(args.output_root, packet)
        print(f"P1_VERIFICATION_PACKET={packet.packet_sha256} PATH={path}")
        return 0
    if args.command == "run":
        _require_external_output_root(args.output_root)
        if args.profile == "PROVIDER_READ_SMOKE":
            parser.error("PROVIDER_READ_SMOKE runtime is NOT_IMPLEMENTED in P1.V0")
        if args.packet is not None:
            parser.error("S0_S5_CURRENT does not accept a provider packet")
        report = run_s0_s5_current(repository_root=_REPOSITORY_ROOT)
        path = publish_report(args.output_root, report)
        print(f"P1_VERIFICATION_REPORT={report.to_dict()['evidenceSha256']} PATH={path}")
        return 0 if report.execution_state == "PASS" else 1
    artifact = args.artifact
    if artifact.name.startswith("packet-"):
        packet = read_packet(artifact)
        print(f"P1_VERIFICATION_PACKET=VERIFIED SHA256={packet.packet_sha256}")
    else:
        report = read_report(artifact)
        print(
            "P1_VERIFICATION_REPORT=VERIFIED "
            f"OUTCOME={report.aggregate_outcome} PROFILE={report.profile}"
        )
    return 0


def _require_external_output_root(output_root: Path) -> None:
    absolute = output_root.absolute()
    try:
        absolute.relative_to(_REPOSITORY_ROOT)
    except ValueError:
        return
    raise ValueError("P1 verification artifacts must remain outside the Git repository")


if __name__ == "__main__":
    raise SystemExit(main())
