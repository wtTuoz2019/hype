/* Telegram 登录走服务端校验。钱包连接只读取公开地址，不算登录。 */
(function(){
var live = window.ALPHASCOPE || null;
function esc(value){return String(value||'').replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function csrf(){var match=document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);return match?decodeURIComponent(match[1]):'';}
function refresh(){
  document.querySelectorAll('#tg-account').forEach(function(el){
    var user = live && live.user;
    if(!user){el.innerHTML='<button class="tg-login" type="button" onclick="openTelegramLogin()">登录 / 连接钱包</button>';return;}
    var photo = user.photo ? '<img class="tg-avatar" alt="" src="'+esc(user.photo)+'">' : '';
    el.innerHTML='<span class="tg-user">'+photo+esc(user.label)+'</span><button class="tg-login" type="button" onclick="logoutTelegram()">退出</button>';
  });
}
function dlg(){var node=document.getElementById('tg-login-dialog');if(!node){node=document.createElement('dialog');node.id='tg-login-dialog';node.className='tg-dialog login-chooser';document.body.appendChild(node);}return node;}
function panel(title, body){var node=dlg();node.innerHTML='<div class="login-heading"><strong>'+title+'</strong><button aria-label="关闭" class="login-close" onclick="document.getElementById(\'tg-login-dialog\').close()">×</button></div>'+body;if(!node.open)node.showModal();}
function widget(note){
  var bot = live && live.bot;
  if(!bot){panel('Telegram 登录','<p class="login-subtitle">服务端还没有配置机器人。设置环境变量 TELEGRAM_BOT_USERNAME 和 TELEGRAM_BOT_TOKEN 后，这里会显示 Telegram 官方登录。</p><button class="login-back" onclick="openTelegramLogin()">← 返回</button>');return;}
  panel('Telegram 登录','<p class="login-subtitle">'+note+'</p><div id="tg-widget"></div><p class="login-security">登录由 Telegram 签名，服务器会核对签名和时间，不会索取助记词或私钥。</p>');
  var script=document.createElement('script');
  script.async=true;
  script.src='https://telegram.org/js/telegram-widget.js?22';
  script.setAttribute('data-telegram-login', bot);
  script.setAttribute('data-size','large');
  script.setAttribute('data-auth-url', live.authUrl);
  script.setAttribute('data-request-access','write');
  document.getElementById('tg-widget').appendChild(script);
}
window.openTelegramLogin=function(){panel('登录 AlphaScope','<p class="login-subtitle">选择你熟悉的登录方式</p><button class="login-choice" onclick="telegramWeb()"><span class="login-choice-icon">✈</span><span><b>Telegram 网页版</b><small>通过浏览器授权</small></span><span>›</span></button><button class="login-choice" onclick="telegramClient()"><span class="login-choice-icon">↗</span><span><b>Telegram 客户端</b><small>在手机或电脑客户端授权</small></span><span>›</span></button><div class="login-divider">或使用钱包</div><button class="login-choice" onclick="walletPicker()"><span class="login-choice-icon">◈</span><span><b>连接加密钱包</b><small>MetaMask / 浏览器钱包 / WalletConnect</small></span><span>›</span></button><p class="login-security">钱包连接只读取公开地址，不会变成登录，也不会索取助记词。</p>');};
window.telegramWeb=function(){widget('使用 Telegram 官方组件，在浏览器里确认授权。');};
window.telegramClient=function(){widget('使用 Telegram 官方组件。若已安装客户端，确认按钮会打开手机或电脑上的 Telegram。');};
window.walletPicker=function(){panel('连接加密钱包','<p class="login-subtitle">请选择钱包类型。连接之后只显示公开地址，不算登录。</p><button class="login-choice" onclick="connectInjected()"><span class="login-choice-icon">◈</span><span><b>MetaMask / 浏览器钱包</b><small>仅请求读取公开地址</small></span><span>›</span></button><button class="login-choice" onclick="walletConnectDemo()"><span class="login-choice-icon">▦</span><span><b>WalletConnect</b><small>扫码连接（待接入）</small></span><span>›</span></button><button class="login-back" onclick="openTelegramLogin()">← 返回</button><p class="login-security">绝不输入助记词。正式把钱包当作登录还需要一次性签名，这一步没有做。</p>');};
window.walletConnectDemo=function(){panel('WalletConnect','<p class="login-subtitle">尚未配置 WalletConnect Project ID，扫码连接功能暂不可用。</p><button class="login-back" onclick="walletPicker()">← 返回钱包选择</button>');};
window.connectInjected=async function(){
  if(!window.ethereum){panel('浏览器钱包','<p class="login-subtitle">没有检测到可用的 EVM 浏览器钱包。请安装受信任的钱包扩展后重试。</p><button class="login-back" onclick="walletPicker()">← 返回</button>');return;}
  try{
    var accounts=await window.ethereum.request({method:'eth_requestAccounts'});
    var address=accounts&&accounts[0];
    if(!address)return;
    panel('已连接钱包（未登录）','<p class="login-subtitle">公开地址：<code>'+esc(address.slice(0,8))+'…'+esc(address.slice(-6))+'</code></p><p class="login-security">网站现在只能读取这个地址。这不是登录，也不会请求转账。</p><button class="login-back" onclick="walletPicker()">← 返回</button>');
  }catch(error){panel('连接未完成','<p class="login-subtitle">钱包连接已取消或失败。</p><button class="login-back" onclick="walletPicker()">← 返回</button>');}
};
window.logoutTelegram=function(){fetch('/logout/',{method:'POST',headers:{'X-CSRFToken':csrf()}}).then(function(){location.href='/';});};
document.addEventListener('DOMContentLoaded', refresh);
})();
