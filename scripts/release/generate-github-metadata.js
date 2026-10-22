const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { parseArgs } = require('util');
const { compareVersions, resolveDownloadPath, verifyMetadataEnvelope } = require('../../electron/updater-security');
const { DEFAULT_REPOSITORY, validateRepository } = require('../../electron/updater-github');

async function createUpdateEnvelope({ filePath, platform, arch, version, repository = DEFAULT_REPOSITORY,
  releaseNotes = '', privateKey, publicKey }) {
  compareVersions(version, '0.0.0');
  if (version.includes('-')) throw new Error('Only stable release versions are supported');
  if (!((platform === 'win32' && arch === 'x64' && path.extname(filePath).toLowerCase() === '.exe')
    || (platform === 'darwin' && ['arm64', 'x64'].includes(arch) && path.extname(filePath).toLowerCase() === '.dmg'))) {
    throw new Error('Unsupported installer platform, architecture or format');
  }
  validateRepository(repository);
  const fileName = path.basename(filePath);
  resolveDownloadPath(path.dirname(filePath), fileName);
  const key = privateKey instanceof crypto.KeyObject ? privateKey : crypto.createPrivateKey(privateKey);
  if (key.asymmetricKeyType !== 'ed25519' || key.type !== 'private') throw new Error('An Ed25519 signing private key is required');
  const hash = crypto.createHash('sha512');
  for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
  const size = fs.statSync(filePath).size;
  if (!Number.isSafeInteger(size) || size <= 0 || size > 2 * 1024 * 1024 * 1024) throw new Error('Invalid installer size');
  const payload = Buffer.from(JSON.stringify({ schema_version: 1, version, platform, arch, channel: 'stable',
    file_name: fileName, file_size: size, file_hash: hash.digest('hex'),
    download_url: `https://github.com/${repository}/releases/download/v${version}/${encodeURIComponent(fileName)}`,
    release_notes: String(releaseNotes), is_mandatory: false, minimum_version: null }));
  const envelope = { signature_algorithm: 'Ed25519', signed_payload: payload.toString('base64'),
    metadata_signature: crypto.sign(null, payload, key).toString('base64') };
  try { verifyMetadataEnvelope(envelope, publicKey); }
  catch { throw new Error('Signing key does not match the public key bundled in electron/update-public-key.pem'); }
  return envelope;
}

async function main() {
  const { values } = parseArgs({ options: {
    file: { type: 'string' }, platform: { type: 'string' }, arch: { type: 'string' },
    repository: { type: 'string', default: process.env.GITHUB_REPOSITORY || DEFAULT_REPOSITORY },
    'notes-file': { type: 'string' }, 'output-dir': { type: 'string', default: 'dist-output' }
  } });
  if (!values.file || !values.platform || !values.arch || !values['notes-file']) {
    throw new Error('Usage: node scripts/release/generate-github-metadata.js --file INSTALLER --platform win32|darwin --arch x64|arm64 --notes-file NOTES.md [--output-dir DIRECTORY]');
  }
  const version = require('../../package.json').version;
  if (process.env.VIDFLOW_RELEASE_TAG && process.env.VIDFLOW_RELEASE_TAG !== `v${version}`) {
    throw new Error('Release tag must match package.json version');
  }
  if (process.env.GITHUB_REF_TYPE === 'tag' && process.env.GITHUB_REF_NAME !== `v${version}`) {
    throw new Error('Git tag must match package.json version');
  }
  const privateKey = process.env.VIDFLOW_UPDATE_SIGNING_KEY
    || (process.env.VIDFLOW_UPDATE_SIGNING_KEY_PATH && fs.readFileSync(process.env.VIDFLOW_UPDATE_SIGNING_KEY_PATH, 'utf8'));
  if (!privateKey) throw new Error('Set VIDFLOW_UPDATE_SIGNING_KEY or VIDFLOW_UPDATE_SIGNING_KEY_PATH to the Ed25519 private key');
  const notes = fs.readFileSync(values['notes-file'], 'utf8');
  const escapedNotes = notes.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const envelope = await createUpdateEnvelope({ filePath: values.file, platform: values.platform, arch: values.arch, version,
    repository: values.repository, releaseNotes: `<pre style="white-space:pre-wrap">${escapedNotes}</pre>`, privateKey,
    publicKey: fs.readFileSync(path.join(__dirname, '../../electron/update-public-key.pem'), 'utf8') });
  fs.mkdirSync(values['output-dir'], { recursive: true });
  const output = path.join(values['output-dir'], `vidflow-update-${values.platform}-${values.arch}.json`);
  fs.writeFileSync(output, `${JSON.stringify(envelope, null, 2)}\n`);
  console.log(`Signed update metadata: ${output}`);
}

if (require.main === module) main().catch((error) => { console.error(error.message); process.exitCode = 1; });
module.exports = { createUpdateEnvelope };
