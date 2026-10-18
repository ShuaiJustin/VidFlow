/**
 * Cookie 管理组件
 * 用于配置各平台的Cookie以支持反爬虫机制
 */
import React, { useState, useEffect } from 'react';
import { invoke } from './TauriIntegration';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';
import { Textarea } from './ui/textarea';
import { toast } from 'sonner';
import { Loader2, Cookie as CookieIcon, CheckCircle2, XCircle, Folder, Bot, FileText, Trash2 } from 'lucide-react';

interface CookiePlatform {
  platform: string;
  name: string;
  description: string;
  configured: boolean;
  category: string;
  file_size?: number;
  last_modified?: string;
  guide_url?: string;
}

interface CookieManagerProps {
  onCookieUpdate?: () => void;
  targetPlatform?: string | null;
  targetUrl?: string | null;
  onTargetPlatformHandled?: () => void;
}

interface CookieValidationResult {
  isValid: boolean;
  cookieLines: number;
  errorLine?: number;
  errorMessage?: string;
}

const validateNetscapeCookieContent = (content: string): CookieValidationResult => {
  const trimmed = content.trim();
  if (!trimmed) {
    return { isValid: true, cookieLines: 0 };
  }

  const lines = content.split(/\r?\n/);
  let cookieLines = 0;

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const line = rawLine.trim();

    if (!line) continue;

    const isHttpOnlyLine = line.startsWith('#HttpOnly_');
    const isCommentLine = line.startsWith('#') && !isHttpOnlyLine;
    if (isCommentLine) continue;

    cookieLines += 1;

    const tabParts = rawLine.split('\t');
    const parts = tabParts.length >= 7 ? tabParts : line.split(/\s+/);

    if (parts.length < 7) {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: '字段数不足（需要 7 列，用 TAB 分隔）'
      };
    }

    const domain = (parts[0] || '').trim();
    const includeSubdomains = (parts[1] || '').trim().toUpperCase();
    const path = (parts[2] || '').trim();
    const secure = (parts[3] || '').trim().toUpperCase();
    const expires = (parts[4] || '').trim();
    const name = (parts[5] || '').trim();

    if (!domain) {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'domain 为空'
      };
    }

    if (includeSubdomains !== 'TRUE' && includeSubdomains !== 'FALSE') {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'includeSubdomains 必须为 TRUE/FALSE'
      };
    }

    if (!path || !path.startsWith('/')) {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'path 必须以 / 开头'
      };
    }

    if (secure !== 'TRUE' && secure !== 'FALSE') {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'secure 必须为 TRUE/FALSE'
      };
    }

    if (!expires || !/^-?\d+$/.test(expires)) {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'expires 必须为数字时间戳'
      };
    }

    if (!name) {
      return {
        isValid: false,
        cookieLines,
        errorLine: i + 1,
        errorMessage: 'name 为空'
      };
    }
  }

  if (cookieLines === 0) {
    return {
      isValid: false,
      cookieLines: 0,
      errorMessage: '未检测到任何 Cookie 行'
    };
  }

  return { isValid: true, cookieLines };
};

export const CookieManager: React.FC<CookieManagerProps> = ({ onCookieUpdate, targetPlatform, targetUrl, onTargetPlatformHandled }) => {
  const [platforms, setPlatforms] = useState<CookiePlatform[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null);
  const [managedBrowserTargetUrl, setManagedBrowserTargetUrl] = useState<string | null>(targetUrl || null);
  const [cookieContent, setCookieContent] = useState('');
  const [cookieValidation, setCookieValidation] = useState<CookieValidationResult>({
    isValid: true,
    cookieLines: 0
  });
  const [saving, setSaving] = useState(false);

  // 自动获取Cookie相关状态
  const [autoGetMode, setAutoGetMode] = useState(false);
  const [browserRunning, setBrowserRunning] = useState(false);
  // 从浏览器读取Cookie相关状态
  const [selectedBrowser, setSelectedBrowser] = useState<string>('chrome');
  const browserDisplayName = selectedBrowser === 'chrome' ? 'Chrome' : selectedBrowser === 'edge' ? 'Edge' : 'Firefox';
  const [cookiesFolderPath, setCookiesFolderPath] = useState<string>('');
  // 分类展开/收缩状态
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({
    short_video: true,
    video_platform: false,
    social_media: false
  });

  const toggleCategory = (category: string) => {
    setExpandedCategories(prev => ({ ...prev, [category]: !prev[category] }));
  };

  const expandAllCategories = () => {
    setExpandedCategories({
      short_video: true,
      video_platform: true,
      social_media: true
    });
  };

  const collapseAllCategories = () => {
    setExpandedCategories({
      short_video: false,
      video_platform: false,
      social_media: false
    });
  };

  // 加载所有平台的Cookie状态
  const loadCookiesStatus = async () => {
    try {
      setLoading(true);
      const response = await invoke('get_cookies_status', {});
      if (response?.status === 'success' && response?.platforms) {
        setPlatforms(response.platforms);
      }
    } catch (error: any) {
      console.error('Failed to load cookies status:', error);
      showMessage('error', '加载Cookie状态失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // 加载指定平台的Cookie内容
  const loadCookieContent = async (platform: string) => {
    try {
      setLoading(true);
      const response = await invoke('get_cookie_content', { platform });
      if (response?.status === 'success') {
        setCookieContent(response.content || '');
        setSelectedPlatform(platform);
        // 滚动到编辑器区域
        setTimeout(() => {
          const editorElement = document.getElementById('cookie-editor');
          if (editorElement) {
            editorElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        }, 100);
      }
    } catch (error: any) {
      console.error('Failed to load cookie content:', error);
      showMessage('error', '加载Cookie内容失败: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // 保存Cookie内容
  const saveCookieContent = async () => {
    if (!selectedPlatform) return;

    try {
      setSaving(true);
      const response = await invoke('save_cookie_content', {
        platform: selectedPlatform,
        content: cookieContent
      });

      if (response?.status === 'success') {
        showMessage('success', response.message || 'Cookie已保存');
        // 重新加载状态
        await loadCookiesStatus();
        if (onCookieUpdate) {
          onCookieUpdate();
        }
      }
    } catch (error: any) {
      console.error('Failed to save cookie:', error);
      showMessage('error', '保存Cookie失败: ' + error.message);
    } finally {
      setSaving(false);
    }
  };

  // 删除Cookie
  const deleteCookie = async (platform: string) => {
    if (!confirm(`确定要删除 ${platforms.find(p => p.platform === platform)?.name} 的Cookie吗？`)) {
      return;
    }

    try {
      const response = await invoke('delete_cookie', { platform });

      if (response?.status === 'success') {
        showMessage('success', response.message || 'Cookie已删除');
        // 重新加载状态
        await loadCookiesStatus();
        // 如果删除的是当前选中的平台，清空内容
        if (selectedPlatform === platform) {
          setCookieContent('');
        }
        if (onCookieUpdate) {
          onCookieUpdate();
        }
      }
    } catch (error: any) {
      console.error('Failed to delete cookie:', error);
      showMessage('error', '删除Cookie失败: ' + error.message);
    }
  };

  // 打开Cookie文件夹
  const openCookiesFolder = async () => {
    try {
      const response = await invoke('open_cookies_folder', {});
      if (response?.status === 'success') {
        if (response?.path) {
          setCookiesFolderPath(response.path);
        }
        showMessage('info', response.message || 'Cookie文件夹已打开');
      }
    } catch (error: any) {
      console.error('Failed to open cookies folder:', error);
      showMessage('error', '打开Cookie文件夹失败: ' + error.message);
    }
  };

  // 显示消息 - 使用toast替代
  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    if (type === 'success') {
      toast.success(text);
    } else if (type === 'error') {
      toast.error(text);
    } else {
      toast.info(text);
    }
  };

  const restoreManagedBrowserSession = async () => {
    try {
      const response = await invoke('check_selenium_status', {});
      const session = response?.data;
      if (!session?.is_running || !session?.platform) {
        return;
      }

      setSelectedPlatform(session.platform);
      if (['chrome', 'edge', 'firefox'].includes(session.browser)) {
        setSelectedBrowser(session.browser);
      }
      setAutoGetMode(true);
      setBrowserRunning(true);
      showMessage('info', '已恢复进行中的受控浏览器登录，请登录完成后点击“完成登录”。');
    } catch (error) {
      console.warn('Failed to restore managed browser session:', error);
    }
  };

  // 启动自动获取Cookie流程
  const startAutoGetCookie = async (platform: string) => {
    try {
      setLoading(true);
      setAutoGetMode(true);
      setSelectedPlatform(platform);

      // 显示加载提示
      const platformName = platforms.find(p => p.platform === platform)?.name || platform;
      const browserForLaunch = platform === 'xinpianchang' ? 'chrome' : selectedBrowser;
      const browserName = browserForLaunch === 'chrome' ? 'Chrome' : browserForLaunch === 'edge' ? 'Edge' : 'Firefox';
      if (platform === 'xinpianchang') {
        setSelectedBrowser('chrome');
        showMessage('info', '正在启动新片场专用 Chrome，请稍候...\n该窗口使用持久登录状态且不使用 WebDriver。');
      } else {
        showMessage('info', `正在启动 ${browserName} 浏览器，请稍候...\n（首次使用可能需要下载 WebDriver，大约需要 10-30 秒）`);
      }

      // 启动浏览器 - TauriIntegration 会将业务错误转换为异常
      const startResult: any = await invoke('start_cookie_browser', {
        platform,
        browser: browserForLaunch,
        target_url: managedBrowserTargetUrl || undefined,
      });

      // ✅ 只有成功时才会到这里
      setManagedBrowserTargetUrl(null);
      setBrowserRunning(true);
      const loginUrl = startResult?.url ? String(startResult.url) : '';
      const currentUrl = startResult?.current_url ? String(startResult.current_url) : '';
      const navigationOk = startResult?.navigation_ok;

      // 滚动到操作面板
      setTimeout(() => {
        const panelElement = document.getElementById('auto-get-cookie-panel');
        if (panelElement) {
          panelElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 100);

      if (navigationOk === false) {
        showMessage(
          'info',
          `${browserName} 浏览器已启动，但未自动跳转到登录页。\n请在浏览器地址栏手动打开：${loginUrl || '（未知）'}\n当前页面：${currentUrl || '（未知）'}\n登录完成后点击"完成登录"按钮`
        );
      } else {
        showMessage(
          'success',
          `${browserName} 浏览器已启动！\n登录地址：${loginUrl || '（未知）'}\n请在浏览器窗口中登录 ${platformName}，登录完成后点击"完成登录"按钮`
        );
      }

    } catch (error: any) {
      console.error('Failed to start auto get cookie:', error);

      // ✅ 统一的错误处理 - 所有错误都会进入这里
      const errorMsg = error.message || '启动浏览器失败';
      const errorMsgLower = String(errorMsg).toLowerCase();

      // 根据错误内容提供友好提示
      if (errorMsgLower.includes('pip install')) {
        showMessage('error', errorMsg);
      } else if (errorMsg.includes('未安装') || errorMsg.includes('not found')) {
        showMessage('error', errorMsg);
      } else if (errorMsg.includes('已在运行')) {
        showMessage('error', '浏览器已在运行中，请先完成当前操作或关闭浏览器');
      } else {
        showMessage('error', `启动浏览器失败：${errorMsg}`);
      }

      // ✅ 清理状态
      setAutoGetMode(false);
      setBrowserRunning(false);
    } finally {
      setLoading(false);
    }
  };

  // 完成登录，提取Cookie
  const finishAutoGetCookie = async () => {
    try {
      setLoading(true);

      // 提取Cookie - TauriIntegration 会将业务错误转换为异常
      const response = await invoke('extract_cookies', {});

      // ✅ 只有成功时才会到这里
      const content = response.content || '';
      const count = response.count || 0;

      // 设置到文本框
      setCookieContent(content);

      showMessage('success', `成功提取 ${count} 个Cookie！Cookie已自动保存，您可以直接关闭浏览器。`);

      // 关闭浏览器
      await invoke('close_cookie_browser', {});
      setBrowserRunning(false);

      // 刷新状态
      await loadCookiesStatus();

      // 退出自动模式
      setAutoGetMode(false);

      if (onCookieUpdate) {
        onCookieUpdate();
      }

    } catch (error: any) {
      console.error('Failed to extract cookies:', error);

      // ✅ 统一的错误处理
      const errorMsg = error.message || '提取Cookie失败';
      showMessage('error', errorMsg);

      // 如果是浏览器相关错误，清理状态
      if (errorMsg.includes('浏览器') || errorMsg.includes('browser') ||
          errorMsg.includes('未运行') || errorMsg.includes('已关闭')) {
        setBrowserRunning(false);
        setAutoGetMode(false);
      }
    } finally {
      setLoading(false);
    }
  };

  // 取消自动获取
  const cancelAutoGetCookie = async () => {
    try {
      if (browserRunning) {
        await invoke('close_cookie_browser', {});
      }
      setBrowserRunning(false);
      setAutoGetMode(false);
      setSelectedPlatform(null);
      showMessage('info', '已取消自动获取Cookie');
    } catch (error: any) {
      console.error('Failed to cancel auto get:', error);
      showMessage('error', '关闭浏览器失败');
    }
  };

  // 从浏览器直接读取Cookie（推荐方式 - 最可靠）
  const extractFromBrowser = async (platform: string) => {
    try {
      setLoading(true);
      const platformName = platforms.find(p => p.platform === platform)?.name || platform;

      showMessage('info', `正在从 ${browserDisplayName} 浏览器读取 ${platformName} Cookie...\n\n💡 如果浏览器正在运行，请先关闭浏览器。`);

      // 从浏览器提取Cookie
      const response = await invoke('extract_cookies_from_browser', {
        platform,
        browser: selectedBrowser
      });

      // ✅ 检查返回值是否有效
      if (!response) {
        throw new Error('服务器返回空响应');
      }

      // 成功提取
      const cookieCount = response.count || 0;
      showMessage('success', `成功从 ${browserDisplayName} 浏览器提取 ${cookieCount} 个 Cookie！Cookie 已自动保存。`);

      // 重新加载Cookie状态
      await loadCookiesStatus();
      if (onCookieUpdate) {
        onCookieUpdate();
      }

    } catch (error: any) {
      console.error('Failed to extract from browser:', error);

      const errorMsg = error.message || '提取Cookie失败';
      const errorMsgLower = String(errorMsg).toLowerCase();
      const shouldStartManagedBrowser =
        (errorMsgLower.includes('cookie') &&
          (errorMsgLower.includes('not found') || errorMsgLower.includes('no matching') || errorMsgLower.includes('decrypt') || errorMsgLower.includes('encryption'))) ||
        (errorMsg.includes('未从') && errorMsg.includes('Cookie')) ||
        errorMsg.includes('加密');

      if (shouldStartManagedBrowser) {
        showMessage('info', `${browserDisplayName} 中没有可用Cookie，正在启动受控浏览器登录。`);
        await startAutoGetCookie(platform);
        return;
      }

      // 提供友好的错误提示
      if (errorMsg.includes('未找到') && errorMsg.includes('Cookie')) {
        showMessage('error', `未找到 Cookie。请先在 ${browserDisplayName} 浏览器中登录该平台，然后重试。`);
      } else if (errorMsg.includes('加密') || errorMsg.includes('password')) {
        showMessage('error', `无法读取 ${browserDisplayName} 浏览器的加密Cookie。\n\n解决方案：\n1. 完全关闭 ${browserDisplayName} 浏览器后重试\n2. 或使用「受控浏览器登录」功能`);
      } else if (errorMsg.includes('未找到') && errorMsg.includes('浏览器')) {
        showMessage('error', `未找到 ${browserDisplayName} 浏览器。请确保浏览器已安装，或选择其他浏览器。`);
      } else if (errorMsg.includes('permission') || errorMsg.includes('access')) {
        showMessage('error', `无法访问浏览器数据。请完全关闭 ${browserDisplayName} 浏览器后重试。`);
      } else {
        showMessage('error', `提取Cookie失败：${errorMsg}`);
      }
    } finally {
      setLoading(false);
    }
  };


  useEffect(() => {
    setCookieValidation(validateNetscapeCookieContent(cookieContent));
  }, [cookieContent]);


  // 初始加载
  useEffect(() => {
    loadCookiesStatus();
    restoreManagedBrowserSession();
  }, []);

  useEffect(() => {
    if (targetUrl) {
      setManagedBrowserTargetUrl(targetUrl);
    }
  }, [targetUrl]);

  // 当有目标平台时，展开对应分类并滚动到目标平台卡片
  useEffect(() => {
    if (targetPlatform && platforms.length > 0) {
      // 找到目标平台的分类
      const targetPlatformData = platforms.find(p => p.platform === targetPlatform);
      if (targetPlatformData) {
        // 展开对应分类
        setExpandedCategories(prev => ({
          ...prev,
          [targetPlatformData.category]: true
        }));

        // 延迟滚动，等待分类展开动画完成
        setTimeout(() => {
          const element = document.getElementById(`cookie-platform-${targetPlatform}`);
          if (element) {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // 添加高亮效果
            element.classList.add('ring-2', 'ring-primary', 'ring-offset-2');
            setTimeout(() => {
              element.classList.remove('ring-2', 'ring-primary', 'ring-offset-2');
            }, 2000);
          }
          // 通知父组件已处理完目标平台
          onTargetPlatformHandled?.();
        }, 100);
      }
    }
  }, [targetPlatform, platforms, onTargetPlatformHandled]);

  // 取消编辑
  const handleCancel = () => {
    setSelectedPlatform(null);
    setCookieContent('');
  };

  return (
    <div className="space-y-6">
      {/* 说明卡片 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <CookieIcon className="size-5 text-primary" />
            <CardTitle>Cookie 配置说明</CardTitle>
          </div>
          <CardDescription>
            某些平台（如小红书、抖音、TikTok等）设有反爬虫机制，需要配置Cookie才能正常下载。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 手动配置方式 */}
          <div>
            <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
              <FileText className="size-4" />
              手动配置方式
            </h4>
            <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside ml-6">
              <li>使用浏览器插件（如"Get cookies.txt"）导出Cookie</li>
              <li>点击"手动配置"按钮，粘贴Cookie内容</li>
            </ul>
          </div>

          {/* 自动获取方式 */}
          <Alert>
            <CookieIcon className="h-4 w-4" />
            <AlertDescription>
              <div className="space-y-2">
                <p className="font-medium">从本机浏览器读取（推荐）</p>
                <p className="text-sm">从已登录的 Chrome/Edge/Firefox 读取并保存 Cookie（建议先完全关闭浏览器）</p>
              </div>
            </AlertDescription>
          </Alert>

          <Alert>
            <Bot className="h-4 w-4" />
            <AlertDescription>
              <div className="space-y-2">
                <p className="font-medium">🤖 受控浏览器登录（备用）</p>
                <p className="text-sm">应用会启动独立浏览器窗口，登录后点击“完成登录”自动提取并保存 Cookie</p>
              </div>
            </AlertDescription>
          </Alert>

          {/* 浏览器选择器 */}
          <div className="space-y-2">
            <label htmlFor="cookie-browser" className="text-sm font-medium">选择浏览器</label>
            <select
              id="cookie-browser"
              value={selectedBrowser}
              onChange={(e) => setSelectedBrowser(e.target.value)}
              className="w-full px-3 py-2 border rounded-md bg-background"
            >
              <option value="chrome">Google Chrome</option>
              <option value="edge">Microsoft Edge</option>
              <option value="firefox">Mozilla Firefox</option>
            </select>
            <p className="text-xs text-muted-foreground">
              💡 用于：从浏览器读取 Cookie / 受控浏览器登录（自动获取）
            </p>
          </div>

          <div className="space-y-2">
            <Button variant="default" onClick={openCookiesFolder} className="w-full">
              <Folder className="size-4 mr-2" />
              打开 Cookie 文件夹
            </Button>
            <p className="text-xs text-muted-foreground break-all">
              {cookiesFolderPath ? `Cookie 文件夹：${cookiesFolderPath}` : 'Cookie 文件默认存放在 data/cookies（点击上方按钮可打开并显示路径）'}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* 平台列表 */}
      {loading && !selectedPlatform ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <span className="ml-2 text-sm text-muted-foreground">加载中...</span>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="flex items-center justify-end gap-2">
            <Button variant="outline" size="sm" onClick={expandAllCategories}>
              全部展开
            </Button>
            <Button variant="outline" size="sm" onClick={collapseAllCategories}>
              全部收起
            </Button>
          </div>
          {/* 短视频平台 */}
          {platforms.filter(p => p.category === 'short_video').length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-3 cursor-pointer" onClick={() => toggleCategory('short_video')}>
                <h3 className="text-lg font-semibold">短视频平台</h3>
                <Button variant="ghost" size="sm">
                  {expandedCategories.short_video ? '收起' : '展开'}
                </Button>
              </div>
              {expandedCategories.short_video && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {platforms.filter(p => p.category === 'short_video').map((platform) => (
                  <Card key={platform.platform} id={`cookie-platform-${platform.platform}`} className="transition-all duration-300">
                    <CardHeader>
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <CardTitle className="text-base">{platform.name}</CardTitle>
                      {platform.configured ? (
                        <Badge variant="default" className="bg-green-600">
                          <CheckCircle2 className="size-3 mr-1" />
                          已配置
                        </Badge>
                      ) : (
                        <Badge variant="secondary">
                          <XCircle className="size-3 mr-1" />
                          未配置
                        </Badge>
                      )}
                    </div>
                    <CardDescription className="text-xs">
                      {platform.description}
                    </CardDescription>
                    {platform.configured && platform.last_modified && (
                      <p className="text-xs text-muted-foreground mt-1">
                        最后更新: {platform.last_modified}
                      </p>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex gap-2">
                  <Button
                    variant="default"
                    size="sm"
                    className="flex-1"
                    onClick={() => loadCookieContent(platform.platform)}
                  >
                    <FileText className="size-4 mr-2" />
                    {platform.configured ? '编辑' : '手动配置'}
                  </Button>
                  {platform.configured && (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => deleteCookie(platform.platform)}
                    >
                      <Trash2 className="size-4 mr-2" />
                      删除
                    </Button>
                  )}
                </div>
                {/* 从浏览器读取Cookie按钮（推荐） */}
                <div className="space-y-1">
                  <Button
                    variant="default"
                    size="sm"
                    className="w-full bg-blue-600 hover:bg-blue-700"
                    onClick={() => extractFromBrowser(platform.platform)}
                    disabled={loading || browserRunning}
                  >
                    <CookieIcon className="size-4 mr-2" />
                    从{browserDisplayName}读取 Cookie（推荐）
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    已在 {browserDisplayName} 登录后使用；建议先完全关闭浏览器再点击
                  </p>
                </div>
                {/* 受控浏览器登录方式 */}
                <div className="space-y-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={() => startAutoGetCookie(platform.platform)}
                    disabled={loading || browserRunning}
                  >
                    <Bot className="size-4 mr-2" />
                    受控浏览器登录（自动获取）
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    如果上方读取失败，可用此方式登录后自动提取并保存
                  </p>
                </div>
              </CardContent>
            </Card>
          ))}
              </div>
              )}
            </div>
          )}

          {/* 视频平台 */}
          {platforms.filter(p => p.category === 'video_platform').length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-3 cursor-pointer" onClick={() => toggleCategory('video_platform')}>
                <h3 className="text-lg font-semibold">视频平台</h3>
                <Button variant="ghost" size="sm">
                  {expandedCategories.video_platform ? '收起' : '展开'}
                </Button>
              </div>
              {expandedCategories.video_platform && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {platforms.filter(p => p.category === 'video_platform').map((platform) => (
                  <Card key={platform.platform} id={`cookie-platform-${platform.platform}`} className="transition-all duration-300">
                    <CardHeader>
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <CardTitle className="text-base">{platform.name}</CardTitle>
                            {platform.configured ? (
                              <Badge variant="default" className="bg-green-600">
                                <CheckCircle2 className="size-3 mr-1" />
                                已配置
                              </Badge>
                            ) : (
                              <Badge variant="secondary">
                                <XCircle className="size-3 mr-1" />
                                未配置
                              </Badge>
                            )}
                          </div>
                          <CardDescription className="text-xs">{platform.description}</CardDescription>
                          {platform.configured && platform.last_modified && (
                            <p className="text-xs text-muted-foreground mt-1">最后更新: {platform.last_modified}</p>
                          )}
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="flex gap-2">
                        <Button variant="default" size="sm" className="flex-1" onClick={() => loadCookieContent(platform.platform)}>
                          <FileText className="size-4 mr-2" />
                          {platform.configured ? '编辑' : '手动配置'}
                        </Button>
                        {platform.configured && (
                          <Button variant="destructive" size="sm" onClick={() => deleteCookie(platform.platform)}>
                            <Trash2 className="size-4 mr-2" />
                            删除
                          </Button>
                        )}
                      </div>
                      <div className="space-y-1">
                        <Button variant="default" size="sm" className="w-full bg-blue-600 hover:bg-blue-700" onClick={() => extractFromBrowser(platform.platform)} disabled={loading || browserRunning}>
                          <CookieIcon className="size-4 mr-2" />
                          从{browserDisplayName}读取 Cookie（推荐）
                        </Button>
                        <p className="text-xs text-muted-foreground">
                          已在 {browserDisplayName} 登录后使用；建议先完全关闭浏览器再点击
                        </p>
                      </div>
                      <div className="space-y-1">
                        <Button variant="outline" size="sm" className="w-full" onClick={() => startAutoGetCookie(platform.platform)} disabled={loading || browserRunning}>
                          <Bot className="size-4 mr-2" />
                          受控浏览器登录（自动获取）
                        </Button>
                        <p className="text-xs text-muted-foreground">
                          如果上方读取失败，可用此方式登录后自动提取并保存
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
              )}
            </div>
          )}

          {/* 社交媒体 */}
          {platforms.filter(p => p.category === 'social_media').length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-3 cursor-pointer" onClick={() => toggleCategory('social_media')}>
                <h3 className="text-lg font-semibold">社交媒体</h3>
                <Button variant="ghost" size="sm">
                  {expandedCategories.social_media ? '收起' : '展开'}
                </Button>
              </div>
              {expandedCategories.social_media && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {platforms.filter(p => p.category === 'social_media').map((platform) => (
                  <Card key={platform.platform} id={`cookie-platform-${platform.platform}`} className="transition-all duration-300">
                    <CardHeader>
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <CardTitle className="text-base">{platform.name}</CardTitle>
                            {platform.configured ? (
                              <Badge variant="default" className="bg-green-600">
                                <CheckCircle2 className="size-3 mr-1" />
                                已配置
                              </Badge>
                            ) : (
                              <Badge variant="secondary">
                                <XCircle className="size-3 mr-1" />
                                未配置
                              </Badge>
                            )}
                          </div>
                          <CardDescription className="text-xs">{platform.description}</CardDescription>
                          {platform.configured && platform.last_modified && (
                            <p className="text-xs text-muted-foreground mt-1">最后更新: {platform.last_modified}</p>
                          )}
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="flex gap-2">
                        <Button variant="default" size="sm" className="flex-1" onClick={() => loadCookieContent(platform.platform)}>
                          <FileText className="size-4 mr-2" />
                          {platform.configured ? '编辑' : '手动配置'}
                        </Button>
                        {platform.configured && (
                          <Button variant="destructive" size="sm" onClick={() => deleteCookie(platform.platform)}>
                            <Trash2 className="size-4 mr-2" />
                            删除
                          </Button>
                        )}
                      </div>
                      <div className="space-y-1">
                        <Button variant="default" size="sm" className="w-full bg-blue-600 hover:bg-blue-700" onClick={() => extractFromBrowser(platform.platform)} disabled={loading || browserRunning}>
                          <CookieIcon className="size-4 mr-2" />
                          从{browserDisplayName}读取 Cookie（推荐）
                        </Button>
                        <p className="text-xs text-muted-foreground">
                          已在 {browserDisplayName} 登录后使用；建议先完全关闭浏览器再点击
                        </p>
                      </div>
                      <div className="space-y-1">
                        <Button variant="outline" size="sm" className="w-full" onClick={() => startAutoGetCookie(platform.platform)} disabled={loading || browserRunning}>
                          <Bot className="size-4 mr-2" />
                          受控浏览器登录（自动获取）
                        </Button>
                        <p className="text-xs text-muted-foreground">
                          如果上方读取失败，可用此方式登录后自动提取并保存
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 自动获取Cookie流程提示 */}
      {autoGetMode && browserRunning && (
        <Alert id="auto-get-cookie-panel" className="border-green-600 bg-green-50 dark:bg-green-900/20">
          <CheckCircle2 className="h-4 w-4 text-green-600 animate-pulse" />
          <AlertDescription>
            <div className="space-y-4">
              <div>
                <h4 className="font-semibold text-green-900 dark:text-green-100 mb-2">
                  🤖 自动获取Cookie流程进行中
                </h4>
                <p className="text-sm font-medium mb-2">浏览器已启动！请按以下步骤操作：</p>
                <ol className="text-sm space-y-1 list-decimal list-inside ml-4">
                  <li>在弹出的浏览器窗口中<strong>登录 {platforms.find(p => p.platform === selectedPlatform)?.name}</strong></li>
                  <li>确保登录成功（能看到个人主页或首页）</li>
                  <li>回到此界面，点击下方<strong>"完成登录"</strong>按钮</li>
                  <li>应用将自动提取并保存Cookie</li>
                </ol>
              </div>

              <Alert className="bg-yellow-50 border-yellow-200">
                <AlertDescription className="text-xs text-yellow-900">
                  💡 <strong>提示：</strong>不要关闭浏览器窗口，VidFlow会在完成后自动关闭它。
                </AlertDescription>
              </Alert>

              <div className="flex gap-3">
                <Button
                  onClick={finishAutoGetCookie}
                  disabled={loading}
                  className="flex-1 bg-green-600 hover:bg-green-700"
                >
                  {loading ? (
                    <>
                      <Loader2 className="size-4 mr-2 animate-spin" />
                      提取中...
                    </>
                  ) : (
                    <>
                      <CheckCircle2 className="size-4 mr-2" />
                      完成登录
                    </>
                  )}
                </Button>
                <Button
                  variant="outline"
                  onClick={cancelAutoGetCookie}
                  disabled={loading}
                >
                  取消
                </Button>
              </div>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Cookie 编辑器 */}
      {selectedPlatform && !autoGetMode && (
        <Card id="cookie-editor">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>
                编辑 {platforms.find(p => p.platform === selectedPlatform)?.name} Cookie
              </CardTitle>
              <Button variant="ghost" size="icon" onClick={handleCancel}>
                <XCircle className="h-4 w-4" />
              </Button>
            </div>
            <CardDescription>
              Cookie 内容（Netscape格式）
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Textarea
              value={cookieContent}
              onChange={(e) => setCookieContent(e.target.value)}
              placeholder="# Netscape HTTP Cookie File&#10;.example.com\tTRUE\t/\tFALSE\t0\tcookie_name\tcookie_value"
              className="font-mono text-sm min-h-[300px]"
            />
            {cookieContent.trim() && (
              <p className={`text-xs ${cookieValidation.isValid ? 'text-green-600' : 'text-red-600'}`}>
                {cookieValidation.isValid ? (
                  <>格式检查通过（检测到 {cookieValidation.cookieLines} 行 Cookie）</>
                ) : (
                  <>
                    格式错误
                    {cookieValidation.errorLine ? `（第 ${cookieValidation.errorLine} 行）` : ''}
                    ：{cookieValidation.errorMessage || '请检查 Cookie 内容'}
                  </>
                )}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              💡 提示：使用浏览器插件（如Chrome的"Get cookies.txt"）可以快速导出Cookie。每行一个Cookie，格式为Netscape格式。
            </p>
            <div className="flex gap-3">
              <Button
                onClick={saveCookieContent}
                disabled={saving || !cookieContent.trim() || !cookieValidation.isValid}
                className="flex-1"
              >
                {saving ? (
                  <>
                    <Loader2 className="size-4 mr-2 animate-spin" />
                    保存中...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="size-4 mr-2" />
                    保存Cookie
                  </>
                )}
              </Button>
              <Button variant="outline" onClick={handleCancel}>
                取消
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default CookieManager;
