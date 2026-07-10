"""
Cookie 管理模块
用于管理各平台的 Cookie 文件路径和友好错误提示
"""
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# 平台与 Cookie 文件的映射
PLATFORM_COOKIE_MAP: Dict[str, str] = {
    'youtube': 'youtube_cookies.txt',
    'bilibili': 'bilibili_cookies.txt',
    'douyin': 'douyin_cookies.txt',
    'tiktok': 'tiktok_cookies.txt',
    'xiaohongshu': 'xiaohongshu_cookies.txt',
    'xinpianchang': 'xinpianchang_cookies.txt',
    'twitter': 'twitter_cookies.txt',
    'instagram': 'instagram_cookies.txt',
    'weixin': 'weixin_cookies.txt',
    'tencent': 'tencent_cookies.txt',
    'youku': 'youku_cookies.txt',
    'iqiyi': 'iqiyi_cookies.txt',
    'facebook': 'facebook_cookies.txt',
    'vimeo': 'vimeo_cookies.txt',
}

# 平台中文名称映射
PLATFORM_DISPLAY_NAMES: Dict[str, str] = {
    'youtube': 'YouTube',
    'bilibili': '哔哩哔哩',
    'douyin': '抖音',
    'tiktok': 'TikTok',
    'xiaohongshu': '小红书',
    'xinpianchang': '新片场',
    'twitter': 'Twitter/X',
    'instagram': 'Instagram',
    'weixin': '微信视频号',
    'tencent': '腾讯视频',
    'youku': '优酷',
    'iqiyi': '爱奇艺',
    'facebook': 'Facebook',
    'vimeo': 'Vimeo',
    'generic': '该平台',
}


def get_cookie_base_dir() -> Path:
    """获取 Cookie 文件的基础目录（支持打包环境）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的环境，使用用户数据目录
        if sys.platform == 'win32':
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            base_dir = Path(appdata) / 'VidFlow'
        elif sys.platform == 'darwin':
            base_dir = Path.home() / 'Library' / 'Application Support' / 'VidFlow'
        else:
            base_dir = Path.home() / '.vidflow'
    else:
        # 开发环境，使用源码目录
        base_dir = Path(__file__).parent.parent.parent.parent

    cookie_dir = base_dir / "data" / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    return cookie_dir


def get_cookie_path_for_platform(platform: str) -> Optional[Path]:
    """
    获取平台对应的 Cookie 文件路径

    Args:
        platform: 平台名称（youtube, bilibili, douyin 等）

    Returns:
        Cookie 文件路径，如果文件不存在则返回 None
    """
    platform_lower = platform.lower()
    cookie_filename = PLATFORM_COOKIE_MAP.get(platform_lower)

    if not cookie_filename:
        logger.debug(f"No cookie mapping found for platform: {platform}")
        return None

    cookie_dir = get_cookie_base_dir()
    cookie_path = cookie_dir / cookie_filename

    if cookie_path.exists():
        logger.debug(f"Found cookie file for {platform}: {cookie_path}")
        return cookie_path

    logger.debug(f"Cookie file not found for {platform}: {cookie_path}")
    return None


def has_cookie_for_platform(platform: str) -> bool:
    """
    检查平台是否已配置 Cookie

    Args:
        platform: 平台名称

    Returns:
        是否已配置 Cookie
    """
    return get_cookie_path_for_platform(platform) is not None


def get_cookie_filename_for_platform(platform: str) -> Optional[str]:
    """
    获取平台对应的 Cookie 文件名（不检查是否存在）

    Args:
        platform: 平台名称

    Returns:
        Cookie 文件名
    """
    return PLATFORM_COOKIE_MAP.get(platform.lower())


def get_platform_display_name(platform: str) -> str:
    """
    获取平台的显示名称（中文）

    Args:
        platform: 平台名称

    Returns:
        平台显示名称
    """
    return PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)


def create_friendly_cookie_error(platform: str, original_error: str = "") -> str:
    """
    创建友好的 Cookie 配置错误提示（中文）

    Args:
        platform: 平台名称
        original_error: 原始错误信息（可选，用于调试）

    Returns:
        用户友好的错误提示
    """
    platform_name = get_platform_display_name(platform)
    cookie_filename = get_cookie_filename_for_platform(platform) or f"{platform}_cookies.txt"
    cookie_dir = get_cookie_base_dir()

    error_message = f"""该视频需要登录才能访问。

💡 解决方法：
1. 在「系统设置 → Cookie 管理」中配置 {platform_name} Cookie
2. 使用浏览器扩展（如 Cookie Editor）导出 Cookie
3. 将 Cookie 文件保存为: {cookie_filename}

📁 Cookie 文件位置: {cookie_dir / cookie_filename}

🔗 获取 Cookie 的步骤：
1. 在浏览器中登录 {platform_name}
2. 安装 Cookie Editor 扩展
3. 导出 Cookie 为 Netscape 格式
4. 保存到上述位置"""

    return error_message


def create_fallback_success_message(platform: str, fallback_reason: str) -> str:
    """
    创建回退成功的提示信息

    Args:
        platform: 平台名称
        fallback_reason: 回退原因

    Returns:
        回退成功提示
    """
    platform_name = get_platform_display_name(platform)
    return f"已使用 {platform_name} 专用下载器（带 Cookie）完成下载"
