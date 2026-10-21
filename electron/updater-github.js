const crypto = require('crypto');
const axios = require('axios');
const { assertSecureUpdateUrl, compareVersions, verifyMetadataEnvelope } = require('./updater-security');

const DEFAULT_REPOSITORY = 'ShuaiJustin/VidFlow';
const GITHUB_ASSET_HOSTS = new Set([
  'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
  'github-releases.githubusercontent.com'
]);

function validateRepository(repository) {
  if (typeof repository !== 'string' || !/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\/[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(repository)) {
    throw new Error('Invalid GitHub repository; expected owner/name');
  }
  return repository;
}

function githubRequestOptions() {
  return {
    maxRedirects: 5,
    beforeRedirect: (options) => {
      if (options.auth) throw new Error('Update URLs must not contain credentials');
      assertSecureUpdateUrl(`${options.protocol}//${options.hostname}${options.port ? `:${options.port}` : ''}/`, false, GITHUB_ASSET_HOSTS);
    }
  };
}

class GitHubReleaseSource {
  constructor({ repository = DEFAULT_REPOSITORY, httpClient = axios } = {}) {
    this.repository = validateRepository(repository);
    this.http = httpClient;
  }

  async requestUpdateInfo({ currentVersion, platform, arch, channel, updatePublicKey }) {
    const noUpdate = (status, message = '') => Object.freeze({ has_update: false, latest_version: currentVersion,
      rollout_blocked: false, status, rollout_message: message });
    if (channel !== 'stable') return noUpdate('channel_unavailable', 'GitHub 更新源目前仅提供正式版');
    const options = { timeout: 15000, maxContentLength: 1024 * 1024, maxRedirects: 0,
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': `VidFlow/${currentVersion}` } };
    // Do not convert HTTP/network failures into an up-to-date result.
    const { data: release } = await this.http.get(`https://api.github.com/repos/${this.repository}/releases/latest`, options);
    if (!release || typeof release.tag_name !== 'string' || typeof release.draft !== 'boolean'
      || typeof release.prerelease !== 'boolean' || !Array.isArray(release.assets)) throw new Error('Invalid GitHub Release response');
    if (release.draft || release.prerelease) return noUpdate('release_unavailable', '暂无可用正式版');
    const version = release.tag_name.replace(/^v/, '');
    if (compareVersions(version, currentVersion) <= 0) return noUpdate('up_to_date');
    const manifestName = `vidflow-update-${platform}-${arch}.json`;
    const manifest = release.assets.find((asset) => asset.name === manifestName);
    if (!manifest) return noUpdate('platform_unavailable', '该版本尚未提供适用于当前系统和架构的更新包');
    const prefix = `https://github.com/${this.repository}/releases/download/${encodeURIComponent(release.tag_name)}/`;
    if (manifest.browser_download_url !== `${prefix}${encodeURIComponent(manifestName)}`) throw new Error('Invalid GitHub metadata asset URL');
    const { data: envelope } = await this.http.get(manifest.browser_download_url, {
      ...options, ...githubRequestOptions(), headers: { 'User-Agent': `VidFlow/${currentVersion}` }
    });
    const metadata = verifyMetadataEnvelope(envelope, updatePublicKey);
    if (metadata.version !== version || metadata.platform !== platform || metadata.arch !== arch || metadata.channel !== channel) {
      throw new Error('Signed update metadata does not match GitHub release or device');
    }
    const asset = release.assets.find((item) => item.name === metadata.file_name);
    if (!asset || asset.size !== metadata.file_size || asset.browser_download_url !== metadata.download_url
      || metadata.download_url !== `${prefix}${encodeURIComponent(metadata.file_name)}`) {
      throw new Error('Signed installer does not match GitHub release asset');
    }
    return Object.freeze({ ...metadata, has_update: true, latest_version: metadata.version,
      file_hash: metadata.file_hash.toLowerCase(), recommended_update_type: 'full', delta_available: false,
      rollout_blocked: false, status: 'update_available', rollout_message: '',
      release_identity: crypto.createHash('sha256').update(envelope.signed_payload).digest('hex') });
  }
}

module.exports = { DEFAULT_REPOSITORY, GITHUB_ASSET_HOSTS, GitHubReleaseSource, githubRequestOptions, validateRepository };
