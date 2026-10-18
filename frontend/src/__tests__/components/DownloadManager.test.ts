import { describe, expect, it } from 'vitest';
import {
  detectPlatform,
  getPlatformConfig,
  isXinpianchangSecurityError,
  isPlatformCookieConfigured,
  PLATFORMS_REQUIRING_COOKIE,
} from '../../components/DownloadManager';
import * as DownloadManagerModule from '../../components/DownloadManager';


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

  it('recognizes a Xinpianchang detail-page security challenge', () => {
    expect(isXinpianchangSecurityError(
      'xinpianchang',
      new Error('ERROR: [Xinpianchang] HTTP Error 403: Forbidden'),
    )).toBe(true);
    expect(isXinpianchangSecurityError('douyin', new Error('403 Forbidden'))).toBe(false);
  });

  it('limits Xinpianchang quality choices to formats returned by the backend', () => {
    const getOptions = (DownloadManagerModule as any).getAvailableVideoQualityOptions;
    const resolveSelection = (DownloadManagerModule as any).resolveVideoQualitySelection;

    expect(getOptions).toBeTypeOf('function');
    expect(resolveSelection).toBeTypeOf('function');

    const options = getOptions('xinpianchang', [
      { height: 720, quality: '720p', ext: 'mp4' },
      { height: 540, quality: '540p', ext: 'mp4' },
    ]);

    expect(options.map((option: { value: string }) => option.value)).toEqual([
      'best',
      '720p',
      '540p',
      'audio',
    ]);
    expect(resolveSelection('1080p', options)).toBe('720p');
    expect(resolveSelection('480p', options)).toBe('540p');
  });
});
