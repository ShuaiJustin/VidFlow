const { app, BrowserWindow, ipcMain, dialog, Notification, Tray, Menu } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const { CustomUpdater } = require('./updater-custom');
const { confirmHealthyUpdate } = require('./updater-health');
const { removeBackendPortFileAtPath } = require('./backend-port-file');

// 设置应用名称和用户模型 ID（改善开发模式下的通知和任务栏体验）
// app.setName('VidFlow Desktop');
// if (process.platform === 'win32') {
//   app.setAppUserModelId('com.vidflow.desktop');
// }

// 全局异常处理，防止应用崩溃
process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error);
  // 不要让应用崩溃
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
  // 不要让应用崩溃
});

// ============================================
// 单实例锁定 - 防止重复启动
// ============================================
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  // 如果获取锁失败，说明已有实例在运行，直接退出
  console.log('Another instance is already running. Quitting...');
  app.quit();
} else {
  // 当第二个实例启动时，聚焦到已有窗口
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    console.log('Second instance detected, focusing existing window...');
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      if (!mainWindow.isVisible()) {
        mainWindow.show();
      }
      mainWindow.focus();
    }
  });
}

let mainWindow;
let updateWindowReady = false;
let updateHealthCheck = null;
let pythonProcess;
let tray = null;
let backendPort = null;
let backendReady = false;
let backendError = null; // 新增: 记录后端启动错误
let updater = null;
let updaterError = null;
let backendShutdownPromise = null;
let backendQuitCleanupDone = false;
let backendShutdownExpected = false;
let backendShutdownReason = null;

function formatBackendExitDetails(code, signal) {
  if (code !== null && code !== undefined) {
    return `退出码: ${code}`;
  }

  if (signal) {
    return `信号: ${signal}`;
  }

  return '退出码: null';
}

// 获取应用图标路径的辅助函数
function getIconPath() {
  const isDev = !app.isPackaged;
  if (isDev) {
    if (process.platform === 'win32') {
      return path.join(__dirname, '../resources/icons/icon.ico');
    } else if (process.platform === 'darwin') {
      return path.join(__dirname, '../resources/icon.icns');
    } else {
      return path.join(__dirname, '../resources/icons/icon.png');
    }
  } else {
    if (process.platform === 'win32') {
      return path.join(process.resourcesPath, 'icons', 'icon.ico');
    } else if (process.platform === 'darwin') {
      return path.join(process.resourcesPath, 'icons', 'icon.png');
    } else {
      return path.join(process.resourcesPath, 'icons', 'icon.png');
    }
  }
}

// 获取端口文件路径
function getPortFilePath() {
  const isDev = !app.isPackaged;
  let portFilePath;

  if (isDev) {
    // 开发模式：使用项目目录
    portFilePath = path.join(__dirname, '../backend/data/backend_port.json');
  } else {
    // 生产模式：使用用户数据目录
    if (process.platform === 'win32') {
      const appdata = process.env.APPDATA || path.join(require('os').homedir(), 'AppData', 'Roaming');
      portFilePath = path.join(appdata, 'VidFlow', 'data', 'backend_port.json');
    } else if (process.platform === 'darwin') {
      portFilePath = path.join(require('os').homedir(), 'Library', 'Application Support', 'VidFlow', 'data', 'backend_port.json');
    } else {
      portFilePath = path.join(require('os').homedir(), '.local', 'share', 'VidFlow', 'data', 'backend_port.json');
    }
  }

  return portFilePath;
}

function getConfigFilePath() {
  const isDev = !app.isPackaged;

  if (isDev) {
    return path.join(__dirname, '../backend/data/config.json');
  }

  if (process.platform === 'win32') {
    const appdata = process.env.APPDATA || path.join(require('os').homedir(), 'AppData', 'Roaming');
    return path.join(appdata, 'VidFlow', 'data', 'config.json');
  }

  if (process.platform === 'darwin') {
    return path.join(require('os').homedir(), 'Library', 'Application Support', 'VidFlow', 'data', 'config.json');
  }

  return path.join(require('os').homedir(), '.local', 'share', 'VidFlow', 'data', 'config.json');
}

function readUpdatePreferences() {
  const defaultPreferences = {
    autoUpdate: true,
    updateServerUrl: String(process.env.VIDFLOW_UPDATE_SERVER_URL || '').trim()
  };

  try {
    const configFilePath = getConfigFilePath();
    if (!fs.existsSync(configFilePath)) {
      return defaultPreferences;
    }

    const raw = fs.readFileSync(configFilePath, 'utf8');
    const config = JSON.parse(raw);

    return {
      autoUpdate: config?.advanced?.auto_update !== false,
      updateServerUrl: String(config?.advanced?.update_server_url || process.env.VIDFLOW_UPDATE_SERVER_URL || '').trim()
    };
  } catch (error) {
    console.warn('[Update] Failed to read update preferences, using defaults:', error.message || error);
    return defaultPreferences;
  }
}

function removeBackendPortFile() {
  removeBackendPortFileAtPath(getPortFilePath());
}

function readBackendPortConfigFromFile() {
  const portFilePath = getPortFilePath();
  if (!fs.existsSync(portFilePath)) {
    throw new Error(`Port file not found: ${portFilePath}`);
  }

  const raw = fs.readFileSync(portFilePath, 'utf8');
  const config = JSON.parse(raw);
  const port = Number(config.port);

  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`Invalid backend port in file: ${config.port}`);
  }

  return {
    ...config,
    port,
  };
}

function readBackendPortFromFile(expectedStartupToken = null) {
  const config = readBackendPortConfigFromFile();

  if (expectedStartupToken && config.startup_token !== expectedStartupToken) {
    throw new Error(`Backend port file token mismatch: ${config.startup_token || 'missing'}`);
  }

  return config.port;
}

function checkBackendHealth(port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path: '/health',
        method: 'GET',
        timeout: timeoutMs,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk) => {
          body += chunk.toString('utf8');
        });
        res.on('end', () => {
          if (res.statusCode !== 200) {
            resolve(false);
            return;
          }
          if (!body) {
            resolve(true);
            return;
          }
          try {
            const parsed = JSON.parse(body);
            resolve(parsed.status === 'healthy' || parsed.backend === 'ready');
          } catch (error) {
            resolve(true);
          }
        });
      }
    );

    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function postBackendRequest(port, requestPath, timeoutMs = 2500) {
  return new Promise((resolve) => {
    if (!port) {
      resolve(false);
      return;
    }

    const req = http.request(
      {
        hostname: '127.0.0.1',
        port,
        path: requestPath,
        method: 'POST',
        timeout: timeoutMs,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk) => {
          body += chunk.toString('utf8');
        });
        res.on('end', () => {
          if (!(res.statusCode >= 200 && res.statusCode < 300)) {
            resolve(false);
            return;
          }

          if (!body) {
            resolve(true);
            return;
          }

          try {
            const parsed = JSON.parse(body);
            if (typeof parsed.success === 'boolean') {
              resolve(parsed.success);
              return;
            }
          } catch (error) {
            // Ignore non-JSON responses from the backend cleanup endpoint.
          }

          resolve(true);
        });
      }
    );

    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

async function stopBackendServicesForQuit(reason = 'app_quit') {
  if (backendShutdownPromise) {
    return backendShutdownPromise;
  }

  backendShutdownExpected = true;
  backendShutdownReason = reason;

  backendShutdownPromise = (async () => {
    let resolvedPort = backendPort;
    if (!resolvedPort) {
      try {
        resolvedPort = readBackendPortFromFile();
      } catch (error) {
        resolvedPort = null;
      }
    }

    if (resolvedPort) {
      const stopped = await postBackendRequest(
        resolvedPort,
        '/api/channels/sniffer/stop',
        3000,
      );
      console.log(`[Quit] Backend sniffer stop (${reason}): ${stopped ? 'ok' : 'skipped'}`);
    }

    if (pythonProcess && !pythonProcess.killed) {
      const processToStop = pythonProcess;
      pythonProcess = null;

      await new Promise((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          resolve();
        };

        const timer = setTimeout(() => {
          finish();
        }, 1500);

        processToStop.once('close', () => {
          clearTimeout(timer);
          finish();
        });

        try {
          processToStop.kill();
        } catch (error) {
          clearTimeout(timer);
          finish();
        }
      });
    }

    backendReady = false;
    backendPort = null;
  })().finally(() => {
    backendQuitCleanupDone = true;
  });

  return backendShutdownPromise;
}

async function findExistingBackend(maxAttempts = 1, intervalMs = 500) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const port = readBackendPortFromFile();
      const healthy = await checkBackendHealth(port);
      if (healthy) {
        backendPort = port;
        backendReady = true;
        backendError = null;
        console.log(`✅ Reusing existing backend at port: ${port}`);
        return port;
      }
    } catch (error) {
      if (attempt === maxAttempts) {
        console.log(`ℹ️ Existing backend not available: ${error.message}`);
      }
    }

    if (attempt < maxAttempts) {
      await sleep(intervalMs);
    }
  }

  return null;
}

// Python 后端进程管理
function startPythonBackend() {
  return new Promise((resolve, reject) => {
    let settled = false;
    const startupToken = crypto.randomUUID();

    backendShutdownExpected = false;
    backendShutdownReason = null;
    backendShutdownPromise = null;
    backendQuitCleanupDone = false;

    const safeResolve = (port) => {
      if (!settled) {
        settled = true;
        backendPort = port;
        backendReady = true;
        backendError = null; // 清除错误状态
        resolve(port);
      }
    };

    const safeReject = (error) => {
      if (!settled) {
        settled = true;
        backendError = error.message || '后端启动失败'; // 记录错误
        reject(error);
      }
    };

    const isDev = !app.isPackaged;

    console.log('========================================');
    console.log('Starting Python Backend...');
    console.log(`Mode: ${isDev ? 'Development' : 'Production'}`);
    console.log(`Platform: ${process.platform}`);
    console.log('========================================');

    if (isDev) {
      // 开发模式：使用 Python 虚拟环境
      const pythonPath = process.platform === 'win32'
        ? path.join(__dirname, '../backend/venv/Scripts/python.exe')
        : path.join(__dirname, '../backend/venv/bin/python');

      const scriptPath = path.join(__dirname, '../backend/src/main.py');

      console.log(`[DEV] Python path: ${pythonPath}`);
      console.log(`[DEV] Script path: ${scriptPath}`);
      console.log(`[DEV] Python exists: ${fs.existsSync(pythonPath)}`);
      console.log(`[DEV] Script exists: ${fs.existsSync(scriptPath)}`);

      // 指定 UTF-8 编码环境和禁用输出缓冲
      const env = {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        PYTHONUNBUFFERED: '1',  // 禁用Python输出缓冲
        VIDFLOW_STARTUP_TOKEN: startupToken,
        // Windows 控制台编码
        PYTHONLEGACYWINDOWSSTDIO: '1'
      };

      pythonProcess = spawn(pythonPath, [scriptPath, '-u'], { env });
    } else {
      // 生产模式：使用打包的可执行文件
      // PyInstaller 打包后的结构：backend/VidFlow-Backend.exe 和相关依赖文件
      const backendPath = process.platform === 'win32'
        ? path.join(process.resourcesPath, 'backend', 'VidFlow-Backend.exe')
        : path.join(process.resourcesPath, 'backend', 'VidFlow-Backend');

      console.log(`[PROD] Backend path: ${backendPath}`);
      console.log(`[PROD] Backend exists: ${fs.existsSync(backendPath)}`);
      console.log(`[PROD] resourcesPath: ${process.resourcesPath}`);

      // 列出 backend 目录的内容以便调试
      const backendDir = path.join(process.resourcesPath, 'backend');
      if (fs.existsSync(backendDir)) {
        console.log(`[PROD] Backend directory contents:`);
        try {
          const files = fs.readdirSync(backendDir);
          files.slice(0, 20).forEach(file => console.log(`  - ${file}`));
          if (files.length > 20) console.log(`  ... and ${files.length - 20} more files`);
        } catch (err) {
          console.error(`[PROD] Error reading backend directory: ${err.message}`);
        }
      } else {
        console.error(`[PROD] Backend directory does not exist: ${backendDir}`);
      }

      if (!fs.existsSync(backendPath)) {
        console.error(`❌ Backend executable not found at: ${backendPath}`);
        reject(new Error('Backend executable not found'));
        return;
      }

      if (process.platform !== 'win32') {
        try {
          fs.chmodSync(backendPath, 0o755);
          console.log(`[PROD] Set executable permission for ${backendPath}`);
        } catch (error) {
          console.error('[PROD] Failed to set executable permission:', error);
        }
      }

      // 指定 UTF-8 编码环境和禁用输出缓冲
      const env = {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        PYTHONUNBUFFERED: '1',  // 禁用Python输出缓冲
        VIDFLOW_STARTUP_TOKEN: startupToken,
        PYTHONLEGACYWINDOWSSTDIO: '1'
      };

      pythonProcess = spawn(backendPath, [], { env });
    }

    console.log('Backend process spawned, waiting for startup...');

    pythonProcess.stdout.on('data', (data) => {
      // 使用 UTF-8 解码 Python 输出
      const output = data.toString('utf8');
      console.log(`[BACKEND STDOUT] ${output}`);

      // 检测端口信息输出（支持多种格式）
      if (output.includes('Uvicorn running on') || output.includes('Backend startup completed')) {
        const match = output.match(/http:\/\/127\.0\.0\.1:(\d+)/);
        if (match && !backendReady) {
          const port = parseInt(match[1]);
          console.log(`✅ Backend ready on port: ${port}`);
          safeResolve(port);
        }
      }

      // 如果看到端口文件写入日志，立即尝试读取
      if (output.includes('Server will start on port:')) {
        const portMatch = output.match(/port:\s*(\d+)/);
        if (portMatch && !backendReady) {
          console.log(`Detected port from log: ${portMatch[1]}, reading port file...`);
          setTimeout(() => {
            if (!backendReady) {
              tryReadPortFile(startupToken).then(resolve).catch(() => {});
            }
          }, 500);
        }
      }
    });

    pythonProcess.stderr.on('data', (data) => {
      // 使用 UTF-8 解码 Python 错误输出
      const errorOutput = data.toString('utf8');
      console.error(`[BACKEND STDERR] ${errorOutput}`);

      // 检查是否是启动信息（有些日志会输出到 stderr）
      if (errorOutput.includes('Uvicorn running on')) {
        const match = errorOutput.match(/http:\/\/127\.0\.0\.1:(\d+)/);
        if (match) {
          const port = parseInt(match[1]);
          console.log(`✅ Backend ready on port (from stderr): ${port}`);
          safeResolve(port);
        }
      }
    });

    pythonProcess.on('close', (code, signal) => {
      const exitDetails = formatBackendExitDetails(code, signal);
      const expectedExit = backendShutdownExpected || app.isQuitting || backendQuitCleanupDone;

      console.log(`========================================`);
      console.log(`${expectedExit ? 'ℹ️' : '❌'} Python process exited (${exitDetails})`);
      console.log(`========================================`);

      pythonProcess = null;
      backendReady = false;
      backendPort = null; // 清空端口

      if (expectedExit) {
        console.log(`[Backend] Expected shutdown detected (${backendShutdownReason || 'app_quit'})`);
        backendError = null;
      } else {
        backendError = `后端进程退出 (${exitDetails})`; // 记录错误
        backendShutdownPromise = null;
      }

      // 通知前端后端已断开
      if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
        mainWindow.webContents.send('backend-disconnected', {
          code,
          signal: signal || null,
          expected: expectedExit,
          message: expectedExit ? null : backendError,
        });
      }

      // 如果后端意外退出，通知用户
      if (!expectedExit && code !== 0 && mainWindow && !mainWindow.isDestroyed()) {
        dialog.showMessageBoxSync(mainWindow, {
          type: 'error',
          title: '后端进程异常退出',
          message: `后端进程异常退出 (${exitDetails})\n应用可能无法正常工作，建议重启应用。`,
          buttons: ['确定']
        });
      }
    });

    pythonProcess.on('error', (error) => {
      console.error('========================================');
      console.error('❌ Python process error:', error);
      console.error('========================================');
      safeReject(error);
    });

    // 立即开始尝试读取端口文件（不等待日志输出）
    // 因为 Python 进程会立即写入端口文件
    const startPortPolling = () => {
      let pollAttempts = 0;
      const maxPollAttempts = 20; // 最多轮询 10 秒（每次 500ms）

      const pollInterval = setInterval(() => {
        pollAttempts++;

        if (settled || backendReady) {
          clearInterval(pollInterval);
          return;
        }

        tryReadPortFile(startupToken)
          .then((port) => {
            clearInterval(pollInterval);
            if (!settled && !backendReady) {
              console.log(`✅ Backend ready via port file polling (attempt ${pollAttempts}): ${port}`);
              safeResolve(port);
            }
          })
          .catch(() => {
            // 继续轮询
            if (pollAttempts >= maxPollAttempts) {
              clearInterval(pollInterval);
              if (!settled && !backendReady) {
                console.error('❌ Port file polling timeout');
                safeReject(new Error('Backend port file not found after polling'));
              }
            }
          });
      }, 500); // 每 500ms 轮询一次
    };

    // 立即开始轮询（不等待）
    setTimeout(startPortPolling, 100);

    // 超时处理 - 增加到15秒，给予数据库初始化足够时间
    setTimeout(() => {
      if (!settled && !backendReady) {
        console.warn('⚠️ Backend startup timeout (15s), trying final port file read...');
        tryReadPortFile(startupToken).then(safeResolve).catch(safeReject);
      }
    }, 15000);
  });
}

// 尝试从文件读取端口信息
function tryReadPortFile(expectedStartupToken = null) {
  return new Promise((resolve, reject) => {
    try {
      const port = readBackendPortFromFile(expectedStartupToken);
      checkBackendHealth(port).then((healthy) => {
        if (!healthy) {
          reject(new Error(`Backend on port ${port} is not healthy`));
          return;
        }

        backendPort = port;
        backendReady = true;
        backendError = null;
        console.log(`📄 Read healthy backend port from file: ${backendPort}`);
        resolve(backendPort);
      });
    } catch (error) {
      reject(error);
    }
  });
}

// 创建主窗口
function createWindow() {
  updateWindowReady = false;
  const isDev = !app.isPackaged;

  // 根据平台选择图标格式
  let iconPath;
  if (isDev) {
    // 开发模式
    if (process.platform === 'win32') {
      iconPath = path.join(__dirname, '../resources/icons/icon.ico');
    } else if (process.platform === 'darwin') {
      // macOS 使用 .icns 格式
      iconPath = path.join(__dirname, '../resources/icon.icns');
    } else {
      iconPath = path.join(__dirname, '../resources/icons/icon.png');
    }
  } else {
    // 生产模式
    if (process.platform === 'win32') {
      iconPath = path.join(process.resourcesPath, 'icons', 'icon.ico');
    } else if (process.platform === 'darwin') {
      // macOS: electron-builder 会自动处理 .icns，但窗口图标需要指定
      // 在打包后，.icns 会被嵌入到 .app 中，这里使用 png 作为备用
      iconPath = path.join(process.resourcesPath, 'icons', 'icon.png');
    } else {
      iconPath = path.join(process.resourcesPath, 'icons', 'icon.png');
    }
  }

  console.log('Creating main window...');
  console.log('Window icon path:', iconPath);
  console.log('Icon exists:', fs.existsSync(iconPath));
  console.log('DISABLE_BACKEND_AUTO_START:', process.env.DISABLE_BACKEND_AUTO_START);

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1000,
    minHeight: 600,
    show: false, // 先不显示，等加载完成后再显示
    frame: false, // 隐藏系统标题栏，使用自定义标题栏
    title: 'VidFlow',
    backgroundColor: '#1a1a1a', // 设置背景色，避免白屏闪烁
    transparent: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      devTools: true // 允许打开开发者工具
    },
    icon: iconPath
  });

  console.log('Main window created');

  // 处理新窗口打开（例如点击外部链接）
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    console.log('Opening new window:', url);
    return {
      action: 'allow',
      overrideBrowserWindowOptions: {
        icon: iconPath,
        title: 'VidFlow',
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true
        }
      }
    };
  });

  // 等页面加载完成后再显示窗口，避免白屏
  mainWindow.once('ready-to-show', () => {
    console.log('Window ready to show');
    mainWindow.show();
    mainWindow.focus();
    updateWindowReady = true;
    confirmInstalledUpdateHealth();
  });

  mainWindow.webContents.on('render-process-gone', () => { updateWindowReady = false; });
  mainWindow.webContents.on('did-fail-load', () => { updateWindowReady = false; });

  // isDev 已经在函数开头声明过了
  if (isDev) {
    // 开发模式：加载 Vite 开发服务器
    console.log('[DEV] Loading from Vite dev server');

    // 读取前端端口文件
    const getFrontendPort = () => {
      try {
        const portFile = path.join(__dirname, '../frontend_port.json');
        if (fs.existsSync(portFile)) {
          const portData = JSON.parse(fs.readFileSync(portFile, 'utf-8'));
          console.log(`[DEV] Found frontend port file: ${portData.port}`);
          return portData.port;
        }
      } catch (error) {
        console.log('[DEV] Failed to read frontend port file, using default 5173');
      }
      return 5173; // 默认端口
    };

    // 添加重试逻辑，最多重试5次
    const loadWithRetry = async (retries = 5) => {
      try {
        const port = getFrontendPort();
        const url = `http://localhost:${port}`;
        console.log(`[DEV] Attempting to load from: ${url}`);
        await mainWindow.loadURL(url);
        console.log('[DEV] Successfully loaded from Vite dev server');
      } catch (err) {
        console.error(`[DEV] Failed to load dev server (attempt ${6 - retries}/5):`, err);
        if (retries > 0) {
          console.log(`[DEV] Retrying in 2 seconds...`);
          await new Promise(resolve => setTimeout(resolve, 2000));
          await loadWithRetry(retries - 1);
        } else {
          console.error('[DEV] All retry attempts failed');
          const port = getFrontendPort();
          // 显示友好的错误页面
          const errorHtml = `
            <html>
              <head><style>
                body {
                  background: #1a1a1a;
                  color: #fff;
                  font-family: sans-serif;
                  padding: 40px;
                  text-align: center;
                }
                h1 { color: #ff6b6b; }
                .message { margin: 20px 0; }
                .tip { color: #4ecdc4; margin-top: 20px; }
              </style></head>
              <body>
                <h1>⚠️ 前端开发服务器未启动</h1>
                <div class="message">
                  <p>无法连接到 http://localhost:${port}/</p>
                  <p>请确保前端开发服务器正在运行</p>
                </div>
                <div class="tip">
                  <p>💡 在终端运行: <code>cd frontend && npm run dev</code></p>
                  <p>🔄 或使用: <code>npm run dev</code> 启动完整应用</p>
                  <p>🔄 服务器启动后按 Ctrl+R 刷新此页面</p>
                </div>
              </body>
            </html>
          `;
          mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml));
        }
      }
    };

    loadWithRetry();
    mainWindow.webContents.openDevTools();
  } else {
    // 生产模式：加载构建后的文件
    console.log('[PROD] Loading frontend...');
    console.log('[PROD] __dirname:', __dirname);
    console.log('[PROD] app.isPackaged:', app.isPackaged);
    console.log('[PROD] process.resourcesPath:', process.resourcesPath);

    // 正确的路径：从 app.asar 中加载
    // __dirname 在打包后指向 app.asar/electron
    const frontendPath = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');

    console.log('[PROD] Loading from:', frontendPath);

    mainWindow.loadFile(frontendPath).then(() => {
      console.log('[PROD] Frontend loaded successfully');
    }).catch(err => {
      console.error('[PROD] Failed to load frontend:', err);
      console.error('[PROD] Path:', frontendPath);

      // 显示详细错误信息
      const errorHtml = `
        <html>
          <head><style>
            body {
              background: #1a1a1a;
              color: #fff;
              font-family: 'Segoe UI', sans-serif;
              padding: 40px;
              max-width: 800px;
              margin: 0 auto;
            }
            h1 { color: #ff6b6b; margin-bottom: 20px; }
            .info {
              background: #2a2a2a;
              padding: 15px;
              border-radius: 8px;
              margin: 15px 0;
              font-family: 'Consolas', monospace;
              font-size: 13px;
              line-height: 1.6;
            }
            .label { color: #4CAF50; font-weight: bold; }
          </style></head>
          <body>
            <h1>Failed to Load Frontend</h1>
            <div class="info">
              <div><span class="label">Error:</span> ${err.message}</div>
              <div><span class="label">Path:</span> ${frontendPath}</div>
              <div><span class="label">__dirname:</span> ${__dirname}</div>
              <div><span class="label">resourcesPath:</span> ${process.resourcesPath}</div>
            </div>
            <p>Press <strong>F12</strong> to open DevTools for more details</p>
          </body>
        </html>
      `;
      mainWindow.loadURL(`data:text/html,${encodeURIComponent(errorHtml)}`);
    });

    // F12 打开开发者工具
    mainWindow.webContents.on('before-input-event', (event, input) => {
      if (input.key === 'F12') {
        mainWindow.webContents.toggleDevTools();
        event.preventDefault();
      }
    });
  }

  // 注册右键上下文菜单
  const inputMenu = Menu.buildFromTemplate([
    { role: 'undo', label: '撤销' },
    { role: 'redo', label: '重做' },
    { type: 'separator' },
    { role: 'cut', label: '剪切' },
    { role: 'copy', label: '复制' },
    { role: 'paste', label: '粘贴' },
    { role: 'delete', label: '删除' },
    { type: 'separator' },
    { role: 'selectAll', label: '全选' },
  ]);

  const selectionMenu = Menu.buildFromTemplate([
    { role: 'copy', label: '复制' },
    { type: 'separator' },
    { role: 'selectAll', label: '全选' },
  ]);

  // 禁用原生右键菜单，使用前端自定义菜单
  // mainWindow.webContents.on('context-menu', (_event, params) => {
  //   const menu = params.isEditable ? inputMenu : selectionMenu;
  //   menu.popup({ window: mainWindow });
  // });

  // 添加加载失败处理
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('Failed to load page:', errorCode, errorDescription);
    // 加载失败后显示错误页面而不是退出
    if (errorCode !== -3) { // -3 是用户取消加载，忽略
      const errorHtml = `
        <html>
          <head>
            <style>
              body {
                background: #1a1a1a;
                color: #fff;
                font-family: sans-serif;
                padding: 50px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
              }
              h1 { color: #ff6b6b; }
              .error-code {
                background: #2a2a2a;
                padding: 20px;
                border-radius: 8px;
                margin: 20px 0;
                font-family: monospace;
              }
              .tip { color: #4ecdc4; margin-top: 20px; }
            </style>
          </head>
          <body>
            <h1>⚠️ 前端加载失败</h1>
            <div class="error-code">
              <p><strong>错误代码:</strong> ${errorCode}</p>
              <p><strong>错误描述:</strong> ${errorDescription}</p>
            </div>
            <p class="tip">💡 按 F12 打开开发者工具查看详细信息</p>
            <p class="tip">🔄 重启应用试试</p>
          </body>
        </html>
      `;
      mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml));
    }
  });

  // 监听加载完成
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('Page loaded successfully');
  });

  // 监听 DOM 加载完成
  mainWindow.webContents.on('dom-ready', () => {
    console.log('DOM ready');
  });

  // 防止窗口意外关闭
  mainWindow.on('closed', () => {
    console.log('Window closed event triggered');
    mainWindow = null;
  });

  // 捕获未处理的异常，防止崩溃
  mainWindow.webContents.on('crashed', (event, killed) => {
    console.error('❌ Renderer process crashed!', { killed });

    // 显示错误对话框
    dialog.showMessageBoxSync(mainWindow, {
      type: 'error',
      title: '渲染进程崩溃',
      message: '应用渲染进程崩溃，请重启应用',
      buttons: ['确定'],
      icon: getIconPath()
    });

    // 重新加载窗口
    if (mainWindow) {
      mainWindow.reload();
    }
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    console.error('❌ Render process gone!', details);
    console.error('Reason:', details.reason);
    console.error('Exit code:', details.exitCode);

    if (details.reason !== 'clean-exit') {
      dialog.showMessageBoxSync(mainWindow, {
        type: 'error',
        title: '渲染进程异常退出',
        message: `渲染进程异常退出\n原因: ${details.reason}\n退出码: ${details.exitCode}`,
        buttons: ['确定'],
        icon: getIconPath()
      });
    }
  });

  // 捕获未响应
  mainWindow.webContents.on('unresponsive', () => {
    console.error('❌ Window became unresponsive');

    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'warning',
      title: '应用未响应',
      message: '应用似乎未响应，是否要重新加载？',
      buttons: ['等待', '重新加载'],
      defaultId: 0,
      icon: getIconPath()
    });

    if (choice === 1) {
      mainWindow.reload();
    }
  });

  mainWindow.webContents.on('responsive', () => {
    console.log('✅ Window became responsive again');
  });

  // 监听窗口最大化/还原状态
  mainWindow.on('maximize', () => {
    console.log('Window maximized');
    mainWindow.webContents.send('window-state-changed', { isMaximized: true });
  });

  mainWindow.on('unmaximize', () => {
    console.log('Window unmaximized');
    mainWindow.webContents.send('window-state-changed', { isMaximized: false });
  });

  // 窗口关闭行为
  mainWindow.on('close', (event) => {
    console.log('========================================');
    console.log('Window close event triggered!');
    console.log('app.isQuitting:', app.isQuitting);
    console.log('Platform:', process.platform);
    console.log('DISABLE_TRAY:', process.env.DISABLE_TRAY);
    console.log('========================================');

    // 如果不是退出应用，在 Windows 上最小化到托盘
    // 但如果设置了 DISABLE_TRAY 环境变量，则直接退出
    if (!app.isQuitting && process.platform === 'win32' && !process.env.DISABLE_TRAY) {
      event.preventDefault();
      mainWindow.hide();
      console.log('✅ Window hidden to tray');
    } else {
      console.log('✅ Window will close and app will quit');
    }
  });
}

// 初始化更新器
function confirmInstalledUpdateHealth() {
  if (updateHealthCheck) return updateHealthCheck;
  updateHealthCheck = confirmHealthyUpdate({
    updater, mainWindow, windowReady: updateWindowReady,
    backendReady, backendPort, checkBackendHealth,
    isWindowReady: () => updateWindowReady,
    isBackendReady: () => backendReady
  }).catch((error) => { console.error('[Update] Health confirmation failed:', error.message); return false; })
    .finally(() => { updateHealthCheck = null; });
  return updateHealthCheck;
}

function initUpdater() {
  const configuredUpdateChannel = process.env.VIDFLOW_UPDATE_CHANNEL || 'stable';
  const updatePreferences = readUpdatePreferences();
  const configuredUpdateServerUrl = updatePreferences.updateServerUrl || process.env.VIDFLOW_UPDATE_SERVER_URL || 'https://shcrystal.top';
  console.log('[Update] Using update server:', configuredUpdateServerUrl);

  try {
    updater?.dispose();
    // 创建更新器实例
    updater = new CustomUpdater({
      updateServerUrl: configuredUpdateServerUrl,
      channel: configuredUpdateChannel,
      autoCheck: updatePreferences.autoUpdate,
      autoDownload: updatePreferences.autoUpdate,
      allowInsecureLocalhost: !app.isPackaged && process.env.VIDFLOW_ALLOW_LOCAL_UPDATE_TEST === '1',
      allowUnsignedDevelopment: !app.isPackaged && process.env.VIDFLOW_ALLOW_UNSIGNED_UPDATE_TEST === '1'
    });
    updaterError = null;
  } catch (error) {
    updater = null;
    updaterError = error.message || 'Updater initialization failed';
    console.error('[Update] Updater disabled:', updaterError);
    if (mainWindow) {
      mainWindow.webContents.send('update-error', updaterError);
    }
    return;
  }

  // 监听更新事件
  updater.on('checking-for-update', () => {
    confirmInstalledUpdateHealth();
    console.log('[Update] Checking for updates...');
    if (mainWindow) {
      mainWindow.webContents.send('update-checking');
    }
  });

  updater.on('update-available', (info) => {
    console.log('[Update] Update available:', info);
    if (mainWindow) {
      mainWindow.webContents.send('update-available', info);
    }
  });

  updater.on('update-not-available', (info) => {
    console.log('[Update] No update available');
    if (mainWindow) {
      mainWindow.webContents.send('update-not-available', info);
    }
  });

  updater.on('download-progress', (progress) => {
    if (mainWindow) {
      mainWindow.webContents.send('download-progress', progress);
    }
  });

  updater.on('download-start', () => mainWindow?.webContents.send('download-start'));
  updater.on('download-cancelled', () => mainWindow?.webContents.send('download-cancelled'));

  updater.on('update-downloaded', (info) => {
    console.log('[Update] Update downloaded:', info);
    if (mainWindow) {
      mainWindow.webContents.send('update-downloaded', info);
    }
  });

  updater.on('error', (error) => {
    console.error('[Update] Update error:', error);
    if (mainWindow) {
      mainWindow.webContents.send('update-error', error.message);
    }
  });

  // 增量更新事件转发
  updater.on('delta-fallback', (info) => {
    console.log('[Update] Delta fallback:', info);
    if (mainWindow) {
      mainWindow.webContents.send('delta-fallback', info);
    }
  });

  updater.on('delta-apply-start', () => {
    console.log('[Update] Delta apply starting...');
    if (mainWindow) {
      mainWindow.webContents.send('delta-apply-start');
    }
  });

  updater.on('delta-apply-complete', () => {
    console.log('[Update] Delta apply complete');
    if (mainWindow) {
      mainWindow.webContents.send('delta-apply-complete');
    }
  });

  updater.startAutoCheck();
  confirmInstalledUpdateHealth();
}

// 创建系统托盘
function createTray() {
  // 如果设置了 DISABLE_TRAY，跳过托盘创建
  if (process.env.DISABLE_TRAY) {
    console.log('⚠️ Tray disabled by environment variable');
    return;
  }

  try {
    const isDev = !app.isPackaged;

    // 托盘图标路径
    // Windows 使用 .ico
    // macOS 使用 Template 图标（系统会自动适应亮/暗模式）
    // Linux 使用 .png
    let iconPath;
    if (isDev) {
      // 开发模式：相对于当前文件
      if (process.platform === 'win32') {
        iconPath = path.join(__dirname, '../resources/icons/tray-icon.ico');
      } else {
        // macOS 和 Linux: 直接使用 tray-icon.png
        iconPath = path.join(__dirname, '../resources/icons/tray-icon.png');
      }
    } else {
      // 生产模式：使用 process.resourcesPath
      if (process.platform === 'win32') {
        iconPath = path.join(process.resourcesPath, 'icons', 'tray-icon.ico');
      } else {
        // macOS 和 Linux: 直接使用 tray-icon.png
        iconPath = path.join(process.resourcesPath, 'icons', 'tray-icon.png');
      }
    }

    console.log('Creating tray with icon:', iconPath);
    console.log('Icon exists:', fs.existsSync(iconPath));
    console.log('Mode:', isDev ? 'Development' : 'Production');
    console.log('Platform:', process.platform);

    if (!fs.existsSync(iconPath)) {
      console.error('❌ Tray icon not found:', iconPath);
      return;
    }

    tray = new Tray(iconPath);
    console.log('✅ Tray created successfully');

    // 托盘提示文本
    tray.setToolTip('VidFlow - 全能视频下载器');

    // 创建托盘右键菜单
    // 使用固定宽度的标签，通过空格填充使菜单更宽
    const padLabel = (text, width = 24) => {
      const spaces = ' '.repeat(Math.max(0, width - text.length));
      return text + spaces;
    };

    const contextMenu = Menu.buildFromTemplate([
      {
        label: padLabel('显示主窗口'),
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        }
      },
      {
        label: padLabel('隐藏窗口'),
        click: () => {
          if (mainWindow) {
            mainWindow.hide();
          }
        }
      },
      { type: 'separator' },
      {
        label: padLabel('检查更新'),
        click: async () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
          if (updater) {
            try {
              await updater.checkForUpdates();
            } catch (err) {
              console.error('Check update failed:', err);
            }
          }
        }
      },
      {
        label: padLabel('打开下载目录'),
        click: async () => {
          try {
            const { shell } = require('electron');
            const downloadPath = path.join(app.getPath('downloads'), 'VidFlow');
            // 确保目录存在
            if (!fs.existsSync(downloadPath)) {
              fs.mkdirSync(downloadPath, { recursive: true });
            }
            await shell.openPath(downloadPath);
          } catch (err) {
            console.error('Failed to open download folder:', err);
          }
        }
      },
      { type: 'separator' },
      {
        label: padLabel('开机自启动'),
        type: 'checkbox',
        checked: app.getLoginItemSettings().openAtLogin,
        click: (menuItem) => {
          app.setLoginItemSettings({
            openAtLogin: menuItem.checked,
            path: app.getPath('exe')
          });
        }
      },
      { type: 'separator' },
      {
        label: padLabel('关于 VidFlow'),
        click: () => {
          // 显示窗口并发送事件到渲染进程显示自定义关于对话框
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
            mainWindow.webContents.send('show-about');
          }
        }
      },
      { type: 'separator' },
      {
        label: padLabel('重启应用'),
        click: () => {
          app.relaunch();
          app.quit();
        }
      },
      {
        label: padLabel('退出应用'),
        click: () => {
          app.isQuitting = true;
          app.quit();
        }
      }
    ]);

    tray.setContextMenu(contextMenu);

    // 双击托盘图标显示/隐藏窗口
    tray.on('double-click', () => {
      if (mainWindow) {
        if (mainWindow.isVisible()) {
          mainWindow.hide();
        } else {
          mainWindow.show();
          mainWindow.focus();
        }
      }
    });

    // Windows 单击托盘图标显示/隐藏窗口
    if (process.platform === 'win32') {
      tray.on('click', () => {
        if (mainWindow) {
          if (mainWindow.isVisible()) {
            mainWindow.hide();
          } else {
            mainWindow.show();
            mainWindow.focus();
          }
        }
      });
    }

    console.log('✅ Tray menu and events configured');
  } catch (error) {
    console.error('❌ Failed to create tray:', error);
    tray = null;
    return;
  }
}

// 禁用硬件加速，避免某些系统上的渲染问题
app.disableHardwareAcceleration();
console.log('Hardware acceleration disabled');

// 应用准备就绪
app.whenReady().then(async () => {
  console.log('========================================');
  console.log('App is ready, starting initialization...');
  console.log('========================================');

  // macOS: 设置 Dock 图标
  if (process.platform === 'darwin' && app.dock) {
    const isDev = !app.isPackaged;
    // Dock 图标使用 PNG 格式（nativeImage 更好支持）
    const dockIconPath = isDev
      ? path.join(__dirname, '../resources/icons/icon.png')
      : path.join(process.resourcesPath, 'icons', 'icon.png');

    console.log('Setting macOS Dock icon:', dockIconPath);
    if (fs.existsSync(dockIconPath)) {
      try {
        const { nativeImage } = require('electron');
        const dockIcon = nativeImage.createFromPath(dockIconPath);
        if (!dockIcon.isEmpty()) {
          app.dock.setIcon(dockIcon);
          console.log('Dock icon set successfully');
        } else {
          console.warn('Dock icon image is empty');
        }
      } catch (err) {
        console.error('Failed to set Dock icon:', err);
      }
    } else {
      console.warn('Dock icon file not found:', dockIconPath);
    }
  }

  // 移除应用菜单栏
  Menu.setApplicationMenu(null);
  console.log('Menu removed');

  // 先创建窗口，立即显示界面给用户
  console.log('Creating window first...');
  createWindow();
  console.log('Window created');

  // 立即创建托盘（不依赖后端启动结果）
  console.log('Creating tray...');
  createTray();

  // 初始化更新器
  console.log('Initializing updater...');
  initUpdater();

  // 检查并应用待处理的增量更新（在后端启动之前）
  if (updater && updater.deltaUpdater) {
    try {
      const applied = await updater.deltaUpdater.applyPendingUpdate();
      if (applied) {
        console.log('✅ Pending delta update applied successfully');
      }
    } catch (err) {
      console.error('Failed to apply pending update:', err);
    }
  }

  const isDev = !app.isPackaged;
  const existingBackendAttempts = isDev ? 12 : 1;
  const disableBackendAutoStart = process.env.DISABLE_BACKEND_AUTO_START === '1';
  const reuseExistingBackend = !isDev || process.env.VIDFLOW_REUSE_EXISTING_BACKEND === '1';
  const probeExistingBackend = disableBackendAutoStart || reuseExistingBackend || isDev;

  if (probeExistingBackend) {
    const existingPort = await findExistingBackend(existingBackendAttempts, 500);
    if (existingPort) {
      const reuseReason = disableBackendAutoStart
        ? 'manual dev backend'
        : (reuseExistingBackend ? 'requested existing backend' : 'healthy dev backend');
      console.log(`✅ Reusing ${reuseReason}: ${existingPort}`);
      await confirmInstalledUpdateHealth();
      if (mainWindow && mainWindow.webContents) {
        mainWindow.webContents.send('backend-ready', { port: existingPort });
        console.log('📢 Sent backend-ready event to renderer');
      }
      return;
    }
  }

  // 检查是否禁用后端自动启动（用于手动启动后端的开发场景）
  if (disableBackendAutoStart) {
    console.error('❌ Backend auto-start is disabled, but no healthy existing backend was found');
    console.error('❌ Please make sure the backend started by START.bat is running');
    return;
  }

  console.log(`[${isDev ? 'DEV' : 'PROD'}] No healthy existing backend found; starting a fresh backend instance`);
  backendReady = false;
  backendPort = null;
  backendError = null;
  removeBackendPortFile();

  // 异步启动后端，不阻塞窗口显示
  console.log('Starting backend...');
  try {
    const port = await startPythonBackend();
    console.log('✅ Backend started successfully on port:', port);
    await confirmInstalledUpdateHealth();

    // 通知渲染进程后端已就绪
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('backend-ready', { port });
      console.log('📢 Sent backend-ready event to renderer');
    }

  } catch (error) {
    console.error('❌ Failed to start backend:', error);

    // 通知渲染进程后端启动失败
    if (mainWindow && mainWindow.webContents) {
      mainWindow.webContents.send('backend-error', {
        message: error.message || '后端启动失败'
      });
    }

    // 或者直接在窗口中显示错误页面
    setTimeout(() => {
      if (mainWindow && mainWindow.webContents) {
        const errorHtml = `
          <html>
            <head>
              <style>
                body {
                  background: #1a1a1a;
                  color: #fff;
                  font-family: 'Segoe UI', sans-serif;
                  padding: 40px;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                  margin: 0;
                }
                h1 { color: #ff6b6b; margin-bottom: 20px; }
                .error {
                  background: #2a2a2a;
                  padding: 20px;
                  border-radius: 8px;
                  margin: 20px 0;
                  max-width: 600px;
                }
                .tip { color: #4ecdc4; margin-top: 10px; }
              </style>
            </head>
            <body>
              <h1>⚠️ 后端启动失败</h1>
              <div class="error">
                <p><strong>错误信息:</strong> ${error.message}</p>
                <p class="tip">请检查后端文件是否存在，或查看控制台日志获取详细信息</p>
              </div>
            </body>
          </html>
        `;
        mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml));
      }
    }, 1000);
  }
});

// macOS 激活应用时如果没有窗口则创建一个
app.on('activate', () => {
  console.log('========================================');
  console.log('App activated');
  console.log('Windows count:', BrowserWindow.getAllWindows().length);
  console.log('========================================');

  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// 所有窗口关闭
app.on('window-all-closed', () => {
  console.log('========================================');
  console.log('window-all-closed event triggered!');
  console.log('Platform:', process.platform);
  console.log('========================================');

  // macOS 下所有窗口关闭时退出应用
  // Windows 下窗口关闭时不退出，继续在托盘运行
  if (process.platform === 'darwin') {
    app.quit();
  }
  // Windows 下窗口关闭时不退出，继续在托盘运行
  console.log('Windows: App continues running in tray');
});

// 应用退出前清理
app.on('before-quit', (event) => {
  console.log('========================================');
  console.log('before-quit event triggered!');
  console.log('========================================');
  app.isQuitting = true;
  updater?.dispose();

  if (backendQuitCleanupDone) {
    return;
  }

  event.preventDefault();
  stopBackendServicesForQuit('before-quit').finally(() => {
    setTimeout(() => {
      app.quit();
    }, 0);
  });
});

app.on('will-quit', () => {
  console.log('========================================');
  console.log('will-quit event triggered!');
  console.log('========================================');
});

// 应用退出
app.on('quit', () => {
  console.log('========================================');
  console.log('App quit!');
  console.log('========================================');

  // 关闭 Python 后端
  if (pythonProcess) {
    pythonProcess.kill();
  }

  // 销毁托盘图标
  if (tray) {
    tray.destroy();
  }
});

// IPC 处理程序

// 获取后端端口
ipcMain.handle('get-backend-port', async () => {
  // 确定状态
  let status = 'starting';
  if (backendReady) {
    status = 'ready';
  } else if (backendError) {
    status = 'failed';
  }

  const config = {
    port: backendPort,
    ready: backendReady,
    host: '127.0.0.1',
    error: backendError || null,
    status: status
  };
  // 启用日志以诊断端口问题
  console.log('[IPC] get-backend-port called, returning:', config);
  return config;
});

// 选择目录
ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  return result.filePaths[0];
});

// 选择视频文件
ipcMain.handle('select-video-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'Video Files', extensions: ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v'] },
      { name: 'All Files', extensions: ['*'] }
    ]
  });
  return result.filePaths[0];
});

// 选择多个文件
ipcMain.handle('select-files', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections']
  });
  return result.filePaths;
});

// 打开文件所在文件夹
ipcMain.handle('show-item-in-folder', async (event, filePath) => {
  const { shell } = require('electron');
  shell.showItemInFolder(filePath);
});

// 打开文件夹
ipcMain.handle('open-folder', async (event, folderPath) => {
  const { shell } = require('electron');
  await shell.openPath(folderPath);
});

// 打开外部链接
ipcMain.handle('open-external', async (event, url) => {
  const { shell } = require('electron');
  await shell.openExternal(url);
});

// 获取应用版本
ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});

// 检查文件是否存在
ipcMain.handle('file-exists', async (event, filePath) => {
  return fs.existsSync(filePath);
});

// 获取文件信息
ipcMain.handle('get-file-info', async (event, filePath) => {
  try {
    const stats = fs.statSync(filePath);
    return {
      size: stats.size,
      created: stats.birthtime,
      modified: stats.mtime,
      isFile: stats.isFile(),
      isDirectory: stats.isDirectory()
    };
  } catch (error) {
    return null;
  }
});

// 生成视频缩略图
ipcMain.handle('generate-video-thumbnail', async (event, videoPath) => {
  try {
    if (!fs.existsSync(videoPath)) {
      return null;
    }

    const { spawn } = require('child_process');
    const crypto = require('crypto');

    // 生成缓存文件名
    const hash = crypto.createHash('md5').update(videoPath).digest('hex');
    const thumbnailDir = path.join(app.getPath('userData'), 'thumbnails');
    const thumbnailPath = path.join(thumbnailDir, `${hash}.jpg`);

    // 如果缩略图已存在，读取并返回 base64
    if (fs.existsSync(thumbnailPath)) {
      try {
        const imageBuffer = fs.readFileSync(thumbnailPath);
        const base64Image = `data:image/jpeg;base64,${imageBuffer.toString('base64')}`;
        return base64Image;
      } catch (error) {
        // Failed to read cached thumbnail
        // 如果读取失败，删除缓存文件并重新生成
        try {
          fs.unlinkSync(thumbnailPath);
        } catch (e) {
          // 忽略删除错误
        }
      }
    }

    // 创建缩略图目录
    if (!fs.existsSync(thumbnailDir)) {
      fs.mkdirSync(thumbnailDir, { recursive: true });
    }

    // 查找 ffmpeg 路径
    let ffmpegPath = null;
    const isWindows = process.platform === 'win32';
    const ffmpegBinary = isWindows ? 'ffmpeg.exe' : 'ffmpeg';

    const possiblePaths = [
      path.join(__dirname, '../backend/tools/bin', ffmpegBinary),
      path.join(__dirname, '../resources/backend/tools/bin', ffmpegBinary),
      path.join(process.resourcesPath, 'backend/tools/bin', ffmpegBinary),
      path.join(process.resourcesPath, 'resources/backend/tools/bin', ffmpegBinary),
      'ffmpeg' // 系统 PATH
    ];

    for (const p of possiblePaths) {
      if (p === 'ffmpeg' || fs.existsSync(p)) {
        ffmpegPath = p;
        break;
      }
    }

    if (!ffmpegPath) {
      // FFmpeg not found
      return null;
    }

    // 使用 ffmpeg 生成缩略图（从视频 1 秒处截取）
    return new Promise((resolve, reject) => {
      const ffmpeg = spawn(ffmpegPath, [
        '-ss', '1',           // 从第 1 秒开始
        '-i', videoPath,      // 输入文件
        '-vframes', '1',      // 只截取 1 帧
        '-vf', 'scale=320:-1', // 缩放到宽度 320px
        '-y',                 // 覆盖已存在的文件
        thumbnailPath
      ]);

      let errorOutput = '';

      ffmpeg.stderr.on('data', (data) => {
        errorOutput += data.toString();
      });

      ffmpeg.on('close', (code) => {
        if (code === 0 && fs.existsSync(thumbnailPath)) {
          try {
            // 读取生成的图片并转换为 base64
            const imageBuffer = fs.readFileSync(thumbnailPath);
            const base64Image = `data:image/jpeg;base64,${imageBuffer.toString('base64')}`;
            resolve(base64Image);
          } catch (error) {
            // Failed to read generated thumbnail
            resolve(null);
          }
        } else {
          // FFmpeg execution failed
          resolve(null);
        }
      });

      ffmpeg.on('error', (err) => {
        // FFmpeg spawn failed
        resolve(null);
      });

      // 超时处理（5秒）
      setTimeout(() => {
        ffmpeg.kill();
        resolve(null);
      }, 5000);
    });
  } catch (error) {
    // Thumbnail generation failed
    return null;
  }
});

// 显示桌面通知
ipcMain.handle('show-notification', async (event, options) => {
  try {
    // 根据平台选择图标格式
    const defaultIcon = process.platform === 'win32'
      ? path.join(__dirname, '../resources/icons/icon.ico')
      : path.join(__dirname, '../resources/icons/icon.png');

    const notification = new Notification({
      title: options.title || 'VidFlow',
      body: options.body || '',
      icon: options.icon || defaultIcon,
      silent: options.silent || false
    });

    notification.show();

    // 点击通知时聚焦窗口
    notification.on('click', () => {
      if (mainWindow) {
        if (mainWindow.isMinimized()) {
          mainWindow.restore();
        }
        mainWindow.focus();
      }
    });

    return { success: true };
  } catch (error) {
    console.error('Notification error:', error);
    return { success: false, error: error.message };
  }
});

// 窗口控制
ipcMain.handle('window-minimize', () => {
  if (mainWindow) {
    mainWindow.minimize();
  }
});

ipcMain.handle('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.handle('window-close', () => {
  if (mainWindow) {
    mainWindow.close();
  }
});

// 更新相关 IPC 处理器

// 手动检查更新
ipcMain.handle('custom-update-check', async () => {
  try {
    if (!updater) {
      return { success: false, error: updaterError || 'Updater not initialized' };
    }
    const result = await updater.checkForUpdates();
    return { success: true, data: result };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

// 开始下载更新
ipcMain.handle('custom-update-download', async () => {
  try {
    if (!updater) {
      return { success: false, error: updaterError || 'Updater not initialized' };
    }

    // 检查是否有增量更新可用，优先使用增量更新
    const updateInfo = updater.updateInfo;
    if (updater.useDeltaUpdate && updateInfo && updateInfo.delta_available && updateInfo.delta_info) {
      console.log('[Update] Using delta update, size:', updateInfo.delta_info.delta_size);
      try {
        await updater.downloadDeltaUpdate();
        return { success: true, type: 'delta' };
      } catch (deltaError) {
        console.error('[Update] Delta update failed, falling back to full update:', deltaError);
        mainWindow?.webContents.send('delta-fallback', { reason: deltaError.message });
        // 回退到全量更新
        await updater.downloadUpdate();
        return { success: true, type: 'full' };
      }
    } else {
      await updater.downloadUpdate();
      return { success: true, type: 'full' };
    }
  } catch (error) {
    return { success: false, cancelled: error.code === 'ERR_CANCELED', error: error.message };
  }
});

// 退出并安装
ipcMain.handle('custom-update-install', async () => {
  try {
    if (!updater) {
      return { success: false, error: updaterError || 'Updater not initialized' };
    }
    const result = await updater.quitAndInstall();
    return { success: true, ...result };
  } catch (error) {
    return { success: false, error: error.message };
  }
});

ipcMain.handle('custom-update-cancel', () => {
  if (!updater) return { success: false, error: updaterError || 'Updater not initialized' };
  return { success: true, cancelled: updater.cancelDownload() };
});

function quotePowerShellArg(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

ipcMain.handle('restart-as-admin', async () => {
  if (process.platform !== 'win32') {
    return { success: false, error: 'Restart as admin is only supported on Windows' };
  }

  const executable = process.execPath;
  const args = process.argv.slice(1);
  const argumentList = args.length
    ? ` -ArgumentList ${args.map(quotePowerShellArg).join(', ')}`
    : '';
  const command = `$ErrorActionPreference = 'Stop'; Start-Process -FilePath ${quotePowerShellArg(executable)}${argumentList} -Verb RunAs | Out-Null`;

  return new Promise((resolve) => {
    let stderr = '';
    const relaunchProcess = spawn(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
      { windowsHide: true }
    );

    relaunchProcess.stderr.on('data', (data) => {
      stderr += data.toString('utf8');
    });

    relaunchProcess.on('error', (error) => {
      console.error('[IPC] Failed to request admin restart:', error);
      resolve({ success: false, error: error.message });
    });

    relaunchProcess.on('close', (code) => {
      if (code !== 0) {
        const error = stderr.trim() || 'Elevation request was cancelled';
        console.warn('[IPC] Admin restart was not completed:', error);
        resolve({ success: false, error });
        return;
      }

      resolve({ success: true });

      setTimeout(() => {
        app.isQuitting = true;
        app.quit();
      }, 100);
    });
  });
});

// 设置/清除代理配置（用于视频号嗅探器）
// 当嗅探器启动设置系统代理时，需要确保 Electron 的 HTTP 请求绕过 localhost
ipcMain.handle('set-proxy-config', async (event, config) => {
  try {
    if (mainWindow && mainWindow.webContents) {
      await mainWindow.webContents.session.setProxy(config);
      console.log('[IPC] Proxy config set:', JSON.stringify(config));
      return { success: true };
    }
    return { success: false, error: 'No window' };
  } catch (error) {
    console.error('[IPC] Failed to set proxy config:', error);
    return { success: false, error: error.message };
  }
});

// 清理所有更新文件
ipcMain.handle('custom-update-clean', async () => {
  try {
    if (!updater) {
      return { success: false, error: 'Updater not initialized' };
    }
    return updater.cleanAllUpdateFiles();
  } catch (error) {
    return { success: false, error: error.message };
  }
});
