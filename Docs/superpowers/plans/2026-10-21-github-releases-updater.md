# GitHub Releases updater implementation plan

**Goal:** Retain the desktop updater and use signed GitHub Release assets for Windows and native Intel/Apple Silicon macOS builds.

**Architecture:** A GitHub source selects the latest stable release and the device-specific signed metadata asset. The existing core continues to verify version, size, SHA-512, resumable downloads and installation. GitHub transport permits HTTPS redirects only to explicit GitHub asset hosts. No update statistics are sent to the former server.

**Tech stack:** Electron, Node test runner, Ed25519, axios, electron-builder, GitHub Actions.

## Tasks

- [x] Add failing tests for latest-release selection, signed platform metadata, no-update cases, changed/mismatched assets and redirect validation. Run `node --test electron/updater-github.test.js` and confirm failures before implementation.
- [x] Implement `electron/updater-github.js`, integrate through `electron/updater-core.js` and `electron/updater-custom.js`, and switch `electron/main.js` to GitHub with automatic checks but user-initiated download. Run updater tests.
- [x] Add a tested `scripts/release/generate-github-metadata.js` CLI accepting installer, platform, architecture and notes file; require an Ed25519 private key matching the bundled public key. Generate schema v1 envelopes and exact GitHub asset URLs.
- [x] Reuse the existing native macOS workflow for both architectures, retain its signing and smoke checks, and upload signed metadata when called by release workflow. Modernize Windows release build with the required backend virtual environment. Publish all installers and metadata together as a GitHub Release.
- [x] Update settings copy and release instructions, test update notifications, run frontend build and all Electron tests, inspect workflow YAML and diffs. Record limitations of local Windows validation and external signing configuration.
- [x] Validate actual macOS artifacts on both native runners. If first-run tool smoke checks fail because an upstream download source is down, retain the smoke checks, reproduce the download failure in a local test, add a platform-specific verified-HTTPS fallback, and rebuild.

## Accepted behavior

Version comes from package.json (currently 1.0.7); no arbitrary version bump. Stable releases only; drafts/prereleases are excluded. Failed checks are shown as failures, not as up-to-date. Missing platform metadata is reported as unavailable. Mac DMG updates open the verified disk image and require copying the app into Applications. Windows starts the verified NSIS installer and exits. Metadata signing private keys remain outside the repository.

## Verification evidence

- Electron and signed metadata tests: 18 passed.
- Update notification UI tests: 6 passed.
- Release artifact audit tests: 3 passed.
- macOS archive, fallback and TLS tests: 13 passed.
- Frontend TypeScript/Vite production build passed.
- Workflow YAML, packaging consistency and PowerShell syntax checks passed.
- Repository signing secret configured and matched to the bundled public key; production-key metadata CLI smoke test passed.
- Independent code review found no remaining blocking issues.
- macOS arm64 and Intel x64 native builds, codesign verification, first-run backend/tool installation and tool execution smoke tests all passed: https://github.com/ShuaiJustin/VidFlow/actions/runs/39625317779 (source commit 99ff30c0140a0a5ef4755034fc69c21a4060939b).
