"""
工具管理器 - 自动下载和管理 FFmpeg、yt-dlp 等工具
"""
import os
import sys
import zipfile
import tarfile
import shutil
import platform
import logging
import asyncio
import aiohttp
import json
import time
from pathlib import Path
from typing import Optional, Tuple, Callable, Awaitable
from .http_client import create_http_session

logger = logging.getLogger(__name__)

AI_TOOLS_LOCK = asyncio.Lock()

# 工具目录
def get_base_dir():
    """获取基础目录（支持打包后的路径）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的路径
        if sys.platform == 'win32':
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            base_dir = Path(appdata) / 'VidFlow'
        elif sys.platform == 'darwin':
            base_dir = Path.home() / 'Library' / 'Application Support' / 'VidFlow'
        else:
            base_dir = Path.home() / '.local' / 'share' / 'VidFlow'
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    else:
        # 开发环境路径 - backend 目录
        return Path(__file__).parent.parent.parent

def get_project_root():
    """获取项目根目录"""
    if getattr(sys, 'frozen', False):
        # 打包后: 使用打包目录
        return Path(sys._MEIPASS)
    else:
        # 开发环境: backend 的上级目录 = VidFlow-Desktop/
        return Path(__file__).parent.parent.parent.parent

BASE_DIR = get_base_dir()
PROJECT_ROOT = get_project_root()

TOOLS_DIR = BASE_DIR / "tools"
BIN_DIR = TOOLS_DIR / "bin"
MODELS_DIR = TOOLS_DIR / "models"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOOLS_META_PATH = DATA_DIR / "tools_meta.json"
AI_PACKAGES_POINTER_PATH = DATA_DIR / "ai_packages_active.json"

def _load_ai_packages_dir() -> Path:
    try:
        if AI_PACKAGES_POINTER_PATH.exists():
            with open(AI_PACKAGES_POINTER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_path = data.get("path")
            if raw_path:
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = DATA_DIR / raw_path
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
    except Exception as e:
        logger.warning(f"[AI] Failed to load ai_packages pointer: {e}")

    fallback = DATA_DIR / "ai_packages"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

AI_PACKAGES_DIR = _load_ai_packages_dir()

def _set_ai_packages_dir(new_dir: Path):
    global AI_PACKAGES_DIR
    new_dir.mkdir(parents=True, exist_ok=True)

    try:
        data_dir_resolved = DATA_DIR.resolve()
    except Exception:
        data_dir_resolved = None

    def _is_ai_packages_entry(entry: str) -> bool:
        try:
            entry_path = Path(entry)
            if not entry_path.is_absolute():
                return False
            if not entry_path.name.startswith("ai_packages"):
                return False
            if data_dir_resolved is None:
                return False
            try:
                return entry_path.parent.resolve() == data_dir_resolved
            except Exception:
                return False
        except Exception:
            return False

    try:
        filtered: list[str] = []
        new_norm = os.path.normcase(os.path.abspath(str(new_dir)))
        for entry in sys.path:
            try:
                entry_norm = os.path.normcase(os.path.abspath(entry))
            except Exception:
                filtered.append(entry)
                continue
            if entry_norm == new_norm:
                continue
            if _is_ai_packages_entry(entry):
                continue
            filtered.append(entry)
        sys.path[:] = filtered
    except Exception:
        pass

    # 在打包环境中，不要把 AI 包目录放在 sys.path 最前面
    # 这样可以让内置的 numpy 等包优先被加载，避免冲突
    if getattr(sys, 'frozen', False):
        sys.path.append(str(new_dir))
    else:
        sys.path.insert(0, str(new_dir))
    AI_PACKAGES_DIR = new_dir

    try:
        with open(AI_PACKAGES_POINTER_PATH, "w", encoding="utf-8") as f:
            json.dump({"path": str(new_dir)}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[AI] Failed to save ai_packages pointer: {e}")

if str(AI_PACKAGES_DIR) not in sys.path:
    # 在打包环境中，不要把 AI 包目录放在 sys.path 最前面
    # 这样可以让内置的 numpy 等包优先被加载，避免冲突
    # 错误: ImportError: cannot load module more than once per process
    if getattr(sys, 'frozen', False):
        # 打包环境：放到 sys.path 末尾
        sys.path.append(str(AI_PACKAGES_DIR))
        logger.info(f"[AI] Added AI_PACKAGES_DIR to end of sys.path (frozen mode): {AI_PACKAGES_DIR}")
    else:
        # 开发环境：放到 sys.path 开头（保持原有行为）
        sys.path.insert(0, str(AI_PACKAGES_DIR))

AI_CLEANUP_QUEUE_PATH = DATA_DIR / "ai_cleanup_queue.json"

def _load_ai_cleanup_queue() -> list[str]:
    try:
        if AI_CLEANUP_QUEUE_PATH.exists():
            with open(AI_CLEANUP_QUEUE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data if x]
    except Exception as e:
        logger.warning(f"[AI] Failed to load cleanup queue: {e}")
    return []

def _save_ai_cleanup_queue(items: list[str]):
    try:
        if not items:
            if AI_CLEANUP_QUEUE_PATH.exists():
                AI_CLEANUP_QUEUE_PATH.unlink(missing_ok=True)
            return
        with open(AI_CLEANUP_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AI] Failed to save cleanup queue: {e}")

def _enqueue_ai_cleanup(path: Path):
    try:
        p = Path(path)
        if not p.exists():
            return
        items = _load_ai_cleanup_queue()
        raw = str(p)
        if raw not in items:
            items.append(raw)
        _save_ai_cleanup_queue(items)
    except Exception as e:
        logger.warning(f"[AI] Failed to enqueue cleanup path {path}: {e}")

def _process_ai_cleanup_queue():
    try:
        items = _load_ai_cleanup_queue()
        if not items:
            return

        remaining: list[str] = []
        active_norm = os.path.normcase(os.path.abspath(str(AI_PACKAGES_DIR)))
        for raw in items:
            try:
                p = Path(raw)
                p_norm = os.path.normcase(os.path.abspath(str(p)))
                if p_norm == active_norm:
                    remaining.append(raw)
                    continue
                if not p.exists():
                    continue
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
                logger.info(f"[AI] Cleaned up pending path: {p}")
            except Exception as e:
                logger.debug(f"[AI] Pending cleanup failed for {raw}: {e}")
                remaining.append(raw)
        _save_ai_cleanup_queue(remaining)
    except Exception as e:
        logger.warning(f"[AI] Cleanup queue processing failed: {e}")


def _cleanup_conflicting_ai_packages():
    """
    处理 AI 包目录中与打包环境可能冲突的包（如 numpy）

    问题: ImportError: cannot load module more than once per process

    原因: PyInstaller 打包的应用已经内置了 numpy，当 AI 包目录中
    又安装了另一个版本的 numpy 时，C 扩展模块会冲突。

    新策略: 不删除 numpy，而是通过预先导入内置 numpy 来避免冲突。
    这样 AI 包中的 torch/faster-whisper 导入 numpy 时会使用已加载的版本。
    """
    if not getattr(sys, 'frozen', False):
        return  # 只在打包环境中执行

    # 策略：预先导入内置的 numpy，这样后续 AI 包导入时会复用已加载的模块
    try:
        import numpy
        logger.info(f"[AI] Pre-loaded numpy {numpy.__version__} from {numpy.__file__}")
    except ImportError as e:
        logger.warning(f"[AI] Failed to pre-load numpy: {e}")

_process_ai_cleanup_queue()

# 清理 AI 包目录中与打包环境冲突的包（如 numpy）
_cleanup_conflicting_ai_packages()

# 内置工具目录（打包时包含的预下载工具）
# 开发环境: VidFlow-Desktop/resources/tools/bin
# 打包后: dist/VidFlow/resources/tools/bin
BUNDLED_TOOLS_DIR = PROJECT_ROOT / "resources" / "tools"
BUNDLED_BIN_DIR = BUNDLED_TOOLS_DIR / "bin"

# 创建目录
for directory in [TOOLS_DIR, BIN_DIR, MODELS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# 调试信息
logger.info(f"Tool Manager initialized:")
logger.info(f"  BASE_DIR: {BASE_DIR}")
logger.info(f"  PROJECT_ROOT: {PROJECT_ROOT}")
logger.info(f"  BUNDLED_BIN_DIR: {BUNDLED_BIN_DIR}")
logger.info(f"  BUNDLED_BIN_DIR exists: {BUNDLED_BIN_DIR.exists()}")

# 工具下载链接
TOOL_URLS = {
    "ffmpeg": {
        "Windows": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "Darwin": "https://evermeet.cx/ffmpeg/ffmpeg-7.1.zip",  # 使用稳定版本（带 ffprobe）
        "Darwin-arm64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip",
        "Linux": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    },
    "ffprobe": {
        "Darwin": "https://evermeet.cx/ffmpeg/ffprobe-7.1.zip",  # Mac 需要单独下载 ffprobe
        "Darwin-arm64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip",
    },
    "yt-dlp": {
        "Windows": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
        "Darwin": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos",
        "Linux": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    }
}

class ToolManager:
    """管理下载器依赖的工具（FFmpeg、yt-dlp等）"""
    # 超时配置
    HEAD_TIMEOUT = 10
    CONNECT_TIMEOUT = 15
    TOTAL_TIMEOUT = 180
    AUTO_UPDATE_INITIAL_DELAY = 2  # 缩短至 2 秒，尽快完成首次更新检查
    AUTO_UPDATE_MIN_INTERVAL = 3600
    CHUNK_SIZE = 8192
    DOWNLOAD_PROGRESS_MAX = 80
    DOWNLOAD_TOTAL_TIMEOUT = 600
    DOWNLOAD_CONNECT_TIMEOUT = 30
    DOWNLOAD_SOCK_TIMEOUT = 60
    PLAYWRIGHT_STATUS_CACHE_TTL = 30

    def __init__(self):
        self.system = platform.system()
        self.ffmpeg_path = None
        self.ytdlp_path = None
        self.progress_callback: Optional[Callable[[str, int, str], Awaitable[None]]] = None
        self._updating_tools = {}  # 追踪工具更新状态 {tool_id: bool}
        self._auto_update_task: Optional[asyncio.Task] = None
        self._update_lock = asyncio.Lock()  # 防止并发更新
        self.auto_update_enabled = os.environ.get("TOOLS_AUTO_UPDATE_ENABLED", "true").lower() == "true"
        try:
            self.auto_update_interval_hours = int(os.environ.get("TOOLS_AUTO_UPDATE_INTERVAL_HOURS", "24"))
        except ValueError:
            self.auto_update_interval_hours = 24
        # 允许通过环境变量覆盖超时和下载参数
        self.HEAD_TIMEOUT = int(os.environ.get("TOOL_HEAD_TIMEOUT", self.HEAD_TIMEOUT))
        self.CONNECT_TIMEOUT = int(os.environ.get("TOOL_CONNECT_TIMEOUT", self.CONNECT_TIMEOUT))
        self.TOTAL_TIMEOUT = int(os.environ.get("TOOL_TOTAL_TIMEOUT", self.TOTAL_TIMEOUT))
        self.AUTO_UPDATE_INITIAL_DELAY = int(os.environ.get("TOOL_AUTO_UPDATE_INITIAL_DELAY", self.AUTO_UPDATE_INITIAL_DELAY))
        self.AUTO_UPDATE_MIN_INTERVAL = int(os.environ.get("TOOL_AUTO_UPDATE_MIN_INTERVAL", self.AUTO_UPDATE_MIN_INTERVAL))
        self.CHUNK_SIZE = int(os.environ.get("TOOL_CHUNK_SIZE", self.CHUNK_SIZE))
        self.DOWNLOAD_PROGRESS_MAX = int(os.environ.get("TOOL_DL_PROGRESS_MAX", self.DOWNLOAD_PROGRESS_MAX))
        self.DOWNLOAD_TOTAL_TIMEOUT = int(os.environ.get("TOOL_DL_TOTAL_TIMEOUT", self.DOWNLOAD_TOTAL_TIMEOUT))
        self.DOWNLOAD_CONNECT_TIMEOUT = int(os.environ.get("TOOL_DL_CONNECT_TIMEOUT", self.DOWNLOAD_CONNECT_TIMEOUT))
        self.DOWNLOAD_SOCK_TIMEOUT = int(os.environ.get("TOOL_DL_SOCK_TIMEOUT", self.DOWNLOAD_SOCK_TIMEOUT))
        self.PLAYWRIGHT_STATUS_CACHE_TTL = int(
            os.environ.get("TOOL_PLAYWRIGHT_STATUS_CACHE_TTL", self.PLAYWRIGHT_STATUS_CACHE_TTL)
        )
        try:
            import yt_dlp
            self.ytdlp_version = yt_dlp.version.__version__
        except Exception:
            self.ytdlp_version = "unknown"
        self.tools_meta = self._load_tools_meta()
        # 代理：默认直连，如需代理请设置 TOOLS_PROXY（例如 http://user:pass@host:port）
        self.proxy = os.environ.get("TOOLS_PROXY")

        # 标记是否已完成清理
        self._cleanup_done = False
        self._playwright_status_cache: Optional[dict] = None
        self._playwright_status_cache_time = 0.0
        self._playwright_status_lock = asyncio.Lock()

    def _load_tools_meta(self) -> dict:
        if TOOLS_META_PATH.exists():
            try:
                with open(TOOLS_META_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[Tools] Failed to load meta file: {e}")
        return {}

    def _save_tools_meta(self):
        try:
            with open(TOOLS_META_PATH, "w", encoding="utf-8") as f:
                json.dump(self.tools_meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Tools] Failed to save meta file: {e}")

    async def _cleanup_old_temp_files(self):
        """清理旧的临时文件（启动时执行）"""
        try:
            logger.info("[Tools] Starting cleanup of old temporary files...")
            cleanup_count = 0

            # 清理 tools 目录下的临时文件
            patterns = [
                "*.tmp",
                "*.retry*",
                "*_download.zip.tmp",
                "*_download.zip",  # 也清理未完成的下载文件
                "ffmpeg_extract_tmp"
            ]

            for pattern in patterns:
                for temp_file in TOOLS_DIR.glob(pattern):
                    try:
                        # 跳过正在使用的文件
                        if temp_file.is_file():
                            if self._is_file_in_use(temp_file):
                                logger.debug(f"[Tools] Skipping in-use file: {temp_file}")
                                continue
                            temp_file.unlink(missing_ok=True)
                            cleanup_count += 1
                            logger.debug(f"[Tools] Removed temp file: {temp_file}")
                        elif temp_file.is_dir():
                            shutil.rmtree(temp_file, ignore_errors=True)
                            cleanup_count += 1
                            logger.debug(f"[Tools] Removed temp directory: {temp_file}")
                    except Exception as e:
                        logger.warning(f"[Tools] Failed to remove temp file/dir {temp_file}: {e}")

            if cleanup_count > 0:
                logger.info(f"[Tools] Cleaned up {cleanup_count} temporary files/directories")
            else:
                logger.debug("[Tools] No temporary files to clean up")

        except Exception as e:
            logger.warning(f"[Tools] Cleanup of temporary files failed: {e}")

    def set_progress_callback(self, callback: Optional[Callable[[str, int, str], Awaitable[None]]]):
        """设置进度回调函数"""
        self.progress_callback = callback

    async def _notify_progress(self, tool_id: str, progress: int, message: str):
        """通知进度"""
        if self.progress_callback:
            try:
                await self.progress_callback(tool_id, progress, message)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    @staticmethod
    def _is_file_in_use(file_path: Path) -> bool:
        """检查文件是否被占用（Windows 特定）"""
        if not file_path.exists():
            return False

        # 在 Windows 上尝试以独占模式打开文件
        if platform.system() == "Windows":
            try:
                # 尝试以独占写入模式打开
                with open(file_path, 'a') as f:
                    pass
                return False
            except (IOError, OSError, PermissionError):
                return True
        else:
            # 非 Windows 系统，假设文件未被占用
            return False

    @staticmethod
    async def _safe_remove_file(
        file_path: Path,
        max_retries: int = 5,
        retry_delay: float = 1.0,
        log_failures: bool = True,
    ) -> bool:
        """安全删除文件，带重试机制"""
        if not file_path.exists():
            return True

        # Windows 需要更长的延迟和更多重试
        if platform.system() == "Windows":
            max_retries = max(max_retries, 5)
            retry_delay = max(retry_delay, 1.0)

        for attempt in range(max_retries):
            try:
                # 检查文件是否被占用
                if ToolManager._is_file_in_use(file_path):
                    if log_failures:
                        logger.warning(f"[Tools] File is in use, attempt {attempt + 1}/{max_retries}: {file_path}")
                    if attempt < max_retries - 1:
                        # 尝试强制垃圾回收来释放文件句柄
                        import gc
                        gc.collect()
                        await asyncio.sleep(retry_delay * (attempt + 1))  # 递增延迟
                        continue
                    else:
                        if log_failures:
                            logger.error(f"[Tools] File still in use after {max_retries} attempts: {file_path}")
                        return False

                # 尝试删除文件
                file_path.unlink(missing_ok=True)
                logger.info(f"[Tools] Successfully removed file: {file_path}")
                return True

            except (IOError, OSError, PermissionError) as e:
                if log_failures:
                    logger.warning(f"[Tools] Failed to remove file (attempt {attempt + 1}/{max_retries}): {file_path}, error: {e}")
                if attempt < max_retries - 1:
                    # 尝试强制垃圾回收
                    import gc
                    gc.collect()
                    await asyncio.sleep(retry_delay * (attempt + 1))  # 递增延迟
                else:
                    if log_failures:
                        logger.error(f"[Tools] Failed to remove file after {max_retries} attempts: {file_path}")
                    return False

        return False

    @staticmethod
    def _defer_cleanup_file(file_path: Path, max_suffix: int = 10) -> Optional[Path]:
        """Rename a busy temp file so it can be cleaned up on a later run."""
        if not file_path.exists():
            return None

        for suffix in range(1, max_suffix + 1):
            deferred_path = file_path.with_name(f"{file_path.name}.retry{suffix}")
            if deferred_path.exists():
                continue
            try:
                file_path.replace(deferred_path)
                return deferred_path
            except (IOError, OSError, PermissionError):
                continue

        return None

    @staticmethod
    async def _safe_replace_file(src: Path, dst: Path, max_retries: int = 5, retry_delay: float = 1.0) -> bool:
        """安全替换文件，带重试机制"""
        if not src.exists():
            logger.error(f"[Tools] Source file does not exist: {src}")
            return False

        # Windows 需要更长的延迟和更多重试
        if platform.system() == "Windows":
            max_retries = max(max_retries, 5)
            retry_delay = max(retry_delay, 1.0)

        for attempt in range(max_retries):
            try:
                # 检查源文件是否被占用
                if ToolManager._is_file_in_use(src):
                    logger.warning(f"[Tools] Source file is in use, attempt {attempt + 1}/{max_retries}: {src}")
                    if attempt < max_retries - 1:
                        import gc
                        gc.collect()
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        logger.error(f"[Tools] Source file still in use after {max_retries} attempts: {src}")
                        return False

                # 检查目标文件是否被占用
                if dst.exists() and ToolManager._is_file_in_use(dst):
                    logger.warning(f"[Tools] Destination file is in use, attempt {attempt + 1}/{max_retries}: {dst}")
                    if attempt < max_retries - 1:
                        import gc
                        gc.collect()
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        logger.error(f"[Tools] Destination file still in use after {max_retries} attempts: {dst}")
                        return False

                # 尝试替换文件
                src.replace(dst)
                logger.info(f"[Tools] Successfully replaced file: {src} -> {dst}")
                return True

            except (IOError, OSError, PermissionError) as e:
                logger.warning(f"[Tools] Failed to replace file (attempt {attempt + 1}/{max_retries}): {src} -> {dst}, error: {e}")
                if attempt < max_retries - 1:
                    import gc
                    gc.collect()
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    logger.error(f"[Tools] Failed to replace file after {max_retries} attempts: {src} -> {dst}")
                    return False

        return False

    async def setup_all_tools(self) -> dict:
        """设置所有工具"""
        results = {}

        # 设置 FFmpeg
        try:
            self.ffmpeg_path = await self.setup_ffmpeg()
            results['ffmpeg'] = {
                'success': True,
                'path': str(self.ffmpeg_path)
            }
        except Exception as e:
            logger.error(f"Setup FFmpeg failed: {e}")
            results['ffmpeg'] = {
                'success': False,
                'error': str(e)
            }

        # 设置 yt-dlp
        try:
            self.ytdlp_path = await self.setup_ytdlp()
            results['yt-dlp'] = {
                'success': True,
                'path': str(self.ytdlp_path)
            }
        except Exception as e:
            logger.error(f"Setup yt-dlp failed: {e}")
            results['yt-dlp'] = {
                'success': False,
                'error': str(e)
            }

        return results

    async def run_auto_update_loop(self, interval_hours: int = 24):
        """定期自动检查并更新 yt-dlp / FFmpeg"""
        # 首次启动时延迟几秒，避免抢资源
        await asyncio.sleep(self.AUTO_UPDATE_INITIAL_DELAY)
        interval_seconds = max(self.AUTO_UPDATE_MIN_INTERVAL, interval_hours * 3600)

        while True:
            try:
                logger.info("[Tools] Auto-update: start check")
                await self.check_and_update_tool("yt-dlp")
                await self.check_and_update_tool("ffmpeg")
                logger.info("[Tools] Auto-update: completed")
            except Exception as e:
                logger.error(f"[Tools] Auto-update failed: {e}", exc_info=True)

            await asyncio.sleep(interval_seconds)

    def _get_download_url(self, tool_id: str) -> Optional[str]:
        urls = TOOL_URLS.get(tool_id, {})
        if self.system == 'Darwin' and platform.machine().lower() in ('arm64', 'aarch64'):
            return urls.get('Darwin-arm64') or urls.get(self.system)
        return urls.get(self.system)

    async def _get_remote_headers(self, url: str) -> dict:
        """获取远端资源的 ETag / Last-Modified，用于判断是否需要下载"""
        headers = {}

        # 尝试 HEAD
        try:
            async with create_http_session() as session:
                async with session.head(
                    url,
                    allow_redirects=True,
                    timeout=self.HEAD_TIMEOUT,
                    proxy=self.proxy
                ) as resp:
                    if resp.status < 400:
                        headers["etag"] = resp.headers.get("ETag")
                        headers["last_modified"] = resp.headers.get("Last-Modified")
        except Exception as e:
            logger.info(f"[Tools] HEAD request failed for {url}: {e}")

        # 如果 HEAD 失败或无头信息，尝试 Range GET 0-0 获取部分头
        if not headers:
            try:
                async with create_http_session() as session:
                    async with session.get(
                        url,
                        allow_redirects=True,
                        timeout=15,
                        proxy=self.proxy,
                        headers={"Range": "bytes=0-0"}
                    ) as resp:
                        if resp.status < 400:
                            headers["etag"] = resp.headers.get("ETag")
                            headers["last_modified"] = resp.headers.get("Last-Modified")
            except Exception as e:
                logger.info(f"[Tools] Fallback GET (Range) failed for {url}: {e}")

        return headers

    async def _get_with_proxy_fallback(self, url: str, **kwargs):
        """
        默认直连；如配置了 TOOLS_PROXY，则优先代理，失败回退直连。
        """
        timeout = aiohttp.ClientTimeout(total=self.TOTAL_TIMEOUT, connect=self.CONNECT_TIMEOUT)
        # 直连优先
        if not self.proxy:
            async with create_http_session(timeout=timeout) as session:
                return await session.get(url, **kwargs)
        # 有代理：先试代理，失败再直连
        try:
            async with create_http_session(timeout=timeout) as session:
                return await session.get(url, proxy=self.proxy, **kwargs)
        except Exception as e:
            logger.info(f"[Tools] GET with proxy failed, retry direct. url={url}, err={e}")
            async with create_http_session(timeout=timeout) as session:
                return await session.get(url, **kwargs)

    async def _should_download(self, tool_id: str, url: str) -> Tuple[bool, dict]:
        """根据远端元数据与本地状态判断是否需要下载"""
        headers = await self._get_remote_headers(url)
        etag = headers.get("etag")
        last_modified = headers.get("last_modified")

        meta = self.tools_meta.get(tool_id, {})
        tool_path = self._get_tool_path(tool_id)
        tool_exists = tool_path is not None and tool_path.exists()

        # 如果有 ETag，比较 ETag
        if etag:
            if meta.get("etag") == etag and tool_exists:
                logger.info(f"[Tools] {tool_id} ETag unchanged: {etag}")
                return False, meta
            logger.info(f"[Tools] {tool_id} ETag changed: {meta.get('etag')} -> {etag}")
            new_meta = {
                "etag": etag,
                "last_modified": last_modified,
                "url": url
            }
            return True, new_meta

        # 如果有 Last-Modified，比较时间
        if last_modified:
            if meta.get("last_modified") == last_modified and tool_exists:
                logger.info(f"[Tools] {tool_id} Last-Modified unchanged: {last_modified}")
                return False, meta
            logger.info(f"[Tools] {tool_id} Last-Modified changed: {meta.get('last_modified')} -> {last_modified}")
            new_meta = {
                "etag": etag,
                "last_modified": last_modified,
                "url": url
            }
            return True, new_meta

        # 如果远端没有元数据
        if not etag and not last_modified:
            if not meta or not tool_exists:
                logger.info(f"[Tools] {tool_id} missing metadata or local file, forcing download")
                return True, meta or {}
            logger.info(f"[Tools] {tool_id} already installed but remote metadata unavailable, skip update")
            return False, meta

        return False, meta

    async def check_and_update_tool(self, tool_id: str):
        """检查并更新指定工具（简单策略：每次按最新地址下载替换）"""
        async with self._update_lock:
            if tool_id not in ("yt-dlp", "ffmpeg"):
                logger.warning(f"[Tools] Unknown tool_id: {tool_id}")
                return

            # 标准化 tool_id 用于前端显示（yt-dlp -> ytdlp）
            display_id = "ytdlp" if tool_id == "yt-dlp" else tool_id

            # 避免并发重复下载
            if self._updating_tools.get(display_id):
                logger.info(f"[Tools] {tool_id} is already updating, skip")
                return

            # 获取下载 URL
            url = self._get_download_url(tool_id)
            if not url:
                logger.warning(f"[Tools] No download URL for {tool_id} on {self.system}")
                return

            # 版本/变更检测：通过 ETag / Last-Modified
            should_download, meta = await self._should_download(tool_id, url)
            if not should_download:
                logger.info(f"[Tools] {tool_id} is up to date (ETag/Last-Modified unchanged), skip download")
                return

            self._updating_tools[display_id] = True
            try:
                if tool_id == "yt-dlp":
                    await self.download_ytdlp()
                elif tool_id == "ffmpeg":
                    await self.download_ffmpeg()

                # 下载完成后，记录元数据（即使为空也要保存，表示已安装过）
                # 这样下次检查时就不会重复下载
                if not meta:
                    # 如果没有 ETag/Last-Modified，使用时间戳作为标记
                    meta = {"timestamp": str(__import__('datetime').datetime.now().isoformat())}

                self.tools_meta[tool_id] = meta
                self._save_tools_meta()
                logger.info(f"[Tools] {tool_id} download completed, metadata saved: {meta}")
            finally:
                self._updating_tools[display_id] = False

    async def setup_ffmpeg(self) -> Path:
        """设置 FFmpeg - 优先使用已下载版本（便于自动更新），其次内置"""
        exe_name = "ffmpeg.exe" if self.system == "Windows" else "ffmpeg"

        # 1. 检查已下载的版本（最高优先级，支持自动更新）
        builtin_path = BIN_DIR / exe_name
        if builtin_path.exists():
            logger.info(f"Using downloaded FFmpeg: {builtin_path}")
            return builtin_path

        # 2. 检查打包的内置版本（备用）
        bundled_path = BUNDLED_BIN_DIR / exe_name
        if bundled_path.exists():
            logger.info(f"[OK] Using bundled FFmpeg: {bundled_path}")
            return bundled_path

        # 3. 检查系统安装
        system_path = shutil.which("ffmpeg")
        if system_path:
            logger.info(f"Using system FFmpeg: {system_path}")
            return Path(system_path)

        # 4. 最后才自动下载（开发模式）
        logger.info("No FFmpeg found, downloading...")
        await self.download_ffmpeg()

        if builtin_path.exists():
            return builtin_path

        raise RuntimeError("Failed to setup FFmpeg")

    async def setup_ytdlp(self) -> Path:
        """设置 yt-dlp - 优先使用用户下载的版本（支持更新）"""
        exe_name = "yt-dlp.exe" if self.system == "Windows" else "yt-dlp"

        # 1. 检查已下载的版本（最高优先级 - 用户主动更新的）
        builtin_path = BIN_DIR / exe_name
        if builtin_path.exists():
            logger.info(f"Using downloaded yt-dlp: {builtin_path}")
            # 确保可执行
            if self.system != "Windows":
                os.chmod(builtin_path, 0o755)
            return builtin_path

        # 2. 检查打包的内置版本（备用版本）
        bundled_path = BUNDLED_BIN_DIR / exe_name
        if bundled_path.exists():
            logger.info(f"[OK] Using bundled yt-dlp: {bundled_path}")
            # 确保可执行
            if self.system != "Windows":
                os.chmod(bundled_path, 0o755)
            return bundled_path

        # 3. 检查系统安装
        system_path = shutil.which("yt-dlp")
        if system_path:
            logger.info(f"Using system yt-dlp: {system_path}")
            return Path(system_path)

        # 4. 最后才自动下载（开发模式）
        logger.info("No yt-dlp found, downloading...")
        await self.download_ytdlp()

        if builtin_path.exists():
            # 确保可执行
            if self.system != "Windows":
                os.chmod(builtin_path, 0o755)
            return builtin_path

        raise RuntimeError("Failed to setup yt-dlp")

    async def _download_with_progress(self, url: str, target_path: Path, tool_id: str, progress_max: int = None):
        """通用下载器，带进度与代理回退。"""
        progress_max = progress_max or self.DOWNLOAD_PROGRESS_MAX
        timeout = aiohttp.ClientTimeout(
            total=self.DOWNLOAD_TOTAL_TIMEOUT,
            connect=self.DOWNLOAD_CONNECT_TIMEOUT,
            sock_read=self.DOWNLOAD_SOCK_TIMEOUT
        )

        async def _stream_download(session, get_kwargs=None):
            get_kwargs = get_kwargs or {}
            async with session.get(url, **get_kwargs) as response:
                if response.status != 200:
                    raise RuntimeError(f"Download failed: HTTP {response.status}")

                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0

                with open(target_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(self.CHUNK_SIZE):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            progress = int((downloaded / total_size) * progress_max)
                        else:
                            progress = min(progress_max, downloaded // (512 * 1024))
                        await self._notify_progress(
                            tool_id,
                            progress,
                            f"下载中... {downloaded // 1024 // 1024}MB / {max(1, total_size // 1024 // 1024)}MB"
                        )

        # 直连优先
        if not self.proxy:
            async with create_http_session(timeout=timeout) as session:
                await _stream_download(session)
            return

        # 有代理：先试代理，失败再直连
        try:
            async with create_http_session(timeout=timeout) as session:
                await _stream_download(session, {"proxy": self.proxy})
        except Exception as e:
            logger.info(f"[Tools] Download with proxy failed, retry direct. url={url}, err={e}")
            async with create_http_session(timeout=timeout) as session:
                await _stream_download(session)

    def _validate_zip_integrity(self, zip_path: Path) -> bool:
        """校验 ZIP 完整性，返回是否通过。"""
        zf = None
        try:
            zf = zipfile.ZipFile(zip_path, "r")
            bad_file = zf.testzip()  # 返回首个 CRC 错误的文件名；正常返回 None
            if bad_file:
                logger.warning(f"[Tools] ZIP CRC check failed: {bad_file}")
                return False
            return True
        except Exception as e:
            logger.warning(f"[Tools] ZIP validation failed: {e}")
            return False
        finally:
            # 确保 ZIP 文件被关闭
            if zf is not None:
                try:
                    zf.close()
                except Exception:
                    pass
            # Windows 需要额外时间释放文件句柄
            if platform.system() == "Windows":
                import gc
                gc.collect()  # 强制垃圾回收，帮助释放文件句柄

    async def download_ffmpeg(self):
        """下载 FFmpeg"""
        url = self._get_download_url('ffmpeg')
        if not url:
            raise RuntimeError(f"No FFmpeg download URL for {self.system}")

        download_ext = url.split('.')[-1]
        download_path = TOOLS_DIR / f"ffmpeg_download.{download_ext}"
        temp_path = download_path.with_suffix(download_path.suffix + ".tmp")

        try:
            await self._notify_progress("ffmpeg", 0, "开始下载 FFmpeg...")

            last_error = None
            for attempt in range(2):
                # 使用临时文件避免被安全软件锁定，成功后再原子替换
                candidate = temp_path if attempt == 0 else temp_path.with_suffix(temp_path.suffix + f".retry{attempt}")

                # 安全删除已存在的临时文件
                if candidate.exists():
                    await self._safe_remove_file(candidate, max_retries=3, retry_delay=0.5)

                try:
                    await self._download_with_progress(url, candidate, "ffmpeg", progress_max=40)
                    logger.info(f"FFmpeg downloaded to {candidate}")

                    # 校验 ZIP 完整性，失败则重试一次
                    if download_path.suffix == ".zip":
                        if not self._validate_zip_integrity(candidate):
                            last_error = RuntimeError("FFmpeg archive CRC check failed, will retry")
                            logger.warning(last_error)
                            continue
                        # Windows 需要额外时间释放文件句柄
                        if self.system == "Windows":
                            await asyncio.sleep(0.5)

                    # 安全替换正式文件名
                    replace_success = await self._safe_replace_file(candidate, download_path, max_retries=5, retry_delay=1.0)
                    if not replace_success:
                        raise RuntimeError(f"Failed to replace file: {candidate} -> {download_path} (file may be in use)")

                    await self._notify_progress("ffmpeg", 50, "FFmpeg 下载完成，开始解压...")
                    await self.extract_ffmpeg(download_path)

                    # Mac 需要单独下载 ffprobe
                    if self.system == "Darwin":
                        await self._notify_progress("ffmpeg", 60, "开始下载 ffprobe...")
                        ffprobe_url = self._get_download_url('ffprobe')
                        if ffprobe_url:
                            ffprobe_download_path = TOOLS_DIR / "ffprobe_download.zip"
                            ffprobe_temp_path = ffprobe_download_path.with_suffix(ffprobe_download_path.suffix + ".tmp")

                            try:
                                await self._download_with_progress(ffprobe_url, ffprobe_temp_path, "ffmpeg", progress_max=20)
                                await self._safe_replace_file(ffprobe_temp_path, ffprobe_download_path, max_retries=3, retry_delay=0.5)
                                await self._notify_progress("ffmpeg", 90, "ffprobe 下载完成，开始解压...")
                                await self.extract_ffmpeg(ffprobe_download_path)
                                # 清理 ffprobe 下载文件
                                if ffprobe_download_path.exists():
                                    await self._safe_remove_file(ffprobe_download_path, max_retries=3, retry_delay=0.5)
                            except Exception as e:
                                logger.warning(f"Failed to download ffprobe: {e}, continuing without it")

                    await self._notify_progress("ffmpeg", 100, "安装完成")
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.error(f"Failed to download/extract FFmpeg (attempt {attempt + 1}): {e}")
                    # 失败后安全清理当前临时文件
                    if candidate.exists():
                        await self._safe_remove_file(candidate, max_retries=2, retry_delay=0.5)
                    # 如果是首次失败，尝试再试一次
                    if attempt == 0:
                        await asyncio.sleep(1)  # 等待1秒后重试
                        continue
                    raise

            if last_error:
                raise last_error

        except Exception as e:
            logger.error(f"Failed to download FFmpeg: {e}")
            raise
        finally:
            # 安全清理下载文件（增加重试次数和延迟）
            # 如果清理失败，只记录警告，不影响主流程
            if download_path.exists():
                remove_success = await self._safe_remove_file(
                    download_path,
                    max_retries=5,
                    retry_delay=1.0,
                    log_failures=False,
                )
                if not remove_success:
                    deferred_path = self._defer_cleanup_file(download_path)
                    if deferred_path:
                        logger.warning(f"[Tools] Deferred cleanup for busy temporary file: {deferred_path}")
                    else:
                        logger.warning(f"Failed to remove temporary file (will be cleaned up later): {download_path}")
            if temp_path.exists():
                remove_success = await self._safe_remove_file(
                    temp_path,
                    max_retries=5,
                    retry_delay=1.0,
                    log_failures=False,
                )
                if not remove_success:
                    deferred_path = self._defer_cleanup_file(temp_path)
                    if deferred_path:
                        logger.warning(f"[Tools] Deferred cleanup for busy temporary file: {deferred_path}")
                    else:
                        logger.warning(f"Failed to remove temporary file (will be cleaned up later): {temp_path}")

    async def extract_ffmpeg(self, archive_path: Path):
        """解压 FFmpeg"""
        try:
            exe_ext = ".exe" if self.system == "Windows" else ""
            target_ffmpeg = BIN_DIR / f"ffmpeg{exe_ext}"
            target_ffprobe = BIN_DIR / f"ffprobe{exe_ext}"

            # 备份旧文件（更新失败时可恢复）
            backup_files = []
            for target in [target_ffmpeg, target_ffprobe]:
                if target.exists():
                    # 检查文件是否被占用
                    if self._is_file_in_use(target):
                        raise RuntimeError(f"FFmpeg executable is in use, cannot update: {target}. Please close all applications using FFmpeg and try again.")

                    backup_path = target.with_suffix(target.suffix + '.backup')
                    shutil.copy2(target, backup_path)
                    backup_files.append((target, backup_path))

                    # 安全删除旧文件
                    remove_success = await self._safe_remove_file(target, max_retries=3, retry_delay=0.5)
                    if not remove_success:
                        raise RuntimeError(f"Failed to remove old FFmpeg executable: {target} (file may be in use)")

            if archive_path.suffix == '.zip':
                zip_ref = None
                try:
                    zip_ref = zipfile.ZipFile(archive_path, 'r')
                    # 解压全部到临时目录，再移动需要的文件
                    extract_dir = TOOLS_DIR / "ffmpeg_extract_tmp"
                    extract_dir.mkdir(parents=True, exist_ok=True)

                    # 安全检查：防止路径遍历攻击
                    for file in zip_ref.namelist():
                        file_path = Path(file)
                        if file_path.is_absolute() or '..' in file_path.parts:
                            raise RuntimeError(f"不安全的文件路径: {file}")

                    zip_ref.extractall(extract_dir)

                    ffmpeg_found = False
                    ffprobe_found = False

                    for file in zip_ref.namelist():
                        lower = file.lower()
                        if lower.endswith(f"ffmpeg{exe_ext}") or lower.endswith(f"ffprobe{exe_ext}"):
                            src = extract_dir / file
                            if not src.exists():
                                continue
                            dst = target_ffmpeg if lower.endswith(f"ffmpeg{exe_ext}") else target_ffprobe
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(src), str(dst))
                            if self.system != "Windows":
                                os.chmod(dst, 0o755)
                            if dst == target_ffmpeg:
                                ffmpeg_found = True
                            else:
                                ffprobe_found = True

                    shutil.rmtree(extract_dir, ignore_errors=True)

                    # Mac 分两次下载 ffmpeg 和 ffprobe，所以只要找到一个即可
                    if self.system == "Darwin":
                        if not ffmpeg_found and not ffprobe_found:
                            raise RuntimeError("FFmpeg package missing ffmpeg/ffprobe executable")
                    else:
                        # Windows/Linux 要求同时找到 ffmpeg 和 ffprobe
                        if not ffmpeg_found or not ffprobe_found:
                            raise RuntimeError("FFmpeg package missing ffmpeg/ffprobe executable")
                finally:
                    # 确保 ZIP 文件被关闭
                    if zip_ref is not None:
                        try:
                            zip_ref.close()
                        except Exception as e:
                            logger.warning(f"Failed to close ZIP file: {e}")
                    # Windows 需要额外时间释放文件句柄
                    if self.system == "Windows":
                        import gc
                        gc.collect()
                        await asyncio.sleep(1.0)  # 给 Windows 足够时间释放文件句柄

            elif archive_path.suffix in ['.tar', '.xz', '.gz']:
                mode = 'r:xz' if archive_path.suffix == '.xz' else 'r:gz'
                with tarfile.open(archive_path, mode) as tar_ref:
                    # 安全检查：防止路径遍历攻击
                    for member in tar_ref.getmembers():
                        if member.name.startswith('/') or '..' in member.name:
                            raise RuntimeError(f"不安全的文件路径: {member.name}")

                    ffmpeg_found = False
                    ffprobe_found = False
                    for member in tar_ref.getmembers():
                        if member.isdir():
                            continue
                        name = member.name
                        if name.endswith(f"ffmpeg{exe_ext}") or name.endswith(f"ffprobe{exe_ext}"):
                            tar_ref.extract(member, TOOLS_DIR)
                            extracted_path = TOOLS_DIR / name
                            dst = target_ffmpeg if name.endswith(f"ffmpeg{exe_ext}") else target_ffprobe
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(extracted_path), str(dst))
                            if self.system != "Windows":
                                os.chmod(dst, 0o755)
                            if dst == target_ffmpeg:
                                ffmpeg_found = True
                            else:
                                ffprobe_found = True

                    # Mac 分两次下载 ffmpeg 和 ffprobe，所以只要找到一个即可
                    if self.system == "Darwin":
                        if not ffmpeg_found and not ffprobe_found:
                            raise RuntimeError("FFmpeg package missing ffmpeg/ffprobe executable")
                    else:
                        # Windows/Linux 要求同时找到 ffmpeg 和 ffprobe
                        if not ffmpeg_found or not ffprobe_found:
                            raise RuntimeError("FFmpeg package missing ffmpeg/ffprobe executable")

            # 清理临时目录
            for item in TOOLS_DIR.iterdir():
                if item.is_dir() and item != BIN_DIR and item != MODELS_DIR:
                    shutil.rmtree(item, ignore_errors=True)

            # 安全清理备份文件
            for _, backup_path in backup_files:
                if backup_path.exists():
                    await self._safe_remove_file(backup_path, max_retries=2, retry_delay=0.3)

        except Exception as e:
            logger.error(f"Failed to extract FFmpeg: {e}")
            # 恢复备份文件
            for target, backup_path in backup_files:
                if backup_path.exists():
                    try:
                        shutil.copy2(backup_path, target)
                        logger.info(f"[Tools] Restored backup: {backup_path} -> {target}")
                    except Exception as restore_err:
                        logger.error(f"[Tools] Failed to restore backup: {restore_err}")
            raise

    async def download_ytdlp(self):
        """下载 yt-dlp"""
        url = self._get_download_url('yt-dlp')
        if not url:
            raise RuntimeError(f"No yt-dlp download URL for {self.system}")

        exe_name = "yt-dlp.exe" if self.system == "Windows" else "yt-dlp"
        target_path = BIN_DIR / exe_name

        try:
            await self._notify_progress("ytdlp", 0, "开始下载 yt-dlp...")
            logger.info(f"[yt-dlp] Downloading from: {url}")
            logger.info(f"[yt-dlp] Target path: {target_path}")

            # 先删除可能存在的旧文件（包括 Mac 的 yt-dlp_macos）
            if self.system == "Darwin":
                old_mac_file = BIN_DIR / "yt-dlp_macos"
                if old_mac_file.exists():
                    try:
                        await self._safe_remove_file(old_mac_file, max_retries=3, retry_delay=0.5)
                        logger.info(f"[yt-dlp] Removed old Mac file: {old_mac_file}")
                    except Exception as e:
                        logger.warning(f"[yt-dlp] Failed to remove old Mac file: {e}")

            # 删除旧的 yt-dlp 文件
            if target_path.exists():
                await self._safe_remove_file(target_path, max_retries=3, retry_delay=0.5)

            # 通用下载 - 直接保存为目标文件名
            await self._download_with_progress(url, target_path, "ytdlp", progress_max=100)

            # 验证文件是否成功下载
            if not target_path.exists():
                raise RuntimeError(f"yt-dlp download failed: file not found at {target_path}")

            file_size = target_path.stat().st_size
            logger.info(f"[yt-dlp] Downloaded successfully: {target_path} ({file_size} bytes)")

            # 设置可执行权限（非 Windows 平台）
            if self.system != "Windows":
                os.chmod(target_path, 0o755)
                logger.info(f"[yt-dlp] Set executable permission for {target_path}")

            await self._notify_progress("ytdlp", 100, "安装完成")

        except Exception as e:
            logger.error(f"Failed to download yt-dlp: {e}")
            raise

    def _get_tool_path(self, tool_id: str) -> Optional[Path]:
        """获取工具路径的辅助方法"""
        if tool_id == "ffmpeg":
            return self._resolve_ffmpeg_path()
        elif tool_id == "yt-dlp":
            return self._resolve_ytdlp_path()
        return None

    def _resolve_ffmpeg_path(self) -> Optional[Path]:
        """获取 FFmpeg 路径"""
        # 如果已经设置，直接返回
        if self.ffmpeg_path:
            return self.ffmpeg_path

        # 否则尝试查找（同步版本，用于快速检测）
        exe_name = "ffmpeg.exe" if self.system == "Windows" else "ffmpeg"

        # 1. 检查打包的内置版本
        bundled_path = BUNDLED_BIN_DIR / exe_name
        if bundled_path.exists():
            self.ffmpeg_path = bundled_path
            return bundled_path

        # 2. 检查已下载的版本
        builtin_path = BIN_DIR / exe_name
        if builtin_path.exists():
            self.ffmpeg_path = builtin_path
            return builtin_path

        # 3. 检查系统安装
        system_path = shutil.which("ffmpeg")
        if system_path:
            self.ffmpeg_path = Path(system_path)
            return Path(system_path)

        return None

    def get_ffmpeg_path(self) -> Optional[str]:
        ffmpeg_path = self._resolve_ffmpeg_path()
        return str(ffmpeg_path) if ffmpeg_path else None

    def _resolve_ytdlp_path(self) -> Optional[Path]:
        """获取 yt-dlp 路径"""
        # 如果已经设置，直接返回
        if self.ytdlp_path:
            logger.debug(f"[yt-dlp] Using cached path: {self.ytdlp_path}")
            return self.ytdlp_path

        # 否则尝试查找（同步版本，用于快速检测）
        exe_name = "yt-dlp.exe" if self.system == "Windows" else "yt-dlp"
        logger.debug(f"[yt-dlp] Searching for {exe_name}...")

        # Mac 特殊处理：如果存在旧的 yt-dlp_macos 文件，删除它
        if self.system == "Darwin":
            old_mac_file = BIN_DIR / "yt-dlp_macos"
            if old_mac_file.exists():
                try:
                    old_mac_file.unlink()
                    logger.info(f"[yt-dlp] Removed old Mac file: {old_mac_file}")
                except Exception as e:
                    logger.warning(f"[yt-dlp] Failed to remove old Mac file: {e}")

        # 1. 检查打包的内置版本
        bundled_path = BUNDLED_BIN_DIR / exe_name
        logger.debug(f"[yt-dlp] Checking bundled: {bundled_path} (exists: {bundled_path.exists()})")
        if bundled_path.exists():
            self.ytdlp_path = bundled_path
            logger.debug(f"[yt-dlp] Found bundled version: {bundled_path}")
            return bundled_path

        # 2. 检查已下载的版本
        builtin_path = BIN_DIR / exe_name
        logger.debug(f"[yt-dlp] Checking downloaded: {builtin_path} (exists: {builtin_path.exists()})")
        if builtin_path.exists():
            self.ytdlp_path = builtin_path
            logger.debug(f"[yt-dlp] Found downloaded version: {builtin_path}")
            return builtin_path

        # 3. 检查系统安装
        system_path = shutil.which("yt-dlp")
        logger.debug(f"[yt-dlp] Checking system PATH: {system_path}")
        if system_path:
            self.ytdlp_path = Path(system_path)
            logger.debug(f"[yt-dlp] Found system version: {system_path}")
            return Path(system_path)

        logger.debug("[yt-dlp] Not found in any location")
        return None

    def get_ytdlp_path(self) -> Optional[str]:
        ytdlp_path = self._resolve_ytdlp_path()
        return str(ytdlp_path) if ytdlp_path else None

    async def check_ai_tools_status(self) -> dict:
        """检查 AI 工具状态（faster-whisper + PyTorch）"""
        async with AI_TOOLS_LOCK:
            result = {
                "installed": False,
                "faster_whisper": False,
                "torch": False,
                "version": None,
                "torch_version": None,
                "device": "unknown",
                "python_compatible": True  # 软件内置 Python 3.11，始终兼容
            }

            try:
                # 移除了 Python 版本检查，因为软件使用内置的 Python 3.11 环境
                # 不需要检查系统 Python 版本

                # 使用 importlib.metadata 检查包是否真的安装（不依赖 import 缓存）
                try:
                    from importlib.metadata import version as get_version, PackageNotFoundError
                except ImportError:
                    # Python < 3.8 fallback
                    from importlib_metadata import version as get_version, PackageNotFoundError

                # 检查 faster-whisper
                try:
                    fw_version = get_version("faster-whisper")
                    result["faster_whisper"] = True
                    result["version"] = fw_version
                except PackageNotFoundError:
                    result["faster_whisper"] = False

                # 检查 PyTorch
                try:
                    torch_version = get_version("torch")
                    result["torch"] = True
                    result["torch_version"] = torch_version

                    try:
                        python_exe = sys.executable
                        if getattr(sys, 'frozen', False):
                            base_path = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(sys.executable).parent / '_internal'
                            # 根据平台选择正确的 Python 可执行文件名
                            python_name = 'python.exe' if sys.platform == 'win32' else 'python'
                            embedded_python = base_path / 'python' / python_name
                            if embedded_python.exists():
                                python_exe = str(embedded_python)

                        env = os.environ.copy()
                        env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")

                        logger.info(f"[Device Detection] Python: {python_exe}")
                        logger.info(f"[Device Detection] AI_PACKAGES_DIR: {AI_PACKAGES_DIR}")

                        # 直接在代码中插入 sys.path，而不是依赖 PYTHONPATH 环境变量
                        # 这对打包后的嵌入式 Python 更可靠
                        detection_code = f'''
import sys
sys.path.insert(0, r"{AI_PACKAGES_DIR}")
import torch
if torch.cuda.is_available():
    print("cuda")
elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
    print("mps")
else:
    print("cpu")
'''

                        process = await asyncio.create_subprocess_exec(
                            python_exe,
                            '-c',
                            detection_code,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env
                        )
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)

                        logger.info(f"[Device Detection] Return code: {process.returncode}")
                        logger.info(f"[Device Detection] stdout: {stdout.decode('utf-8', errors='ignore').strip()}")
                        if stderr:
                            logger.warning(f"[Device Detection] stderr: {stderr.decode('utf-8', errors='ignore').strip()}")

                        if process.returncode == 0:
                            device = stdout.decode('utf-8', errors='ignore').strip().lower()
                            if device in {"cuda", "mps", "cpu"}:
                                result["device"] = device
                                logger.info(f"[Device Detection] Detected device: {device}")
                        else:
                            result["device"] = "cpu"
                            stderr_text = stderr.decode('utf-8', errors='ignore').strip() if stderr else ''
                            logger.warning(f"[Device Detection] Failed, defaulting to CPU. Error: {stderr_text}")
                    except Exception as e:
                        result["device"] = "cpu"
                        logger.error(f"[Device Detection] Exception, defaulting to CPU: {e}", exc_info=True)
                except PackageNotFoundError:
                    result["torch"] = False

                # 两者都安装才认为完整
                result["installed"] = result["faster_whisper"] and result["torch"]

            except Exception as e:
                result["error"] = str(e)

            return result

    async def install_ai_tools(self, version: str = "cpu", progress_callback=None) -> dict:
        """
        安装 AI 工具（faster-whisper + PyTorch）

        Args:
            version: "cpu" 或 "cuda"
            progress_callback: 进度回调函数 callback(percent, message)

        Returns:
            {"success": bool, "message": str, "error": str}
        """
        async with AI_TOOLS_LOCK:
            try:
                # 检查 Python 版本
                python_version = sys.version_info
                if python_version.major == 3 and python_version.minor >= 12:
                    error_msg = (
                        f"faster-whisper 不支持 Python {python_version.major}.{python_version.minor}\n"
                        f"需要 Python 3.8-3.11\n\n"
                        f"解决方案:\n"
                        f"1. 运行 backend/FIX_PYTHON_VERSION.bat 脚本\n"
                        f"2. 或手动删除 backend/venv 文件夹\n"
                        f"3. 使用 Python 3.11 运行 SETUP.bat 重建环境"
                    )
                    return {"success": False, "error": error_msg}

                machine = platform.machine()
                is_macos = self.system == "Darwin"
                is_apple_silicon = is_macos and machine in {"arm64", "aarch64"}

                if is_macos and version == "cuda":
                    error_msg = (
                        "macOS 不支持 CUDA 版本 AI 工具\n\n"
                        "请使用 CPU 版本。\n"
                        "Apple Silicon（M1/M2/M3）可正常运行，但当前字幕识别使用 CPU 模式（Apple 芯片已做优化）。"
                    )
                    return {"success": False, "error": error_msg}

                logger.info(f"Installing AI tools ({version} version) with Python {python_version.major}.{python_version.minor}")

                # 获取正确的 Python 解释器路径
                python_exe = sys.executable
                if getattr(sys, 'frozen', False):
                    # 打包后环境：寻找嵌入式 Python（在 _internal/python 目录）
                    base_path = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(sys.executable).parent / '_internal'
                    # 根据平台选择正确的 Python 可执行文件名
                    python_name = 'python.exe' if sys.platform == 'win32' else 'python'
                    embedded_python = base_path / 'python' / python_name

                    if embedded_python.exists():
                        python_exe = str(embedded_python)
                        logger.info(f"[PyTorch Install] Found embedded Python: {python_exe}")
                    else:
                        return {"success": False, "error": f"未找到嵌入式 Python\n\n预期路径: {embedded_python}\n\n请重新安装应用"}

                logger.info(f"[PyTorch Install] Using Python: {python_exe}")

                previous_dir = AI_PACKAGES_DIR
                staging_dir = DATA_DIR / f"ai_packages_{int(time.time() * 1000)}_{os.getpid()}"
                try:
                    staging_dir.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    staging_dir = DATA_DIR / f"ai_packages_{time.time_ns()}_{os.getpid()}"
                    staging_dir.mkdir(parents=True, exist_ok=True)

                # 步骤 1: 卸载旧版本（如果存在）
                if progress_callback:
                    await progress_callback(5, "清理旧版本...")

                try:
                    process = await asyncio.create_subprocess_exec(
                        python_exe, '-m', 'pip', 'uninstall',
                        'torch', 'torchvision', 'torchaudio', '-y',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    # 设置超时 30 秒
                    try:
                        await asyncio.wait_for(process.communicate(), timeout=30)
                    except asyncio.TimeoutError:
                        process.kill()
                        logger.warning("Uninstall timeout, continuing...")
                except Exception as e:
                    logger.warning(f"Uninstall error (ignored): {e}")

                # 步骤 2: 安装 PyTorch
                if progress_callback:
                    await progress_callback(10, f"下载 PyTorch ({version} 版本)，请耐心等待...")

                common_pip_args = [
                    python_exe, '-m', 'pip', 'install',
                    '--target', str(staging_dir),
                    '--upgrade',
                    '--no-warn-script-location',
                    '--progress-bar', 'on',  # Enable progress bar to get download output
                    '--default-timeout=600',  # Increase timeout for large downloads
                    '-v'
                ]

                if version == "cpu":
                    # CPU 版本（推荐，体积小）
                    # macOS 使用官方 PyPI（国内镜像对大文件有限制）
                    # Windows/Linux 优先尝试镜像，失败后回退到官方源
                    if is_macos:
                        # macOS 直接使用官方源
                        torch_cmd = common_pip_args + [
                            'torch', 'torchvision', 'torchaudio',
                        ]
                    else:
                        # Windows/Linux 使用阿里云镜像
                        torch_cmd = common_pip_args + [
                            'torch', 'torchvision', 'torchaudio',
                            '-i', 'https://mirrors.aliyun.com/pypi/simple/',
                        ]
                else:
                    # CUDA 版本 - 使用阿里云镜像的 find-links 服务
                    # 注意：镜像最高到torch 2.5.1，必须指定版本号避免下载最新的CPU版本
                    torch_cmd = common_pip_args + [
                        'torch==2.5.1', 'torchvision==0.20.1', 'torchaudio==2.5.1',
                        # 使用阿里云镜像的PyTorch CUDA wheels（国内速度快）
                        '-f', 'https://mirrors.aliyun.com/pytorch-wheels/cu121/',
                        '--retries', '5',
                        '--no-cache-dir',  # 强制从源下载，避免使用缓存的旧版本
                    ]

                process = await asyncio.create_subprocess_exec(
                    *torch_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT  # 合并输出
                )

                # 创建安装日志文件
                install_log_path = DATA_DIR / f"pytorch_install_{version}_{int(time.time())}.log"
                logger.info(f"[PyTorch Install] Logging to: {install_log_path}")

                # 实时读取输出并更新进度
                output_lines = []
                current_progress = 10
                last_progress_time = asyncio.get_event_loop().time()

                async def read_output():
                    nonlocal current_progress, last_progress_time
                    # 打开日志文件用于实时写入
                    with open(install_log_path, 'w', encoding='utf-8') as log_file:
                        log_file.write(f"=== PyTorch {version} Installation Log ===\n")
                        log_file.write(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        log_file.write(f"Command: {' '.join(torch_cmd)}\n")
                        log_file.write("=" * 50 + "\n\n")
                        log_file.flush()

                        while True:
                            line = await process.stdout.readline()
                            if not line:
                                break

                            line_text = line.decode('utf-8', errors='ignore').strip()
                            if not line_text:  # 跳过空行
                                continue
                            last_progress_time = asyncio.get_event_loop().time()

                            output_lines.append(line_text)
                            logger.info(f"[PyTorch Install] {line_text}")

                            # 实时写入日志文件
                            log_file.write(line_text + '\n')
                            log_file.flush()  # 确保立即写入

                            # 根据输出更新进度 - 使用优先级检测（按阶段优先级）
                            lower_text = line_text.lower()
                            progress_updated = False

                            # 最高优先级：检测完成
                            if 'successfully installed' in lower_text:
                                current_progress = 60
                                if progress_callback:
                                    await progress_callback(60, "PyTorch 安装完成！")
                                last_progress_time = asyncio.get_event_loop().time()
                                progress_updated = True

                            # 次高优先级：检测安装和构建阶段（50% -> 60%）
                            elif any(keyword in lower_text for keyword in ['installing', 'building', 'processing', 'preparing']):
                                current_progress = min(max(current_progress, 50) + 2, 60)
                                if progress_callback:
                                    await progress_callback(current_progress, "安装 PyTorch...")
                                last_progress_time = asyncio.get_event_loop().time()
                                progress_updated = True

                            # 第三优先级：检测下载和收集阶段（10% -> 50%）
                            elif any(keyword in lower_text for keyword in ['downloading', 'obtaining', 'collecting', 'fetching', 'receiving']):
                                current_progress = min(current_progress + 2, 50)
                                if progress_callback:
                                    await progress_callback(current_progress, "下载 PyTorch 依赖包...")
                                last_progress_time = asyncio.get_event_loop().time()
                                progress_updated = True

                            # 最低优先级：检测下载进度指示器（通用兜底）
                            if not progress_updated and any(keyword in lower_text for keyword in ['kb', 'mb', 'gb']) and any(char.isdigit() for char in line_text):
                                # pip 显示文件大小时，缓慢递增进度，上限58%
                                if current_progress < 58:
                                    current_progress = min(current_progress + 1, 58)
                                    if progress_callback:
                                        await progress_callback(current_progress, "下载 PyTorch...")
                                    last_progress_time = asyncio.get_event_loop().time()

                        # 写入结束标记
                        log_file.write("\n" + "=" * 50 + "\n")
                        log_file.write(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        log_file.write(f"Exit code: {process.returncode}\n")

                # 心跳任务：每 10 秒发送一次进度更新，检测进度停滞
                async def heartbeat():
                    nonlocal current_progress, last_progress_time
                    heartbeat_count = 0
                    start_time = asyncio.get_event_loop().time()
                    last_output_time = last_progress_time
                    downloading_large_file = False

                    while process.returncode is None:
                        await asyncio.sleep(10)
                        heartbeat_count += 1
                        elapsed_time = int(asyncio.get_event_loop().time() - start_time)
                        current_time = asyncio.get_event_loop().time()
                        time_since_output = int(current_time - last_output_time)

                        if last_progress_time > last_output_time:
                            last_output_time = last_progress_time

                        # 检测是否正在下载大文件（检查最近的输出）
                        if output_lines and not downloading_large_file:
                            recent_lines = ' '.join(output_lines[-5:]).lower()
                            # 检测到正在下载超过1GB的文件
                            if 'downloading' in recent_lines and ('gb' in recent_lines or 'mb' in recent_lines):
                                # 提取文件大小
                                for line in output_lines[-5:]:
                                    if 'downloading' in line.lower():
                                        # 检查是否包含 GB 或超过500MB
                                        if 'gb' in line.lower() or ('mb' in line.lower() and any(str(i) in line for i in range(500, 10000))):
                                            downloading_large_file = True
                                            logger.info(f"[PyTorch Install] Detected large file download, extending timeout to 20 minutes")
                                            break

                        # CUDA 版本下载大文件（2-3GB）时可能长时间无输出
                        # 检测到大文件下载时使用更长超时：20分钟
                        # CPU 版本较小（300MB）使用较短超时：5分钟
                        # 其他CUDA操作：10分钟
                        if downloading_large_file:
                            no_output_timeout = 1200  # 20分钟
                        elif version == "cuda":
                            no_output_timeout = 600   # 10分钟
                        else:
                            no_output_timeout = 300   # 5分钟

                        if time_since_output >= no_output_timeout:
                            timeout_minutes = no_output_timeout // 60
                            logger.error(f"[PyTorch Install] No output for {timeout_minutes} minutes, killing process")
                            process.kill()
                            return

                        if progress_callback and current_progress < 60:
                            if downloading_large_file and time_since_output > 60:
                                await progress_callback(current_progress, f"正在下载大文件... (已等待{elapsed_time}s, {time_since_output}s无输出)")
                            else:
                                await progress_callback(current_progress, f"正在安装 PyTorch... (已等待{elapsed_time}s)")
                            logger.info(f"[PyTorch Install] Heartbeat #{heartbeat_count}: {current_progress}%, elapsed: {elapsed_time}s, no output: {time_since_output}s")
                        else:
                            logger.info(f"[PyTorch Install] Heartbeat #{heartbeat_count}: {current_progress}% (max), elapsed: {elapsed_time}s")

                # 设置超时：CPU 版 10 分钟，CUDA 版 30 分钟（增加以支持大文件下载）
                timeout_seconds = 1800 if version == "cuda" else 600
                try:
                    await asyncio.wait_for(
                        asyncio.gather(read_output(), heartbeat(), process.wait()),
                        timeout=timeout_seconds
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    timeout_minutes = timeout_seconds // 60
                    _enqueue_ai_cleanup(staging_dir)
                    logger.error(f"PyTorch installation timeout after {timeout_minutes} minutes")
                    return {
                        "success": False,
                        "error": f"PyTorch 安装超时（超过{timeout_minutes}分钟）\n\n可能原因：\n1. 网络速度太慢（{'CUDA版本约2-3GB' if version == 'cuda' else 'CPU版本约300MB'}）\n2. PyPI 或清华镜像服务器响应慢\n3. 防火墙或代理拦截\n\n建议：\n- 检查网络连接速度\n- 关闭 VPN 或代理重试\n- 更换时间段重试" + ("\n- 如果网络条件不好，可以尝试 CPU 版本（体积更小）" if version == "cuda" else "")
                    }

                if process.returncode != 0:
                    error_output = '\n'.join(output_lines[-20:])  # 最后 20 行
                    _enqueue_ai_cleanup(staging_dir)
                    logger.error(f"Failed to install PyTorch: {error_output}")
                    return {
                        "success": False,
                        "error": f"PyTorch 安装失败\n\n{error_output[:300]}"
                    }

                if progress_callback:
                    await progress_callback(65, "开始安装 faster-whisper...")

                # 步骤 3: 安装 faster-whisper 和依赖 - 使用清华镜像加速
                # 根据 CUDA 版本选择兼容的 ctranslate2 版本
                whisper_packages = ['faster-whisper', 'requests']

                if is_apple_silicon:
                    whisper_packages.append('ctranslate2>=3.20.0')

                # 检测 CUDA 版本（仅 CUDA 版本需要）
                if version == "cuda":
                    try:
                        # 检测 PyTorch 的 CUDA 版本
                        env = os.environ.copy()
                        env["PYTHONPATH"] = str(staging_dir) + (os.pathsep + env.get("PYTHONPATH", ""))

                        code = (
                            'import json, torch; '
                            'print(json.dumps({'
                            '"cuda_version": getattr(torch.version, "cuda", None)'
                            '}))'
                        )

                        process_check = await asyncio.create_subprocess_exec(
                            python_exe, '-c', code,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            env=env
                        )

                        stdout, _ = await asyncio.wait_for(process_check.communicate(), timeout=10)
                        data = json.loads(stdout.decode('utf-8', errors='ignore').strip() or '{}')
                        cuda_version = data.get("cuda_version")

                        if cuda_version:
                            cuda_major = int(str(cuda_version).split('.')[0])
                            logger.info(f"[Whisper Install] Detected CUDA {cuda_version}")

                            # ctranslate2 3.24.0 支持 CUDA 11.x
                            # ctranslate2 4.x 需要 CUDA 12.x
                            if cuda_major < 12:
                                logger.info(f"[Whisper Install] Using ctranslate2==3.24.0 for CUDA 11.x compatibility")
                                whisper_packages.append('ctranslate2==3.24.0')
                            else:
                                logger.info(f"[Whisper Install] Using latest ctranslate2 for CUDA 12.x")
                                # 不指定版本，使用最新的 ctranslate2
                    except Exception as e:
                        logger.warning(f"[Whisper Install] Failed to detect CUDA version: {e}, using default ctranslate2")

                # 根据平台选择镜像源
                if is_macos:
                    # macOS 使用官方 PyPI（镜像对大文件有限制）
                    index_url = []
                else:
                    # Windows/Linux 使用阿里云镜像加速
                    index_url = ['-i', 'https://mirrors.aliyun.com/pypi/simple/']

                whisper_cmd = [
                    python_exe, '-m', 'pip', 'install',
                    '--target', str(staging_dir),
                    '--upgrade',
                    *whisper_packages,
                    *index_url,
                    '--default-timeout=300',
                ]
                process = await asyncio.create_subprocess_exec(
                    *whisper_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )

                # 实时读取输出
                output_lines = []
                current_progress = 65
                last_whisper_progress_time = asyncio.get_event_loop().time()

                async def read_whisper_output():
                    nonlocal current_progress, last_whisper_progress_time
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break

                        line_text = line.decode('utf-8', errors='ignore').strip()
                        output_lines.append(line_text)
                        logger.info(f"[Whisper Install] {line_text}")

                        if 'Downloading' in line_text or 'Obtaining' in line_text:
                            current_progress = min(current_progress + 3, 85)
                            if progress_callback:
                                await progress_callback(current_progress, "下载 faster-whisper...")
                            last_whisper_progress_time = asyncio.get_event_loop().time()
                        elif 'Installing' in line_text:
                            current_progress = min(current_progress + 3, 95)
                            if progress_callback:
                                await progress_callback(current_progress, "安装 faster-whisper...")
                            last_whisper_progress_time = asyncio.get_event_loop().time()
                        elif 'Successfully installed' in line_text:
                            if progress_callback:
                                await progress_callback(98, "finalizing...")
                            last_whisper_progress_time = asyncio.get_event_loop().time()

                # 心跳任务：每 10 秒发送一次进度更新
                async def whisper_heartbeat():
                    nonlocal current_progress, last_whisper_progress_time
                    while process.returncode is None:
                        await asyncio.sleep(10)
                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_whisper_progress_time >= 10:
                            if progress_callback and current_progress < 98:
                                await progress_callback(current_progress, f"正在安装 faster-whisper... ({current_progress}%)")
                                logger.info(f"[Whisper Install] Heartbeat: {current_progress}%")

                try:
                    await asyncio.wait_for(
                        asyncio.gather(read_whisper_output(), whisper_heartbeat(), process.wait()),
                        timeout=600
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    _enqueue_ai_cleanup(staging_dir)
                    logger.error("faster-whisper installation timeout")
                    return {
                        "success": False,
                        "error": "faster-whisper 安装超时\n请检查网络连接后重试"
                    }

                if process.returncode != 0:
                    error_output = '\n'.join(output_lines[-20:])
                    _enqueue_ai_cleanup(staging_dir)
                    logger.error(f"Failed to install faster-whisper: {error_output}")

                    # 检查是否是依赖编译问题
                    if 'av' in error_output.lower() or 'cython' in error_output.lower():
                        return {
                            "success": False,
                            "error": (
                                f"依赖包编译失败\n"
                                f"可能原因:\n"
                                f"1. Python 版本不兼容\n"
                                f"2. 缺少 C++ 编译工具\n\n"
                                f"建议: 使用 Python 3.11 重建环境"
                            )
                        }

                    return {
                        "success": False,
                        "error": f"faster-whisper 安装失败\n\n{error_output[:300]}"
                    }

                _set_ai_packages_dir(staging_dir)
                if str(staging_dir) not in sys.path:
                    sys.path.insert(0, str(staging_dir))
                if previous_dir and previous_dir.exists() and previous_dir != staging_dir:
                    _enqueue_ai_cleanup(previous_dir)

                # 注意：不再删除 AI 包目录中的 numpy
                # 新策略是在模块加载时预先导入内置 numpy，避免冲突

                if progress_callback:
                    await progress_callback(100, "安装完成！")

                logger.info("AI tools installed successfully")
                return {
                    "success": True,
                    "message": f"AI 工具安装成功 ({version} 版本)"
                }

            except Exception as e:
                logger.error(f"Error installing AI tools: {e}")
                try:
                    if 'staging_dir' in locals():
                        _enqueue_ai_cleanup(staging_dir)
                except Exception:
                    pass
                import traceback
                traceback.print_exc()
                return {
                    "success": False,
                    "error": f"安装出错: {str(e)}"
                }

    async def uninstall_ai_tools(self, progress_callback=None) -> dict:
        """卸载 AI 工具"""
        async with AI_TOOLS_LOCK:
            try:
                logger.info("Uninstalling AI tools...")

                old_dir = AI_PACKAGES_DIR
                if progress_callback:
                    await progress_callback(0, "开始卸载 AI 工具...")

                new_dir = DATA_DIR / f"ai_packages_{int(time.time() * 1000)}_{os.getpid()}"
                try:
                    new_dir.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    new_dir = DATA_DIR / f"ai_packages_{time.time_ns()}_{os.getpid()}"
                    new_dir.mkdir(parents=True, exist_ok=True)

                _set_ai_packages_dir(new_dir)
                if progress_callback:
                    await progress_callback(50, "已切换环境，正在清理旧文件...")

                cleanup_scheduled = False
                if old_dir and old_dir.exists() and old_dir != new_dir:
                    try:
                        await asyncio.to_thread(shutil.rmtree, old_dir)
                    except Exception as e:
                        logger.info(f"[Uninstall] Deferred cleanup for {old_dir}: {e}")
                        _enqueue_ai_cleanup(old_dir)
                        cleanup_scheduled = True

                if progress_callback:
                    await progress_callback(100, "卸载完成")

                logger.info("AI tools uninstalled successfully")
                return {
                    "success": True,
                    "message": "AI 工具已卸载" + ("（部分文件将在下次启动时继续清理）" if cleanup_scheduled else "")
                }

            except Exception as e:
                logger.error(f"Error uninstalling AI tools: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }

    def get_ai_tool_info(self, version: str = "cpu") -> dict:
        """获取 AI 工具下载信息"""
        if version == "cpu":
            return {
                "name": "AI 字幕生成 (CPU 版本)",
                "description": "使用 faster-whisper 进行语音识别（CPU 版本，兼容所有机器）",
                "download_size": "约 300 MB",
                "install_time": "3-5 分钟",
                "compatible": "所有机器"
            }
        else:
            return {
                "name": "AI 字幕生成 (GPU 版本)",
                "description": "使用 faster-whisper 进行语音识别，GPU 加速",
                "download_size": "约 1 GB",
                "install_time": "5-10 分钟",
                "compatible": "需要 NVIDIA 显卡"
            }

    # ==================== Playwright 安装支持 ====================

    def invalidate_playwright_status_cache(self):
        self._playwright_status_cache = None
        self._playwright_status_cache_time = 0.0

    async def check_playwright_status(self, force_refresh: bool = False) -> dict:
        """
        检查 Playwright 和 Chromium 浏览器的安装状态

        Args:
            force_refresh: 是否忽略缓存强制重新扫描
        """
        now = time.monotonic()
        cache_valid = (
            not force_refresh
            and self._playwright_status_cache is not None
            and now - self._playwright_status_cache_time < self.PLAYWRIGHT_STATUS_CACHE_TTL
        )
        if cache_valid:
            return dict(self._playwright_status_cache)

        async with self._playwright_status_lock:
            now = time.monotonic()
            cache_valid = (
                not force_refresh
                and self._playwright_status_cache is not None
                and now - self._playwright_status_cache_time < self.PLAYWRIGHT_STATUS_CACHE_TTL
            )
            if cache_valid:
                return dict(self._playwright_status_cache)

            result = await self._check_playwright_status_uncached()
            self._playwright_status_cache = dict(result)
            self._playwright_status_cache_time = time.monotonic()
            return dict(result)

    async def _check_playwright_status_uncached(self) -> dict:
        """
        检查 Playwright 和 Chromium 浏览器的安装状态

        Returns:
            dict: {
                "installed": bool,  # 是否完全安装（包+浏览器）
                "package_installed": bool,  # playwright 包是否安装
                "browser_installed": bool,  # Chromium 浏览器是否安装
                "version": str | None,  # playwright 版本
                "browser_path": str | None,  # Chromium 路径
                "error": str | None  # 错误信息
            }
        """
        result = {
            "installed": False,
            "package_installed": False,
            "browser_installed": False,
            "version": None,
            "browser_path": None,
            "error": None
        }

        try:
            # 检查 playwright 包是否安装
            try:
                from importlib.metadata import version as get_version, PackageNotFoundError
            except ImportError:
                from importlib_metadata import version as get_version, PackageNotFoundError

            try:
                pw_version = get_version("playwright")
                result["package_installed"] = True
                result["version"] = pw_version
                logger.debug(f"[Playwright] Package installed: v{pw_version}")
            except PackageNotFoundError:
                result["package_installed"] = False
                logger.debug("[Playwright] Package not installed")
                return result

            # 检查 Chromium 浏览器是否安装
            # 需要检查多个可能的位置
            try:
                # 可能的浏览器路径列表
                possible_browser_paths = []

                # 1. 用户目录（标准安装位置）
                if sys.platform == 'win32':
                    user_pw_path = Path.home() / 'AppData' / 'Local' / 'ms-playwright'
                elif sys.platform == 'darwin':
                    user_pw_path = Path.home() / 'Library' / 'Caches' / 'ms-playwright'
                else:
                    user_pw_path = Path.home() / '.cache' / 'ms-playwright'
                possible_browser_paths.append(user_pw_path)

                # 2. 打包环境中 Playwright 的 .local-browsers 目录
                if getattr(sys, 'frozen', False):
                    if hasattr(sys, '_MEIPASS'):
                        internal_path = Path(sys._MEIPASS)
                    else:
                        internal_path = Path(sys.executable).parent / '_internal'

                    playwright_local = internal_path / 'playwright' / 'driver' / 'package' / '.local-browsers'
                    possible_browser_paths.append(playwright_local)

                logger.debug(f"[Playwright] Checking browser paths: {possible_browser_paths}")

                # 检查每个可能的路径
                for pw_browsers_path in possible_browser_paths:
                    if not pw_browsers_path.exists():
                        continue

                    # 查找 chromium_headless_shell 或 chromium 目录
                    # 优先检查 chromium_headless_shell，因为这是我们默认安装的（更小）
                    chromium_patterns = ['chromium_headless_shell-*', 'chromium-*']
                    for pattern in chromium_patterns:
                        chromium_dirs = list(pw_browsers_path.glob(pattern))
                        if chromium_dirs:
                            chromium_dir = sorted(chromium_dirs, reverse=True)[0]

                            # 查找可执行文件 - 支持多种目录结构
                            chrome_exe = None
                            if sys.platform == 'win32':
                                possible_exes = [
                                    chromium_dir / 'chrome-headless-shell-win64' / 'chrome-headless-shell.exe',
                                    chromium_dir / 'chrome-win64' / 'chrome.exe',
                                    chromium_dir / 'chrome-win' / 'chrome.exe',
                                ]
                            elif sys.platform == 'darwin':
                                possible_exes = [
                                    chromium_dir / 'chrome-headless-shell-mac-arm64' / 'chrome-headless-shell',
                                    chromium_dir / 'chrome-headless-shell-mac' / 'chrome-headless-shell',
                                    chromium_dir / 'chrome-mac' / 'Chromium.app' / 'Contents' / 'MacOS' / 'Chromium',
                                    chromium_dir / 'chrome-mac-arm64' / 'Chromium.app' / 'Contents' / 'MacOS' / 'Chromium',
                                ]
                            else:
                                possible_exes = [
                                    chromium_dir / 'chrome-headless-shell-linux64' / 'chrome-headless-shell',
                                    chromium_dir / 'chrome-headless-shell-linux' / 'chrome-headless-shell',
                                    chromium_dir / 'chrome-linux64' / 'chrome',
                                    chromium_dir / 'chrome-linux' / 'chrome',
                                ]

                            for exe_path in possible_exes:
                                if exe_path.exists():
                                    chrome_exe = exe_path
                                    break

                            if chrome_exe:
                                result["browser_installed"] = True
                                result["browser_path"] = str(chrome_exe)
                                result["installed"] = True
                                logger.debug(f"[Playwright] Chromium installed: {chrome_exe}")
                                return result

                # 没有找到浏览器
                result["browser_installed"] = False
                logger.debug("[Playwright] No chromium browser found in any location")

            except Exception as e:
                result["browser_installed"] = False
                result["error"] = f"浏览器检查失败: {str(e)}"
                logger.warning(f"[Playwright] Browser check failed: {e}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[Playwright] Status check failed: {e}")

        return result

    def _get_python_executable_for_pip(self) -> str:
        """
        获取用于 pip 安装的 Python 可执行文件路径

        Returns:
            str: Python 可执行文件路径

        Raises:
            RuntimeError: 如果在打包环境中找不到嵌入式 Python
        """
        if getattr(sys, 'frozen', False):
            # 打包后环境：寻找嵌入式 Python
            # PyInstaller 打包后的目录结构：
            # - VidFlow-Backend.exe
            # - _internal/ (sys._MEIPASS 指向这里)
            #   - python/
            #     - python.exe

            python_name = 'python.exe' if sys.platform == 'win32' else 'python'

            # 可能的嵌入式 Python 路径
            possible_paths = []

            # 1. _MEIPASS/python (PyInstaller 解压目录)
            if hasattr(sys, '_MEIPASS'):
                possible_paths.append(Path(sys._MEIPASS) / 'python' / python_name)

            # 2. exe 同级目录的 _internal/python
            exe_dir = Path(sys.executable).parent
            possible_paths.append(exe_dir / '_internal' / 'python' / python_name)

            # 3. exe 同级目录的 python
            possible_paths.append(exe_dir / 'python' / python_name)

            for path in possible_paths:
                if path.exists():
                    logger.info(f"[Playwright] Using embedded Python: {path}")
                    return str(path)

            # 如果都找不到，抛出错误
            searched_paths = '\n'.join(str(p) for p in possible_paths)
            error_msg = f"未找到嵌入式 Python\n\n已搜索路径:\n{searched_paths}\n\n请确保 python_embedded 目录已正确打包"
            logger.error(f"[Playwright] {error_msg}")
            raise RuntimeError(error_msg)
        else:
            # 开发环境
            return sys.executable

    async def _call_progress_callback(self, callback, percent: int, message: str):
        """调用进度回调，支持同步和异步回调函数"""
        if callback is None:
            return
        try:
            result = callback(percent, message)
            # 如果返回的是协程，则 await 它
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning(f"[Playwright] Progress callback error: {e}")

    async def install_playwright(self, progress_callback=None) -> dict:
        """
        安装 Playwright 和 Chromium 浏览器

        Args:
            progress_callback: 进度回调函数 callback(percent, message)，支持同步或异步函数

        Returns:
            dict: {
                "success": bool,
                "message": str,
                "version": str | None,
                "error": str | None
            }
        """
        try:
            # 检查是否已安装
            status = await self.check_playwright_status(force_refresh=True)
            if status["installed"]:
                logger.info("[Playwright] Already installed, skipping")
                return {
                    "success": True,
                    "message": f"Playwright 已安装 (v{status['version']})",
                    "version": status["version"],
                    "already_installed": True
                }

            try:
                python_exe = self._get_python_executable_for_pip()
            except RuntimeError as e:
                return {
                    "success": False,
                    "error": f"无法安装 Playwright: {str(e)}\n\n请重新安装应用程序"
                }

            logger.info(f"[Playwright] Using Python: {python_exe}")

            # 确定安装目标目录
            if getattr(sys, 'frozen', False):
                target_dir = AI_PACKAGES_DIR
                pip_target_args = ['--target', str(target_dir)]
                logger.info(f"[Playwright] Installing to: {target_dir}")
            else:
                target_dir = None
                pip_target_args = []
                logger.info("[Playwright] Installing to system Python")

            # 步骤 1: 安装 playwright 包 (0% - 50%)
            await self._call_progress_callback(progress_callback, 0, "开始安装 Playwright...")

            if not status["package_installed"]:
                await self._call_progress_callback(progress_callback, 5, "下载 Playwright 包...")

                pip_cmd = [
                    python_exe, '-m', 'pip', 'install',
                    *pip_target_args,
                    'playwright',
                    '--upgrade',
                    '--no-warn-script-location',
                    '-i', 'https://mirrors.aliyun.com/pypi/simple/',
                    '--default-timeout=300',
                ]

                logger.info(f"[Playwright] Running: {' '.join(pip_cmd)}")

                process = await asyncio.create_subprocess_exec(
                    *pip_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )

                output_lines = []
                current_progress = 5

                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break

                    line_text = line.decode('utf-8', errors='ignore').strip()
                    if line_text:
                        output_lines.append(line_text)
                        logger.info(f"[Playwright pip] {line_text}")

                        # 更新进度
                        lower_text = line_text.lower()
                        if 'downloading' in lower_text or 'collecting' in lower_text:
                            current_progress = min(current_progress + 5, 40)
                        elif 'installing' in lower_text:
                            current_progress = min(current_progress + 5, 45)
                        elif 'successfully installed' in lower_text:
                            current_progress = 50

                        await self._call_progress_callback(progress_callback, current_progress, "安装 Playwright 包...")

                await process.wait()

                if process.returncode != 0:
                    error_output = '\n'.join(output_lines[-10:])
                    logger.error(f"[Playwright] pip install failed: {error_output}")
                    return {
                        "success": False,
                        "error": f"Playwright 包安装失败\n\n{error_output[:300]}"
                    }

                logger.info("[Playwright] Package installed successfully")
            else:
                logger.info("[Playwright] Package already installed, skipping pip install")
                await self._call_progress_callback(progress_callback, 50, "Playwright 包已安装")

            # 步骤 2: 安装 Chromium 浏览器 (50% - 100%)
            await self._call_progress_callback(progress_callback, 50, "开始下载 Chromium 浏览器...")

            # 构建 playwright install 命令
            # 注意：Playwright 1.49+ 默认使用 chromium-headless-shell 进行 headless 模式
            # 我们安装 chromium-headless-shell 因为它更小（~50MB vs ~200MB）且足够用于抓取
            if getattr(sys, 'frozen', False) and target_dir:
                # 打包环境：需要设置 PYTHONPATH
                env = os.environ.copy()
                env["PYTHONPATH"] = str(target_dir) + (os.pathsep + env.get("PYTHONPATH", ""))
                playwright_cmd = [python_exe, '-m', 'playwright', 'install', 'chromium-headless-shell']
            else:
                env = None
                playwright_cmd = [python_exe, '-m', 'playwright', 'install', 'chromium-headless-shell']

            logger.info(f"[Playwright] Running: {' '.join(playwright_cmd)}")

            process = await asyncio.create_subprocess_exec(
                *playwright_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )

            output_lines = []
            current_progress = 50

            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_text = line.decode('utf-8', errors='ignore').strip()
                if line_text:
                    output_lines.append(line_text)
                    logger.info(f"[Playwright browser] {line_text}")

                    # 解析下载进度
                    lower_text = line_text.lower()
                    if 'downloading' in lower_text or '%' in line_text:
                        # 尝试提取百分比
                        import re
                        match = re.search(r'(\d+)%', line_text)
                        if match:
                            browser_progress = int(match.group(1))
                            # 映射到 50-95 范围
                            current_progress = 50 + int(browser_progress * 0.45)
                        else:
                            current_progress = min(current_progress + 2, 90)
                    elif 'chromium' in lower_text and ('installed' in lower_text or 'downloaded' in lower_text):
                        current_progress = 95

                    await self._call_progress_callback(progress_callback, current_progress, "下载 Chromium 浏览器...")

            await process.wait()

            if process.returncode != 0:
                error_output = '\n'.join(output_lines[-10:])
                logger.error(f"[Playwright] Browser install failed: {error_output}")

                # 检查常见错误
                error_lower = error_output.lower()
                if 'disk' in error_lower or 'space' in error_lower:
                    return {
                        "success": False,
                        "error": "磁盘空间不足，Chromium 浏览器需要约 200MB 空间"
                    }
                elif 'network' in error_lower or 'timeout' in error_lower or 'connection' in error_lower:
                    return {
                        "success": False,
                        "error": "网络连接超时，请检查网络后重试"
                    }

                return {
                    "success": False,
                    "error": f"Chromium 浏览器安装失败\n\n{error_output[:300]}"
                }

            await self._call_progress_callback(progress_callback, 100, "Playwright 安装完成！")

            # 验证安装
            self.invalidate_playwright_status_cache()
            final_status = await self.check_playwright_status(force_refresh=True)
            if final_status["installed"]:
                logger.info(f"[Playwright] Installation completed: v{final_status['version']}")
                return {
                    "success": True,
                    "message": f"Playwright 安装成功 (v{final_status['version']})",
                    "version": final_status["version"]
                }
            else:
                logger.error("[Playwright] Installation verification failed")
                return {
                    "success": False,
                    "error": "安装验证失败，请重试"
                }

        except Exception as e:
            logger.error(f"[Playwright] Installation error: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"安装出错: {str(e)}"
            }

    # 保留旧方法以兼容现有代码
    async def check_faster_whisper(self) -> bool:
        """检查 faster-whisper 是否可用（兼容旧代码）"""
        status = await self.check_ai_tools_status()
        return status.get("installed", False)

    async def install_faster_whisper(self) -> bool:
        """安装 faster-whisper（兼容旧代码）"""
        result = await self.install_ai_tools(version="cpu")
        return result.get("success", False)

    async def download_whisper_model(self, model_name: str = "base") -> Path:
        """下载 Whisper 模型（首次使用时自动下载）"""
        # faster-whisper 会自动下载模型到缓存目录
        # 这里只是返回模型缓存路径
        model_dir = MODELS_DIR / "whisper"
        model_dir.mkdir(parents=True, exist_ok=True)
        return model_dir

# 全局工具管理器实例
tool_manager = ToolManager()

async def initialize_tools():
    """初始化所有工具"""
    logger.info("Initializing tools...")

    # 清理旧的临时文件（仅执行一次）
    if not tool_manager._cleanup_done:
        try:
            await tool_manager._cleanup_old_temp_files()
            tool_manager._cleanup_done = True
        except Exception as e:
            logger.warning(f"Failed to cleanup old temp files: {e}")

    results = await tool_manager.setup_all_tools()

    for tool, result in results.items():
        if result['success']:
            logger.info(f"[OK] {tool}: {result['path']}")
        else:
            logger.warning(f"[FAIL] {tool}: {result.get('error', 'Failed')}")

    # 启动自动更新任务（可通过环境变量关闭）
    if tool_manager.auto_update_enabled:
        interval = max(1, tool_manager.auto_update_interval_hours)
        logger.info(f"[Tools] Auto-update enabled, interval: {interval}h")

        # 启动后台自动更新循环
        # 注意：run_auto_update_loop 内部已有延迟和首次检查逻辑，无需额外的 _background_initial_update
        try:
            if tool_manager._auto_update_task is None or tool_manager._auto_update_task.done():
                tool_manager._auto_update_task = asyncio.create_task(tool_manager.run_auto_update_loop(interval))
        except Exception as e:
            logger.error(f"[Tools] Failed to start auto-update loop: {e}")
    else:
        logger.info("[Tools] Auto-update disabled by TOOLS_AUTO_UPDATE_ENABLED")

    return results

def get_tool_manager() -> ToolManager:
    """获取工具管理器实例"""
    return tool_manager
