from pathlib import Path

import pytest

from src.core.downloaders import generic_downloader as generic_module
from src.core.downloaders.generic_downloader import GenericDownloader


PAGE_URL = 'https://www.xinpianchang.com/a13727498?from=ArticleList'
MEDIA_URL = 'https://us-xpc5-l2.xpccdn.com/video.mp4?signature=valid'
CAPTURE = {
    'page_url': PAGE_URL,
    'title': 'Browser verified video',
    'description': 'Captured in the verified browser session',
    'thumbnail': 'https://cdn.example/poster.jpg',
    'media_url': MEDIA_URL,
    'duration': 42.5,
}


PUBLIC_INFO = {
    'id': 'a13685467',
    'title': 'OPPO - FIND X',
    'description': 'Public API video',
    'duration': 94,
    'thumbnail': 'https://cdn.example/cover.jpg',
    'uploader': 'creator',
    'formats': [{
        'format_id': '720p',
        'url': 'https://us-xpc5-l2.xpccdn.com/public.mp4?signature=valid',
        'ext': 'mp4',
        'width': 1280,
        'height': 720,
        'filesize': 6375673,
        'format_note': '720p',
        'http_headers': {
            'Range': 'bytes=0-',
            'Referer': 'https://www.xinpianchang.com/a13685467',
        },
    }],
}


@pytest.mark.asyncio
async def test_public_info_uses_next_data_and_media_api(monkeypatch, tmp_path):
    requested = []

    class FakeResponse:
        def __init__(self, *, text='', payload=None, status_code=200):
            self.text = text
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f'HTTP {self.status_code}')

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            requested.append((url, kwargs.get('params')))
            if url.endswith('/_next/static/BUILD_ID'):
                return FakeResponse(
                    status_code=404,
                    text='<script src="/_next/static/build-123/_buildManifest.js"></script>',
                )
            if '/_next/data/build-123/' in url:
                return FakeResponse(payload={
                    'pageProps': {'detail': {'video': {'vid': 'video-123', 'appKey': 'app-123'}}},
                })
            if url.startswith('https://mod-api.xinpianchang.com/'):
                return FakeResponse(payload={
                    'status': 0,
                    'data': {
                        'title': 'OPPO - FIND X',
                        'duration': 94,
                        'cover': 'https://cdn.example/cover.jpg',
                        'owner': {'username': 'creator', 'id': 42},
                        'resource': {'progressive': [
                            {'quality': '720p', 'width': 1280, 'height': 720,
                             'filesize': 6375673, 'codecName': 'h264 / aac',
                             'url': PUBLIC_INFO['formats'][0]['url']},
                        ]},
                    },
                })
            raise AssertionError(f'Unexpected URL: {url}')

    monkeypatch.setattr(
        generic_module,
        'httpx',
        type('FakeHttpx', (), {
            'AsyncClient': FakeAsyncClient,
            'Timeout': staticmethod(lambda *_args, **_kwargs: None),
        }),
        raising=False,
    )
    downloader = GenericDownloader(output_dir=str(tmp_path))

    info = await downloader._extract_xinpianchang_public_info(
        'https://www.xinpianchang.com/a13685467?from=search_post'
    )

    assert info['title'] == 'OPPO - FIND X'
    assert info['extractor'] == 'XinpianchangPublicData'
    assert info['formats'][0]['url'] == PUBLIC_INFO['formats'][0]['url']
    assert requested == [
        ('https://www.xinpianchang.com/_next/static/BUILD_ID', None),
        ('https://www.xinpianchang.com/_next/data/build-123/a13685467.json', None),
        ('https://mod-api.xinpianchang.com/mod/api/v2/media/video-123', {'appKey': 'app-123'}),
    ]


@pytest.mark.asyncio
async def test_public_info_uses_saved_cookie_for_1080p_url(monkeypatch, tmp_path):
    cookie_file = tmp_path / 'xinpianchang_cookies.txt'
    cookie_file.write_text(
        '# Netscape HTTP Cookie File\n'
        '.xinpianchang.com\tTRUE\t/\tTRUE\t1893456000\tAuthorization\tlogin-token\n',
        encoding='utf-8',
    )
    captured_cookies = []

    class FakeResponse:
        def __init__(self, *, text='', payload=None):
            self.text = text
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured_cookies.append(kwargs.get('cookies'))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **kwargs):
            if url.endswith('/_next/static/BUILD_ID'):
                return FakeResponse(
                    text='<script src="/_next/static/build-123/_buildManifest.js"></script>',
                )
            if '/_next/data/build-123/' in url:
                return FakeResponse(payload={
                    'pageProps': {'detail': {'video': {'vid': 'video-123', 'appKey': 'app-123'}}},
                })
            cookie_names = dict(captured_cookies[0].items()) if captured_cookies[0] else {}
            media_url = (
                'https://us-xpc5-l2.xpccdn.com/video-1080.mp4?signature=valid'
                if cookie_names.get('Authorization') == 'login-token'
                else ''
            )
            return FakeResponse(payload={
                'status': 0,
                'data': {
                    'title': 'OPPO - FIND X',
                    'resource': {'progressive': [{
                        'quality': '1080p',
                        'width': 1920,
                        'height': 1080,
                        'url': media_url,
                    }]},
                },
            })

    monkeypatch.setattr(
        generic_module,
        'httpx',
        type('FakeHttpx', (), {
            'AsyncClient': FakeAsyncClient,
            'Cookies': generic_module.httpx.Cookies,
            'Timeout': staticmethod(lambda *_args, **_kwargs: None),
        }),
    )
    downloader = GenericDownloader(output_dir=str(tmp_path))
    monkeypatch.setattr(downloader, '_get_platform_cookie_path', lambda _url: cookie_file)

    info = await downloader._extract_xinpianchang_public_info(
        'https://www.xinpianchang.com/a13685467'
    )

    assert dict(captured_cookies[0].items())['Authorization'] == 'login-token'
    assert info['formats'][0]['format_id'] == '1080p'


@pytest.mark.asyncio
async def test_video_info_prefers_public_data_without_browser(monkeypatch, tmp_path):
    async def public_info(_self, _url):
        return PUBLIC_INFO

    monkeypatch.setattr(
        GenericDownloader,
        '_extract_xinpianchang_public_info',
        public_info,
        raising=False,
    )
    monkeypatch.setattr(
        generic_module,
        'get_browser_page_capture',
        lambda _url: pytest.fail('Public data must be tried before browser capture'),
    )
    downloader = GenericDownloader(output_dir=str(tmp_path))

    info = await downloader.get_video_info('https://www.xinpianchang.com/a13685467')

    assert info['title'] == PUBLIC_INFO['title']
    assert info['formats'][0]['url'] == PUBLIC_INFO['formats'][0]['url']
    assert info['browser_capture'] is False


@pytest.mark.asyncio
async def test_video_info_uses_verified_browser_capture(monkeypatch, tmp_path):
    async def public_info_unavailable(_self, _url):
        raise RuntimeError('public endpoint blocked')

    monkeypatch.setattr(
        GenericDownloader,
        '_extract_xinpianchang_public_info',
        public_info_unavailable,
        raising=False,
    )
    monkeypatch.setattr(generic_module, 'get_browser_page_capture', lambda _url: CAPTURE)

    class UnexpectedYoutubeDL:
        def __init__(self, _opts):
            raise AssertionError('The protected page must not be requested again')

    monkeypatch.setattr(generic_module.yt_dlp, 'YoutubeDL', UnexpectedYoutubeDL)
    downloader = GenericDownloader(output_dir=str(tmp_path))

    info = await downloader.get_video_info(PAGE_URL)

    assert info['title'] == CAPTURE['title']
    assert info['duration'] == CAPTURE['duration']
    assert info['formats'][0]['url'] == MEDIA_URL
    assert info['browser_capture'] is True


@pytest.mark.asyncio
async def test_download_uses_captured_media_url(monkeypatch, tmp_path):
    extracted_urls = []
    output_file = tmp_path / 'captured.mp4'

    monkeypatch.setattr(generic_module, 'get_browser_page_capture', lambda _url: CAPTURE)
    async def public_info_unavailable(_self, _url):
        raise RuntimeError('public endpoint blocked')

    monkeypatch.setattr(
        GenericDownloader,
        '_extract_xinpianchang_public_info',
        public_info_unavailable,
        raising=False,
    )
    monkeypatch.setattr(
        generic_module,
        'get_tool_manager',
        lambda: type('Tools', (), {'get_ffmpeg_path': lambda self: None})(),
    )

    class FakeYoutubeDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download):
            extracted_urls.append(url)
            assert download is True
            return {
                'title': 'Direct CDN filename',
                'duration': CAPTURE['duration'],
                'filesize': 1024,
            }

        def prepare_filename(self, _info):
            return str(output_file)

    monkeypatch.setattr(generic_module.yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    downloader = GenericDownloader(output_dir=str(tmp_path))
    downloader._use_cookie_in_smart_mode = False

    result = await downloader.download_video(PAGE_URL)

    assert extracted_urls == [MEDIA_URL]
    assert result['status'] == 'success'
    assert result['title'] == CAPTURE['title']
    assert Path(result['filename']) == output_file


@pytest.mark.asyncio
async def test_download_processes_public_media_without_webpage(monkeypatch, tmp_path):
    processed = []
    output_file = tmp_path / 'public.mp4'

    async def public_info(_self, _url):
        return PUBLIC_INFO

    monkeypatch.setattr(
        GenericDownloader,
        '_extract_xinpianchang_public_info',
        public_info,
        raising=False,
    )
    monkeypatch.setattr(
        generic_module,
        'get_browser_page_capture',
        lambda _url: pytest.fail('Browser capture is only a fallback'),
    )
    monkeypatch.setattr(
        generic_module,
        'get_tool_manager',
        lambda: type('Tools', (), {'get_ffmpeg_path': lambda self: None})(),
    )

    class FakeYoutubeDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def process_ie_result(self, info, download):
            processed.append(info)
            assert download is True
            return {**info, 'ext': 'mp4', 'format_id': '720p', 'filesize': 6375673}

        def extract_info(self, *_args, **_kwargs):
            raise AssertionError('Protected Xinpianchang webpage must not be requested')

        def prepare_filename(self, _info):
            return str(output_file)

    monkeypatch.setattr(generic_module.yt_dlp, 'YoutubeDL', FakeYoutubeDL)
    downloader = GenericDownloader(output_dir=str(tmp_path))

    result = await downloader.download_video('https://www.xinpianchang.com/a13685467')

    assert processed == [PUBLIC_INFO]
    assert result['status'] == 'success'
    assert result['title'] == PUBLIC_INFO['title']
    assert result['actual_quality'] == '720p'
    assert Path(result['filename']) == output_file
