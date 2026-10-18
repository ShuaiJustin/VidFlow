const { app } = require('electron');
const fs = require('fs');
const path = require('path');
const axios = require('axios');
const { CustomUpdater: VerifiedUpdater } = require('./updater-core');

// Keep the desktop integration API; signing, download and install state live in the shared core.
class CustomUpdater extends VerifiedUpdater {
  constructor(options = {}) {
    const keyPath = process.env.VIDFLOW_UPDATE_PUBLIC_KEY_PATH || path.join(__dirname, 'update-public-key.pem');
    super({
      ...options,
      updateServerUrl: options.updateServerUrl || process.env.VIDFLOW_UPDATE_SERVER_URL || 'https://shcrystal.top',
      updatePublicKey: options.updatePublicKey || fs.readFileSync(keyPath, 'utf8'),
      channel: String(options.channel || process.env.VIDFLOW_UPDATE_CHANNEL || 'stable'),
      arch: String(options.arch || process.arch).toLowerCase(),
      httpClient: options.httpClient || axios.create({ proxy: false }),
      downloadPath: options.downloadPath || path.join(app.getPath('downloads'), 'VidFlow', 'updates')
    });
    // ponytail: full-only until a signed, compatible delta format has an independently tested apply path.
    this.useDeltaUpdate = false;
    this.deltaUpdateApplied = false;
  }

  normalizeArch(rawArch) {
    const arch = String(rawArch || '').toLowerCase();
    return ['ia32', 'x32'].includes(arch) ? 'x32' : (arch || 'x64');
  }

  async downloadDeltaUpdate() { throw new Error('This client supports verified full updates only'); }

  cleanOldUpdateFiles() { return this.cleanUpdateFiles(true); }
  cleanAllUpdateFiles() { return this.cleanUpdateFiles(false); }

  cleanUpdateFiles(expiredOnly) {
    if (this.downloading || this.installing || fs.existsSync(this.pendingInstallPath)) {
      return { success: false, message: 'An update is in progress; update files were preserved' };
    }
    let count = 0, size = 0;
    try {
      for (const name of fs.readdirSync(this.downloadPath)) {
        // Cleanup owns installer files and their resume sidecars, never legacy backup/pending directories.
        if (!/\.(exe|dmg|pkg|deb|rpm|appimage)(?:\.part(?:\.json)?)?$/i.test(name)) continue;
        const file = path.join(this.downloadPath, name);
        const stat = fs.lstatSync(file);
        if (!stat.isFile() || file === this.downloadedFilePath || (expiredOnly && Date.now() - stat.mtimeMs <= 3 * 86400000)) continue;
        fs.unlinkSync(file); count++; size += stat.size;
      }
      return { success: true, message: `Cleaned ${count} update file(s)`, count, size };
    } catch (error) { return { success: false, message: error.message, count, size }; }
  }
}

module.exports = { CustomUpdater };
