"""Split macOS FFmpeg archives must not remove the sibling executable."""
import zipfile

import pytest
from aiohttp import web
from src.core import tool_manager as module


@pytest.fixture
def isolated_manager(tmp_path, monkeypatch):
    tools = tmp_path / 'tools'
    binary_dir = tools / 'bin'
    models = tools / 'models'
    binary_dir.mkdir(parents=True)
    models.mkdir()
    monkeypatch.setattr(module, 'TOOLS_DIR', tools)
    monkeypatch.setattr(module, 'BIN_DIR', binary_dir)
    monkeypatch.setattr(module, 'MODELS_DIR', models)
    manager = module.ToolManager()
    manager.system = 'Darwin'
    return manager, binary_dir


def archive(tmp_path, filename, contents):
    result = tmp_path / filename
    with zipfile.ZipFile(result, 'w') as output:
        for name, data in contents.items():
            output.writestr(name, data)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('first,second', [('ffmpeg', 'ffprobe'), ('ffprobe', 'ffmpeg')])
async def test_separate_mac_archives_keep_both_tools(isolated_manager, tmp_path, first, second):
    manager, binary_dir = isolated_manager
    await manager.extract_ffmpeg(archive(tmp_path, 'first.zip', {first: first.encode()}))
    await manager.extract_ffmpeg(archive(tmp_path, 'second.zip', {second: second.encode()}))
    assert (binary_dir / first).read_bytes() == first.encode()
    assert (binary_dir / second).read_bytes() == second.encode()


@pytest.mark.asyncio
async def test_ffprobe_update_does_not_touch_running_ffmpeg(isolated_manager, tmp_path, monkeypatch):
    manager, binary_dir = isolated_manager
    (binary_dir / 'ffmpeg').write_bytes(b'existing ffmpeg')
    (binary_dir / 'ffprobe').write_bytes(b'old ffprobe')
    monkeypatch.setattr(manager, '_is_file_in_use', lambda target: target.name == 'ffmpeg')
    await manager.extract_ffmpeg(archive(tmp_path, 'probe.zip', {'ffprobe': b'new ffprobe'}))
    assert (binary_dir / 'ffmpeg').read_bytes() == b'existing ffmpeg'
    assert (binary_dir / 'ffprobe').read_bytes() == b'new ffprobe'


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', ['ffmpeg', 'ffprobe'])
@pytest.mark.parametrize('machine,key', [('arm64', 'Darwin-arm64'), ('x86_64', 'Darwin')])
async def test_mac_tool_download_falls_back_when_primary_returns_521(
    isolated_manager, tmp_path, monkeypatch, tool, machine, key
):
    manager, _ = isolated_manager
    seen = []
    payload = b'fallback download bytes'
    async def primary(request):
        seen.append('primary')
        return web.Response(status=521)
    async def fallback(request):
        seen.append('fallback')
        return web.Response(body=payload)
    application = web.Application()
    application.router.add_get('/primary.zip', primary)
    application.router.add_get('/fallback.zip', fallback)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    primary_url = f'http://127.0.0.1:{port}/primary.zip'
    fallback_url = f'http://127.0.0.1:{port}/fallback.zip'
    monkeypatch.setattr(module.platform, 'machine', lambda: machine)
    monkeypatch.setitem(module.TOOL_URLS[tool], key, primary_url)
    monkeypatch.setattr(module, 'TOOL_FALLBACK_URLS', {tool: {key: [fallback_url]}}, raising=False)
    target = tmp_path / 'download.zip'
    try:
        await manager._download_with_progress(primary_url, target, 'ffmpeg')
        assert target.read_bytes() == payload
        assert seen == ['primary', 'fallback']
    finally:
        await runner.cleanup()
