"""Release audit must reject actual signing keys while allowing public keys."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


spec = importlib.util.spec_from_file_location(
    "release_audit", Path(__file__).resolve().parents[1] / "verify-release-artifact.py"
)
release_audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_audit)


class EmptyArchive:
    toc = {}


class ReleaseAuditTests(unittest.TestCase):
    def audit_key(self, contents):
        with tempfile.TemporaryDirectory(prefix="vidflow-audit-test-") as directory:
            root = Path(directory)
            project = root / "source"
            package = root / "package"
            source_electron = project / "electron"
            shipped_electron = package / "resources" / "app" / "electron"
            source_electron.mkdir(parents=True)
            shipped_electron.mkdir(parents=True)
            for name in release_audit.UPDATER_FILES:
                (source_electron / name).write_bytes(b"fixture")
                (shipped_electron / name).write_bytes(b"fixture")
            backend = root / "built-backend.exe"
            backend.write_bytes(b"fixture")
            shipped_backend = package / "resources" / "backend" / backend.name
            shipped_backend = shipped_backend.with_name("VidFlow-Backend.exe")
            shipped_backend.parent.mkdir(parents=True)
            shipped_backend.write_bytes(backend.read_bytes())
            (package / "resources" / "signing-key.pem").write_bytes(contents)
            with patch.object(release_audit, "CArchiveReader", return_value=EmptyArchive()):
                return release_audit.audit(package, backend, project)

    def test_ed25519_pkcs8_private_key_rejected(self):
        key = ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        report = self.audit_key(pem)
        self.assertFalse(report["pass"])
        self.assertEqual([item["kind"] for item in report["issues"]], ["complete-private-key"])

    def test_rsa_traditional_private_key_rejected(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption())
        self.assertFalse(self.audit_key(pem)["pass"])

    def test_ed25519_public_key_allowed(self):
        key = ed25519.Ed25519PrivateKey.generate().public_key()
        pem = key.public_bytes(serialization.Encoding.PEM,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertTrue(self.audit_key(pem)["pass"])


if __name__ == "__main__":
    unittest.main()
