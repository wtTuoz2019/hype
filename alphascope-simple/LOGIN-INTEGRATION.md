# 登录接入说明

当前版本是登录选择界面演示，不会完成真实身份认证。Telegram 网页版/客户端两个选项当前均走模拟登录；正式部署可共用 Telegram 官方 OIDC 登录流程，由官方授权页决定网页或客户端完成授权。不要通过用户自行输入昵称冒充 Telegram 登录。

## Telegram
1. 在 @BotFather 创建 bot，并在 Login Widget 配置允许的 HTTPS 域名、回调地址，获取 Client ID/Secret。
2. 服务端实现 OAuth/OIDC（推荐 Authorization Code + PKCE），校验 state、nonce、ID token 签名、issuer、audience、过期时间，建立 HttpOnly Secure 会话。
3. 前端把 telegramWeb/telegramClient 替换为服务端生成的授权入口。Telegram 客户端深链必须由实际 OIDC 授权过程生成，不要硬编码未经验证的授权链接。

## EVM 钱包
1. 本演示支持浏览器注入钱包连接并展示地址，但**不视为登录**。
2. 服务端签发短期一次性 nonce，使用 SIWE (EIP-4361) 让用户签名，后端验证签名、域名、chainId、nonce 和过期时间。
3. WalletConnect 需配置官方 SDK 和 Project ID；本演示只提供入口，不提供虚假的 QR 登录。
4. 切勿索取助记词、私钥或要求用户为登录批准资产转移。

## 会员权限
登录与 Pro 会员权限分离；会员权限必须由服务端校验，不可信任 localStorage。

参考： https://core.telegram.org/bots/telegram-login ; https://eips.ethereum.org/EIPS/eip-4361
