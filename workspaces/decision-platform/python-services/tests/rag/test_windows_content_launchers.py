from __future__ import annotations

import re
from pathlib import Path


EXPECTED = {
    "setup-rag-content.bat": "setup",
    "rag-import-auto.bat": "import-auto",
    "rag-import-cpu.bat": "import-cpu",
    "rag-import-intel-gpu.bat": "import-intel-gpu",
    "rag-import-nvidia-gpu.bat": "import-nvidia-gpu",
    "rag-import-status.bat": "status",
    "rag-remove-document.bat": "remove-document",
    "rag-cache-clean.bat": "cache-clean",
}


def test_windows_bat_launchers_are_thin_quoted_wrappers_with_exact_command_mapping() -> None:
    root = Path(__file__).resolve().parents[4] / "capstone-rag/tools/windows"

    assert {path.name for path in root.glob("*.bat")} == set(EXPECTED)
    for name, command in EXPECTED.items():
        text = (root / name).read_text(encoding="utf-8")
        assert "@echo off" in text.lower()
        assert '"%~dp0rag-content.ps1"' in text
        assert re.search(rf"-Command\s+{re.escape(command)}(?:\s|$)", text)
        assert "%*" in text
        assert "curl" not in text.lower()
        assert "token" not in text.lower()


def test_windows_powershell_launcher_pins_uv_projects_and_never_silently_falls_back() -> None:
    root = Path(__file__).resolve().parents[4] / "capstone-rag/tools/windows"
    text = (root / "rag-content.ps1").read_text(encoding="utf-8")

    assert "Set-StrictMode -Version Latest" in text
    assert "$ErrorActionPreference = 'Stop'" in text
    assert "--frozen" in text
    assert "ocr/cpu" in text.replace("\\", "/")
    assert "ocr/intel" in text.replace("\\", "/")
    assert "ocr/nvidia" in text.replace("\\", "/")
    assert "OPENVINO_DEVICE=GPU" in text
    assert "NOT_RUN_NO_NVIDIA" in text
    assert "fallback" not in text.lower()
    assert "Invoke-Expression" not in text
    assert "Start-Process" not in text


def test_ocr_subprojects_have_independent_frozen_locks_and_ignore_venvs() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    ocr_root = repository_root / "capstone-rag/ocr"

    for lane in ("cpu", "intel", "nvidia"):
        project = ocr_root / lane
        assert (project / "pyproject.toml").is_file()
        assert (project / "uv.lock").is_file()
        assert ".venv" in (project / ".gitignore").read_text(encoding="utf-8")
    assert "UNLIMITED_GGUF" not in (
        ocr_root / "cpu/pyproject.toml"
    ).read_text(encoding="utf-8")

