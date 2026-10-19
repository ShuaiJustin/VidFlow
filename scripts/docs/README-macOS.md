# macOS 脚本使用指南

本目录包含用于 macOS 开发和构建的自动化脚本。

## 当前云端 Mac 构建

GitHub Actions 的 `Build macOS installers` 工作流分别在 Apple Silicon 和
Intel Mac 上构建，生成各自架构的 DMG，不是通用二进制安装包。
v1.0.6 修复了顶部菜单栏图标，使用支持深浅色和 Retina 的 Template 图片；
Mac 界面暂时隐藏视频号入口，现有微信自动捕获实现仍仅面向 Windows。
工作流会检查平台导航、打包后的原生图标及后端启动情况。

v1.0.7 的工具下载显式加载随应用打包的 CA 证书包，同时保留系统和自定义
证书信任。Apple Silicon 使用原生 ARM64 FFmpeg/FFprobe 下载源。
云端构建会在不依赖构建机 CA 路径的环境中实际安装并执行 FFmpeg、FFprobe
和 yt-dlp，验证 HTTPS 下载与二进制架构。

## 📋 脚本列表

### 1. `dev/setup-mac.sh` - 环境初始化
一键配置开发环境，安装所有必要的依赖。

**功能**:
- 检查并安装 Homebrew
- 安装 Node.js、Python 3.11、Git
- 安装项目依赖（npm + Python）
- 生成 macOS 图标

**使用方法**:
```bash
chmod +x scripts/dev/setup-mac.sh
./scripts/dev/setup-mac.sh
```

**首次使用必须运行此脚本！**

---

### 2. `dev/dev-mac.sh` - 开发模式
启动完整的开发环境（前端 + 后端 + Electron）。

**功能**:
- 启动 FastAPI 后端服务器
- 启动 Vite 前端开发服务器
- 启动 Electron 主进程
- 自动热重载

**使用方法**:
```bash
chmod +x scripts/dev/dev-mac.sh
./scripts/dev/dev-mac.sh
```

**或使用 npm 命令**:
```bash
npm run dev
```

**停止**: 按 `Ctrl+C`

---

### 3. `build/build-mac.sh` - 生产构建
构建可分发的 macOS DMG 安装包。

**功能**:
- 检查环境依赖
- 构建前端（Vite）
- 打包后端（PyInstaller）
- 生成 macOS 应用（Electron Builder）
- 输出 .dmg 安装包

**使用方法**:
```bash
chmod +x scripts/build/build-mac.sh
./scripts/build/build-mac.sh
```

**或使用 npm 命令**:
```bash
npm run build:mac
```

**输出**: `dist-output/VidFlow-{version}.dmg`

---

## 🚀 快速开始

### 全新安装
```bash
# 1. 克隆项目
git clone <repository-url>
cd VidFlow

# 2. 赋予脚本执行权限
chmod +x scripts/dev/*.sh scripts/build/*.sh

# 3. 初始化环境
./scripts/dev/setup-mac.sh

# 4. 启动开发模式
./scripts/dev/dev-mac.sh
```

### 日常开发
```bash
# 启动开发模式
npm run dev

# 或直接运行脚本
./scripts/dev/dev-mac.sh
```

### 构建发布版本
```bash
# 构建 macOS 应用
npm run build:mac

# 或直接运行脚本
./scripts/build/build-mac.sh
```

---

## 📦 构建说明

### 通用二进制 (Universal Binary)
构建脚本会自动生成包含 Intel 和 Apple Silicon 两种架构的通用二进制：
- **x64**: Intel Mac
- **arm64**: Apple Silicon (M1/M2/M3)

单个 DMG 文件可在所有 Mac 上运行！

### 构建要求
- **macOS**: 10.15 或更高
- **Xcode Command Line Tools**: `xcode-select --install`
- **Node.js**: 16.x 或更高
- **Python**: 3.11（推荐）

### 构建产物
```
dist-output/
├── VidFlow-{version}.dmg          # DMG 安装包
├── VidFlow-{version}-mac.zip      # ZIP 压缩包（可选）
└── mac-universal/                 # 应用包目录
    └── VidFlow.app
```

---

## 🍎 Apple Silicon 支持

### 自动优化
所有脚本已针对 Apple Silicon 优化：
- ✅ 原生 ARM64 编译
- ✅ Metal GPU 加速（AI 工具）
- ✅ Rosetta 2 兼容
- ✅ 通用二进制输出

### AI 工具支持
在 M1/M2/M3 Mac 上：
- PyTorch 自动使用 MPS (Metal Performance Shaders)
- faster-whisper 使用 ARM64 优化版本
- 性能接近 NVIDIA GPU

---

## ⚠️ 常见问题

### 1. 权限错误
```bash
# 赋予执行权限
chmod +x scripts/dev/*.sh scripts/build/*.sh
```

### 2. Python 版本问题
```bash
# AI 功能需要 Python 3.11
brew install python@3.11

# 重新创建虚拟环境
cd backend
rm -rf venv
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Node.js 版本过旧
```bash
# 更新 Node.js
brew upgrade node
```

### 4. 构建失败
```bash
# 清理并重新构建
rm -rf node_modules frontend/node_modules backend/venv
./scripts/dev/setup-mac.sh
./scripts/build/build-mac.sh
```

### 5. Electron 打包失败
```bash
# 安装必要工具
xcode-select --install

# 清理缓存
rm -rf ~/Library/Caches/electron
rm -rf ~/Library/Caches/electron-builder
```

---

## 🔧 高级选项

### 只构建特定架构
编辑 `electron-builder.json`:
```json
{
  "mac": {
    "target": [
      {
        "target": "dmg",
        "arch": ["x64"]  // 或 ["arm64"]
      }
    ]
  }
}
```

### 跳过代码签名
默认配置已禁用签名（`gatekeeperAssess: false`）。

如需启用签名：
```bash
export CSC_LINK="/path/to/certificate.p12"
export CSC_KEY_PASSWORD="your-password"
npm run build:mac
```

### 调试模式
```bash
# 启用详细日志
DEBUG=* npm run build:mac

# 保留构建文件
electron-builder --mac --config electron-builder.json --dir
```

---

## 📚 相关资源

- [Electron Builder 文档](https://www.electron.build/)
- [PyInstaller 文档](https://pyinstaller.org/)
- [Apple Silicon 开发指南](https://developer.apple.com/documentation/apple-silicon)
- [Homebrew 官网](https://brew.sh/)

---

## 💡 提示

### 开发效率
- 使用 `npm run dev` 启动开发模式
- 前端支持热重载
- 后端修改后自动重启

### 性能优化
- Apple Silicon 用户会自动获得 Metal 加速
- 构建时使用 SSD 可显著提升速度
- 推荐至少 8GB RAM

### 首次运行
构建的应用首次运行时，macOS 会显示警告：
1. 右键点击应用
2. 选择"打开"
3. 在对话框中确认

---

**最后更新**: 2025-01-24
