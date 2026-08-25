from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "p1-offline-demo-release.yml"
CODEOWNERS = REPOSITORY_ROOT / ".github" / "CODEOWNERS"
VERIFY_RELEASE = REPOSITORY_ROOT / "deploy" / "p1" / "verify-release"
VERIFY_RUNTIME = REPOSITORY_ROOT / "deploy" / "p1" / "verify-offline-runtime"
ASSEMBLER = REPOSITORY_ROOT / "deploy" / "p1" / "assemble-offline-bundle"
BACKUP = REPOSITORY_ROOT / "deploy" / "p1" / "backup"
RESTORE = REPOSITORY_ROOT / "deploy" / "p1" / "restore-test"
POSTGRES_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "postgres-pgvector.Dockerfile"


class P1ReleaseWorkflowSecurityTest(unittest.TestCase):
    def test_dispatch_input_is_never_interpolated_into_shell_source(self) -> None:
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        input_expression = "${{ inputs.releaseVersion }}"
        run_blocks: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.lstrip()
            if not stripped.startswith("run:"):
                index += 1
                continue
            indentation = len(line) - len(stripped)
            block: list[str] = []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indentation:
                    break
                block.append(candidate)
                index += 1
            run_blocks.append("\n".join(block))

        self.assertTrue(run_blocks)
        self.assertTrue(all(input_expression not in block for block in run_blocks))
        workflow = "\n".join(lines)
        self.assertIn(f"RELEASE_VERSION: {input_expression}", workflow)
        self.assertIn("version=$RELEASE_VERSION", workflow)

    def test_privileged_release_paths_have_a_code_owner(self) -> None:
        owners = CODEOWNERS.read_text(encoding="utf-8").splitlines()
        self.assertIn("/.github/workflows/ @robinhood0107", owners)
        self.assertIn("/deploy/p1/ @robinhood0107", owners)

    def test_release_verifier_cannot_be_disabled_by_python_optimization(self) -> None:
        verifier = VERIFY_RELEASE.read_text(encoding="utf-8")
        self.assertNotIn("assert ", verifier)
        self.assertIn("def require(condition: bool, message: str)", verifier)
        signature = verifier.index("bundle-root-signature.bundle.json")
        checksum = verifier.index("sha256sum --check --strict")
        self.assertLess(signature, checksum)
        self.assertIn('manifest["releaseStage"] == "FINAL"', verifier)

        p1ctl = (REPOSITORY_ROOT / "deploy" / "p1" / "p1ctl").read_text(encoding="utf-8")
        self.assertNotIn("assert ", p1ctl)
        self.assertIn("P1_ERROR=login_status", p1ctl)
        self.assertIn("verify_runtime_env", p1ctl)
        self.assertIn("runtime_env_duplicate", p1ctl)
        self.assertIn("runtime_env_inventory", p1ctl)
        self.assertIn("runtime_env_secret_gid", p1ctl)

    def test_release_runs_both_runtime_lifecycles(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        runtime = VERIFY_RUNTIME.read_text(encoding="utf-8")
        assembler = ASSEMBLER.read_text(encoding="utf-8")
        self.assertIn("for mode in db kafka; do", workflow)
        self.assertIn('verify-offline-runtime" "$bundle" "$mode"', workflow)
        self.assertIn("Sign complete bundle roots", workflow)
        self.assertIn('sudo env "PATH=$PATH" unshare --net --mount-proc', workflow)
        self.assertIn("Attest DB offline archive", workflow)
        self.assertIn("Attest Kafka offline archive", workflow)
        self.assertIn("Build PostgreSQL pgvector image for scan", workflow)
        self.assertIn("Scan PostgreSQL pgvector image", workflow)
        self.assertIn("Attest PostgreSQL pgvector image provenance", workflow)
        self.assertIn("P1_POSTGRES_IMAGE: ${{ steps.push.outputs.postgres_ref }}", workflow)
        self.assertIn("P1_KAFKA_IMAGE: ${{ steps.push.outputs.kafka_ref }}", workflow)
        self.assertIn(
            'require(expected == set(services), "compose service inventory")',
            VERIFY_RELEASE.read_text(encoding="utf-8"),
        )
        for command in ('up "$mode"', "smoke", "backup", "restore-test"):
            self.assertIn(command, runtime)
        self.assertLess(runtime.index("verify-release"), runtime.index("p1ctl"))
        self.assertNotIn("kafka-ui", runtime)
        self.assertNotIn("kafbat", workflow.lower())
        self.assertIn("verify-offline-runtime", assembler)
        self.assertIn("for adapter in db kafka db", runtime)
        self.assertIn("initial_secret_fingerprint", runtime)
        self.assertIn("ADAPTER_SWITCH=PASS", runtime)
        self.assertNotIn("--volumes", runtime)

    def test_adapter_start_recreates_only_containers_and_preserves_state(self) -> None:
        p1ctl = (REPOSITORY_ROOT / "deploy" / "p1" / "p1ctl").read_text(encoding="utf-8")
        kafka_cleanup = "compose kafka rm -f -s kafka kafka-topic-init"
        self.assertIn(kafka_cleanup, p1ctl)
        self.assertLess(
            p1ctl.index(kafka_cleanup), p1ctl.index('compose "$mode" up -d --force-recreate')
        )
        self.assertIn('compose "$mode" up -d --force-recreate', p1ctl)
        self.assertNotIn("down -v", p1ctl)
        self.assertNotIn("volume rm", p1ctl)
        self.assertNotIn("credential rotation", p1ctl.lower())
        runtime = VERIFY_RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn("--volumes", runtime)
        self.assertIn("VOLUMES_PRESERVED=TRUE", runtime)

    def test_backup_and_restore_follow_the_selected_compose_project(self) -> None:
        backup = BACKUP.read_text(encoding="utf-8")
        restore = RESTORE.read_text(encoding="utf-8")
        for script in (backup, restore):
            self.assertIn("compose.offline.db.yml", script)
            self.assertIn('python3 "$RELEASE_GUARD" state-check "$STATE_DIR"', script)
            self.assertIn('python3 "$RELEASE_GUARD" state-', script)
        self.assertIn('--project-name "$PROJECT_NAME"', backup)
        self.assertIn('--project-name "$restore_project"', restore)
        self.assertIn("restore_identity_trust_root", restore)
        self.assertIn("restore_demo_authority", restore)
        self.assertIn("restore_owner", restore)
        self.assertIn("VOLUME_PRESERVED=TRUE", restore)

    def test_existing_database_upgrade_bootstraps_auth_role_before_flyway(self) -> None:
        compose = (REPOSITORY_ROOT / "infra" / "docker-compose.infra.yml").read_text(
            encoding="utf-8"
        )
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("  role-bootstrap:\n", compose)
        self.assertIn("PGHOST: postgres", compose)
        self.assertIn('entrypoint: ["bash", "/opt/capstone/02-application-roles.sh"]', compose)
        bootstrap = "docker compose --env-file .env -f infra/docker-compose.infra.yml run --rm role-bootstrap"
        self.assertIn(bootstrap, readme)
        self.assertLess(readme.index(bootstrap), readme.index("./gradlew bootRun"))

    def test_postgres_extension_image_is_pinned_non_root_and_scanned(self) -> None:
        dockerfile = POSTGRES_DOCKERFILE.read_text(encoding="utf-8")
        compose = (REPOSITORY_ROOT / "deploy" / "p1" / "compose.db.yml").read_text(encoding="utf-8")
        assembler = ASSEMBLER.read_text(encoding="utf-8")
        verifier = VERIFY_RELEASE.read_text(encoding="utf-8")
        self.assertIn(
            "# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5",
            dockerfile,
        )
        self.assertIn("postgres:16.14-alpine3.24@sha256:", dockerfile)
        self.assertIn("PGVECTOR_VERSION=0.8.6", dockerfile)
        self.assertIn(
            "PGVECTOR_SHA256=10bf9938906e5d643bbc4a7eea104b6f57ba4898e5b76b20e60484ea1d5a7f8f",
            dockerfile,
        )
        self.assertIn("RUN rm -f /usr/local/bin/gosu", dockerfile)
        self.assertIn("USER 70:70", dockerfile)
        self.assertIn("${P1_POSTGRES_IMAGE:-capstone-postgres-pgvector:p1-local}", compose)
        self.assertIn("P1_POSTGRES_IMAGE", assembler)
        self.assertIn("COSIGN_POSTGRES_MANIFEST", verifier)
        self.assertIn("GITHUB_POSTGRES", verifier)

    def test_runtime_images_and_secret_boundary_are_pinned(self) -> None:
        python_dockerfile = (
            REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "python-services.Dockerfile"
        ).read_text(encoding="utf-8")
        spring_dockerfile = (
            REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "spring-api.Dockerfile"
        ).read_text(encoding="utf-8")
        build = (
            REPOSITORY_ROOT / "workspaces" / "decision-platform" / "spring-api" / "build.gradle.kts"
        ).read_text(encoding="utf-8")
        compose = (REPOSITORY_ROOT / "deploy" / "p1" / "compose.db.yml").read_text(encoding="utf-8")
        p1ctl = (REPOSITORY_ROOT / "deploy" / "p1" / "p1ctl").read_text(encoding="utf-8")
        frontend = "# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5"
        self.assertIn(frontend, python_dockerfile)
        self.assertIn(frontend, spring_dockerfile)
        self.assertIn("cgr.dev/chainguard/python:latest-dev@sha256:", python_dockerfile)
        self.assertIn("python-3.14=3.14.7-r1", python_dockerfile)
        self.assertIn("libgomp=16.2.0-r0", python_dockerfile)
        self.assertNotIn("python-runtime-overrides.txt", python_dockerfile)
        self.assertIn("uv sync --frozen", python_dockerfile)
        self.assertIn("cgr.dev/chainguard/wolfi-base:latest@sha256:", spring_dockerfile)
        self.assertIn("openjdk-25-jre=25.0.4.1-r0", spring_dockerfile)
        self.assertIn('runtimeOnly("org.postgresql:postgresql:42.7.12")', build)
        self.assertIn('group_add: ["${P1_SECRET_GID}"]', compose)
        self.assertIn('chmod 640 "$SECRETS_DIR"/*', p1ctl)

    def test_gradle_dependencies_are_locked_and_checksum_verified(self) -> None:
        project = REPOSITORY_ROOT / "workspaces" / "decision-platform" / "spring-api"
        build = (project / "build.gradle.kts").read_text(encoding="utf-8")
        lock = (project / "gradle.lockfile").read_text(encoding="utf-8")
        verification = (project / "gradle" / "verification-metadata.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn("lockAllConfigurations()", build)
        self.assertIn("org.postgresql:postgresql:42.7.12", lock)
        self.assertIn("<verification-metadata", verification)
        self.assertIn("<sha256 value=", verification)

    def test_docker_context_excludes_owner_private_runtime_state(self) -> None:
        dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("deploy/p1/.state", dockerignore.splitlines())
        self.assertIn("deploy/p1/.state/**", dockerignore.splitlines())
        self.assertIn("**/.state", dockerignore.splitlines())

    def test_release_build_is_unprivileged_and_publish_uses_only_scanned_images(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        assembler = ASSEMBLER.read_text(encoding="utf-8")
        verifier = VERIFY_RELEASE.read_text(encoding="utf-8")
        build = workflow.split("  build-scan:\n", 1)[1].split("\n  publish:\n", 1)[0]
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertEqual(workflow.count("          context: .\n"), 4)
        self.assertNotIn("GIT_AUTH_TOKEN", workflow)
        self.assertNotIn("packages: write", build)
        self.assertNotIn("id-token: write", build)
        self.assertNotIn("attestations: write", build)
        self.assertIn("packages: write", publish)
        self.assertNotIn("docker/build-push-action", publish)
        self.assertNotIn("apt-get", publish)
        self.assertIn("Upload scanned immutable build outputs", build)
        self.assertIn("Load only the scanned image archive", publish)
        self.assertLess(
            publish.index("Load only the scanned image archive"),
            publish.index("Log in only after all scans pass"),
        )
        self.assertIn("P1_RELEASE_VERSION_COLLISION", workflow)
        self.assertIn('git -C "$REPOSITORY_ROOT" archive --format=tar --output=', assembler)
        for field in (
            "configSha256",
            "sourceArchiveSha256",
            "licenseSha256",
            "imageArchiveEntries",
        ):
            self.assertIn(field, assembler)
            self.assertIn(field, verifier)
        self.assertIn('stage-archive "$bundle/images.tar"', verifier)
        self.assertIn('stage-bundle "$source" "$stage_parent/bundle"', verifier)
        self.assertLess(verifier.index("archive-compare"), verifier.index("docker load"))
        self.assertIn("publish-directory", assembler)
        self.assertIn("publish-file", assembler)
        self.assertIn("publish-file", workflow)
        self.assertNotIn('rm -- "release-output/$name.tar.zst"', workflow)

    def test_activation_rebinds_every_release_path_to_verified_snapshot(self) -> None:
        p1ctl = (REPOSITORY_ROOT / "deploy" / "p1" / "p1ctl").read_text(encoding="utf-8")
        prepare = p1ctl.index("prepare_release_boundary")
        load = p1ctl.index("load_release_images", p1ctl.rindex("prepare_release_boundary"))
        self.assertLess(prepare, load)
        self.assertIn('verify-release" --stage-to "$sealed_root"', p1ctl)
        self.assertIn("RELEASE_MANIFEST=$sealed_root/release-manifest.json", p1ctl)
        self.assertIn("RELEASE_GUARD=$SCRIPT_DIR/release_guard.py", p1ctl)
        self.assertIn('verify-release" --sealed', p1ctl)
        self.assertIn('P1_STATE_DIR="$STATE_DIR" P1_PROJECT_NAME="$PROJECT_NAME"', p1ctl)

    def test_release_publishes_pre_extract_signed_archive_checksums(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        checksum = workflow.index('sha256sum "$name.tar.zst"')
        signature = workflow.index('"release-output/$name.tar.zst.sha256"')
        self.assertLess(checksum, signature)
        self.assertIn("release-output/*.tar.zst.sha256*", workflow)

    def test_release_carries_the_repository_agpl_license_exactly(self) -> None:
        spring_dockerfile = (
            REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "spring-api.Dockerfile"
        ).read_text(encoding="utf-8")
        python_dockerfile = (
            REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "python-services.Dockerfile"
        ).read_text(encoding="utf-8")
        assembler = ASSEMBLER.read_text(encoding="utf-8")
        verifier = VERIFY_RELEASE.read_text(encoding="utf-8")
        license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertTrue(
            license_text.startswith("                    GNU AFFERO GENERAL PUBLIC LICENSE\n")
        )
        self.assertIn('org.opencontainers.image.licenses="AGPL-3.0-only"', spring_dockerfile)
        self.assertIn('org.opencontainers.image.licenses="AGPL-3.0-only"', python_dockerfile)
        self.assertIn('cp -- "$REPOSITORY_ROOT/LICENSE" "$bundle/LICENSE"', assembler)
        self.assertIn('chmod 0444 "$bundle/LICENSE"', assembler)
        self.assertIn('root / "LICENSE"', verifier)
        self.assertIn("bundle AGPL license text is incomplete", verifier)

    def test_kafka_release_uses_sasl_capable_jvm_broker_without_unscanned_ui(self) -> None:
        compose = (REPOSITORY_ROOT / "deploy" / "p1" / "compose.kafka.yml").read_text(
            encoding="utf-8"
        )
        verifier = VERIFY_RELEASE.read_text(encoding="utf-8")
        self.assertIn("${P1_KAFKA_IMAGE:-capstone-kafka:p1-local}", compose)
        kafka_dockerfile = (
            REPOSITORY_ROOT / "deploy" / "p1" / "docker" / "kafka.Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn("FROM apache/kafka:4.3.1@sha256:ccd1314e47ec", kafka_dockerfile)
        self.assertIn("/opt/kafka/libs/jline-3.30.4.jar", kafka_dockerfile)
        self.assertNotIn("org/jline/jline", kafka_dockerfile)
        self.assertIn('entrypoint: ["/usr/local/bin/p1-secret-entrypoint", "kafka-admin"]', compose)
        self.assertIn('command: ["python", "-m", "app.async_worker.kafka_topics"]', compose)
        self.assertNotIn("kafka-ui", compose)
        self.assertIn("kafka-volume-init:", compose)
        self.assertIn("cap_add: [CHOWN, FOWNER]", compose)
        self.assertIn("network_mode: none", compose)
        self.assertIn("kafka-volume-init: {condition: service_completed_successfully}", compose)
        self.assertIn('"role-bootstrap"', verifier)
        self.assertIn('services["role-bootstrap"]["image"] == spring_ref', verifier)
        self.assertIn('services["kafka-topic-init"]["image"] == python_ref', verifier)

    def test_loopback_edge_does_not_attach_application_namespace_to_egress(self) -> None:
        compose = (REPOSITORY_ROOT / "deploy" / "p1" / "compose.db.yml").read_text(encoding="utf-8")
        offline = (REPOSITORY_ROOT / "deploy" / "p1" / "compose.offline.db.yml").read_text(
            encoding="utf-8"
        )
        proxy = (
            REPOSITORY_ROOT
            / "workspaces"
            / "decision-platform"
            / "python-services"
            / "app"
            / "s8_demo"
            / "tcp_proxy.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:${P1_API_PORT:-18080}:8080"', compose)
        self.assertIn("networks: [p1-internal, p1-edge]", compose)
        self.assertIn("runtime-netns:\n", compose)
        runtime_block = compose.split("  runtime-netns:\n", 1)[1].split("\n  api-edge:", 1)[0]
        self.assertNotIn("p1-edge", runtime_block)
        self.assertIn("p1-internal:\n    internal: true", compose)
        self.assertIn("api-edge: {pull_policy: never}", offline)
        self.assertIn("role-bootstrap: {pull_policy: never}", offline)
        self.assertIn('UPSTREAM_HOST = "runtime-netns"', proxy)
        self.assertIn("UPSTREAM_PORT = 8080", proxy)
        self.assertIn("MAX_CONNECTIONS = 64", proxy)
        self.assertIn("IDLE_TIMEOUT_SECONDS = 30.0", proxy)

    def test_signed_author_command_documents_all_required_security_arguments(self) -> None:
        api = (REPOSITORY_ROOT / "docs" / "API_명세서.md").read_text(encoding="utf-8")
        start = api.index("p1-verify author --approval-id")
        command = api[start : api.index("p1-verify run", start)]
        for argument in (
            "--approval-id",
            "--output-root",
            "--kis-token-cap",
            "--private-key",
            "--issuer-key-id",
            "--reason-code",
        ):
            self.assertIn(argument, command)


if __name__ == "__main__":
    unittest.main()
