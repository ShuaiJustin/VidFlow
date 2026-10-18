"""
Cookie 自动获取辅助模块
使用 Selenium 打开受控浏览器，让用户手动登录后自动提取 Cookie
"""
import asyncio
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
import time
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from src.core.chrome_cdp import ChromeCdpError, ChromeCdpSession, find_chrome_executable
from src.core.config_manager import DATA_DIR

logger = logging.getLogger(__name__)

# Cookie错误代码常量
class CookieErrorCode:
    """Cookie操作错误代码"""
    BROWSER_CLOSED = "BROWSER_CLOSED"  # 浏览器已关闭
    SELENIUM_NOT_INSTALLED = "SELENIUM_NOT_INSTALLED"  # Selenium未安装
    BROWSER_NOT_RUNNING = "BROWSER_NOT_RUNNING"  # 浏览器未运行
    NO_COOKIES_FOUND = "NO_COOKIES_FOUND"  # 未找到Cookie
    BROWSER_ALREADY_RUNNING = "BROWSER_ALREADY_RUNNING"  # 浏览器已在运行
    PLATFORM_NOT_SUPPORTED = "PLATFORM_NOT_SUPPORTED"  # 平台不支持
    MISSING_PARAMETER = "MISSING_PARAMETER"  # 缺少参数
    CHROME_NOT_FOUND = "CHROME_NOT_FOUND"  # Chrome浏览器未找到
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 内部错误

# 平台登录页面映射
PLATFORM_URLS = {
    "douyin": "https://www.douyin.com/",
    "tiktok": "https://www.tiktok.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "xinpianchang": "https://www.xinpianchang.com/",
    "bilibili": "https://www.bilibili.com/",
    "youtube": "https://www.youtube.com/",
    "twitter": "https://twitter.com/login",
    "instagram": "https://www.instagram.com/"
}

# 平台域名映射（使用基础域名，兼容根域和子域）
PLATFORM_DOMAINS = {
    "douyin": "douyin.com",
    "tiktok": "tiktok.com",
    "xiaohongshu": "xiaohongshu.com",
    "xinpianchang": "xinpianchang.com",
    "bilibili": "bilibili.com",
    "youtube": "youtube.com",
    "twitter": "twitter.com",
    "instagram": "instagram.com"
}

# Cookie 管理器单例
_cookie_browser_manager = None


class CookieBrowserManager:
    """Cookie浏览器管理器"""

    def __init__(self):
        self.driver = None
        self.cdp_session = None
        self.current_platform = None
        self.current_browser = None
        self.page_captures: Dict[str, Dict] = {}
        self.is_running = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._selenium_import_error = None

    def cleanup(self):
        """清理资源"""
        if self.cdp_session:
            try:
                self.cdp_session.close()
            except Exception as e:
                logger.error(f"关闭新片场专用 Chrome 失败: {e}")
            finally:
                self.cdp_session = None

        if self.driver:
            driver_pid = None
            try:
                service = getattr(self.driver, "service", None)
                process = getattr(service, "process", None) if service else None
                driver_pid = getattr(process, "pid", None) if process else None
            except Exception:
                driver_pid = None

            try:
                self.driver.quit()
            except Exception as e:
                logger.error(f"关闭浏览器失败: {e}")
            finally:
                if driver_pid:
                    try:
                        import sys
                        if sys.platform == 'win32':
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(driver_pid)],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                check=False
                            )
                        else:
                            os.kill(int(driver_pid), 9)
                    except Exception as kill_error:
                        logger.warning(f"强制结束浏览器进程失败: {kill_error}")
                self.driver = None

        self.current_platform = None
        self.current_browser = None
        self.is_running = False

    def is_selenium_available(self) -> bool:
        """检查Selenium是否可用"""
        try:
            import selenium
            from selenium import webdriver
            self._selenium_import_error = None
            return True
        except Exception as e:
            self._selenium_import_error = str(e)
            return False

    def _resolve_target_url(self, platform: str, target_url: Optional[str] = None) -> str:
        fallback_url = PLATFORM_URLS[platform]
        if not target_url or not isinstance(target_url, str):
            return fallback_url

        try:
            parsed = urlsplit(target_url)
            hostname = (parsed.hostname or "").lower()
            platform_domain = PLATFORM_DOMAINS[platform].lower()
            if (
                parsed.scheme.lower() in ("http", "https")
                and (hostname == platform_domain or hostname.endswith(f".{platform_domain}"))
            ):
                return target_url
        except (TypeError, ValueError):
            pass

        return fallback_url

    @staticmethod
    def _page_capture_key(url: str) -> str:
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or '').lower()
            path = parsed.path.rstrip('/') or '/'
            return f'{hostname}{path}'
        except (TypeError, ValueError):
            return ''

    def get_page_capture(self, url: str) -> Optional[Dict]:
        capture = self.page_captures.get(self._page_capture_key(url))
        return dict(capture) if capture else None

    def _capture_xinpianchang_page(self, current_url: str) -> Optional[Dict]:
        if self.current_platform != 'xinpianchang' or not self.driver:
            return None

        capture = self.driver.execute_script("""
            const getMeta = (property) =>
                document.querySelector(`meta[property="${property}"]`)?.content || '';
            const video = document.querySelector('video');
            return {
                page_url: window.location.href,
                title: getMeta('og:title') || document.title || '',
                description: getMeta('og:description'),
                thumbnail: getMeta('og:image') || video?.poster || '',
                media_url: video?.currentSrc || video?.src || '',
                duration: Number.isFinite(video?.duration) ? video.duration : 0,
            };
        """) or {}

        return self._store_xinpianchang_capture(capture, current_url)

    def _store_xinpianchang_capture(
        self,
        capture: Optional[Dict],
        current_url: str,
    ) -> Optional[Dict]:
        capture = capture or {}
        page_url = str(capture.get('page_url') or current_url)
        media_url = str(capture.get('media_url') or '')
        try:
            media_host = (urlsplit(media_url).hostname or '').lower()
            media_scheme = urlsplit(media_url).scheme.lower()
        except (TypeError, ValueError):
            return None

        if media_scheme not in ('http', 'https') or not (
            media_host == 'xpccdn.com' or media_host.endswith('.xpccdn.com')
        ):
            return None

        normalized_capture = {
            'page_url': page_url,
            'title': str(capture.get('title') or '新片场视频'),
            'description': str(capture.get('description') or ''),
            'thumbnail': str(capture.get('thumbnail') or ''),
            'media_url': media_url,
            'duration': float(capture.get('duration') or 0),
            'captured_at': time.time(),
        }
        self.page_captures[self._page_capture_key(page_url)] = normalized_capture
        logger.info('已从受控浏览器捕获新片场播放器地址')
        return normalized_capture

    async def start_browser(
        self,
        platform: str,
        browser: str = "chrome",
        target_url: Optional[str] = None,
    ) -> Dict:
        """
        启动受控浏览器窗口

        Args:
            platform: 平台名称 (douyin, xiaohongshu, etc.)
            browser: 浏览器类型 (chrome, edge, firefox)

        Returns:
            {"status": "success", "message": "浏览器已启动"} 或
            {"status": "error", "error": "错误信息", "error_code": "错误代码", "should_reset": bool}
        """
        use_xinpianchang_cdp = platform == 'xinpianchang'
        if use_xinpianchang_cdp and browser.lower() != 'chrome':
            return {
                "status": "error",
                "error": "新片场安全验证目前仅支持 Chrome。请选择 Chrome 后重试。",
                "error_code": "XINPIANCHANG_CHROME_REQUIRED",
                "should_reset": False,
            }

        if not use_xinpianchang_cdp and not self.is_selenium_available():
            import sys

            detail = self._selenium_import_error
            if getattr(sys, 'frozen', False):
                msg = "受控浏览器功能不可用（内置 Selenium 加载失败）。\n\n解决方案：\n1. 更新/重新安装 VidFlow\n2. 或使用「从浏览器读取 Cookie（推荐）」"
                if detail:
                    msg = f"{msg}\n\n错误详情：{detail}"
            else:
                if detail:
                    msg = f"Selenium 导入失败: {detail}\n\n请运行: pip install selenium webdriver-manager\n\n然后重启应用。"
                else:
                    msg = "Selenium 未安装。请运行: pip install selenium webdriver-manager\n\n然后重启应用。"

            return {
                "status": "error",
                "error": msg,
                "error_code": CookieErrorCode.SELENIUM_NOT_INSTALLED,
                "should_reset": True
            }

        if self.is_running:
            return {
                "status": "error",
                "error": "浏览器已在运行中，请先完成当前操作",
                "error_code": CookieErrorCode.BROWSER_ALREADY_RUNNING,
                "should_reset": False
            }

        if platform not in PLATFORM_URLS:
            return {
                "status": "error",
                "error": f"不支持的平台: {platform}",
                "error_code": CookieErrorCode.PLATFORM_NOT_SUPPORTED,
                "should_reset": True
            }

        try:
            # 在线程池中执行 Selenium 操作，避免阻塞异步事件循环
            logger.info(f"[DEBUG] start_browser called with platform={platform}, browser={browser}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._start_browser_sync,
                platform,
                browser.lower(),
                target_url,
            )

            if result.get("status") == "success":
                self.current_platform = platform
                self.current_browser = browser.lower()
                self.is_running = True

            return result

        except Exception as e:
            logger.error(f"启动浏览器失败: {e}")
            self.cleanup()
            return {
                "status": "error",
                "error": f"启动浏览器失败: {str(e)}",
                "error_code": CookieErrorCode.INTERNAL_ERROR,
                "should_reset": True
            }

    def _start_browser_sync(
        self,
        platform: str,
        browser: str = "chrome",
        target_url: Optional[str] = None,
    ) -> Dict:
        """
        同步方法：启动浏览器（在线程池中执行）
        支持 Chrome, Edge, Firefox
        """
        if platform == 'xinpianchang' and browser == 'chrome':
            return self._start_xinpianchang_cdp_sync(target_url)

        try:
            from selenium import webdriver
            import threading
            import os

            # 配置环境变量以优化 WebDriver 下载（尝试使用国内源或忽略 SSL 错误）
            # 注意：最新版 webdriver_manager 通常能自动处理，但设置这些作为备用
            os.environ["WDM_SSL_VERIFY"] = "0"

            logger.info(f"[DEBUG] _start_browser_sync called with platform={platform}, browser={browser}")
            browser_name = browser.capitalize()
            logger.info(f"[1/3] 正在配置 {browser_name} 浏览器选项...")

            # 根据浏览器类型选择对应的配置
            if browser == "chrome":
                logger.info("[DEBUG] Importing Chrome modules...")
                from selenium.webdriver.chrome.options import Options
                from selenium.webdriver.chrome.service import Service
                logger.info("[DEBUG] Importing ChromeDriverManager...")
                from webdriver_manager.chrome import ChromeDriverManager
                logger.info("[DEBUG] Chrome modules imported successfully")

                options = Options()
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)

            elif browser == "edge":
                logger.info("[DEBUG] Importing Edge modules...")
                from selenium.webdriver.edge.options import Options
                from selenium.webdriver.edge.service import Service
                from webdriver_manager.microsoft import EdgeChromiumDriverManager
                logger.info("[DEBUG] Edge modules imported successfully")

                options = Options()
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)

            elif browser == "firefox":
                logger.info("[DEBUG] Importing Firefox modules...")
                from selenium.webdriver.firefox.options import Options
                from selenium.webdriver.firefox.service import Service
                from webdriver_manager.firefox import GeckoDriverManager
                logger.info("[DEBUG] Firefox modules imported successfully")

                options = Options()
                options.set_preference("dom.webdriver.enabled", False)
                options.set_preference('useAutomationExtension', False)
            else:
                return {
                    "status": "error",
                    "error": f"不支持的浏览器: {browser}。支持: chrome, edge, firefox",
                    "error_code": "BROWSER_NOT_SUPPORTED",
                    "should_reset": True
                }

            # 通用选项
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-notifications')
            options.add_argument('--disable-popup-blocking')
            options.add_argument('--start-maximized')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            # 移除硬编码 UA，使用浏览器默认 UA 以避免指纹不匹配
            # options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

            logger.info(f"[2/3] 正在启动 {browser_name} 浏览器...")

            driver_instance = [None]
            error_instance = [None]
            cancel_event = threading.Event()

            def start_browser_thread():
                try:
                    if browser == "chrome":
                        try:
                            logger.info("[DEBUG] Installing ChromeDriver via webdriver_manager...")
                            service = Service(ChromeDriverManager().install())
                            logger.info("[DEBUG] ChromeDriver installed successfully")
                        except Exception as wdm_error:
                            logger.warning(f"[DEBUG] webdriver_manager failed: {wdm_error}, trying default Service")
                            service = Service()
                        logger.info("[DEBUG] Creating Chrome webdriver instance...")
                        driver_instance[0] = webdriver.Chrome(service=service, options=options)
                    elif browser == "edge":
                        try:
                            logger.info("[DEBUG] Installing EdgeDriver via webdriver_manager...")
                            service = Service(EdgeChromiumDriverManager().install())
                            logger.info("[DEBUG] EdgeDriver installed successfully")
                        except Exception as wdm_error:
                            logger.warning(f"[DEBUG] webdriver_manager failed: {wdm_error}, trying default Service")
                            service = Service()
                        logger.info("[DEBUG] Creating Edge webdriver instance...")
                        driver_instance[0] = webdriver.Edge(service=service, options=options)
                    elif browser == "firefox":
                        try:
                            logger.info("[DEBUG] Installing GeckoDriver via webdriver_manager...")
                            service = Service(GeckoDriverManager().install())
                            logger.info("[DEBUG] GeckoDriver installed successfully")
                        except Exception as wdm_error:
                            logger.warning(f"[DEBUG] webdriver_manager failed: {wdm_error}, trying default Service")
                            service = Service()
                        logger.info("[DEBUG] Creating Firefox webdriver instance...")
                        driver_instance[0] = webdriver.Firefox(service=service, options=options)
                    logger.info(f"[2/3] {browser_name} 浏览器已启动成功")
                    if cancel_event.is_set() and driver_instance[0]:
                        try:
                            driver_instance[0].quit()
                        except Exception:
                            pass
                        driver_instance[0] = None
                except Exception as e:
                    error_instance[0] = e

            browser_thread = threading.Thread(target=start_browser_thread, daemon=True)
            browser_thread.start()
            # 首次下载 ChromeDriver 可能需要较长时间，设置 180 秒超时
            browser_thread.join(timeout=180)

            if browser_thread.is_alive():
                cancel_event.set()
                logger.error(f"{browser_name} 启动超时（180秒）")
                return {
                    "status": "error",
                    "error": f"{browser_name} 浏览器启动超时。\n\n可能原因：\n1. WebDriver 正在下载但网络较慢\n2. {browser_name} 浏览器响应缓慢\n\n解决方案：\n1. 检查网络连接\n2. 稍后重试",
                    "error_code": "BROWSER_TIMEOUT",
                    "should_reset": True
                }

            if error_instance[0]:
                error = error_instance[0]
                logger.error(f"{browser_name} 启动失败: {error}")
                error_str = str(error).lower()

                if 'not found' in error_str or 'cannot find' in error_str:
                    download_urls = {
                        "chrome": "https://www.google.com/chrome/",
                        "edge": "https://www.microsoft.com/edge",
                        "firefox": "https://www.mozilla.org/firefox/"
                    }
                    return {
                        "status": "error",
                        "error": f"{browser_name} 浏览器未安装。\n\n请从以下地址下载安装：\n{download_urls.get(browser, '')}\n\n错误详情：{str(error)}",
                        "error_code": "BROWSER_NOT_FOUND",
                        "should_reset": True
                    }
                elif 'network' in error_str or 'connection' in error_str or 'timeout' in error_str:
                    return {
                        "status": "error",
                        "error": f"WebDriver 下载失败（网络问题）。\n\n原因：{str(error)}\n\n解决方案：\n1. 检查网络连接\n2. 稍后重试",
                        "error_code": "WEBDRIVER_DOWNLOAD_FAILED",
                        "should_reset": True
                    }
                else:
                    return {
                        "status": "error",
                        "error": f"{browser_name} 浏览器启动失败。\n\n错误详情：{str(error)}",
                        "error_code": "BROWSER_START_FAILED",
                        "should_reset": True
                    }

            if driver_instance[0]:
                self.driver = driver_instance[0]
            else:
                return {
                    "status": "error",
                    "error": f"{browser_name} 启动失败，原因未知。请检查系统环境。",
                    "error_code": CookieErrorCode.INTERNAL_ERROR,
                    "should_reset": True
                }

            try:
                # 移除 webdriver 标志
                if browser in ("chrome", "edge"):
                    try:
                        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                            'source': '''
                                Object.defineProperty(navigator, 'webdriver', {
                                    get: () => undefined
                                })
                            '''
                        })
                    except Exception as e:
                        logger.warning(f"移除 webdriver 标志失败: {e}")

                # 打开登录页面
                url = self._resolve_target_url(platform, target_url)
                logger.info(f"[3/3] 正在打开登录页面: {url}")
                self.driver.get(url)

                current_url = ""
                navigation_ok = True
                try:
                    time.sleep(0.5)
                    current_url = self.driver.current_url or ""
                    if (not current_url) or current_url.startswith("data:") or current_url.startswith("about:"):
                        navigation_ok = False
                except Exception as url_error:
                    navigation_ok = False
                    logger.warning(f"读取当前页面地址失败: {url_error}")

                if not navigation_ok:
                    try:
                        logger.warning(f"页面未自动跳转，正在重试打开: {url} (当前: {current_url})")
                        self.driver.execute_script("window.location.href = arguments[0];", url)
                        time.sleep(0.5)
                        current_url = self.driver.current_url or current_url
                        navigation_ok = not ((not current_url) or current_url.startswith("data:") or current_url.startswith("about:"))
                    except Exception as retry_error:
                        logger.warning(f"重试打开登录页面失败: {retry_error}")

                logger.info(f"[3/3] 登录页面已加载，等待用户登录... 当前URL: {current_url}")

                return {
                    "status": "success",
                    "message": f"浏览器已启动，请在浏览器窗口中登录 {PLATFORM_DOMAINS[platform]}",
                    "platform": platform,
                    "url": url,
                    "current_url": current_url,
                    "navigation_ok": navigation_ok
                }
            except Exception as init_error:
                logger.error(f"启动浏览器后初始化失败: {init_error}")
                self.cleanup()
                return {
                    "status": "error",
                    "error": f"启动浏览器失败: {str(init_error)}",
                    "error_code": CookieErrorCode.INTERNAL_ERROR,
                    "should_reset": True
                }

        except ImportError as e:
            logger.error(f"依赖模块导入失败: {e}")
            import sys
            err_lower = str(e).lower()

            if 'trio_websocket' in err_lower:
                missing_module = "trio-websocket"
            elif 'trio' in err_lower:
                missing_module = "trio"
            elif 'webdriver_manager' in err_lower or 'webdriver-manager' in err_lower:
                missing_module = "webdriver-manager"
            elif "selenium" in err_lower:
                missing_module = "Selenium"
            else:
                missing_module = "依赖模块"

            if getattr(sys, 'frozen', False):
                error_msg = (
                    f"{missing_module} 加载失败（打包环境）。\n\n"
                    f"解决方案：\n1. 更新/重新安装 VidFlow\n2. 或使用「从浏览器读取 Cookie（推荐）」\n\n"
                    f"错误详情：{str(e)}"
                )
            else:
                error_msg = (
                    f"{missing_module} 未安装。\n\n"
                    f"请运行以下命令安装：\n"
                    f"pip install selenium webdriver-manager\n\n"
                    f"然后重启应用。\n\n"
                    f"错误详情：{str(e)}"
                )
            return {
                "status": "error",
                "error": error_msg,
                "error_code": CookieErrorCode.SELENIUM_NOT_INSTALLED,
                "should_reset": True
            }

    def _start_xinpianchang_cdp_sync(self, target_url: Optional[str]) -> Dict:
        url = self._resolve_target_url('xinpianchang', target_url)
        chrome_path = find_chrome_executable()
        if not chrome_path:
            return {
                "status": "error",
                "error": "未找到 Google Chrome，请先安装 Chrome 后重试。",
                "error_code": CookieErrorCode.CHROME_NOT_FOUND,
                "should_reset": True,
            }

        profile_dir = DATA_DIR / 'browser_profiles' / 'xinpianchang_chrome'
        session = ChromeCdpSession(chrome_path, profile_dir)
        try:
            info = session.start(url)
            self.cdp_session = session
            logger.info(
                "已启动新片场专用 Chrome（无 WebDriver），配置目录: %s",
                profile_dir,
            )
            return {
                "status": "success",
                "message": "专用 Chrome 已启动。请手动完成安全验证并等待视频开始播放，然后点击“完成登录”。",
                "platform": "xinpianchang",
                "url": url,
                "current_url": info.get('current_url') or url,
                "navigation_ok": True,
                "browser_mode": "chrome_cdp",
            }
        except (ChromeCdpError, OSError) as error:
            session.close()
            return {
                "status": "error",
                "error": f"启动新片场专用 Chrome 失败: {error}",
                "error_code": "CHROME_CDP_START_FAILED",
                "should_reset": True,
            }
        except Exception as e:
            logger.error(f"启动浏览器失败: {e}")
            self.cleanup()
            return {
                "status": "error",
                "error": f"启动浏览器失败: {str(e)}",
                "error_code": CookieErrorCode.INTERNAL_ERROR,
                "should_reset": True
            }

    def convert_cookies_to_netscape(self, cookies: List[Dict], domain: str) -> str:
        """
        将 Selenium Cookie 转换为 Netscape 格式

        Args:
            cookies: Selenium 获取的 Cookie 列表
            domain: 域名

        Returns:
            Netscape 格式的 Cookie 字符串
        """
        lines = ["# Netscape HTTP Cookie File", "# Generated by VidFlow Desktop", ""]

        for cookie in cookies:
            # 跳过无效的 Cookie（name 为空）
            name = cookie.get('name', '')
            if not name:
                logger.warning(f"跳过无效 Cookie: name 为空, domain={cookie.get('domain', '')}, value={cookie.get('value', '')[:20]}...")
                continue

            # Netscape 格式：domain flag path secure expiration name value
            domain_value = cookie.get('domain', domain)

            # 跳过 domain 为空的 Cookie
            if not domain_value:
                logger.warning(f"跳过无效 Cookie: domain 为空, name={name}")
                continue

            # 正确设置 domain_specified 标志
            # 如果域名以 . 开头，表示匹配所有子域，flag 应该是 TRUE
            # 如果域名不以 . 开头，仅匹配该域名，flag 应该是 FALSE
            flag = 'TRUE' if domain_value.startswith('.') else 'FALSE'

            path = cookie.get('path', '/')
            if not path:
                path = '/'

            secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'

            # 过期时间（Unix时间戳）
            expiry = cookie.get('expiry', 0)
            if expiry == 0:
                # 会话 Cookie，设置为1年后过期
                expiry = int(time.time()) + 365 * 24 * 3600

            value = cookie.get('value', '')

            line = f"{domain_value}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
            lines.append(line)

        return '\n'.join(lines)

    async def extract_cookies(self) -> Dict:
        """
        提取当前浏览器的 Cookie

        Returns:
            {"status": "success", "content": "Cookie内容", "count": Cookie数量}
        """
        if not self.is_running or (not self.driver and not self.cdp_session):
            return {
                "status": "error",
                "error": "浏览器未运行，请先启动浏览器"
            }

        try:
            # 在线程池中执行 Selenium 操作，避免阻塞异步事件循环
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._extract_cookies_sync
            )
            return result

        except Exception as e:
            logger.error(f"提取 Cookie 失败: {e}")
            return {
                "status": "error",
                "error": f"提取 Cookie 失败: {str(e)}"
            }

    def _extract_cookies_sync(self) -> Dict:
        """
        同步方法：提取 Cookie
        """
        try:
            # 检查浏览器是否已关闭
            if not self.driver and not self.cdp_session:
                return {
                    "status": "error",
                    "error": "浏览器未运行",
                    "error_code": CookieErrorCode.BROWSER_NOT_RUNNING,
                    "should_reset": True
                }

            cdp_snapshot = None
            try:
                if self.cdp_session:
                    cdp_snapshot = self.cdp_session.snapshot()
                    current_url = str(cdp_snapshot.get('current_url') or '')
                else:
                    # 尝试切换到最新的窗口（防止用户在只有新标签页保留的情况下操作）
                    if self.driver.window_handles:
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                    current_url = self.driver.current_url
                logger.info(f"正在从 URL 提取 Cookie: {current_url}")
            except Exception as session_error:
                # 浏览器已关闭，清理资源
                self.cleanup()
                logger.warning(f"浏览器已关闭: {session_error}")
                return {
                    "status": "error",
                    "error": "浏览器已关闭，请重新启动浏览器",
                    "error_code": CookieErrorCode.BROWSER_CLOSED,
                    "should_reset": True
                }

            platform = self.current_platform
            domain = PLATFORM_DOMAINS.get(platform, '')

            logger.info(f"提取 {platform} Cookie (目标域名: {domain})...")

            # 获取 Cookie
            cookies = list(cdp_snapshot.get('cookies') or []) if cdp_snapshot else []
            if cdp_snapshot is None:
                max_attempts = 5
                delay = 0.5
                for attempt in range(1, max_attempts + 1):
                    cookies = self.driver.get_cookies()
                    if cookies:
                        break
                    if attempt < max_attempts:
                        logger.warning(f"未找到任何 Cookie，{delay:.1f} 秒后重试（{attempt}/{max_attempts}）...")
                        time.sleep(delay)
                        delay = min(delay * 2, 5)

            capture = None
            if platform == 'xinpianchang' and '/a' in urlsplit(current_url).path:
                if cdp_snapshot is not None:
                    capture = self._store_xinpianchang_capture(
                        cdp_snapshot.get('capture'),
                        current_url,
                    )
                else:
                    capture = self._capture_xinpianchang_page(current_url)
                if not capture:
                    return {
                        "status": "error",
                        "error": "尚未检测到视频播放器。请先完成安全验证并等待视频页面加载完成，然后再次点击“完成登录”。",
                        "error_code": "VIDEO_NOT_READY",
                        "should_reset": False,
                    }

            if not cookies and not capture:
                return {
                    "status": "error",
                    "error": "未检测到 Cookie，请确保您已登录",
                    "error_code": CookieErrorCode.NO_COOKIES_FOUND,
                    "should_reset": False  # 不重置，允许用户重试
                }

            # 过滤有效 Cookie
            valid_cookies = []
            if domain:
                 valid_cookies = [
                    c for c in cookies
                    if domain in c.get('domain', '')
                ]

            if valid_cookies:
                used_cookies = valid_cookies
            else:
                # 使用所有 Cookie
                used_cookies = cookies
                logger.warning(f"未找到 {domain} Cookie，使用 {len(cookies)} 个 Cookie")

            # 转换为 Netscape 格式
            cookie_content = self.convert_cookies_to_netscape(used_cookies, domain)

            logger.info(f"提取 {len(used_cookies)} 个 {platform} Cookie")

            return {
                "status": "success",
                "content": cookie_content,
                "count": len(used_cookies),
                "platform": platform
            }

        except Exception as e:
            logger.error(f"提取 Cookie 内部错误: {e}")
            return {
                "status": "error",
                "error": f"提取 Cookie 内部错误: {str(e)}",
                "error_code": CookieErrorCode.INTERNAL_ERROR,
                "should_reset": True
            }

    async def close_browser(self) -> Dict:
        """
        关闭浏览器

        Returns:
            {"status": "success", "message": "浏览器已关闭"}
        """
        self.cleanup()
        return {
            "status": "success",
            "message": "浏览器已关闭"
        }

    def get_status(self) -> Dict:
        """
        获取当前状态

        Returns:
            {"is_running": bool, "platform": str, "browser": str}
        """
        return {
            "is_running": self.is_running,
            "platform": self.current_platform,
            "browser": self.current_browser,
            "selenium_available": self.is_selenium_available()
        }


def get_cookie_browser_manager() -> CookieBrowserManager:
    """获取 Cookie 浏览器管理器单例"""
    global _cookie_browser_manager
    if _cookie_browser_manager is None:
        _cookie_browser_manager = CookieBrowserManager()
    return _cookie_browser_manager


def get_browser_page_capture(url: str) -> Optional[Dict]:
    return get_cookie_browser_manager().get_page_capture(url)


# 便捷函数
async def auto_get_cookie(platform: str, browser: str = "chrome") -> Dict:
    """
    自动获取 Cookie（完整流程）

    Args:
        platform: 平台名称
        browser: 浏览器类型 (chrome, edge, firefox)

    Returns:
        {"status": "success", "content": "Cookie内容"}
    """
    manager = get_cookie_browser_manager()

    # 启动浏览器
    result = await manager.start_browser(platform, browser)
    if result["status"] != "success":
        return result

    # 注意：这里需要等待用户手动登录
    # 实际应用中，用户登录完成后需要主动调用 extract_cookies

    return {
        "status": "success",
        "message": "请在浏览器中完成登录，然后点击'完成登录'按钮"
    }


async def extract_cookies_from_browser(platform: str, browser: str = "chrome") -> Dict:
    """
    从已安装的浏览器直接读取 Cookie
    使用 browser-cookie3 库（避免浏览器锁定问题）

    Args:
        platform: 平台名称 (douyin, xiaohongshu, etc.)
        browser: 浏览器名称 (chrome, edge, firefox)

    Returns:
        {"status": "success", "content": "Cookie内容", "count": Cookie数量} 或
        {"status": "error", "error": "错误信息"}
    """
    if platform not in PLATFORM_DOMAINS:
        return {
            "status": "error",
            "error": f"不支持的平台: {platform}",
            "error_code": CookieErrorCode.PLATFORM_NOT_SUPPORTED
        }

    try:
        import browser_cookie3
        import time

        domain = PLATFORM_DOMAINS[platform]
        browser_lower = browser.lower()

        logger.info(f"正在从 {browser} 浏览器读取 {platform} Cookie...")

        # 在线程池中执行，避免阻塞
        loop = asyncio.get_event_loop()

        def extract_cookies_sync():
            try:
                # Windows 需要初始化 COM
                import sys
                if sys.platform == 'win32':
                    import pythoncom
                    pythoncom.CoInitialize()

                try:
                    # 根据浏览器类型选择对应的函数
                    browser_funcs = {
                        'chrome': browser_cookie3.chrome,
                        'edge': browser_cookie3.edge,
                        'firefox': browser_cookie3.firefox
                    }

                    if browser_lower not in browser_funcs:
                        return {
                            "status": "error",
                            "error": f"不支持的浏览器: {browser}。支持: chrome, edge, firefox"
                        }

                    cookie_list = []
                    max_attempts = 3
                    delay = 0.5
                    for attempt in range(1, max_attempts + 1):
                        try:
                            cookies = browser_funcs[browser_lower](domain_name=domain)
                            cookie_list = list(cookies)
                        except PermissionError as pe:
                            raise Exception(f"权限不足，无法访问浏览器 Cookie。请确保：\n1. 以管理员身份运行程序\n2. 浏览器已完全关闭\n详细错误: {str(pe)}")
                        except Exception as cookie_err:
                            error_msg = str(cookie_err).lower()
                            if 'decrypt' in error_msg or 'key' in error_msg or 'password' in error_msg:
                                raise Exception(f"Cookie 解密失败（可能由于浏览器 App-Bound Encryption 加密限制）。\n\n💡 解决方法：\n1. 尝试使用「受控浏览器登录」功能（推荐，更稳定）\n2. 或手动使用浏览器插件导出 Cookie\n\n详细错误: {str(cookie_err)}")
                            else:
                                raise

                        if cookie_list:
                            break

                        if attempt < max_attempts:
                            logger.warning(f"未读取到 Cookie，{delay:.1f} 秒后重试（{attempt}/{max_attempts}）...")
                            time.sleep(delay)
                            delay = min(delay * 2, 3)
                finally:
                    # 清理 COM
                    if sys.platform == 'win32':
                        pythoncom.CoUninitialize()

                if not cookie_list:
                    return {
                        "status": "error",
                        "error": f"未从 {browser} 浏览器中找到 {domain} 的 Cookie。\n\n💡 可能原因：\n1. 您尚未在 {browser} 浏览器中登录 {platform}\n2. 浏览器 Cookie 已过期\n\n建议：\n1. 在 {browser} 浏览器中访问并登录 {platform}\n2. 重试提取 Cookie",
                        "error_code": CookieErrorCode.NO_COOKIES_FOUND
                    }

                # 转换为 Netscape 格式，过滤无效 Cookie
                lines = ["# Netscape HTTP Cookie File\n"]
                skipped_count = 0
                for cookie in cookie_list:
                    # 跳过无效的 Cookie（name 或 domain 为空）
                    if not cookie.name:
                        logger.warning(f"跳过无效 Cookie: name 为空, domain={cookie.domain}, value={str(cookie.value)[:20]}...")
                        skipped_count += 1
                        continue
                    if not cookie.domain:
                        logger.warning(f"跳过无效 Cookie: domain 为空, name={cookie.name}")
                        skipped_count += 1
                        continue

                    path = cookie.path or '/'
                    lines.append(f"{cookie.domain}\tTRUE\t{path}\t"
                               f"{'TRUE' if cookie.secure else 'FALSE'}\t"
                               f"{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n")

                if skipped_count > 0:
                    logger.info(f"跳过了 {skipped_count} 个无效 Cookie")

                cookie_content = ''.join(lines)
                cookie_count = len(cookie_list) - skipped_count

                # region agent log
                try:
                    log_path = Path(r"d:\Coding Project\VidFlow\VidFlow-Desktop\.cursor\debug.log")
                    sample_line = lines[1] if len(lines) > 1 else ""
                    payload = {
                        "sessionId": "debug-session",
                        "runId": "pre-fix",
                        "hypothesisId": "H3",
                        "location": "backend/src/core/cookie_helper.py:extract_cookies_from_browser",
                        "message": "Browser cookie converted to Netscape",
                        "data": {
                            "platform": platform,
                            "browser": browser,
                            "cookieCount": cookie_count,
                            "sampleLine": sample_line.strip()
                        },
                        "timestamp": int(time.time() * 1000)
                    }
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                # endregion

                logger.info(f"成功从 {browser} 提取 {cookie_count} 个 {platform} Cookie")

                return {
                    "status": "success",
                    "content": cookie_content,
                    "count": cookie_count,
                    "platform": platform,
                    "browser": browser,
                    "message": f"成功从 {browser} 浏览器提取 {cookie_count} 个 Cookie"
                }

            except Exception as e:
                logger.error(f"从浏览器提取 Cookie 失败: {e}")
                return {
                    "status": "error",
                    "error": f"从浏览器提取 Cookie 失败。\n\n错误详情：{str(e)}\n\n💡 建议：\n1. 确保已在 {browser} 浏览器中登录 {platform}\n2. 或使用「自动获取 Cookie」功能",
                    "error_code": CookieErrorCode.INTERNAL_ERROR
                }

        result = await loop.run_in_executor(None, extract_cookies_sync)
        return result

    except ImportError:
        return {
            "status": "error",
            "error": "browser-cookie3 未安装。请运行: pip install browser-cookie3",
            "error_code": CookieErrorCode.INTERNAL_ERROR
        }
    except Exception as e:
        logger.error(f"从浏览器提取 Cookie 发生异常: {e}")
        return {
            "status": "error",
            "error": f"提取 Cookie 失败: {str(e)}",
            "error_code": CookieErrorCode.INTERNAL_ERROR
        }
