/**
 * Tauri Integration Adapter for Electron
 * Compatibility layer that maps Tauri-style calls onto Electron + REST APIs
 */
import { createContext, useContext, ReactNode, useState, useEffect } from 'react';
import axios from 'axios';
import type { SnifferStatusResponse, DetectedVideo } from '../types/channels';

// Extend Window for timeout warning flags.
declare global {
  interface Window {
    _statusTimeoutWarned?: boolean;
    _videosTimeoutWarned?: boolean;
  }
}

// Development mode flag.
const isDev = import.meta.env.DEV;

// Dynamically resolve the backend port
let API_BASE = ''; // Set by initializeBackendPort at runtime
let portInitialized = false;
let initializationInProgress = false; // Prevent concurrent initialization attempts
let initializationAttempts = 0;
const MAX_INIT_ATTEMPTS = 10;
let backendPortListenersRegistered = false;
// The backend permits up to 180 seconds for a first-time WebDriver download.
export const CONTROLLED_BROWSER_START_TIMEOUT_MS = 210_000;

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
  withCredentials: true,
});

function setBackendApiBase(host: string, port: number) {
  API_BASE = `http://${host}:${port}`;
  api.defaults.baseURL = API_BASE;
  portInitialized = true;
}

function clearBackendApiBase() {
  API_BASE = '';
  api.defaults.baseURL = '';
  portInitialized = false;
}

function registerBackendPortListeners() {
  if (
    backendPortListenersRegistered ||
    typeof window === 'undefined' ||
    !window.electron ||
    typeof window.electron.on !== 'function'
  ) {
    return;
  }

  backendPortListenersRegistered = true;

  window.electron.on('backend-ready', (data: { port: number }) => {
    if (typeof data?.port === 'number' && data.port > 0) {
      setBackendApiBase('127.0.0.1', data.port);
      console.log('[Backend] Port updated from backend-ready event:', API_BASE);
    }
  });

  window.electron.on('backend-error', () => {
    clearBackendApiBase();
    console.warn('[Backend] Cleared API base after backend-error event');
  });

  window.electron.on('backend-disconnected', () => {
    clearBackendApiBase();
    console.warn('[Backend] Cleared API base after backend-disconnected event');
  });
}

const DEFAULT_SNIFFER_STATUS: SnifferStatusResponse = {
  state: 'stopped',
  proxy_address: null,
  proxy_port: 8888,
  videos_detected: 0,
  started_at: null,
  error_message: null,
  capture_mode: 'proxy_only',
  capture_state: 'stopped',
  capture_started_at: null,
  statistics: null,
};

let lastSnifferStatus: SnifferStatusResponse | null = null;
let lastVideosSnapshot: DetectedVideo[] | null = null;

// 初始化后端端口配置（带重试）
async function initializeBackendPort(): Promise<boolean> {
  if (portInitialized) return true;
  if (initializationInProgress) {
    // Another caller is already initializing the backend port.
    await new Promise(resolve => setTimeout(resolve, 100));
    return portInitialized;
  }

  initializationInProgress = true;
  initializationAttempts++;

  if (window.electron) {
    try {
      console.log(`🔄 [Attempt ${initializationAttempts}/${MAX_INIT_ATTEMPTS}] Requesting backend port from Electron...`);
      const config = await window.electron.invoke('get-backend-port');
      console.log('📡 Backend config received:', config);

      if (config && config.port && config.ready) {
        setBackendApiBase(config.host || '127.0.0.1', config.port);

        // 验证后端真的可以响应
        console.log('🔍 Verifying backend health...');
        try {
          // Use the backend health endpoint to verify the port is actually ready.
          const healthCheck = await api.get('/health', { timeout: 5000 });
          if (healthCheck.status === 200) {
            portInitialized = true;
            initializationInProgress = false;
            console.log('[Backend] API URL initialized and verified:', API_BASE);
            console.log('[Backend] Health check passed:', healthCheck.data);
            return true;
          } else {
            console.warn('⚠️ Backend health check failed, status:', healthCheck.status);
          }
        } catch (healthError) {
          console.error('[Backend] Health check failed:', healthError);
          console.warn('⚠️ Port received but backend not responding, will retry...');
        }
      } else if (config && config.port && !config.ready) {
        console.warn('⚠️ Backend port received but backend not ready yet, will retry...');
      } else {
        console.warn('⚠️ Backend port not available yet, will retry...');
      }
    } catch (error) {
      console.error('[Backend] Failed to get backend port:', error);
    }
  } else {
    console.warn('⚠️ window.electron not available, using default port');
    // Fall back to the default port outside the Electron runtime.
    portInitialized = true;
    initializationInProgress = false;
    return true;
  }

  initializationInProgress = false;
  return false;
}

// Retry backend-port initialization during startup until it succeeds or times out.
async function startPortInitialization() {
  for (let i = 0; i < MAX_INIT_ATTEMPTS; i++) {
    const success = await initializeBackendPort();
    if (success) {
      console.log('[Backend] Port initialization completed successfully');
      return;
    }
    // 等待后再重试
    console.log('[Backend] Waiting 2 seconds before retry...');
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  console.error('[Backend] Failed to initialize backend port after maximum attempts');
  console.error('⚠️ Frontend will continue with default port, but API calls may fail');
}

// 立即开始初始化
console.log('🚀 TauriIntegration.tsx loaded, starting port initialization...');
console.log('🔍 window.electron exists:', !!window.electron);
console.log('🔍 window.electron.invoke exists:', !!(window.electron && window.electron.invoke));
registerBackendPortListeners();
startPortInitialization();

// Request interceptor: always use the latest resolved backend port.
api.interceptors.request.use(
  async (config) => {
    // 只在完全未初始化且没有正在初始化时才尝试
    if (!portInitialized && !initializationInProgress) {
      console.log('🔄 Port not initialized, waiting for initialization...');
      // Wait up to 5 seconds for another initializer to finish.
      for (let i = 0; i < 50; i++) {
        if (portInitialized) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }

    // Always use the latest API_BASE, whether initialized or still empty.
    config.baseURL = API_BASE;

    if (isDev) {
      console.log(`📡 API Request: ${config.method?.toUpperCase()} ${config.baseURL || 'pending'}${config.url}`);
    }

    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const silent = error?.config?.headers?.['x-silent-error'] === '1';
    if (!silent) {
      console.error('[API] Request failed:', error.message);
    }

    // Log backend response details when they are available.
    if (!silent && error.response?.data) {
      console.error('[API] Backend error details:', error.response.data);
    }

    if (!silent && (error.code === 'ERR_NETWORK' || error.code === 'ECONNREFUSED')) {
      console.error('[API] Backend connection refused. Backend might not be ready yet.');
    }

    // Prefer the backend-provided error message when available.
    if (error.response?.data?.detail) {
      error.message = error.response.data.detail;
    }

    return Promise.reject(error);
  }
);

interface TauriContextType {
  isDesktop: boolean;
  version: string;
  isOnline: boolean;
  minimize: () => void;
  maximize: () => void;
  close: () => void;
}

const TauriContext = createContext<TauriContextType>({
  isDesktop: true,
  version: '1.0.0',
  isOnline: true,
  minimize: () => {},
  maximize: () => {},
  close: () => {},
});

export function TauriProvider({ children }: { children: ReactNode }) {
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  const value: TauriContextType = {
    isDesktop: true, // Electron 环境
    version: '1.0.0',
    isOnline,
    minimize: () => {
      // Electron IPC 调用（如果需要）
      console.log('Minimize window');
    },
    maximize: () => {
      console.log('Maximize window');
    },
    close: () => {
      console.log('Close window');
    },
  };

  return (
    <TauriContext.Provider value={value}>
      {children}
    </TauriContext.Provider>
  );
}

export function useTauri() {
  return useContext(TauriContext);
}

export function useDesktopFeatures() {
  return {
    openDownloadFolder: async () => {
      console.log('Open download folder');
      // Electron IPC can later open the system file manager here.
    },
    selectDirectory: async (): Promise<string | null> => {
      // The browser build falls back to the HTML5 file API.
      return null;
    },
  };
}

type StructuredErrorPayload = {
  code?: string;
  message?: string;
  hint?: string;
};

function normalizeStructuredErrorPayload(value: unknown): StructuredErrorPayload | null {
  if (!value) return null;

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return normalizeStructuredErrorPayload(parsed);
    } catch {
      return null;
    }
  }

  if (typeof value === 'object') {
    const payload = value as Record<string, unknown>;
    const message = payload.message;
    if (typeof message === 'string' && message.trim()) {
      const hint = payload.hint;
      const code = payload.code;
      return {
        code: typeof code === 'string' ? code : undefined,
        message: message,
        hint: typeof hint === 'string' ? hint : undefined,
      };
    }
  }

  return null;
}

function formatBackendErrorMessage(detail: unknown, fallback: string): string {
  const structured = normalizeStructuredErrorPayload(detail);
  if (structured?.message) {
    if (structured.hint) {
      return `${structured.message} (${structured.hint})`;
    }
    return structured.message;
  }
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  return fallback;
}

function getAxiosErrorMessage(error: any, fallback: string): string {
  const detail = error?.response?.data?.detail ?? error?.response?.data?.error;
  if (detail !== undefined) {
    return formatBackendErrorMessage(detail, fallback);
  }
  if (typeof error?.message === 'string' && error.message.trim()) {
    return error.message;
  }
  return fallback;
}

/**
 * Emulate the Tauri invoke API
 * by routing commands through the Electron + REST bridge.
 */
export async function invoke(command: string, args?: any): Promise<any> {
  // Keep noisy debug logs limited to development usage.
  if (import.meta.env.DEV) {
    console.debug(`[invoke] ${command}`, args);
  }

  if (!portInitialized) {
    await initializeBackendPort();
    for (let i = 0; i < 50; i++) {
      if (API_BASE) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }

  if (!API_BASE && window.electron) {
    throw new Error('Backend is not ready yet. Please try again.');
  }

  const commandMap: Record<string, () => Promise<any>> = {
    // 视频信息
    'get_video_info': async () => {
      const res = await api.post('/api/v1/downloads/info', { url: args?.url });
      return res.data.data;
    },

    // Start a download task.
    'start_download': async () => {
      const res = await api.post('/api/v1/downloads/start', {
        url: args?.url,
        quality: args?.quality,
        format_id: args?.format_id,
        output_path: args?.output_path,
      });
      return res.data;
    },

    // 获取下载任务列表
    'get_download_tasks': async () => {
      try {
        const res = await api.get('/api/v1/downloads/tasks');
        console.log('[DEBUG] get_download_tasks response:', res.data);
        const tasks = res.data?.tasks || [];
        return tasks;
      } catch (error) {
        console.error('[ERROR] Failed to get download tasks:', error);
        return [];
      }
    },

    // Get a single task status.
    'get_task_status': async () => {
      const res = await api.get(`/api/v1/downloads/tasks/${args?.task_id}`);
      return res.data.task;
    },

    // 删除任务（同时删除本机文件）
    'delete_download_task': async () => {
      const res = await api.delete(`/api/v1/downloads/tasks/${args?.task_id}`, {
        params: { delete_file: true },
      });
      return res.data;
    },

    // 取消任务
    'cancel_download_task': async () => {
      await api.post(`/api/v1/downloads/tasks/${args?.task_id}/cancel`);
      return { success: true };
    },

    // 暂停任务
    'pause_download_task': async () => {
      await api.post(`/api/v1/downloads/tasks/${args?.task_id}/pause`);
      return { success: true };
    },

    // 恢复任务
    'resume_download_task': async () => {
      await api.post(`/api/v1/downloads/tasks/${args?.task_id}/resume`);
      return { success: true };
    },

    // 系统信息
    'get_system_info': async () => {
      try {
        const res = await api.get('/api/v1/system/info');
        return res.data;
      } catch (error) {
        // Fall back to mock data when the backend endpoint is unavailable.
        return {
          cpu_usage: Math.random() * 30 + 10,
          memory_usage: Math.random() * 40 + 20,
          disk_usage: Math.random() * 50 + 30,
          network_speed: { download: 0, upload: 0 },
          active_tasks: 0,
          queue_size: 0,
          total_downloads: 0,
          backend_status: 'online',
          uptime: '0h 0m'
        };
      }
    },

    // Queue status (mock fallback).
    'get_queue_status': async () => {
      try {
        const tasks = await api.get('/api/v1/downloads/tasks');
        console.log('[DEBUG] get_queue_status response:', tasks.data);
        const downloading = tasks.data?.tasks?.filter((t: any) =>
          t.status === 'downloading' || t.status === 'pending'
        ).length || 0;
        return [downloading];
      } catch (error) {
        console.error('[ERROR] Failed to get queue status:', error);
        return [0];
      }
    },

    // Tool status check.
    'check_tool_status': async () => {
      try {
        const res = await api.get('/api/v1/system/tools/status');
        return res.data;
      } catch (error) {
        // Fall back to mock data when the backend endpoint is unavailable.
        return [
          { name: 'FFmpeg', installed: false, required: true },
          { name: 'yt-dlp', installed: false, required: true },
          { name: 'Python', installed: true, version: '3.14.0', required: true },
          { name: 'faster-whisper', installed: false, required: false }
        ];
      }
    },

    // Resolve the downloads directory.
    'get_downloads_path': async () => {
      try {
        const res = await api.get('/api/v1/system/downloads-path');
        return res.data?.path || '';
      } catch (error) {
        console.error('Failed to get downloads path:', error);
        return '';
      }
    },

    // 打开外部链接
    'open_external': async () => {
      window.open(args?.url, '_blank');
      return { success: true };
    },

    // 选择目录 (使用 HTML5)
    'select_directory': async () => {
      // Browsers can fall back to input[type=file].
      return null;
    },

    // Install FFmpeg (download required, 5-minute timeout).
    'install_ffmpeg': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/install/ffmpeg', {}, {
          timeout: 300000 // 5 分钟
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装失败');
      }
    },

    // Install yt-dlp (download required, 5-minute timeout).
    'install_ytdlp': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/install/ytdlp', {}, {
          timeout: 300000 // 5 分钟
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装失败');
      }
    },

    // Install faster-whisper (large model download, 10-minute timeout).
    'install_whisper': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/install/whisper', {}, {
          timeout: 600000 // 10 分钟
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装失败');
      }
    },

    // Install all tools (can take a long time, 15-minute timeout).
    'install_all_tools': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/install/all', {}, {
          timeout: 900000 // 15 分钟
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装失败');
      }
    },

    // Update dependencies (may take a while, 5-minute timeout).
    'update_dependencies': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/update/dependencies', {}, {
          timeout: 300000 // 5 分钟
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '更新失败');
      }
    },

    // 打开终端
    'open_terminal': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/terminal');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '打开终端失败');
      }
    },

    // 获取Python环境信息
    'get_python_info': async () => {
      try {
        const res = await api.get('/api/v1/system/tools/python-info');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取信息失败');
      }
    },

    // 获取日志
    'get_logs': async () => {
      try {
        const res = await api.get('/api/v1/logs/', { params: args });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取日志失败');
      }
    },

    // 获取日志统计
    'get_log_stats': async () => {
      try {
        const res = await api.get('/api/v1/logs/stats');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取统计失败');
      }
    },

    // 清空日志
    'clear_logs': async () => {
      try {
        const res = await api.delete('/api/v1/logs/clear');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '清空日志失败');
      }
    },

    // 下载日志
    'download_logs': async () => {
      try {
        const res = await api.get('/api/v1/logs/download', {
          responseType: 'blob'
        });
        // 创建下载链接
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `vidflow_logs_${new Date().toISOString().split('T')[0]}.log`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        return { success: true };
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '下载失败');
      }
    },

    // 获取日志路径
    'get_log_path': async () => {
      try {
        const res = await api.get('/api/v1/logs/path');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取日志路径失败');
      }
    },

    // 获取存储信息
    'get_storage_info': async () => {
      try {
        const res = await api.get('/api/v1/system/storage');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取存储信息失败');
      }
    },

    // 清理缓存
    'clear_cache': async () => {
      try {
        const res = await api.post('/api/v1/system/cache/clear');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '清理缓存失败');
      }
    },

    // 创建字幕生成任务
    'generate_subtitle': async () => {
      try {
        const res = await api.post('/api/v1/subtitle/generate', args);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '创建任务失败'));
      }
    },

    // 获取字幕任务列表
    'get_subtitle_tasks': async () => {
      try {
        const res = await api.get('/api/v1/subtitle/tasks');
        return res.data?.tasks || [];
      } catch (error: any) {
        console.error('Failed to get subtitle tasks:', error);
        return [];
      }
    },

    // 获取单个字幕任务
    'get_subtitle_task': async () => {
      try {
        const res = await api.get(`/api/v1/subtitle/tasks/${args?.task_id}`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '获取任务失败'));
      }
    },

    // 删除字幕任务
    'delete_subtitle_task': async () => {
      try {
        await api.delete(`/api/v1/subtitle/tasks/${args?.task_id}`);
        return { success: true };
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '删除失败'));
      }
    },

    'cancel_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/tasks/${args?.task_id}/cancel`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '取消任务失败'));
      }
    },

    // 暂停字幕任务
    'pause_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/tasks/${args?.task_id}/pause`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '暂停任务失败'));
      }
    },

    // 恢复字幕任务
    'resume_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/tasks/${args?.task_id}/resume`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '恢复任务失败'));
      }
    },

    // 获取可用模型
    'get_subtitle_models': async () => {
      try {
        const res = await api.get('/api/v1/subtitle/models');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取模型失败');
      }
    },

    // 获取支持的语言
    'get_subtitle_languages': async () => {
      try {
        const res = await api.get('/api/v1/subtitle/languages');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取语言失败');
      }
    },

    // Check whether the proxy is reachable.
    'check_proxy': async () => {
      try {
        const res = await api.get('/api/v1/system/network/proxy-check');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Proxy check failed');
      }
    },

    // Open a folder in the system file manager.
    'open_folder': async () => {
      try {
        const res = await api.post('/api/v1/system/open-folder', {
          path: args?.path
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to open folder');
      }
    },

    // Burn subtitles into a video file.
    'burn_subtitle': async () => {
      try {
        const res = await api.post('/api/v1/subtitle/burn-subtitle', {
          video_path: args?.video_path,
          subtitle_path: args?.subtitle_path,
          output_path: args?.output_path
        });
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '字幕烧录失败'));
      }
    },

    // 选择文件
    'select_file': async () => {
      try {
        const res = await api.post('/api/v1/system/select-file', {
          filters: args?.filters
        });
        return res.data?.path;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '选择文件失败');
      }
    },

    // Show a save-file dialog.
    'save_file': async () => {
      try {
        const res = await api.post('/api/v1/system/save-file', {
          default_path: args?.defaultPath,
          filters: args?.filters
        });
        return res.data?.path;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '保存文件失败');
      }
    },

    // ==================== 烧录字幕任务管理 ====================

    // 创建烧录字幕任务
    'create_burn_subtitle_task': async () => {
      try {
        const res = await api.post('/api/v1/subtitle/burn-subtitle-task', {
          video_path: args?.video_path,
          subtitle_path: args?.subtitle_path,
          output_path: args?.output_path,
          video_title: args?.video_title
        });
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '创建烧录任务失败'));
      }
    },

    // 获取烧录字幕任务列表
    'get_burn_subtitle_tasks': async () => {
      try {
        const res = await api.get('/api/v1/subtitle/burn-subtitle-tasks');
        return res.data || [];
      } catch (error: any) {
        console.error('Failed to get burn subtitle tasks:', error);
        return [];
      }
    },

    // 删除烧录字幕任务
    'delete_burn_subtitle_task': async () => {
      try {
        await api.delete(`/api/v1/subtitle/burn-subtitle-tasks/${args?.task_id}`);
        return { success: true };
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '删除烧录任务失败'));
      }
    },

    // 取消烧录字幕任务
    'cancel_burn_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/burn-subtitle-tasks/${args?.task_id}/cancel`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '取消烧录任务失败'));
      }
    },

    // 暂停烧录字幕任务
    'pause_burn_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/burn-subtitle-tasks/${args?.task_id}/pause`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '暂停烧录任务失败'));
      }
    },

    // 恢复烧录字幕任务
    'resume_burn_subtitle_task': async () => {
      try {
        const res = await api.post(`/api/v1/subtitle/burn-subtitle-tasks/${args?.task_id}/resume`);
        return res.data;
      } catch (error: any) {
        throw new Error(getAxiosErrorMessage(error, '恢复烧录任务失败'));
      }
    },

    // ==================== GPU Acceleration ====================

    // Fetch GPU status.
    'get_gpu_status': async () => {
      try {
        const res = await api.get('/api/v1/system/gpu/status');
        return res.data?.data ?? res.data;
      } catch (error: any) {
        console.error('Failed to get GPU status:', error);
        return {
          gpu_available: false,
          gpu_enabled: false,
          can_install: false,
          installing: false
        };
      }
    },

    // 安装GPU加速包
    'install_gpu_package': async () => {
      try {
        const res = await api.post('/api/v1/system/gpu/install');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '启动GPU安装失败');
      }
    },

    // ==================== Tool Status ====================

    // Check installed tools.
    'check_tools_status': async () => {
      try {
        const res = await api.get('/api/v1/system/tools/status');
        return res.data || [];
      } catch (error: any) {
        console.error('Failed to check tools status:', error);
        return [];
      }
    },

    // 下载/更新 yt-dlp
    'download_ytdlp': async () => {
      try {
        const res = await api.post('/api/v1/system/tools/ytdlp/download');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '下载 yt-dlp 失败');
      }
    },

    // ==================== 配置管理 ====================

    // 获取完整配置
    'get_config': async () => {
      try {
        const res = await api.get('/api/v1/config');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取配置失败');
      }
    },

    // Get a single config value.
    'get_config_value': async () => {
      try {
        const res = await api.get(`/api/v1/config/${args?.key}`);
        return res.data?.value;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to get config value');
      }
    },

    // 更新配置
    'update_config': async () => {
      try {
        const res = await api.post('/api/v1/config/update', {
          updates: args?.updates
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '更新配置失败');
      }
    },

    // 重置配置
    'reset_config': async () => {
      try {
        const res = await api.post('/api/v1/config/reset');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '重置配置失败');
      }
    },

    // Update queue concurrency settings.
    'update_queue_config': async () => {
      try {
        const res = await api.post('/api/v1/downloads/queue/config', null, {
          params: { max_concurrent: args?.max_concurrent }
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '更新队列配置失败');
      }
    },

    // ==================== Cookie 管理 ====================

    // Fetch cookie status for all platforms.
    'get_cookies_status': async () => {
      try {
        const res = await api.get('/api/v1/system/cookies/status');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to get cookie status');
      }
    },

    // 获取指定平台的Cookie内容
    'get_cookie_content': async () => {
      try {
        const res = await api.get(`/api/v1/system/cookies/${args?.platform}`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取Cookie内容失败');
      }
    },

    // 保存指定平台的Cookie
    'save_cookie_content': async () => {
      try {
        const res = await api.post(`/api/v1/system/cookies/${args?.platform}`, {
          content: args?.content
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '保存Cookie失败');
      }
    },

    // 删除指定平台的Cookie
    'delete_cookie': async () => {
      try {
        const res = await api.delete(`/api/v1/system/cookies/${args?.platform}`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '删除Cookie失败');
      }
    },

    // Open the cookie folder.
    'open_cookies_folder': async () => {
      try {
        const res = await api.get('/api/v1/system/cookies/open-folder');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to open cookie folder');
      }
    },

    // ==================== Cookie 自动获取 ====================

    // Check Selenium status.
    'check_selenium_status': async () => {
      try {
        const res = await api.get('/api/v1/system/cookies/auto/selenium-status');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to check Selenium status');
      }
    },

    // Launch the managed browser.
    'start_cookie_browser': async () => {
      try {
        const res = await api.post('/api/v1/system/cookies/auto/start-browser', {
          platform: args?.platform,
          browser: args?.browser
        }, {
          timeout: CONTROLLED_BROWSER_START_TIMEOUT_MS,
        });

        // Convert backend business failures into thrown errors.
        if (res.data?.status === 'error') {
          throw new Error(res.data.error || 'Failed to start browser');
        }

        return res.data;
      } catch (error: any) {
        // Re-throw errors that we created locally above.
        if (error.message && !error.response) {
          throw error;
        }
        throw new Error(error.response?.data?.detail || 'Failed to start browser');
      }
    },

    // 提取Cookie
    'extract_cookies': async () => {
      try {
        const res = await api.post('/api/v1/system/cookies/auto/extract');

        // Convert backend business failures into thrown errors.
        if (res.data?.status === 'error') {
          throw new Error(res.data.error || '提取Cookie失败');
        }

        return res.data;
      } catch (error: any) {
        // Re-throw errors that we created locally above.
        if (error.message && !error.response) {
          throw error;
        }
        throw new Error(error.response?.data?.detail || '提取Cookie失败');
      }
    },

    // Close the managed browser.
    'close_cookie_browser': async () => {
      try {
        const res = await api.post('/api/v1/system/cookies/auto/close-browser');

        // Convert backend business failures into thrown errors.
        if (res.data?.status === 'error') {
          throw new Error(res.data.error || 'Failed to close browser');
        }

        return res.data;
      } catch (error: any) {
        // Re-throw errors that we created locally above.
        if (error.message && !error.response) {
          throw error;
        }
        throw new Error(error.response?.data?.detail || 'Failed to close browser');
      }
    },

    // 从已安装的浏览器提取Cookie（最可靠的方法）
    'extract_cookies_from_browser': async () => {
      try {
        // 确保参数存在
        if (!args?.platform) {
          throw new Error('缺少 platform 参数');
        }

        const requestBody = {
          platform: args.platform,
          browser: args.browser || 'chrome'
        };

        console.log('[DEBUG] extract_cookies_from_browser request:', requestBody);

        const res = await api.post('/api/v1/system/cookies/from-browser', requestBody, {
          headers: {
            'Content-Type': 'application/json'
          }
        });

        console.log('[DEBUG] extract_cookies_from_browser response:', res.data);

        // Convert backend business failures into thrown errors.
        if (res.data?.status === 'error') {
          throw new Error(res.data.error || '从浏览器提取Cookie失败');
        }

        return res.data;
      } catch (error: any) {
        console.error('[DEBUG] extract_cookies_from_browser error:', error);
        // Re-throw errors that we created locally above.
        if (error.message && !error.response) {
          throw error;
        }
        // 422错误通常是请求体验证失败
        if (error.response?.status === 422) {
          throw new Error('Invalid request parameters. Check the browser and platform values.');
        }
        throw new Error(error.response?.data?.detail || error.response?.data?.error || '从浏览器提取Cookie失败');
      }
    },

    // ==================== Cookie/CORS 测试 ====================
    'auth_test_login': async () => {
      try {
        const res = await api.post('/api/v1/system/auth/test/login');
        const data = res.data;
        if (data?.session) {
          const expiresAt = Date.now() + 7 * 24 * 3600 * 1000;
          const payload = { value: data.session, expiresAt };
          try {
            localStorage.setItem('vidflow_session', JSON.stringify(payload));
          } catch (err) {
            console.warn('Failed to persist session to localStorage:', err);
          }
        }
        return data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '测试登录失败');
      }
    },
    'auth_test_check': async () => {
      try {
        const res = await api.get('/api/v1/system/auth/test/check');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Cookie check failed');
      }
    },

    // Restore the bundled yt-dlp build.
    'reset_ytdlp': async () => {
      try {
        const res = await api.delete('/api/v1/system/tools/ytdlp/downloaded');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '恢复失败');
      }
    },

    // ==================== QR 扫码登录 ====================

    // Get platforms that support QR login.
    'qr_login_get_supported_platforms': async () => {
      try {
        const res = await api.get('/api/v1/admin/cookies/qr/supported');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取支持平台列表失败');
      }
    },

    // Get a platform QR code.
    'qr_login_get_qrcode': async () => {
      try {
        const res = await api.get(`/api/v1/admin/cookies/qr/${args?.platformId}/qrcode`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to get QR code');
      }
    },

    // Check the QR login status.
    'qr_login_check_status': async () => {
      try {
        const res = await api.post(`/api/v1/admin/cookies/qr/${args?.platformId}/qrcode/check`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to check QR login status');
      }
    },

    // 取消扫码登录
    'qr_login_cancel': async () => {
      try {
        const res = await api.post(`/api/v1/admin/cookies/qr/${args?.platformId}/qrcode/cancel`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '取消扫码登录失败');
      }
    },

    // 启用/禁用平台扫码登录
    'qr_login_set_enabled': async () => {
      try {
        const res = await api.post(`/api/v1/admin/cookies/qr/${args?.platformId}/enable`, {
          enabled: args?.enabled
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to update platform status');
      }
    },

    // ==================== WeChat Channels API ====================

    // Get sniffer status.
    'channels_get_status': async () => {
      try {
        // 使用较长的超时时间，避免频繁超时
        const res = await api.get('/api/channels/sniffer/status', {
          timeout: 15000,  // 15 second timeout
          headers: { 'x-silent-error': '1' },
        });
        const data = { ...DEFAULT_SNIFFER_STATUS, ...(res.data || {}) } as SnifferStatusResponse;
        lastSnifferStatus = data;
        return data;
      } catch (error: any) {
        // 超时或网络错误时返回默认状态，减少日志噪音
        if (error.code === 'ECONNABORTED' || error.code === 'ERR_NETWORK') {
          // 只在第一次失败时打印警告
          if (!window._statusTimeoutWarned) {
            console.warn('Failed to get sniffer status (timeout/network), returning stopped status');
            window._statusTimeoutWarned = true;
            setTimeout(() => { window._statusTimeoutWarned = false; }, 30000); // 30秒后重置
          }
          const fallbackBase = { ...DEFAULT_SNIFFER_STATUS, ...(lastSnifferStatus || {}) };
          return { ...fallbackBase, is_fallback: true };
        }
        throw new Error(error.response?.data?.detail || 'Failed to get sniffer status');
      }
    },

    // Start the sniffer.
    'channels_start_sniffer': async () => {
      try {
        const res = await api.post('/api/channels/sniffer/start', {
          port: args?.port,
          capture_mode: args?.capture_mode
        });

        if (res.data?.success !== false && window.electron) {
          try {
            const captureMode = String(res.data?.capture_mode || args?.capture_mode || '');
            const shouldUseExplicitProxy = captureMode === 'proxy_only';
            const proxyTarget = shouldUseExplicitProxy
              ? (res.data?.system_proxy || res.data?.proxy_address || null)
              : null;
            const localProxy = proxyTarget
              ? (String(proxyTarget).includes('://') ? String(proxyTarget) : `http://${proxyTarget}`)
              : null;

            if (localProxy) {
              await window.electron.invoke('set-proxy-config', {
                proxyRules: localProxy,
                proxyBypassRules: '127.0.0.1,localhost,<local>'
              });
              console.log('[Electron] Proxy bypass configured for localhost');
            } else {
              await window.electron.invoke('set-proxy-config', {
                mode: 'direct'
              });
              console.log('[Electron] Direct mode restored for non-proxy capture');
            }
          } catch (proxyErr) {
            console.warn('⚠️ Failed to update Electron proxy mode:', proxyErr);
          }
        }

        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to start sniffer');
      }
    },

    // Stop the sniffer.
    'channels_stop_sniffer': async () => {
      try {
        const res = await api.post('/api/channels/sniffer/stop');

        // Restore direct mode after stopping the sniffer.
        if (window.electron) {
          try {
            await window.electron.invoke('set-proxy-config', {
              mode: 'direct'
            });
            console.log('[Electron] Proxy cleared');
          } catch (proxyErr) {
            console.warn('⚠️ Failed to clear Electron proxy:', proxyErr);
          }
        }

        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to stop sniffer');
      }
    },

    // Get detected videos.
    'channels_get_videos': async () => {
      try {
        // 使用较长的超时时间，避免频繁超时
        const res = await api.get('/api/channels/videos', {
          timeout: 15000,  // 15 second timeout
          headers: { 'x-silent-error': '1' },
        });
        const data = Array.isArray(res.data) ? res.data : [];
        lastVideosSnapshot = data;
        return data;
      } catch (error: any) {
        // Return the last known list on timeout or network errors.
        if (error.code === 'ECONNABORTED' || error.code === 'ERR_NETWORK') {
          // 只在第一次失败时打印警告
          if (!window._videosTimeoutWarned) {
            console.warn('Failed to get videos (timeout/network), returning empty list');
            window._videosTimeoutWarned = true;
            setTimeout(() => { window._videosTimeoutWarned = false; }, 30000); // 30秒后重置
          }
          const fallback = lastVideosSnapshot ? [...lastVideosSnapshot] : [];
          (fallback as any).__fallback = true;
          return fallback;
        }
        throw new Error(error.response?.data?.detail || '获取视频列表失败');
      }
    },

    // 清空视频列表
    'channels_clear_videos': async () => {
      try {
        const res = await api.delete('/api/channels/videos');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '清空视频列表失败');
      }
    },

    // 手动添加视频 URL
    'channels_add_video': async () => {
      try {
        const res = await api.post('/api/channels/videos/add', {
          url: args?.url,
          title: args?.title
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '添加视频失败');
      }
    },

    // 下载视频
    'channels_download_video': async () => {
      try {
        const rawDecodeKey = typeof args?.decryption_key === 'string'
          ? args.decryption_key.trim()
          : '';
        const normalizedDecodeKey = /^\d+$/.test(rawDecodeKey) ? rawDecodeKey : null;
        const payload: Record<string, unknown> = {
          url: args?.url,
          quality: args?.quality,
          output_path: args?.output_path,
          decryption_key: normalizedDecodeKey
        };
        if (typeof args?.auto_decrypt === 'boolean') {
          payload.auto_decrypt = args.auto_decrypt;
        }
        const res = await api.post('/api/channels/download', payload);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '下载视频失败');
      }
    },

    // 取消下载
    'channels_cancel_download': async () => {
      try {
        const res = await api.post('/api/channels/download/cancel', {
          task_id: args?.task_id
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '取消下载失败');
      }
    },

    // 获取下载任务列表
    'channels_get_download_tasks': async () => {
      try {
        const res = await api.get('/api/channels/download/tasks');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取下载任务失败');
      }
    },

    // 删除下载任务
    'channels_delete_download_task': async () => {
      try {
        const res = await api.delete(`/api/channels/download/tasks/${args?.task_id}`);
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '删除任务失败');
      }
    },

    // 获取证书信息
    'channels_get_cert_info': async () => {
      try {
        const res = await api.get('/api/channels/certificate');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取证书信息失败');
      }
    },

    // 生成证书
    'channels_generate_cert': async () => {
      try {
        const res = await api.post('/api/channels/certificate/generate');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '生成证书失败');
      }
    },

    // 导出证书
    'channels_export_cert': async () => {
      try {
        const res = await api.post('/api/channels/certificate/export', {
          export_path: args?.export_path,
          format: args?.format,
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '导出证书失败');
      }
    },

    // 获取证书安装说明
    'channels_install_root_cert': async () => {
      try {
        const res = await api.post('/api/channels/certificate/install-root');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装系统根证书失败');
      }
    },

    'channels_install_wechat_p12': async () => {
      try {
        const res = await api.post('/api/channels/certificate/install-wechat-p12');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '导入微信兼容 P12 失败');
      }
    },

    'channels_get_cert_instructions': async () => {
      try {
        const res = await api.get('/api/channels/certificate/instructions');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取证书说明失败');
      }
    },

    // 获取配置
    'channels_get_config': async () => {
      try {
        const res = await api.get('/api/channels/config');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取配置失败');
      }
    },

    // 更新配置
    'channels_update_config': async () => {
      try {
        const res = await api.put('/api/channels/config', {
          proxy_port: args?.proxy_port,
          download_dir: args?.download_dir,
          auto_decrypt: args?.auto_decrypt,
          quality_preference: args?.quality_preference,
          clear_on_exit: args?.clear_on_exit
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '更新配置失败');
      }
    },

    // ==================== 透明捕获 API ====================

    // Get driver status.
    'channels_get_driver_status': async () => {
      try {
        const res = await api.get('/api/channels/driver/status');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to get driver status');
      }
    },

    // 安装驱动
    'channels_install_driver': async () => {
      try {
        const res = await api.post('/api/channels/driver/install');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '安装驱动失败');
      }
    },

    // Request an administrator restart.
    'channels_request_admin_restart': async () => {
      if (window.electron) {
        const result = await window.electron.invoke('restart-as-admin');
        if (!result?.success) {
          throw new Error(result?.error || 'Failed to request admin restart');
        }
        return result;
      }

      try {
        const res = await api.post('/api/channels/driver/request-admin');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to request admin restart');
      }
    },

    // 获取捕获配置
    'channels_get_capture_config': async () => {
      try {
        const res = await api.get('/api/channels/capture/config');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取捕获配置失败');
      }
    },

    // 更新捕获配置
    'channels_update_capture_config': async () => {
      try {
        const res = await api.put('/api/channels/capture/config', {
          capture_mode: args?.capture_mode,
          use_windivert: args?.use_windivert,
          quic_blocking_enabled: args?.quic_blocking_enabled,
          target_processes: args?.target_processes,
          no_detection_timeout: args?.no_detection_timeout,
          log_unrecognized_domains: args?.log_unrecognized_domains
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '更新捕获配置失败');
      }
    },

    // 获取捕获统计
    'channels_get_capture_statistics': async () => {
      try {
        const res = await api.get('/api/channels/capture/statistics');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '获取捕获统计失败');
      }
    },

    'channels_get_quic_status': async () => {
      try {
        const res = await api.get('/api/channels/quic/status');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to fetch QUIC status');
      }
    },

    'channels_toggle_quic': async () => {
      try {
        const res = await api.post('/api/channels/quic/toggle', {
          enabled: args?.enabled,
        });
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to toggle QUIC blocking');
      }
    },

    // 系统诊断
    'channels_detect_proxy': async () => {
      try {
        const res = await api.get('/api/channels/proxy/detect');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to detect proxy');
      }
    },

    'channels_get_diagnostics': async () => {
      try {
        const res = await api.get('/api/channels/diagnostics');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || 'Failed to fetch diagnostics');
      }
    },

    'channels_diagnose': async () => {
      try {
        const res = await api.get('/api/channels/diagnose');
        return res.data;
      } catch (error: any) {
        throw new Error(error.response?.data?.detail || '系统诊断失败');
      }
    },

  };

  const handler = commandMap[command];
  if (!handler) {
    // Keep unknown-command logs limited to development mode.
    if (import.meta.env.DEV) {
      console.debug(`[invoke] Unknown command: ${command}`);
    }
    // Return a safe default instead of throwing for unknown commands.
    return null;
  }

  try {
    return await handler();
  } catch (error) {
    console.error(`Error executing command ${command}:`, error);
    // Choose the most appropriate fallback by command type.
    if (command.includes('get_') && command.includes('tasks')) {
      return [];
    }
    // For other commands, rethrow instead of silently returning null.
    // 这样上层可以捕获到真实的错误信息
    throw error;
  }
}

/**
 * 模拟 Tauri 事件监听
 */
export function listen(event: string, _callback: (data: any) => void): Promise<() => void> {
  console.log(`[listen] ${event}`);

  // 可以使用 WebSocket 实现实时事件
  // WebSocket is preferred here, but polling would also work.

  const unlisten = () => {
    console.log(`[unlisten] ${event}`);
  };

  return Promise.resolve(unlisten);
}

/**
 * Return the current API base URL, including the dynamic port.
 * Useful when callers must construct backend URLs directly.
 */
export function getApiBaseUrl(): string {
  return API_BASE;
}

// Export a Tauri-compatible API surface.
export const tauri = {
  invoke,
  event: { listen },
};
