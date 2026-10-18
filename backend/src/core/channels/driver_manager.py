"""
WinDivert 驱动管理器

负责 WinDivert 驱动的检测、安装和状态管理。
支持从 GitHub 自动下载 WinDivert 驱动文件。
"""

import os
import sys
import ctypes
import shutil
import subprocess
import logging
import zipfile
import tempfile
import importlib
from pathlib import Path
from typing import Optional

from .models import (
    DriverState,
    DriverStatus,
    DriverInstallResult,
    ErrorCode,
    get_error_message,
)


logger = logging.getLogger(__name__)

# WinDivert 下载配置
WINDIVERT_VERSION = "2.2.2"
WINDIVERT_DOWNLOAD_URL = (
    f"https://github.com/basil00/WinDivert/releases/download/"
    f"v{WINDIVERT_VERSION}/WinDivert-{WINDIVERT_VERSION}-A.zip"
)
# zip 内的子目录名
WINDIVERT_ZIP_PREFIX = f"WinDivert-{WINDIVERT_VERSION}-A"


class DriverManager:
    """WinDivert 驱动管理器"""

    # 驱动文件列表
    DRIVER_FILES_64 = ["WinDivert.dll", "WinDivert64.sys"]
    DRIVER_FILES_32 = ["WinDivert.dll", "WinDivert32.sys"]

    def __init__(self, driver_dir: Optional[Path] = None):
        """初始化驱动管理器

        Args:
            driver_dir: 驱动文件目录，默认为 tools/bin/windivert
        """
        if driver_dir is None:
            # 默认路径：backend/tools/bin/windivert
            base_dir = Path(__file__).parent.parent.parent.parent
            driver_dir = base_dir / "tools" / "bin" / "windivert"

        self.driver_dir = Path(driver_dir)
        self._is_64bit = sys.maxsize > 2**32

    @property
    def driver_files(self) -> list:
        """Get required driver files for current architecture."""
        return self.DRIVER_FILES_64 if self._is_64bit else self.DRIVER_FILES_32

    def _iter_candidate_driver_dirs(self) -> list[Path]:
        """Return directories that may contain WinDivert binaries."""
        candidates = [self.driver_dir]

        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            meipass_base = Path(meipass)
            candidates.extend([
                meipass_base / 'mitmproxy_windows',
                meipass_base / 'pydivert' / 'windivert_dll',
            ])

        for module_name in ('mitmproxy_windows', 'pydivert.windivert_dll'):
            try:
                module = importlib.import_module(module_name)
                module_file = getattr(module, '__file__', None)
                if module_file:
                    candidates.append(Path(module_file).resolve().parent)
            except Exception:
                logger.debug('Driver module not available: %s', module_name, exc_info=True)

        unique_candidates: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            resolved = Path(candidate)
            try:
                resolved = resolved.resolve()
            except OSError:
                resolved = Path(candidate)
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(resolved)

        return unique_candidates

    def _get_missing_files(self, driver_dir: Path) -> list[str]:
        return [filename for filename in self.driver_files if not (driver_dir / filename).exists()]

    def _find_available_driver_dir(self) -> Optional[Path]:
        for candidate in self._iter_candidate_driver_dirs():
            if candidate.exists() and not self._get_missing_files(candidate):
                return candidate
        return None

    def is_installed(self) -> bool:
        """Check whether WinDivert binaries are available in the current runtime.

        This recognizes both the project-managed tools/bin/windivert directory
        and the bundled mitmproxy/pydivert copies used by transparent capture.
        """
        return self._find_available_driver_dir() is not None

    def get_status(self) -> DriverStatus:
        """Get detailed driver status."""
        is_admin = self.check_admin_privileges()
        driver_dir = self._find_available_driver_dir()

        if driver_dir is None:
            if self.driver_dir.exists():
                missing_files = self._get_missing_files(self.driver_dir)
                error_message = f"缺少驱动文件: {', '.join(missing_files)}" if missing_files else "驱动文件未找到"
            else:
                candidate_dirs = "；".join(str(path) for path in self._iter_candidate_driver_dirs())
                error_message = f"未找到 WinDivert 驱动，可检查以下目录: {candidate_dirs}"

            return DriverStatus(
                state=DriverState.NOT_INSTALLED,
                path=str(self.driver_dir),
                error_message=error_message,
                is_admin=is_admin,
            )

        version = self._get_driver_version(driver_dir)
        return DriverStatus(
            state=DriverState.INSTALLED,
            version=version,
            path=str(driver_dir),
            is_admin=is_admin,
        )

    def _get_driver_version(self, driver_dir: Optional[Path] = None) -> Optional[str]:
        """Read WinDivert version information if available."""
        try:
            resolved_driver_dir = Path(driver_dir) if driver_dir else self._find_available_driver_dir()
            if resolved_driver_dir is None:
                return None

            # Try reading version data from the DLL.
            dll_path = resolved_driver_dir / "WinDivert.dll"
            if not dll_path.exists():
                return None

            # Windows version information API.
            if sys.platform == 'win32':
                from ctypes import wintypes

                version_dll = ctypes.windll.version

                # Determine version data buffer size.
                size = version_dll.GetFileVersionInfoSizeW(str(dll_path), None)
                if size == 0:
                    return None

                # Read version data into memory.
                buffer = ctypes.create_string_buffer(size)
                if not version_dll.GetFileVersionInfoW(str(dll_path), 0, size, buffer):
                    return None

                # Query the fixed file info block.
                vs_fixedfileinfo = ctypes.c_void_p()
                length = ctypes.c_uint()
                if not version_dll.VerQueryValueW(
                    buffer, "\\", ctypes.byref(vs_fixedfileinfo), ctypes.byref(length)
                ):
                    return None

                # Parse the version number.
                class VS_FIXEDFILEINFO(ctypes.Structure):
                    _fields_ = [
                        ("dwSignature", wintypes.DWORD),
                        ("dwStrucVersion", wintypes.DWORD),
                        ("dwFileVersionMS", wintypes.DWORD),
                        ("dwFileVersionLS", wintypes.DWORD),
                        ("dwProductVersionMS", wintypes.DWORD),
                        ("dwProductVersionLS", wintypes.DWORD),
                        ("dwFileFlagsMask", wintypes.DWORD),
                        ("dwFileFlags", wintypes.DWORD),
                        ("dwFileOS", wintypes.DWORD),
                        ("dwFileType", wintypes.DWORD),
                        ("dwFileSubtype", wintypes.DWORD),
                        ("dwFileDateMS", wintypes.DWORD),
                        ("dwFileDateLS", wintypes.DWORD),
                    ]

                info = ctypes.cast(vs_fixedfileinfo, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
                version = (
                    f"{(info.dwFileVersionMS >> 16) & 0xFFFF}."
                    f"{info.dwFileVersionMS & 0xFFFF}."
                    f"{(info.dwFileVersionLS >> 16) & 0xFFFF}."
                    f"{info.dwFileVersionLS & 0xFFFF}"
                )
                return version

            return None

        except Exception as e:
            logger.debug(f"Failed to get driver version: {e}")
            return None

    def check_admin_privileges(self) -> bool:
        """检查当前进程是否有管理员权限

        Returns:
            有管理员权限返回 True
        """
        if sys.platform != 'win32':
            return False

        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def install(self, source_dir: Optional[Path] = None) -> DriverInstallResult:
        """安装 WinDivert 驱动

        将驱动文件复制到目标目录。实际的驱动加载由 pydivert 在首次使用时自动完成。

        Args:
            source_dir: 源驱动文件目录，如果为 None 则假设文件已在目标目录

        Returns:
            DriverInstallResult: 安装结果
        """
        # 检查管理员权限
        if not self.check_admin_privileges():
            return DriverInstallResult(
                success=False,
                error_code=ErrorCode.ADMIN_REQUIRED,
                error_message=get_error_message(ErrorCode.ADMIN_REQUIRED),
            )

        try:
            # 确保目标目录存在
            self.driver_dir.mkdir(parents=True, exist_ok=True)

            # 如果提供了源目录，复制文件
            if source_dir:
                source_dir = Path(source_dir)
                copied: list[Path] = []
                try:
                    for filename in self.driver_files:
                        src = source_dir / filename
                        dst = self.driver_dir / filename
                        if src.exists():
                            shutil.copy2(src, dst)
                            copied.append(dst)
                            logger.info(f"Copied driver file: {filename}")
                        else:
                            raise FileNotFoundError(f"源文件不存在: {src}")
                except Exception as copy_err:
                    # 回滚已复制的文件
                    for dst in copied:
                        try:
                            dst.unlink(missing_ok=True)
                        except OSError:
                            pass
                    return DriverInstallResult(
                        success=False,
                        error_code=ErrorCode.DRIVER_MISSING,
                        error_message=str(copy_err),
                    )

            # 验证安装
            if not self.is_installed():
                return DriverInstallResult(
                    success=False,
                    error_code=ErrorCode.DRIVER_MISSING,
                    error_message="驱动文件安装验证失败",
                )

            logger.info("WinDivert driver installed successfully")
            return DriverInstallResult(success=True)

        except PermissionError as e:
            logger.error(f"Permission denied during driver installation: {e}")
            return DriverInstallResult(
                success=False,
                error_code=ErrorCode.PERMISSION_DENIED,
                error_message=get_error_message(ErrorCode.PERMISSION_DENIED),
            )
        except Exception as e:
            logger.exception("Failed to install driver")
            return DriverInstallResult(
                success=False,
                error_code=ErrorCode.DRIVER_LOAD_FAILED,
                error_message=str(e),
            )

    async def download_and_install(self) -> DriverInstallResult:
        """从 GitHub 下载并安装 WinDivert 驱动

        自动从 GitHub releases 下载 WinDivert zip，
        解压出当前架构所需的驱动文件并安装到目标目录。

        Returns:
            DriverInstallResult: 安装结果
        """
        import aiohttp

        # 如果已安装则跳过下载
        # Reuse bundled drivers when the runtime already ships them.
        existing_driver_dir = self._find_available_driver_dir()
        if existing_driver_dir is not None:
            version = self._get_driver_version(existing_driver_dir)
            logger.info(
                "WinDivert 驱动已存在，跳过下载 (version=%s, path=%s)",
                version,
                existing_driver_dir,
            )
            return DriverInstallResult(success=True)

        arch_dir = "amd64" if self._is_64bit else "x86"
        logger.info(f"开始从 GitHub 下载 WinDivert {WINDIVERT_VERSION} ({arch_dir})...")

        tmp_zip = None
        tmp_extract_dir = None
        try:
            # 下载 zip 到临时文件
            timeout = aiohttp.ClientTimeout(total=120, connect=30, sock_read=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(WINDIVERT_DOWNLOAD_URL) as resp:
                    if resp.status != 200:
                        return DriverInstallResult(
                            success=False,
                            error_code=ErrorCode.DRIVER_LOAD_FAILED,
                            error_message=f"下载失败: HTTP {resp.status}",
                        )

                    # 写入临时文件
                    tmp_fd, tmp_zip = tempfile.mkstemp(suffix=".zip", prefix="windivert_")
                    os.close(tmp_fd)
                    tmp_zip_path = Path(tmp_zip)

                    with open(tmp_zip_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            f.write(chunk)

                    total_size = tmp_zip_path.stat().st_size
                    logger.info(f"WinDivert zip 下载完成: {total_size / 1024:.0f} KB")

            # 解压到临时目录
            tmp_extract_dir = tempfile.mkdtemp(prefix="windivert_extract_")
            with zipfile.ZipFile(tmp_zip_path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    return DriverInstallResult(
                        success=False,
                        error_code=ErrorCode.DRIVER_LOAD_FAILED,
                        error_message=f"下载的 zip 文件损坏: {bad}",
                    )
                zf.extractall(tmp_extract_dir)

            # 从解压目录中找到对应架构的文件
            extract_base = Path(tmp_extract_dir)
            # zip 内结构: WinDivert-x.x.x-A/<arch>/WinDivert.dll 等
            arch_path = extract_base / WINDIVERT_ZIP_PREFIX / arch_dir
            if not arch_path.exists():
                # 尝试不带前缀的路径
                arch_path = extract_base / arch_dir
            if not arch_path.exists():
                # 搜索包含 WinDivert.dll 的目录
                found = False
                for root, _dirs, files in os.walk(extract_base):
                    if "WinDivert.dll" in files:
                        root_path = Path(root)
                        if root_path.name == arch_dir:
                            arch_path = root_path
                            found = True
                            break
                if not found:
                    return DriverInstallResult(
                        success=False,
                        error_code=ErrorCode.DRIVER_LOAD_FAILED,
                        error_message=f"zip 中未找到 {arch_dir} 架构的驱动文件",
                    )

            # 验证所需文件都在解压目录中
            for filename in self.driver_files:
                if not (arch_path / filename).exists():
                    return DriverInstallResult(
                        success=False,
                        error_code=ErrorCode.DRIVER_LOAD_FAILED,
                        error_message=f"zip 中缺少文件: {arch_dir}/{filename}",
                    )

            # 复制到目标目录
            result = self.install(source_dir=arch_path)
            if result.success:
                logger.info(f"WinDivert {WINDIVERT_VERSION} 安装成功: {self.driver_dir}")
            return result

        except aiohttp.ClientError as e:
            logger.error(f"WinDivert 下载网络错误: {e}")
            return DriverInstallResult(
                success=False,
                error_code=ErrorCode.DRIVER_LOAD_FAILED,
                error_message=f"下载失败（网络错误）: {e}",
            )
        except Exception as e:
            logger.exception("WinDivert 下载安装失败")
            return DriverInstallResult(
                success=False,
                error_code=ErrorCode.DRIVER_LOAD_FAILED,
                error_message=f"下载安装失败: {e}",
            )
        finally:
            # 清理临时文件
            if tmp_zip and os.path.exists(tmp_zip):
                try:
                    os.unlink(tmp_zip)
                except OSError:
                    pass
            if tmp_extract_dir and os.path.exists(tmp_extract_dir):
                try:
                    shutil.rmtree(tmp_extract_dir, ignore_errors=True)
                except OSError:
                    pass

    def request_admin_restart(self) -> bool:
        """请求以管理员身份重启应用

        使用 Windows ShellExecute 以管理员身份重新启动当前进程。

        Returns:
            请求成功返回 True（注意：成功只表示请求已发送，不表示用户已确认）
        """
        if sys.platform != 'win32':
            logger.warning("Admin restart is only supported on Windows")
            return False

        try:
            # 获取当前可执行文件路径
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包后的可执行文件
                executable = sys.executable
                working_dir = str(Path(executable).parent)
                params = subprocess.list2cmdline(sys.argv[1:]) if len(sys.argv) > 1 else ''
            else:
                # 开发环境：重新以管理员身份运行当前 Python 脚本
                executable = sys.executable
                script = Path(sys.argv[0]).resolve() if sys.argv else None
                argv = [str(script)] if script else []
                argv.extend(sys.argv[1:])
                params = subprocess.list2cmdline(argv)
                working_dir = str(script.parent) if script else None

            ret = ctypes.windll.shell32.ShellExecuteW(
                None,           # hwnd
                "runas",        # 以管理员身份运行
                executable,     # 可执行文件
                params,         # 参数
                working_dir,    # 工作目录
                1               # SW_SHOWNORMAL
            )

            # ShellExecuteW 返回值 > 32 表示成功
            if ret > 32:
                logger.info("Admin restart requested successfully")
                return True
            else:
                logger.warning(f"ShellExecute returned: {ret}")
                return False

        except Exception as e:
            logger.exception("Failed to request admin restart")
            return False

    def can_load_driver(self) -> bool:
        """检查是否可以加载驱动

        验证驱动文件存在且有管理员权限。

        Returns:
            可以加载返回 True
        """
        return self.is_installed() and self.check_admin_privileges()

    def get_dll_path(self) -> Optional[Path]:
        """Return the active WinDivert.dll path."""
        driver_dir = self._find_available_driver_dir()
        if driver_dir is None:
            return None
        dll_path = driver_dir / "WinDivert.dll"
        return dll_path if dll_path.exists() else None
