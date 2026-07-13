# Update System Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a configurable, HTTPS-only, signed, architecture-correct VidFlow desktop update pipeline with reliable full updates and transactional backend/frontend delta updates.

**Architecture:** Build-time metadata is the production source of truth, with explicit managed overrides that cannot replace the embedded signing key. Pure modules handle configuration, release identity, signature verification, and artifact download; `CustomUpdater` coordinates one serialized state machine, while `DeltaUpdater` applies only eligible resources before normal startup.

**Tech Stack:** Electron 28, Node.js CommonJS and `node:test`, Electron Builder 24, React 18/TypeScript/Vitest, Python 3/Pytest, Ed25519 via Node `crypto`.

---

## File Structure

- `electron/update-config.js`: packaged/managed configuration policy.
- `scripts/build/build-electron.js`: Electron Builder wrapper and metadata injection.
- `scripts/release/release-arch.js`: authoritative packaged architecture.
- `electron/update-signature.js`: canonical signed metadata verification.
- `scripts/release/update-keygen.js` and `sign-update-metadata.js`: operator signing tools.
- `electron/update-downloader.js`: bounded atomic downloads.
- `electron/updater-custom.js`: serialized full/delta update coordinator.
- `electron/delta-updater.js`: trusted pending delta transaction.
- `electron/update-bootstrap.js`: apply/relaunch before UI startup.
- `backend/src/core/delta_generator.py`: exact release inventory and full-only policy.
- `frontend/src/utils/appUpdate.ts`: renderer manual-check result mapping.
- Matching `*.test.js`, Vitest, and Pytest files cover every pure module and state transition.

### Task 1: Build-Time Update Configuration

**Files:**
- Create: `electron/update-config.js`
- Create: `electron/update-config.test.js`
- Create: `scripts/build/build-electron.js`
- Create: `scripts/build/build-electron.test.js`
- Modify: `package.json`

- [ ] **Step 1: Write failing configuration tests**

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const { resolveUpdateConfig } = require('./update-config');

const packaged = {
  serverUrl: 'https://updates.shcrystal.top',
  channel: 'stable',
  arch: 'x64',
  publicKey: 'PUBLIC_KEY'
};

test('uses packaged production configuration', () => {
  assert.deepEqual(resolveUpdateConfig({ packaged, env: {}, persisted: {} }), packaged);
});

test('managed endpoint cannot replace trust material', () => {
  const result = resolveUpdateConfig({
    packaged,
    env: { VIDFLOW_ALLOW_UPDATE_SERVER_OVERRIDE: '1' },
    persisted: { updateServerUrl: 'https://updates-backup.example.com' }
  });
  assert.equal(result.serverUrl, 'https://updates-backup.example.com');
  assert.equal(result.publicKey, 'PUBLIC_KEY');
});

test('rejects public HTTP but permits loopback development', () => {
  assert.throws(() => resolveUpdateConfig({
    packaged: { ...packaged, serverUrl: 'http://example.com' },
    env: {},
    persisted: {}
  }), /HTTPS/);
  assert.equal(resolveUpdateConfig({
    packaged: { ...packaged, serverUrl: 'http://127.0.0.1:8321' },
    env: {},
    persisted: {}
  }).serverUrl, 'http://127.0.0.1:8321');
});
```

- [ ] **Step 2: Run RED**

Run: `node --test electron/update-config.test.js`

Expected: FAIL because `electron/update-config.js` does not exist.

- [ ] **Step 3: Implement configuration policy**

Export `assertAllowedUpdateUrl`, `normalizeChannel`, `resolveUpdateConfig`, and `loadPackagedUpdateConfig`. Use this exact precedence:

```javascript
function resolveUpdateConfig({ packaged = {}, env = process.env, persisted = {} }) {
  const allowManaged = env.VIDFLOW_ALLOW_UPDATE_SERVER_OVERRIDE === '1';
  const envUrl = String(env.VIDFLOW_UPDATE_SERVER_URL || '').trim();
  const managedUrl = allowManaged ? String(persisted.updateServerUrl || '').trim() : '';
  const serverUrl = envUrl || managedUrl || String(packaged.serverUrl || '').trim();
  if (!serverUrl) throw new Error('No update server is configured');
  return {
    serverUrl: assertAllowedUpdateUrl(serverUrl),
    channel: normalizeChannel(env.VIDFLOW_UPDATE_CHANNEL || packaged.channel || 'stable'),
    arch: String(packaged.arch || '').trim(),
    publicKey: String(packaged.publicKey || '').trim()
  };
}
```

`loadPackagedUpdateConfig(app)` reads `vidflowUpdate` from `app.getAppPath()/package.json`. Supported channels are only `stable`, `beta`, and `nightly`.

- [ ] **Step 4: Write build-wrapper tests and run RED**

Test `readBuildUpdateConfig(env, args)` and `createBuilderOptions(config, platform, arch, dirOnly)`. Require production HTTPS URL, public-key file, supported channel, and explicit architecture. Assert `extraMetadata.vidflowUpdate` contains only the public PEM and public configuration.

Run: `node --test scripts/build/build-electron.test.js`

Expected: FAIL because the wrapper does not exist.

- [ ] **Step 5: Implement build wrapper**

Load `electron-builder.json`, call Electron Builder programmatically, and merge:

```javascript
extraMetadata: {
  ...baseConfig.extraMetadata,
  vidflowUpdate: {
    serverUrl,
    channel,
    arch,
    publicKey: fs.readFileSync(publicKeyFile, 'utf8')
  }
}
```

Support `--win`, `--mac`, `--linux`, `--arch=<value>`, and `--dir`. Route all package scripts through this wrapper.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
node --test electron/update-config.test.js scripts/build/build-electron.test.js
node --check electron/update-config.js
node --check scripts/build/build-electron.js
git add electron/update-config.js electron/update-config.test.js scripts/build/build-electron.js scripts/build/build-electron.test.js package.json
git commit -m "feat: inject secure update build configuration"
```

### Task 2: Authoritative Release Architecture

**Files:**
- Create: `scripts/release/release-arch.js`
- Create: `scripts/release/release-arch.test.js`
- Modify: `scripts/release/generate-delta.js`
- Modify: `scripts/release/build-and-generate-delta.js`
- Modify: `scripts/release/upload-release.bat`

- [ ] **Step 1: Write the failing NSIS regression**

```javascript
test('does not infer architecture from the NSIS bootstrap stub', () => {
  const installer = writePeFixture(tempDir, 'setup.exe', 0x014c);
  const application = writePeFixture(tempDir, 'VidFlow.exe', 0x8664);
  assert.equal(detectPeArch(installer), 'x32');
  assert.equal(resolveReleaseArch({ expected: 'x64', application }), 'x64');
});

test('fails when unpacked app disagrees with build target', () => {
  const application = writePeFixture(tempDir, 'VidFlow.exe', 0x014c);
  assert.throws(() => resolveReleaseArch({ expected: 'x64', application }), /expected x64.*got x32/);
});
```

- [ ] **Step 2: Run RED**

Run: `node --test scripts/release/release-arch.test.js`

Expected: FAIL because `release-arch.js` does not exist.

- [ ] **Step 3: Implement authoritative architecture**

Export `normalizeArch`, `detectPeArch(filePath)`, and `resolveReleaseArch({ expected, application })`. The CLI prints only the validated architecture. Release scripts take `VIDFLOW_RELEASE_ARCH` or `--arch`, then validate `dist-output/win-unpacked/VidFlow.exe`. Remove installer-stub PE detection from the batch upload.

- [ ] **Step 4: Verify GREEN and real artifact**

```powershell
node --test scripts/release/release-arch.test.js
$env:VIDFLOW_RELEASE_ARCH = 'x64'
node scripts/release/release-arch.js --expected=x64 --application="dist-output/win-unpacked/VidFlow.exe"
```

Expected CLI output: exactly `x64`.

- [ ] **Step 5: Commit**

```powershell
git add scripts/release/release-arch.js scripts/release/release-arch.test.js scripts/release/generate-delta.js scripts/release/build-and-generate-delta.js scripts/release/upload-release.bat
git commit -m "fix: use packaged app release architecture"
```

### Task 3: Signed Release Metadata

**Files:**
- Create: `electron/update-signature.js`
- Create: `electron/update-signature.test.js`
- Create: `scripts/release/update-keygen.js`
- Create: `scripts/release/sign-update-metadata.js`
- Modify: `scripts/release/upload-release.bat`
- Modify: `package.json`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing Ed25519 tests**

```javascript
test('verifies signed metadata and rejects tampering', () => {
  const { publicKey, privateKey } = generateKeyPairSync('ed25519');
  const data = fixtureUpdateInfo();
  const signature = sign(null, canonicalizeUpdateMetadata(data), privateKey).toString('base64');
  const pem = publicKey.export({ type: 'spki', format: 'pem' });
  assert.equal(verifyUpdateEnvelope({ data, signature, signature_algorithm: 'ed25519' }, pem).latest_version, data.latest_version);
  assert.throws(() => verifyUpdateEnvelope({
    data: { ...data, download_url: 'https://evil.example/file.exe' },
    signature,
    signature_algorithm: 'ed25519'
  }, pem), /signature/);
});
```

Also reject missing signatures, unsupported algorithms, altered architecture/channel/version, and altered delta identity.

- [ ] **Step 2: Run RED**

Run: `node --test electron/update-signature.test.js`

Expected: FAIL because `update-signature.js` does not exist.

- [ ] **Step 3: Implement canonical verification**

`canonicalizeUpdateMetadata(data)` creates a fixed-key object containing version, platform, arch, channel, rollout flags, full artifact name/size/SHA-512/URL, and delta source/target/size/hash/URL. `verifyUpdateEnvelope` requires `ed25519` and uses:

```javascript
const valid = crypto.verify(
  null,
  canonicalizeUpdateMetadata(envelope.data),
  publicKey,
  Buffer.from(envelope.signature, 'base64')
);
if (!valid) throw new Error('Update metadata signature verification failed');
return envelope.data;
```

- [ ] **Step 4: Implement operator key and signing tools**

`update-keygen.js --out-dir=<path>` refuses overwrite and writes PKCS8 private PEM plus SPKI public PEM. `sign-update-metadata.js --input --private-key --output` emits:

```json
{
  "data": {},
  "signature": "base64-ed25519-signature",
  "signature_algorithm": "ed25519"
}
```

Ignore `.update-keys/`; add `update:keygen` and `update:sign` scripts.

- [ ] **Step 5: Integrate upload contract**

Require `VIDFLOW_UPDATE_PRIVATE_KEY_FILE` for production upload. Send `metadata_signature` and `signature_algorithm=ed25519` without logging private material. The external service must persist them and return the signed envelope from `POST /api/v1/updates/check`.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
node --test electron/update-signature.test.js
node --check scripts/release/update-keygen.js
node --check scripts/release/sign-update-metadata.js
git add electron/update-signature.js electron/update-signature.test.js scripts/release/update-keygen.js scripts/release/sign-update-metadata.js scripts/release/upload-release.bat package.json .gitignore
git commit -m "feat: sign and verify update metadata"
```

### Task 4: Atomic Bounded Downloads

**Files:**
- Create: `electron/update-downloader.js`
- Create: `electron/update-downloader.test.js`
- Modify: `electron/updater-custom.js`
- Modify: `electron/delta-updater.js`

- [ ] **Step 1: Write failing stream tests**

Inject an HTTP request function returning Node `Readable` streams. Test exact success, overflow, truncation, wrong hash, stream error, timeout, redirect, `.part` cleanup, and atomic rename:

```javascript
test('renames only after exact size and hash verification', async () => {
  const body = Buffer.from('verified update');
  const destination = path.join(tempDir, 'VidFlow Setup.exe');
  const result = await downloadArtifact({
    request: async () => ({ status: 200, data: Readable.from(body) }),
    url: 'https://updates.example.com/file.exe',
    destination,
    expectedSize: body.length,
    expectedHash: sha512(body),
    hashAlgorithm: 'sha512',
    timeoutMs: 1000
  });
  assert.equal(result.path, destination);
  assert.equal(fs.existsSync(destination + '.part'), false);
});

test('removes partial data after declared-size overflow', async () => {
  await assert.rejects(downloadArtifact({
    request: async () => ({ status: 200, data: Readable.from(Buffer.from('too large')) }),
    url: 'https://updates.example.com/file.exe',
    destination,
    expectedSize: 3,
    expectedHash: sha512(Buffer.from('too')),
    hashAlgorithm: 'sha512',
    timeoutMs: 1000
  }), /exceeds declared size/);
  assert.equal(fs.existsSync(destination + '.part'), false);
});
```

- [ ] **Step 2: Run RED**

Run: `node --test electron/update-downloader.test.js`

Expected: FAIL because the downloader does not exist.

- [ ] **Step 3: Implement bounded pipeline**

Export `downloadArtifact(options)`. Validate positive size, supported hash, lowercase hexadecimal digest length, and status 200. Set Axios `maxRedirects: 0` and use an `AbortController` total timeout. Count bytes in a transform:

```javascript
const counter = new Transform({
  transform(chunk, encoding, callback) {
    transferred += chunk.length;
    if (transferred > expectedSize) return callback(new Error('Update download exceeds declared size'));
    hash.update(chunk);
    onProgress?.({
      transferred,
      total: expectedSize,
      percent: Math.min(100, Math.round(transferred * 100 / expectedSize))
    });
    callback(null, chunk);
  }
});
await pipeline(response.data, counter, fs.createWriteStream(partPath, { flags: 'wx' }));
```

Require exact final size and digest, then atomically rename. Delete `.part` on every failure.

- [ ] **Step 4: Route full and delta downloads through it**

Replace both direct writer implementations. Preserve event names and add stable `type: 'full' | 'delta'` progress fields.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
node --test electron/update-downloader.test.js
node --check electron/updater-custom.js
node --check electron/delta-updater.js
git add electron/update-downloader.js electron/update-downloader.test.js electron/updater-custom.js electron/delta-updater.js
git commit -m "fix: download updates atomically with strict bounds"
```

### Task 5: Serialized Updater State And IPC

**Files:**
- Create: `electron/updater-custom.test.js`
- Modify: `electron/updater-custom.js`
- Modify: `electron/main.js`
- Modify: `electron/preload.js`
- Modify: `frontend/src/types/electron.d.ts`
- Modify: `package.json`
- Modify: `package-lock.json`

- [ ] **Step 1: Write failing state tests**

Allow injected `runtime`, `httpClient`, and `artifactDownloader` dependencies. Cover duplicate check/download serialization, manual download while auto update is disabled, stale artifact clearing, signed response validation, downgrade rejection, rollout blocking, recommended full fallback, and installer spawn failure:

```javascript
test('duplicate checks await one request', async () => {
  const gate = deferred();
  let checks = 0;
  const updater = createUpdater({ post: async () => { checks += 1; return gate.promise; } });
  const first = updater.checkForUpdates({ download: false });
  const second = updater.checkForUpdates({ download: false });
  gate.resolve(signedNoUpdateEnvelope());
  assert.deepEqual(await first, await second);
  assert.equal(checks, 1);
});

test('manual check downloads when automatic updates are off', async () => {
  const updater = createUpdater({ autoDownload: false });
  await updater.checkForUpdates({ download: true });
  assert.equal(updater.getState().phase, 'ready');
});

test('no-update clears stale install artifact', async () => {
  const updater = createUpdater({ checkEnvelope: signedNoUpdateEnvelope() });
  updater.downloadedArtifact = { version: '1.0.6', path: 'old.exe', hash: 'a'.repeat(128) };
  await updater.checkForUpdates({ download: false });
  assert.equal(updater.downloadedArtifact, null);
});
```

- [ ] **Step 2: Run RED**

Run: `node --test electron/updater-custom.test.js`

Expected: FAIL because the current class lacks injectable serialized state.

- [ ] **Step 3: Add standard version handling and state**

Run `npm install semver@latest --save`. Validate with `semver.valid` and require `semver.gt(latest, current)`.

```javascript
this.state = { phase: 'idle', error: null, progress: null };
this.checkPromise = null;
this.downloadPromise = null;
this.downloadedArtifact = null;

async checkForUpdates({ download = this.autoDownload } = {}) {
  if (this.checkPromise) return this.checkPromise;
  this.checkPromise = this._checkForUpdates({ download })
    .finally(() => { this.checkPromise = null; });
  return this.checkPromise;
}
```

`_checkForUpdates` verifies the signed envelope, validates platform/arch/channel/newer version, clears stale state, and optionally awaits one shared download. Use delta only when available and `recommended_update_type === 'delta'`.

- [ ] **Step 4: Fix IPC semantics and insecure defaults**

Manual IPC uses:

```javascript
const data = await updater.checkForUpdates({ download: true });
return { success: true, data, state: updater.getState() };
```

Automatic checks run only when enabled. Delete the hardcoded public HTTP fallback and initialize via `resolveUpdateConfig`. Return `server-unavailable` separately from `up-to-date`.

For Windows install, await the child `spawn` event and handle `error`. A spawn error preserves the verified artifact and `ready` phase.

- [ ] **Step 5: Verify GREEN and commit**

```powershell
node --test electron/update-config.test.js electron/update-signature.test.js electron/update-downloader.test.js electron/updater-custom.test.js
node --check electron/main.js
node --check electron/preload.js
git add electron/updater-custom.js electron/updater-custom.test.js electron/main.js electron/preload.js frontend/src/types/electron.d.ts package.json package-lock.json
git commit -m "fix: serialize desktop update state and IPC"
```

### Task 6: Delta Release Eligibility

**Files:**
- Modify: `scripts/release/archive-release.js`
- Modify: `scripts/release/generate-delta.js`
- Modify: `scripts/release/build-and-generate-delta.js`
- Modify: `backend/src/core/delta_generator.py`
- Create: `backend/tests/test_core/test_delta_generator.py`

- [ ] **Step 1: Write failing exact-inventory tests**

```python
def test_backend_and_frontend_changes_are_delta_eligible(tmp_path):
    source, target = make_release_pair(tmp_path)
    write(target / "resources/backend/app.bin", b"new backend")
    write(target / "resources/app/frontend/dist/index.html", b"new frontend")
    result = DeltaGenerator(tmp_path / "deltas").analyze_release(
        "1.0.5", "1.0.6", source, target, "win32", "x64"
    )
    assert result.requires_full_update is False
    assert {change.path for change in result.changes} == {
        "backend/app.bin", "frontend/dist/index.html"
    }

def test_electron_change_requires_full_update(tmp_path):
    source, target = make_release_pair(tmp_path)
    write(target / "resources/app/electron/main.js", b"new main")
    result = DeltaGenerator(tmp_path / "deltas").analyze_release(
        "1.0.5", "1.0.6", source, target, "win32", "x64"
    )
    assert result.requires_full_update is True
    assert result.unsupported_changes == ["app/electron/main.js"]
```

- [ ] **Step 2: Run RED**

Run from `backend/`: `python -m pytest tests/test_core/test_delta_generator.py -q`

Expected: FAIL because exact packaged snapshots and `analyze_release` do not exist.

- [ ] **Step 3: Archive exact unpacked application**

Copy the installer plus `dist-output/win-unpacked/resources/app` and `resources/backend`. Write version/platform/arch and SHA-256 inventory for every packaged file. Reject version or architecture mismatch.

- [ ] **Step 4: Implement full-only rules**

Hash all packaged files. Only `resources/backend/**` and `resources/app/frontend/dist/**` may become delta entries. Ignore only the expected version-field change in `resources/app/package.json` after comparing all other fields. Any other change sets `requires_full_update=True`; the CLI exits 2 and creates no partial delta.

- [ ] **Step 5: Handle exit 2 in Node release commands**

Treat exit 2 as a successful full-only decision, print unsupported paths, and never claim or upload a delta artifact.

- [ ] **Step 6: Verify GREEN and commit**

```powershell
python -m pytest tests/test_core/test_delta_generator.py -q
node --check scripts/release/archive-release.js
node --check scripts/release/generate-delta.js
git add scripts/release/archive-release.js scripts/release/generate-delta.js scripts/release/build-and-generate-delta.js backend/src/core/delta_generator.py backend/tests/test_core/test_delta_generator.py
git commit -m "fix: require full updates for unsupported package changes"
```

### Task 7: Transactional Delta And Early Bootstrap

**Files:**
- Create: `electron/delta-updater.test.js`
- Create: `electron/update-bootstrap.js`
- Create: `electron/update-bootstrap.test.js`
- Modify: `electron/delta-updater.js`
- Modify: `electron/main.js`

- [ ] **Step 1: Write failing transaction tests**

Use temporary resources/download directories. Cover add/replace/delete, missing payload, target hash mismatch, delete verification, version rollback, pending-path tampering, archive hash revalidation, and failure marker:

```javascript
test('restores files and package version after failed verification', async () => {
  const fixture = createDeltaFixture({
    sourceVersion: '1.0.5',
    targetVersion: '1.0.6',
    corruptTargetAfterCopy: true
  });
  const result = await fixture.updater.applyPendingUpdate({ resourcesPath: fixture.resourcesPath });
  assert.equal(result.status, 'rolled-back');
  assert.equal(readPackageVersion(fixture.resourcesPath), '1.0.5');
  assert.equal(fs.readFileSync(fixture.originalPath, 'utf8'), 'old content');
  assert.match(JSON.parse(fs.readFileSync(fixture.failureMarker)).error, /verification/);
});

test('derives target from current resources path', async () => {
  const fixture = createDeltaFixture({ persistedTargetDir: 'C:\\Windows' });
  await fixture.updater.applyPendingUpdate({ resourcesPath: fixture.resourcesPath });
  assert.equal(fs.existsSync(path.join(fixture.resourcesPath, 'backend', 'new.bin')), true);
});
```

- [ ] **Step 2: Run RED**

Run: `node --test electron/delta-updater.test.js`

Expected: FAIL because pending state trusts absolute paths and version rollback is incomplete.

- [ ] **Step 3: Persist only authenticated pending identity**

```javascript
{
  deltaFileName,
  signedEnvelope,
  createdAt
}
```

At apply time, derive all paths under the current update directory and `process.resourcesPath`. Reverify the envelope and archive hash, extract fresh, then read the manifest from the verified archive. Validate source/target/platform/arch and normalized relative paths.

- [ ] **Step 4: Implement exact transaction and rollback**

Back up every replace/delete target plus `resources/app/package.json`. Missing add/replace payload is an error. Verify additions/replacements exist and match, deletions do not exist, and packaged version equals target. Rollback removes additions, restores every backup/version, cleans pending data, writes `update_apply_failure.json`, and returns:

```javascript
{ status: 'none' | 'applied' | 'rolled-back', error: optionalMessage }
```

- [ ] **Step 5: Write bootstrap tests and run RED**

Test that `applied` calls `app.relaunch()` and `app.quit()` before `createWindow`; `none` continues; `rolled-back` continues and exposes failure.

Run: `node --test electron/update-bootstrap.test.js`

Expected: FAIL because the bootstrap module does not exist.

- [ ] **Step 6: Implement early bootstrap**

```javascript
async function runUpdateBootstrap({ updater, app, resourcesPath }) {
  const result = await updater.deltaUpdater.applyPendingUpdate({ resourcesPath });
  if (result.status === 'applied') {
    app.relaunch();
    app.quit();
    return { shouldContinue: false, result };
  }
  return { shouldContinue: true, result };
}
```

In `app.whenReady()`, initialize without scheduling a check, run bootstrap, and stop when it relaunches. Only afterward create window/tray/backend, deliver rollback error, and schedule checking.

- [ ] **Step 7: Verify GREEN and commit**

```powershell
node --test electron/delta-updater.test.js electron/update-bootstrap.test.js
node --check electron/delta-updater.js
node --check electron/update-bootstrap.js
node --check electron/main.js
git add electron/delta-updater.js electron/delta-updater.test.js electron/update-bootstrap.js electron/update-bootstrap.test.js electron/main.js
git commit -m "fix: apply delta updates transactionally before startup"
```

### Task 8: Renderer Update Feedback

**Files:**
- Create: `frontend/src/utils/appUpdate.ts`
- Create: `frontend/src/__tests__/utils/appUpdate.test.ts`
- Create: `frontend/src/__tests__/components/CustomUpdateNotification.test.tsx`
- Modify: `frontend/src/components/SettingsPanel.tsx`
- Modify: `frontend/src/components/CustomUpdateNotification.tsx`
- Modify: `frontend/src/types/electron.d.ts`

- [ ] **Step 1: Write failing manual-flow tests**

```typescript
type ManualUpdateResult =
  | { status: 'up-to-date' }
  | { status: 'server-unavailable'; message: string }
  | { status: 'rollout-blocked'; message: string }
  | { status: 'downloading'; version: string }
  | { status: 'ready'; version: string; updateType: 'full' | 'delta' }
  | { status: 'error'; message: string };
```

Test every result from mocked `checkForUpdates`, including manual preparation when auto updates are disabled.

- [ ] **Step 2: Run utility RED**

Run from `frontend/`: `npm run test:run -- src/__tests__/utils/appUpdate.test.ts`

Expected: FAIL because `appUpdate.ts` does not exist.

- [ ] **Step 3: Implement utility and settings mapping**

`checkAndPrepareAppUpdate(electronApi)` validates `success`, `data`, and `state`, returning one discriminated result without UI side effects. Map results to truthful Chinese toasts: unavailable is never up-to-date; rollout shows server message; downloading says background preparation; ready offers restart; errors preserve the main message.

- [ ] **Step 4: Write notification tests and run RED**

Mock `window.electron.on/off/installUpdate`. Verify full/delta ready summary, optional dismissal, mandatory non-dismissal, listener cleanup, successful install, and failure restoration.

Run: `npm run test:run -- src/__tests__/components/CustomUpdateNotification.test.tsx`

Expected: FAIL because resolved `{ success: false }` is ignored.

- [ ] **Step 5: Implement install response handling**

```typescript
const result = await window.electron.installUpdate();
if (!result.success) {
  setShowCard(true);
  toast.error('重启更新失败', { description: result.error || '未知错误' });
  return;
}
setShowCard(false);
```

Use the existing Lucide `X` icon instead of the hand-drawn close SVG; preserve accessible labeling and existing compact layout.

- [ ] **Step 6: Verify GREEN/build and commit**

```powershell
npm run test:run -- src/__tests__/utils/appUpdate.test.ts src/__tests__/components/CustomUpdateNotification.test.tsx
npm run build
git add src/utils/appUpdate.ts src/__tests__/utils/appUpdate.test.ts src/__tests__/components/CustomUpdateNotification.test.tsx src/components/SettingsPanel.tsx src/components/CustomUpdateNotification.tsx src/types/electron.d.ts
git commit -m "fix: complete manual update feedback flow"
```

Run and commit from `frontend/`.

### Task 9: Dependencies, Docs, And Packaged Smoke Test

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `scripts/docs/README-DELTA-UPDATE.md`

- [ ] **Step 1: Upgrade audited HTTP dependencies**

```powershell
npm install axios@^1.15.3 --save
npm pkg set overrides.follow-redirects="^1.15.12" overrides.form-data="^4.0.6"
npm install
```

These are the first non-vulnerable ranges for the discovered advisories; do not introduce an unreviewed major upgrade.

- [ ] **Step 2: Run focused suites**

```powershell
node --test electron/update-config.test.js electron/update-signature.test.js electron/update-downloader.test.js electron/updater-custom.test.js electron/delta-updater.test.js electron/update-bootstrap.test.js scripts/build/build-electron.test.js scripts/release/release-arch.test.js
cd backend
python -m pytest tests/test_core/test_delta_generator.py -q
```

Expected: zero failures.

- [ ] **Step 3: Run full existing regression checks**

```powershell
node --test electron/*.test.js
cd frontend
npm run test:run
npm run build
```

Expected: Electron and frontend tests pass; production build exits 0.

- [ ] **Step 4: Run syntax, audit, and diff checks**

```powershell
node --check electron/main.js
node --check electron/preload.js
node --check electron/updater-custom.js
node --check electron/delta-updater.js
node --check scripts/release/archive-release.js
node --check scripts/release/generate-delta.js
npm audit --omit=dev
git diff --check
```

Expected: syntax exits 0, zero production vulnerabilities, clean diff check.

- [ ] **Step 5: Document operator workflow**

Document key generation/custody, production and staging build variables, HTTPS 443 reverse proxy, architecture validation, signed full upload, full-only delta reasons, old-client full rollout, and the external service requirement to preserve signed envelopes.

- [ ] **Step 6: Build and inspect unpacked package**

```powershell
$env:VIDFLOW_UPDATE_SERVER_URL = 'https://updates.shcrystal.top'
$env:VIDFLOW_UPDATE_CHANNEL = 'stable'
$env:VIDFLOW_UPDATE_PUBLIC_KEY_FILE = "$PWD\.update-keys\vidflow-update-public.pem"
$env:VIDFLOW_RELEASE_ARCH = 'x64'
node scripts/build/build-electron.js --win --arch=x64 --dir
```

Inspect `dist-output/win-unpacked/resources/app/package.json`. It must contain URL/channel/x64/public key and no private key or private-key path.

- [ ] **Step 7: Record external verification limit**

Attempt the signed endpoint only when deployed. If `https://updates.shcrystal.top/api/v1/updates/check` is unavailable or unsigned, report that exact infrastructure blocker and do not claim online end-to-end success.

- [ ] **Step 8: Commit**

```powershell
git add package.json package-lock.json scripts/docs/README-DELTA-UPDATE.md
git commit -m "chore: secure updater dependencies and release docs"
```

## Final Acceptance Checklist

- [ ] Production build requires HTTPS endpoint, channel, architecture, and public key.
- [ ] Packaged output contains public configuration only.
- [ ] Windows release identity is `x64`.
- [ ] Signed metadata passes and signed-field tampering fails.
- [ ] Manual checking prepares an update when automatic updates are off.
- [ ] Duplicate checks/downloads share one operation.
- [ ] Full downloads are bounded, atomic, verified, and retryable.
- [ ] Unsupported packaged changes force a full update.
- [ ] Eligible deltas apply before UI startup and roll back version/files transactionally.
- [ ] Install spawn errors preserve the ready update and remain visible.
- [ ] Focused/full tests pass, frontend builds, and dependency audit is clean.
- [ ] Online success is claimed only after the external signed HTTPS endpoint works.
