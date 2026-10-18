const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function loadDesktopUpdater(directory) {
  const app = { getVersion: () => '1.0.0', getPath: () => directory, isPackaged: false };
  const cache = new Map();
  const load = (name) => {
    if (cache.has(name)) return cache.get(name);
    const file = path.join(__dirname, name);
    const module = { exports: {} };
    const sandbox = { module, exports: module.exports, __dirname, Buffer, URL, AbortController, console, setTimeout, clearTimeout, setInterval, clearInterval,
      process: { platform: 'win32', arch: 'x64', env: {} },
      require: (id) => id === 'electron' ? { app, shell: {} } : id === './updater-core' ? load('updater-core.js') : id.startsWith('.') ? require(path.resolve(__dirname, id)) : require(id) };
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: file });
    cache.set(name, module.exports);
    return module.exports;
  };
  return load('updater-custom.js').CustomUpdater;
}

test('desktop updater verifies offline release and resumes a real interrupted HTTP download', async (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vidflow-http-updater-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const keys = crypto.generateKeyPairSync('ed25519');
  const bytes = Buffer.from('signed installer bytes for an isolated HTTP test');
  const seen = [];
  let envelope, first = true;
  const server = http.createServer((req, res) => {
    if (req.method === 'POST') {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        seen.push({ path: req.url, body: JSON.parse(body) });
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify({ data: req.url.endsWith('/check') ? { release: envelope, decision: { status: 'update_available', rollout_blocked: false, message: '' } } : {} }));
      });
      return;
    }
    if (first) {
      first = false; res.setHeader('Content-Length', bytes.length); res.write(bytes.subarray(0, 8));
      setTimeout(() => res.destroy(), 50); return;
    }
    assert.equal(req.headers.range, 'bytes=8-');
    res.writeHead(206, { 'Content-Range': `bytes 8-${bytes.length - 1}/${bytes.length}`, 'Content-Length': bytes.length - 8 });
    res.end(bytes.subarray(8));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const serverUrl = `http://127.0.0.1:${server.address().port}`;
  const payload = Buffer.from(JSON.stringify({ schema_version: 1, version: '2.0.0', channel: 'stable', platform: 'win32', arch: 'x64',
    file_name: 'VidFlow-2.0.0.exe', file_size: bytes.length, file_hash: crypto.createHash('sha512').update(bytes).digest('hex'),
    download_url: `${serverUrl}/installer.exe`, release_notes: '', is_mandatory: false, minimum_version: null }));
  envelope = { signature_algorithm: 'Ed25519', signed_payload: payload.toString('base64'), metadata_signature: crypto.sign(null, payload, keys.privateKey).toString('base64') };
  const CustomUpdater = loadDesktopUpdater(directory);
  const updater = new CustomUpdater({ updateServerUrl: serverUrl, updatePublicKey: keys.publicKey, allowInsecureLocalhost: true, autoCheck: false, autoDownload: false });
  updater.on('error', () => {}); t.after(() => updater.dispose());
  assert.equal(updater.useDeltaUpdate, false);
  assert.equal(updater.normalizeArch('ia32'), 'x32');
  assert.equal((await updater.checkForUpdates()).latest_version, '2.0.0');
  await assert.rejects(updater.downloadUpdate());
  assert.equal(fs.statSync(path.join(updater.downloadPath, 'VidFlow-2.0.0.exe.part')).size, 8);
  assert.equal(updater.downloading, false);
  await updater.downloadUpdate();
  assert.deepEqual(fs.readFileSync(updater.downloadedFilePath), bytes);
  assert.equal(JSON.stringify(seen[0].body.supported_update_types), '["full"]');
  assert.equal(seen.filter((entry) => entry.path.endsWith('/stats/download')).length, 1);
  assert.equal(updater.cleanAllUpdateFiles().count, 0, 'verified active installer is preserved');
});

test('desktop refuses unsigned remote HTTP and preserves cleanup/download interfaces', () => {
  const CustomUpdater = loadDesktopUpdater(os.tmpdir());
  assert.throws(() => new CustomUpdater({ updateServerUrl: 'http://updates.example.com', updatePublicKey: 'unused' }), /HTTPS/);
  for (const method of ['checkForUpdates', 'downloadUpdate', 'downloadDeltaUpdate', 'quitAndInstall', 'cleanOldUpdateFiles', 'cleanAllUpdateFiles', 'confirmUpdateHealth', 'cancelDownload']) {
    assert.equal(typeof CustomUpdater.prototype[method], 'function', method);
  }
});
