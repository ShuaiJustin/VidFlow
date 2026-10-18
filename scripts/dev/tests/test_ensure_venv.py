from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "dev" / "ensure_venv.py"


def _create_venv(venv_dir: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )


def _run_ensure_venv(venv_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(venv_dir)],
        capture_output=True,
        text=True,
    )


def _run_venv_python(venv_dir: Path) -> subprocess.CompletedProcess[str]:
    python_path = venv_dir / "Scripts" / "python.exe"
    return subprocess.run(
        [str(python_path), "--version"],
        capture_output=True,
        text=True,
    )


def _break_venv_launcher(venv_dir: Path) -> None:
    cfg_path = venv_dir / "pyvenv.cfg"
    broken_home = r"C:\Users\someone\AppData\Local\Programs\Python\Python311"
    broken_python = broken_home + r"\python.exe"

    lines = []
    for line in cfg_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("home = "):
            lines.append(f"home = {broken_home}")
        elif line.startswith("executable = "):
            lines.append(f"executable = {broken_python}")
        elif line.startswith("command = "):
            lines.append(f"command = {broken_python} -m venv {venv_dir}")
        else:
            lines.append(line)

    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_creates_missing_virtual_environment(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"

    result = _run_ensure_venv(venv_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (venv_dir / "Scripts" / "python.exe").exists()

    version_result = _run_venv_python(venv_dir)
    assert version_result.returncode == 0, version_result.stdout + version_result.stderr
    assert "created" in result.stdout.lower()


def test_recreates_broken_virtual_environment(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    _create_venv(venv_dir)
    _break_venv_launcher(venv_dir)

    broken_result = _run_venv_python(venv_dir)
    assert broken_result.returncode != 0

    result = _run_ensure_venv(venv_dir)

    assert result.returncode == 0, result.stdout + result.stderr

    repaired_result = _run_venv_python(venv_dir)
    assert repaired_result.returncode == 0, repaired_result.stdout + repaired_result.stderr

    cfg_contents = (venv_dir / "pyvenv.cfg").read_text(encoding="utf-8")
    assert r"C:\Users\someone\AppData\Local\Programs\Python\Python311" not in cfg_contents
    assert "recreated" in result.stdout.lower()
