"""Verified HTTP sessions with relocatable CA trust for frozen applications."""
import ssl

import aiohttp
import certifi


def create_http_session(**kwargs) -> aiohttp.ClientSession:
    # Keep system and SSL_CERT_FILE/SSL_CERT_DIR trust, then add the shipped CA
    # bundle. Frozen macOS Python can retain the build machine's OpenSSL path.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=context)
    return aiohttp.ClientSession(connector=connector, **kwargs)
