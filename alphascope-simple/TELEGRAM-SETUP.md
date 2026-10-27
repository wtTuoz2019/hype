# Telegram 登录接入说明

目前每个页面顶部都有「Telegram 登录」入口。默认是**演示登录**：只存储浏览器本地昵称，不会证明用户拥有 Telegram 账号，也不与会员状态绑定。

## 正式接入步骤
1. 在 Telegram 官方 BotFather 创建机器人，并按 Telegram 官方文档配置登录所需域名（HTTPS）。
2. 将 `telegram-config.js` 中的 `window.ALPHASCOPE_TELEGRAM` 配置启用，并在每个 HTML 页面中 `telegram-auth.js` 之前添加 `<script src="telegram-config.js"></script>`。
3. 在服务器实现 `GET /auth/telegram/callback`，**不要仅信任 URL 参数**：按 Telegram Login Widget 的验证规则使用机器人 token 对收到的字段校验 HMAC-SHA256，验证 auth_date 时效并防重放。
4. 校验成功后，服务器创建 HttpOnly + Secure + SameSite Cookie 会话，绑定内部用户 ID，并跳转首页。
5. 将会员状态、订阅钱包和推送偏好迁移到服务端数据库，服务端校验 Pro 权限。当前 `as_pro` localStorage 仅用于演示，不能作为真实会员权限。
6. 如果采用 Telegram 新版 Login/OIDC 方案，需按新版官方文档实现服务端 token 验证；不要混用两种签名算法。

说明：纯静态 HTML 无法安全完成真实 Telegram 登录验证，机器人 token 绝不可写入前端。
