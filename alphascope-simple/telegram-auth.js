/* Telegram：跳独立登录页（官方 Widget onauth）。OKX：页内签名登录。 */
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
  location.reload();
}
function goTelegramOauth(){
  // 独立页里点官方按钮，避免 dialog/小窗回调丢失
  location.href = '/auth/telegram/start/';
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
    '<button class="login-choice" onclick="telegramWeb()"><span class="login-choice-icon">✈</span><span><b>Telegram 登录</b><small>跳转官方授权页</small></span><span>›</span></button>'+
    '<div class="login-divider">或使用钱包</div>'+
    '<button class="login-choice" onclick="loginWithOkx()"><span class="login-choice-icon okx">◆</span><span><b>OKX 钱包登录</b><small>浏览器扩展 / OKX Web3</small></span><span>›</span></button>'+
    '<p class="login-security">OKX 登录只需签名一次证明地址归属，不会转账，也不会索取助记词。</p>'
  );
};
window.telegramWeb=function(){goTelegramOauth();};
window.telegramClient=function(){goTelegramOauth();};
window.goTelegramOauth=goTelegramOauth;
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
