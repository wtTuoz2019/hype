/* Telegram 官方登录（onauth 直传）+ OKX 钱包签名登录。 */
(function(){
var live = window.ALPHASCOPE || null;
function esc(value){return String(value||'').replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function csrf(){var match=document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);return match?decodeURIComponent(match[1]):'';}
function refresh(){
  document.querySelectorAll('#tg-account').forEach(function(el){
    var user = live && live.user;
    if(!user){el.innerHTML='<button class="tg-login" type="button" onclick="openTelegramLogin()">登录</button>';return;}
    var photo = user.photo ? '<img class="tg-avatar" alt="" src="'+esc(user.photo)+'">' : '';
    el.innerHTML='<span class="tg-user">'+photo+esc(user.label)+'</span><button class="tg-login" type="button" onclick="logoutTelegram()">退出</button>';
  });
}
function dlg(){var node=document.getElementById('tg-login-dialog');if(!node){node=document.createElement('dialog');node.id='tg-login-dialog';node.className='tg-dialog login-chooser';document.body.appendChild(node);}return node;}
function panel(title, body){var node=dlg();node.innerHTML='<div class="login-heading"><strong>'+title+'</strong><button aria-label="关闭" class="login-close" onclick="document.getElementById(\'tg-login-dialog\').close()">×</button></div>'+body;if(!node.open)node.showModal();}
function closeDlg(){var node=document.getElementById('tg-login-dialog');if(node && node.open) node.close();}
function postJson(url, body){
  return fetch(url,{
    method:'POST',
    credentials:'same-origin',
    headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},
    body:JSON.stringify(body||{})
  }).then(async function(res){
    var data={};
    try{data=await res.json();}catch(e){}
    if(!res.ok) throw new Error((data && data.message) || '请求失败');
    return data;
  });
}
function finishLogin(data){
  if(live){
    live.user = data.user || null;
    if('pro' in data) live.pro = !!data.pro;
    if(data.subs) live.subs = data.subs;
  }
  refresh();
  closeDlg();
  // 刷新整页，让会员态 / 延迟 / 焦点与会话对齐
  location.reload();
}
window.onTelegramAuth = function(user){
  panel('Telegram 登录','<p class="login-subtitle">正在完成登录…</p>');
  postJson('/auth/telegram/', user).then(finishLogin).catch(function(error){
    panel(
      'Telegram 登录失败',
      '<p class="login-subtitle">'+esc((error && error.message) || '登录失败')+'</p>'+
      '<button class="login-back" onclick="openTelegramLogin()">← 返回重试</button>'+
      '<p class="login-security">请确认 BotFather 已把域名设为 p.c7.pro，且服务器 TELEGRAM_BOT_TOKEN 与机器人一致。</p>'
    );
  });
};
function goTelegramOauth(){
  if(!(live && (live.botId || live.bot))){
    panel('Telegram 登录','<p class="login-subtitle">服务端还没有配置机器人。设置环境变量 TELEGRAM_BOT_USERNAME 和 TELEGRAM_BOT_TOKEN 后再试。</p><button class="login-back" onclick="openTelegramLogin()">← 返回</button>');
    return;
  }
  // 小窗授权：主页面保持打开，授权完小窗关闭并刷新主页
  panel('Telegram 登录','<p class="login-subtitle">请在弹出的窗口里确认授权。完成后会自动回到本页。</p><p class="login-security">如果没有弹出窗口，请允许本站弹窗后重试。</p><button class="login-back" onclick="openTelegramLogin()">← 返回</button>');
  var popup = window.open('/auth/telegram/start/','tg_login','width=520,height=640,menubar=no,toolbar=no,status=no,noopener=no');
  if(!popup){
    panel(
      '需要允许弹窗',
      '<p class="login-subtitle">浏览器拦截了授权窗口。</p>'+
      '<button class="login-primary" type="button" onclick="window.open(\'/auth/telegram/start/\',\'_blank\')">再次打开授权</button>'+
      '<button class="login-back" onclick="openTelegramLogin()">← 返回</button>'
    );
    return;
  }
  var timer = setInterval(function(){
    if(!popup || popup.closed){
      clearInterval(timer);
      location.reload();
    }
  }, 900);
}
function okxProvider(){
  if(window.okxwallet && window.okxwallet.ethereum) return window.okxwallet.ethereum;
  if(window.okxwallet) return window.okxwallet;
  if(window.ethereum){
    if(window.ethereum.isOkxWallet) return window.ethereum;
    var list = window.ethereum.providers || [];
    for(var i=0;i<list.length;i++){
      if(list[i] && list[i].isOkxWallet) return list[i];
    }
  }
  return null;
}
window.openTelegramLogin=function(){
  panel(
    '登录 AlphaScope',
    '<p class="login-subtitle">选择你熟悉的登录方式</p>'+
    '<button class="login-choice" onclick="telegramWeb()"><span class="login-choice-icon">✈</span><span><b>Telegram 网页版</b><small>通过浏览器授权</small></span><span>›</span></button>'+
    '<button class="login-choice" onclick="telegramClient()"><span class="login-choice-icon">↗</span><span><b>Telegram 客户端</b><small>在手机或电脑客户端授权</small></span><span>›</span></button>'+
    '<div class="login-divider">或使用钱包</div>'+
    '<button class="login-choice" onclick="loginWithOkx()"><span class="login-choice-icon okx">◆</span><span><b>OKX 钱包登录</b><small>浏览器扩展 / OKX Web3</small></span><span>›</span></button>'+
    '<p class="login-security">OKX 登录只需签名一次证明地址归属，不会转账，也不会索取助记词。</p>'
  );
};
window.telegramWeb=function(){goTelegramOauth();};
window.telegramClient=function(){goTelegramOauth();};
window.loginWithOkx=async function(){
  var provider = okxProvider();
  if(!provider){
    panel(
      '需要 OKX 钱包',
      '<p class="login-subtitle">没有检测到 OKX 钱包。请安装 OKX 浏览器插件或打开 OKX App 的 Web3 浏览器后再试。</p>'+
      '<p class="login-security"><a href="https://www.okx.com/web3" target="_blank" rel="noopener">前往 OKX Web3 下载</a></p>'+
      '<button class="login-back" onclick="openTelegramLogin()">← 返回</button>'
    );
    return;
  }
  panel('OKX 钱包登录','<p class="login-subtitle">正在请求连接与签名…请在 OKX 钱包里确认。</p>');
  try{
    var accounts = await provider.request({method:'eth_requestAccounts'});
    var address = accounts && accounts[0];
    if(!address) throw new Error('未拿到钱包地址');
    var challenge = await postJson('/auth/wallet/nonce/', {address: address});
    var signature = await provider.request({
      method:'personal_sign',
      params:[challenge.message, challenge.address]
    });
    var result = await postJson('/auth/wallet/verify/', {
      address: challenge.address,
      nonce: challenge.nonce,
      message: challenge.message,
      signature: signature
    });
    finishLogin(result);
  }catch(error){
    var tip = (error && error.message) ? error.message : '连接或签名已取消';
    panel(
      'OKX 登录未完成',
      '<p class="login-subtitle">'+esc(tip)+'</p>'+
      '<button class="login-back" onclick="openTelegramLogin()">← 返回</button>'+
      '<p class="login-security">只会请求一次签名登录，不会要求转账或输入助记词。</p>'
    );
  }
};
window.logoutTelegram=function(){fetch('/logout/',{method:'POST',headers:{'X-CSRFToken':csrf()}}).then(function(){location.href='/';});};
document.addEventListener('DOMContentLoaded', refresh);
})();
