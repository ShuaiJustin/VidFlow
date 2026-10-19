# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

block_cipher = None


def env_flag(name, default='1'):
    value = os.environ.get(name, default)
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off', '')


BUNDLE_TOOLS = env_flag('VIDFLOW_BUNDLE_TOOLS', '1')
BUNDLE_PLAYWRIGHT = env_flag('VIDFLOW_BUNDLE_PLAYWRIGHT', '1')

# 收集数据文件
import glob
inject_script_datas = []
inject_script_path = os.path.join(
    os.path.dirname(os.path.abspath(SPEC)),
    'src',
    'core',
    'channels',
    'inject_script.js',
)
if os.path.exists(inject_script_path):
    inject_script_datas.append((inject_script_path, 'src/core/channels'))
    print(f"Packaged inject script: {inject_script_path}")
else:
    print(f"Warning: inject_script.js not found: {inject_script_path}")
datas = []
datas += inject_script_datas
certifi_datas = collect_data_files('certifi', includes=['cacert.pem'])
if not certifi_datas:
    raise RuntimeError('certifi/cacert.pem is required for verified HTTPS downloads')
datas += certifi_datas
binaries = []  # 初始化 binaries 列表

# 如果 tools/bin 目录存在且有文件，则打包工具
# 优先检查项目根目录的 resources/tools/bin（构建脚本下载的位置）
project_root = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
resources_tools_bin = os.path.join(project_root, 'resources', 'tools', 'bin')
backend_tools_bin = os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'tools', 'bin')

if BUNDLE_TOOLS:
    # 优先使用 resources/tools/bin
    if os.path.exists(resources_tools_bin) and os.listdir(resources_tools_bin):
        tools_bin = resources_tools_bin
        dest_path = 'resources/tools/bin'  # 保持与运行时代码一致的路径
    elif os.path.exists(backend_tools_bin) and os.listdir(backend_tools_bin):
        tools_bin = backend_tools_bin
        dest_path = 'tools/bin'
    else:
        tools_bin = None
        dest_path = None

    if tools_bin:
        # 打包所有工具文件（包括子目录，如 windivert/）
        for root, dirs, files in os.walk(tools_bin):
            rel_dir = os.path.relpath(root, tools_bin)
            target = dest_path if rel_dir == '.' else os.path.join(dest_path, rel_dir)
            for filename in files:
                filepath = os.path.join(root, filename)
                datas.append((filepath, target))
        print(f"已打包工具目录: {tools_bin} -> {dest_path}")
    else:
        print("警告: tools/bin 目录为空，工具将在首次运行时自动下载")
else:
    print("已跳过预打包 FFmpeg/yt-dlp，工具将在首次运行或手动安装时下载")

# 打包嵌入式 Python 3.11
python_embedded = os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'python_embedded')
if os.path.exists(python_embedded):
    for root, dirs, files in os.walk(python_embedded):
        # Cached code and Windows console launchers contain build-machine paths.
        # Runtime package management uses the relocatable python -m pip entry point.
        dirs[:] = [name for name in dirs if name.lower() != '__pycache__'
                   and not (root == python_embedded and name.lower() == 'scripts')]
        for file in files:
            if file.lower().endswith(('.pyc', '.pyo')):
                continue
            src = os.path.join(root, file)
            rel_path = os.path.relpath(root, python_embedded)
            dest = os.path.join('python', rel_path) if rel_path != '.' else 'python'
            datas.append((src, dest))
    print(f"已打包嵌入式 Python: {python_embedded}")
else:
    print("警告: python_embedded 目录不存在，请运行 download_embedded_python.py")

# 收集隐藏导入
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    # 'faster_whisper',  # 已移除：作为可选工具，按需安装
    'aiohttp',
    'sqlalchemy',
    'aiosqlite',
    'httpx',
    'httpx._client',
    'httpx._config',
    'httpx._models',
    'email.mime',
    'email.mime.text',
    'email.mime.multipart',
    # Selenium 依赖 (Selenium 4.x 需要)
    'trio',
    'trio_websocket',
    'certifi',
    'typing_extensions',
    'browser_cookie3',
    # webdriver_manager 的网络依赖
    'requests',
    'urllib3',
    'packaging',
    # Selenium 和 WebDriver Manager（用于受控浏览器获取 Cookie）
    'selenium',
    'selenium.webdriver',
    'selenium.webdriver.chrome',
    'selenium.webdriver.chrome.service',
    'selenium.webdriver.chrome.options',
    'selenium.webdriver.edge',
    'selenium.webdriver.edge.service',
    'selenium.webdriver.edge.options',
    'selenium.webdriver.firefox',
    'selenium.webdriver.firefox.service',
    'selenium.webdriver.firefox.options',
    'selenium.webdriver.common',
    'selenium.webdriver.common.service',
    'selenium.webdriver.remote',
    'selenium.webdriver.support',
    'selenium.common.exceptions',
    'webdriver_manager',
    'webdriver_manager.chrome',
    'webdriver_manager.microsoft',
    'webdriver_manager.firefox',
    'webdriver_manager.core',
    'webdriver_manager.core.driver',
    'webdriver_manager.core.driver_cache',
    'webdriver_manager.core.download_manager',
    'webdriver_manager.core.manager',
    'webdriver_manager.core.os_manager',
    'webdriver_manager.core.utils',
    'webdriver_manager.drivers',
    'webdriver_manager.drivers.chrome',
    'webdriver_manager.drivers.edge',
    'webdriver_manager.drivers.firefox',
]

if BUNDLE_PLAYWRIGHT:
    hiddenimports += [
        # Playwright（抖音下载，浏览器需要用户单独安装）
        'playwright',
        'playwright.sync_api',
        'playwright.async_api',
        'playwright._impl',
        'playwright._impl._browser',
        'playwright._impl._browser_context',
        'playwright._impl._browser_type',
        'playwright._impl._connection',
        'playwright._impl._driver',
        'playwright._impl._element_handle',
        'playwright._impl._frame',
        'playwright._impl._helper',
        'playwright._impl._page',
        'playwright._impl._transport',
    ]

# 收集 pip 的所有内容（用于 -m pip 模式）
pip_datas, pip_binaries, pip_hiddenimports = collect_all('pip')
datas += pip_datas
hiddenimports += pip_hiddenimports

# 收集 setuptools
setuptools_datas, setuptools_binaries, setuptools_hiddenimports = collect_all('setuptools')
datas += setuptools_datas
hiddenimports += setuptools_hiddenimports

# 收集 Selenium 关键依赖（必须在 Selenium 之前）
# Selenium 4.x 依赖 trio 进行异步操作
try:
    trio_datas, trio_binaries, trio_hiddenimports = collect_all('trio')
    datas += trio_datas
    binaries += trio_binaries
    hiddenimports += trio_hiddenimports
    print(f"已收集 trio 模块（Selenium 核心依赖）")
except Exception as e:
    print(f"警告: 收集 trio 失败 - {e}")

try:
    trio_ws_datas, trio_ws_binaries, trio_ws_hiddenimports = collect_all('trio_websocket')
    datas += trio_ws_datas
    binaries += trio_ws_binaries
    hiddenimports += trio_ws_hiddenimports
    print(f"已收集 trio_websocket 模块")
except Exception as e:
    print(f"警告: 收集 trio_websocket 失败 - {e}")

# 收集 Selenium 的数据文件（JavaScript 脚本等）
try:
    selenium_datas, selenium_binaries, selenium_hiddenimports = collect_all('selenium')
    datas += selenium_datas
    binaries += selenium_binaries
    hiddenimports += selenium_hiddenimports
    print(f"已收集 Selenium 数据文件和模块")
except Exception as e:
    print(f"警告: 收集 Selenium 失败 - {e}")

# 收集 webdriver-manager 的数据文件
try:
    wdm_datas, wdm_binaries, wdm_hiddenimports = collect_all('webdriver_manager')
    datas += wdm_datas
    binaries += wdm_binaries
    hiddenimports += wdm_hiddenimports
    print(f"已收集 webdriver-manager 数据文件和模块")
except Exception as e:
    print(f"警告: 收集 webdriver-manager 失败 - {e}")

# 收集 Playwright 的数据文件（不包含浏览器，浏览器需要用户单独安装）
if BUNDLE_PLAYWRIGHT:
    try:
        playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all('playwright')
        # 过滤掉浏览器二进制文件（太大，用户需要单独安装）
        playwright_datas = [(src, dst) for src, dst in playwright_datas if 'chromium' not in src.lower() and 'firefox' not in src.lower() and 'webkit' not in src.lower()]
        playwright_binaries = [(src, dst) for src, dst in playwright_binaries if 'chromium' not in src.lower() and 'firefox' not in src.lower() and 'webkit' not in src.lower()]
        datas += playwright_datas
        binaries += playwright_binaries
        hiddenimports += playwright_hiddenimports
        print(f"已收集 Playwright 数据文件和模块（不含浏览器）")
    except Exception as e:
        print(f"警告: 收集 Playwright 失败 - {e}")
else:
    print("已跳过预打包 Playwright 包，相关功能将按需安装")

# 排除 AI 组件（作为可选工具，用户按需安装）
excludes = [
    # AI 组件
    'torch',
    'torchvision',
    'torchaudio',
    'faster_whisper',
    'ctranslate2',
    'onnxruntime',
    # 其他大型依赖
    'matplotlib',
    'scipy',
    'pandas',
    'numpy.testing',
    # 不需要的标准库模块
    # 'tkinter',         # ❌ 不能排除：某些依赖需要
    'unittest',          # 测试框架
    'pydoc',            # 文档生成
    'doctest',          # 文档测试
    'test',             # 测试模块
    # 'setuptools',     # ❌ 不能排除：pip 需要
    # 'pip',            # ❌ 不能排除：需要用于 -m pip 模式
    # 'wheel',          # ❌ 不能排除：setuptools 内部会 alias wheel，排除会导致冲突
    # 'distutils',      # ❌ 不能排除：setuptools 依赖
    # 'email',          # ❌ 不能排除：uvicorn 需要
    'xml.dom',          # XML DOM（如果不用）
    'xml.sax',          # XML SAX（如果不用）
    'pdb',              # 调试器
    'profile',          # 性能分析
    'pstats',           # 性能统计
]

excludes = [name for name in excludes if name not in ('xml.dom', 'xml.sax')]

if not BUNDLE_PLAYWRIGHT:
    excludes += [
        'playwright',
        'playwright.sync_api',
        'playwright.async_api',
        'playwright._impl',
    ]

a = Analysis(
    ['src/main.py'],
    pathex=[],
    binaries=binaries,  # 使用收集的二进制文件
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,  # 应用排除列表
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

use_upx = sys.platform == 'win32'

icon_path = None
if sys.platform == 'win32':
    icon_path = os.path.join('..', 'resources', 'icons', 'icon.ico')
elif sys.platform == 'darwin':
    candidate_icon = os.path.join('..', 'resources', 'icon.icns')
    if os.path.exists(candidate_icon):
        icon_path = candidate_icon

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VidFlow-Backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=use_upx,
    console=True,  # 保留控制台窗口以查看日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=use_upx,
    upx_exclude=[],
    name='VidFlow-Backend',
)
