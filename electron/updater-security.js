const crypto = require('crypto');
const path = require('path');

const ALLOWED_INSTALLER_EXTENSIONS = new Set([
  '.exe',
  '.dmg',
  '.pkg',
  '.deb',
  '.rpm',
  '.appimage'
]);

function assertSecureUpdateUrl(value, allowInsecureLocalhost = false, allowedHosts = null) {
  const parsed = new URL(value);
  const isLocalhost = ['localhost', '127.0.0.1', '::1'].includes(parsed.hostname.replace(/^\[|\]$/g, ''));
  if (parsed.username || parsed.password || parsed.hash) {
    throw new Error('Update URLs must not contain credentials or fragments');
  }
  if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && allowInsecureLocalhost && isLocalhost)) {
    throw new Error('Update URLs must use HTTPS');
  }
  if (allowedHosts && !allowedHosts.has(parsed.hostname)) {
    throw new Error('Update URL host is not allowed');
  }
  return parsed;
}

function canonicalizeValue(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalizeValue);
  }
  if (value && typeof value === 'object') {
    return Object.keys(value)
      .sort()
      .reduce((result, key) => {
        result[key] = canonicalizeValue(value[key]);
        return result;
      }, {});
  }
  return value;
}

function canonicalizeMetadata(metadata) {
  return JSON.stringify(canonicalizeValue(metadata));
}

function resolveDownloadPath(downloadDirectory, fileName) {
  if (typeof fileName !== 'string' || !fileName || /[\\/:\x00-\x1f]/.test(fileName) || path.basename(fileName) !== fileName || /[. ]$/.test(fileName)) {
    throw new Error('Invalid update filename');
  }
  const extension = path.extname(fileName).toLowerCase();
  if (!ALLOWED_INSTALLER_EXTENSIONS.has(extension)) {
    throw new Error('Invalid update filename extension');
  }
  const base = path.resolve(downloadDirectory);
  const target = path.resolve(base, fileName);
  if (path.dirname(target) !== base) {
    throw new Error('Invalid update filename path');
  }
  return target;
}

function verifyMetadataSignature(metadata, signature, publicKey) {
  if (!signature || !publicKey) {
    throw new Error('Update metadata signature is required');
  }
  const valid = crypto.verify(
    null,
    Buffer.from(canonicalizeMetadata(metadata), 'utf8'),
    publicKey,
    Buffer.from(signature, 'base64')
  );
  if (!valid) {
    throw new Error('Update metadata signature verification failed');
  }
}

function verifyMetadataEnvelope(envelope, publicKey) {
  if (!envelope || envelope.signature_algorithm !== 'Ed25519') {
    throw new Error('Unsupported update metadata signature algorithm');
  }
  if (!envelope.signed_payload || !envelope.metadata_signature || !publicKey) {
    throw new Error('Update metadata signature is required');
  }
  const payload = decodeBase64(envelope.signed_payload);
  const signature = decodeBase64(envelope.metadata_signature);
  const key = publicKey instanceof crypto.KeyObject ? publicKey : crypto.createPublicKey(publicKey);
  if (signature.length !== 64 || key.type !== 'public' || key.asymmetricKeyType !== 'ed25519') {
    throw new Error('Invalid Ed25519 update metadata signature or public key');
  }
  const valid = crypto.verify(
    null,
    payload,
    publicKey,
    signature
  );
  if (!valid) {
    throw new Error('Update metadata signature verification failed');
  }
  return JSON.parse(payload.toString('utf8'));
}

function decodeBase64(value) {
  if (typeof value !== 'string' || !value || value.length > 1024 * 1024 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error('Invalid update metadata base64');
  }
  const bytes = Buffer.from(value, 'base64');
  if (bytes.toString('base64') !== value) throw new Error('Invalid update metadata base64');
  return bytes;
}

// SemVer comparison is O(version length); numeric components use BigInt to avoid rounding.
function compareVersions(left, right) {
  const parse = (version) => {
    if (typeof version !== 'string' || version.length > 128) throw new Error('Invalid update version');
    const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(version);
    if (!match) throw new Error('Invalid update version');
    const pre = match[4] ? match[4].split('.') : [];
    if (pre.some((part) => /^\d+$/.test(part) && part.length > 1 && part[0] === '0')) throw new Error('Invalid update version');
    return { core: match.slice(1, 4).map(BigInt), pre };
  };
  const a = parse(left), b = parse(right);
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] > b.core[i] ? 1 : -1;
  if (!a.pre.length || !b.pre.length) return a.pre.length === b.pre.length ? 0 : a.pre.length ? -1 : 1;
  for (let i = 0; i < Math.max(a.pre.length, b.pre.length); i++) {
    if (a.pre[i] === b.pre[i]) continue;
    if (a.pre[i] === undefined || b.pre[i] === undefined) return a.pre[i] === undefined ? -1 : 1;
    const aNumeric = /^\d+$/.test(a.pre[i]), bNumeric = /^\d+$/.test(b.pre[i]);
    if (aNumeric !== bNumeric) return aNumeric ? -1 : 1;
    const x = aNumeric ? BigInt(a.pre[i]) : a.pre[i], y = bNumeric ? BigInt(b.pre[i]) : b.pre[i];
    return x > y ? 1 : -1;
  }
  return 0;
}

module.exports = {
  assertSecureUpdateUrl,
  canonicalizeMetadata,
  compareVersions,
  decodeBase64,
  resolveDownloadPath,
  verifyMetadataEnvelope,
  verifyMetadataSignature
};
