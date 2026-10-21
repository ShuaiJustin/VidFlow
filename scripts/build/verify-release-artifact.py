"""Audit a locally built unpacked release without launching the application."""
import argparse
import hashlib
import json
import re
import types
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


PRIVATE_KEY = re.compile(
    rb"-----BEGIN ((?:[A-Z]+ )*PRIVATE KEY)-----\s+"
    rb"[A-Za-z0-9+/=\r\n]+-----END \1-----"
)
UPDATER_FILES = ("updater-core.js", "updater-security.js", "updater-custom.js", "updater-github.js",
                 "updater-health.js", "update-public-key.pem")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(unpacked, built_backend, project):
    resources = unpacked / "resources"
    app = resources / "app"
    issues = []
    source_paths = {str(project), str(Path.home())}
    needles = set()
    for source_path in source_paths:
        for spelling in (source_path, source_path.replace("\\", "/")):
            needles.update((spelling.encode(), spelling.encode("utf-16-le")))

    def inspect_bytes(data, label):
        if PRIVATE_KEY.search(data):
            issues.append({"path": label, "kind": "complete-private-key"})
        if any(needle in data for needle in needles):
            issues.append({"path": label, "kind": "local-machine-path"})

    for name in UPDATER_FILES:
        shipped = app / "electron" / name
        if not shipped.is_file() or sha256(shipped) != sha256(project / "electron" / name):
            issues.append({"path": f"electron/{name}", "kind": "missing-or-stale-updater"})
    if list((app / "electron").rglob("*.test.js")) or (app / "electron" / "test-main.js").exists():
        issues.append({"path": "electron", "kind": "test-entry-packaged"})
    shipped_backend = resources / "backend" / "VidFlow-Backend.exe"
    if not shipped_backend.is_file() or sha256(shipped_backend) != sha256(built_backend):
        issues.append({"path": "backend/VidFlow-Backend.exe", "kind": "missing-or-stale-backend"})

    files = total_bytes = 0
    # Linear streaming scan: O(total bytes), bounded chunk memory. Never print matched data.
    for path in resources.rglob("*"):
        if not path.is_file():
            continue
        files += 1
        total_bytes += path.stat().st_size
        label = str(path.relative_to(unpacked))
        if path.name.lower() in {".env", "cookies.txt", "cookies.json", "login data", "config.json"} or path.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".pfx", ".p12"}:
            issues.append({"path": label, "kind": "unexpected-user-data-file"})
        tail = b""
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                before = len(issues)
                inspect_bytes(tail + chunk, label)
                if len(issues) > before:
                    break
                tail = chunk[-8192:]

    modules = 0

    def inspect_code(code, label):
        inspect_bytes(code.co_filename.encode(), label)
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                inspect_code(constant, label)
            elif isinstance(constant, str):
                inspect_bytes(constant.encode("utf-8", errors="replace"), label)
            elif isinstance(constant, bytes):
                inspect_bytes(constant, label)

    archive = CArchiveReader(str(built_backend))
    for name, entry in archive.toc.items():
        if entry[-1] != "z":
            continue
        pyz = archive.open_embedded_archive(name)
        for module in pyz.toc:
            code = pyz.extract(module)
            if isinstance(code, types.CodeType):
                modules += 1
                inspect_code(code, f"PYZ:{module}")
    unique_issues = [dict(item) for item in sorted({tuple(sorted(item.items())) for item in issues})]
    return {"pass": not unique_issues, "package_files": files, "package_bytes": total_bytes,
            "embedded_modules_inspected": modules, "issues": unique_issues,
            "scope": "Updater/source hashes, new backend, test entries, known local paths, user-data filenames and complete PEM private keys; not an exhaustive secret scanner."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unpacked", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    report = audit(args.unpacked.resolve(strict=True), args.backend.resolve(strict=True), project)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
