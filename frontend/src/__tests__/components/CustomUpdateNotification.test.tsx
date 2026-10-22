import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { CustomUpdateNotification } from '../../components/CustomUpdateNotification';

vi.mock('sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

describe('update install result handling', () => {
  const callbacks = new Map<string, (...args: any[]) => void>();
  const installUpdate = vi.fn();
  const downloadUpdate = vi.fn();
  const release = { has_update: true, latest_version: '2.0.0', release_notes: '', file_size: 123, is_mandatory: false, download_url: 'https://updates.example/file.exe' };
  beforeEach(() => {
    callbacks.clear(); vi.clearAllMocks();
    window.electron = {
      on: vi.fn((channel, callback) => callbacks.set(channel, callback)), off: vi.fn(),
      installUpdate, downloadUpdate, cancelUpdateDownload: vi.fn().mockResolvedValue({ success: true }), platform: 'win32'
    } as unknown as NonNullable<Window['electron']>;
  });
  const ready = () => {
    render(<CustomUpdateNotification />);
    act(() => { callbacks.get('update-available')!(release); callbacks.get('update-downloaded')!({ type: 'full', version: '2.0.0' }); });
  };
  it('displays IPC success=false and preserves the retry action', async () => {
    installUpdate.mockResolvedValue({ success: false, error: 'Installer launch failed' });
    ready(); fireEvent.click(screen.getByRole('button', { name: '立即重启' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('重启更新失败', { description: 'Installer launch failed' }));
    expect(screen.getByRole('button', { name: '立即重启' })).toBeInTheDocument();
  });
  it('does not leave a stale install action after the release becomes unavailable', () => {
    ready(); act(() => callbacks.get('update-not-available')!({ status: 'rollout_blocked', rollout_message: '发布已暂停' }));
    expect(screen.queryByRole('button', { name: '立即重启' })).not.toBeInTheDocument();
  });
  it('tells macOS users to complete the opened package manually', async () => {
    window.electron!.platform = 'darwin';
    installUpdate.mockResolvedValue({ success: true, manualInstall: true });
    ready(); fireEvent.click(screen.getByRole('button', { name: '打开安装包' }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('请完成手动安装', expect.anything()));
  });
  it('offers manual download and surfaces a failed download IPC response', async () => {
    downloadUpdate.mockResolvedValue({ success: false, error: 'Paused release' });
    render(<CustomUpdateNotification />);
    act(() => callbacks.get('update-available')!(release));
    fireEvent.click(screen.getByRole('button', { name: '下载更新' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('下载更新失败', { description: 'Paused release' }));
    expect(screen.getByRole('button', { name: '下载更新' })).toBeInTheDocument();
  });
  it('treats a cancelled download response as cancellation rather than failure', async () => {
    downloadUpdate.mockResolvedValue({ success: false, cancelled: true });
    render(<CustomUpdateNotification />); act(() => callbacks.get('update-available')!(release));
    fireEvent.click(screen.getByRole('button', { name: '下载更新' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '下载更新' })).toBeInTheDocument());
    expect(toast.error).not.toHaveBeenCalled();
  });
  it('offers cancellation while downloading and restores retry after the cancel event', async () => {
    render(<CustomUpdateNotification />);
    act(() => { callbacks.get('update-available')!(release); callbacks.get('download-start')!(); });
    fireEvent.click(screen.getByRole('button', { name: '取消下载' }));
    await waitFor(() => expect(window.electron?.cancelUpdateDownload).toHaveBeenCalled());
    act(() => callbacks.get('download-cancelled')!());
    expect(screen.getByRole('button', { name: '下载更新' })).toBeInTheDocument();
  });
});
