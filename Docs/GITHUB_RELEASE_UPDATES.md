# GitHub Releases 内置更新与 Mac 打包

应用默认从 `ShuaiJustin/VidFlow` 的最新正式 GitHub Release 检查更新，保留现有更新器、Ed25519 签名校验、SHA-512 文件校验、断点续传和进度提示。无需部署更新服务器。

## 用户操作

1. 开启设置中的自动更新后，启动约 5 秒检查一次，此后每 4 小时检查；也可点击设置中的“检查更新”。
2. 发现新版后展示版本、更新说明和安装包大小，点击“下载更新”下载。
3. Windows 下载并校验完成后，点击“立即重启”，应用启动 NSIS 安装器并退出。
4. Mac 下载并校验完成后，点击“打开安装包”，打开对应架构的 DMG 并退出应用；将 VidFlow 拖到“应用程序”替换旧版，再重新启动。

网络错误、GitHub 限流、签名错误会作为检查失败显示，不会显示“已是最新版本”。如果最新 Release 缺少本机架构的更新描述文件，则显示暂无适用更新包。草稿和预发布不会向正式版用户推送。当前发布流水线覆盖 Windows x64、Mac arm64 和 Mac x64。

## 首次配置签名

仓库的 Actions Secret `VIDFLOW_UPDATE_SIGNING_KEY` 保存 Ed25519 私钥 PEM，必须与应用内的 `electron/update-public-key.pem` 配对。签名用于确认更新描述文件来自发行者；SHA-512 确认下载到的安装包与描述文件一致。

- 已有配套私钥时，将它保存在仓库外，用 GitHub CLI 上传：

  ```powershell
  Get-Content -Raw 'C:\private\vidflow-update-private.pem' | gh secret set VIDFLOW_UPDATE_SIGNING_KEY --repo ShuaiJustin/VidFlow
  ```

- 如果原私钥遗失，必须生成新密钥对、替换应用中的公钥，并把新私钥保存到 GitHub Secret。使用新公钥的首个版本需要手动安装；旧公钥客户端无法验证新密钥签名。不要把私钥加入 Git，也不要把私钥放进安装包。
- Release 工作流会在开始构建前验证 Secret 存在且与公钥配对；缺失或不匹配直接失败，不会发布未签名的更新。

这些是更新元数据签名，Mac 现有的 ad-hoc 应用签名继续保留。当前流水线未配置 Apple Developer ID 签名或 Apple 公证，因此不能将产物视为已通过 Apple 公证的发行包。

## 发布新版本

先将本次更新器、打包配置和工作流改动提交到 GitHub。版本以根目录 `package.json` 为准，现有版本为 `1.0.7`。发布下一版时例如执行：

```powershell
npm version 1.1.0 --no-git-tag-version
git add package.json package-lock.json
git commit -m "chore: release 1.1.0"
git tag v1.1.0
git push origin HEAD
git push origin v1.1.0
```

推送 `v*` 标签会启动 `.github/workflows/release.yml`：

1. 检查标签与 package.json 版本一致、签名私钥有效，生成共享更新说明。
2. Windows 原生构建 NSIS 安装包。
3. 复用 `.github/workflows/build-macos.yml`，在 Apple Silicon 和 Intel Mac runner 上分别构建 Electron 与 Python 后端，保留架构、签名、托盘图标、后端和工具安装冒烟验证。
4. 为三个安装包分别生成签名更新描述文件。
5. 三种构建全部成功后创建草稿 Release、上传文件，再公开为最新正式 Release，避免客户端看到不完整的更新。

已存在远程标签时，也可以在 GitHub Actions 的 **Release → Run workflow** 输入标签重新启动，或在 Windows 执行 `npm run release:github` / `scripts\release\upload-release.bat`。已存在同名 Release 时，脚本不会覆盖，需先检查原 Release；发布中断留下的草稿可以在 GitHub 中检查和处理后重试。

标准 Release 文件：

| 系统 | 安装包 | 更新描述文件 |
| --- | --- | --- |
| Windows x64 | `VidFlow-Setup-1.1.0-x64.exe` | `vidflow-update-win32-x64.json` |
| Mac Apple Silicon | `VidFlow-1.1.0-macOS-arm64.dmg` | `vidflow-update-darwin-arm64.json` |
| Mac Intel | `VidFlow-1.1.0-macOS-x64.dmg` | `vidflow-update-darwin-x64.json` |

安装包和描述文件必须属于同一个标签。客户端同时核对签名、版本、系统、架构、文件名、Release 资源 URL、大小和 SHA-512，安装前会再次检查 Release 及文件完整性。

## 单独打包 Mac

GitHub Actions 中运行 **Build macOS installers → Run workflow**。无需元数据签名 Secret 即可构建用于手动安装的两个 DMG；完成后在对应工作流的 Artifacts 下载 `VidFlow-macOS-arm64` 和 `VidFlow-macOS-x64`。单独构建不发布 Release、不生成更新元数据。

在 Mac 本地打包时，分别使用对应架构的 Mac：

```bash
npm ci
npm ci --prefix frontend
python3.11 -m venv backend/venv
backend/venv/bin/python -m pip install -r backend/requirements.txt
VIDFLOW_BUNDLE_TOOLS=0 npm run build:all
npx electron-builder --mac dmg --arm64 --publish never --config electron-builder.json
# Intel Mac 使用 --x64
```

Windows 本机无法完成本项目的 Mac 原生 Python 后端、DMG 和 codesign 验证，应使用 Mac 或上述 macOS Actions runner。

## 手动生成更新描述文件

已有安装包时：

```powershell
$env:VIDFLOW_UPDATE_SIGNING_KEY_PATH = 'C:\private\vidflow-update-private.pem'
npm run release:metadata -- --file 'dist-output/VidFlow-Setup-1.1.0-x64.exe' --platform win32 --arch x64 --notes-file release-notes.md
```

脚本从 package.json 读取版本。Mac 使用 `--platform darwin --arch arm64` 或 `--arch x64`，并提供对应 DMG。必须在 Release 同时上传安装包和生成的 JSON。发布后尽量避免替换同名安装包。

可通过 `VIDFLOW_GITHUB_REPOSITORY=owner/repo` 切换到其他公开 GitHub 仓库，并在生成元数据时使用 `--repository owner/repo`。旧的 `advanced.update_server_url` 和 `VIDFLOW_UPDATE_SERVER_URL` 不再决定桌面更新源。客户端不包含 GitHub Token。
