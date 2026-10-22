# VidFlow Desktop 1.0.0

> 视频下载器桌面版，采用 **Electron + Python FastAPI** 架构

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/vidflow/desktop)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## ✨ 特性

- 🎥 **多平台支持**：YouTube、Bilibili、抖音、小红书、TikTok 等主流平台
- 🍪 **自动 Cookie 获取**：内置 Selenium，一键登录并提取 Cookie
- 🤖 **AI 字幕生成**：基于 `faster-whisper`，按需安装
- 🌍 **字幕翻译**：支持多语言字幕翻译
- 🧰 **统一工具管理**：FFmpeg、yt-dlp、AI 工具集中管理
- ⚡ **体积优化**：Windows 优化安装包实测约 `151.71 MB`，首次使用再按需下载工具
- 🖥️ **跨平台**：支持 Windows、macOS、Linux

## 🆕 1.0.0 重点更新

### 🍪 自动 Cookie 获取
- ✅ 一键启动浏览器，登录后自动提取 Cookie
- ✅ 密码只在真实浏览器中输入，不经过应用
- ✅ 支持小红书、抖音、TikTok、B 站、YouTube、Twitter、Instagram
- ✅ UI 全程图形化配置，无需手动改文件

详见 [自动 Cookie 获取指南](Docs/AUTO_COOKIE_GUIDE.md)

### 📦 工具按需安装
- ✅ FFmpeg、yt-dlp、Playwright、AI 工具按需下载
- ✅ 减少基础安装包体积，降低首包下载成本
- ✅ 支持在“系统设置 -> 工具管理”中统一安装和维护

详见 [AI 工具指南](Docs/AI_TOOLS_GUIDE.md)

## 🏗️ 技术栈

### 前端
- **Electron** 28
- **React** 18
- **TypeScript**
- **Vite**

### 后端
- **Python** 3.8-3.11（推荐 3.11）
- **FastAPI**
- **yt-dlp**
- **Selenium**
- **faster-whisper**（可选）
- **SQLite**

## 🚀 快速开始

### 方式 1：使用脚本（推荐）

```bash
# Windows
scripts\dev\setup.bat              # 安装 Node / Python 依赖并创建 venv
scripts\dev\start.bat              # 启动开发环境

# 打包
scripts\build\build-optimized.bat  # 推荐：精简构建，产物更小
scripts\build\build-release.bat    # 交互式发布构建菜单
```

### 方式 2：手动安装

#### 环境要求

- Node.js >= 18
- Python 3.8-3.11（推荐 3.11）
- ⚠️ 不支持 Python 3.12+ 的 AI 字幕能力

#### 安装步骤

```bash
# 1. 安装 Node 依赖
npm install
cd frontend && npm install && cd ..

# 2. 创建 Python 虚拟环境
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 3. 启动开发
npm run dev
```

## 📦 打包应用

软件内置更新使用 GitHub Releases，支持启动检查、手动检查、下载校验和安装提示。Windows、Intel Mac 与 Apple Silicon Mac 的发布、签名配置和打包步骤见 [GitHub Releases 更新与 Mac 打包](Docs/GITHUB_RELEASE_UPDATES.md)。

### 推荐：Windows 优化构建

```bash
scripts\build\build-optimized.bat
```

这个脚本会自动完成：

- 清理旧的构建输出和 Python 缓存
- 构建前端生产包
- 以精简模式打包后端
- 用 `electron-builder` 生成 Windows 安装包
- 将详细日志写入 `build-logs/optimized/`

优化构建当前实测产物（2026-03-21）：

- 安装包：`dist-output/VidFlow Setup 1.0.0.exe`，约 `151.71 MB`
- 解包目录：`dist-output/win-unpacked`，约 `431.44 MB`
- 后端包：`backend/dist/VidFlow-Backend`，约 `158.69 MB`

说明：

- 默认启用 `VIDFLOW_BUNDLE_TOOLS=0`
- 默认启用 `VIDFLOW_BUNDLE_PLAYWRIGHT=0`
- FFmpeg、yt-dlp、Playwright 会在首次使用相关功能时再安装

### 交互式发布构建

```bash
scripts\build\build-release.bat
```

适合需要手动选择“仅构建后端 / 仅构建前端 / 仅打包 Electron”的场景。

### 手动打包

```bash
# 1. 构建前端
cd frontend && npm run build && cd ..

# 2. 精简打包后端
set VIDFLOW_BUNDLE_TOOLS=0
set VIDFLOW_BUNDLE_PLAYWRIGHT=0
npm run build:backend

# 3. 打包 Electron
npm run build:electron
```

详见 [打包准备清单](PACKAGE_READY.md)

## 📁 项目结构

```text
VidFlow/
├── electron/          # Electron 主进程
├── frontend/          # React 前端
├── backend/           # Python FastAPI 后端
├── scripts/           # Windows / macOS 构建与开发脚本
├── resources/         # 图标等资源
└── package.json
```

## 🔧 常用命令

```bash
npm run dev              # 启动完整开发环境
npm run frontend:dev     # 仅启动前端
npm run backend:dev      # 仅启动后端
npm run electron:dev     # 仅启动 Electron
npm run build:backend    # 打包后端
npm run build:frontend   # 构建前端
npm run build:electron   # 仅打包 Electron
```

## 📚 文档

### API 文档

后端启动后访问：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 项目文档

- [快速开始](QUICK_START.md)
- [打包准备](PACKAGE_READY.md)
- [AI 工具指南](Docs/AI_TOOLS_GUIDE.md)
- [前端实施](Docs/FRONTEND_IMPLEMENTATION.md)
- [脚本说明](scripts/docs/README.md)
- [变更日志](Docs/CHANGELOG_AI_TOOLS.md)

## 🐛 故障排除

### Python 后端无法启动

1. 确认 `backend/venv` 已创建并可激活
2. 确认已安装依赖：`pip install -r backend/requirements.txt`
3. 如果用了 Python 3.12+，请切回 Python 3.11

### Electron 无法连接后端

1. 先运行 `scripts\dev\start.bat`
2. 检查 `backend\data\backend_port.json` 是否生成
3. 查看 Electron 或后端控制台日志

### 优化构建失败

1. 查看 `build-logs/optimized/frontend-build.log`
2. 查看 `build-logs/optimized/backend-build.log`
3. 查看 `build-logs/optimized/electron-build.log`

### FFmpeg / yt-dlp 相关错误

- 默认会在首次使用相关功能时自动下载
- 如果网络受限，可手动放入 `backend\tools\bin\`
- 也可以通过应用内“系统设置 -> 工具管理”重新安装

## 📄 许可证

MIT License

## 🙏 致谢

- [Electron](https://www.electronjs.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [faster-whisper](https://github.com/guillaumekln/faster-whisper)

---

**VidFlow Team** © 2026
