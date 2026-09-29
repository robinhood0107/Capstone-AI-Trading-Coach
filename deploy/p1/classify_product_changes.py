#!/usr/bin/env python3
"""Classify changed repository paths before product-scoped checks or publishing."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class Rule:
    prefix: str
    category: str
    owner: str


RULES = (
    Rule("workspaces/mars-demo/", "DEMO-only", "demo-product-owner"),
    Rule("deploy/p1/mars-demo-release.json", "DEMO-only", "demo-product-owner"),
    Rule("contracts/openapi/mars-demo-", "DEMO-only", "demo-product-owner"),
    Rule("contracts/tests/", "deployment-only", "release-contract-owner"),
    Rule("contracts/changes/", "deployment-only", "release-contract-owner"),
    # The standalone DEMO imports these exact FULL presentation modules. Changes to
    # them must be evaluated and, when merged, built for both products at that SHA.
    Rule("workspaces/experience-dashboard/src/shared/ui/LoginCard.tsx", "FULL-only", "full-product-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/client.ts", "FULL-only", "full-product-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/session.ts", "FULL-only", "full-product-owner"),
    Rule("workspaces/experience-dashboard/src/shared/ui/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/lib/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/endpoints.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/wire.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/envelope.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/latestRun.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/api/nullableRead.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/globals.css", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/page.tsx", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/principles/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/strategy/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/model-evaluation/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/backtest/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/automation/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/order-review/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/rag/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/journal/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/report/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/overview/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/principles/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/strategy/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/model-evaluation/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/backtest-report/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/automation/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/order-review/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/rag-source/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/journal/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/report/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/system/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/admin/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/ui/Panel.tsx", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/ui/LoginCardFrame.tsx", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/ui/ThemeToggle.tsx", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/features/intro/", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/shared/lib/theme.ts", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/src/app/globals.css", "shared-ui/contract", "shared-ui-owner"),
    Rule("workspaces/experience-dashboard/public/", "shared-ui/contract", "shared-ui-owner"),
    Rule("contracts/openapi/", "FULL-only", "full-product-owner"),
    Rule("workspaces/decision-platform/", "FULL-only", "full-product-owner"),
    Rule("workspaces/return-engine/", "FULL-only", "full-product-owner"),
    Rule("workspaces/experience-dashboard/", "FULL-only", "full-product-owner"),
    Rule("deploy/p1/mars-release-gate.json", "FULL-only", "full-product-owner"),
    Rule("deploy/p1/docker/", "FULL-only", "full-product-owner"),
    Rule("deploy/p1/sync_mars_public_operator_env.py", "FULL-only", "full-product-owner"),
    Rule("CHANGELOG.md", "FULL-only", "full-product-owner"),
    Rule(".github/", "deployment-only", "release-contract-owner"),
    Rule(".dockerignore", "shared-ui/contract", "release-contract-owner"),
    Rule(".gitignore", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/render_mars_release_compose.py", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/classify_product_changes.py", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/test_classify_product_changes.py", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/test_mars_release_failure_isolation.py", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/portainer/", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/compose.public-demo.yml", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/compose.public-full.yml", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/assemble_mars_public_secrets.py", "deployment-only", "release-contract-owner"),
    Rule("deploy/p1/", "deployment-only", "release-contract-owner"),
    Rule("docs/", "deployment-only", "release-contract-owner"),
    Rule("README.md", "deployment-only", "release-contract-owner"),
)


def _normalize(path: str) -> str:
    normalized = str(PurePosixPath(path.strip()))
    if not normalized or normalized == "." or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError(f"invalid changed path: {path!r}")
    return normalized


def classify_paths(paths: Iterable[str]) -> dict[str, object]:
    normalized_paths = sorted({_normalize(path) for path in paths if path.strip()})
    if not normalized_paths:
        raise ValueError("no changed paths supplied")
    classifications: dict[str, list[str]] = {}
    owners: dict[str, list[str]] = {}
    unclassified: list[str] = []
    for path in normalized_paths:
        matching = next((rule for rule in RULES if path == rule.prefix or path.startswith(rule.prefix)), None)
        if matching is None:
            unclassified.append(path)
            continue
        classifications.setdefault(matching.category, []).append(path)
        owners.setdefault(matching.owner, []).append(path)
    if unclassified:
        raise ValueError("unclassified changed path(s): " + ", ".join(unclassified))

    categories = sorted(classifications)
    full_runtime = bool({"FULL-only", "shared-ui/contract"} & set(categories))
    demo_runtime = bool({"DEMO-only", "shared-ui/contract"} & set(categories))
    full_changed = bool({"FULL-only", "shared-ui/contract"} & set(categories))
    return {
        "categories": categories,
        "paths": normalized_paths,
        "owners": {owner: sorted(owner_paths) for owner, owner_paths in sorted(owners.items())},
        "fullImageRequired": full_runtime,
        "demoCandidateRequired": demo_runtime,
        "demoSyncEvaluationRequired": full_changed,
        "fullPublishNoOp": not full_runtime,
        "demoOnlyFullPublishNoOp": categories == ["DEMO-only"],
    }


def changed_paths(base: str, head: str) -> list[str]:
    for label, ref in (("base", base), ("head", head)):
        if not ref:
            raise ValueError(f"{label} ref is required")
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{base}...{head}"],
        text=True,
    )
    return [line for line in output.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--paths-file")
    parser.add_argument("--format", choices=("json", "github"), default="json")
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()
    try:
        if args.paths_file:
            paths = open(args.paths_file, encoding="utf-8").read().splitlines()
        else:
            paths = changed_paths(args.base or "", args.head or "")
        result = classify_paths(paths)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"PRODUCT_IMPACT_CLASSIFICATION_FAILED: {error}", file=sys.stderr)
        return 2

    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if args.format == "json":
        print(encoded)
    else:
        output_path = args.github_output or os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            print("GITHUB_OUTPUT is required for --format github", file=sys.stderr)
            return 2
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"full_image_required={'true' if result['fullImageRequired'] else 'false'}\n")
            output.write(f"demo_candidate_required={'true' if result['demoCandidateRequired'] else 'false'}\n")
            output.write(f"demo_sync_required={'true' if result['demoSyncEvaluationRequired'] else 'false'}\n")
            output.write(f"full_publish_noop={'true' if result['fullPublishNoOp'] else 'false'}\n")
            output.write(f"demo_only_full_noop={'true' if result['demoOnlyFullPublishNoOp'] else 'false'}\n")
            output.write(f"categories={','.join(result['categories'])}\n")
            output.write(f"manifest<<IMPACT_JSON\n{encoded}\nIMPACT_JSON\n")
        print(f"PRODUCT_IMPACT={','.join(result['categories'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
