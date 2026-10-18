const { contextBridge, ipcRenderer } = require('electron');

// 允许渲染进程调用的 IPC 通道白名单
const VALID_INVOKE_CHANNELS = [
  'select-directory',
  'select-video-file',
  'select-files',
  'show-item-in-folder',
  'open-folder',
  'file-exists',
  'get-file-info',
  'get-app-version',
  'open-external',
  'get-backend-port',
  'show-notification',
  'window-minimize',
  'window-maximize',
  'window-close',
  'custom-update-check',
  'custom-update-download',
  'custom-update-install',
  'custom-update-clean',
  'custom-update-cancel',
  'generate-video-thumbnail',
  'set-proxy-config',
  'restart-as-admin'
];

const VALID_EVENT_CHANNELS = [
  'update-checking',
  'update-available',
  'update-not-available',
  'download-progress',
  'download-start',
  'download-cancelled',
  'update-downloaded',
  'update-error',
  'delta-fallback',
  'delta-apply-start',
  'delta-apply-complete',
  'window-state-changed',
  'backend-ready',
  'backend-error',
  'backend-disconnected',
  'show-about'
];

const listenerWrappers = new Map();

function getWrappedListener(channel, callback) {
  let channelListeners = listenerWrappers.get(channel);
  if (!channelListeners) {
    channelListeners = new WeakMap();
    listenerWrappers.set(channel, channelListeners);
  }

  let wrapped = channelListeners.get(callback);
  if (!wrapped) {
    wrapped = (_event, ...args) => callback(...args);
    channelListeners.set(callback, wrapped);
  }

  return wrapped;
}

// 安全地暴露 API 到渲染进程
contextBridge.exposeInMainWorld('electron', {
  // 事件监听
  on: (channel, callback) => {
    if (VALID_EVENT_CHANNELS.includes(channel) && typeof callback === 'function') {
      const wrapped = getWrappedListener(channel, callback);
      ipcRenderer.removeListener(channel, wrapped);
      ipcRenderer.on(channel, wrapped);
    }
  },

  // 移除事件监听
  off: (channel, callback) => {
    if (VALID_EVENT_CHANNELS.includes(channel) && typeof callback === 'function') {
      const channelListeners = listenerWrappers.get(channel);
      const wrapped = channelListeners?.get(callback);
      if (wrapped) {
        ipcRenderer.removeListener(channel, wrapped);
        channelListeners.delete(callback);
      }
    }
  },

  // 通用 IPC 调用
  invoke: (channel, ...args) => {
    if (!VALID_INVOKE_CHANNELS.includes(channel)) {
      console.error(`[Security] Blocked unauthorized IPC call: ${channel}`);
      return Promise.reject(new Error('Unauthorized IPC channel'));
    }
    return ipcRenderer.invoke(channel, ...args);
  },

  // 文件选择
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  selectVideoFile: () => ipcRenderer.invoke('select-video-file'),
  selectFiles: () => ipcRenderer.invoke('select-files'),

  // 文件操作
  showItemInFolder: (filePath) => ipcRenderer.invoke('show-item-in-folder', filePath),
  openFolder: (folderPath) => ipcRenderer.invoke('open-folder', folderPath),
  fileExists: (filePath) => ipcRenderer.invoke('file-exists', filePath),
  getFileInfo: (filePath) => ipcRenderer.invoke('get-file-info', filePath),
  generateVideoThumbnail: (videoPath) => ipcRenderer.invoke('generate-video-thumbnail', videoPath),

  // 应用信息
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // 后端配置
  getBackendPort: () => ipcRenderer.invoke('get-backend-port'),

  // 桌面通知
  showNotification: (options) => ipcRenderer.invoke('show-notification', options),

  // 窗口控制
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),
  restartAsAdmin: () => ipcRenderer.invoke('restart-as-admin'),

  // 更新相关
  checkForUpdates: () => ipcRenderer.invoke('custom-update-check'),
  downloadUpdate: () => ipcRenderer.invoke('custom-update-download'),
  installUpdate: () => ipcRenderer.invoke('custom-update-install'),
  cancelUpdateDownload: () => ipcRenderer.invoke('custom-update-cancel'),
  cleanUpdateFiles: () => ipcRenderer.invoke('custom-update-clean'),

  // 平台信息
  platform: process.platform,
  arch: process.arch,
  isElectron: true
});
