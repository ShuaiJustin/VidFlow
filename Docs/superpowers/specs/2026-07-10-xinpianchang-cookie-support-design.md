# 新片场 Cookie 支持设计

## 目标

让 VidFlow 能够为 `xinpianchang.com` 下载任务保存、读取并传递用户自己的登录 Cookie，以便访问用户在浏览器中已获授权的内容。

## 方案

将新片场加入现有 Cookie 平台注册表，平台标识为 `xinpianchang`，Cookie 文件为 `xinpianchang_cookies.txt`。Cookie 管理界面沿用现有手动 Netscape 格式粘贴与本机浏览器读取流程；浏览器读取范围限定为 `xinpianchang.com`。

下载器工厂与通用下载器将 `xinpianchang.com` 识别为 `xinpianchang`。智能下载流程检测到该 Cookie 文件后，会优先使用带 Cookie 的通用下载器。通用下载器为新片场请求设置当前视频页面作为 Referer，并将保存的 Cookie 文件传给 yt-dlp。

## 错误处理

未配置 Cookie 且站点返回认证错误时，现有友好提示会明确显示“新片场”并给出正确的 Cookie 文件名。登录 Cookie 失效、会员限制、DRM 或站点提取器变更仍应保留原始失败原因，不承诺绕过这些访问控制。

## 验证

增加单元测试覆盖新片场 URL 识别、Cookie 文件映射和浏览器 Cookie 域名映射。使用用户提供链接验证无 Cookie 请求仍能复现 403；不在自动化测试中使用或保存用户 Cookie。登录后的端到端验证由用户在本机完成。
