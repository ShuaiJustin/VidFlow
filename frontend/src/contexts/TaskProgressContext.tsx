import { createContext, useContext, useEffect, useRef, useState, ReactNode, useCallback } from 'react';
import { invoke } from '../components/TauriIntegration';
import { subscribeSharedWebSocket, isSharedWebSocketConnected } from './SharedWebSocket';

type DownloadStatus = 'pending' | 'downloading' | 'completed' | 'failed' | 'paused' | 'cancelled' | string;
type SubtitleStatus = 'pending' | 'processing' | 'generating' | 'translating' | 'cancelled' | 'completed' | 'failed' | 'paused' | string;
type BurnStatus = 'pending' | 'burning' | 'cancelled' | 'completed' | 'failed' | 'paused' | string;

export interface StructuredTaskError {
  code: string;
  message: string;
  hint?: string;
}

export interface DownloadTask {
  task_id: string;
  url: string;
  title: string;
  platform: string;
  quality: string;
  format?: string;
  format_id?: string;
  output_path?: string;
  filename?: string;
  status: DownloadStatus;
  progress: number;
  downloaded?: number;
  total?: number;
  speed?: number | string;
  eta?: number | string;
  file_path?: string;
  thumbnail?: string;
  created_at: string;
  completed_at?: string;
  error?: string;
  error_message?: string;
  file_size?: string;
  filesize?: number;
  // 智能下载器信息
  downloader_used?: string;  // 使用的下载器名称
  fallback_used?: boolean;   // 是否使用了回退
  fallback_reason?: string;  // 回退原因
}

export interface SubtitleTask {
  id: string;
  video_path: string;
  video_title: string;
  status: SubtitleStatus;
  progress: number;
  message?: string;
  source_language: string;
  target_languages: string[];
  model: string;
  created_at: string;
  completed_at?: string;
  error?: string;
  error_detail?: StructuredTaskError | null;
  cancelled?: boolean;
  output_files: string[];
}

export interface BurnSubtitleTask {
  id: string;
  video_path: string;
  subtitle_path: string;
  output_path: string;
  video_title: string;
  status: BurnStatus;
  progress: number;
  current?: number;
  duration?: number;
  created_at: string;
  completed_at?: string;
  error?: string;
  error_detail?: StructuredTaskError | null;
  cancelled?: boolean;
}

// 视频信息获取状态
export interface VideoInfoState {
  url: string;
  info: VideoInfo | null;
  loading: boolean;
  cookieWarning: { platform: string; platformName: string; reason?: 'security_check' } | null;
}

export interface VideoInfo {
  title: string;
  duration: number;
  platform?: string;
  thumbnail?: string;
  quality?: string[];
  formats: { ext: string; height?: number; quality?: string; format_id?: string }[];
  downloader_used?: string;
  fallback_used?: boolean;
  fallback_reason?: string;
}

interface TaskProgressContextType {
  downloads: DownloadTask[];
  subtitleTasks: SubtitleTask[];
  burnTasks: BurnSubtitleTask[];
  loading: boolean;
  refreshAll: () => Promise<void>;
  refreshDownloads: () => Promise<void>;
  refreshSubtitles: () => Promise<void>;
  refreshBurns: () => Promise<void>;
  // 视频信息获取状态
  videoInfoState: VideoInfoState;
  setVideoInfoUrl: (url: string) => void;
  setVideoInfo: (info: VideoInfo | null) => void;
  setVideoInfoLoading: (loading: boolean) => void;
  setVideoCookieWarning: (warning: { platform: string; platformName: string; reason?: 'security_check' } | null) => void;
  clearVideoInfo: () => void;
}

const TaskProgressContext = createContext<TaskProgressContextType | undefined>(undefined);

const ACTIVE_DOWNLOAD = new Set<DownloadStatus>(['pending', 'downloading']);
const ACTIVE_SUBTITLE = new Set<SubtitleStatus>(['pending', 'processing', 'generating', 'translating']);
const ACTIVE_BURN = new Set<BurnStatus>(['pending', 'burning']);

const normalizeProgress = (progress: unknown): number => {
  if (typeof progress !== 'number' || Number.isNaN(progress)) return 0;
  return Number(Math.min(Math.max(progress, 0), 100).toFixed(1));
};

async function showDesktopNotification(title: string, body: string) {
  try {
    if (window.electron && window.electron.isElectron) {
      await window.electron.showNotification({ title, body });
    }
  } catch (error) {
    console.error('[TaskProgress] Notification failed', error);
  }
}

export function TaskProgressProvider({ children }: { children: ReactNode }) {
  const [downloads, setDownloads] = useState<DownloadTask[]>([]);
  const [subtitleTasks, setSubtitleTasks] = useState<SubtitleTask[]>([]);
  const [burnTasks, setBurnTasks] = useState<BurnSubtitleTask[]>([]);
  const [loading, setLoading] = useState(false);

  // 视频信息获取状态
  const [videoInfoState, setVideoInfoState] = useState<VideoInfoState>({
    url: '',
    info: null,
    loading: false,
    cookieWarning: null,
  });

  const setVideoInfoUrl = useCallback((url: string) => {
    setVideoInfoState(prev => ({ ...prev, url }));
  }, []);

  const setVideoInfo = useCallback((info: VideoInfo | null) => {
    setVideoInfoState(prev => ({ ...prev, info }));
  }, []);

  const setVideoInfoLoading = useCallback((loading: boolean) => {
    setVideoInfoState(prev => ({ ...prev, loading }));
  }, []);

  const setVideoCookieWarning = useCallback((warning: { platform: string; platformName: string; reason?: 'security_check' } | null) => {
    setVideoInfoState(prev => ({ ...prev, cookieWarning: warning }));
  }, []);

  const clearVideoInfo = useCallback(() => {
    setVideoInfoState({ url: '', info: null, loading: false, cookieWarning: null });
  }, []);

  const prevDownloadStatus = useRef<Map<string, DownloadStatus>>(new Map());
  const prevSubtitleStatus = useRef<Map<string, SubtitleStatus>>(new Map());
  const prevBurnStatus = useRef<Map<string, BurnStatus>>(new Map());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notificationDedupRef = useRef<Set<string>>(new Set());
  const notificationQueueRef = useRef<Array<{ key: string; title: string; body: string }>>([]);
  const notificationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notificationLastSentAtRef = useRef<number>(0);

  const flushNotificationQueue = useCallback(() => {
    if (notificationTimerRef.current) return;

    const now = Date.now();
    const elapsed = now - notificationLastSentAtRef.current;
    const delay = elapsed >= 5000 ? 0 : 5000 - elapsed;

    notificationTimerRef.current = setTimeout(() => {
      notificationTimerRef.current = null;

      const next = notificationQueueRef.current.shift();
      if (!next) return;

      notificationLastSentAtRef.current = Date.now();
      Promise.resolve(showDesktopNotification(next.title, next.body)).finally(() => {
        flushNotificationQueue();
      });
    }, delay);
  }, []);

  const enqueueDesktopNotification = useCallback(
    (title: string, body: string, taskId: string) => {
      const key = `${taskId}-${title}`;
      if (notificationDedupRef.current.has(key)) return;

      notificationDedupRef.current.add(key);
      notificationQueueRef.current.push({ key, title, body });
      flushNotificationQueue();

      setTimeout(() => {
        notificationDedupRef.current.delete(key);
      }, 5000);
    },
    [flushNotificationQueue]
  );

  useEffect(() => {
    return () => {
      if (notificationTimerRef.current) {
        clearTimeout(notificationTimerRef.current);
        notificationTimerRef.current = null;
      }
    };
  }, []);

  const processTransitions = useCallback(
    <T extends { id?: string; task_id?: string; title?: string; video_title?: string; status: string }>(
      list: T[],
      prevMap: React.MutableRefObject<Map<string, string>>,
      doneMessage: (task: T) => string,
      failMessage: (task: T) => string,
      activeSet: Set<string>
    ) => {
      const nextMap = new Map<string, string>();
      list.forEach((task: T) => {
        const id = (task as any).task_id || (task as any).id;
        if (!id) return;
        const status = task.status;
        nextMap.set(id, status);

        const prev = prevMap.current.get(id);
        // Skip first-seen tasks (no previous state)
        if (!prev) {
          return;
        }

        const wasActive = activeSet.has(prev);
        const nowDone = status === 'completed';
        const nowFailed = status === 'failed';

        if (wasActive && nowDone) {
          enqueueDesktopNotification('任务完成', doneMessage(task), String(id));
        } else if (wasActive && nowFailed) {
          enqueueDesktopNotification('任务失败', failMessage(task), String(id));
        }
      });
      prevMap.current = nextMap;
    },
    [enqueueDesktopNotification]
  );

  const fetchDownloads = useCallback(async () => {
    const result = await invoke('get_download_tasks');
    const raw = Array.isArray(result) ? (result as DownloadTask[]) : [];
    return raw.map(task => ({
      ...task,
      progress: normalizeProgress((task as any).progress)
    }));
  }, []);

  const fetchSubtitleTasks = useCallback(async () => {
    const result = await invoke('get_subtitle_tasks');
    const raw = Array.isArray(result) ? (result as SubtitleTask[]) : [];
    return raw.map(task => ({
      ...task,
      progress: normalizeProgress((task as any).progress)
    }));
  }, []);

  const fetchBurnTasks = useCallback(async () => {
    const result = await invoke('get_burn_subtitle_tasks');
    const raw = Array.isArray(result) ? (result as BurnSubtitleTask[]) : [];
    return raw.map(task => ({
      ...task,
      progress: normalizeProgress((task as any).progress)
    }));
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      const [dl, sub, burn] = await Promise.all([
        fetchDownloads().catch(() => []),
        fetchSubtitleTasks().catch(() => []),
        fetchBurnTasks().catch(() => []),
      ]);

      setDownloads(dl);
      setSubtitleTasks(sub);
      setBurnTasks(burn);

      processTransitions(
        dl,
        prevDownloadStatus,
        (t: DownloadTask) => `${t.title || '下载任务'} 已完成`,
        (t: DownloadTask) => `${t.title || '下载任务'} 下载失败`,
        ACTIVE_DOWNLOAD
      );

      processTransitions(
        sub,
        prevSubtitleStatus,
        (t: SubtitleTask) => `${t.video_title || '字幕任务'} 已完成`,
        (t: SubtitleTask) => `${t.video_title || '字幕任务'} 失败`,
        ACTIVE_SUBTITLE
      );

      processTransitions(
        burn,
        prevBurnStatus,
        (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 已完成`,
        (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 失败`,
        ACTIVE_BURN
      );
    } finally {
      setLoading(false);
    }
  }, [fetchBurnTasks, fetchDownloads, fetchSubtitleTasks, processTransitions]);

  const refreshDownloads = useCallback(async () => {
    try {
      const dl = await fetchDownloads();
      setDownloads(dl);
      processTransitions(
        dl,
        prevDownloadStatus,
        (t: DownloadTask) => `${t.title || '下载任务'} 已完成`,
        (t: DownloadTask) => `${t.title || '下载任务'} 下载失败`,
        ACTIVE_DOWNLOAD
      );
    } catch {
      /* ignore */
    }
  }, [fetchDownloads, processTransitions]);

  const refreshSubtitles = useCallback(async () => {
    try {
      const sub = await fetchSubtitleTasks();
      setSubtitleTasks(sub);
      processTransitions(
        sub,
        prevSubtitleStatus,
        (t: SubtitleTask) => `${t.video_title || '字幕任务'} 已完成`,
        (t: SubtitleTask) => `${t.video_title || '字幕任务'} 失败`,
        ACTIVE_SUBTITLE
      );
    } catch {
      /* ignore */
    }
  }, [fetchSubtitleTasks, processTransitions]);

  const refreshBurns = useCallback(async () => {
    try {
      const burn = await fetchBurnTasks();
      setBurnTasks(burn);
      processTransitions(
        burn,
        prevBurnStatus,
        (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 已完成`,
        (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 失败`,
        ACTIVE_BURN
      );
    } catch {
      /* ignore */
    }
  }, [fetchBurnTasks, processTransitions]);

  // WebSocket 订阅（与轮询解耦，避免因依赖变化重新订阅）
  useEffect(() => {
    const unsubscribe = subscribeSharedWebSocket((data) => {
      try {
        if (data.type === 'download_progress') {
          const taskId = data.task_id;
          setDownloads(prev => {
            const next = [...prev];
            const idx = next.findIndex(t => t.task_id === taskId);

            if (idx >= 0) {
              const currentTask = next[idx];

              // 如果任务已经是 paused/cancelled/completed/failed 状态，
              // 忽略后续的进度更新（可能是延迟到达的旧消息）
              if (['paused', 'cancelled', 'completed', 'failed'].includes(currentTask.status)) {
                // 只有当 WebSocket 消息明确包含新状态时才更新
                if (data.status && data.status !== 'downloading') {
                  next[idx] = { ...currentTask, status: data.status };
                }
                return next;
              }

              const patch = {
                progress: normalizeProgress(data.progress),
                downloaded: data.downloaded ?? data.downloaded_bytes,
                total: data.total ?? data.total_bytes,
                speed: data.speed,
                eta: data.eta,
                quality: data.quality ?? currentTask.quality,
                filename: data.filename ?? currentTask.filename,
                file_path: data.file_path ?? currentTask.file_path,
                filesize: data.filesize ?? currentTask.filesize,
                // 只有当 WebSocket 消息包含 status 时才更新，否则保持当前状态
                status: data.status || currentTask.status
              } as Partial<DownloadTask>;

              next[idx] = { ...currentTask, ...patch };
            } else {
              // 任务不在本地列表时触发一次刷新
              refreshDownloads();
            }
            return next;
          });
        } else if (data.type === 'subtitle_progress') {
          const taskId = data.task_id;
          setSubtitleTasks(prev => {
            const next = [...prev];
            const idx = next.findIndex(t => t.id === taskId);
            if (idx >= 0) {
              const nextProgress = typeof data.progress === 'number' ? normalizeProgress(data.progress) : next[idx].progress;
              next[idx] = {
                ...next[idx],
                progress: nextProgress,
                message: data.message ?? next[idx].message,
                status: data.status || next[idx].status
              };
            } else {
              refreshSubtitles();
            }
            return next;
          });
        } else if (data.type === 'burn_progress') {
          const taskId = data.task_id;
          setBurnTasks(prev => {
            const next = [...prev];
            const idx = next.findIndex(t => t.id === taskId);
            if (idx >= 0) {
              const nextProgress = typeof data.progress === 'number' ? normalizeProgress(data.progress) : next[idx].progress;
              next[idx] = {
                ...next[idx],
                progress: nextProgress,
                current: data.current ?? next[idx].current,
                duration: data.duration ?? next[idx].duration,
                status: data.status || next[idx].status
              };
            } else {
              refreshBurns();
            }
            return next;
          });
        } else if (data.type === 'subtitle_task_complete') {
          refreshSubtitles();
        } else if (data.type === 'burn_subtitle_task_complete') {
          refreshBurns();
        }
      } catch (err) {
        console.warn('[TaskProgress] WS parse error', err);
      }
    });

    return () => {
      unsubscribe();
    };
    // refreshDownloads/refreshSubtitles/refreshBurns are stable useCallback
  }, [refreshBurns, refreshDownloads, refreshSubtitles]);

  // 轮询逻辑（依赖数据获取函数）
  useEffect(() => {
    let isMounted = true;

    const schedule = (delayMs: number) => {
      if (!isMounted) return;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(run, delayMs);
    };

    const run = async () => {
      if (!isMounted) return;
      try {
        const [dl, sub, burn] = await Promise.all([
          fetchDownloads().catch(() => []),
          fetchSubtitleTasks().catch(() => []),
          fetchBurnTasks().catch(() => []),
        ]);

        setDownloads(dl);
        setSubtitleTasks(sub);
        setBurnTasks(burn);

        processTransitions(
          dl,
          prevDownloadStatus,
          (t: DownloadTask) => `${t.title || '下载任务'} 已完成`,
          (t: DownloadTask) => `${t.title || '下载任务'} 下载失败`,
          ACTIVE_DOWNLOAD
        );

        processTransitions(
          sub,
          prevSubtitleStatus,
          (t: SubtitleTask) => `${t.video_title || '字幕任务'} 已完成`,
          (t: SubtitleTask) => `${t.video_title || '字幕任务'} 失败`,
          ACTIVE_SUBTITLE
        );

        processTransitions(
          burn,
          prevBurnStatus,
          (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 已完成`,
          (t: BurnSubtitleTask) => `${t.video_title || '烧录任务'} 失败`,
          ACTIVE_BURN
        );

        const active =
          dl.some(t => ACTIVE_DOWNLOAD.has(t.status)) ||
          sub.some(t => ACTIVE_SUBTITLE.has(t.status)) ||
          burn.some(t => ACTIVE_BURN.has(t.status));

        const wsConnected = isSharedWebSocketConnected();
        const delayMs = active ? (wsConnected ? 15000 : 5000) : 30000;
        schedule(delayMs);
      } catch {
        schedule(15000);
      }
    };

    run();

    return () => {
      isMounted = false;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, [fetchBurnTasks, fetchDownloads, fetchSubtitleTasks, processTransitions]);

  return (
    <TaskProgressContext.Provider
      value={{
        downloads,
        subtitleTasks,
        burnTasks,
        loading,
        refreshAll,
        refreshDownloads,
        refreshSubtitles,
        refreshBurns,
        videoInfoState,
        setVideoInfoUrl,
        setVideoInfo,
        setVideoInfoLoading,
        setVideoCookieWarning,
        clearVideoInfo,
      }}
    >
      {children}
    </TaskProgressContext.Provider>
  );
}

export function useTaskProgress() {
  const ctx = useContext(TaskProgressContext);
  if (!ctx) {
    throw new Error('useTaskProgress must be used within TaskProgressProvider');
  }
  return ctx;
}
