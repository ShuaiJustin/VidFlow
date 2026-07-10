"""
系统相关 API
"""
import os
import platform
import psutil
import shutil
import subprocess
import logging
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, BackgroundTasks, Body, Response, Request
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import get_session
from src.core.cookie_storage import read_cookie_file, write_cookie_file, validate_netscape_cookie_format, clean_cookie_content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])

def get_base_dir() -> Path:
    """获取应用数据目录（与 main.py 保持一致）"""
    import sys
    if getattr(sys, 'frozen', False):
        # 打包后：使用用户数据目录
        if sys.platform == 'win32':
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            return Path(appdata) / 'VidFlow'
        elif sys.platform == 'darwin':
            return Path.home() / 'Library' / 'Application Support' / 'VidFlow'
        else:
            return Path.home() / '.local' / 'share' / 'VidFlow'
    else:
        # 开发环境
        return Path(__file__).parent.parent.parent

# 应用启动时间
START_TIME = datetime.now()

# AI工具状态缓存（避免频繁导入torch导致慢查询）
_ai_status_cache: Optional[Dict[str, Any]] = None
_ai_status_cache_time: Optional[datetime] = None
AI_STATUS_CACHE_TTL = timedelta(minutes=2)  # 缓存2分钟

def _preload_ai_status_cache():
    """
    后端启动时预热AI状态缓存
    避免首次请求时导入torch导致慢查询（可能需要20+秒）
    """
    global _ai_status_cache, _ai_status_cache_time

    logger.info("[AI Status] 开始预热缓存...")
    try:
        # 预热逻辑需要与 /api/v1/system/tools/ai/status 的返回结构保持一致
        # 否则前端会因为字段缺失/类型不匹配而出错。
        from importlib.metadata import version as get_version, PackageNotFoundError

        result: Dict[str, Any] = {
            "installed": False,
            "faster_whisper": False,
            "torch": False,
            "version": None,
            "torch_version": None,
            "device": "unknown",
            "python_compatible": True,
        }

        try:
            result["version"] = get_version("faster-whisper")
            result["faster_whisper"] = True
        except PackageNotFoundError:
            result["faster_whisper"] = False

        try:
            result["torch_version"] = get_version("torch")
            result["torch"] = True
        except PackageNotFoundError:
            result["torch"] = False

        if result["torch"]:
            try:
                import torch  # type: ignore[import-not-found]
                if torch.cuda.is_available():
                    result["device"] = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    result["device"] = "mps"
                else:
                    result["device"] = "cpu"
            except Exception as e:
                logger.warning(f"[AI Status] torch preload failed, defaulting to CPU: {e}")
                result["device"] = "cpu"

        result["installed"] = bool(result["faster_whisper"] and result["torch"])

        _ai_status_cache = result
        _ai_status_cache_time = datetime.now()
        logger.info(f"[AI Status] 缓存预热完成: {_ai_status_cache}")
    except Exception as e:
        # 其他错误
        _ai_status_cache = {
            "installed": False,
            "faster_whisper": False,
            "torch": False,
            "version": None,
            "torch_version": None,
            "device": "unknown",
            "python_compatible": True,
            "error": str(e),
        }
        _ai_status_cache_time = datetime.now()
        logger.warning(f"[AI Status] 缓存预热失败: {e}")

def _get_ai_status_snapshot() -> Dict[str, Any]:
    if _ai_status_cache is not None:
        return dict(_ai_status_cache)

    return {
        "installed": False,
        "faster_whisper": False,
        "torch": False,
        "version": None,
        "torch_version": None,
        "device": "unknown",
        "python_compatible": True,
    }


_ALLOWED_OPEN_FOLDER_BASE = os.environ.get("VIDFLOW_ALLOWED_OPEN_BASE")

def _handle_task_exception(task: asyncio.Task):
    """记录后台任务异常，避免静默失败。"""
    try:
        exc = task.exception()
        if exc:
            logger.error(f"Background task failed: {exc}", exc_info=exc)
    except asyncio.CancelledError:
        logger.info("Background task was cancelled")
    except Exception as e:
        logger.error(f"Error handling task exception: {e}", exc_info=True)


@router.get("/health")
async def health_check():
    """基础健康检查"""
    return {
        "status": "healthy",
        "backend": "ready",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/health/full")
async def full_health_check():
    """
    深度健康检查：
    - FFmpeg / yt-dlp 可用性
    - data 目录写入权限
    """
    from src.core.tool_manager import get_tool_manager

    tool_mgr = get_tool_manager()
    ffmpeg_path = tool_mgr.get_ffmpeg_path()
    ytdlp_path = tool_mgr.get_ytdlp_path()

    data_dir = Path(__file__).parent.parent.parent / "data"
    temp_file = data_dir / "healthcheck.tmp"
    write_ok = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        temp_file.write_text("ok", encoding="utf-8")
        write_ok = True
    except Exception as e:
        logger.error(f"Health check write failed: {e}")
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

    healthy = bool(ffmpeg_path and ytdlp_path and write_ok)
    return {
        "status": "healthy" if healthy else "degraded",
        "ffmpeg": str(ffmpeg_path) if ffmpeg_path else None,
        "ytdlp": str(ytdlp_path) if ytdlp_path else None,
        "write_ok": write_ok,
        "timestamp": datetime.now().isoformat()
    }


def _get_system_directories() -> list[Path]:
    """根据平台返回敏感系统目录列表。"""
    system_dirs: list[Path] = []
    system = platform.system()

    if system == "Windows":
        env_root = os.environ.get("SystemRoot", r"C:\Windows")
        env_pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        env_pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        env_pgdata = os.environ.get("ProgramData", r"C:\ProgramData")
        system_dirs.extend([
            Path(r"C:\\"),
            Path(env_root),
            Path(env_pf),
            Path(env_pf86),
            Path(env_pgdata),
            Path(r"C:\Users\All Users"),
        ])
    elif system == "Darwin":
        system_dirs.extend([
            Path("/System"),
            Path("/Library"),
            Path("/private"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
        ])
    else:  # Linux/others
        system_dirs.extend([
            Path("/"),
            Path("/root"),
            Path("/etc"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/boot"),
            Path("/sys"),
            Path("/proc"),
        ])

    return system_dirs


def _validate_folder_path(folder_path: str) -> Path:
    """Validate folder path before opening it."""
    if not folder_path or not isinstance(folder_path, str):
        raise HTTPException(status_code=400, detail="路径不能为空")

    try:
        path = Path(folder_path).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="无效的路径")

    if not path.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="不是有效的文件夹")

    if _ALLOWED_OPEN_FOLDER_BASE:
        try:
            allowed_base = Path(_ALLOWED_OPEN_FOLDER_BASE).expanduser().resolve()
            path.relative_to(allowed_base)
        except ValueError:
            raise HTTPException(status_code=403, detail="路径不在允许的范围内")
        except Exception:
            raise HTTPException(status_code=500, detail="路径校验失败")
    else:
        # 当未配置允许目录时，避免访问敏感系统目录
        for sys_dir in _get_system_directories():
            try:
                resolved_sys = sys_dir.expanduser().resolve()
            except Exception:
                continue
            # 只阻止系统目录本身，不阻止其子目录
            if path == resolved_sys:
                raise HTTPException(status_code=403, detail="不允许打开系统目录")

    return path

class ToolStatus(BaseModel):
    id: str
    name: str
    description: str
    installed: bool
    version: Optional[str] = None
    path: Optional[str] = None
    required: bool
    official_url: str
    compatible: Optional[bool] = True
    incompatible_reason: Optional[str] = None
    bundled: bool = False  # 是否为应用内置工具
    updating: bool = False  # 是否正在更新

class SystemInfo(BaseModel):
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    network_speed: Dict[str, float]
    active_tasks: int
    queue_size: int
    total_downloads: int
    backend_status: str
    uptime: str

@router.get("/info", response_model=SystemInfo)
async def get_system_info():
    """获取系统信息"""
    try:
        # CPU 使用率
        cpu_usage = psutil.cpu_percent(interval=0.1)

        # 内存使用率
        memory = psutil.virtual_memory()
        memory_usage = memory.percent

        # 磁盘使用率
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent

        # 网络速度（简化版，实际需要持续监控）
        network_speed = {
            "download": 0,
            "upload": 0
        }

        # 运行时长
        uptime_seconds = (datetime.now() - START_TIME).total_seconds()
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime = f"{hours}h {minutes}m"

        return SystemInfo(
            cpu_usage=round(cpu_usage, 1),
            memory_usage=round(memory_usage, 1),
            disk_usage=round(disk_usage, 1),
            network_speed=network_speed,
            active_tasks=0,  # 从数据库获取
            queue_size=0,    # 从数据库获取
            total_downloads=0,  # 从数据库获取
            backend_status="online",
            uptime=uptime
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

_GITHUB_VERSION_CACHE: Dict[str, Dict[str, Any]] = {}
_GITHUB_VERSION_CACHE_LOCK: Optional[asyncio.Lock] = None
_GITHUB_VERSION_CACHE_TTL_SECONDS = 600
_GITHUB_VERSION_CACHE_ERROR_TTL_SECONDS = 60

async def _get_github_version(repo: str, timeout: float = 5.0) -> Optional[str]:
    """
    从 GitHub API 获取最新版本号

    Args:
        repo: GitHub 仓库，格式为 "owner/repo"（如 "yt-dlp/yt-dlp"）
        timeout: 超时时间

    Returns:
        版本字符串或 None
    """
    global _GITHUB_VERSION_CACHE_LOCK

    if _GITHUB_VERSION_CACHE_LOCK is None:
        _GITHUB_VERSION_CACHE_LOCK = asyncio.Lock()

    loop = asyncio.get_running_loop()
    now = loop.time()

    try:
        async with _GITHUB_VERSION_CACHE_LOCK:
            cached = _GITHUB_VERSION_CACHE.get(repo)
            if cached:
                expires_at = cached.get("expires_at", 0)
                if expires_at and expires_at > now:
                    return cached.get("version")
    except Exception:
        pass

    version: Optional[str] = None
    try:
        import aiohttp

        url = f"https://api.github.com/repos/{repo}/releases/latest"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url, headers={"User-Agent": "VidFlow"}) as response:
                if response.status == 200:
                    data = await response.json()
                    tag_name = data.get('tag_name', '')
                    version = tag_name.lstrip('v')
                    logger.info(f"[Version Check] GitHub {repo}: {version}")
                else:
                    logger.warning(f"[Version Check] GitHub {repo}: HTTP {response.status}")
    except asyncio.TimeoutError:
        logger.warning(f"[Version Check] GitHub {repo}: timeout")
    except Exception as e:
        logger.debug(f"[Version Check] GitHub {repo}: {type(e).__name__}: {e}")

    try:
        ttl = _GITHUB_VERSION_CACHE_TTL_SECONDS if version else _GITHUB_VERSION_CACHE_ERROR_TTL_SECONDS
        cached_at = loop.time()
        async with _GITHUB_VERSION_CACHE_LOCK:
            _GITHUB_VERSION_CACHE[repo] = {
                "version": version,
                "expires_at": cached_at + ttl,
            }
    except Exception:
        pass

    return version

# 工具版本缓存: { tool_path: (version_str, timestamp) }
_version_cache: dict[str, tuple[Optional[str], float]] = {}
_VERSION_CACHE_TTL = 60.0  # 缓存 60 秒


async def _get_tool_version(tool_path: str, version_arg: str, parse_fn=None, timeout: float = 5.0) -> Optional[str]:
    """
    通用工具版本检查函数（支持并行执行，带 60s TTL 缓存）

    Args:
        tool_path: 工具路径
        version_arg: 版本查询参数（如 "-version" 或 "--version"）
        parse_fn: 可选的版本解析函数
        timeout: 进程执行超时（秒）

    Returns:
        版本字符串或 None
    """
    import asyncio
    import os
    import time as _time

    # 命中缓存则直接返回
    cached = _version_cache.get(tool_path)
    if cached is not None:
        cached_version, cached_at = cached
        if _time.monotonic() - cached_at < _VERSION_CACHE_TTL:
            return cached_version

    try:
        if not os.path.exists(tool_path):
            logger.warning(f"[Version Check] Tool not found: {tool_path}")
            _version_cache[tool_path] = (None, _time.monotonic())
            return None

        logger.debug(f"[Version Check] Getting version for: {tool_path} {version_arg}")

        process = await asyncio.create_subprocess_exec(
            str(tool_path), version_arg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            output_raw = stdout or stderr
            if output_raw:
                output = output_raw.decode('utf-8', errors='ignore').strip()
                if output:
                    logger.debug(f"[Version Check] Raw output: {output[:100]}")
                    result = parse_fn(output) if parse_fn else output[:50]
                    logger.info(f"[Version Check] {tool_path}: {result}")
                    _version_cache[tool_path] = (result, _time.monotonic())
                    return result

            logger.warning(f"[Version Check] {tool_path} {version_arg}: no output (returncode={process.returncode})")
        except asyncio.TimeoutError:
            logger.warning(f"[Version Check] {tool_path} {version_arg}: timeout after {timeout}s")
            try:
                process.kill()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[Version Check] {tool_path} {version_arg}: {type(e).__name__}: {e}")

    # 超时或失败也缓存，避免反复重试
    _version_cache[tool_path] = (None, _time.monotonic())
    return None

@router.get("/tools/status", response_model=List[ToolStatus])
async def check_tools_status():
    """检查工具安装状态"""
    from src.core.tool_manager import BUNDLED_BIN_DIR, get_tool_manager
    import asyncio

    tools = []
    tool_mgr = get_tool_manager()

    # 获取工具路径
    ffmpeg_path = tool_mgr.get_ffmpeg_path() or shutil.which("ffmpeg")
    ytdlp_path = tool_mgr.get_ytdlp_path() or shutil.which("yt-dlp")

    # 检查内置版本
    def is_bundled(tool_path):
        if not tool_path or not BUNDLED_BIN_DIR:
            return False
        try:
            return Path(tool_path).is_relative_to(BUNDLED_BIN_DIR)
        except Exception:
            # Python < 3.9 兼容
            return str(BUNDLED_BIN_DIR) in str(tool_path)

    ffmpeg_bundled = is_bundled(ffmpeg_path)
    ytdlp_bundled = is_bundled(ytdlp_path)

    def parse_ffmpeg_version(output):
        for line in output.splitlines():
            if "version" not in line.lower():
                continue

            parts = line.split()
            for index, part in enumerate(parts):
                if part.lower() == "version" and index + 1 < len(parts):
                    return parts[index + 1]
        return None

    def parse_ytdlp_version(output):
        for line in output.splitlines():
            version = line.strip()
            if version:
                return version
        return None

    async def get_ffmpeg_version():
        if not ffmpeg_path:
            return None
        return await _get_tool_version(
            str(ffmpeg_path),
            "-version",
            parse_ffmpeg_version,
            timeout=2.0,
        )

    async def get_ytdlp_version():
        if not ytdlp_path:
            return None
        return await _get_tool_version(
            str(ytdlp_path),
            "--version",
            parse_ytdlp_version,
            timeout=2.0,
        )

    ffmpeg_version, ytdlp_version, playwright_status = await asyncio.gather(
        get_ffmpeg_version(),
        get_ytdlp_version(),
        tool_mgr.check_playwright_status(),
        return_exceptions=True,
    )

    if isinstance(ffmpeg_version, Exception):
        ffmpeg_version = None

    if isinstance(ytdlp_version, Exception):
        ytdlp_version = None

    if isinstance(playwright_status, Exception):
        playwright_status = {
            "installed": False,
            "version": None,
            "browser_path": None,
            "error": str(playwright_status),
        }

    # 添加 FFmpeg 工具状态
    tools.append(ToolStatus(
        id="ffmpeg",
        name="FFmpeg",
        description="视频处理工具",
        installed=ffmpeg_path is not None,
        version=ffmpeg_version,
        path=str(ffmpeg_path) if ffmpeg_path else None,
        required=True,
        official_url="https://ffmpeg.org",
        bundled=ffmpeg_bundled
    ))

    # 添加 yt-dlp 工具状态
    tools.append(ToolStatus(
        id="ytdlp",
        name="yt-dlp",
        description="视频下载引擎",
        installed=ytdlp_path is not None,
        version=ytdlp_version,
        path=str(ytdlp_path) if ytdlp_path else None,
        required=True,
        official_url="https://github.com/yt-dlp/yt-dlp",
        bundled=ytdlp_bundled,
        updating=tool_mgr._updating_tools.get("ytdlp", False)
    ))

    ai_status = _get_ai_status_snapshot()
    whisper_available = bool(ai_status.get("installed"))
    whisper_version = ai_status.get("version")
    whisper_compatible = bool(ai_status.get("python_compatible", True))
    whisper_reason = ai_status.get("error")

    tools.append(ToolStatus(
        id="faster-whisper",
        name="faster-whisper",
        description="AI 字幕生成引擎",
        installed=whisper_available,
        version=whisper_version,
        required=False,
        official_url="https://github.com/SYSTRAN/faster-whisper",
        compatible=whisper_compatible,
        incompatible_reason=whisper_reason
    ))

    tools.append(ToolStatus(
        id="playwright",
        name="Playwright",
        description="抖音/TikTok 下载支持",
        installed=playwright_status.get("installed", False),
        version=playwright_status.get("version"),
        path=playwright_status.get("browser_path"),
        required=False,
        official_url="https://playwright.dev",
        compatible=True,
        incompatible_reason=playwright_status.get("error")
    ))

    return tools

@router.get("/storage")
async def get_storage_info(db: AsyncSession = Depends(get_session)):
    """获取存储信息"""
    try:
        from pathlib import Path
        from sqlalchemy import select, func
        from src.models.download import DownloadTask

        base_dir = get_base_dir()
        data_dir = base_dir / "data"

        def get_dir_size(path: Path) -> int:
            """计算目录大小"""
            total = 0
            try:
                if path.is_file():
                    return path.stat().st_size
                for item in path.rglob('*'):
                    if item.is_file():
                        total += item.stat().st_size
            except Exception:
                pass
            return total

        def format_size(bytes_size: int) -> str:
            """格式化大小"""
            if bytes_size == 0:
                return "0 B"
            for unit in ['B', 'KB', 'MB', 'GB']:
                if bytes_size < 1024.0:
                    return f"{bytes_size:.1f} {unit}"
                bytes_size /= 1024.0
            return f"{bytes_size:.1f} TB"

        # 数据库大小
        db_path = data_dir / "database.db"
        db_size = get_dir_size(db_path) if db_path.exists() else 0

        # 缓存大小
        temp_dir = data_dir / "temp"
        cache_size = get_dir_size(temp_dir) if temp_dir.exists() else 0

        # 日志大小
        logs_dir = data_dir / "logs"
        logs_size = get_dir_size(logs_dir) if logs_dir.exists() else 0

        # 获取下载历史计数
        download_count = 0
        try:
            result = await db.execute(select(func.count()).select_from(DownloadTask))
            download_count = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Failed to get download count: {e}")

        return {
            "database_size": format_size(db_size),
            "cache_size": format_size(cache_size),
            "logs_size": format_size(logs_size),
            "total_size": format_size(db_size + cache_size + logs_size),
            "download_history_count": download_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cache/clear")
async def clear_cache():
    """清理所有缓存"""
    try:
        base_dir = get_base_dir()
        cleared_items = []

        # 清理 temp 目录
        temp_dir = base_dir / "data" / "temp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            cleared_items.append("temp")

        # 清理视频信息缓存目录
        cache_dir = base_dir / "cache" / "video_info"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cleared_items.append("video_info")

        # 清理内存缓存
        try:
            from src.core.downloaders.cache_manager import get_cache
            video_cache = get_cache()
            video_cache.clear()
            cleared_items.append("memory_cache")
        except Exception as e:
            logger.warning(f"Failed to clear memory cache: {e}")

        return {
            "message": "缓存已清理",
            "cleared": cleared_items
        }
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/platform")
async def get_platform_info():
    """获取平台信息"""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version()
    }

@router.get("/downloads-path")
async def get_downloads_path():
    """获取 VidFlow 默认下载文件夹路径"""
    try:
        sys_platform = platform.system()

        # 获取用户主目录下的 Downloads/VidFlow 文件夹
        home_dir = os.path.expanduser("~")
        downloads_path = os.path.join(home_dir, "Downloads", "VidFlow")

        # 确保目录存在
        Path(downloads_path).mkdir(parents=True, exist_ok=True)

        return {"path": downloads_path}
    except Exception as e:
        # 即使出错也返回 200，只是路径为空
        print(f"Failed to get downloads path: {e}")
        return {"path": ""}

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点 - 用于实时推送进度"""
    from src.core.websocket_manager import get_ws_manager

    ws_manager = get_ws_manager()
    await ws_manager.connect(websocket)

    try:
        while True:
            # 保持连接，接收客户端消息（如果需要）
            data = await websocket.receive_text()
            # 这里可以处理客户端发来的消息
            logger.debug(f"Received from client: {data}")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await ws_manager.disconnect(websocket)

@router.post("/tools/install/ffmpeg")
async def install_ffmpeg():
    """检查并更新 FFmpeg"""
    try:
        from src.core.tool_manager import get_tool_manager, BIN_DIR
        from src.core.websocket_manager import get_ws_manager

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        # 设置进度回调
        async def progress_callback(tool_id: str, progress: int, message: str):
            await ws_manager.send_tool_progress(tool_id, progress, message)

        tool_mgr.set_progress_callback(progress_callback)

        # 获取远程版本（使用 BtbN/FFmpeg-Builds 仓库）
        remote_version = await _get_github_version("BtbN/FFmpeg-Builds", timeout=5.0)
        logger.info(f"[Tools] GitHub FFmpeg-Builds version: {remote_version}")

        if not remote_version:
            logger.warning("[Tools] Failed to get FFmpeg version from GitHub, will update anyway")
            # 无法获取远程版本，进行更新以确保最新
            await tool_mgr.download_ffmpeg()
            ffmpeg_path = await tool_mgr.setup_ffmpeg()
            return {
                "success": True,
                "message": "FFmpeg 更新完成",
                "path": str(ffmpeg_path),
                "updated": True,
                "version": "unknown"
            }

        # 读取本地版本记录文件
        version_file = BIN_DIR / ".ffmpeg_version"
        local_version = None
        if version_file.exists():
            try:
                local_version = version_file.read_text().strip()
            except Exception as e:
                logger.debug(f"[Tools] Failed to read local version: {e}")

        logger.info(f"[Tools] FFmpeg version check: local={local_version}, remote={remote_version}")

        # 比较版本
        if local_version == remote_version:
            # 版本相同，无需更新
            logger.info(f"[Tools] FFmpeg is already up to date: {remote_version}")
            return {
                "success": True,
                "message": f"FFmpeg 已是最新版本 ({remote_version})",
                "path": str(await tool_mgr.setup_ffmpeg()),
                "updated": False,
                "version": remote_version
            }

        # 版本不同或无本地版本记录，进行更新
        logger.info(f"[Tools] FFmpeg version mismatch, updating from {local_version} to {remote_version}...")
        await tool_mgr.download_ffmpeg()
        ffmpeg_path = await tool_mgr.setup_ffmpeg()

        # 保存版本号到文件
        try:
            version_file.write_text(remote_version)
            logger.info(f"[Tools] Saved FFmpeg version: {remote_version}")
        except Exception as e:
            logger.warning(f"[Tools] Failed to save version file: {e}")

        return {
            "success": True,
            "message": f"FFmpeg 更新成功 (版本: {remote_version})",
            "path": str(ffmpeg_path),
            "updated": True,
            "version": remote_version
        }
    except Exception as e:
        logger.error(f"[Tools] FFmpeg install error: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

@router.post("/tools/install/ytdlp")
async def install_ytdlp():
    """检查并更新 yt-dlp"""
    try:
        from src.core.tool_manager import get_tool_manager, BIN_DIR
        from src.core.websocket_manager import get_ws_manager
        import json

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        # 设置进度回调
        async def progress_callback(tool_id: str, progress: int, message: str):
            await ws_manager.send_tool_progress(tool_id, progress, message)

        tool_mgr.set_progress_callback(progress_callback)

        # 获取远程版本
        remote_version = await _get_github_version("yt-dlp/yt-dlp", timeout=5.0)
        logger.info(f"[Tools] GitHub yt-dlp version: {remote_version}")

        if not remote_version:
            logger.warning("[Tools] Failed to get yt-dlp version from GitHub, will update anyway")
            # 无法获取远程版本，进行更新以确保最新
            await tool_mgr.download_ytdlp()
            ytdlp_path = await tool_mgr.setup_ytdlp()
            return {
                "success": True,
                "message": "yt-dlp 更新完成",
                "path": str(ytdlp_path),
                "updated": True,
                "version": "unknown"
            }

        # 读取本地版本记录文件
        version_file = BIN_DIR / ".ytdlp_version"
        local_version = None
        if version_file.exists():
            try:
                local_version = version_file.read_text().strip()
            except Exception as e:
                logger.debug(f"[Tools] Failed to read local version: {e}")

        logger.info(f"[Tools] yt-dlp version check: local={local_version}, remote={remote_version}")

        # 比较版本
        if local_version == remote_version:
            # 版本相同，无需更新
            logger.info(f"[Tools] yt-dlp is already up to date: {remote_version}")
            return {
                "success": True,
                "message": f"yt-dlp 已是最新版本 ({remote_version})",
                "path": str(await tool_mgr.setup_ytdlp()),
                "updated": False,
                "version": remote_version
            }

        # 版本不同或无本地版本记录，进行更新
        logger.info(f"[Tools] yt-dlp version mismatch, updating from {local_version} to {remote_version}...")
        await tool_mgr.download_ytdlp()
        ytdlp_path = await tool_mgr.setup_ytdlp()

        # 保存版本号到文件
        try:
            version_file.write_text(remote_version)
            logger.info(f"[Tools] Saved yt-dlp version: {remote_version}")
        except Exception as e:
            logger.warning(f"[Tools] Failed to save version file: {e}")

        return {
            "success": True,
            "message": f"yt-dlp 更新成功 (版本: {remote_version})",
            "path": str(ytdlp_path),
            "updated": True,
            "version": remote_version
        }
    except Exception as e:
        logger.error(f"[Tools] yt-dlp install error: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

@router.post("/tools/install/playwright")
async def install_playwright(background_tasks: BackgroundTasks = None):
    """
    安装 Playwright 和 Chromium 浏览器（用于抖音/TikTok下载）
    使用后台任务，立即返回，通过 WebSocket 推送进度
    """
    try:
        from src.core.tool_manager import get_tool_manager
        from src.core.websocket_manager import get_ws_manager

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        # 先检查是否已安装
        status = await tool_mgr.check_playwright_status(force_refresh=True)
        if status.get("installed"):
            return {
                "success": True,
                "message": f"Playwright 已安装 (v{status.get('version')})",
                "version": status.get("version"),
                "already_installed": True
            }

        # 后台安装任务
        async def install_task():
            try:
                # 进度回调
                async def progress_callback(percent, message):
                    await ws_manager.send_tool_progress("playwright", percent, message)

                await ws_manager.send_tool_progress("playwright", 0, "开始安装 Playwright...")
                result = await tool_mgr.install_playwright(progress_callback)

                if result.get("success"):
                    await ws_manager.send_tool_progress("playwright", 100, result.get("message", "安装完成"))
                else:
                    await ws_manager.send_message({
                        "type": "tool_install_error",
                        "tool_id": "playwright",
                        "error": result.get("error", "安装失败")
                    })
            except Exception as e:
                logger.error(f"Background Playwright install task failed: {e}")
                await ws_manager.send_message({
                    "type": "tool_install_error",
                    "tool_id": "playwright",
                    "error": str(e)
                })

        # 添加到后台任务
        if background_tasks:
            background_tasks.add_task(install_task)
        else:
            import asyncio
            t = asyncio.create_task(install_task())
            t.add_done_callback(_handle_task_exception)

        return {
            "success": True,
            "message": "Playwright 安装任务已启动，请通过 WebSocket 查看进度",
            "status": "started"
        }

    except Exception as e:
        logger.error(f"Install Playwright failed: {e}")
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

@router.get("/tools/playwright/status")
async def get_playwright_status():
    """获取 Playwright 安装状态"""
    try:
        from src.core.tool_manager import get_tool_manager

        tool_mgr = get_tool_manager()
        status = await tool_mgr.check_playwright_status()

        return {
            "status": "success",
            **status
        }
    except Exception as e:
        logger.error(f"Failed to get Playwright status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tools/ytdlp/download")
async def download_ytdlp():
    """下载/更新 yt-dlp"""
    try:
        from src.core.tool_manager import get_tool_manager

        tool_mgr = get_tool_manager()

        # 异步下载（后台任务）
        import asyncio

        async def download_task():
            await tool_mgr.download_ytdlp()

        # 创建后台任务（不等待完成）
        t = asyncio.create_task(download_task())
        t.add_done_callback(_handle_task_exception)

        return {
            "success": True,
            "message": "yt-dlp 下载已启动"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动下载失败: {str(e)}")

@router.delete("/tools/ytdlp/downloaded")
async def reset_ytdlp_to_bundled():
    """恢复 yt-dlp 到内置版本（删除下载的版本）"""
    try:
        from pathlib import Path
        import platform

        # 确定下载文件的路径
        system = platform.system()
        exe_name = "yt-dlp.exe" if system == "Windows" else "yt-dlp"

        # 获取工具目录
        base_dir = Path(__file__).parent.parent.parent
        bin_dir = base_dir / "tools" / "bin"
        downloaded_path = bin_dir / exe_name

        if downloaded_path.exists():
            downloaded_path.unlink()  # 删除文件
            logger.info(f"Deleted downloaded yt-dlp: {downloaded_path}")
            return {
                "success": True,
                "message": "已恢复到内置版本"
            }
        else:
            return {
                "success": True,
                "message": "当前使用的就是内置版本"
            }
    except Exception as e:
        logger.error(f"Failed to reset yt-dlp: {e}")
        raise HTTPException(status_code=500, detail=f"恢复失败: {str(e)}")

@router.post("/tools/install/whisper")
async def install_whisper():
    """自动安装 faster-whisper（兼容旧接口）"""
    try:
        from src.core.tool_manager import get_tool_manager
        from src.core.websocket_manager import get_ws_manager

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        # 发送开始通知
        await ws_manager.send_tool_progress("faster-whisper", 0, "开始安装 faster-whisper...")

        success = await tool_mgr.install_faster_whisper()

        if success:
            await ws_manager.send_tool_progress("faster-whisper", 100, "安装完成")
            return {
                "success": True,
                "message": "faster-whisper 安装成功"
            }
        else:
            await ws_manager.send_tool_progress("faster-whisper", 0, "安装失败")
            raise HTTPException(status_code=500, detail="安装失败")
    except Exception as e:
        await ws_manager.send_tool_progress("faster-whisper", 0, f"安装失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

# ===== 新增：AI 工具管理 API =====

def _clear_ai_status_cache():
    """清除AI状态缓存（安装/卸载后调用）"""
    global _ai_status_cache, _ai_status_cache_time
    _ai_status_cache = None
    _ai_status_cache_time = None
    logger.info("[AI Status] 缓存已清除")

@router.get("/tools/ai/status")
async def get_ai_tools_status():
    """
    检查 AI 工具状态（带缓存 + 异步更新）

    策略：
    1. 缓存有效：立即返回缓存（毫秒级）
    2. 缓存过期但存在：立即返回旧缓存，后台异步更新（不阻塞响应）
    3. 缓存不存在：同步查询（仅首次或清除后）

    这样确保接口永远不会超时，最差情况下返回略过期的数据
    """
    global _ai_status_cache, _ai_status_cache_time

    now = datetime.now()

    # 缓存有效：直接返回
    if (_ai_status_cache is not None and
        _ai_status_cache_time is not None and
        now - _ai_status_cache_time < AI_STATUS_CACHE_TTL):
        logger.debug(f"[AI Status] 返回有效缓存（缓存时间: {_ai_status_cache_time}）")
        return _ai_status_cache

    # 缓存过期但存在：立即返回旧缓存，后台异步更新
    if _ai_status_cache is not None:
        logger.info("[AI Status] 缓存过期，返回旧缓存并后台更新")

        # 后台异步更新任务
        async def update_cache_task():
            global _ai_status_cache, _ai_status_cache_time
            try:
                from src.core.tool_manager import get_tool_manager
                tool_mgr = get_tool_manager()
                status = await tool_mgr.check_ai_tools_status()

                _ai_status_cache = status
                _ai_status_cache_time = datetime.now()
                logger.info(f"[AI Status] 后台缓存更新完成: {status}")
            except Exception as e:
                logger.error(f"[AI Status] 后台更新失败: {e}")
                # 失败保持旧缓存

        # 启动后台任务（不等待）
        asyncio.create_task(update_cache_task())

        # 立即返回旧缓存
        return _ai_status_cache

    # 缓存不存在：同步查询（仅首次或清除后）
    try:
        logger.info("[AI Status] 缓存不存在，同步查询AI工具状态")
        from src.core.tool_manager import get_tool_manager
        tool_mgr = get_tool_manager()
        status = await tool_mgr.check_ai_tools_status()

        # 更新缓存
        _ai_status_cache = status
        _ai_status_cache_time = now
        logger.info(f"[AI Status] 缓存已创建: {status}")

        return status
    except Exception as e:
        logger.error(f"[AI Status] 查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tools/ai/info")
async def get_ai_tools_info(version: str = "cpu"):
    """获取 AI 工具信息"""
    try:
        from src.core.tool_manager import get_tool_manager
        tool_mgr = get_tool_manager()
        info = tool_mgr.get_ai_tool_info(version)
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tools/ai/install")
async def install_ai_tools(version: str = "cpu", background_tasks: BackgroundTasks = None):
    """
    安装 AI 工具（faster-whisper + PyTorch）
    使用后台任务，立即返回，通过 WebSocket 推送进度

    Args:
        version: "cpu" 或 "cuda"
        background_tasks: FastAPI 后台任务
    """
    try:
        from src.core.tool_manager import get_tool_manager
        from src.core.websocket_manager import get_ws_manager

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        # 后台安装任务
        async def install_task():
            try:
                # 进度回调
                async def progress_callback(percent, message):
                    await ws_manager.send_tool_progress("ai-tools", percent, message)

                await ws_manager.send_tool_progress("ai-tools", 0, "开始安装 AI 工具...")
                result = await tool_mgr.install_ai_tools(version, progress_callback)

                # 安装完成后清除缓存
                _clear_ai_status_cache()

                if result.get("success"):
                    await ws_manager.send_tool_progress("ai-tools", 100, result.get("message", "安装完成"))
                else:
                    await ws_manager.send_message({
                        "type": "tool_install_error",
                        "tool_id": "ai-tools",
                        "error": result.get("error", "安装失败")
                    })
            except Exception as e:
                logger.error(f"Background install task failed: {e}")
                _clear_ai_status_cache()  # 失败也清除缓存
                await ws_manager.send_message({
                    "type": "tool_install_error",
                    "tool_id": "ai-tools",
                    "error": str(e)
                })

        # 添加到后台任务
        if background_tasks:
            background_tasks.add_task(install_task)
        else:
            # 如果没有 background_tasks，直接在新任务中执行
            import asyncio
            t = asyncio.create_task(install_task())
            t.add_done_callback(_handle_task_exception)

        return {
            "success": True,
            "message": "安装任务已启动，请通过 WebSocket 查看进度",
            "status": "started"
        }

    except Exception as e:
        logger.error(f"Install AI tools failed: {e}")
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

@router.post("/tools/ai/uninstall")
async def uninstall_ai_tools(background_tasks: BackgroundTasks = None):
    """卸载 AI 工具（后台执行，使用 WebSocket 推送进度）"""
    try:
        from src.core.tool_manager import get_tool_manager
        from src.core.websocket_manager import get_ws_manager

        tool_mgr = get_tool_manager()
        ws_manager = get_ws_manager()

        async def uninstall_task():
            try:
                # 进度回调
                async def progress_callback(percent, message):
                    await ws_manager.send_tool_progress("ai-tools-uninstall", percent, message)

                await ws_manager.send_tool_progress("ai-tools-uninstall", 0, "开始卸载 AI 工具...")
                result = await tool_mgr.uninstall_ai_tools(progress_callback)

                # 卸载完成后清除缓存
                _clear_ai_status_cache()

                if result.get("success"):
                    await ws_manager.send_tool_progress("ai-tools-uninstall", 100, result.get("message", "卸载完成"))
                else:
                    await ws_manager.send_tool_progress("ai-tools-uninstall", 100, result.get("error", "卸载失败"))
                    logger.warning(f"AI tools uninstall partial failure: {result}")
            except Exception as e:
                logger.error(f"Background uninstall task failed: {e}", exc_info=True)
                _clear_ai_status_cache()  # 失败也清除缓存
                await ws_manager.send_tool_progress("ai-tools-uninstall", 100, f"卸载失败: {str(e)}")

        if background_tasks:
            background_tasks.add_task(uninstall_task)
        else:
            import asyncio
            t = asyncio.create_task(uninstall_task())
            t.add_done_callback(_handle_task_exception)

        return {
            "success": True,
            "message": "卸载任务已启动，请通过 WebSocket 查看进度",
            "status": "started"
        }

    except Exception as e:
        logger.error(f"Uninstall AI tools failed: {e}")
        raise HTTPException(status_code=500, detail=f"卸载失败: {str(e)}")

@router.post("/tools/install/all")
async def install_all_tools():
    """一键安装所有工具"""
    try:
        from src.core.tool_manager import get_tool_manager

        tool_mgr = get_tool_manager()
        results = await tool_mgr.setup_all_tools()

        # 安装 faster-whisper
        whisper_success = await tool_mgr.install_faster_whisper()
        results['faster-whisper'] = {
            'success': whisper_success,
            'message': '安装成功' if whisper_success else '安装失败'
        }

        return {
            "success": True,
            "message": "工具安装完成",
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"安装失败: {str(e)}")

@router.post("/tools/update/dependencies")
async def update_dependencies():
    """更新所有 Python 依赖包"""
    try:
        import sys

        # 使用异步 subprocess 避免阻塞
        # 更新 pip
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()

        if process.returncode != 0:
            raise Exception("pip 更新失败")

        # 更新所有已安装的包
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pip', 'install', '--upgrade', '-r', 'requirements.txt',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore')
            raise Exception(f"依赖包更新失败: {error_msg}")

        return {
            "success": True,
            "message": "依赖包更新成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

@router.post("/tools/terminal")
async def open_terminal():
    """打开系统终端"""
    try:
        import subprocess
        system = platform.system()

        if system == "Windows":
            # Windows: 打开 PowerShell 在项目目录
            subprocess.Popen(['powershell.exe'], cwd=os.getcwd())
        elif system == "Darwin":
            # macOS: 打开 Terminal
            subprocess.Popen(['open', '-a', 'Terminal', os.getcwd()])
        else:
            # Linux: 打开默认终端
            subprocess.Popen(['x-terminal-emulator'], cwd=os.getcwd())

        return {
            "success": True,
            "message": "终端已打开"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开终端失败: {str(e)}")

@router.get("/tools/python-info")
async def get_python_info():
    """获取 Python 环境信息"""
    import sys

    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_path": sys.executable,
        "platform": platform.platform(),
        "architecture": platform.architecture()[0],
        "system": platform.system(),
        "cwd": os.getcwd()
    }

class ProxyStatus(BaseModel):
    """代理状态模型"""
    available: bool
    proxy_type: Optional[str] = None
    proxy_url: Optional[str] = None
    response_time: Optional[float] = None
    error: Optional[str] = None

@router.get("/network/proxy-check", response_model=ProxyStatus)
async def check_proxy():
    """检测网络连接（测试是否能访问 Google）"""
    import httpx
    import time

    # 获取系统代理设置（可能为空）
    http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    proxy_url = https_proxy or http_proxy

    test_url = "https://www.google.com"
    start_time = time.time()

    try:
        # 根据是否有代理配置，选择不同的客户端创建方式
        if proxy_url:
            # 使用代理
            client = httpx.AsyncClient(
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=10.0,
                follow_redirects=True
            )
        else:
            # 直连
            client = httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True
            )

        async with client:
            response = await client.head(test_url)  # 使用 HEAD 请求更快
            response_time = time.time() - start_time

            if response.status_code in [200, 301, 302]:  # 允许重定向
                return ProxyStatus(
                    available=True,
                    proxy_type="代理" if proxy_url else "直连",
                    proxy_url=proxy_url if proxy_url else "直接连接",
                    response_time=round(response_time * 1000, 2)  # 转换为毫秒
                )
            else:
                return ProxyStatus(
                    available=False,
                    proxy_url=proxy_url if proxy_url else "直接连接",
                    error=f"无法访问 Google (HTTP {response.status_code})"
                )

    except httpx.TimeoutException:
        return ProxyStatus(
            available=False,
            proxy_url=proxy_url if proxy_url else "直接连接",
            error="连接超时，无法访问 Google"
        )
    except httpx.ConnectError:
        return ProxyStatus(
            available=False,
            proxy_url=proxy_url if proxy_url else "直接连接",
            error="网络连接失败，无法访问 Google"
        )
    except Exception as e:
        return ProxyStatus(
            available=False,
            proxy_url=proxy_url if proxy_url else "直接连接",
            error=f"检测失败: {str(e)}"
        )

@router.post("/open-folder")
async def open_folder(request: dict = Body(...)):
    """打开文件夹"""
    import subprocess
    import platform

    folder_path = request.get("path")
    path = _validate_folder_path(folder_path)

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))
        elif system == "Darwin":  # macOS
            subprocess.run(["open", str(path)], check=True)
        else:  # Linux
            subprocess.run(["xdg-open", str(path)], check=True)

        return {"success": True, "message": "文件夹已打开"}
    except Exception as e:
        logger.error(f"Failed to open folder: {e}")
        raise HTTPException(status_code=500, detail="打开文件夹失败")

@router.post("/select-file")
async def select_file(request: dict = Body(...)):
    """打开文件选择对话框"""
    import tkinter as tk
    from tkinter import filedialog

    try:
        # 创建隐藏的 Tk 窗口
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        # 获取过滤器
        filters = request.get("filters", [])
        filetypes = []
        for f in filters:
            name = f.get("name", "所有文件")
            extensions = f.get("extensions", ["*"])
            ext_str = ";".join([f"*.{ext}" for ext in extensions])
            filetypes.append((name, ext_str))

        if not filetypes:
            filetypes = [("所有文件", "*.*")]

        # 打开文件选择对话框
        file_path = filedialog.askopenfilename(
            title="选择文件",
            filetypes=filetypes
        )

        root.destroy()

        if file_path:
            return {"path": file_path}
        else:
            return {"path": None}

    except Exception as e:
        logger.error(f"Failed to select file: {e}")
        raise HTTPException(status_code=500, detail=f"选择文件失败: {str(e)}")

@router.post("/save-file")
async def save_file(request: dict = Body(...)):
    """打开文件保存对话框"""
    import tkinter as tk
    from tkinter import filedialog

    try:
        # 创建隐藏的 Tk 窗口
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        # 获取默认路径和过滤器
        default_path = request.get("default_path", "")
        if default_path:
            try:
                default_parent = Path(default_path).expanduser().parent
                _validate_folder_path(str(default_parent))
            except Exception:
                raise HTTPException(status_code=400, detail="默认保存路径无效或不允许")
        filters = request.get("filters", [])
        filetypes = []
        for f in filters:
            name = f.get("name", "所有文件")
            extensions = f.get("extensions", ["*"])
            ext_str = ";".join([f"*.{ext}" for ext in extensions])
            filetypes.append((name, ext_str))

        if not filetypes:
            filetypes = [("所有文件", "*.*")]

        # 打开文件保存对话框
        file_path = filedialog.asksaveasfilename(
            title="保存文件",
            initialfile=default_path,
            filetypes=filetypes,
            defaultextension=".mp4"
        )

        root.destroy()

        if file_path:
            return {"path": file_path}
        else:
            return {"path": None}

    except Exception as e:
        logger.error(f"Failed to save file: {e}")
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")

@router.get("/gpu/status")
async def get_gpu_status():
    """获取GPU状态和加速插件信息"""
    try:
        from src.core.gpu_manager import get_gpu_manager

        gpu_mgr = get_gpu_manager()
        status = await gpu_mgr.get_status()  # 异步调用

        return status
    except Exception as e:
        logger.error(f"Failed to get GPU status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gpu/install")
async def install_gpu_package(background_tasks: BackgroundTasks = None):
    """安装GPU加速包"""
    try:
        from src.core.gpu_manager import get_gpu_manager
        from src.core.websocket_manager import get_ws_manager

        gpu_mgr = get_gpu_manager()
        ws_manager = get_ws_manager()

        # 启动后台安装任务
        async def install_task():
            try:
                # 进度回调
                async def progress_callback(percent, message):
                    await ws_manager.send_tool_progress("gpu", percent, message)

                await ws_manager.send_tool_progress("gpu", 0, "开始安装 GPU 加速包...")
                result = await gpu_mgr.install_gpu_package(progress_callback)

                if result.get("success"):
                    await ws_manager.send_tool_progress("gpu", 100, result.get("message", "安装完成"))
                else:
                    error = result.get("error") or result.get("message") or "安装失败"
                    if hasattr(ws_manager, "send_tool_error"):
                        await ws_manager.send_tool_error("gpu", error)
                    else:
                        await ws_manager.send_message({
                            "type": "tool_install_error",
                            "tool_id": "gpu",
                            "error": error
                        })
            except Exception as e:
                logger.error(f"Background GPU install task failed: {e}", exc_info=True)
                error = str(e)
                if hasattr(ws_manager, "send_tool_error"):
                    await ws_manager.send_tool_error("gpu", error)
                else:
                    await ws_manager.send_message({
                        "type": "tool_install_error",
                        "tool_id": "gpu",
                        "error": error
                    })

        if background_tasks:
            background_tasks.add_task(install_task)
        else:
            t = asyncio.create_task(install_task())
            t.add_done_callback(_handle_task_exception)

        return {
            "status": "success",
            "success": True,
            "message": "GPU加速包安装已开始，请稍候...",
            "note": "安装需要5-10分钟，完成后请重启软件"
        }
    except Exception as e:
        logger.error(f"Failed to start GPU installation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ===== Cookie 管理 API =====

# 支持的平台及其Cookie文件名
SUPPORTED_PLATFORMS = {
    "douyin": {
        "name": "抖音",
        "filename": "douyin_cookies.txt",
        "description": "用于下载抖音视频",
        "guide_url": "Docs/DOUYIN_COOKIE_GUIDE.md",
        "category": "short_video"
    },
    "tiktok": {
        "name": "TikTok",
        "filename": "tiktok_cookies.txt",
        "description": "用于下载TikTok视频",
        "guide_url": "Docs/DOUYIN_COOKIE_GUIDE.md",
        "category": "short_video"
    },
    "xiaohongshu": {
        "name": "小红书",
        "filename": "xiaohongshu_cookies.txt",
        "description": "用于下载小红书视频和图片",
        "guide_url": "Docs/XIAOHONGSHU_COOKIE_GUIDE.md",
        "category": "short_video"
    },
    "xinpianchang": {
        "name": "新片场",
        "filename": "xinpianchang_cookies.txt",
        "description": "用于下载需要登录的新片场视频",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "bilibili": {
        "name": "Bilibili",
        "filename": "bilibili_cookies.txt",
        "description": "用于下载B站会员专享视频",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "youtube": {
        "name": "YouTube",
        "filename": "youtube_cookies.txt",
        "description": "用于下载YouTube会员或私享视频",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "iqiyi": {
        "name": "爱奇艺",
        "filename": "iqiyi_cookies.txt",
        "description": "用于下载爱奇艺会员视频（DRM内容无法下载）",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "youku": {
        "name": "优酷",
        "filename": "youku_cookies.txt",
        "description": "用于下载优酷会员视频（DRM内容无法下载）",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "tencent": {
        "name": "腾讯视频",
        "filename": "tencent_cookies.txt",
        "description": "用于下载腾讯视频会员内容（DRM内容无法下载）",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    },
    "twitter": {
        "name": "Twitter/X",
        "filename": "twitter_cookies.txt",
        "description": "用于下载Twitter/X的私密内容",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "social_media"
    },
    "instagram": {
        "name": "Instagram",
        "filename": "instagram_cookies.txt",
        "description": "用于下载Instagram的私密内容",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "social_media"
    },
    "vimeo": {
        "name": "Vimeo",
        "filename": "vimeo_cookies.txt",
        "description": "用于下载Vimeo私有视频或需要登录的内容",
        "guide_url": "backend/data/cookies/README.txt",
        "category": "video_platform"
    }
}

# 平台分类定义
PLATFORM_CATEGORIES = {
    "short_video": {
        "name": "短视频平台",
        "description": "抖音、TikTok、小红书等短视频平台"
    },
    "video_platform": {
        "name": "视频平台",
        "description": "B站、YouTube等视频平台"
    },
    "social_media": {
        "name": "社交媒体",
        "description": "Twitter、Instagram等社交媒体平台"
    }
}

def get_cookies_dir() -> Path:
    """获取Cookie文件夹路径（支持打包环境）"""
    # 使用统一的 Cookie 目录获取函数，确保开发和打包环境一致
    from src.core.downloaders.cookie_manager import get_cookie_base_dir
    return get_cookie_base_dir()

# ===== Cookie/CORS 测试与示例 API =====

@router.post("/auth/test/login")
async def auth_test_login(response: Response, request: Request):
    """设置测试会话 Cookie（用于验证 Set-Cookie/CORS/withCredentials）"""
    try:
        session_value = f"test-session-{int(datetime.now().timestamp())}"
        response.set_cookie(
            key="vidflow_session",
            value=session_value,
            max_age=7 * 24 * 3600,
            expires=7 * 24 * 3600,
            path="/",
            domain=None,
            secure=False,
            httponly=True,
            samesite="lax",
        )
        # region agent log
        try:
            log_path = Path(r"d:\Coding Project\VidFlow\VidFlow-Desktop\.cursor\debug.log")
            payload = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H1",
                "location": "backend/src/api/system.py:auth_test_login",
                "message": "Set-Cookie configuration observed",
                "data": {
                    "origin": request.headers.get("origin"),
                    "host": request.headers.get("host"),
                    "cookie": {
                        "name": "vidflow_session",
                        "domain": None,
                        "samesite": "lax",
                        "secure": False,
                        "path": "/",
                        "max_age": 7 * 24 * 3600
                    }
                },
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # endregion
        return {
            "status": "success",
            "message": "测试登录成功，已设置 Cookie",
            "session": session_value,
        }
    except Exception as e:
        logger.error(f"Set test cookie failed: {e}")
        raise HTTPException(status_code=500, detail=f"设置 Cookie 失败: {str(e)}")

@router.get("/auth/test/check")
async def auth_test_check(request: Request):
    """检查请求中是否携带测试会话 Cookie"""
    try:
        cookie_val = request.cookies.get("vidflow_session")
        return {
            "status": "success",
            "has_cookie": bool(cookie_val),
            "cookie": cookie_val,
        }
    except Exception as e:
        logger.error(f"Check cookie failed: {e}")
        raise HTTPException(status_code=500, detail=f"检查 Cookie 失败: {str(e)}")

class CookieStatus(BaseModel):
    """Cookie状态模型"""
    platform: str
    name: str
    description: str
    configured: bool
    category: str
    file_size: Optional[int] = None
    last_modified: Optional[str] = None
    guide_url: Optional[str] = None

class CookieContent(BaseModel):
    """Cookie内容模型"""
    content: str

@router.get("/cookies/status")
async def get_cookies_status():
    """获取所有平台的Cookie配置状态"""
    try:
        cookies_dir = get_cookies_dir()
        status_list = []

        for platform_id, platform_info in SUPPORTED_PLATFORMS.items():
            cookie_file = cookies_dir / platform_info["filename"]
            configured = cookie_file.exists() and cookie_file.stat().st_size > 0

            status = CookieStatus(
                platform=platform_id,
                name=platform_info["name"],
                description=platform_info["description"],
                configured=configured,
                category=platform_info["category"],
                guide_url=platform_info["guide_url"]
            )

            if configured:
                stat = cookie_file.stat()
                status.file_size = stat.st_size
                status.last_modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            status_list.append(status)

        return {
            "status": "success",
            "platforms": status_list
        }
    except Exception as e:
        logger.error(f"Failed to get cookies status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cookies/open-folder")
async def open_cookies_folder():
    """打开Cookie文件夹"""
    try:
        cookies_dir = get_cookies_dir()

        system = platform.system()
        if system == "Windows":
            os.startfile(str(cookies_dir))
        elif system == "Darwin":  # macOS
            subprocess.run(["open", str(cookies_dir)])
        else:  # Linux
            subprocess.run(["xdg-open", str(cookies_dir)])

        return {
            "status": "success",
            "message": "Cookie文件夹已打开",
            "path": str(cookies_dir)
        }
    except Exception as e:
        logger.error(f"Failed to open cookies folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ===== 具体路由必须在通用路由 {platform} 之前定义 =====

@router.post("/cookies/from-browser")
async def extract_cookies_from_installed_browser(request: dict = Body(...)):
    try:
        platform_id = request.get("platform")
        browser_name = request.get("browser", "chrome")

        if not platform_id:
            return {
                "status": "error",
                "error": "缺少platform参数"
            }

        if platform_id not in SUPPORTED_PLATFORMS:
            return {
                "status": "error",
                "error": f"不支持的平台: {platform_id}"
            }

        from src.core.cookie_helper import extract_cookies_from_browser as extract_from_local_browser

        result = await extract_from_local_browser(platform_id, browser_name)
        if result.get("status") == "error":
            return result

        cookies_dir = get_cookies_dir()
        cookie_file = cookies_dir / SUPPORTED_PLATFORMS[platform_id]["filename"]
        content = result.get("content") or ""

        if content:
            try:
                write_cookie_file(cookie_file, content)
            except Exception as write_error:
                logger.error(f"写入 Cookie 文件失败: {write_error}")
                return {
                    "status": "error",
                    "error": f"保存 Cookie 文件失败: {str(write_error)}"
                }

            try:
                stat = cookie_file.stat()
                result["file_size"] = stat.st_size
                result["last_modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        return result
    except Exception as e:
        logger.error(f"从浏览器提取 Cookie 失败: {e}")
        return {
            "status": "error",
            "error": f"提取 Cookie 失败: {str(e)}"
        }

# ===== 通用路由 {platform} 必须在具体路由之后 =====

@router.get("/cookies/{platform}")
async def get_cookie_content(platform: str):
    """获取指定平台的Cookie内容"""
    try:
        if platform not in SUPPORTED_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

        cookies_dir = get_cookies_dir()
        cookie_file = cookies_dir / SUPPORTED_PLATFORMS[platform]["filename"]

        if not cookie_file.exists():
            return {
                "status": "success",
                "content": "",
                "configured": False
            }

        content = read_cookie_file(cookie_file)

        return {
            "status": "success",
            "content": content,
            "configured": True,
            "file_size": cookie_file.stat().st_size,
            "last_modified": datetime.fromtimestamp(cookie_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cookie content for {platform}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cookies/{platform}")
async def save_cookie_content(platform: str, cookie_data: CookieContent):
    """保存指定平台的Cookie内容"""
    try:
        if platform not in SUPPORTED_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

        cookies_dir = get_cookies_dir()
        cookie_file = cookies_dir / SUPPORTED_PLATFORMS[platform]["filename"]

        # 验证 Cookie 格式
        is_valid, errors, cleaned_content = validate_netscape_cookie_format(cookie_data.content)

        if errors:
            # 有格式错误，返回详细错误信息
            error_summary = f"Cookie 格式存在 {len(errors)} 个错误:\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                error_summary += f"\n... 还有 {len(errors) - 5} 个错误"

            raise HTTPException(
                status_code=400,
                detail={
                    "message": error_summary,
                    "errors": errors,
                    "error_count": len(errors)
                }
            )

        # 保存Cookie内容
        write_cookie_file(cookie_file, cookie_data.content)
        # region agent log
        try:
            log_path = Path(r"d:\Coding Project\VidFlow\VidFlow-Desktop\.cursor\debug.log")
            payload = {
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "H2",
                "location": "backend/src/api/system.py:save_cookie_content",
                "message": "Cookie file persisted",
                "data": {
                    "platform": platform,
                    "file": str(cookie_file),
                    "contentLength": len(cookie_data.content or ""),
                    "existsAfterWrite": cookie_file.exists()
                },
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # endregion

        return {
            "status": "success",
            "message": f"{SUPPORTED_PLATFORMS[platform]['name']} Cookie已保存",
            "file_size": cookie_file.stat().st_size,
            "last_modified": datetime.fromtimestamp(cookie_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save cookie for {platform}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/cookies/{platform}")
async def delete_cookie(platform: str):
    """删除指定平台的Cookie"""
    try:
        if platform not in SUPPORTED_PLATFORMS:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}")

        cookies_dir = get_cookies_dir()
        cookie_file = cookies_dir / SUPPORTED_PLATFORMS[platform]["filename"]

        if cookie_file.exists():
            cookie_file.unlink()
            message = f"{SUPPORTED_PLATFORMS[platform]['name']} Cookie已删除"
        else:
            message = f"{SUPPORTED_PLATFORMS[platform]['name']} Cookie不存在"

        return {
            "status": "success",
            "message": message
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cookie for {platform}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ===== Cookie 自动获取 API =====

@router.get("/cookies/auto/selenium-status")
async def check_selenium_status():
    """检查Selenium是否可用"""
    try:
        from src.core.cookie_helper import get_cookie_browser_manager

        manager = get_cookie_browser_manager()
        status = manager.get_status()

        return {
            "status": "success",
            "data": status
        }
    except Exception as e:
        logger.error(f"Failed to check Selenium status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cookies/auto/start-browser")
async def start_cookie_browser(request: dict = Body(...)):
    """启动受控浏览器,让用户手动登录"""
    try:
        platform = request.get("platform")
        browser = request.get("browser", "chrome")
        logger.info(f"[DEBUG] start_cookie_browser received: platform={platform}, browser={browser}")
        if not platform:
            return {
                "status": "error",
                "error": "缺少platform参数"
            }

        from src.core.cookie_helper import get_cookie_browser_manager

        manager = get_cookie_browser_manager()
        logger.info(f"[DEBUG] Calling manager.start_browser with platform={platform}, browser={browser}")
        result = await manager.start_browser(platform, browser)

        # 业务层面的错误直接返回给前端，不抛HTTP异常
        return result
    except Exception as e:
        logger.error(f"Failed to start cookie browser: {e}")
        return {
            "status": "error",
            "error": f"启动浏览器失败: {str(e)}"
        }

@router.post("/cookies/auto/extract")
async def extract_cookies_from_browser():
    """从浏览器提取Cookie"""
    try:
        from src.core.cookie_helper import get_cookie_browser_manager

        manager = get_cookie_browser_manager()
        result = await manager.extract_cookies()

        # 业务层面的错误（如未登录、未检测到平台 Cookie）直接返回给前端
        # 由前端根据 status 字段展示友好提示，而不是抛 HTTP 错误
        if result.get("status") == "error":
            return result

        # 自动保存到文件
        platform = result.get("platform")
        content = result.get("content")

        if platform and content:
            cookies_dir = get_cookies_dir()
            # 确保目录存在
            cookies_dir.mkdir(parents=True, exist_ok=True)

            cookie_file = cookies_dir / SUPPORTED_PLATFORMS[platform]["filename"]
            try:
                write_cookie_file(cookie_file, content)
                logger.info(f"自动保存 {platform} Cookie 到文件: {cookie_file}")
            except Exception as write_error:
                logger.error(f"写入 Cookie 文件失败: {write_error}")
                return {
                    "status": "error",
                    "error": f"保存 Cookie 文件失败: {str(write_error)}"
                }

        return result
    except Exception as e:
        logger.error(f"Failed to extract cookies: {e}")
        return {
            "status": "error",
            "error": f"提取 Cookie 失败: {str(e)}"
        }

@router.post("/cookies/auto/close-browser")
async def close_cookie_browser():
    """关闭浏览器"""
    try:
        from src.core.cookie_helper import get_cookie_browser_manager

        manager = get_cookie_browser_manager()
        result = await manager.close_browser()

        return result
    except Exception as e:
        logger.error(f"Failed to close browser: {e}")
        return {
            "status": "error",
            "error": f"关闭浏览器失败: {str(e)}"
        }
