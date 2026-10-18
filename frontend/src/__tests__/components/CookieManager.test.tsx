import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../components/TauriIntegration', () => ({
  invoke: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

import { invoke } from '../../components/TauriIntegration';
import CookieManager from '../../components/CookieManager';

const mockInvoke = vi.mocked(invoke);

describe('CookieManager managed browser recovery', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    mockInvoke.mockReset();
    mockInvoke.mockImplementation(async (command: string) => {
      if (command === 'get_cookies_status') {
        return {
          status: 'success',
          platforms: [{
            platform: 'xinpianchang',
            name: '新片场',
            description: '新片场视频',
            configured: false,
            category: 'short_video',
          }],
        };
      }
      if (command === 'check_selenium_status') {
        return {
          status: 'success',
          data: {
            is_running: true,
            platform: 'xinpianchang',
            browser: 'chrome',
          },
        };
      }
      return {};
    });
  });

  it('restores an active managed login with its original browser', async () => {
    render(<CookieManager />);

    await waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith('check_selenium_status', {});
    });

    expect(screen.getByText('完成登录')).toBeInTheDocument();
    expect(screen.getByLabelText('选择浏览器')).toHaveValue('chrome');
    expect(screen.getByText(/登录 新片场/)).toBeInTheDocument();
  });

  it('starts a managed login when the selected browser has no matching cookie', async () => {
    mockInvoke.mockImplementation(async (command: string) => {
      if (command === 'get_cookies_status') {
        return {
          status: 'success',
          platforms: [{
            platform: 'xinpianchang',
            name: '新片场',
            description: '新片场视频',
            configured: false,
            category: 'short_video',
          }],
        };
      }
      if (command === 'check_selenium_status') {
        return { status: 'success', data: { is_running: false } };
      }
      if (command === 'extract_cookies_from_browser') {
        throw new Error('No matching cookie found');
      }
      if (command === 'start_cookie_browser') {
        return { status: 'success', navigation_ok: true };
      }
      return {};
    });

    render(<CookieManager targetUrl="https://www.xinpianchang.com/a13685467" />);

    const browserSelect = await screen.findByLabelText('选择浏览器');
    fireEvent.change(browserSelect, { target: { value: 'edge' } });
    fireEvent.click(await screen.findByText('从Edge读取 Cookie（推荐）'));

    await waitFor(() => {
      expect(mockInvoke).toHaveBeenCalledWith('start_cookie_browser', {
        platform: 'xinpianchang',
        browser: 'chrome',
        target_url: 'https://www.xinpianchang.com/a13685467',
      });
    });

    expect(screen.getByText('完成登录')).toBeInTheDocument();
  });
});
