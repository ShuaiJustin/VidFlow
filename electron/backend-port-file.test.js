const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { removeBackendPortFileAtPath } = require('./backend-port-file');

test('removes a stale backend port file before starting a fresh backend', () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidflow-port-file-'));
  const portFile = path.join(tempDir, 'backend_port.json');
  fs.writeFileSync(portFile, JSON.stringify({ port: 33025, startup_token: 'stale' }));

  try {
    removeBackendPortFileAtPath(portFile, { log() {}, warn() {} });
    assert.equal(fs.existsSync(portFile), false);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
