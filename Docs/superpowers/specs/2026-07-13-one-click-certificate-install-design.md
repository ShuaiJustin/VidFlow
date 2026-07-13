# One-Click Certificate Installation Design

## Goal

Allow a Windows user to complete the VidFlow mitmproxy certificate setup with one click. The flow must generate missing certificate files, install the system root certificate, import the WeChat-compatible P12 into the current user's personal certificate store, and return the verified final state.

Windows may still display a UAC consent prompt while the root certificate is installed. VidFlow must not claim success until both certificate stores have been checked after installation.

## Considered Approaches

### 1. Backend orchestration endpoint (recommended)

Add one backend endpoint that performs the existing generation, root installation, P12 import, and final verification steps. The frontend sends one request and renders the structured result.

This keeps the state machine and partial-failure handling next to `CertInstaller`, and prevents the frontend from coordinating a long-running UAC operation across multiple requests.

### 2. Frontend orchestration of existing endpoints

The frontend could call generate, install-root, and install-wechat-p12 sequentially. This has a smaller backend diff, but duplicates installation policy in the UI and makes partial success, retries, and UAC cancellation harder to report consistently.

### 3. Automatic installation while starting the sniffer

The sniffer start endpoint could install missing certificates without a separate user action. This removes a visible setup step, but changes the Windows trust store as a side effect of an unrelated command. It is rejected because certificate installation should remain an explicit user action.

## Backend Design

Add `POST /api/channels/certificate/install-all`.

The endpoint runs these idempotent steps in order:

1. Call `ensure_cert_exists()`.
2. Call `install_cert()` when the system root certificate is not already installed. This may wait for UAC consent.
3. Call `install_wechat_p12()` when the current user does not already have the mitmproxy certificate and private key.
4. Call `get_cert_info()` and derive success from the verified final state.

The response extends the existing certificate installation response shape with per-step state:

```json
{
  "success": true,
  "message": "证书已自动安装，请完全退出并重新打开微信",
  "root_installed": true,
  "wechat_p12_installed": true,
  "failed_step": null
}
```

`failed_step` is one of `generate`, `root`, or `wechat_p12`. A failed root installation stops the flow before P12 import because the setup would remain unusable. A failed P12 import preserves the successfully installed root certificate and reports that only the P12 step needs retrying.

The existing individual endpoints remain available for compatibility and troubleshooting.

## Frontend Design

Add a `channels_install_all_certificates` bridge command and an `installAllCertificates` hook method.

In the certificate management dialog:

- Add one visually primary button labeled `自动安装证书`.
- The button is available whenever either verified certificate state is incomplete.
- While the request is running, disable all certificate actions and show `自动安装中...`.
- After success, refresh certificate information and show the existing installed status for both stores.
- Keep the separate root and P12 buttons as secondary troubleshooting actions.
- Keep certificate download actions unchanged.

After full success, the message must tell the user to completely exit WeChat, including background processes, reopen it, and replay the target video. VidFlow will not terminate WeChat automatically.

## Error Handling

- Certificate generation failure: report the existing `last_error` and `failed_step: generate`.
- UAC rejection, timeout, or root verification failure: report `failed_step: root` and do not import P12.
- P12 import or private-key verification failure: report `failed_step: wechat_p12` while preserving `root_installed: true`.
- Already-complete setup: return success without modifying either store.
- The frontend displays the backend message without replacing it with a generic error.

## Tests

Backend API tests will cover:

- successful generation and installation of both certificate types;
- skipping already-complete steps;
- stopping after a root installation failure;
- preserving and reporting partial success when P12 import fails.

Frontend tests will cover the hook/bridge contract and the dialog's single-click action, loading state, and error display using the repository's existing Vitest setup. The focused backend tests and focused frontend tests run first, followed by the frontend production build.

## Out of Scope

- Silently bypassing Windows UAC.
- Automatically restarting or terminating WeChat.
- Removing the existing manual certificate endpoints or download options.
- Changing mitmproxy certificate generation algorithms or certificate-store detection rules.
