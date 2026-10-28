/* Telegram：一键进官方授权页。OKX：页内签名登录。 */
(function(){
var live = window.ALPHASCOPE || null;
function esc(value){return String(value||'').replace(/[&<>"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];});}
function csrf(){var match=document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);return match?decodeURIComponent(match[1]):'';}
function refresh(){
  document.querySelectorAll('#tg-account').forEach(function(el){
    var user = live && live.user;
    if(!user){el.innerHTML='<button class="tg-login" type="button" onclick="openTelegramLogin()">登录</button>';return;}
    var photo = user.photo ? '<img class="tg-avatar" alt="" src="'+esc(user.photo)+'">' : '';
    var tip = user.handle ? ('@' + user.handle) : (user.label || '');
    el.innerHTML='<span class="tg-user" title="'+esc(tip)+'">'+photo+'<span class="tg-name">'+esc(user.label)+'</span></span><button class="tg-login" type="button" onclick="logoutTelegram()">退出</button>';
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
    '<p class="login-subtitle">选择登录方式</p>'+
    '<button class="login-choice" onclick="goTelegramOauth()"><span class="login-choice-icon">✈</span><span><b>Telegram</b><small>推荐 · 一键授权</small></span><span>›</span></button>'+
    '<button class="login-choice" onclick="loginWithOkx()"><span class="login-choice-icon okx">◆</span><span><b>OKX 钱包</b><small>签名登录，不转账</small></span><span>›</span></button>'
  );
};
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
      '<button class="login-back" onclick="openTelegramLogin()">← 返回</button>'
    );
  }
};
window.logoutTelegram=function(){fetch('/logout/',{method:'POST',headers:{'X-CSRFToken':csrf()}}).then(function(){location.href='/';});};
document.addEventListener('DOMContentLoaded', function(){
  refresh();
  if(location.search.indexOf('login=okx') >= 0) loginWithOkx();
});
})();
