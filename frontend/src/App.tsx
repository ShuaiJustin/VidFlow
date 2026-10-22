import { useState, useEffect } from 'react';
import { TauriProvider, invoke } from './components/TauriIntegration';
import { SettingsProvider } from './contexts/SettingsContext';
import { BackendProvider, useBackend } from './contexts/BackendContext';
import { InstallProgressProvider } from './contexts/InstallProgressContext';
import { TaskProgressProvider } from './contexts/TaskProgressContext';
import { ContextMenuProvider } from './components/ContextMenu';
import packageJson from '../package.json';
import { DownloadManager } from './components/DownloadManager';
import { TaskManager } from './components/TaskManager';
import { SettingsPanel } from './components/SettingsPanel';
import { SubtitleProcessor } from './components/SubtitleProcessor';
import BurnSubtitle from './components/BurnSubtitle';
import { ChannelsPanel } from './components/channels';
import { LogViewer } from './components/LogViewer';
import { CustomUpdateNotification } from './components/CustomUpdateNotification';
import AboutDialog from './components/AboutDialog';
import { Toaster } from './components/ui/sonner';
import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Alert, AlertDescription, AlertTitle } from './components/ui/alert';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './components/ui/tooltip';
import {
  Wifi,
  WifiOff,
  Download,
  List,
  Settings,
  FileText,
  ChevronLeft,
  ChevronRight,
  ScrollText,
  Shield,
  ShieldCheck,
  ShieldAlert,
  Loader2,
  Minus,
  Square,
  X,
  Film,
  Copy,
  AlertCircle,
  RefreshCw,
  Video
} from 'lucide-react';

interface ProxyStatus {
  available: boolean;
  proxy_type?: string;
  proxy_url?: string;
  response_time?: number;
  error?: string;
}

function AppContent() {
  const isMacOS = window.electron?.platform === 'darwin';
  const { state: backendState, retry: retryBackend } = useBackend();
  const [isOnline] = useState(navigator.onLine);
  const [activeTab, setActiveTab] = useState('download');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [statusQueue, setStatusQueue] = useState<number>(0);
  const [statusCompleted, setStatusCompleted] = useState<number>(0);
  const [proxyStatus, setProxyStatus] = useState<ProxyStatus | null>(null);
  const [checkingProxy, setCheckingProxy] = useState(false);
  const [appVersion, setAppVersion] = useState<string>(packageJson.version);
  const [isMaximized, setIsMaximized] = useState(false);
  const [targetCookiePlatform, setTargetCookiePlatform] = useState<string | null>(null);
  const [targetCookieUrl, setTargetCookieUrl] = useState<string | null>(null);
  const [showAboutDialog, setShowAboutDialog] = useState(false);

  // 导航到设置页面的 Cookie 配置
  const handleNavigateToSettings = (platform?: string, targetUrl?: string) => {
    if (platform) {
      setTargetCookiePlatform(platform);
    }
    setTargetCookieUrl(targetUrl || null);
    setActiveTab('settings');
  };

  // 导航项
  const navigationItems = [
    { id: 'download', label: '下载中心', icon: Download, description: '新建和管理下载任务' },
    { id: 'tasks', label: '任务管理', icon: List, description: '查看历史和批量操作' },
    { id: 'channels', label: '视频号', icon: Video, description: '微信视频号下载' },
    { id: 'subtitle', label: '字幕处理', icon: FileText, description: 'AI 字幕生成和翻译' },
    { id: 'burn', label: '烧录字幕', icon: Film, description: '将字幕嵌入到视频中' },
    { id: 'logs', label: '日志中心', icon: ScrollText, description: '查看系统运行日志' },
    { id: 'settings', label: '系统设置', icon: Settings, description: '偏好设置和配置' }
  ].filter((item) => !isMacOS || item.id !== 'channels');

  // 检测代理
  const handleCheckProxy = async () => {
    setCheckingProxy(true);
    try {
      const result = await invoke('check_proxy');
      setProxyStatus(result as ProxyStatus);
    } catch (error) {
      setProxyStatus({
        available: false,
        error: error instanceof Error ? error.message : '检测失败'
      });
    } finally {
      setCheckingProxy(false);
    }
  };

  // 获取应用版本号
  useEffect(() => {
    const getVersion = async () => {
      if (window.electron?.getAppVersion) {
        try {
          const version = await window.electron.getAppVersion();
          setAppVersion(version);
        } catch (error) {
          console.error('Failed to get app version:', error);
        }
      }
    };
    getVersion();
  }, []);

  // 自动检测代理状态 (定期 + 网络变化)
  useEffect(() => {
    // 立即执行一次
    handleCheckProxy();

    // 每5分钟检测一次
    const intervalId = setInterval(() => {
      handleCheckProxy();
    }, 5 * 60 * 1000);

    // 监听网络状态变化
    const handleNetworkChange = () => {
      setTimeout(() => handleCheckProxy(), 1000);
    };

    window.addEventListener('online', handleNetworkChange);

    return () => {
      clearInterval(intervalId);
      window.removeEventListener('online', handleNetworkChange);
    };
  }, []);

  // 监听窗口最大化状态
  useEffect(() => {
    if (window.electron) {
      const handleWindowStateChange = (state: { isMaximized: boolean }) => {
        setIsMaximized(state.isMaximized);
      };

      window.electron.on('window-state-changed', handleWindowStateChange);

      return () => {
        window.electron?.off('window-state-changed', handleWindowStateChange);
      };
    }
  }, []);

  // 监听托盘"关于"菜单点击
  useEffect(() => {
    if (window.electron) {
      const handleShowAbout = () => {
        setShowAboutDialog(true);
      };

      window.electron.on('show-about', handleShowAbout);

      return () => {
        window.electron?.off('show-about', handleShowAbout);
      };
    }
  }, []);

  // 获取队列状态
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const [q, tasks] = await Promise.all([
          invoke('get_queue_status'),
          invoke('get_download_tasks')
        ]);
        setStatusQueue(Array.isArray(q) ? (q[0] as number) : 0);
        setStatusCompleted(Array.isArray(tasks) ? tasks.filter((t: any) => t.status === 'completed').length : 0);
      } catch (e) {
        console.error('获取状态失败', e);
      }
    };

    fetchStats();
    const interval = setInterval(fetchStats, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <SettingsProvider>
      <TauriProvider>
        <InstallProgressProvider>
          <TaskProgressProvider>
            <TooltipProvider delayDuration={100} disableHoverableContent>
        <div className="h-screen bg-background flex flex-col overflow-hidden">
          {/* Backend Status Alert */}
          {backendState.status === 'failed' && (
            <Alert variant="destructive" className="m-4 rounded-lg">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>后端连接失败</AlertTitle>
              <AlertDescription className="mt-2 flex items-center gap-3">
                <span className="flex-1">
                  {backendState.error || '无法连接到后端服务'}
                  {backendState.retryCount > 0 && ` (已重试 ${backendState.retryCount} 次)`}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={retryBackend}
                  className="shrink-0"
                >
                  <RefreshCw className="h-3.5 w-3.5 mr-2" />
                  重试连接
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {/* Title Bar */}
          <div
            className={`h-12 bg-background border-b border-border flex items-center justify-between select-none ${isMaximized ? 'px-3' : 'px-6'}`}
            style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
            onDoubleClick={() => window.electron?.maximize()}
          >
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-md overflow-hidden shadow-sm border border-border/50 flex items-center justify-center bg-gradient-to-br from-[#2c2c2c] to-[#1a1a1a]">
                  <svg width="22" height="22" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
                    <defs>
                      <linearGradient id="highlight-mini" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style={{stopColor:'#ffffff', stopOpacity:0.14}} />
                        <stop offset="35%" style={{stopColor:'#ffffff', stopOpacity:0.04}} />
                        <stop offset="100%" style={{stopColor:'#ffffff', stopOpacity:0}} />
                      </linearGradient>
                    </defs>
                    <rect x="0" y="0" width="256" height="256" fill="url(#highlight-mini)"/>
                    <g transform="translate(128, 128)" fill="none" stroke="white" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="0" cy="-34" r="40" fill="white" fillOpacity="0.12" strokeWidth="14" strokeOpacity="0.95"/>
                      <path d="M -12 -50 L -12 -18 L 16 -34 Z" fill="white" stroke="none"/>
                      <path d="M 0 6 L 0 54" strokeWidth="20"/>
                      <path d="M -34 54 L 0 86 L 34 54" strokeWidth="20"/>
                      <rect x="-44" y="100" width="88" height="8" fill="white" stroke="none" rx="4" opacity="0.65"/>
                    </g>
                  </svg>
                </div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-[15px] tracking-tight">VidFlow Desktop</span>
                  <Badge
                    variant="secondary"
                    className="text-[10px] px-1.5 py-0 h-4 font-semibold bg-primary text-primary-foreground border-0 shadow-sm rounded-full"
                  >
                    v{appVersion}
                  </Badge>
                </div>
              </div>

              {!isOnline && (
                <Badge variant="destructive" className="text-xs px-2 h-5 gap-1">
                  <WifiOff className="size-3" />
                  离线
                </Badge>
              )}
            </div>

            <div className="flex items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
              {/* 网络状态 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="p-1.5 hover:bg-muted rounded-md cursor-default transition-colors">
                    {isOnline ? (
                      <Wifi className="size-4 text-green-500" />
                    ) : (
                      <WifiOff className="size-4 text-red-500" />
                    )}
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{isOnline ? '网络连接正常' : '网络连接断开'}</p>
                </TooltipContent>
              </Tooltip>

              {/* 代理检测 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={handleCheckProxy}
                    disabled={checkingProxy}
                  >
                    {checkingProxy ? (
                      <Loader2 className="size-4 animate-spin text-blue-500" />
                    ) : proxyStatus === null ? (
                      <Shield className="size-4 text-muted-foreground" />
                    ) : proxyStatus.available ? (
                      <ShieldCheck className="size-4 text-green-500" />
                    ) : (
                      <ShieldAlert className="size-4 text-orange-500" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {checkingProxy ? (
                    <p>自动检查中...</p>
                  ) : proxyStatus === null ? (
                    <p>代理状态（点击刷新）</p>
                  ) : proxyStatus.available ? (
                    <div className="space-y-1">
                      <p className="font-semibold text-green-500">✓ 代理可用</p>
                      <p className="text-xs">类型: {proxyStatus.proxy_type}</p>
                      {proxyStatus.response_time && (
                        <p className="text-xs">响应: {proxyStatus.response_time}ms</p>
                      )}
                      {proxyStatus.proxy_url && (
                        <p className="text-xs truncate">地址: {proxyStatus.proxy_url}</p>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-1">
                      <p className="font-semibold text-orange-500">✗ 代理不可用</p>
                      {proxyStatus.error && (
                        <p className="text-xs">{proxyStatus.error}</p>
                      )}
                      {proxyStatus.proxy_url && (
                        <p className="text-xs truncate">地址: {proxyStatus.proxy_url}</p>
                      )}
                    </div>
                  )}
                </TooltipContent>
              </Tooltip>

              {/* 窗口控制按钮 */}
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 hover:bg-muted"
                  onClick={() => window.electron?.minimize()}
                >
                  <Minus className="size-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 hover:bg-muted"
                  onClick={() => window.electron?.maximize()}
                >
                  {isMaximized ? <Copy className="size-3.5" /> : <Square className="size-3.5" />}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0 hover:bg-destructive hover:text-destructive-foreground"
                  onClick={() => window.electron?.close()}
                >
                  <X className="size-4" />
                </Button>
              </div>
            </div>
          </div>

          <div className="flex-1 flex overflow-hidden">
            {/* Sidebar Navigation */}
            <div className={`${sidebarCollapsed ? 'w-16' : 'w-64'} bg-muted/30 border-r border-border flex flex-col transition-all duration-200 sidebar-transition`}>
              {/* Sidebar Header */}
              <div className="p-4 border-b border-border">
                <div className="flex items-center justify-between">
                  {!sidebarCollapsed && (
                    <div>
                      <h2 className="font-semibold">VidFlow</h2>
                    </div>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
                        className="h-8 w-8 p-0"
                      >
                        {sidebarCollapsed ? <ChevronRight className="size-4" /> : <ChevronLeft className="size-4" />}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">
                      <p>{sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}</p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              </div>

              {/* Navigation Items */}
              <div className="flex-1 p-2 space-y-1 overflow-auto">
                {navigationItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = activeTab === item.id;

                  return (
                    <Tooltip key={item.id} delayDuration={0}>
                      <TooltipTrigger asChild>
                        <Button
                          variant={isActive ? 'secondary' : 'ghost'}
                          className={`w-full justify-start gap-3 ${sidebarCollapsed ? 'px-0 justify-center' : ''}`}
                          onClick={() => setActiveTab(item.id)}
                        >
                          <Icon className={`size-4 ${isActive ? 'text-primary' : ''}`} />
                          {!sidebarCollapsed && (
                            <span className="flex-1 text-left">{item.label}</span>
                          )}
                        </Button>
                      </TooltipTrigger>
                      {sidebarCollapsed && (
                        <TooltipContent side="right" sideOffset={12} align="center">
                          <p className="font-medium">{item.label}</p>
                          <p className="text-xs text-muted-foreground">{item.description}</p>
                        </TooltipContent>
                      )}
                    </Tooltip>
                  );
                })}
              </div>

              {/* Sidebar Footer */}
              <div className="p-4 border-t border-border">
                {!sidebarCollapsed && (
                  <div className="text-xs text-muted-foreground space-y-1">
                    <div className="flex justify-between">
                      <span>队列任务</span>
                      <span className="font-medium">{statusQueue}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>已完成</span>
                      <span className="font-medium text-green-600">{statusCompleted}</span>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Main Content */}
            <div className="flex-1 overflow-auto">
              {activeTab === 'download' && <DownloadManager onNavigateToSettings={handleNavigateToSettings} />}
              {activeTab === 'tasks' && <TaskManager />}
              {!isMacOS && activeTab === 'channels' && <ChannelsPanel />}
              {activeTab === 'subtitle' && <SubtitleProcessor />}
              {activeTab === 'burn' && <BurnSubtitle />}
              {activeTab === 'logs' && <LogViewer />}
              {activeTab === 'settings' && (
                <SettingsPanel
                  appVersion={appVersion}
                  targetCookiePlatform={targetCookiePlatform}
                  targetCookieUrl={targetCookieUrl}
                  onCookiePlatformHandled={() => {
                    setTargetCookiePlatform(null);
                    setTargetCookieUrl(null);
                  }}
                />
              )}
            </div>
          </div>
        </div>

        {/* Toast Notifications */}
        <Toaster />

        {/* Update Notification */}
        <CustomUpdateNotification />

        {/* About Dialog */}
        <AboutDialog
          isOpen={showAboutDialog}
          onClose={() => setShowAboutDialog(false)}
          version={appVersion}
        />
            </TooltipProvider>
          </TaskProgressProvider>
        </InstallProgressProvider>
      </TauriProvider>
    </SettingsProvider>
  );
}

function App() {
  return (
    <BackendProvider>
      <ContextMenuProvider>
        <AppContent />
      </ContextMenuProvider>
    </BackendProvider>
  );
}

export default App;
