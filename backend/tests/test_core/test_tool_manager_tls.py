"""Exercise real TLS downloads against an isolated loopback server."""
import ipaddress
import ssl
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi
import pytest
import pytest_asyncio
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from src.core.tool_manager import ToolManager


@pytest_asyncio.fixture
async def tls_download_server(tmp_path):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'VidFlow test CA')])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
                   .issuer_name(ca_name).public_key(server_key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([
                       x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                   ]), critical=False)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                   .sign(ca_key, hashes.SHA256()))
    ca_path = tmp_path / 'ca.pem'
    cert_path = tmp_path / 'server.pem'
    key_path = tmp_path / 'server-key.pem'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
                                                serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    app = web.Application()

    async def download(request):
        return web.Response(body=b'verified tool download', headers={'ETag': 'tls-fixture'})

    app.router.add_get('/tool', download)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=server_context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f'https://127.0.0.1:{port}/tool', ca_path
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_tool_download_uses_bundled_ca_and_keeps_metadata_trusted(tls_download_server, tmp_path, monkeypatch):
    url, ca_path = tls_download_server
    monkeypatch.setattr(certifi, 'where', lambda: str(ca_path))
    manager = ToolManager()
    manager.proxy = None
    target = tmp_path / 'tool.bin'
    await manager._download_with_progress(url, target, 'ffmpeg')
    assert target.read_bytes() == b'verified tool download'
    assert (await manager._get_remote_headers(url))['etag'] == 'tls-fixture'


@pytest.mark.asyncio
async def test_tool_download_preserves_explicit_custom_ca(tls_download_server, tmp_path, monkeypatch):
    url, ca_path = tls_download_server
    monkeypatch.setenv('SSL_CERT_FILE', str(ca_path))
    manager = ToolManager()
    manager.proxy = None
    target = tmp_path / 'tool.bin'
    await manager._download_with_progress(url, target, 'yt-dlp')
    assert target.read_bytes() == b'verified tool download'


@pytest.mark.asyncio
async def test_tool_download_still_rejects_untrusted_certificates(tls_download_server, tmp_path):
    url, _ = tls_download_server
    manager = ToolManager()
    manager.proxy = None
    target = tmp_path / 'untrusted.bin'
    with pytest.raises(aiohttp.ClientConnectorCertificateError):
        await manager._download_with_progress(url, target, 'yt-dlp')
    assert not target.exists()


@pytest.mark.asyncio
async def test_proxy_failure_falls_back_to_verified_direct_download(tls_download_server, tmp_path, monkeypatch):
    url, ca_path = tls_download_server
    monkeypatch.setattr(certifi, 'where', lambda: str(ca_path))
    manager = ToolManager()
    manager.proxy = 'http://127.0.0.1:1'
    target = tmp_path / 'tool.bin'
    await manager._download_with_progress(url, target, 'yt-dlp')
    assert target.read_bytes() == b'verified tool download'
