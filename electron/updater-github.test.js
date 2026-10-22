const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { Readable } = require('node:stream');
const { EventEmitter } = require('node:events');

const repository = 'ShuaiJustin/VidFlow';
const prefix = `https://github.com/${repository}/releases/download/v1.1.0/`;
const keys = crypto.generateKeyPairSync('ed25519');
const context = { currentVersion: '1.0.7', platform: 'darwin', arch: 'arm64', channel: 'stable', updatePublicKey: keys.publicKey };
function fixture(overrides = {}) {
  const metadata = { schema_version: 1, version: '1.1.0', platform: 'darwin', arch: 'arm64', channel: 'stable',
    file_name: 'VidFlow-1.1.0-macOS-arm64.dmg', file_size: 42, file_hash: 'a'.repeat(128),
    download_url: `${prefix}VidFlow-1.1.0-macOS-arm64.dmg`, release_notes: 'New version', is_mandatory: false, ...overrides };
  const bytes = Buffer.from(JSON.stringify(metadata));
  const envelope = { signature_algorithm: 'Ed25519', signed_payload: bytes.toString('base64'), metadata_signature: crypto.sign(null, bytes, keys.privateKey).toString('base64') };
  const release = { tag_name: 'v1.1.0', draft: false, prerelease: false, assets: [
    { name: 'vidflow-update-darwin-arm64.json', browser_download_url: `${prefix}vidflow-update-darwin-arm64.json` },
    { name: metadata.file_name, size: metadata.file_size, browser_download_url: metadata.download_url }
  ] };
  const calls = [];
  const http = { get: async (url, options) => { calls.push({ url, options }); return { data: url.includes('api.github.com') ? release : envelope }; } };
  return { metadata, envelope, release, calls, http };
}
function source(http) {
  const { GitHubReleaseSource } = require('./updater-github');
  return new GitHubReleaseSource({ repository, httpClient: http });
}

test('selects signed metadata for the Mac architecture and preserves release identity', async () => {
  const f = fixture(); const updater = source(f.http);
  const info = await updater.requestUpdateInfo(context);
  assert.equal(f.calls[0].url, `https://api.github.com/repos/${repository}/releases/latest`);
  assert.equal(f.calls[1].url, `${prefix}vidflow-update-darwin-arm64.json`);
  assert.equal(info.version, '1.1.0'); assert.equal(info.has_update, true);
  assert.equal(info.recommended_update_type, 'full');
  assert.equal((await updater.requestUpdateInfo(context)).release_identity, info.release_identity);
});

test('same or older versions need no metadata fetch', async () => {
  for (const tag of ['v1.0.7', 'v1.0.0']) {
    const f = fixture(); f.release.tag_name = tag;
    assert.equal((await source(f.http).requestUpdateInfo(context)).status, 'up_to_date');
    assert.equal(f.calls.length, 1);
  }
});

test('does not offer drafts, prereleases or a different platform architecture', async () => {
  for (const flag of ['draft', 'prerelease']) {
    const f = fixture(); f.release[flag] = true;
    assert.equal((await source(f.http).requestUpdateInfo(context)).has_update, false);
  }
  const f = fixture();
  const info = await source(f.http).requestUpdateInfo({ ...context, arch: 'x64' });
  assert.equal(info.has_update, false); assert.equal(info.status, 'platform_unavailable');
  assert.notEqual(info.status, 'up_to_date');
});

test('rejects tampered metadata and tag, installer URL or size mismatches', async () => {
  const tampered = fixture(); tampered.envelope.signed_payload = Buffer.from('{}').toString('base64');
  await assert.rejects(source(tampered.http).requestUpdateInfo(context), /signature/);
  for (const overrides of [{ version: '1.2.0' }, { arch: 'x64' }, { platform: 'win32' }, { channel: 'beta' }]) {
    const f = fixture(overrides);
    await assert.rejects(source(f.http).requestUpdateInfo(context), /match/);
  }
  const f = fixture(); f.release.assets[1].size++;
  await assert.rejects(source(f.http).requestUpdateInfo(context), /match/);
  const external = fixture({ download_url: 'https://example.com/installer.dmg' });
  await assert.rejects(source(external.http).requestUpdateInfo(context), /GitHub/);
});

test('rate limits and network errors fail checks instead of claiming up to date', async () => {
  for (const status of [403, 404, 429, 500]) {
    const http = { get: async () => { throw Object.assign(new Error(`HTTP ${status}`), { response: { status } }); } };
    await assert.rejects(source(http).requestUpdateInfo(context), new RegExp(String(status)));
  }
});

test('permits GitHub asset redirects and rejects untrusted or insecure redirects', () => {
  const { githubRequestOptions } = require('./updater-github');
  const options = githubRequestOptions();
  assert.equal(options.maxRedirects, 5);
  assert.doesNotThrow(() => options.beforeRedirect({ protocol: 'https:', hostname: 'release-assets.githubusercontent.com', path: '/asset' }));
  for (const redirect of [
    { protocol: 'http:', hostname: 'release-assets.githubusercontent.com' },
    { protocol: 'https:', hostname: 'evil.example' },
    { protocol: 'https:', hostname: 'github.com.evil.example' },
    { protocol: 'https:', hostname: 'github.com', auth: 'user:password' }
  ]) assert.throws(() => options.beforeRedirect(redirect), /HTTPS|host|credentials/);
});

test('rejects malformed repository configuration', () => {
  const { GitHubReleaseSource } = require('./updater-github');
  for (const repository of ['https://github.com/a/b', '../repo', 'a/b/c', '']) {
    assert.throws(() => new GitHubReleaseSource({ repository }), /repository/);
  }
});

function desktopFixture(t, platform, arch) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vidflow-github-desktop-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const bytes = Buffer.alloc(42, 65);
  const name = platform === 'darwin' ? `VidFlow-1.1.0-macOS-${arch}.dmg` : 'VidFlow-Setup-1.1.0-x64.exe';
  const f = fixture({ platform, arch, file_name: name, download_url: `${prefix}${name}`,
    file_hash: crypto.createHash('sha512').update(bytes).digest('hex') });
  f.release.assets[0] = { name: `vidflow-update-${platform}-${arch}.json`, browser_download_url: `${prefix}vidflow-update-${platform}-${arch}.json` };
  const downloads = [], opened = [], spawned = [];
  let quitCount = 0;
  const client = async (options) => {
    downloads.push(options);
    return { status: 200, headers: { 'content-length': String(bytes.length) }, data: Readable.from([bytes]) };
  };
  client.get = f.http.get;
  client.post = () => { throw new Error('GitHub updates must not POST to the old server'); };
  const app = { getVersion: () => '1.0.7', getPath: () => directory, isPackaged: true, quit: () => { quitCount++; } };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'updater-core.js'), 'utf8'), {
    module, exports: module.exports, Buffer, URL, AbortController, setTimeout, clearTimeout, setInterval, clearInterval,
    process: { platform, arch },
    require: (id) => {
      if (id === 'electron') return { app, shell: { openPath: async (file) => { opened.push(file); return ''; } } };
      if (id === 'child_process') return { spawn: (file, args) => {
        spawned.push({ file, args }); const child = new EventEmitter(); child.unref = () => {};
        process.nextTick(() => child.emit('spawn')); return child;
      } };
      return id.startsWith('.') ? require(path.resolve(__dirname, id)) : require(id);
    }
  });
  const { githubRequestOptions, GITHUB_ASSET_HOSTS } = require('./updater-github');
  const updater = new module.exports.CustomUpdater({ updatePublicKey: keys.publicKey, releaseSource: source(client),
    downloadRequestOptions: githubRequestOptions(), allowedUpdateHosts: GITHUB_ASSET_HOSTS,
    httpClient: client, autoDownload: false, autoCheck: false });
  updater.on('error', () => {});
  t.after(() => updater.dispose());
  return { ...f, updater, bytes, downloads, opened, spawned, directory, quits: () => quitCount };
}

test('Mac arm64 and Intel updates download verified DMGs and exit after opening the installer', async (t) => {
  for (const arch of ['arm64', 'x64']) {
    const f = desktopFixture(t, 'darwin', arch);
    await f.updater.checkForUpdates();
    assert.equal(f.downloads.length, 0, 'checking must not automatically download');
    await f.updater.downloadUpdate();
    assert.deepEqual(fs.readFileSync(f.updater.downloadedFilePath), f.bytes);
    assert.equal(f.downloads[0].maxRedirects, 5);
    const result = await f.updater.quitAndInstall();
    assert.equal(result.manualInstall, true); assert.equal(f.opened.length, 1);
    assert.equal(f.quits(), 1);
    assert.equal(await f.updater.confirmUpdateHealth(), false, 'old version cannot confirm a new install');
  }
});

test('Windows update starts the verified NSIS installer and exits without server statistics', async (t) => {
  const f = desktopFixture(t, 'win32', 'x64');
  await f.updater.checkForUpdates(); await f.updater.downloadUpdate();
  assert.equal((await f.updater.quitAndInstall()).manualInstall, false);
  assert.equal(f.spawned.length, 1); assert.equal(f.spawned[0].args[0], '/S');
  assert.equal(f.quits(), 1); assert.equal(f.opened.length, 0);
  f.updater.currentVersion = '1.1.0';
  assert.equal(await f.updater.confirmUpdateHealth(), true);
  assert.equal(fs.existsSync(f.updater.pendingInstallPath), false);
});

test('rejects an installer modified after download and a release withdrawn before install', async (t) => {
  for (const change of ['file', 'release']) {
    const f = desktopFixture(t, 'darwin', 'arm64');
    await f.updater.checkForUpdates(); await f.updater.downloadUpdate();
    if (change === 'file') fs.writeFileSync(f.updater.downloadedFilePath, Buffer.alloc(42, 66));
    else f.release.assets = [];
    await assert.rejects(f.updater.quitAndInstall(), /integrity|No update/);
    assert.equal(f.quits(), 0); assert.equal(f.opened.length, 0);
  }
});
