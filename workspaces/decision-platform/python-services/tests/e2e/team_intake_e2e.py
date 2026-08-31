"""Team B 산출물 수신 경로를 production 적재기로 처음부터 끝까지 한 번 태운다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나. `full_pipeline_e2e.py`는 **검증을 통과한 뒤의 import packet부터** 시작한다.
그래서 그 앞 구간 — Team B가 실제로 건네는 10개 파일이 검증을 통과하는가 — 은 아직 스택에서
한 번도 확인된 적이 없다. 여기서 그 구간을 본다.

  1. 물질화된 번들 디렉터리가 정확히 매니페스트 1개 + 산출물 10개다.
  2. `artifact-importer` compose 서비스가 production 적재기(`app.p1_owner.importer`)를 그대로
     실행해 검증·아카이브·DB 적재를 한 번에 끝낸다. 테스트가 행을 손으로 쓰지 않는다.
  3. 적재 뒤 번들 행이 생기고 신호 투영이 쌓이며, 무결성 영수증(매니페스트·패킷 해시)이
     디스크의 실제 파일 해시와 같다.
  4. 매니페스트 해시를 한 글자만 바꿔 부르면 적재가 열리지 않는다.

무엇을 확인하지 않나. 실제 Team B 산출물은 아직 없다. 여기서 쓰는 것은 합성 golden 번들이며
`evidence_mode`가 그 사실을 그대로 말한다. 이 runner가 증명하는 것은 **경로**이지 산출물의
품질이 아니다.

정리. 적재한 번들과 그 투영은 끝에서 차집합으로 되돌린다. 정리에 실패하면 FAIL이다.

실행:
  P1_TEAM_INTAKE_E2E=1 python -m tests.e2e.team_intake_e2e \\
    --out artifacts/decision-platform/e2e/team-intake.json
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Final

from .harness import (
    DEPLOY,
    DOCKER,
    HarnessError,
    PROJECT,
    Recorder,
    STATE,
    cleanup,
    psql,
    require_opt_in,
    run,
    snapshot,
    write_report,
)

_OPT_IN: Final = "P1_TEAM_INTAKE_E2E"
_MANIFEST: Final = "p1-return-engine-manifest.v2.json"
_ARTIFACT_COUNT: Final = 10


def _bundle_root() -> Path:
    """물질화된 번들을 찾는다. 없으면 지어내지 않고 멈춘다."""

    parent = STATE / "artifacts"
    candidates = [path for path in sorted(parent.glob("*")) if (path / _MANIFEST).is_file()]
    if not candidates:
        raise HarnessError(f"no materialized Team B bundle under {parent.name}")
    return candidates[-1]


def _manifest_sha256(bundle: Path) -> str:
    return hashlib.sha256((bundle / _MANIFEST).read_bytes()).hexdigest()


def _import(bundle: Path, manifest_sha256: str, archive: Path) -> str:
    return run(
        [
            DOCKER,
            "compose",
            "--project-name",
            PROJECT,
            "--env-file",
            str(STATE / "runtime.env"),
            "-f",
            str(DEPLOY / "compose.yml"),
            "--profile",
            "owner",
            "run",
            "--rm",
            "artifact-importer",
        ],
        env={
            "P1_OPERATOR_UID": str(os.getuid()),
            "P1_ARTIFACT_BUNDLE_PARENT": str(bundle.parent),
            "P1_ARTIFACT_BUNDLE_NAME": bundle.name,
            "P1_ARTIFACT_MANIFEST_SHA256": manifest_sha256,
            "P1_ARTIFACT_ARCHIVE_DIR": str(archive),
        },
    )


def check_inventory(recorder: Recorder, bundle: Path) -> None:
    entries = sorted(path.name for path in bundle.iterdir() if path.is_file())
    artifacts = [name for name in entries if name != _MANIFEST]
    recorder.add(
        "번들 목록",
        "PASS" if _MANIFEST in entries and len(artifacts) == _ARTIFACT_COUNT else "FAIL",
        f"매니페스트={_MANIFEST in entries} 산출물={len(artifacts)}개 "
        f"(정확히 {_ARTIFACT_COUNT}개여야 한다) {artifacts[:4]}",
    )


def check_wrong_digest_is_rejected(recorder: Recorder, bundle: Path, archive: Path) -> None:
    """해시가 한 글자만 달라도 적재가 열리면 안 된다."""

    wrong = _manifest_sha256(bundle)
    wrong = ("0" if wrong[0] != "0" else "1") + wrong[1:]
    before = int(psql("select count(*) from public.p1_return_artifact_bundle;") or 0)
    opened = True
    detail = ""
    try:
        _import(bundle, wrong, archive)
    except HarnessError as error:
        opened = False
        detail = str(error).splitlines()[0][:160]
    after = int(psql("select count(*) from public.p1_return_artifact_bundle;") or 0)
    recorder.add(
        "틀린 매니페스트 해시는 닫힌다",
        "PASS" if not opened and after == before else "FAIL",
        f"적재 열림={opened} 번들 {before}→{after} {detail}",
    )


def check_import(recorder: Recorder, bundle: Path, archive: Path) -> str | None:
    manifest_sha256 = _manifest_sha256(bundle)
    before = int(psql("select count(*) from public.p1_return_artifact_bundle;") or 0)
    try:
        output = _import(bundle, manifest_sha256, archive)
    except HarnessError as error:
        recorder.add("production 적재기 실행", "FAIL", str(error).splitlines()[0][:400])
        return None
    after = int(psql("select count(*) from public.p1_return_artifact_bundle;") or 0)
    tail = output.strip().splitlines()[-1] if output.strip() else ""
    recorder.add(
        "production 적재기 실행",
        "PASS" if after == before + 1 else "FAIL",
        f"번들 {before}→{after} 마지막 출력={tail[:160]}",
    )
    if after != before + 1:
        return None

    row = psql(
        "select bundle_sha256 || '|' || manifest_sha256 || '|' || evidence_mode || '|' ||"
        " real_team_b::text || '|' || model_quality"
        " from public.p1_return_artifact_bundle order by imported_at desc limit 1;"
    )
    bundle_sha, stored_manifest, evidence_mode, real_team_b, quality = row.splitlines()[0].split(
        "|"
    )
    signals = int(
        psql(
            "select count(*) from public.p1_return_signal_projection"
            f" where bundle_sha256 = '{bundle_sha}';"
        )
        or 0
    )
    recorder.add(
        "무결성 영수증과 신호 투영",
        "PASS" if stored_manifest == manifest_sha256 and signals > 0 else "FAIL",
        f"매니페스트 해시 일치={stored_manifest == manifest_sha256} 신호 투영={signals}행 "
        f"evidence_mode={evidence_mode} real_team_b={real_team_b} model_quality={quality} "
        "(evidence_mode가 합성이라고 말하는 것이 맞다. 실제 산출물은 아직 없다)",
    )
    return bundle_sha


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    before: dict[str, list[str]] = {}
    try:
        before = snapshot()
        bundle = _bundle_root()
        check_inventory(recorder, bundle)
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="p1-intake-") as archive_dir:
            archive = Path(archive_dir)
            archive.chmod(0o700)
            check_wrong_digest_is_rejected(recorder, bundle, archive)
            check_import(recorder, bundle, archive)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 정리는 반드시 돈다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")
    finally:
        if before:
            cleanup(before, recorder)
        else:
            recorder.add("정리", "FAIL", "스냅샷을 찍지 못해 되돌릴 범위를 알 수 없다")

    report = write_report(
        contract_id="p1-team-intake-e2e.v1",
        marker="P1_TEAM_INTAKE_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
