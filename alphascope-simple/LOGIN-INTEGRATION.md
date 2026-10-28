# 登录接入说明

当前支持两种正式登录：Telegram Login Widget、OKX 钱包签名登录。

## Telegram
1. 在 @BotFather 创建 bot，`/setdomain` 配置站点 HTTPS 域名。
2. 服务端配置 `TELEGRAM_BOT_USERNAME`、`TELEGRAM_BOT_TOKEN`。
3. 回调 `/auth/telegram/callback` 校验 hash、防重放，并写入前台会员资料。

## OKX 钱包
1. 登录弹窗只保留 **OKX 钱包**，不再提供 MetaMask / WalletConnect 入口。
2. 流程：连接 OKX → `/auth/wallet/nonce/` 取一次性消息 → `personal_sign` → `/auth/wallet/verify/` 验签建会话。
3. 只证明地址归属，不转账、不索取助记词/私钥。
4. 需安装 [OKX Web3](https://www.okx.com/web3) 浏览器扩展，或在 OKX App Web3 浏览器打开站点。

## 会员权限
登录与 Pro 会员权限分离；会员权限由服务端校验，不可信任 localStorage。

参考： https://core.telegram.org/bots/telegram-login ; https://www.okx.com/web3
