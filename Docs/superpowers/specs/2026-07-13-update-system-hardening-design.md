# Update System Hardening Design

## Goal

Make VidFlow desktop updates deployable, configurable, secure, and recoverable. Production installers must receive their update endpoint and channel at build time, while development and managed deployments may override those values explicitly. The client must reject untrusted update data, avoid stale or concurrent update state, and recover cleanly from failed delta application.

The first release containing this work will be published as a full installer. Existing clients cannot receive changes to the updater itself through the current delta format.

## Current Findings

- The configured public endpoint on port 8321 is not reachable as a valid update API from the current network, while the domain's HTTPS port 443 serves an unrelated application.
- The release scripts infer `x32` from the NSIS bootstrap executable even though the packaged `VidFlow.exe` and Electron build target are `x64`.
- A build-time `VIDFLOW_UPDATE_SERVER_URL` value is currently read only at application runtime and is not embedded in the installer.
- Public HTTP is accepted when the configured URL starts with `http://`. A hash delivered over the same untrusted connection does not authenticate an update.
- The delta snapshot contains backend and frontend files only, so Electron main-process, preload, dependency, and packaging changes are silently omitted.
- Delta rollback does not restore the packaged `package.json` version after a failed verification.
- The renderer window can start loading resources before a pending delta is applied.
- Manual checks do not force a download when automatic updates are disabled, and failed install IPC results can be hidden by the UI.
- Update downloads are not serialized, are written directly to their final path, and do not consistently enforce timeout, size, or redirect limits.
- The updater and release architecture logic have no focused automated tests.

## Considered Approaches

### 1. Build-time defaults with secure runtime overrides (recommended)

Embed a production update configuration in every packaged application. Allow explicit environment or managed-config overrides for development and enterprise deployment, but verify all update metadata and artifacts with the same embedded public key.

This supports different production, staging, and beta installers without source edits. A stable HTTPS hostname lets infrastructure move behind DNS or a reverse proxy without rebuilding clients.

### 2. Source-controlled endpoint only

Store the endpoint directly in `electron-builder.json` or `updater-custom.js`. This is simple, but every environment switch requires a source change and makes accidental production/staging crossover likely.

### 3. Runtime-only endpoint configuration

Read the endpoint only from the user configuration or environment. This is flexible, but a missing or modified local setting can disable updates or redirect the client. It also leaves every installation dependent on external provisioning.

## Packaged Configuration

Add a small build wrapper that reads and validates these inputs:

- `VIDFLOW_UPDATE_SERVER_URL`, required for production and required to use public HTTPS;
- `VIDFLOW_UPDATE_CHANNEL`, defaulting to `stable` and limited to `stable`, `beta`, or `nightly`;
- `VIDFLOW_UPDATE_PUBLIC_KEY_FILE`, required for production signature verification;
- `VIDFLOW_RELEASE_ARCH`, taken from the requested Electron build target.

The wrapper passes non-secret values into Electron Builder as packaged metadata. The private signing key is never included in the application or printed by build scripts.

Runtime resolution order is:

1. an explicit environment or managed deployment override;
2. the packaged configuration;
3. no updater initialization, with a clear diagnostic error.

The existing persisted `advanced.update_server_url` value may be honored only when the deployment also sets `VIDFLOW_ALLOW_UPDATE_SERVER_OVERRIDE=1`. It must pass the same URL policy, and changing the endpoint never changes the trusted public key.

Public endpoints must use HTTPS. Plain HTTP is accepted only for loopback or private-network development targets. Production redirects are disabled unless every redirect target is validated against a packaged host allowlist.

Example production build:

```powershell
$env:VIDFLOW_UPDATE_SERVER_URL = "https://updates.shcrystal.top"
$env:VIDFLOW_UPDATE_CHANNEL = "stable"
$env:VIDFLOW_UPDATE_PUBLIC_KEY_FILE = "C:\secure\vidflow-update-public.pem"
$env:VIDFLOW_RELEASE_ARCH = "x64"
npm run build:win
```

## Release Identity And Architecture

The release architecture comes from `VIDFLOW_RELEASE_ARCH` or the Electron Builder target, never from the NSIS installer stub. Before upload, the release script verifies that `dist-output/win-unpacked/VidFlow.exe` has the expected PE architecture and fails on mismatch.

The full installer metadata and delta metadata use the same authoritative values for version, platform, architecture, and channel. Upload scripts must refuse missing artifacts, mismatched versions, ambiguous architectures, or unsigned production metadata.

## Trust Model

HTTPS protects transport, while an embedded Ed25519 public key authenticates release metadata. The release pipeline signs a canonical payload containing at least:

- version, platform, architecture, and channel;
- mandatory and rollout flags;
- file name, size, SHA-512 hash, and download URL;
- delta source version, target version, size, hash, and URL when present.

The client verifies the metadata signature before trusting any URL or hash. It then verifies the downloaded artifact against the authenticated hash before marking the update ready. Windows Authenticode signing remains recommended as an additional operating-system trust layer.

Signature failure, unsupported algorithms, unsigned production metadata, HTTPS downgrade, cross-host redirects, and version downgrade all fail closed. Development mode may use an explicitly enabled unsigned localhost server.

## Client Update State Machine

Use one explicit state machine:

`idle -> checking -> available -> downloading -> ready -> installing`

Any active phase may transition to `error`; a fresh manual check resets recoverable error state. The updater stores the version and verified hash associated with a downloaded file so a later check cannot install stale content.

Only one check and one download promise may exist at a time. Duplicate callers await the existing promise. A manual check requests download even when automatic updates are disabled. Automatic checks respect the saved preference.

The IPC response exposes structured state and errors. The settings page reports `up-to-date`, `server-unavailable`, `rollout-blocked`, `downloading`, and `ready` separately. The update notification keeps the card visible when install IPC returns `{ success: false }`.

## Download Reliability

Full and delta downloads share a hardened downloader that:

- validates metadata types, positive sizes, supported hash formats, and semantic version ordering;
- uses connection and total-operation timeouts;
- rejects a transfer that exceeds the declared size and requires the final size to match exactly;
- writes to a version-specific `.part` file using stream `pipeline`;
- verifies hash and signature before atomically renaming to the final path;
- removes invalid partial files after failure;
- validates or disables redirects;
- emits bounded progress values and a stable update type.

Reporting failures never change the update outcome. Telemetry is best effort and cannot block installation or leave the state machine stuck.

## Delta Update Policy

Delta updates remain available only for backend and frontend resource changes. The release snapshot records all packaged file groups. If Electron main-process files, preload files, root package metadata other than the version, Node dependencies, icons, native modules, or packaging configuration differ, the generator marks the release full-only instead of silently omitting changes.

Pending delta state contains trusted relative paths and signed metadata, not a persisted arbitrary target directory. At startup the client derives the target from the current `process.resourcesPath`, revalidates the signed manifest and delta hash, and applies the update before creating the renderer window, tray, or backend process.

The delta transaction backs up every replaced or deleted file plus the packaged version file. It verifies additions, replacements, and deletions. On any failure it restores all backups, removes newly added files, restores the old version, records a user-visible failure marker, and continues with the old application.

After a successful delta transaction, the bootstrap process relaunches once before showing the application. The second process reads the new package version and resources, avoiding mixed old/new runtime state.

## Server And Infrastructure Contract

`https://updates.shcrystal.top` is the stable client-facing hostname. It terminates TLS on standard port 443 and forwards to the update service. The origin host and port may change without a client rebuild.

The check endpoint returns a signed response envelope. Download URLs use the same host unless an allowlisted CDN host is packaged. Channels are request and response fields rather than separate hardcoded endpoints.

Deploying DNS, TLS certificates, reverse-proxy rules, update-service persistence, and private signing-key custody is an infrastructure prerequisite. The repository changes provide client validation and release signing hooks but do not publish external DNS or server configuration automatically.

## Error Handling

- Missing packaged production configuration: disable the updater and show a configuration error.
- Network timeout or 5xx response: report `server-unavailable`, not `up-to-date`.
- Invalid or unsigned metadata: report a security validation error and do not download.
- Architecture or channel mismatch: reject the release and log expected and received values.
- Download interruption or size/hash mismatch: delete the partial artifact and allow retry.
- Installer spawn failure: keep the application open, preserve the ready update, and show the error.
- Delta preparation or application failure: roll back transactionally, preserve the old version, and offer a full-update retry when metadata is available.

## Tests

Node tests will cover:

- packaged configuration precedence and HTTPS policy;
- authoritative architecture detection and rejection of the 32-bit NSIS stub result;
- metadata signature validation and downgrade rejection;
- duplicate check/download serialization;
- partial download cleanup, size limits, hash failures, and redirect rejection;
- stale downloaded-file prevention and install spawn failures;
- delta manifest path validation, full-only eligibility, startup ordering, version rollback, and successful relaunch.

Frontend Vitest tests will cover manual checking with automatic updates disabled, unavailable and rollout states, ready notifications, and failed install responses.

Release-script tests will validate build metadata, architecture, channel, signature generation, and the full-only decision for unsupported delta changes. Verification ends with focused tests, the full frontend test suite, the frontend production build, Electron syntax checks, dependency audit, and a packaged-config smoke check. A real external update is reported as verified only when the HTTPS service and signed test release are available.

## Rollout

1. Ship this updater as a full installer and publish it under the corrected `x64` release identity.
2. Keep delta disabled for clients older than this hardened updater.
3. Validate a signed full update from the previous public version.
4. Validate a backend/frontend-only signed delta between two hardened versions.
5. Enable delta recommendations only after the rollback and two-restart bootstrap smoke tests pass.

## Out Of Scope

- Purchasing or provisioning a Windows code-signing certificate.
- Automatically changing public DNS, Cloudflare, firewall, or reverse-proxy configuration.
- Allowing ordinary users to select arbitrary update servers in the UI.
- Delta patching Electron binaries, Node native modules, or installer configuration.
- Replacing the external update-service administration application.
