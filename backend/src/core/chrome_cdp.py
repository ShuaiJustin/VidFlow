"""Launch and inspect a dedicated, persistent Chrome profile through CDP."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import websocket


class ChromeCdpError(RuntimeError):
    pass


def find_chrome_executable() -> Optional[Path]:
    candidates: List[Path] = []
    for env_name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')

    executable = shutil.which('chrome') or shutil.which('google-chrome')
    if executable:
        candidates.append(Path(executable))

    return next((path for path in candidates if path.is_file()), None)


def find_available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return int(listener.getsockname()[1])


def build_chrome_command(
    chrome_path: Path,
    profile_dir: Path,
    target_url: str,
    debug_port: int,
) -> List[str]:
    return [
        str(chrome_path),
        f'--remote-debugging-port={debug_port}',
        f'--user-data-dir={profile_dir}',
        '--no-first-run',
        '--no-default-browser-check',
        '--start-maximized',
        target_url,
    ]


def _page_key(url: str) -> str:
    try:
        parsed = urlsplit(url)
        return f'{(parsed.hostname or "").lower()}{parsed.path.rstrip("/") or "/"}'
    except (TypeError, ValueError):
        return ''


def select_page_target(targets: Iterable[Dict[str, Any]], requested_url: str) -> Dict[str, Any]:
    pages = [
        target for target in targets
        if target.get('type') == 'page' and target.get('webSocketDebuggerUrl')
    ]
    requested_key = _page_key(requested_url)
    for target in pages:
        if _page_key(str(target.get('url') or '')) == requested_key:
            return target
    for target in pages:
        hostname = (urlsplit(str(target.get('url') or '')).hostname or '').lower()
        if hostname == 'xinpianchang.com' or hostname.endswith('.xinpianchang.com'):
            return target
    raise ChromeCdpError('未找到新片场作品页面，请确认专用 Chrome 窗口仍处于打开状态')


def _read_json(url: str, *, method: str = 'GET', timeout: float = 2.0) -> Any:
    request = Request(url, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


class _CdpConnection:
    def __init__(self, websocket_url: str, timeout: float = 5.0):
        self._socket = websocket.create_connection(
            websocket_url,
            timeout=timeout,
            suppress_origin=True,
            http_proxy_host=None,
        )
        self._next_id = 0

    def close(self) -> None:
        self._socket.close()

    def command(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        command_id = self._next_id
        self._socket.send(json.dumps({
            'id': command_id,
            'method': method,
            'params': params or {},
        }))
        while True:
            message = json.loads(self._socket.recv())
            if message.get('id') != command_id:
                continue
            if message.get('error'):
                raise ChromeCdpError(str(message['error'].get('message') or message['error']))
            return message.get('result') or {}


class ChromeCdpSession:
    def __init__(
        self,
        chrome_path: Path,
        profile_dir: Path,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        self.chrome_path = Path(chrome_path)
        self.profile_dir = Path(profile_dir)
        self.process_factory = process_factory
        self.process: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.browser_websocket_url: Optional[str] = None
        self.target_url = ''

    @property
    def _port_marker_file(self) -> Path:
        return self.profile_dir / 'VidFlowCdpPort'

    def _load_active_endpoint(self) -> bool:
        try:
            port = int(self._port_marker_file.read_text(encoding='utf-8').strip())
            version = _read_json(f'http://127.0.0.1:{port}/json/version')
            self.port = port
            self.browser_websocket_url = str(version['webSocketDebuggerUrl'])
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _load_version_endpoint(self) -> bool:
        if self.port is None:
            return False
        try:
            version = _read_json(f'http://127.0.0.1:{self.port}/json/version')
            self.browser_websocket_url = str(version['webSocketDebuggerUrl'])
            return True
        except (OSError, ValueError, KeyError):
            return False

    def _open_target(self, target_url: str) -> None:
        if self.port is None:
            raise ChromeCdpError('Chrome CDP 端口尚未就绪')
        _read_json(
            f'http://127.0.0.1:{self.port}/json/new?{quote(target_url, safe="")}',
            method='PUT',
        )

    def start(self, target_url: str, timeout: float = 20.0) -> Dict[str, Any]:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.target_url = target_url

        if self._load_active_endpoint():
            self._open_target(target_url)
        else:
            self._port_marker_file.unlink(missing_ok=True)
            self.port = find_available_local_port()
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            self.process = self.process_factory(
                build_chrome_command(self.chrome_path, self.profile_dir, target_url, self.port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise ChromeCdpError(f'Chrome 提前退出，退出码 {self.process.returncode}')
                if self._load_version_endpoint():
                    self._port_marker_file.write_text(str(self.port), encoding='utf-8')
                    break
                time.sleep(0.2)
            else:
                raise ChromeCdpError('等待 Chrome 调试端口超时')

        target = self._wait_for_target(timeout)
        return {
            'current_url': str(target.get('url') or target_url),
            'port': self.port,
            'profile_dir': str(self.profile_dir),
        }

    def _wait_for_target(self, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                return self.current_target()
            except (ChromeCdpError, OSError, ValueError) as error:
                last_error = error
                time.sleep(0.2)
        raise ChromeCdpError(str(last_error or '等待新片场页面超时'))

    def current_target(self) -> Dict[str, Any]:
        if self.port is None:
            raise ChromeCdpError('Chrome CDP 端口尚未就绪')
        targets = _read_json(f'http://127.0.0.1:{self.port}/json/list')
        return select_page_target(targets, self.target_url)

    def snapshot(self) -> Dict[str, Any]:
        target = self.current_target()
        connection = _CdpConnection(str(target['webSocketDebuggerUrl']))
        try:
            evaluation = connection.command('Runtime.evaluate', {
                'expression': """(() => {
                    const getMeta = (property) =>
                        document.querySelector(`meta[property="${property}"]`)?.content || '';
                    const video = document.querySelector('video');
                    const resourceUrl = performance.getEntriesByType('resource')
                        .map((entry) => entry.name)
                        .find((url) => {
                            const hostname = new URL(url).hostname.toLowerCase();
                            return (hostname === 'xpccdn.com' || hostname.endsWith('.xpccdn.com'))
                                && /\\.(?:mp4|m3u8|mpd)(?:$|[?#])/i.test(url);
                        }) || '';
                    return {
                        page_url: window.location.href,
                        title: getMeta('og:title') || document.title || '',
                        description: getMeta('og:description'),
                        thumbnail: getMeta('og:image') || video?.poster || '',
                        media_url: video?.currentSrc || video?.src || resourceUrl,
                        duration: Number.isFinite(video?.duration) ? video.duration : 0,
                        webdriver: navigator.webdriver === true,
                    };
                })()""",
                'returnByValue': True,
            })
            capture = evaluation.get('result', {}).get('value') or {}
            cookie_result = connection.command('Network.getAllCookies')
        finally:
            connection.close()

        cookies = []
        for cookie in cookie_result.get('cookies') or []:
            normalized = dict(cookie)
            normalized['expiry'] = int(float(cookie.get('expires') or 0))
            cookies.append(normalized)

        return {
            'current_url': str(capture.get('page_url') or target.get('url') or ''),
            'cookies': cookies,
            'capture': capture,
        }

    def close(self) -> None:
        if self.browser_websocket_url:
            connection = None
            try:
                connection = _CdpConnection(self.browser_websocket_url, timeout=2.0)
                connection.command('Browser.close')
            except Exception:
                pass
            finally:
                if connection:
                    connection.close()

        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
        self.process = None
        self.port = None
        self.browser_websocket_url = None
