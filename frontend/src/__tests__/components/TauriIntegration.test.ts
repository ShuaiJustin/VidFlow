import { describe, expect, it } from 'vitest';
import { CONTROLLED_BROWSER_START_TIMEOUT_MS } from '../../components/TauriIntegration';


describe('controlled browser startup', () => {
  it('waits longer than the backend WebDriver startup budget', () => {
    expect(CONTROLLED_BROWSER_START_TIMEOUT_MS).toBeGreaterThanOrEqual(210_000);
  });
});
