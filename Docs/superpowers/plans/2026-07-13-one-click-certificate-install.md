# One-Click Certificate Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one explicit user action that generates and installs both VidFlow certificate formats, verifies both Windows stores, and reports actionable partial failures.

**Architecture:** A new FastAPI orchestration endpoint owns the ordered, idempotent installation workflow. The Electron REST bridge and React hook expose one typed command, while `CertificateDialog` renders one primary action and retains the existing individual controls for troubleshooting.

**Tech Stack:** Python 3, FastAPI, pytest, React 18, TypeScript, Vitest, Testing Library

---

### Task 1: Backend orchestration endpoint

**Files:**
- Modify: `backend/tests/test_channels/test_api_channels.py`
- Modify: `backend/src/api/channels.py`

- [ ] **Step 1: Write failing endpoint tests**

Add async tests that patch `get_cert_installer()` with a `MagicMock` and call the endpoint function directly. Cover successful installation, already-complete idempotency, root failure short-circuiting, and P12 partial failure. The success assertion is:

```python
result = await channels.install_all_certificates()

assert result == {
    "success": True,
    "message": "证书已自动安装，请完全退出并重新打开微信，然后重新播放目标视频。",
    "root_installed": True,
    "wechat_p12_installed": True,
    "failed_step": None,
}
installer.install_cert.assert_called_once_with()
installer.install_wechat_p12.assert_called_once_with()
```

The failure tests assert `failed_step == "root"` or `failed_step == "wechat_p12"`, and assert that P12 import is not called after root installation fails.

- [ ] **Step 2: Run the tests and verify RED**

Run from `backend/`:

```powershell
python -m pytest tests/test_channels/test_api_channels.py -k install_all_certificates -v
```

Expected: FAIL because `install_all_certificates` does not exist.

- [ ] **Step 3: Implement the minimal endpoint**

Add `POST /certificate/install-all` next to the existing certificate endpoints. Use `asyncio.to_thread()` for every blocking installer operation. The endpoint must:

```python
installer = get_cert_installer()
if not await asyncio.to_thread(installer.ensure_cert_exists):
    return {
        "success": False,
        "message": getattr(installer, "last_error", None) or "证书生成失败",
        "root_installed": False,
        "wechat_p12_installed": False,
        "failed_step": "generate",
    }

if not await asyncio.to_thread(installer.is_cert_installed):
    if not await asyncio.to_thread(installer.install_cert):
        info = await asyncio.to_thread(installer.get_cert_info)
        return {
            "success": False,
            "message": getattr(installer, "last_error", None) or "系统根证书安装失败，请在 UAC 窗口中点击「是」授权",
            "root_installed": bool(info.get("cert_installed")),
            "wechat_p12_installed": bool(info.get("wechat_p12_installed")),
            "failed_step": "root",
        }

if not await asyncio.to_thread(installer.is_wechat_p12_installed):
    if not await asyncio.to_thread(installer.install_wechat_p12):
        info = await asyncio.to_thread(installer.get_cert_info)
        return {
            "success": False,
            "message": "系统根证书已安装，但微信兼容 P12 导入失败，请重试 P12 导入。",
            "root_installed": bool(info.get("cert_installed")),
            "wechat_p12_installed": bool(info.get("wechat_p12_installed")),
            "failed_step": "wechat_p12",
        }

info = await asyncio.to_thread(installer.get_cert_info)
root_installed = bool(info.get("cert_installed"))
wechat_p12_installed = bool(info.get("wechat_p12_installed"))
success = root_installed and wechat_p12_installed
failed_step = None if success else ("root" if not root_installed else "wechat_p12")
return {
    "success": success,
    "message": (
        "证书已自动安装，请完全退出并重新打开微信，然后重新播放目标视频。"
        if success
        else "证书安装命令已完成，但证书状态验证失败，请重试。"
    ),
    "root_installed": root_installed,
    "wechat_p12_installed": wechat_p12_installed,
    "failed_step": failed_step,
}
```

Return only verified `root_installed` and `wechat_p12_installed` state. Do not expose exception text in the HTTP error response.

- [ ] **Step 4: Run the focused backend tests and verify GREEN**

```powershell
python -m pytest tests/test_channels/test_api_channels.py -k "install_all_certificates or CertificateWarning" -v
```

Expected: all selected tests PASS.

### Task 2: Typed frontend bridge and hook

**Files:**
- Modify: `frontend/src/__tests__/components/TauriIntegration.test.ts`
- Modify: `frontend/src/__tests__/hooks/useChannelsSniffer.test.ts`
- Modify: `frontend/src/types/channels.ts`
- Modify: `frontend/src/components/TauriIntegration.tsx`
- Modify: `frontend/src/hooks/useChannelsSniffer.ts`

- [ ] **Step 1: Write failing bridge and hook tests**

Mock the Axios instance in `TauriIntegration.test.ts`, invoke the new command, and assert the REST path:

```typescript
const response = { success: true, message: 'ok', root_installed: true, wechat_p12_installed: true, failed_step: null };
apiMocks.post.mockResolvedValue({ data: response });

await expect(invoke('channels_install_all_certificates')).resolves.toEqual(response);
expect(apiMocks.post).toHaveBeenCalledWith('/api/channels/certificate/install-all');
```

In the hook test, return the same response for `channels_install_all_certificates`, call `result.current.installAllCertificates()`, assert the response, assert the command name, and assert that certificate information is refreshed.

- [ ] **Step 2: Run the tests and verify RED**

Run from `frontend/`:

```powershell
npm run test:run -- src/__tests__/components/TauriIntegration.test.ts src/__tests__/hooks/useChannelsSniffer.test.ts
```

Expected: FAIL because the bridge command and hook method do not exist.

- [ ] **Step 3: Implement types, bridge, and hook**

Extend the response type:

```typescript
export type CertificateInstallStep = 'generate' | 'root' | 'wechat_p12';

export interface CertInstallResponse {
  success: boolean;
  message: string;
  root_installed?: boolean;
  wechat_p12_installed?: boolean;
  failed_step?: CertificateInstallStep | null;
}
```

Map `channels_install_all_certificates` to `POST /api/channels/certificate/install-all`. Add `installAllCertificates()` to the hook, always refresh certificate information after a response so partial success is visible, preserve the backend message, and return the response.

- [ ] **Step 4: Run the focused frontend tests and verify GREEN**

```powershell
npm run test:run -- src/__tests__/components/TauriIntegration.test.ts src/__tests__/hooks/useChannelsSniffer.test.ts
```

Expected: all selected tests PASS.

### Task 3: Certificate dialog one-click interaction

**Files:**
- Create: `frontend/src/__tests__/components/channels/CertificateDialog.test.tsx`
- Modify: `frontend/src/components/channels/CertificateDialog.tsx`
- Modify: `frontend/src/components/channels/ChannelsPanel.tsx`

- [ ] **Step 1: Write failing dialog tests**

Render an open dialog with incomplete certificate state and a deferred `onInstallAll` promise. Click `自动安装证书`, assert it is called exactly once, assert `自动安装中...` is visible while pending, then resolve with:

```typescript
{
  success: true,
  message: '证书已自动安装，请完全退出并重新打开微信，然后重新播放目标视频。',
  root_installed: true,
  wechat_p12_installed: true,
  failed_step: null,
}
```

Assert that the returned message is rendered. Add a failure case that returns `success: false` and asserts that the backend message is shown in the destructive alert.

- [ ] **Step 2: Run the dialog test and verify RED**

```powershell
npm run test:run -- src/__tests__/components/channels/CertificateDialog.test.tsx
```

Expected: FAIL because `onInstallAll` and the automatic installation button do not exist.

- [ ] **Step 3: Implement the minimal dialog behavior**

Add `onInstallAll: () => Promise<CertInstallResponse>` to the dialog props. Add `handleInstallAll` using the existing `busyAction` state, clear prior status before starting, and render either a success alert or the backend error message. Render one full-width primary button before the individual controls:

```tsx
<Button
  onClick={handleInstallAll}
  disabled={busyAction !== null || certificatesComplete}
  className="w-full sm:col-span-2"
>
  {busyAction === 'install-all' ? '自动安装中...' : certificatesComplete ? '证书已安装' : '自动安装证书'}
</Button>
```

Disable generation while any installation action is active. Pass `installAllCertificates` from `ChannelsPanel` into the dialog.

- [ ] **Step 4: Run focused UI tests and verify GREEN**

```powershell
npm run test:run -- src/__tests__/components/channels/CertificateDialog.test.tsx src/__tests__/components/channels/ChannelsPanel.test.tsx
```

Expected: all selected tests PASS.

### Task 4: Cross-layer verification and security review

**Files:**
- Verify only; no planned production edits

- [ ] **Step 1: Run all certificate-related backend tests**

```powershell
python -m pytest tests/test_channels/test_cert_installer.py tests/test_channels/test_api_channels.py -k "cert or install_all" -v
```

Expected: all selected tests PASS.

- [ ] **Step 2: Run all touched frontend tests**

```powershell
npm run test:run -- src/__tests__/components/TauriIntegration.test.ts src/__tests__/hooks/useChannelsSniffer.test.ts src/__tests__/components/channels/CertificateDialog.test.tsx src/__tests__/components/channels/ChannelsPanel.test.tsx
```

Expected: all selected tests PASS.

- [ ] **Step 3: Run the frontend production build**

```powershell
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 4: Review the privileged path**

Confirm the route is POST-only, performs no shell interpolation from request input, retains explicit UAC consent, reports verified state rather than subprocess success alone, and does not return stack traces or certificate private-key material.

- [ ] **Step 5: Inspect the final diff**

```powershell
git diff --check
git diff -- backend/src/api/channels.py backend/tests/test_channels/test_api_channels.py frontend/src/types/channels.ts frontend/src/components/TauriIntegration.tsx frontend/src/hooks/useChannelsSniffer.ts frontend/src/components/channels/CertificateDialog.tsx frontend/src/components/channels/ChannelsPanel.tsx frontend/src/__tests__/components/TauriIntegration.test.ts frontend/src/__tests__/hooks/useChannelsSniffer.test.ts frontend/src/__tests__/components/channels/CertificateDialog.test.tsx
```

Expected: no whitespace errors and no unrelated edits introduced by this implementation.
