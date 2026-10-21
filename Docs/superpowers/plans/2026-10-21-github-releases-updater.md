# GitHub Releases updater implementation plan

**Goal:** Retain the desktop updater and use signed GitHub Release assets for Windows and native Intel/Apple Silicon macOS builds.

**Architecture:** A GitHub source selects the latest stable release and the device-specific signed metadata asset. The existing core continues to verify version, size, SHA-512, resumable downloads and installation. GitHub transport permits HTTPS redirects only to explicit GitHub asset hosts. No update statistics are sent to the former server.

**Tech stack:** Electron, Node test runner, Ed25519, axios, electron-builder, GitHub Actions.

## Tasks

- [ ] Add failing tests for latest-release selection, signed platform metadata, no-update cases, changed/mismatched assets and redirect validation. Run `node --test electron/updater-github.test.js` and confirm failures before implementation.
- [ ] Implement `electron/updater-github.js`, integrate through `electron/updater-core.js` and `electron/updater-custom.js`, and switch `electron/main.js` to GitHub with automatic checks but user-initiated download. Run updater tests.
- [ ] Add a tested `scripts/release/generate-github-metadata.js` CLI accepting installer, platform, architecture and notes file; require an Ed25519 private key matching the bundled public key. Generate schema v1 envelopes and exact GitHub asset URLs.
- [ ] Reuse the existing native macOS workflow for both architectures, retain its signing and smoke checks, and upload signed metadata when called by release workflow. Modernize Windows release build with the required backend virtual environment. Publish all installers and metadata together as a GitHub Release.
- [ ] Update settings copy and release instructions, test update notifications, run frontend build and all Electron tests, inspect workflow YAML and diffs. Record limitations of local Windows validation and external signing configuration.

## Accepted behavior

Version comes from package.json (currently 1.0.7); no arbitrary version bump. Stable releases only; drafts/prereleases are excluded. Failed checks are shown as failures, not as up-to-date. Missing platform metadata is reported as unavailable. Mac DMG updates open the verified disk image and require copying the app into Applications. Windows starts the verified NSIS installer and exits. Metadata signing private keys remain outside the repository.
