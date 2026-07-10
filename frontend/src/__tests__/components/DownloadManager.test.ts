import { describe, expect, it } from 'vitest';
import {
  detectPlatform,
  getPlatformConfig,
  isPlatformCookieConfigured,
  PLATFORMS_REQUIRING_COOKIE,
} from '../../components/DownloadManager';


describe('DownloadManager platform handling', () => {
  it('recognizes Xinpianchang and requires its configured cookie', () => {
    const platform = 'xinpianchang';

    expect(detectPlatform('https://www.xinpianchang.com/a13685467')).toBe(platform);
    expect(PLATFORMS_REQUIRING_COOKIE).toContain(platform);
    expect(getPlatformConfig(platform).name).toBe('新片场');
    expect(isPlatformCookieConfigured({
      status: 'success',
      platforms: [{ platform, configured: true }],
    }, platform)).toBe(true);
  });
});
