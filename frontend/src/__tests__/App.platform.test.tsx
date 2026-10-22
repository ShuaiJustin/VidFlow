import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

vi.mock('../components/TauriIntegration', () => ({
  TauriProvider: ({ children }: { children: ReactNode }) => children,
  invoke: vi.fn(async (command: string) => command === 'check_proxy' ? { available: false } : []),
}));
vi.mock('../contexts/BackendContext', () => ({
  BackendProvider: ({ children }: { children: ReactNode }) => children,
  useBackend: () => ({ state: { status: 'ready' }, retry: vi.fn() }),
}));
vi.mock('../contexts/SettingsContext', () => ({ SettingsProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('../contexts/InstallProgressContext', () => ({ InstallProgressProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('../contexts/TaskProgressContext', () => ({ TaskProgressProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('../components/ContextMenu', () => ({ ContextMenuProvider: ({ children }: { children: ReactNode }) => children }));
vi.mock('../components/DownloadManager', () => ({ DownloadManager: () => <div>下载内容</div> }));
vi.mock('../components/TaskManager', () => ({ TaskManager: () => null }));
vi.mock('../components/SettingsPanel', () => ({ SettingsPanel: () => null }));
vi.mock('../components/SubtitleProcessor', () => ({ SubtitleProcessor: () => null }));
vi.mock('../components/BurnSubtitle', () => ({ default: () => null }));
vi.mock('../components/LogViewer', () => ({ LogViewer: () => null }));
vi.mock('../components/AboutDialog', () => ({ default: () => null }));
vi.mock('../components/CustomUpdateNotification', () => ({ CustomUpdateNotification: () => null }));
vi.mock('../components/ui/sonner', () => ({ Toaster: () => null }));
vi.mock('../components/channels', () => ({ ChannelsPanel: vi.fn(() => <div>视频号内容</div>) }));

import App from '../App';
import { ChannelsPanel } from '../components/channels';

const originalElectron = Object.getOwnPropertyDescriptor(window, 'electron');
afterEach(() => {
  if (originalElectron) Object.defineProperty(window, 'electron', originalElectron);
  else Reflect.deleteProperty(window, 'electron');
});

describe('platform navigation', () => {
  beforeEach(() => vi.clearAllMocks());

  const setPlatform = (platform: string) => {
    Object.defineProperty(window, 'electron', { configurable: true, value: { platform, on: vi.fn(), off: vi.fn() } });
  };

  it('hides the entire unsupported Channels entry on Mac', async () => {
    setPlatform('darwin');
    await act(async () => { render(<App />); });
    expect(screen.queryByText('视频号')).not.toBeInTheDocument();
    expect(screen.getByText('下载中心')).toBeInTheDocument();
    expect(screen.getByText('字幕处理')).toBeInTheDocument();
    expect(ChannelsPanel).not.toHaveBeenCalled();
  });

  it('retains the working Channels entry and page on Windows', async () => {
    setPlatform('win32');
    await act(async () => { render(<App />); });
    fireEvent.click(screen.getByText('视频号'));
    expect(await screen.findByText('视频号内容')).toBeInTheDocument();
  });
});
