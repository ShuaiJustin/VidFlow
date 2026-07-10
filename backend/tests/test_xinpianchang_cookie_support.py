from src.api.system import SUPPORTED_PLATFORMS
from src.core.cookie_helper import PLATFORM_DOMAINS, PLATFORM_URLS
from src.core.downloaders.cookie_manager import (
    PLATFORM_COOKIE_MAP,
    get_cookie_filename_for_platform,
    get_platform_display_name,
)
from src.core.downloaders.downloader_factory import DownloaderFactory
from src.core.downloaders.generic_downloader import GenericDownloader


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
