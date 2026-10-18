/**
 * Download Manager - 适配 VidFlow-Tauri UI
 */
import { useEffect, useState } from 'react';
import { invoke } from './TauriIntegration';
import { useSettings } from '../contexts/SettingsContext';
import { useTaskProgress, DownloadTask, VideoInfo } from '../contexts/TaskProgressContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Badge } from './ui/badge';
import { Label } from './ui/label';
import { ScrollArea } from './ui/scroll-area';
import { Separator } from './ui/separator';
import {
  Download,
  Clock,
  CheckCircle,
  AlertCircle,
  Youtube,
  PlayCircle,
  Music,
  Video,
  Globe,
  Loader2,
  Cookie,
  Settings,
  Pause
} from 'lucide-react';
import { toast } from 'sonner';
import { DownloadTaskCard } from './DownloadTaskCard';
import { getProxiedImageUrl, handleImageError } from '../utils/imageProxy';

// 格式化时长（秒 -> 分:秒 或 时:分:秒）
function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return '未知';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  } else {
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  }
}

const platformConfig: Record<string, { icon: any, color: string, name: string }> = {
  youtube: { icon: Youtube, color: 'bg-red-500', name: 'YouTube' },
  bilibili: { icon: PlayCircle, color: 'bg-pink-500', name: 'Bilibili' },
  douyin: { icon: Music, color: 'bg-black', name: '抖音' },
  tiktok: { icon: Music, color: 'bg-black', name: 'TikTok' },
  weixin: { icon: Video, color: 'bg-green-500', name: '微信视频号' },
  xiaohongshu: { icon: PlayCircle, color: 'bg-red-400', name: '小红书' },
  xinpianchang: { icon: PlayCircle, color: 'bg-emerald-600', name: '新片场' },
  iqiyi: { icon: PlayCircle, color: 'bg-green-600', name: '爱奇艺' },
  youku: { icon: PlayCircle, color: 'bg-blue-500', name: '优酷' },
  tencent: { icon: PlayCircle, color: 'bg-blue-600', name: '腾讯视频' },
  twitter: { icon: PlayCircle, color: 'bg-sky-500', name: 'Twitter/X' },
  instagram: { icon: PlayCircle, color: 'bg-pink-600', name: 'Instagram' },
  facebook: { icon: PlayCircle, color: 'bg-blue-700', name: 'Facebook' },
  generic: { icon: Globe, color: 'bg-gray-500', name: '通用下载' }
};

// 需要Cookie的平台列表（有反爬虫机制或需要登录）
// 与后端 cookie_helper.py 中的 PLATFORM_URLS 保持一致
export const PLATFORMS_REQUIRING_COOKIE = [
  'douyin',      // 抖音
  'tiktok',      // TikTok
  'xiaohongshu', // 小红书
  'xinpianchang', // 新片场
  'bilibili',    // B站（部分会员内容）
  'youtube',     // YouTube（部分会员内容）
  'twitter',     // Twitter/X
  'instagram'    // Instagram
];

// 平台检测函数（与后端 downloader_factory.py 保持一致）
export function detectPlatform(url: string): string {
  const urlLower = url.toLowerCase();
  if (urlLower.includes('youtube.com') || urlLower.includes('youtu.be')) return 'youtube';
  if (urlLower.includes('bilibili.com') || urlLower.includes('b23.tv')) return 'bilibili';
  if (urlLower.includes('douyin.com') || urlLower.includes('v.douyin.com')) return 'douyin';
  if (urlLower.includes('tiktok.com')) return 'tiktok';
  if (urlLower.includes('xiaohongshu.com') || urlLower.includes('xhslink.com')) return 'xiaohongshu';
  if (urlLower.includes('xinpianchang.com')) return 'xinpianchang';
  if (urlLower.includes('weixin') || urlLower.includes('qq.com/channels')) return 'weixin';
  if (urlLower.includes('v.qq.com')) return 'tencent';
  if (urlLower.includes('youku.com')) return 'youku';
  if (urlLower.includes('iqiyi.com')) return 'iqiyi';
  if (urlLower.includes('twitter.com') || urlLower.includes('x.com')) return 'twitter';
  if (urlLower.includes('instagram.com')) return 'instagram';
  if (urlLower.includes('facebook.com')) return 'facebook';
  return 'generic';
}

export function getPlatformConfig(platform?: string) {
  return platformConfig[platform?.toLowerCase() || ''] || platformConfig.generic;
}

export function isPlatformCookieConfigured(
  status: { platforms?: Array<{ platform: string; configured: boolean }> } | undefined,
  platform: string,
): boolean {
  return status?.platforms?.some(
    (item) => item.platform === platform && item.configured,
  ) ?? false;
}

export function isXinpianchangSecurityError(platform: string, error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '');
  return platform === 'xinpianchang' && /(?:403|forbidden)/i.test(message);
}

export interface VideoQualityOption {
  value: string;
  label: string;
}

const DEFAULT_VIDEO_QUALITY_OPTIONS: VideoQualityOption[] = [
  { value: 'best', label: '最佳质量' },
  { value: '2160p', label: '4K (2160p)' },
  { value: '1440p', label: '2K (1440p)' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '480p', label: '480p' },
  { value: 'audio', label: '仅音频' },
];

export function getAvailableVideoQualityOptions(
  platform: string | undefined,
  formats: VideoInfo['formats'] | undefined,
): VideoQualityOption[] {
  if (platform !== 'xinpianchang') {
    return DEFAULT_VIDEO_QUALITY_OPTIONS;
  }

  const heights = Array.from(new Set(
    (formats || [])
      .map(format => Number(format.height || 0))
      .filter(height => Number.isFinite(height) && height > 0),
  )).sort((left, right) => right - left);

  if (heights.length === 0) {
    return DEFAULT_VIDEO_QUALITY_OPTIONS;
  }

  return [
    { value: 'best', label: '最佳质量' },
    ...heights.map(height => ({ value: `${height}p`, label: `${height}p` })),
    { value: 'audio', label: '仅音频' },
  ];
}

export function resolveVideoQualitySelection(
  selected: string,
  options: VideoQualityOption[],
): string {
  if (options.some(option => option.value === selected)) {
    return selected;
  }

  const requestedMatch = selected.match(/^(\d{3,4})p?$/i);
  const numericOptions = options
    .map(option => ({ option, match: option.value.match(/^(\d{3,4})p$/i) }))
    .filter(entry => entry.match)
    .map(entry => ({ option: entry.option, height: Number(entry.match?.[1]) }))
    .sort((left, right) => right.height - left.height);

  if (requestedMatch && numericOptions.length > 0) {
    const requestedHeight = Number(requestedMatch[1]);
    return (
      numericOptions.find(entry => entry.height <= requestedHeight) ||
      numericOptions[numericOptions.length - 1]
    ).option.value;
  }

  return numericOptions[0]?.option.value || 'best';
}

interface DownloadManagerProps {
  onNavigateToSettings?: (targetPlatform?: string, targetUrl?: string) => void;
}

export function DownloadManager({ onNavigateToSettings }: DownloadManagerProps = {}) {
  const { settings } = useSettings();
  const {
    downloads: currentTasks,
    refreshDownloads,
    videoInfoState,
    setVideoInfoUrl,
    setVideoInfo,
    setVideoInfoLoading,
    setVideoCookieWarning,
    clearVideoInfo,
  } = useTaskProgress();

  // 从全局状态获取
  const url = videoInfoState.url;
  const videoInfo = videoInfoState.info;
  const infoLoading = videoInfoState.loading;
  const cookieWarning = videoInfoState.cookieWarning;

  // 本地状态
  const [selectedQuality, setSelectedQuality] = useState(settings.defaultQuality || 'best');
  const [selectedFormat, setSelectedFormat] = useState(settings.defaultFormat || 'mp4');
  const [loading, setLoading] = useState(false); // downloading state
  const [thumbnailError, setThumbnailError] = useState(false); // thumbnail load error
  const qualityOptions = getAvailableVideoQualityOptions(videoInfo?.platform, videoInfo?.formats);

  useEffect(() => {
    if (!videoInfo) return;
    const options = getAvailableVideoQualityOptions(videoInfo.platform, videoInfo.formats);
    setSelectedQuality(current => resolveVideoQualitySelection(current, options));
  }, [videoInfo]);

  // 设置 URL
  const setUrl = (newUrl: string) => {
    setVideoInfoUrl(newUrl);
  };

  // 打开文件夹
  const handleOpenFolder = async (filePath?: string) => {
    if (!filePath) {
      toast.error('文件路径不存在');
      return;
    }

    try {
      // 检查是否在 Electron 环境
      if (window.electron && window.electron.isElectron) {
        await window.electron.showItemInFolder(filePath);
      } else {
        // 浏览器环境降级
        toast.info('打开文件夹', {
          description: '浏览器环境不支持此功能，请使用 Electron 版本'
        });
      }
    } catch (error) {
      toast.error('打开文件夹失败', {
        description: error instanceof Error ? error.message : '未知错误'
      });
    }
  };

  // URL格式验证
  const isValidUrl = (urlString: string): boolean => {
    // 检查是否包含协议
    if (!urlString.startsWith('http://') && !urlString.startsWith('https://')) {
      return false;
    }

    try {
      new URL(urlString);
      return true;
    } catch {
      return false;
    }
  };

  // 检查Cookie状态
  const checkCookieStatus = async (platform: string): Promise<boolean> => {
    try {
      const status = await invoke('get_cookies_status');
      return isPlatformCookieConfigured(status, platform);
    } catch (error) {
      console.error('Failed to check cookie status:', error);
      return false;
    }
  };

  // 清理和提取 URL
  const cleanUrl = (text: string): string => {
    // 移除首尾空格
    text = text.trim();

    // 使用正则提取 URL
    const urlPattern = /(https?:\/\/[^\s]+)/gi;
    const matches = text.match(urlPattern);

    if (matches && matches.length > 0) {
      // 获取第一个匹配的 URL
      let cleanedUrl = matches[0];

      // 移除 URL 末尾可能的特殊字符和文本
      // 例如：https://v.douyin.com/xxx/ M@W.zt 09/29 UlC:/
      cleanedUrl = cleanedUrl.split(/\s/)[0]; // 按空格分割，取第一部分

      // 移除末尾的特殊字符（但保留 / ? # & = 等有效字符）
      cleanedUrl = cleanedUrl.replace(/[^\w\-._~:/?#[\]@!$&'()*+,;=%]+$/, '');

      return cleanedUrl;
    }

    return text;
  };

  // 获取视频信息
  const handleGetInfo = async () => {
    const cleanedUrl = cleanUrl(url);

    // 如果清理后的 URL 与原始输入不同，更新输入框
    if (cleanedUrl !== url) {
      setUrl(cleanedUrl);
    }

    if (!cleanedUrl) {
      toast.error('请输入视频链接');
      return;
    }

    // URL格式验证
    if (!isValidUrl(cleanedUrl)) {
      toast.error('请输入有效的视频链接', {
        description: '链接应以 http:// 或 https:// 开头'
      });
      return;
    }

    // 检测平台
    const platform = detectPlatform(cleanedUrl);

    // 检查是否需要Cookie
    if (PLATFORMS_REQUIRING_COOKIE.includes(platform)) {
      const hasCookie = await checkCookieStatus(platform);
      if (!hasCookie) {
        const platformNames: Record<string, string> = {
          douyin: '抖音',
          tiktok: 'TikTok',
          xiaohongshu: '小红书',
          xinpianchang: '新片场',
          instagram: 'Instagram',
          twitter: 'Twitter/X'
        };
        setVideoCookieWarning({
          platform,
          platformName: platformNames[platform] || platform
        });
        toast.warning(`${platformNames[platform]} 需要配置Cookie`, {
          description: '点击下方提示配置Cookie以获取更好的下载体验'
        });
      } else {
        setVideoCookieWarning(null);
      }
    } else {
      setVideoCookieWarning(null);
    }

    setVideoInfoLoading(true);
    setThumbnailError(false); // 重置缩略图错误状态
    try {
      const info = await invoke('get_video_info', { url: cleanedUrl });
      setVideoInfo(info as VideoInfo);
      toast.success('视频信息获取成功');
    } catch (error: any) {
      if (isXinpianchangSecurityError(platform, error)) {
        setVideoCookieWarning({
          platform,
          platformName: '新片场',
          reason: 'security_check',
        });
      }
      toast.error('获取视频信息失败', {
        description: error.message || '请检查链接是否正确'
      });
      setVideoInfo(null);
    } finally {
      setVideoInfoLoading(false);
    }
  };

  // 开始下载
  const handleDownload = async () => {
    const cleanedUrl = cleanUrl(url);

    // 更新输入框
    if (cleanedUrl !== url) {
      setUrl(cleanedUrl);
    }

    if (!cleanedUrl) {
      toast.error('请输入视频链接');
      return;
    }

    // URL格式验证
    if (!isValidUrl(cleanedUrl)) {
      toast.error('请输入有效的视频链接', {
        description: '链接应以 http:// 或 https:// 开头'
      });
      return;
    }

    setLoading(true);
    try {
      const result = await invoke('start_download', {
        url: cleanedUrl,
        quality: selectedQuality,
        format_id: selectedFormat,
        output_path: settings.downloadPath || undefined, // 使用设置中的下载路径
      });
      toast.success(`下载任务已创建！任务ID: ${result.task_id}`);
      clearVideoInfo(); // 清空全局状态
      await refreshDownloads();
    } catch (error: any) {
      toast.error('开始下载失败', {
        description: error.message || '请检查链接是否正确'
      });
    } finally {
      setLoading(false);
    }
  };

  // 删除任务（同时删除本机文件）
  const handleDeleteTask = async (taskId: string) => {
    try {
      const res: any = await invoke('delete_download_task', { task_id: taskId });
      const fileDeleted = res?.file_deleted;
      toast.success(fileDeleted ? '任务和文件已删除' : '任务已删除');
      await refreshDownloads();
    } catch (error: any) {
      toast.error(`删除失败: ${error.message}`);
    }
  };

  // 取消任务
  const handleCancelTask = async (taskId: string) => {
    try {
      await invoke('cancel_download_task', { task_id: taskId });
      toast.success('已发送取消请求');
      await refreshDownloads();
    } catch (error: any) {
      toast.error(`取消失败: ${error.message}`);
    }
  };

  // 暂停任务
  const handlePauseTask = async (taskId: string) => {
    try {
      await invoke('pause_download_task', { task_id: taskId });
      toast.success('下载已暂停');
      await refreshDownloads();
    } catch (error: any) {
      toast.error(`暂停失败: ${error.message}`);
    }
  };

  // 恢复任务
  const handleResumeTask = async (taskId: string) => {
    try {
      await invoke('resume_download_task', { task_id: taskId });
      toast.success('下载已恢复');
      await refreshDownloads();
    } catch (error: any) {
      toast.error(`恢复失败: ${error.message}`);
    }
  };

  // 重试失败的任务
  const handleRetryTask = async (task: DownloadTask) => {
    try {
      // 先删除失败的任务
      await invoke('delete_download_task', { task_id: task.task_id });

      // 重新提交下载
      const result = await invoke('start_download', {
        url: task.url,
        quality: task.quality || 'best',
        format_id: task.format_id,
        output_path: settings.downloadPath || undefined,
      });

      toast.success(`任务已重新提交！任务ID: ${result.task_id}`);
      await refreshDownloads();
    } catch (error: any) {
      toast.error('重试失败', {
        description: error.message || '请检查网络或配置'
      });
    }
  };

  // 获取状态徽章变体
  const getStatusBadge = (status: string) => {
    const badges: Record<string, { variant: any, icon: any, text: string }> = {
      pending: { variant: 'outline' as const, icon: Clock, text: '等待中' },
      downloading: { variant: 'default' as const, icon: Download, text: '下载中' },
      paused: { variant: 'secondary' as const, icon: Pause, text: '已暂停' },
      completed: { variant: 'default' as const, icon: CheckCircle, text: '已完成' },
      failed: { variant: 'destructive' as const, icon: AlertCircle, text: '失败' },
      cancelled: { variant: 'outline' as const, icon: AlertCircle, text: '已取消' },
    };
    return badges[status] || badges.pending;
  };

  return (
    <div className="space-y-6 p-6">
      {/* 输入区域 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Download className="size-5" />
            新建下载任务
          </CardTitle>
          <CardDescription>输入视频链接开始下载</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex gap-2">
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="输入视频链接（支持完整分享文本）"
                disabled={loading || infoLoading}
                onKeyDown={(e) => e.key === 'Enter' && handleGetInfo()}
                onBlur={() => {
                  // 失去焦点时自动清理 URL
                  const cleaned = cleanUrl(url);
                  if (cleaned !== url) {
                    setUrl(cleaned);
                  }
                }}
                onPaste={() => {
                  // 粘贴时自动清理 URL
                  setTimeout(() => {
                    const cleaned = cleanUrl(url);
                    if (cleaned !== url) {
                      setUrl(cleaned);
                    }
                  }, 0);
                }}
                className={url.trim() && !isValidUrl(cleanUrl(url)) ? 'border-destructive' : ''}
              />
              <Button
                onClick={handleGetInfo}
                disabled={loading || infoLoading}
                variant="secondary"
              >
                {infoLoading ? (
                  <>
                    <Loader2 className="mr-2 size-4 animate-spin" />
                    获取中...
                  </>
                ) : (
                    <>获取信息</>
                )}
              </Button>
            </div>
            {url.trim() && !isValidUrl(url.trim()) && (
              <p className="text-sm text-destructive flex items-center gap-1">
                <AlertCircle className="size-3" />
                请输入有效的视频链接（需以 http:// 或 https:// 开头）
              </p>
            )}

            {/* Cookie 警告提示 */}
            {cookieWarning && (
              <div className="bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                <div className="flex items-start gap-3">
                  <Cookie className="size-5 text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-amber-900 dark:text-amber-100 mb-1">
                      {cookieWarning.reason === 'security_check'
                        ? '新片场需要完成安全验证'
                        : `${cookieWarning.platformName} 需要配置 Cookie`}
                    </h4>
                    <p className="text-sm text-amber-800 dark:text-amber-200 mb-2">
                      {cookieWarning.reason === 'security_check' ? (
                        <>请在受控浏览器打开的视频详情页完成勾选验证，然后点击“完成登录”。</>
                      ) : (
                        <>
                          该平台有反爬虫机制，配置Cookie后可以获得更好的下载体验。
                          <br />
                          <span className="text-xs">注：未配置Cookie可能导致下载失败或只能下载低画质视频</span>
                        </>
                      )}
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      className="bg-white dark:bg-gray-900"
                      onClick={() => {
                        onNavigateToSettings?.(cookieWarning.platform, cleanUrl(url));
                      }}
                    >
                      <Settings className="size-3 mr-2" />
                      {cookieWarning.reason === 'security_check' ? '前往安全验证' : '前往配置 Cookie'}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 加载骨架 */}
          {infoLoading && (
            <div className="space-y-4 p-4 border rounded-lg">
              <div className="flex gap-4 animate-pulse">
                <div className="w-32 h-32 bg-muted rounded" />
                <div className="flex-1 space-y-2">
                  <div className="h-5 bg-muted rounded w-3/4" />
                  <div className="h-4 bg-muted rounded w-1/2" />
                  <div className="h-4 bg-muted rounded w-1/3" />
                </div>
              </div>
              <Separator />
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <div className="h-4 bg-muted rounded w-24" />
                  <div className="h-10 bg-muted rounded" />
                </div>
                <div className="space-y-2">
                  <div className="h-4 bg-muted rounded w-16" />
                  <div className="h-10 bg-muted rounded" />
                </div>
              </div>
              <div className="h-10 bg-muted rounded" />
            </div>
          )}

          {/* 视频信息 */}
          {videoInfo && (
            <div className="space-y-4 p-4 border rounded-lg bg-muted/50">
              <div className="flex gap-4">
                {videoInfo.thumbnail && !thumbnailError ? (
                  <img
                    src={getProxiedImageUrl(videoInfo.thumbnail)}
                    alt={videoInfo.title}
                    className="w-32 h-32 object-cover rounded bg-muted"
                    onError={(e) => {
                      handleImageError(e);
                      setThumbnailError(true);
                    }}
                  />
                ) : (
                  <div className="w-32 h-32 bg-muted rounded flex items-center justify-center">
                    <Video className="size-12 text-muted-foreground/50" />
                  </div>
                )}
                <div className="flex-1 space-y-2">
                  <h3 className="font-semibold">{videoInfo.title}</h3>
                  <div className="flex gap-4 text-sm text-muted-foreground">
                    {videoInfo.platform && (() => {
                      const config = getPlatformConfig(videoInfo.platform);
                      const PlatformIcon = config.icon;
                      return (
                        <span className="flex items-center gap-1">
                          <PlatformIcon className="size-4" />
                          <span>{config.name}</span>
                        </span>
                      );
                    })()}
                    {videoInfo.duration && <span>时长: {formatDuration(videoInfo.duration)}</span>}
                  </div>
                  {/* 智能下载器信息 */}
                  {videoInfo.downloader_used && (
                    <div className="flex items-center gap-2 text-xs">
                      <Badge variant={videoInfo.fallback_used ? "secondary" : "outline"} className="text-xs">
                        {videoInfo.downloader_used === 'generic' ? '通用下载器' : `${videoInfo.downloader_used} 专用下载器`}
                      </Badge>
                      {videoInfo.fallback_used && (
                        <span className="text-amber-600 dark:text-amber-400">
                          (已自动回退)
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <Separator />

              <div className="flex gap-2">
                <div className="flex-1">
                  <Label>视频质量</Label>
                  <Select value={selectedQuality} onValueChange={setSelectedQuality}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {qualityOptions.map(option => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="flex-1">
                  <Label>格式</Label>
                  <Select value={selectedFormat} onValueChange={setSelectedFormat}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="mp4">MP4</SelectItem>
                      <SelectItem value="mkv">MKV</SelectItem>
                      <SelectItem value="webm">WebM</SelectItem>
                      <SelectItem value="mp3">MP3 (音频)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button
                onClick={handleDownload}
                disabled={loading}
                className="w-full"
              >
                <Download className="mr-2 size-4" />
                开始下载
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 任务列表 */}
      <Card>
        <CardHeader>
          <CardTitle>下载任务</CardTitle>
          <CardDescription>
            共 {currentTasks.length} 个任务
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[400px]">
            {currentTasks.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Download className="size-12 mx-auto mb-4 opacity-20" />
                <p>暂无下载任务</p>
              </div>
            ) : (
              <div className="space-y-3">
                {currentTasks.map((task) => {
                  const statusInfo = getStatusBadge(task.status);
                  const platform = getPlatformConfig(task.platform);

                  return (
                    <DownloadTaskCard
                      key={task.task_id}
                      task={task}
                      statusInfo={statusInfo}
                      platformConfig={platform}
                      onDelete={handleDeleteTask}
                      onPause={handlePauseTask}
                      onResume={handleResumeTask}
                      onCancel={handleCancelTask}
                      onRetry={handleRetryTask}
                      onOpenFolder={handleOpenFolder}
                    />
                  );
                })}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
