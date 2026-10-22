const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { verifyMetadataEnvelope } = require('../../electron/updater-security');

test('generates a signed envelope with the exact installer hash and GitHub URL for each supported build', async (t) => {
  const { createUpdateEnvelope } = require('./generate-github-metadata');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vidflow-metadata-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const keys = crypto.generateKeyPairSync('ed25519');
  for (const [platform, arch, name] of [
    ['win32', 'x64', 'VidFlow Setup 1.1.0.exe'],
    ['darwin', 'arm64', 'VidFlow-1.1.0-macOS-arm64.dmg'],
    ['darwin', 'x64', 'VidFlow-1.1.0-macOS-x64.dmg']
  ]) {
    const filePath = path.join(directory, name); const bytes = Buffer.from(`installer for ${platform} ${arch}`);
    fs.writeFileSync(filePath, bytes);
    const envelope = await createUpdateEnvelope({ filePath, platform, arch, version: '1.1.0', repository: 'ShuaiJustin/VidFlow',
      releaseNotes: '<pre>New version</pre>', privateKey: keys.privateKey, publicKey: keys.publicKey });
    const metadata = verifyMetadataEnvelope(envelope, keys.publicKey);
    assert.equal(metadata.file_size, bytes.length);
    assert.equal(metadata.file_hash, crypto.createHash('sha512').update(bytes).digest('hex'));
    assert.equal(metadata.download_url, `https://github.com/ShuaiJustin/VidFlow/releases/download/v1.1.0/${encodeURIComponent(name)}`);
    assert.equal(metadata.platform, platform); assert.equal(metadata.arch, arch);
    assert.equal(metadata.release_notes, '<pre>New version</pre>');
  }
});

test('refuses a signing key that does not match the public key bundled in the application', async (t) => {
  const { createUpdateEnvelope } = require('./generate-github-metadata');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vidflow-metadata-key-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filePath = path.join(directory, 'setup.exe'); fs.writeFileSync(filePath, 'installer');
  const a = crypto.generateKeyPairSync('ed25519'), b = crypto.generateKeyPairSync('ed25519');
  await assert.rejects(createUpdateEnvelope({ filePath, platform: 'win32', arch: 'x64', version: '1.1.0',
    privateKey: a.privateKey, publicKey: b.publicKey }), /match/);
});

test('rejects incompatible installer formats, unsupported architectures and prerelease versions', async () => {
  const { createUpdateEnvelope } = require('./generate-github-metadata');
  for (const args of [
    { platform: 'darwin', arch: 'arm64', filePath: 'setup.exe' },
    { platform: 'win32', arch: 'x64', filePath: 'setup.dmg' },
    { platform: 'darwin', arch: 'ia32', filePath: 'setup.dmg' },
    { platform: 'linux', arch: 'x64', filePath: 'setup.AppImage' },
    { platform: 'darwin', arch: 'arm64', filePath: 'setup.dmg', version: '1.1.0-beta' }
  ]) await assert.rejects(createUpdateEnvelope({ version: '1.1.0', ...args }), /Unsupported|stable/);
});
