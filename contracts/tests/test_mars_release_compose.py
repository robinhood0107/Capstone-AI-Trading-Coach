from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "deploy/p1/render_mars_release_compose.py"


class MarsReleaseComposeTest(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        source_sha = "a" * 40
        tag = f"v0.1.0-{source_sha[:12]}"
        images: dict[str, dict[str, str]] = {}
        for product_index, product in enumerate(("demo", "full")):
            for part_index, part in enumerate(("api", "web", "postgres", "redis")):
                digest_char = str((product_index * 4 + part_index) % 10)
                images[f"{product}-{part}"] = {
                    "reference": f"pjjpjj111/mars-{product}:{tag}-{part}",
                    "digest": f"sha256:{digest_char * 64}",
                }
        return {"sourceSha": source_sha, "tag": tag, "images": images}

    def test_both_public_products_render_all_images_as_digest_pins(self) -> None:
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            images_path = Path(directory) / "mars-images.json"
            images_path.write_text(json.dumps(manifest), encoding="utf-8")
            for product in ("demo", "full"):
                output = Path(directory) / f"{product}.compose.yml"
                subprocess.run(
                    [
                        sys.executable,
                        str(RENDERER),
                        "--product",
                        product,
                        "--images",
                        str(images_path),
                        "--output",
                        str(output),
                    ],
                    check=True,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                rendered = output.read_text(encoding="utf-8")
                image_lines = [line for line in rendered.splitlines() if line.lstrip().startswith("image:")]
                self.assertTrue(image_lines)
                self.assertTrue(all("@sha256:" in line for line in image_lines))
                self.assertNotIn("MARS_DEMO_TAG", rendered)
                self.assertNotIn("MARS_FULL_TAG", rendered)
                self.assertTrue(all(f"pjjpjj111/mars-{product}@sha256:" in line for line in image_lines))
                docker = "/usr/bin/docker" if Path("/usr/bin/docker").exists() else shutil.which("docker")
                if docker is None:
                    self.skipTest("Docker Compose is required to validate a rendered release file")
                environment = {
                    **os.environ,
                    "MARS_DEMO_SECRET_GID": "1000",
                    "MARS_DEMO_SECRETS_DIR": "/tmp/mars-demo-release-compose-secrets",
                    "MARS_FULL_SECRET_GID": "1000",
                    "MARS_FULL_SECRETS_DIR": "/tmp/mars-full-release-compose-secrets",
                    "MARS_FULL_BROKERAGE_KEK_DIR": "/tmp/mars-full-release-compose-kek",
                    "MARS_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256": "a" * 64,
                    "MARS_VERTEX_MODEL_ID": "test-model",
                    "MARS_VERTEX_PROJECT_ID": "test-project",
                }
                config = subprocess.run(
                    [docker, "compose", "-f", str(output), "config", "--format", "json"],
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(config.returncode, 0, config.stderr)
                services = json.loads(config.stdout)["services"]
                self.assertTrue(services)
                self.assertTrue(
                    all(service["image"].startswith(f"pjjpjj111/mars-{product}@sha256:") for service in services.values())
                )

    def test_incomplete_or_mismatched_image_manifest_is_rejected(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("mars_release_compose", RENDERER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = self.manifest()
        source = (ROOT / "deploy/p1/compose.public-demo.yml").read_text(encoding="utf-8")
        manifest["images"].pop("demo-web")  # type: ignore[index,union-attr]
        with self.assertRaises(ValueError):
            module.render_compose("demo", source, manifest)


if __name__ == "__main__":
    unittest.main()
