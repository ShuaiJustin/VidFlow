const assert = require('node:assert/strict');
const test = require('node:test');

test('host requires initialized main window and healthy backend before confirming install', async () => {
  const { confirmHealthyUpdate } = require('./updater-health');
  let confirmed = 0, probes = 0;
  const state = { updater: { confirmUpdateHealth: async () => { confirmed++; return true; } },
    mainWindow: { isDestroyed: () => false, webContents: { isDestroyed: () => false } },
    windowReady: true, backendReady: true, backendPort: 8322,
    checkBackendHealth: async () => { probes++; return true; } };
  assert.equal(await confirmHealthyUpdate({ ...state, windowReady: false }), false);
  assert.equal(await confirmHealthyUpdate({ ...state, backendReady: false }), false);
  assert.equal(await confirmHealthyUpdate({ ...state, checkBackendHealth: async () => false }), false);
  assert.equal(await confirmHealthyUpdate({ ...state, isWindowReady: () => false }), false);
  assert.equal(confirmed, 0);
  assert.equal(await confirmHealthyUpdate(state), true);
  assert.equal(confirmed, 1);
  assert.equal(probes, 2);
});
