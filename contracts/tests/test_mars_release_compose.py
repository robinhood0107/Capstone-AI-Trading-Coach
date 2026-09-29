from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "deploy/p1/render_mars_release_compose.py"
SPEC = importlib.util.spec_from_file_location("mars_release_compose", RENDERER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MarsReleaseComposeTest(unittest.TestCase):
    source_sha = "a" * 40

    def manifest(self, product: str, tag: str = "v2.0.0") -> dict[str, object]:
        parts = ("web",) if product == "demo" else ("api", "web", "postgres", "redis")
        images: dict[str, dict[str, str]] = {}
        for part_index, part in enumerate(parts):
            digest_char = str((part_index + (1 if product == "full" else 0)) % 10)
            images[f"{product}-{part}"] = {
                "reference": f"pjjpjj111/mars-{product}:{tag}-{part}",
                "digest": f"sha256:{digest_char * 64}",
            }
        return {"sourceSha": self.source_sha, "tag": tag, "images": images}

    def source_compose(self, product: str) -> str:
        return (ROOT / f"deploy/p1/compose.public-{product}.yml").read_text(encoding="utf-8")

    def test_full_semver_manifest_contains_only_full_images(self) -> None:
        manifest = self.manifest("full", "v1.0.12")
        rendered = MODULE.render_compose("full", self.source_compose("full"), manifest)
        image_lines = [line for line in rendered.splitlines() if line.lstrip().startswith("image:")]
        self.assertTrue(image_lines)
        self.assertTrue(all("pjjpjj111/mars-full@sha256:" in line for line in image_lines))
        self.assertTrue(all("pjjpjj111/mars-demo" not in line for line in image_lines))
        self.assertIn("Generated from compose.public-full.yml for v1.0.12", rendered)

    def test_demo_release_renders_one_web_image_and_no_full_resources(self) -> None:
        manifest = self.manifest("demo", "v2.0.0")
        rendered = MODULE.render_compose("demo", self.source_compose("demo"), manifest)
        data = yaml.safe_load(rendered)
        services = data["services"]
        self.assertEqual(set(services), {"web"})
        self.assertEqual(services["web"]["image"], "pjjpjj111/mars-demo@sha256:" + "0" * 64)
        self.assertEqual(set(services["web"]["volumes"][0].split(":")), {"demo-state", "/data"})
        self.assertEqual(set(services["web"]["secrets"]), {"session-signing-key", "vertex-service-account"})
        serialized = json.dumps(data, sort_keys=True)
        self.assertNotIn("mars-full", serialized)
        self.assertNotIn("full-postgres", serialized)
        self.assertNotIn("brokerage-kek", serialized)
        self.assertNotIn("/api/v1/auth", serialized)

    def test_demo_manifest_rejects_full_images_and_unpinned_or_mismatched_images(self) -> None:
        manifest = self.manifest("demo")
        images = manifest["images"]
        assert isinstance(images, dict)
        images["full-api"] = {"reference": "pjjpjj111/mars-full:v2.0.0-api", "digest": "sha256:" + "1" * 64}
        with self.assertRaises(ValueError):
            MODULE.render_compose("demo", self.source_compose("demo"), manifest)

        manifest = self.manifest("demo")
        manifest["images"]["demo-web"]["reference"] = "pjjpjj111/mars-demo:v2.0.0-api"
        with self.assertRaises(ValueError):
            MODULE.render_compose("demo", self.source_compose("demo"), manifest)

    def test_demo_version_tag_does_not_require_or_collide_with_full_version(self) -> None:
        demo = self.manifest("demo", "v2.0.0")
        full = self.manifest("full", "v1.0.12")
        self.assertNotEqual(demo["tag"], full["tag"])
        self.assertIn("v2.0.0-web", demo["images"]["demo-web"]["reference"])
        self.assertIn("v1.0.12-web", full["images"]["full-web"]["reference"])

    def test_commit_suffix_must_match_source_sha_when_present(self) -> None:
        manifest = self.manifest("demo", "v2.0.0-" + "b" * 12)
        with self.assertRaises(ValueError):
            MODULE.render_compose("demo", self.source_compose("demo"), manifest)


if __name__ == "__main__":
    unittest.main()
