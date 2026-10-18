const { app, shell } = require('electron');
const { EventEmitter } = require('events');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const axios = require('axios');
const { pipeline } = require('stream/promises');
const { Transform } = require('stream');
const { assertSecureUpdateUrl, resolveDownloadPath, verifyMetadataEnvelope, decodeBase64, compareVersions } = require('./updater-security');

class CustomUpdater extends EventEmitter {
  constructor(options = {}) {
    super();
    this.updateServerUrl = String(options.updateServerUrl || 'https://shcrystal.top').replace(/\/+$/, '');
    this.allowInsecureLocalhost = options.allowInsecureLocalhost === true;
    const server = assertSecureUpdateUrl(this.updateServerUrl, this.allowInsecureLocalhost);
    this.allowedUpdateHosts = new Set(options.allowedUpdateHosts || [server.hostname]);
    this.updatePublicKey = options.updatePublicKey;
    this.allowUnsignedDevelopment = options.allowUnsignedDevelopment === true
      && this.allowInsecureLocalhost && !app.isPackaged
      && ['localhost', '127.0.0.1', '::1'].includes(server.hostname.replace(/^\[|\]$/g, ''));
    if (!this.updatePublicKey && !this.allowUnsignedDevelopment) throw new Error('Update metadata public key is required');
    this.maxDownloadSize = options.maxDownloadSize || 2 * 1024 * 1024 * 1024;
    this.currentVersion = app.getVersion();
    this.platform = process.platform;
    this.arch = ['ia32', 'x32'].includes(options.arch || process.arch) ? 'x32' : (options.arch || process.arch);
    this.channel = options.channel || 'stable';
    this.userId = this.getUserId();
    this.http = options.httpClient || axios;
    this.downloadPath = options.downloadPath || path.join(app.getPath('temp'), 'vidflow-updates');
    this.pendingInstallPath = path.join(app.getPath('userData'), 'pending_install.json');
    this.autoCheck = options.autoCheck !== false;
    this.autoDownload = options.autoDownload !== false;
    this.checkIntervalMs = options.checkIntervalMs || 4 * 60 * 60 * 1000;
    this.downloading = false;
    this.installing = false;
    this.downloadController = null;
    this.downloadProgress = 0;
    this.updateInfo = null;
    this.downloadedFilePath = null;
    this.downloadedIdentity = null;
    this.checkPromise = null;
    this.autoCheckTimers = [];
    fs.mkdirSync(this.downloadPath, { recursive: true });
  }

  getUserId() {
    const file = path.join(app.getPath('userData'), 'user_id.txt');
    if (fs.existsSync(file)) return fs.readFileSync(file, 'utf8').trim();
    const id = `user_${crypto.randomUUID()}`;
    fs.writeFileSync(file, id);
    return id;
  }

  startAutoCheck(initialDelayMs = 5000) {
    this.stopAutoCheck();
    if (!this.autoCheck) return;
    const check = () => {
      if (!this.autoCheck || this.downloading || this.installing || this.checkPromise) return;
      this.checkForUpdates().catch(() => {});
    };
    this.autoCheckTimers = [setTimeout(check, initialDelayMs), setInterval(check, this.checkIntervalMs)];
    this.autoCheckTimers.forEach((timer) => timer.unref?.());
  }

  stopAutoCheck() {
    this.autoCheckTimers.forEach((timer) => { clearTimeout(timer); clearInterval(timer); });
    this.autoCheckTimers = [];
  }

  dispose() { this.stopAutoCheck(); this.cancelDownload(); }

  validateRelease(release) {
    if (!release || typeof release !== 'object' || Array.isArray(release)) throw new Error('Invalid update release');
    if (release.schema_version !== 1) throw new Error('Unsupported update release schema version');
    for (const field of ['platform', 'arch', 'channel']) {
      if (release[field] !== this[field]) throw new Error(`Update ${field} does not match this device`);
    }
    if (compareVersions(release.version, this.currentVersion) <= 0) throw new Error('Update version must be an upgrade');
    if (release.minimum_version != null && compareVersions(this.currentVersion, release.minimum_version) < 0) throw new Error('Current version cannot upgrade directly to this release');
    if (!Number.isSafeInteger(release.file_size) || release.file_size <= 0 || release.file_size > this.maxDownloadSize) throw new Error('Invalid update file size');
    if (typeof release.file_hash !== 'string' || !/^[a-f0-9]{128}$/i.test(release.file_hash)) throw new Error('Invalid update file hash');
    if (typeof release.release_notes !== 'string' || typeof release.is_mandatory !== 'boolean') throw new Error('Invalid update release notes or mandatory flag');
    assertSecureUpdateUrl(release.download_url, this.allowInsecureLocalhost, this.allowedUpdateHosts);
    resolveDownloadPath(this.downloadPath, release.file_name);
    return release;
  }

  async requestUpdateInfo() {
    const response = await this.http.post(`${this.updateServerUrl}/api/v1/updates/check`, {
      current_version: this.currentVersion, platform: this.platform, arch: this.arch,
      user_id: this.userId, channel: this.channel, supported_update_types: ['full']
    }, { timeout: 10000, maxRedirects: 0, maxContentLength: 1024 * 1024,
      headers: { 'Content-Type': 'application/json', 'User-Agent': `VidFlow/${this.currentVersion} (${this.platform})` } });
    const data = response.data?.data;
    if (!data || typeof data !== 'object') throw new Error('Update server returned an invalid payload');
    const decision = data.decision;
    if (!decision || typeof decision.status !== 'string' || typeof decision.rollout_blocked !== 'boolean') throw new Error('Update server returned an invalid decision');
    if (data.release === null) return Object.freeze({ has_update: false, latest_version: this.currentVersion, rollout_blocked: decision.rollout_blocked, status: decision.status, rollout_message: String(decision.message || '') });
    let release;
    if (data.release?.signature_algorithm === 'none' && this.allowUnsignedDevelopment) {
      release = JSON.parse(decodeBase64(data.release.signed_payload).toString('utf8'));
      const host = new URL(release.download_url).hostname.replace(/^\[|\]$/g, '');
      if (!['localhost', '127.0.0.1', '::1'].includes(host)) throw new Error('Unsigned updates require loopback downloads');
    } else {
      release = verifyMetadataEnvelope(data.release, this.updatePublicKey);
    }
    this.validateRelease(release);
    return Object.freeze({ ...release, latest_version: release.version, has_update: true,
      file_hash: release.file_hash.toLowerCase(), recommended_update_type: 'full', delta_available: false,
      rollout_blocked: decision.rollout_blocked || decision.status !== 'update_available',
      rollout_percentage: decision.rollout_percentage, rollout_message: String(decision.message || ''),
      release_identity: crypto.createHash('sha256').update(data.release.signed_payload).digest('hex') });
  }

  async checkForUpdates() {
    if (this.checkPromise) return this.checkPromise;
    if (this.downloading || this.installing) throw new Error('Update operation already in progress');
    this.checkPromise = this.performCheck();
    try { return await this.checkPromise; } finally { this.checkPromise = null; }
  }

  async performCheck() {
    this.emit('checking-for-update');
    let info;
    try {
      info = await this.requestUpdateInfo();
      if (this.updateInfo?.release_identity !== info.release_identity || !info.has_update || info.rollout_blocked) {
        this.downloadedFilePath = null;
        this.downloadedIdentity = null;
        if (this.downloading) this.cancelDownload();
      }
      this.updateInfo = info;
      if (!info.has_update) this.emit('update-not-available', info);
      else {
        this.emit('update-available', info);
      }
    } catch (error) {
      this.updateInfo = null;
      this.downloadedFilePath = null;
      this.downloadedIdentity = null;
      this.emit('error', error);
      throw error;
    }
    if (this.autoDownload && info.has_update && !info.rollout_blocked && !this.downloading && !this.installing) await this.downloadUpdate();
    return info;
  }

  assertEligible(info = this.updateInfo) {
    if (!info?.has_update) throw new Error('No update available');
    if (info.rollout_blocked) throw new Error('Update is blocked by rollout policy');
    this.validateRelease(info);
    return info;
  }

  async refreshEligibility(info) {
    const fresh = await this.requestUpdateInfo();
    this.assertEligible(fresh);
    if (fresh.release_identity !== info.release_identity) {
      this.updateInfo = fresh;
      this.downloadedFilePath = null;
      this.downloadedIdentity = null;
      throw new Error('Update release changed; check for updates again');
    }
    return fresh;
  }

  cancelDownload() {
    if (!this.downloadController) return false;
    this.downloadController.abort();
    return true;
  }

  async downloadUpdate() {
    if (this.downloading || this.installing) throw new Error('Update operation already in progress');
    const info = this.assertEligible();
    this.downloading = true;
    this.downloadController = new AbortController();
    const signal = this.downloadController.signal;
    this.downloadedFilePath = null;
    this.downloadedIdentity = null;
    let partialPath, bindingPath, discard = false;
    try {
      await this.refreshEligibility(info);
      signal.throwIfAborted();
      const localPath = resolveDownloadPath(this.downloadPath, info.file_name);
      partialPath = `${localPath}.part`;
      bindingPath = `${partialPath}.json`;
      const clearPartial = () => {
        for (const file of [partialPath, bindingPath]) if (fs.existsSync(file)) fs.unlinkSync(file);
      };
      const binding = JSON.stringify({ release_identity: info.release_identity, version: info.version,
        file_hash: info.file_hash, file_size: info.file_size });
      const verified = async (file) => fs.statSync(file).size === info.file_size && (await this.calculateFileHash(file)) === info.file_hash;
      if (fs.existsSync(localPath) && !(await verified(localPath))) fs.unlinkSync(localPath);
      if (!fs.existsSync(localPath)) {
        if (!fs.existsSync(bindingPath) || fs.readFileSync(bindingPath, 'utf8') !== binding || (fs.existsSync(partialPath) && fs.statSync(partialPath).size > info.file_size)) clearPartial();
        fs.writeFileSync(bindingPath, binding);
        let offset = fs.existsSync(partialPath) ? fs.statSync(partialPath).size : 0;
        this.emit('download-start', { type: 'full' });
        if (offset < info.file_size) {
          const response = await this.http({ method: 'GET', url: info.download_url, responseType: 'stream',
            signal, timeout: 30000, maxRedirects: 0, maxContentLength: this.maxDownloadSize, maxBodyLength: this.maxDownloadSize,
            headers: { 'User-Agent': `VidFlow/${this.currentVersion} (${this.platform})`, ...(offset ? { Range: `bytes=${offset}-` } : {}) } });
          if (response.status === 200) offset = 0;
          else if (response.status === 206) {
            const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers?.['content-range'] || '');
            if (!range || Number(range[1]) !== offset || Number(range[2]) !== info.file_size - 1 || Number(range[3]) !== info.file_size) {
              discard = true; response.data.destroy(); throw new Error('Invalid update Content-Range');
            }
          } else { discard = true; response.data.destroy(); throw new Error('Unexpected update download response'); }
          const length = response.headers?.['content-length'];
          if (length != null && (!/^\d+$/.test(String(length)) || Number(length) !== info.file_size - offset)) {
            discard = true; response.data.destroy(); throw new Error('Downloaded update response size does not match signed metadata');
          }
          let transferred = offset;
          const started = Date.now();
          const limiter = new Transform({ transform: (chunk, encoding, callback) => {
            transferred += chunk.length;
            if (transferred > info.file_size) { discard = true; callback(new Error('Downloaded update exceeds signed file size')); return; }
            this.downloadProgress = Math.min(100, Math.floor(transferred * 100 / info.file_size));
            this.emit('download-progress', { percent: this.downloadProgress, transferred, total: info.file_size,
              bytesPerSecond: Math.round((transferred - offset) * 1000 / Math.max(1, Date.now() - started)) });
            callback(null, chunk);
          } });
          await pipeline(response.data, limiter, fs.createWriteStream(partialPath, { flags: offset ? 'a' : 'w' }), { signal });
          if (transferred !== info.file_size) throw new Error('Downloaded update size does not match signed metadata');
        }
        if (!(await verified(partialPath))) { discard = true; throw new Error('File hash verification failed'); }
        signal.throwIfAborted();
        fs.renameSync(partialPath, localPath);
        clearPartial();
      }
      signal.throwIfAborted();
      this.downloadedFilePath = localPath;
      this.downloadedIdentity = info.release_identity;
      await this.reportDownloadComplete(info);
      signal.throwIfAborted();
      this.emit('update-downloaded', { version: info.version, path: localPath, type: 'full', requiresRestart: this.platform === 'win32', manualInstall: this.platform !== 'win32' });
    } catch (error) {
      if (error.response?.status === 416) discard = true;
      if (discard) for (const file of [partialPath, bindingPath]) if (file && fs.existsSync(file)) fs.unlinkSync(file);
      if (signal.aborted) {
        this.downloadedFilePath = null;
        this.downloadedIdentity = null;
        this.emit('download-cancelled');
        throw Object.assign(new Error('Update download cancelled'), { code: 'ERR_CANCELED' });
      }
      else this.emit('error', error);
      throw error;
    } finally { this.downloading = false; this.downloadController = null; }
  }

  async calculateFileHash(filePath) {
    const hash = crypto.createHash('sha512');
    for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
    return hash.digest('hex');
  }

  async postStat(endpoint, payload) {
    try {
      await this.http.post(`${this.updateServerUrl}/api/v1/stats/${endpoint}`, payload, { timeout: 5000, maxRedirects: 0 });
      return true;
    } catch { return false; }
  }

  reportDownloadComplete(info) {
    return this.postStat('download', { user_id: this.userId, version: info.version, from_version: this.currentVersion,
      status: 'completed', platform: this.platform, arch: this.arch, channel: this.channel });
  }

  writePendingInstallMarker(info = this.assertEligible()) {
    const marker = { attempt_id: crypto.randomUUID(), user_id: this.userId, from_version: this.currentVersion,
      to_version: info.version, platform: this.platform, arch: this.arch, channel: this.channel, created_at: new Date().toISOString() };
    fs.writeFileSync(this.pendingInstallPath, JSON.stringify(marker), 'utf8');
    return marker;
  }

  clearPendingInstallMarker(expectedAttemptId) {
    if (!fs.existsSync(this.pendingInstallPath)) return;
    const current = JSON.parse(fs.readFileSync(this.pendingInstallPath, 'utf8'));
    if (current.attempt_id !== expectedAttemptId) return;
    fs.unlinkSync(this.pendingInstallPath);
  }

  // The host calls this only after its core dependencies and main window have passed health checks.
  async confirmUpdateHealth() {
    if (!fs.existsSync(this.pendingInstallPath)) return false;
    try {
      const marker = JSON.parse(fs.readFileSync(this.pendingInstallPath, 'utf8'));
      if (marker.status === 'failed') {
        if (await this.postStat('install', marker)) this.clearPendingInstallMarker(marker.attempt_id);
        return false;
      }
      if (marker.to_version !== this.currentVersion || marker.platform !== this.platform || marker.arch !== this.arch || marker.channel !== this.channel) return false;
      if (!(await this.postStat('install', { ...marker, status: 'success' }))) return false;
      this.clearPendingInstallMarker(marker.attempt_id);
      return true;
    } catch { return false; }
  }

  async quitAndInstall() {
    if (this.installing || this.downloading) throw new Error('Update operation already in progress');
    const info = this.assertEligible();
    if (!this.downloadedFilePath || this.downloadedIdentity !== info.release_identity) throw new Error('No verified update downloaded');
    if (!['win32', 'darwin'].includes(this.platform)) throw new Error(`Automatic installation is unavailable on this platform; install the verified package manually: ${this.downloadedFilePath}`);
    this.installing = true;
    let marker;
    try {
      await this.refreshEligibility(info);
      if (fs.statSync(this.downloadedFilePath).size !== info.file_size || (await this.calculateFileHash(this.downloadedFilePath)) !== info.file_hash) {
        this.downloadedFilePath = null; this.downloadedIdentity = null; throw new Error('Installer integrity verification failed');
      }
      marker = this.writePendingInstallMarker(info);
      await this.postStat('install', { ...marker, status: 'started' });
      if (this.platform === 'win32') {
        const { spawn } = require('child_process');
        await new Promise((resolve, reject) => {
          const child = spawn(this.downloadedFilePath, ['/S'], { detached: true, stdio: 'ignore', windowsHide: true });
          child.once('error', reject);
          child.once('spawn', () => { child.unref(); resolve(); });
        });
        app.quit();
        return { manualInstall: false };
      }
      const openError = await shell.openPath(this.downloadedFilePath);
      if (openError) throw new Error(openError);
      return { manualInstall: true, path: this.downloadedFilePath };
    } catch (error) {
      if (marker) {
        marker.status = 'failed'; marker.error_message = error.message;
        fs.writeFileSync(this.pendingInstallPath, JSON.stringify(marker), 'utf8');
        if (await this.postStat('install', marker)) this.clearPendingInstallMarker(marker.attempt_id);
      }
      throw error;
    } finally { this.installing = false; }
  }
}

module.exports = { CustomUpdater };
