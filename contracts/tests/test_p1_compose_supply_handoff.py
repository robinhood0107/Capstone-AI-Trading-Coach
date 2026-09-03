from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import contracts.verify_p1_compose_supply_handoff as verifier
from contracts.verify_p1_compose_supply_handoff import (
    CATALOG_PATH,
    EXACT_TEAM_B_FILES,
    HANDOFF_PATHS,
    ContractError,
    main,
    remote_verification_commands,
    supply_catalog,
    verify_compose,
    verify_handoff_docs,
    verify_bundle,
    verify_receipt,
)


class P1ComposeSupplyHandoffTest(unittest.TestCase):
    def valid_receipt(self) -> dict[str, object]:
        digest = "a" * 64
        return {
            "artifactManifestSha256": digest,
            "contractId": "p1-team-b-oci-receipt.v1",
            "dependencyLockSha256": digest,
            "dockerfileSha256": digest,
            "imageReference": f"ghcr.io/robinhood0107/capstone-team-b-return-artifact@sha256:{digest}",
            "inputPackSha256": digest,
            "manifestDigest": f"sha256:{digest}",
            "outputArtifacts": [
                {"path": path, "sha256": digest, "sizeBytes": 1}
                for path in EXACT_TEAM_B_FILES
            ],
            "providerAuthority": supply_catalog()["providerAuthority"],
            "producerCommitSha256": digest,
            "sourceArchiveSha256": digest,
            "subjectCommitSha": "b" * 40,
        }

    def test_checked_in_compose_supply_and_handoff_are_complete(self) -> None:
        self.assertEqual(0, main([]))
        verify_compose()
        verify_handoff_docs()
        self.assertEqual(
            supply_catalog(), json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        )
        self.assertTrue(
            all(
                (Path(__file__).resolve().parents[2] / path).is_file()
                for path in HANDOFF_PATHS
            )
        )

    def test_local_team_b_validator_is_network_none_and_validate_only(self) -> None:
        root = Path(__file__).resolve().parents[2]
        controller = (root / "deploy/p1/full-appctl").read_text(encoding="utf-8")
        block = controller.split("artifact_validate() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("--pull never", block)
        self.assertIn("--network none", block)
        self.assertIn("--read-only", block)
        self.assertIn("--cap-drop ALL", block)
        self.assertIn("--security-opt no-new-privileges:true", block)
        self.assertIn("--validate-only", block)
        self.assertIn("PROVIDER_LIVE_CALLS_ENABLED=false", block)
        self.assertIn("KIS_OFFLINE=1", block)
        self.assertIn("PROVIDER_CALLS=0", block)

    def test_local_team_b_validator_rejects_unsafe_input_before_docker(self) -> None:
        root = Path(__file__).resolve().parents[2]
        controller = root / "deploy/p1/full-appctl"

        def invoke(bundle: str, digest: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    str(controller),
                    "artifact",
                    "validate",
                    bundle,
                    "--manifest-sha256",
                    digest,
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        relative = invoke("relative/bundle", "a" * 64)
        self.assertEqual(1, relative.returncode)
        self.assertIn("CAPSTONE_ERROR=ARTIFACT_VALIDATE_BUNDLE_ROOT", relative.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            target = temporary_root / "target"
            target.mkdir()
            link = temporary_root / "link"
            link.symlink_to(target, target_is_directory=True)
            symlinked = invoke(str(link), "a" * 64)
            self.assertEqual(1, symlinked.returncode)
            self.assertIn(
                "CAPSTONE_ERROR=ARTIFACT_VALIDATE_BUNDLE_ROOT", symlinked.stderr
            )

            invalid_hash = invoke(str(target), "not-a-sha")
            self.assertEqual(1, invalid_hash.returncode)
            self.assertIn(
                "CAPSTONE_ERROR=ARTIFACT_VALIDATE_MANIFEST_SHA256",
                invalid_hash.stderr,
            )

            comma_parent = temporary_root / "unsafe,parent" / "bundle"
            comma_parent.mkdir(parents=True)
            invalid_parent = invoke(str(comma_parent), "a" * 64)
            self.assertEqual(1, invalid_parent.returncode)
            self.assertIn(
                "CAPSTONE_ERROR=ARTIFACT_VALIDATE_BUNDLE_PARENT",
                invalid_parent.stderr,
            )

    def test_receipt_requires_restricted_digest_exact10_and_all_attestations(
        self,
    ) -> None:
        receipt = self.valid_receipt()
        verify_receipt(receipt)
        for mutation in (
            "tag",
            "foreign",
            "missing",
            "malformed-output",
            "bool-size",
            "zero-commit",
            "authority",
        ):
            candidate = copy.deepcopy(receipt)
            if mutation == "tag":
                candidate["imageReference"] = (
                    "ghcr.io/robinhood0107/capstone-team-b-return-artifact:latest"
                )
            elif mutation == "foreign":
                candidate["imageReference"] = (
                    "ghcr.io/example/foreign@sha256:" + "a" * 64
                )
            elif mutation == "missing":
                candidate["outputArtifacts"] = candidate["outputArtifacts"][:-1]
            elif mutation == "malformed-output":
                candidate["outputArtifacts"][0] = "not-an-object"
            elif mutation == "bool-size":
                candidate["outputArtifacts"][0]["sizeBytes"] = True
            elif mutation == "zero-commit":
                candidate["subjectCommitSha"] = "0" * 40
            else:
                candidate["providerAuthority"]["providerCalls"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                verify_receipt(candidate)

    def test_remote_commands_verify_before_digest_pull(self) -> None:
        reference = self.valid_receipt()["imageReference"]
        commands = remote_verification_commands(reference)
        self.assertEqual("cosign", commands[0][0])
        self.assertEqual("verify", commands[0][1])
        self.assertEqual("verify-blob", commands[1][1])
        self.assertEqual("oras", commands[-1][0])
        self.assertEqual(6, len(commands))
        self.assertEqual(reference, commands[-1][2])
        with self.assertRaises(ContractError):
            remote_verification_commands(
                "ghcr.io/robinhood0107/capstone-team-b-return-artifact:latest"
            )

    def test_compose_rejects_duplicate_yaml_and_unsigned_receipt_verifier(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            compose = temporary_root / "compose.yml"
            control = temporary_root / "full-appctl"
            oci_verifier = temporary_root / "verify-team-b-oci"
            compose.write_text(
                (root / "deploy/p1/compose.yml").read_text(encoding="utf-8")
                + "\nservices: {}\n",
                encoding="utf-8",
            )
            control.write_text(
                (root / "deploy/p1/full-appctl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            oci_verifier.write_text(
                (root / "deploy/p1/verify-team-b-oci").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with (
                patch.object(verifier, "COMPOSE_PATH", compose),
                patch.object(verifier, "CONTROL_PATH", control),
                patch.object(verifier, "OCI_VERIFIER_PATH", oci_verifier),
                self.assertRaises(ContractError),
            ):
                verify_compose()

            compose.write_text(
                (root / "deploy/p1/compose.yml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            oci_verifier.write_text(
                (root / "deploy/p1/verify-team-b-oci")
                .read_text(encoding="utf-8")
                .replace("cosign verify-blob", "cosign inspect-blob", 1),
                encoding="utf-8",
            )
            with (
                patch.object(verifier, "COMPOSE_PATH", compose),
                patch.object(verifier, "CONTROL_PATH", control),
                patch.object(verifier, "OCI_VERIFIER_PATH", oci_verifier),
                self.assertRaises(ContractError),
            ):
                verify_compose()

    def test_handoff_rejects_missing_sections_and_internal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = "docs/handoff/team-a/README.md"
            handoff = root / relative
            handoff.parent.mkdir(parents=True)
            handoff.write_text(
                "\n".join(verifier.HEADINGS[:-1]) + "\n", encoding="utf-8"
            )
            with (
                patch.object(verifier, "ROOT", root),
                patch.object(verifier, "HANDOFF_PATHS", (relative,)),
                self.assertRaises(ContractError),
            ):
                verify_handoff_docs()

            handoff.write_text(
                "\n".join(verifier.HEADINGS)
                + "\n"
                + "-".join(("private", "reference"))
                + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(verifier, "ROOT", root),
                patch.object(verifier, "HANDOFF_PATHS", (relative,)),
                self.assertRaises(ContractError),
            ):
                verify_handoff_docs()

    def test_pulled_bundle_requires_manifest_plus_exact10_hashes(self) -> None:
        receipt = self.valid_receipt()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = json.loads(
                (
                    Path(__file__).resolve().parents[2]
                    / "contracts/examples/p1-return-engine-artifact-manifest.v2.valid.json"
                ).read_text(encoding="utf-8")
            )
            for manifest_item, receipt_item in zip(
                manifest["artifacts"], receipt["outputArtifacts"], strict=True
            ):
                content = receipt_item["path"].encode()
                (root / receipt_item["path"]).write_bytes(content)
                receipt_item["sizeBytes"] = len(content)
                receipt_item["sha256"] = hashlib.sha256(content).hexdigest()
                manifest_item["sizeBytes"] = receipt_item["sizeBytes"]
                manifest_item["sha256"] = receipt_item["sha256"]
            payload = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            (root / "manifest.json").write_bytes(payload)
            receipt["artifactManifestSha256"] = hashlib.sha256(payload).hexdigest()
            verify_bundle(receipt, root)
            (root / "unexpected.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(ContractError):
                verify_bundle(receipt, root)
            (root / "unexpected.txt").unlink()
            manifest["producer"]["dependencyLockSha256"] = "b" * 64
            payload = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            (root / "manifest.json").write_bytes(payload)
            receipt["artifactManifestSha256"] = hashlib.sha256(payload).hexdigest()
            with self.assertRaises(ContractError):
                verify_bundle(receipt, root)

    def test_no_real_receipt_is_checked_in(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual([], list(root.glob("**/p1-team-b-oci-receipt.v1.json")))
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "receipt.json"
            receipt.write_text(json.dumps(self.valid_receipt()), encoding="utf-8")
            self.assertEqual(0, main(["--receipt", str(receipt)]))
            self.assertEqual(
                0,
                main(
                    [
                        "--receipt",
                        str(receipt),
                        "--expected-reference",
                        self.valid_receipt()["imageReference"],
                    ]
                ),
            )
            self.assertEqual(
                1,
                main(
                    [
                        "--receipt",
                        str(receipt),
                        "--expected-reference",
                        "ghcr.io/robinhood0107/capstone-team-b-return-artifact@sha256:"
                        + "b" * 64,
                    ]
                ),
            )
            receipt.write_text("[]", encoding="utf-8")
            self.assertEqual(1, main(["--receipt", str(receipt)]))
            receipt.write_text('{"contractId":"a","contractId":"b"}', encoding="utf-8")
            self.assertEqual(1, main(["--receipt", str(receipt)]))
            target = Path(temporary) / "target.json"
            target.write_text(json.dumps(self.valid_receipt()), encoding="utf-8")
            receipt.unlink()
            receipt.symlink_to(target)
            self.assertEqual(1, main(["--receipt", str(receipt)]))

    def test_daily_collector_keeps_the_default_container_count_at_five(self) -> None:
        """기본은 5개, --models 가 2개, --mock 이 1개를 더한다.

        "기본 장기 컨테이너 5개"는 계약 기록이다. 상주 수집기를 기본으로 올리면 그 문장이
        거짓이 되므로 automation profile 뒤에 두고 --mock 에서만 띄운다.
        """

        root = Path(__file__).resolve().parents[2]
        controller = (root / "deploy/p1/full-appctl").read_text(encoding="utf-8")

        self.assertIn("local expected=5", controller)
        self.assertIn("((models)) && expected=$((expected + 2))", controller)
        self.assertIn("((mock)) && expected=$((expected + 1))", controller)
        # 수집기는 --mock 에서만 올라오고 아니면 내려간다.
        self.assertIn(
            "compose --profile automation up -d --wait market-data-daily", controller
        )
        self.assertIn(
            "compose --profile automation stop market-data-daily", controller
        )

    def test_daily_collector_is_least_privilege_and_reuses_the_stack(self) -> None:
        """수집기는 writer DSN 하나만 들고, 새 이미지·secret 을 만들지 않는다."""

        root = Path(__file__).resolve().parents[2]
        compose = (root / "deploy/p1/compose.yml").read_text(encoding="utf-8")
        block = compose.split("  market-data-daily:\n", 1)[1].split("\n\n", 1)[0]

        # 기본 up 이 건드리지 않도록 profile 뒤에 있다.
        self.assertIn("profiles: [automation]", block)
        # 기존 이미지를 그대로 쓴다.
        self.assertIn("${P1_PYTHON_IMAGE:-capstone-decision-platform:p1-local}", block)
        # secret 은 정확히 하나. 여기에 KIS 앱키나 자동운용 DSN 이 붙으면 안 된다.
        self.assertIn("secrets: [market_data_env]", block)
        self.assertNotIn("market_data_provider_env", block)
        self.assertNotIn("automation_runtime_env", block)
        # postgres 와 외부 HTTPS 둘 다 필요하다.
        self.assertIn("networks: [p1-data, p1-app]", block)
        # 보안 앵커를 그대로 상속한다 (read_only, cap_drop ALL, no-new-privileges).
        self.assertIn("<<: *app-security", block)
        # 실행은 기존 CLI 다. 새 스케줄러 코드를 만들지 않았다.
        self.assertIn("app.data.market_data.yfinance_daily_cli", block)

    def test_daily_collector_entrypoint_profile_requires_only_the_writer_dsn(self) -> None:
        """entrypoint whitelist 가 이 컨테이너에 writer DSN 하나만 허용한다.

        market-data 프로파일을 재사용하면 secret 파일 세 개가 붙어 KIS 앱키와 자동운용 DSN 이
        함께 들어온다. 그래서 전용 항목을 두었고, 그것이 좁게 유지되는지 본다.
        """

        root = Path(__file__).resolve().parents[2]
        entrypoint = (root / "deploy/p1/docker/secret-entrypoint.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "market-data-daily) secret_files=/run/secrets/market_data_env ;;", entrypoint
        )
        self.assertIn("market-data-daily:MARKET_DATA_WRITER_DSN) return 0 ;;", entrypoint)
        self.assertIn(
            "market-data-daily) printf '%s\\n' 'MARKET_DATA_WRITER_DSN' ;;", entrypoint
        )
        # 이 프로파일에 다른 키를 허용하지 않는다.
        for forbidden in (
            "market-data-daily:P1_AUTOMATION_DATABASE_DSN",
            "market-data-daily:KIS_MOCK_APP_KEY",
            "market-data-daily:KIS_LIVE_APP_KEY",
            "market-data-daily:AUTOMATION_RUNTIME_SHARED_SECRET",
        ):
            self.assertNotIn(forbidden, entrypoint)


if __name__ == "__main__":
    unittest.main()
