import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Switch } from './ui/switch';
import { Slider } from './ui/slider';
import { Separator } from './ui/separator';
import { Badge } from './ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Progress } from './ui/progress';
import { toast } from 'sonner';
import { useSettings, Settings } from '../contexts/SettingsContext';
import { invoke } from './TauriIntegration';
import { ToolsConfig } from './ToolsConfig';
import { CookieManager } from './CookieManager';
import {
  FolderOpen,
  Save,
  RotateCcw,
  Download,
  Palette,
  Bell,
  Shield,
  Loader2,
  Settings as SettingsIcon,
  Sliders,
  Wrench,
  Cookie
} from 'lucide-react';

interface SettingsPanelProps {
  appVersion: string;
  targetCookiePlatform?: string | null;
  targetCookieUrl?: string | null;
  onCookiePlatformHandled?: () => void;
}

export function SettingsPanel({ appVersion, targetCookiePlatform, targetCookieUrl, onCookiePlatformHandled }: SettingsPanelProps) {
  const { settings, updateSettings, resetSettings } = useSettings();
  const [localSettings, setLocalSettings] = useState<Settings>(settings);
  const [hasChanges, setHasChanges] = useState(false);
  const [storageInfo, setStorageInfo] = useState<any>(null);
  const [clearingCache, setClearingCache] = useState(false);
  const [checkingAppUpdates, setCheckingAppUpdates] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeSettingsTab, setActiveSettingsTab] = useState('download');

  // 当有目标 Cookie 平台时，自动切换到 cookies 标签页
  useEffect(() => {
    if (targetCookiePlatform) {
      setActiveSettingsTab('cookies');
    }
  }, [targetCookiePlatform]);

  useEffect(() => {
    if (!hasChanges) {
      setLocalSettings(settings);
    }
  }, [settings, hasChanges]);

  const computeHasChanges = (a: Settings, b: Settings) => {
    return (
      a.downloadPath !== b.downloadPath ||
      a.defaultQuality !== b.defaultQuality ||
      a.defaultFormat !== b.defaultFormat ||
      a.maxConcurrentDownloads !== b.maxConcurrentDownloads ||
      a.autoSubtitle !== b.autoSubtitle ||
      a.autoTranslate !== b.autoTranslate ||
      a.theme !== b.theme ||
      a.language !== b.language ||
      a.notifications !== b.notifications ||
      a.autoUpdate !== b.autoUpdate ||
      a.saveHistory !== b.saveHistory
    );
  };

  const validateLocalSettings = (value: Settings) => {
    if (!value.downloadPath || !value.downloadPath.trim()) {
      toast.error('下载保存路径不能为空');
      return false;
    }

    if (value.maxConcurrentDownloads < 1 || value.maxConcurrentDownloads > 10) {
      toast.error('最大并发下载数必须在 1-10 之间');
      return false;
    }

    return true;
  };

  // 显示关于信息
  const handleShowAbout = () => {
    toast.info('关于 VidFlow', {
      description: `VidFlow v${appVersion}\nElectron + Python FastAPI`,
      duration: 5000
    });
  };

  const parseSizeToBytes = (value: string | undefined) => {
    if (!value) return 0;
    const trimmed = value.trim();
    const match = trimmed.match(/^([\d.]+)\s*([a-zA-Z]+)$/);
    if (!match) return 0;

    const num = Number.parseFloat(match[1]);
    if (Number.isNaN(num)) return 0;

    const unit = match[2].toUpperCase();
    const multipliers: Record<string, number> = {
      B: 1,
      KB: 1024,
      MB: 1024 ** 2,
      GB: 1024 ** 3,
      TB: 1024 ** 4,
    };

    return Math.round(num * (multipliers[unit] ?? 1));
  };

  const getStorageMetrics = () => {
    const databaseBytes = parseSizeToBytes(storageInfo?.database_size);
    const cacheBytes = parseSizeToBytes(storageInfo?.cache_size);
    const logsBytes = parseSizeToBytes(storageInfo?.logs_size);

    const computedTotalBytes = databaseBytes + cacheBytes + logsBytes;
    const totalBytes = Math.max(parseSizeToBytes(storageInfo?.total_size), computedTotalBytes);

    const pct = (bytes: number) => {
      if (totalBytes <= 0) return 0;
      return Math.min(100, Math.max(0, Math.round((bytes / totalBytes) * 100)));
    };

    return {
      totalBytes,
      databaseBytes,
      cacheBytes,
      logsBytes,
      databasePct: pct(databaseBytes),
      cachePct: pct(cacheBytes),
      logsPct: pct(logsBytes),
    };
  };

  // 获取存储信息
  const fetchStorageInfo = async () => {
    try {
      const info = await invoke('get_storage_info');
      setStorageInfo(info);
    } catch (error) {
      console.error('Failed to fetch storage info:', error);
    }
  };

  // 清理缓存
  const handleClearCache = async () => {
    setClearingCache(true);
    try {
      await invoke('clear_cache');
      toast.success('缓存已清理');
      await fetchStorageInfo(); // 刷新数据
    } catch (error) {
      toast.error('清理失败', {
        description: error instanceof Error ? error.message : '操作失败'
      });
    } finally {
      setClearingCache(false);
    }
  };

  const handleCheckForAppUpdates = async () => {
    if (!window.electron?.checkForUpdates) {
      toast.info('当前环境不支持应用更新检查', {
        description: '请使用 Electron 客户端以检查并准备桌面应用更新'
      });
      return;
    }

    setCheckingAppUpdates(true);
    toast.info('正在检查新版本...', {
      description: '如发现新版本，会在后台自动准备更新'
    });

    try {
      const result = await window.electron.checkForUpdates();
      if (!result.success) {
        toast.error('检查新版本失败', {
          description: result.error || '未知错误'
        });
        return;
      }

      const updateInfo = result.data;
      if (!updateInfo) {
        toast.error('检查新版本失败', {
          description: '更新服务没有返回有效结果'
        });
        return;
      }

      if (updateInfo.unavailable) {
        toast.warning('更新服务暂时不可用', {
          description: '稍后会自动重试，你也可以晚点再检查'
        });
        return;
      }

      if (!updateInfo.has_update) {
        if (!updateInfo.status || updateInfo.status === 'up_to_date') toast.success('当前已是最新版本');
        else toast.info('暂无可用更新', { description: updateInfo.rollout_message || '当前发布尚未对本设备开放' });
      }
    } catch (error) {
      toast.error('检查新版本失败', {
        description: error instanceof Error ? error.message : '未知错误'
      });
    } finally {
      setCheckingAppUpdates(false);
    }
  };

  // 加载存储信息
  useEffect(() => {
    fetchStorageInfo();
  }, []);

  // 更新本地设置
  const updateSetting = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setLocalSettings(prev => {
      const next = { ...prev, [key]: value };
      setHasChanges(computeHasChanges(next, settings));
      return next;
    });
  };

  // 保存设置
  const handleSave = async () => {
    try {
      if (!validateLocalSettings(localSettings)) {
        return;
      }

      const updates: Partial<Settings> = {};
      if (localSettings.downloadPath !== settings.downloadPath) updates.downloadPath = localSettings.downloadPath;
      if (localSettings.defaultQuality !== settings.defaultQuality) updates.defaultQuality = localSettings.defaultQuality;
      if (localSettings.defaultFormat !== settings.defaultFormat) updates.defaultFormat = localSettings.defaultFormat;
      if (localSettings.maxConcurrentDownloads !== settings.maxConcurrentDownloads) {
        updates.maxConcurrentDownloads = localSettings.maxConcurrentDownloads;
      }
      if (localSettings.autoSubtitle !== settings.autoSubtitle) updates.autoSubtitle = localSettings.autoSubtitle;
      if (localSettings.autoTranslate !== settings.autoTranslate) updates.autoTranslate = localSettings.autoTranslate;
      if (localSettings.theme !== settings.theme) updates.theme = localSettings.theme;
      if (localSettings.language !== settings.language) updates.language = localSettings.language;
      if (localSettings.notifications !== settings.notifications) updates.notifications = localSettings.notifications;
      if (localSettings.autoUpdate !== settings.autoUpdate) updates.autoUpdate = localSettings.autoUpdate;
      if (localSettings.saveHistory !== settings.saveHistory) updates.saveHistory = localSettings.saveHistory;
      const autoUpdateChanged = localSettings.autoUpdate !== settings.autoUpdate;

      setSaving(true);
      await updateSettings(updates);
      setHasChanges(false);
      toast.success('设置已保存', {
        description: autoUpdateChanged
          ? '配置已保存。自动更新偏好会在下次启动应用时生效'
          : '您的配置已成功保存到后端并生效'
      });
    } catch (error) {
      setHasChanges(false);
      toast.error('保存设置失败', {
        description: error instanceof Error ? error.message : '请检查后端连接'
      });
      setLocalSettings(settings);
    } finally {
      setSaving(false);
    }
  };

  // 重置设置
  const handleReset = async () => {
    try {
      await resetSettings();
      setHasChanges(false);
      toast.info('设置已重置', {
        description: '所有设置已恢复为默认值'
      });
    } catch (error) {
      toast.error('重置设置失败', {
        description: error instanceof Error ? error.message : '请检查后端连接'
      });
    }
  };

  // 选择文件夹
  const handleSelectFolder = async () => {
    try {
      // 检查是否在 Electron 环境
      if (window.electron && window.electron.isElectron) {
        const folderPath = await window.electron.selectDirectory();
        if (folderPath) {
          updateSetting('downloadPath', folderPath);
          toast.success('文件夹已选择', {
            description: '请点击“保存设置”使其生效'
          });
        }
      } else {
        // 浏览器环境降级
        toast.info('选择文件夹', {
          description: '浏览器环境不支持文件夹选择，请使用 Electron 版本'
        });
      }
    } catch (error) {
      toast.error('选择文件夹失败', {
        description: error instanceof Error ? error.message : '未知错误'
      });
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="border-b px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <SettingsIcon className="size-5" />
            <h2 className="text-xl font-semibold">系统设置</h2>
          </div>
          <div className="flex items-center gap-3">
            {hasChanges && (
              <span className="text-sm text-muted-foreground flex items-center gap-2">
                <Bell className="size-4" />
                有未保存的更改
              </span>
            )}
            <Button variant="outline" size="sm" onClick={handleReset}>
              <RotateCcw className="size-4 mr-2" />
              重置
            </Button>
            <Button size="sm" onClick={handleSave} disabled={!hasChanges || saving}>
              {saving ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  保存中...
                </>
              ) : (
                <>
                  <Save className="size-4 mr-2" />
                  保存设置
                </>
              )}
            </Button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeSettingsTab} onValueChange={setActiveSettingsTab} className="flex-1 flex flex-col">
        <TabsList className="w-full justify-start rounded-none border-b bg-transparent px-6 h-auto p-0">
          <TabsTrigger
            value="download"
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            下载设置
          </TabsTrigger>
          <TabsTrigger
            value="appearance"
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            外观设置
          </TabsTrigger>
          <TabsTrigger
            value="advanced"
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            高级设置
          </TabsTrigger>
          <TabsTrigger
            value="tools"
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            <Wrench className="size-4 mr-2" />
            工具管理
          </TabsTrigger>
          <TabsTrigger
            value="cookies"
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            <Cookie className="size-4 mr-2" />
            Cookie 管理
          </TabsTrigger>
        </TabsList>

        <TabsContent value="download" className="flex-1 overflow-auto">
          <div className="p-6 space-y-6 max-w-4xl">
            {/* 下载设置 */}
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Download className="size-5 text-primary" />
                  <CardTitle>下载设置</CardTitle>
                </div>
                <CardDescription>配置下载相关的默认参数</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* 下载路径 */}
                <div className="space-y-2">
                  <Label htmlFor="downloadPath">下载保存路径</Label>
                  <div className="flex gap-2">
                    <div className="flex-1 relative">
                      <Input
                        id="downloadPath"
                        value={localSettings.downloadPath}
                        onChange={(e) => updateSetting('downloadPath', e.target.value)}
                        placeholder="选择下载文件夹"
                        className="w-full pr-8"
                        readOnly
                        title={localSettings.downloadPath || '未设置下载路径'}
                      />
                      {localSettings.downloadPath && (
                        <div className="absolute right-2 top-1/2 -translate-y-1/2">
                          <span className="text-xs text-green-600 dark:text-green-400">✓</span>
                        </div>
                      )}
                    </div>
                    <Button variant="outline" onClick={handleSelectFolder}>
                      <FolderOpen className="size-4 mr-2" />
                      浏览
                    </Button>
                  </div>
                  {!localSettings.downloadPath && (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      ⚠ 未设置（将使用系统默认下载文件夹）
                    </p>
                  )}
                </div>

                <Separator />

                {/* 默认质量 */}
                <div className="space-y-2">
                  <Label htmlFor="defaultQuality">默认视频质量</Label>
                  <Select
                    value={localSettings.defaultQuality}
                    onValueChange={(value) => updateSetting('defaultQuality', value)}
                  >
                    <SelectTrigger id="defaultQuality">
                      <SelectValue placeholder="选择默认画质" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="2160p">4K (2160p)</SelectItem>
                      <SelectItem value="1440p">2K (1440p)</SelectItem>
                      <SelectItem value="1080p">Full HD (1080p)</SelectItem>
                      <SelectItem value="720p">HD (720p)</SelectItem>
                      <SelectItem value="480p">SD (480p)</SelectItem>
                      <SelectItem value="360p">低质量 (360p)</SelectItem>
                      <SelectItem value="audio">仅音频</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    新建下载任务时的默认质量选项
                  </p>
                </div>

                {/* 默认格式 */}
                <div className="space-y-2">
                  <Label htmlFor="defaultFormat">默认文件格式</Label>
                  <Select
                    value={localSettings.defaultFormat}
                    onValueChange={(value) => updateSetting('defaultFormat', value)}
                  >
                    <SelectTrigger id="defaultFormat">
                      <SelectValue placeholder="选择默认格式" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="mp4">MP4 (推荐)</SelectItem>
                      <SelectItem value="mkv">MKV</SelectItem>
                      <SelectItem value="webm">WebM</SelectItem>
                      <SelectItem value="mp3">MP3 (音频)</SelectItem>
                      <SelectItem value="m4a">M4A (音频)</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    下载视频时使用的默认容器格式
                  </p>
                </div>

                <Separator />

                {/* 并发下载数 */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="maxConcurrent">最大并发下载数</Label>
                    <Badge variant="secondary">{localSettings.maxConcurrentDownloads}</Badge>
                  </div>
                  <Slider
                    id="maxConcurrent"
                    min={1}
                    max={10}
                    step={1}
                    value={[localSettings.maxConcurrentDownloads]}
                    onValueChange={(value) => updateSetting('maxConcurrentDownloads', value[0])}
                    className="w-full"
                  />
                  <p className="text-xs text-muted-foreground">
                    同时进行的下载任务数量 (1-10)
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="appearance" className="flex-1 overflow-auto">
          <div className="p-6 space-y-6 max-w-4xl">
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Palette className="size-5 text-primary" />
                  <CardTitle>外观设置</CardTitle>
                </div>
                <CardDescription>自定义应用外观和主题</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-2">
                  <Label>主题</Label>
                  <Select value={localSettings.theme} onValueChange={(value) => updateSetting('theme', value as any)}>
                    <SelectTrigger>
                      <SelectValue placeholder="选择主题" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="light">浅色</SelectItem>
                      <SelectItem value="dark">深色</SelectItem>
                      <SelectItem value="auto">跟随系统</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <Separator />

                <div className="space-y-2">
                  <Label>语言</Label>
                  <Select value={localSettings.language} onValueChange={(value) => updateSetting('language', value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="选择语言" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="zh-CN">简体中文</SelectItem>
                      <SelectItem value="en">English</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* 高级设置 */}
        <TabsContent value="advanced" className="flex-1 overflow-auto">
          <div className="p-6 space-y-6 max-w-4xl">
            <Card>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <Sliders className="size-5 text-primary" />
                  <CardTitle>高级设置</CardTitle>
                </div>
                <CardDescription>高级功能和系统选项</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>桌面通知</Label>
                    <p className="text-sm text-muted-foreground">
                      任务完成时显示系统通知
                    </p>
                  </div>
                  <Switch
                    id="notifications"
                    checked={localSettings.notifications}
                    onCheckedChange={(checked) => updateSetting('notifications', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>自动更新</Label>
                    <p className="text-sm text-muted-foreground">
                      启动后自动检查新版本，并在后台准备更新；修改后下次启动生效
                    </p>
                  </div>
                  <Switch
                    id="autoUpdate"
                    checked={localSettings.autoUpdate}
                    onCheckedChange={(checked) => updateSetting('autoUpdate', checked)}
                  />
                </div>

                <Separator />

                <div className="rounded-lg border p-4 bg-muted/30">
                  <div className="flex items-center justify-between gap-4">
                    <div className="space-y-1">
                      <Label>应用更新</Label>
                      <p className="text-sm text-muted-foreground">
                        当前版本 v{appVersion}。支持后台静默下载，完成后只提示重启。
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      onClick={handleCheckForAppUpdates}
                      disabled={checkingAppUpdates}
                    >
                      {checkingAppUpdates ? (
                        <>
                          <Loader2 className="size-4 mr-2 animate-spin" />
                          检查中...
                        </>
                      ) : (
                        <>
                          <Download className="size-4 mr-2" />
                          检查新版本
                        </>
                      )}
                    </Button>
                  </div>
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>保存下载历史</Label>
                    <p className="text-sm text-muted-foreground">
                      记录所有下载任务的历史记录
                    </p>
                  </div>
                  <Switch
                    id="saveHistory"
                    checked={localSettings.saveHistory}
                    onCheckedChange={(checked) => updateSetting('saveHistory', checked)}
                  />
                </div>

                <Separator />

                {/* 存储信息 */}
                <div className="space-y-3">
                  <Label>存储管理</Label>
                  {storageInfo ? (
                    <>
                      {(() => {
                        const m = getStorageMetrics();
                        return (
                          <div className="rounded-lg border p-3 space-y-3 bg-muted/30">
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">总占用</span>
                              <span className="font-medium">{storageInfo.total_size || '0 B'}</span>
                            </div>

                            <div className="space-y-2">
                              <div className="space-y-1">
                                <div className="flex justify-between text-xs">
                                  <span className="text-muted-foreground">数据库</span>
                                  <span className="font-medium">{storageInfo.database_size || '0 B'}</span>
                                </div>
                                <Progress value={m.databasePct} className="h-2" />
                              </div>

                              <div className="space-y-1">
                                <div className="flex justify-between text-xs">
                                  <span className="text-muted-foreground">缓存</span>
                                  <span className="font-medium">{storageInfo.cache_size || '0 B'}</span>
                                </div>
                                <Progress value={m.cachePct} className="h-2" />
                              </div>

                              <div className="space-y-1">
                                <div className="flex justify-between text-xs">
                                  <span className="text-muted-foreground">日志</span>
                                  <span className="font-medium">{storageInfo.logs_size || '0 B'}</span>
                                </div>
                                <Progress value={m.logsPct} className="h-2" />
                              </div>
                            </div>

                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">下载历史</span>
                              <span className="font-medium">{storageInfo.download_history_count || 0} 条记录</span>
                            </div>
                          </div>
                        );
                      })()}
                    </>
                  ) : (
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">加载中...</span>
                    </div>
                  )}
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={handleClearCache}
                    disabled={clearingCache}
                  >
                    {clearingCache ? (
                      <>
                        <Loader2 className="size-4 mr-2 animate-spin" />
                        清理中...
                      </>
                    ) : (
                      <>
                        <Shield className="size-4 mr-2" />
                        清理缓存
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* 工具管理 */}
        <TabsContent value="tools" className="flex-1 overflow-auto data-[state=inactive]:hidden" forceMount>
          <div className="p-6" data-tab="tools">
            <ToolsConfig active={activeSettingsTab === 'tools'} />
          </div>
        </TabsContent>

        {/* Cookie 管理 */}
        <TabsContent value="cookies" className="flex-1 overflow-auto data-[state=inactive]:hidden" forceMount>
          <div className="p-6 max-w-6xl" data-tab="cookies">
            <CookieManager
              onCookieUpdate={fetchStorageInfo}
              targetPlatform={targetCookiePlatform}
              targetUrl={targetCookieUrl}
              onTargetPlatformHandled={onCookiePlatformHandled}
            />
          </div>
        </TabsContent>
      </Tabs>

      {/* Footer */}
      <div className="border-t px-6 py-3">
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            VidFlow v{appVersion} • Electron + Python FastAPI
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleShowAbout}>关于</Button>
          </div>
        </div>
      </div>
    </div>
  );
}
