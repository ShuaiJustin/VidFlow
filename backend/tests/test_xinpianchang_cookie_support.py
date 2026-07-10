import asyncio
from pathlib import Path

from src.api.system import SUPPORTED_PLATFORMS
from src.core.cookie_helper import PLATFORM_DOMAINS, PLATFORM_URLS
from src.core.downloaders.cookie_manager import (
    PLATFORM_COOKIE_MAP,
    get_cookie_filename_for_platform,
    get_platform_display_name,
)
from src.core.downloaders.downloader_factory import DownloaderFactory
from src.core.downloaders.generic_downloader import GenericDownloader
from src.core.downloaders import generic_downloader


XINPIANCHANG_URL = "https://www.xinpianchang.com/a13685467"


def test_xinpianchang_is_registered_for_cookie_downloads():
    """新片场 URL、Cookie 文件与浏览器域名使用同一个平台标识。"""
    platform = "xinpianchang"

    assert DownloaderFactory.detect_platform(XINPIANCHANG_URL) == platform
    assert GenericDownloader()._detect_platform(XINPIANCHANG_URL) == platform
    assert PLATFORM_COOKIE_MAP[platform] == "xinpianchang_cookies.txt"
    assert get_cookie_filename_for_platform(platform) == "xinpianchang_cookies.txt"
    assert get_platform_display_name(platform) == "新片场"
    assert PLATFORM_URLS[platform] == "https://www.xinpianchang.com/"
    assert PLATFORM_DOMAINS[platform] == "xinpianchang.com"


def test_cookie_manager_exposes_xinpianchang():
    assert SUPPORTED_PLATFORMS["xinpianchang"] == {
        "name": "新片场",
        "filename": "xinpianchang_cookies.txt",
        "description": "用于下载需要登录的新片场视频",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform",
    }


def test_xinpianchang_info_passes_cookie_and_referer_to_ytdlp(tmp_path, monkeypatch):
    cookie_file = tmp_path / "xinpianchang_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".xinpianchang.com\tTRUE\t/\tTRUE\t0\tsession\tlogged-in\n",
        encoding="utf-8",
    )
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def extract_info(self, url, download):
            captured["url"] = url
            captured["referer"] = self.options["http_headers"].get("Referer")
            cookie_path = Path(self.options["cookiefile"])
            captured["cookie_content"] = cookie_path.read_text(encoding="utf-8")
            return {"title": "fixture", "formats": []}

    monkeypatch.setattr(
        "src.core.downloaders.cookie_manager.get_cookie_base_dir",
        lambda: tmp_path,
    )
    monkeypatch.setattr(generic_downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    url = f"{XINPIANCHANG_URL}?from=search_post"
    downloader = GenericDownloader()
    monkeypatch.setattr(downloader, "_get_cached_info", lambda _: None)
    asyncio.run(downloader.get_video_info(url))

    assert captured["url"] == url
    assert captured["referer"] == XINPIANCHANG_URL
    assert "session\tlogged-in" in captured["cookie_content"]
