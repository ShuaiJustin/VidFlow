from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VenvProbeResult:
    healthy: bool
    reason: str
    version_output: str | None = None


def _venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _expected_version_prefix() -> str:
    return f"Python {sys.version_info.major}.{sys.version_info.minor}"


def _run_python_version(python_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python_path), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )


def probe_venv(venv_dir: Path) -> VenvProbeResult:
    if not venv_dir.exists():
        return VenvProbeResult(False, f"virtual environment not found: {venv_dir}")

    cfg_path = venv_dir / "pyvenv.cfg"
    if not cfg_path.exists():
        return VenvProbeResult(False, f"missing pyvenv.cfg in {venv_dir}")

    python_path = _venv_python_path(venv_dir)
    if not python_path.exists():
        return VenvProbeResult(False, f"missing virtual environment interpreter: {python_path}")

    try:
        version_result = _run_python_version(python_path)
    except OSError as exc:
        return VenvProbeResult(False, f"failed to launch virtual environment interpreter: {exc}")

    version_output = (version_result.stdout or version_result.stderr).strip()
    if version_result.returncode != 0:
        reason = version_output or f"launcher exited with code {version_result.returncode}"
        return VenvProbeResult(False, reason)

    expected_prefix = _expected_version_prefix()
    if not version_output.startswith(expected_prefix):
        return VenvProbeResult(
            False,
            f"version mismatch, expected {expected_prefix} but found {version_output}",
            version_output=version_output,
        )

    return VenvProbeResult(True, f"virtual environment is healthy: {version_output}", version_output=version_output)


def _remove_readonly(func, path: str, _: tuple[type[BaseException], BaseException, object]) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _recreate_venv(venv_dir: Path) -> None:
    if venv_dir.exists():
        shutil.rmtree(venv_dir, onerror=_remove_readonly)

    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    create_result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if create_result.returncode != 0:
        output = (create_result.stdout + create_result.stderr).strip()
        raise RuntimeError(output or "python -m venv failed")


def ensure_venv(venv_dir: Path) -> str:
    probe_result = probe_venv(venv_dir)

    if probe_result.healthy:
        print(f"[INFO] {probe_result.reason}")
        return "unchanged"

    if venv_dir.exists():
        print(f"[WARN] Existing virtual environment is invalid: {probe_result.reason}")
        _recreate_venv(venv_dir)
        print(f"[OK] Recreated virtual environment with {_expected_version_prefix()}")
        return "recreated"

    print(f"[INFO] {probe_result.reason}")
    _recreate_venv(venv_dir)
    print(f"[OK] Created virtual environment with {_expected_version_prefix()}")
    return "created"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: ensure_venv.py <venv_dir>", file=sys.stderr)
        return 2

    venv_dir = Path(sys.argv[1]).resolve()

    try:
        ensure_venv(venv_dir)
    except Exception as exc:  # pragma: no cover - CLI safeguard
        print(f"[ERROR] Failed to prepare virtual environment: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
