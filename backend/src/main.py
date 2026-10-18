"""VidFlow backend entrypoint."""

import asyncio
import atexit
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


if len(sys.argv) >= 3 and sys.argv[1] == "-m" and sys.argv[2] == "pip":
    import runpy

    sys.argv = ["pip"] + sys.argv[3:]
    runpy.run_module("pip", run_name="__main__")
    raise SystemExit(0)


if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            root = Path(os.environ.get("APPDATA", str(Path.home())))
            base_dir = root / "VidFlow"
        elif sys.platform == "darwin":
            base_dir = Path.home() / "Library" / "Application Support" / "VidFlow"
        else:
            base_dir = Path.home() / ".local" / "share" / "VidFlow"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir
    return Path(__file__).resolve().parent.parent


BASE_DIR = _resolve_base_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.core.config_manager import get_config_manager
from src.core.logging_config import DEFAULT_LOG_FORMAT, normalize_log_format

DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE_PATH = LOGS_DIR / "app.log"
LOG_FILE_PATH.touch(exist_ok=True)

config_manager = get_config_manager()
log_level_raw = os.environ.get("LOG_LEVEL") or config_manager.get("advanced.log_level", "INFO")
log_format = os.environ.get("LOG_FORMAT") or config_manager.get(
    "advanced.log_format",
    DEFAULT_LOG_FORMAT,
)
log_format = normalize_log_format(log_format)
valid_levels = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
logging.basicConfig(
    level=valid_levels.get(str(log_level_raw).upper(), logging.INFO),
    format=log_format,
    handlers=[
        RotatingFileHandler(
            LOG_FILE_PATH,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)
_capture_cleanup_registered = False
_capture_cleanup_running = False
_LOCAL_LOOPBACK_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$"
_LOCAL_LOOPBACK_ORIGIN_PATTERN = re.compile(_LOCAL_LOOPBACK_ORIGIN_REGEX)


def _cleanup_channels_capture_resources_on_exit() -> None:
    """Best-effort cleanup for exits that bypass FastAPI lifespan shutdown."""
    global _capture_cleanup_running

    if _capture_cleanup_running:
        return

    _capture_cleanup_running = True
    try:
        from src.api import channels as channels_api

        # 检查是否有正在运行的事件循环，避免与 uvicorn 冲突
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and not loop.is_closed():
            # 已有事件循环运行中，不能调用 asyncio.run()
            # 尝试在现有循环中调度清理任务
            try:
                loop.create_task(channels_api.shutdown_capture_resources())
            except RuntimeError:
                pass
        else:
            # 没有运行中的事件循环，安全创建新循环
            asyncio.run(channels_api.shutdown_capture_resources())
    except Exception:
        logger.exception("Failed to cleanup channels capture resources during process exit")
    finally:
        _capture_cleanup_running = False


def _register_capture_cleanup_on_exit() -> None:
    global _capture_cleanup_registered
    if _capture_cleanup_registered:
        return
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return

    atexit.register(_cleanup_channels_capture_resources_on_exit)
    _capture_cleanup_registered = True


_register_capture_cleanup_on_exit()


async def cleanup_stale_tasks() -> None:
    """Mark unfinished tasks as failed after an unclean shutdown."""
    try:
        from sqlalchemy import select
        from src.models.database import AsyncSessionLocal
        from src.models.download import DownloadTask
        from src.models.subtitle import BurnSubtitleTask, SubtitleTask

        task_specs = [
            (SubtitleTask, ["pending", "processing"], "error"),
            (BurnSubtitleTask, ["pending", "burning"], "error"),
            (DownloadTask, ["pending", "downloading"], "error_message"),
        ]
        total_cleaned = 0

        for model, statuses, error_field in task_specs:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(model).where(model.status.in_(statuses)))
                tasks = result.scalars().all()
                if not tasks:
                    continue

                for task in tasks:
                    task.status = "failed"
                    setattr(task, error_field, "Application exited unexpectedly; task cancelled")
                    task.completed_at = datetime.utcnow()

                await session.commit()
                total_cleaned += len(tasks)

        if total_cleaned:
            logger.info("Cleaned up %s stale tasks", total_cleaned)
    except Exception:
        logger.exception("Failed to clean up stale tasks")


async def stop_all_active_tasks() -> None:
    """Mark active tasks as failed during a graceful shutdown."""
    try:
        from sqlalchemy import select
        from src.models.database import AsyncSessionLocal
        from src.models.download import DownloadTask
        from src.models.subtitle import BurnSubtitleTask, SubtitleTask

        task_specs = [
            (SubtitleTask, ["pending", "processing"], "error"),
            (BurnSubtitleTask, ["pending", "burning"], "error"),
            (DownloadTask, ["pending", "downloading"], "error_message"),
        ]

        async with AsyncSessionLocal() as session:
            for model, statuses, error_field in task_specs:
                result = await session.execute(select(model).where(model.status.in_(statuses)))
                tasks = result.scalars().all()
                for task in tasks:
                    task.status = "failed"
                    setattr(task, error_field, "Application shutdown; task stopped")
                    task.completed_at = datetime.utcnow()
            await session.commit()
    except Exception:
        logger.exception("Failed to stop active tasks during shutdown")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    startup_start_time = time.time()
    logger.info("Backend startup initiated")

    from src.models import init_database

    db_init_task = asyncio.create_task(init_database())

    async def cleanup_after_db_init() -> None:
        try:
            await db_init_task
            await cleanup_stale_tasks()
        except Exception:
            logger.exception("Background database initialization failed")

    asyncio.create_task(cleanup_after_db_init())

    try:
        from src.core.tool_manager import initialize_tools

        asyncio.create_task(initialize_tools())
    except Exception:
        logger.exception("Failed to initialize tools")

    try:
        from src.core.qr_login import register_default_providers

        register_default_providers()
    except Exception:
        logger.exception("Failed to register QR login providers")

    def preload_ai_cache() -> None:
        try:
            from src.api.system import _preload_ai_status_cache

            _preload_ai_status_cache()
        except Exception:
            logger.exception("Failed to preload AI status cache")

    asyncio.create_task(asyncio.to_thread(preload_ai_cache))

    backup_task = None
    try:
        from src.core.backup_manager import schedule_backup_task

        backup_task = asyncio.create_task(
            schedule_backup_task(
                db_path=DATA_DIR / "database.db",
                backup_dir=DATA_DIR / "backups",
                interval_hours=24,
            )
        )
    except Exception:
        logger.exception("Failed to start backup scheduler")

    startup_duration = time.time() - startup_start_time
    logger.info("Backend startup completed in %.3fs", startup_duration)

    try:
        await asyncio.wait_for(db_init_task, timeout=5.0)
        logger.info("Database initialized successfully")
    except asyncio.TimeoutError:
        logger.warning("Database initialization still in progress")
    except Exception:
        logger.exception("Database initialization failed")

    yield

    await stop_all_active_tasks()

    try:
        from src.api import channels as channels_api

        await channels_api.shutdown_capture_resources()
    except Exception:
        logger.exception("Failed to cleanup channels capture resources during shutdown")

    if backup_task is not None and not backup_task.done():
        backup_task.cancel()
        try:
            await backup_task
        except asyncio.CancelledError:
            pass

    logger.info("Application shutdown")


app = FastAPI(
    title="VidFlow API",
    description="VidFlow backend API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time

    slow_request_whitelist = {
        "/api/v1/system/network/proxy-check",
        "/api/v1/system/tools/status",
        "/api/v1/system/tools/check-updates",
        "/api/v1/system/gpu/status",
        "/api/channels/certificate",
    }
    if process_time > 1.0 and request.url.path not in slow_request_whitelist:
        logger.warning(
            "Slow request: %s %s took %.2fs",
            request.method,
            request.url.path,
            process_time,
        )

    response.headers["X-Process-Time"] = str(process_time)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=_LOCAL_LOOPBACK_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def allow_private_network_access(request: Request, call_next):
    """Allow local development frontends to talk to the loopback backend."""
    response = await call_next(request)

    request_private_network = request.headers.get("access-control-request-private-network")
    request_origin = request.headers.get("origin")
    origin_is_loopback = bool(request_origin and _LOCAL_LOOPBACK_ORIGIN_PATTERN.fullmatch(request_origin))
    if request_private_network == "true" and origin_is_loopback:
        response.headers["Access-Control-Allow-Private-Network"] = "true"

    if request_private_network == "true" and origin_is_loopback:
        vary_parts = [
            part.strip()
            for part in response.headers.get("Vary", "").split(",")
            if part.strip()
        ]
        for extra in ["Origin", "Access-Control-Request-Private-Network"]:
            if extra not in vary_parts:
                vary_parts.append(extra)
        response.headers["Vary"] = ", ".join(vary_parts)

    return response


from src.api import channels, config, downloads, logs, proxy, qr_login, subtitle, system, updates, websocket

app.include_router(downloads.router)
app.include_router(system.router)
app.include_router(websocket.router)
app.include_router(subtitle.router)
app.include_router(logs.router)
app.include_router(config.router)
app.include_router(proxy.router)
app.include_router(updates.router)
app.include_router(qr_login.router)
app.include_router(channels.router)


@app.get("/")
async def root():
    return {
        "message": "VidFlow API Server",
        "version": app.version,
        "status": "running",
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "backend": "ready",
        "timestamp": datetime.now().isoformat(),
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Global exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)

    is_dev = os.getenv("ENV", "production") == "development"
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc) if is_dev else "Server internal error",
            "type": type(exc).__name__ if is_dev else "InternalServerError",
        },
    )


def _find_available_port(start_port: int = 10000, end_port: int = 65535, max_attempts: int = 100) -> int:
    import random
    import socket

    for _ in range(max_attempts):
        port = random.randint(start_port, end_port)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    from src.core.config_manager import get_default_download_path

    default_download_path = get_default_download_path()
    fixed_port = os.environ.get("VIDFLOW_FIXED_PORT")

    if fixed_port:
        try:
            port = int(fixed_port)
        except ValueError:
            logger.warning("Invalid VIDFLOW_FIXED_PORT=%s, falling back to a random port", fixed_port)
            port = _find_available_port()
    else:
        port = _find_available_port()

    startup_token = os.environ.get("VIDFLOW_STARTUP_TOKEN")
    port_file_payload = {
        "port": port,
        "host": "127.0.0.1",
        "pid": os.getpid(),
    }
    if startup_token:
        port_file_payload["startup_token"] = startup_token

    port_file = DATA_DIR / "backend_port.json"
    port_file.write_text(json.dumps(port_file_payload), encoding="utf-8")

    logger.info("Data directory: %s", DATA_DIR)
    logger.info("Default download path: %s", default_download_path)
    logger.info("Server will start on port: %s", port)

    sys.stdout.flush()
    sys.stderr.flush()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
