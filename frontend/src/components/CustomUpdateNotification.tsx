import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import DOMPurify from 'dompurify';

interface DeltaInfo {
  source_version: string;
  target_version: string;
  delta_size: number;
  delta_hash: string;
  delta_url: string;
  full_size: number;
  savings_percent: number;
}

interface UpdateInfo {
  has_update: boolean;
  latest_version: string;
  release_notes: string;
  file_size: number;
  is_mandatory: boolean;
  download_url: string;
  rollout_blocked?: boolean;
  rollout_message?: string;
  delta_available?: boolean;
  delta_info?: DeltaInfo;
  recommended_update_type?: 'delta' | 'full';
}

interface UpdateDownloadedInfo {
  version?: string;
  type?: 'delta' | 'full';
  requiresRestart?: boolean;
  manualInstall?: boolean;
}

export function CustomUpdateNotification() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [showCard, setShowCard] = useState(false);
  const [updateDownloaded, setUpdateDownloaded] = useState(false);
  const [applyingDelta, setApplyingDelta] = useState(false);
  const [downloadedUpdateType, setDownloadedUpdateType] = useState<'delta' | 'full'>('full');
  const [downloading, setDownloading] = useState(false);
  const [downloadPercent, setDownloadPercent] = useState(0);

  useEffect(() => {
    if (!window.electron) {
      return;
    }

    const handleUpdateChecking = () => {
      console.log('Checking for updates...');
    };

    const handleUpdateAvailable = (info: UpdateInfo) => {
      console.log('Update available:', info);
      setUpdateInfo(info);
      setShowCard(false);
      setUpdateDownloaded(false);
      setApplyingDelta(false);
      setDownloadedUpdateType('full');

      if (info.rollout_blocked) {
        toast.info('新版本发布中', {
          description: info.rollout_message || '您的设备暂未进入更新名单，请稍后再试'
        });
        return;
      }

      setShowCard(true);

      toast.info(`发现新版本 ${info.latest_version}`, { description: '下载并校验完成后可以安装更新' });
    };

    const handleUpdateNotAvailable = (info?: { status?: string; rollout_message?: string }) => {
      console.log('No update available');
      setUpdateInfo(null);
      setShowCard(false);
      setUpdateDownloaded(false);
      setDownloading(false);
      if (info?.status && info.status !== 'up_to_date') {
        toast.info('暂无可用更新', { description: info.rollout_message || '发布尚未对本设备开放，请稍后重试' });
      }
    };

    const handleUpdateDownloaded = (info?: UpdateDownloadedInfo) => {
      setDownloading(false);
      setApplyingDelta(false);
      setUpdateDownloaded(true);
      setShowCard(true);
      setDownloadedUpdateType(info?.type === 'delta' ? 'delta' : 'full');

      toast.success('更新已准备就绪', {
        description: info?.manualInstall
          ? '更新包已校验，请打开安装包完成安装'
          : info?.type === 'delta'
          ? '重启应用即可完成更新'
          : '重启应用后会静默完成安装',
        action: {
          label: info?.manualInstall ? '打开安装包' : '立即重启',
          onClick: handleInstall
        }
      });
    };

    const handleUpdateError = (error: string) => {
      setDownloading(false);
      setApplyingDelta(false);
      toast.error('更新失败', {
        description: error
      });
    };

    const handleDeltaFallback = (info: { reason: string }) => {
      toast.warning('增量更新失败，已自动切换为完整更新', {
        description: info.reason
      });
    };

    const handleDeltaApplyStart = () => {
      setApplyingDelta(true);
      toast.info('已下载更新，正在后台准备重启补丁...');
    };

    const handleDeltaApplyComplete = () => {
      setApplyingDelta(false);
    };

    const handleDownloadStart = () => { setDownloading(true); setDownloadPercent(0); };
    const handleDownloadProgress = (progress: { percent: number }) => setDownloadPercent(progress.percent);
    const handleDownloadCancelled = () => { setDownloading(false); toast.info('下载已取消，已保存进度'); };

    window.electron.on('update-checking', handleUpdateChecking);
    window.electron.on('update-available', handleUpdateAvailable);
    window.electron.on('update-not-available', handleUpdateNotAvailable);
    window.electron.on('update-downloaded', handleUpdateDownloaded);
    window.electron.on('update-error', handleUpdateError);
    window.electron.on('delta-fallback', handleDeltaFallback);
    window.electron.on('delta-apply-start', handleDeltaApplyStart);
    window.electron.on('delta-apply-complete', handleDeltaApplyComplete);
    window.electron.on('download-start', handleDownloadStart);
    window.electron.on('download-progress', handleDownloadProgress);
    window.electron.on('download-cancelled', handleDownloadCancelled);

    return () => {
      if (!window.electron) {
        return;
      }

      window.electron.off('update-checking', handleUpdateChecking);
      window.electron.off('update-available', handleUpdateAvailable);
      window.electron.off('update-not-available', handleUpdateNotAvailable);
      window.electron.off('update-downloaded', handleUpdateDownloaded);
      window.electron.off('update-error', handleUpdateError);
      window.electron.off('delta-fallback', handleDeltaFallback);
      window.electron.off('delta-apply-start', handleDeltaApplyStart);
      window.electron.off('delta-apply-complete', handleDeltaApplyComplete);
      window.electron.off('download-start', handleDownloadStart);
      window.electron.off('download-progress', handleDownloadProgress);
      window.electron.off('download-cancelled', handleDownloadCancelled);
    };
  }, []);

  const handleInstall = async () => {
    try {
      setShowCard(false);
      const result = await window.electron?.installUpdate();
      if (!result?.success) throw new Error(result?.error || '安装器没有返回成功结果');
      if (result.manualInstall) toast.info('请完成手动安装', { description: '更新包已打开；安装完成后再启动应用' });
    } catch (error) {
      setShowCard(true);
      toast.error('重启更新失败', { description: error instanceof Error ? error.message : String(error) });
    }
  };

  const handleClose = () => {
    if (!updateInfo?.is_mandatory) {
      setShowCard(false);
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const result = await window.electron?.downloadUpdate();
      if (result?.cancelled) { setDownloading(false); return; }
      if (!result?.success) throw new Error(result?.error || '下载没有返回成功结果');
    } catch (error) {
      setDownloading(false);
      toast.error('下载更新失败', { description: error instanceof Error ? error.message : String(error) });
    }
  };

  const handleCancelDownload = async () => {
    try {
      const result = await window.electron?.cancelUpdateDownload();
      if (!result?.success) throw new Error(result?.error || '取消下载失败');
    } catch (error) { toast.error('取消下载失败', { description: error instanceof Error ? error.message : String(error) }); }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`;
  };

  const getPackageSummary = () => {
    if (!updateInfo) {
      return '';
    }

    if (downloadedUpdateType === 'delta' && updateInfo.delta_info) {
      return `增量更新包 ${formatBytes(updateInfo.delta_info.delta_size)}，重启即可生效`;
    }

    return `完整更新包 ${formatBytes(updateInfo.file_size)}${window.electron?.platform === 'win32' ? '，安装时将重启应用' : '，需要手动完成安装'}`;
  };

  if (!showCard || !updateInfo || updateInfo.rollout_blocked) {
    return null;
  }

  return (
    <div className="fixed bottom-6 right-6 z-50 w-[calc(100%-2rem)] max-w-md rounded-2xl border border-gray-200 bg-white/95 p-5 shadow-2xl backdrop-blur dark:border-gray-700 dark:bg-gray-900/95">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-blue-600 dark:text-blue-400">
            {downloading ? `正在下载 ${downloadPercent}%` : updateDownloaded ? '更新已就绪' : '发现新版本'}
          </p>
          <h2 className="mt-1 text-lg font-semibold text-gray-900 dark:text-white">
            VidFlow {updateInfo.latest_version}
          </h2>
          <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
            {getPackageSummary()}
          </p>
          {applyingDelta && (
            <p className="mt-2 text-xs text-amber-600 dark:text-amber-300">
              正在准备增量补丁，请稍等片刻再重启。
            </p>
          )}
        </div>

        {!updateInfo.is_mandatory && (
          <button
            onClick={handleClose}
            className="rounded-full p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            aria-label="关闭更新提示"
          >
            <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
              <path
                fillRule="evenodd"
                d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        )}
      </div>

      {updateInfo.release_notes && (
        <div className="mt-4 rounded-xl bg-gray-50 p-3 dark:bg-gray-800">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
            更新内容
          </p>
          <div
            className="prose prose-sm max-h-40 overflow-y-auto text-gray-700 dark:prose-invert dark:text-gray-200"
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(updateInfo.release_notes) }}
          />
        </div>
      )}

      <div className="mt-4 flex gap-3">
        {!updateInfo.is_mandatory && (
          <button
            onClick={handleClose}
            className="flex-1 rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
          >
            稍后再说
          </button>
        )}
        {downloading ? <button onClick={handleCancelDownload} className="flex-1 rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium">取消下载</button> : <button
          onClick={updateDownloaded ? handleInstall : handleDownload}
          disabled={applyingDelta}
          className="flex-1 rounded-xl bg-green-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-70"
        >
          {applyingDelta ? '准备中...' : updateDownloaded ? (window.electron?.platform === 'win32' ? '立即重启' : '打开安装包') : '下载更新'}
        </button>}
      </div>
    </div>
  );
}
