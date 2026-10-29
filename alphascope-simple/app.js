var fallbackWallets = [
  {id:'A1',name:'鲸鱼猎手',address:'0x8a71...f32d',win:72,wins:72,total:100,roi:36.4,pnl:18420,dd:12.5},
  {id:'B2',name:'趋势观察者',address:'0xf97a...ddd1',win:68,wins:34,total:50,roi:82.6,pnl:29500,dd:28.1},
  {id:'C3',name:'链上先锋',address:'0x72ac...a40e',win:64,wins:48,total:75,roi:54.8,pnl:16280,dd:17.9},
  {id:'D4',name:'稳健交易员',address:'0x91bd...813e',win:61,wins:61,total:100,roi:22.3,pnl:12660,dd:6.8}
];
var fallbackEvents = [['09:36:21','BTC','开多','A1',2850000],['09:34:08','ETH','开空','B2',1430000],['09:28:55','SOL','加多','C3',960000],['09:12:41','BTC','平多','D4',810000],['08:55:03','ETH','开多','A1',720000],['08:41:09','SOL','开空','B2',680000]];
var live = window.ALPHASCOPE || null;
var wallets = live && live.wallets ? live.wallets : fallbackWallets;
var events = live && live.events ? live.events : fallbackEvents.map(function(row, index){return {id:index,time:row[0],symbol:row[1],act:row[2],wallet:row[3],amount:row[4]};});
var filter = '全部';
var seen = {};
events.forEach(function(row){ seen[row.id] = true; });
var booted = false;
var $ = function(sel){ return document.querySelector(sel); };
function esc(value){ return String(value || '').replace(/[&<>"']/g, function(ch){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]; }); }

function csrf(){
  var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}
function postJSON(url, body){
  return fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(body || {})}).then(function(res){ return res.json().then(function(data){ data.ok = res.ok; return data; }); });
}
function pro(){ return live ? !!live.pro : localStorage.getItem('as_pro') === '1'; }
function subscriptions(){ return live ? (live.subs || []) : JSON.parse(localStorage.getItem('as_subs') || '[]'); }
function shell(){
  document.querySelectorAll('[data-pro-label]').forEach(function(node){ node.textContent = pro() ? 'Pro 会员 · 约 1 分钟同步' : '免费用户 · 延迟约 30 分钟'; });
  document.querySelectorAll('[data-pro-only]').forEach(function(node){ node.classList.toggle('hidden', !pro()); });
  document.querySelectorAll('[data-free-only]').forEach(function(node){ node.classList.toggle('hidden', pro()); });
}
function applyState(data){
  if(!live || !data) return;
  if('pro' in data) live.pro = !!data.pro;
  if(data.subs) live.subs = data.subs;
}
async function upgrade(){
  if(live){ applyState(await postJSON('/api/membership/', {pro:true})); }
  else localStorage.setItem('as_pro','1');
  shell(); render(); if(typeof renderLeaderboard === 'function') renderLeaderboard();
}
async function downgrade(){
  if(live){ applyState(await postJSON('/api/membership/', {pro:false})); }
  else { localStorage.removeItem('as_pro'); localStorage.removeItem('as_subs'); }
  shell(); render(); if(typeof renderLeaderboard === 'function') renderLeaderboard();
}
function win(wallet){ return wallet.win + '%'; }
function pctText(n){ var v = Number(n); if(!Number.isFinite(v)) return '—'; return (v > 0 ? '+' : '') + v.toFixed(1) + '%'; }
function moneyText(n){ var v = Number(n); if(!Number.isFinite(v)) return '—'; var body = Math.abs(v).toLocaleString('en-US', {maximumFractionDigits: 0}); return (v > 0 ? '+' : v < 0 ? '-' : '') + body + ' USDT'; }
function formatAmount(n){
  n = Number(n) || 0;
  return n >= 10000 ? (n / 10000).toLocaleString('zh-CN', {maximumFractionDigits:1}) + ' 万 USDT' : n.toLocaleString('zh-CN') + ' USDT';
}
function eventHTML(row){
  var wallet = wallets.find(function(item){ return item.id === row.wallet; });
  if(!wallet) return '';
  var side = String(row.act).includes('空') ? 'short' : 'long';
  var subscribed = subscriptions().indexOf(wallet.id) >= 0;
  return '<div class="event"><span class="clock">' + esc(row.time) + '</span><div class="event-main"><div class="line"><span class="action ' + side + '">' + esc(row.act) + '</span><span class="symbol">' + esc(row.symbol) + ' 合约</span></div><div class="name"><a href="/wallet/?id=' + encodeURIComponent(wallet.id) + '">' + esc(wallet.name) + '</a> <span class="tiny">' + esc(wallet.address) + '</span></div><div class="meta">历史平仓胜率 ' + win(wallet) + ' · ' + wallet.wins + '/' + wallet.total + ' 笔' + (row.amount ? ' · 开单 ' + formatAmount(row.amount) : '') + '</div></div><div class="right"><button class="btn ' + (subscribed ? 'secondary' : 'outline') + '" onclick="toggleSub(\'' + wallet.id + '\')">' + (subscribed ? '✓ 已订阅' : '订阅提醒') + '</button></div></div>';
}
async function toggleSub(id){
  if(!pro()){ location.href = '/pricing/?need=alert'; return; }
  if(live){
    var data = await postJSON('/api/subscribe/', {id:id});
    if(!data.ok){ location.href = '/pricing/?need=alert'; return; }
    applyState(data);
  } else {
    var list = subscriptions();
    list = list.indexOf(id) >= 0 ? list.filter(function(item){ return item !== id; }) : list.concat([id]);
    localStorage.setItem('as_subs', JSON.stringify(list));
  }
  render(); updateVoiceUI(); renderLeaderboard();
}
function focusRows(){
  if(live && live.today_focus && live.today_focus.length) return live.today_focus.slice();
  // 兜底：快讯窗可能被加仓刷屏，尽量仍从本地事件里找开/加
  return events.filter(function(row){
    return ['开多','开空','加多','加空'].indexOf(row.act) >= 0 && row.amount;
  }).slice().sort(function(a,b){ return b.amount - a.amount; }).slice(0, 3);
}
function walletFor(row){
  return wallets.find(function(item){ return item.id === String(row.wallet); }) || {
    id: String(row.wallet),
    name: row.wallet_name || ('钱包 ' + row.wallet),
    win: 0
  };
}
function renderImportant(){
  var box = document.getElementById('biggest-open');
  var list = document.getElementById('biggest-list');
  if(!box) return;
  var opens = focusRows();
  if(!opens.length){ box.innerHTML = '<p class="muted">今日暂无开仓</p>'; if(list) list.innerHTML = ''; return; }
  var top = opens[0];
  var wallet = walletFor(top);
  var subscribed = subscriptions().indexOf(wallet.id) >= 0;
  box.innerHTML = '<div class="biggest-label">🏆 今日最大单笔开单资金</div><strong class="biggest-amount">' + formatAmount(top.amount) + '</strong><div class="biggest-coin"><span class="action ' + (String(top.act).includes('空') ? 'short' : 'long') + '">' + top.act + '</span> <b>' + top.symbol + ' 合约</b></div><div class="biggest-name">' + esc(wallet.name) + ' <span>· 胜率 ' + win(wallet) + '</span></div><div class="biggest-time">' + esc(top.time) + ' · 历史已平仓胜率</div><button class="btn outline important-sub" onclick="toggleSub(\'' + wallet.id + '\')">' + (subscribed ? '✓ 已订阅' : '＋ 订阅这个钱包') + '</button>';
  if(list) list.innerHTML = opens.slice(0,3).map(function(row, index){
    var who = walletFor(row);
    return '<div class="top-open-row"><span class="top-place">' + (index + 1) + '</span><div><b>' + esc(row.symbol) + ' ' + esc(row.act) + '</b><small>' + esc(who.name) + ' · ' + esc(row.time) + '</small></div><strong>' + formatAmount(row.amount) + '</strong></div>';
  }).join('');
}
function renderDirectionOverview(){
  var tracked = events.filter(function(row){ return ['开多','开空','加多','加空'].indexOf(row.act) >= 0 && Number.isFinite(Number(row.amount)); });
  var longEvents = tracked.filter(function(row){ return String(row.act).includes('多'); });
  var shortEvents = tracked.filter(function(row){ return String(row.act).includes('空'); });
  var longFunds = longEvents.reduce(function(sum, row){ return sum + Number(row.amount); }, 0);
  var shortFunds = shortEvents.reduce(function(sum, row){ return sum + Number(row.amount); }, 0);
  var total = longFunds + shortFunds;
  var ratio = total ? longFunds / total * 100 : 0;
  var set = function(id, value){ var node = document.getElementById(id); if(node) node.textContent = value; };
  set('long-funds', formatAmount(longFunds));
  set('short-funds', formatAmount(shortFunds));
  set('wallet-long-count', '做多钱包 ' + new Set(longEvents.map(function(row){ return row.wallet; })).size + ' 个');
  set('wallet-short-count', '做空钱包 ' + new Set(shortEvents.map(function(row){ return row.wallet; })).size + ' 个');
  set('long-share', '多头资金 ' + (total ? ratio.toFixed(1) : '0') + '%');
  set('short-share', '空头资金 ' + (total ? (100 - ratio).toFixed(1) : '0') + '%');
  set('fund-difference', '资金差 ' + formatAmount(Math.abs(longFunds - shortFunds)));
  var bar = document.getElementById('direction-long-bar');
  if(bar) bar.style.width = ratio + '%';
  paintAnalysis();
}
function paintAnalysis(){
  var longNode = document.getElementById('analysis-long');
  if(!longNode) return;
  var stats = live && live.today ? live.today : null;
  var longCount = stats ? stats.long : events.filter(function(row){ return row.act === '开多' || row.act === '加多'; }).length;
  var shortCount = stats ? stats.short : events.filter(function(row){ return row.act === '开空' || row.act === '加空'; }).length;
  var closeCount = stats ? stats.close : events.filter(function(row){ return String(row.act).indexOf('平') === 0; }).length;
  longNode.textContent = longCount + ' 笔';
  var shortNode = document.getElementById('analysis-short');
  if(shortNode) shortNode.textContent = shortCount + ' 笔';
  var note = document.getElementById('analysis-close-note');
  if(note) note.textContent = '另有 ' + closeCount + ' 笔平仓记录';
}
function render(){
  shell();
  renderImportant();
  renderDirectionOverview();
  var feed = $('#feed');
  if(feed){
    var rows = events.filter(function(row){ return filter === '全部' || String(row.act).includes(filter); });
    feed.innerHTML = rows.map(eventHTML).join('') || '<div class="empty">暂无对应开单快讯</div>';
  }
  if($('#sub-count')) $('#sub-count').textContent = subscriptions().length + ' 个';
  if($('#voice-mode-summary')) updateVoiceUI();
  if($('#sync-status')){
    var poll = live && live.poll_sec ? live.poll_sec : 3;
    $('#sync-status').textContent = pro()
      ? ('列表约每 ' + poll + ' 秒刷新 · 新开单约 1 分钟后可见')
      : ('列表约每 ' + poll + ' 秒刷新 · 新开单约 30 分钟后可见');
  }
}
function delaySeconds(){
  if(live && live.delay_sec) return Number(live.delay_sec) || 60;
  return pro() ? 60 : 30 * 60;
}
function formatCountdown(sec){
  sec = Math.max(0, Math.floor(sec));
  if(sec >= 3600){
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }
  return String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
}
function syncCountdownFrom(data){
  if(!live || !data) return;
  if('delay_sec' in data) live.delay_sec = Number(data.delay_sec) || live.delay_sec;
  if('next_visible_at' in data) live.next_visible_at = data.next_visible_at;
}
function tickCountdown(){
  var box = $('#sync-countdown');
  if(!box) return;
  var at = live && live.next_visible_at != null ? Number(live.next_visible_at) : NaN;
  if(!Number.isFinite(at) || at <= 0){
    // 当前延迟窗里没有排队信号
    box.textContent = '--:--';
    box.title = '当前没有等待可见的新开单';
    return;
  }
  var left = Math.max(0, Math.ceil(at - Date.now() / 1000));
  box.textContent = formatCountdown(left);
  box.title = left > 0
    ? ('下一条开单约 ' + formatCountdown(left) + ' 后可见')
    : '下一条开单即将可见';
}
function selectFilter(name, el){
  filter = name;
  document.querySelectorAll('.chip').forEach(function(node){ node.classList.remove('on'); });
  el.classList.add('on');
  render();
}
function voiceMode(){ return localStorage.getItem('as_voice_mode') === 'subs' ? 'subs' : 'all'; }
function setVoiceMode(mode){ localStorage.setItem('as_voice_mode', mode === 'subs' ? 'subs' : 'all'); updateVoiceUI(); }
function saveVoiceEnabled(){
  localStorage.setItem('as_voice_on', $('#voice-on') && $('#voice-on').checked ? '1' : '0');
  if($('#voice-on') && $('#voice-on').checked) unlockVoice();
}
function updateVoiceUI(){
  if($('#voice-scope')) $('#voice-scope').value = voiceMode();
  if($('#voice-mode-summary')) $('#voice-mode-summary').textContent = voiceMode() === 'all' ? '当前模式：所有监控钱包的新开单都会播报' : '当前模式：仅播报已订阅的 ' + subscriptions().length + ' 个钱包' + (subscriptions().length ? '' : '（还没有订阅钱包，可在下方快讯中订阅）');
  if($('#voice-on')) $('#voice-on').checked = localStorage.getItem('as_voice_on') !== '0';
}
function feedback(msg){ if($('#notify')){ $('#notify').textContent = msg; $('#notify').classList.remove('hidden'); } }
var voiceUnlocked = false;
var spokenAlerts = {};
function unlockVoice(){
  if(voiceUnlocked || !('speechSynthesis' in window)) return;
  voiceUnlocked = true;
  try{
    speechSynthesis.resume();
    var warm = new SpeechSynthesisUtterance(' ');
    warm.volume = 0;
    warm.lang = 'zh-CN';
    speechSynthesis.speak(warm);
    speechSynthesis.cancel();
  }catch(e){}
}
function voiceEnabled(){
  var box = $('#voice-on');
  // Pro 区未渲染时，仍允许按本地开关意图播报（默认开）
  if(!box) return localStorage.getItem('as_voice_on') !== '0';
  return !!box.checked;
}
function speak(msg, force){
  // force=true：试听，不依赖「语音开启」开关
  if(!force && !voiceEnabled()) return;
  if(!('speechSynthesis' in window)) return;
  unlockVoice();
  try{
    speechSynthesis.resume();
    var utter = new SpeechSynthesisUtterance(msg);
    utter.lang = 'zh-CN';
    utter.rate = 1;
    speechSynthesis.cancel();
    speechSynthesis.speak(utter);
  }catch(e){}
}
function alertFor(row){
  if(!pro()) return;
  // 减仓/平仓不喊，避免加空刷屏把语音打爆
  if(['开多','开空','加多','加空'].indexOf(row.act) < 0) return;
  var key = String(row.id || '') || (String(row.wallet) + '|' + String(row.symbol) + '|' + String(row.act) + '|' + String(row.time));
  if(spokenAlerts[key]) return; // 同一条只播一次
  spokenAlerts[key] = true;
  var wallet = walletFor(row);
  var matches = voiceMode() === 'all' || subscriptions().indexOf(String(wallet.id)) >= 0;
  if(!matches){ feedback('已收到 ' + wallet.name + ' 的新开单；按“仅订阅钱包”模式，本次不播报。'); return; }
  var msg = wallet.name + '，' + row.symbol + ' 合约' + row.act;
  feedback('🔔 ' + msg);
  speak(msg);
  if('Notification' in window && Notification.permission === 'granted') new Notification('聪明钱包开单提醒', {body:msg});
}
async function simulate(){
  if(live){
    var data = await postJSON('/api/tape/test/', {});
    if(data.event){
      if(!seen[data.event.id]){ events.unshift(data.event); seen[data.event.id] = true; }
      render();
      alertFor(data.event);
    } else feedback(data.message || '免费用户不会即时收到这条新开单。');
    return;
  }
  var wallet = wallets[Math.floor(Math.random() * wallets.length)];
  var symbols = ['BTC','ETH','SOL'];
  var acts = ['开多','开空','加多','平空'];
  var row = {id:Date.now(), time:new Date().toLocaleTimeString('zh-CN',{hour12:false}), symbol:symbols[Math.floor(Math.random()*symbols.length)], act:acts[Math.floor(Math.random()*acts.length)], wallet:wallet.id, amount:Math.round((150000+Math.random()*3200000)/1000)*1000};
  if(pro()){ events.unshift(row); render(); alertFor(row); }
  else feedback('模拟产生一条新信号。免费用户不会即时收到推送，需等待延迟更新；升级 Pro 可开启播报。');
}
function testVoice(){
  unlockVoice();
  if(!pro()){ location.href = '/pricing/?need=alert'; return; }
  if(!('speechSynthesis' in window)){ feedback('当前浏览器不支持语音播报'); return; }
  var pool = voiceMode() === 'all' ? wallets : wallets.filter(function(wallet){ return subscriptions().indexOf(wallet.id) >= 0; });
  if(!pool.length){ feedback('当前是“仅订阅钱包”模式，请先在下方快讯中订阅一个钱包，再测试播报。'); return; }
  var wallet = pool[0];
  feedback('🔊 试听 · ' + wallet.name + '，BTC 合约开多' + (voiceEnabled() ? '' : '（尚未开启自动播报）'));
  speak('语音测试：' + wallet.name + '，BTC 合约开多', true);
}
async function permission(){
  if('Notification' in window) await Notification.requestPermission();
  else alert('此浏览器不支持桌面通知');
}
var walletRev = live && live.wallet_rev ? live.wallet_rev : '';
var lastEventId = 0;
var pullingWallets = false;
events.forEach(function(row){ if(row.id > lastEventId) lastEventId = row.id; });
function applyTape(data){
  if(!data || !data.events) return;
  var fresh = data.events.filter(function(row){ return !seen[row.id]; });
  data.events.forEach(function(row){
    seen[row.id] = true;
    if(row.id > lastEventId) lastEventId = row.id;
  });
  if(data.incremental){
    if(fresh.length){
      events = fresh.slice().reverse().concat(events).slice(0, 80);
    }
  } else {
    events = data.events;
  }
  if(data.today) live.today = data.today;
  if(data.today_focus) live.today_focus = data.today_focus;
  syncCountdownFrom(data);
  applyState(data);
  render();
  if(booted && pro()) fresh.forEach(alertFor);
  booted = true;
  if(data.wallet_rev && data.wallet_rev !== walletRev){
    walletRev = data.wallet_rev;
    pullWallets();
  } else if(data.wallet_rev){
    walletRev = data.wallet_rev;
  }
}
function pull(){
  if(!live) return;
  var url = '/api/tape/';
  if(lastEventId) url += '?after=' + lastEventId;
  fetch(url).then(function(res){ return res.json(); }).then(applyTape).catch(function(){});
}
function pullWallets(){
  if(!live || pullingWallets) return;
  pullingWallets = true;
  fetch('/api/tape/?wallets=1&events=0').then(function(res){ return res.json(); }).then(function(data){
    if(!data) return;
    if(data.wallets){
      wallets = data.wallets;
      if(data.wallet_rev) walletRev = data.wallet_rev;
      renderLeaderboard();
      if(typeof paintWallet === 'function') paintWallet();
    }
    applyState(data);
  }).catch(function(){}).finally(function(){ pullingWallets = false; });
}
function loadWalletDetail(id){
  if(!live || !id) return Promise.resolve(null);
  return fetch('/api/wallet/?id=' + encodeURIComponent(id)).then(function(res){ return res.json(); }).then(function(data){
    if(!data || !data.wallet) return null;
    var card = data.wallet;
    var index = wallets.findIndex(function(item){ return item.id === card.id; });
    if(index >= 0) wallets[index] = Object.assign({}, wallets[index], card);
    else wallets.push(card);
    applyState(data);
    return card;
  }).catch(function(){ return null; });
}
document.addEventListener('DOMContentLoaded', function(){
  shell();
  tickCountdown();
  setInterval(tickCountdown, 1000);
  render();
  if($('#need-note') && location.search.indexOf('need=alert') >= 0) $('#need-note').classList.remove('hidden');
  if(live){
    booted = true;
    var pollMs = (live.poll_sec ? Number(live.poll_sec) : 3) * 1000;
    setInterval(pull, pollMs);
    setInterval(pullWallets, 60000);
  }
});

var boardOrder = 'roi';
var boardTerm = '';
function setBoardOrder(value){
  boardOrder = value;
  document.querySelectorAll('[data-rank]').forEach(function(button){ button.classList.toggle('on', button.dataset.rank === value); });
  renderLeaderboard();
}
function setBoardTerm(value){ boardTerm = value.trim().toLowerCase(); renderLeaderboard(); }
function renderLeaderboard(){
  var root = document.getElementById('leaderboard');
  if(!root) return;
  var data = wallets.filter(function(wallet){ return (wallet.name + wallet.address).toLowerCase().indexOf(boardTerm) >= 0; }).slice().sort(function(a, b){
    if(!!a.waiting !== !!b.waiting) return a.waiting ? 1 : -1;
    if(boardOrder === 'dd') return a.dd - b.dd;
    return b[boardOrder] - a[boardOrder];
  });
  root.innerHTML = data.map(function(wallet, index){
    var sub = subscriptions().indexOf(wallet.id) >= 0;
    var metrics = wallet.waiting
      ? '<p class="rank-foot">收益正在后台刷新</p>'
      : '<div class="rank-metrics"><div><small>30天收益率</small><strong class="' + (wallet.roi >= 0 ? 'positive' : 'risk') + '">' + pctText(wallet.roi) + '</strong></div><div><small>历史胜率</small><strong>' + Number(wallet.win).toFixed(1) + '%</strong></div><div><small>已平仓笔数</small><strong>' + wallet.total + ' 笔</strong></div><div><small>30天最大回撤</small><strong class="risk">' + Number(wallet.dd).toFixed(1) + '%</strong></div></div><div class="rank-foot">30天盈亏：' + moneyText(wallet.pnl) + '</div>';
    return '<article class="rank-card"><div class="rank-head"><span class="rank-number">#' + (index + 1) + '</span><div class="rank-identity"><strong><a href="/wallet/?id=' + wallet.id + '">' + wallet.name + '</a></strong><small>' + wallet.address + '</small></div><button class="btn ' + (sub ? 'secondary' : 'outline') + '" onclick="toggleSub(\'' + wallet.id + '\')">' + (sub ? '✓ 已订阅' : '＋ 订阅提醒') + '</button></div>' + metrics + '</article>';
  }).join('') || '<div class="empty">没有找到匹配的钱包</div>';
  var hint = document.getElementById('rank-plan');
  if(hint) hint.textContent = pro() ? 'Pro 已开启：点击订阅后，可在首页使用“仅订阅钱包播报”' : '普通用户可查看所有榜单；订阅即时提醒需升级 Pro';
}
document.addEventListener('DOMContentLoaded', renderLeaderboard);
