// Extracted from dashboard/user.html — community group chats
    const el=document.getElementById('shevelevBal');
    const dock=document.getElementById('shevelevBalMe');
    const num=String(txt).replace(/\s*SHEVELEV\s*$/i,'').trim();
    if(el)el.textContent=txt;
    if(dock)dock.textContent=num;
  };
  setErr('',true);
  setBal('…');
  try{
    const dec=await erc20Decimals(SHEVELEV_TOKEN);
    const pad=addr.replace(/^0x/i,'').toLowerCase().padStart(64,'0');
    const data='0x70a08231'+pad;
    const res=await window.ethereum.request({method:'eth_call',params:[{to:SHEVELEV_TOKEN,data},'latest']});
    const wei=BigInt(!res||res==='0x'?'0':res);
    const human=Number(wei)/Math.pow(10,dec);
    const txt=(Number.isFinite(human)?human.toLocaleString('ru-RU',{maximumFractionDigits:8}):'0')+' SHEVELEV';
    setBal(txt);

  const data=_shevEncodeTransfer(to,wei);
  if(!data){if(st){st.style.color='#f87171';st.textContent='Слишком большая сумма';}return;}
  try{
    const accs=await window.ethereum.request({method:'eth_requestAccounts'});
    const from=accs[0];
    try{
      await window.ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:DSC_CHAIN_ID}]});
    }catch(se){
      if(se&&se.code===4902){
        await window.ethereum.request({method:'wallet_addEthereumChain',params:[DSC_PARAMS]});
      }else if(se&&se.code!==4001)throw se;
    }
    if(st)st.textContent='Подтвердите транзакцию в MetaMask…';
    const txh=await window.ethereum.request({method:'eth_sendTransaction',params:[{from,to:SHEVELEV_TOKEN,data}]});
    if(st){st.style.color='#4ade80';st.textContent='Отправлено: '+String(txh).slice(0,18)+'…';}
    try{
      await fetch('/profile/shevelev-transfer-notify',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        credentials:'same-origin',
        body:JSON.stringify({to:to,tx_hash:String(txh),amount:rawAmt})
      });
    }catch(_){}
    syncBalancesServerSilent({silent:true});
    setTimeout(()=>syncBalancesServerSilent({silent:true}),4000);
  }catch(e){
    if(e&&e.code===4001){if(st){st.style.color='#888';st.textContent='Отменено';}return;}
    if(st){st.style.color='#f87171';st.textContent=e.message||String(e);}
  }
}

// ── Save profile (кабинет + экран «я») ──
async function saveMeProfile(){
  const fd=new FormData();
  fd.append('name',(document.getElementById('meName')?.value||'').trim());
  fd.append('bio',(document.getElementById('meBio')?.value||'').trim());
  fd.append('link_label',(document.getElementById('meLinkLabel')?.value||'').trim());
  fd.append('link_url',(document.getElementById('meLinkUrl')?.value||'').trim());
  const ok=document.getElementById('meProfOk');
  try{
    const r=await fetch('/profile/me',{method:'POST',body:fd});
    if(r.ok){if(ok){ok.style.display='block';setTimeout(()=>location.reload(),800)}}
  }catch(e){}
}
async function saveProfile(){
  const ok=document.getElementById('profOk');
  if(ok)ok.style.display='none';
  const fd=new FormData();
  fd.append('name',(document.getElementById('profName')?.value||'').trim());
  fd.append('bio',(document.getElementById('profBio')?.value||'').trim());
  fd.append('link_label',(document.getElementById('profLinkLabel')?.value||'').trim());
  fd.append('link_url',(document.getElementById('profLinkUrl')?.value||'').trim());
  try{
    const r=await fetch('/profile/me',{method:'POST',body:fd});
    if(r.ok&&ok){ok.style.display='block';setTimeout(()=>ok.style.display='none',3000)}
  }catch(e){}
}

// ── Список диалогов: время как «сегодня / вчера / день недели» ──
function formatMsgListTime(iso){
  if(!iso) return '';
  const d=new Date(iso);
  if(isNaN(d.getTime())) return '';
  const now=new Date();
  const startOf=(x)=>new Date(x.getFullYear(),x.getMonth(),x.getDate());
  const diffDays=Math.round((startOf(now)-startOf(d))/864e5);
  const pad=n=>String(n).padStart(2,'0');
  const hm=pad(d.getHours())+':'+pad(d.getMinutes());
  if(diffDays===0) return hm;
  if(diffDays===1) return 'Вчера';
  if(diffDays>=2 && diffDays<7){
    const days=['Вс','Пн','Вт','Ср','Чт','Пт','Сб'];
    return days[d.getDay()];
  }
  return pad(d.getDate())+'.'+pad(d.getMonth()+1);
}

// ── DM Preview ──
async function loadDMPreview(){
  const list=document.getElementById('dmList');if(!list)return;
  try{
    const r=await fetch('/messages/conversations',{credentials:'same-origin'});
    const d=await r.json();
    if(!d.conversations||!d.conversations.length){
      list.innerHTML='<div style="text-align:center;padding:40px;color:#444"><div style="font-size:36px;margin-bottom:8px">💬</div>Нет сообщений</div>';
      return;
    }
    list.innerHTML='<div class="dm-ios-list">'+d.conversations.map(c=>{
      const ur=parseInt(c.unread,10)||0;
      const av=c.avatar?`<img src="${escAttr(c.avatar)}" alt="">`:'🍄';
      const bd=ur>0?`<span class="dm-ios-badge">${ur}</span>`:'';
      const t=formatMsgListTime(c.last_time||'');
      return `<a href="/chats?open_user=${c.other_id}" class="dm-ios-row">
        <div class="dm-ios-av" style="border-color:${ur>0?'rgba(10,132,255,.6)':'rgba(255,255,255,.1)'}">${av}</div>
        <div class="dm-ios-mid">
          <div class="dm-ios-title-row"><span class="dm-ios-name">${esc(c.name||'Участник')}</span><span class="dm-ios-time">${esc(t)}</span></div>
          <div class="dm-ios-preview-row"><span class="dm-ios-preview">${esc((c.last_text||'').substring(0,120))}</span>${bd}</div>
        </div></a>`;
    }).join('')+'</div>';
  }catch(e){list.innerHTML='<div style="text-align:center;padding:40px;color:#444">Ошибка загрузки</div>';}
}

// ── Feed tabs + pagination (20 posts per page) ──
const FEED_PAGE_SIZE = 20;
let currentFeedTab = 'all';
let currentFeedPage = 1;

function _isVisibleForTab(el, tab){
  const mine = el.dataset.mine === 'true';
  const following = el.dataset.following === 'true' || mine;
  if(tab === 'following') return following;
  return true;
}

function renderFeedPage(){
  const allPosts = [...document.querySelectorAll('#feedPosts .fp')];
  const filtered = allPosts.filter(el => _isVisibleForTab(el, currentFeedTab));
  const totalPages = Math.max(1, Math.ceil(filtered.length / FEED_PAGE_SIZE));
  if(currentFeedPage > totalPages) currentFeedPage = totalPages;
  if(currentFeedPage < 1) currentFeedPage = 1;
  const start = (currentFeedPage - 1) * FEED_PAGE_SIZE;
  const end = start + FEED_PAGE_SIZE;
  const visibleSet = new Set(filtered.slice(start, end));

  allPosts.forEach(el => {
    el.style.display = visibleSet.has(el) ? '' : 'none';
  });

  let empty = document.getElementById('feedEmpty');
  if(!filtered.length){
    if(!empty){
      empty = document.createElement('div');
      empty.id = 'feedEmpty';
      empty.style.cssText = 'text-align:center;padding:40px 0;color:#444';
      empty.innerHTML = '<div style="font-size:36px;margin-bottom:8px">🍄</div><div>Нет постов</div>';
      document.getElementById('feedPosts').appendChild(empty);
    }
  } else {
    empty?.remove();
  }

  const pag = document.getElementById('feedPagination');
  if(!pag) return;
  pag.innerHTML = '';
  if(filtered.length <= FEED_PAGE_SIZE) return;
  for(let p = 1; p <= totalPages; p++){
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = String(p);
    b.className = 'feed-tab' + (p === currentFeedPage ? ' feed-tab-active' : '');
    b.style.minWidth = '42px';
    b.onclick = function(){
      currentFeedPage = p;
      renderFeedPage();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    pag.appendChild(b);
  }
}

function feedTab(tab, btn){
  currentFeedTab = tab;
  currentFeedPage = 1;
  document.querySelectorAll('.feed-tab[data-tab]').forEach(b => b.classList.remove('feed-tab-active'));
  if(btn) btn.classList.add('feed-tab-active');
  renderFeedPage();
}

function refreshFeedEmptyState(){
  renderFeedPage();
}
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded', function(){ renderFeedPage(); });
}else{
  renderFeedPage();
}

// ── Like feed post ──
function likeFeedPost(postId, btn){
  if(typeof NF_communityStarLike!=='function') return;
  const cnt=document.getElementById('like-count-'+postId);
  const icon=btn?btn.querySelector('span'):null;
  const wasLiked=icon && icon.textContent==='⭐';
  NF_communityStarLike(postId,{
    wasLiked:wasLiked,
    backUrl:location.href,
    onCounts:function(d){
      if(cnt && typeof d.count==='number') cnt.textContent=String(Math.max(0,d.count));
      if(d.liked && icon) icon.textContent='⭐';
      if(btn && d.liked) btn.style.color='#ffd84f';
    }
  });
}

function likeProfilePost(postId, btn){
  if(typeof NF_communityStarLike!=='function') return;
  const cnt=document.getElementById('profile-like-count-'+postId);
  const icon=btn?btn.querySelector('span'):null;
  const wasLiked=icon && icon.textContent==='⭐';
  NF_communityStarLike(postId,{
    wasLiked:wasLiked,
    backUrl:location.href,
    onCounts:function(d){
      if(cnt && typeof d.count==='number') cnt.textContent=String(Math.max(0,d.count));
      const feedCnt=document.getElementById('like-count-'+postId);
      if(feedCnt && typeof d.count==='number') feedCnt.textContent=String(Math.max(0,d.count));
      if(d.liked && icon) icon.textContent='⭐';
      if(btn && d.liked) btn.style.color='#ffd84f';
    }
  });
}

async function deleteFeedPost(postId){
  if(!postId||!confirm('Удалить этот пост? Комментарии и лайки тоже будут удалены.')) return;
  try{
    const r=await fetch('/community/post/'+postId,{method:'DELETE',credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){
      document.getElementById('fp-'+postId)?.remove();
      refreshFeedEmptyState();
      showNotification('Пост удалён','success');
    }else showNotification(d.error||'Не удалось удалить','error');
  }catch(e){showNotification('Ошибка сети','error');}
}

async function submitPlanUpgradeRequest(){
  const st=document.getElementById('planReqSt');
  const sel=document.getElementById('planReqSelect');
  const note=document.getElementById('planReqNote');
  if(st)st.textContent='';
  const fd=new FormData();
  fd.append('requested_plan',sel&&sel.value||'start');
  fd.append('note',note&&note.value||'');
  try{
    const r=await fetch('/profile/plan-upgrade-request',{method:'POST',body:fd,credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){
      if(st){st.style.color='#4ade80';st.textContent='Запрос отправлен администратору.';}
      showNotification('Запрос отправлен','success');
      setTimeout(()=>{const m=document.getElementById('planReqModal');if(m)m.style.display='none';},1200);
    }else{
      if(st){st.style.color='#f87171';st.textContent=d.error||'Ошибка';}
      showNotification(d.error||'Ошибка','error');
    }
  }catch(e){if(st){st.style.color='#f87171';st.textContent='Сеть';}}
}

// ── Group chats ──
let selectedGroupId = null;
let groupPollTimer = null;
let _lastGroupMsgSig = {};
let _groupNotificationsEnabled = true;
let _typingPingTimer = null;
window.__canCreateGroups = {{ can_create_groups | tojson }};
window.__canManageGroupSettings = {{ can_manage_group_settings | default(false) | tojson }};

function stopGroupPoll(){
  if(groupPollTimer){ clearInterval(groupPollTimer); groupPollTimer=null; }
}
function startGroupPoll(){
  stopGroupPoll();
  if(!selectedGroupId) return;
  const item = document.querySelector('.ig-g-item[data-gid="'+selectedGroupId+'"]');
  if(!item || item.dataset.member !== '1') return;
  groupPollTimer = setInterval(()=>{
    if(document.hidden) return;
    if(selectedGroupId) loadGroupMessages(selectedGroupId, { silent: true });
  }, 8000);
}
document.addEventListener('visibilitychange', function(){
  if(document.hidden) stopGroupPoll();
  else if(selectedGroupId){
    const it = document.querySelector('.ig-g-item[data-gid="'+selectedGroupId+'"]');
    if(it && it.dataset.member === '1'){ loadGroupMessages(selectedGroupId, { silent: true }); startGroupPoll(); }
  }
});

function _dashUid(){
  const v=document.getElementById('dashUserId');
  return v?parseInt(v.value||'0',10)||0:0;
}

function syncDrawerGroupList(groups){
  const el = document.getElementById('drawerGroupList');
  if(!el) return;
  function escD(t){
    return String(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function escA(s){
    return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
  }
  if(!groups || !groups.length){
    el.innerHTML = '<span style="color:#666;font-size:12px">Пока нет групп</span>';
    return;
  }
  el.innerHTML = groups.map(function(g){
    const nm = escD((g.name||'').trim() || ('Группа #'+g.id));
    const mu = (g.image_url||'').trim();
    const rawName = String(g.name||'').trim();
    const letter = rawName ? rawName.charAt(0).toUpperCase() : '?';
    const av = (mu && (mu.indexOf('http')===0 || mu.indexOf('/')===0))
      ? '<span class="drawer-group-av"><img src="'+escA(mu)+'" alt="" loading="lazy"></span>'
      : '<span class="drawer-group-av">'+escD(letter)+'</span>';
    let prev = String(g.last_message_text||'').trim().slice(0,80);
    if(!prev){
      if(!g.is_member) prev = 'Вступите, чтобы писать';
      else prev = 'Нет сообщений';
    }
    const ur = g.is_member ? (parseInt(g.unread_count,10)||0) : 0;
    const tiso = g.last_message_at || '';
    const tshow = formatMsgListTime(tiso);
    const badge = ur > 0 ? '<span class="drawer-group-badge" style="background:#0a84ff;color:#fff;border-radius:10px;padding:2px 7px;font-size:11px">'+ur+'</span>' : '<span class="drawer-group-badge"></span>';
    return '<button type="button" class="drawer-group-item" onclick="openGroupFromDrawer('+g.id+')">'+av+
      '<span class="drawer-group-meta"><span class="t">'+nm+'</span><span class="s">'+escD(prev)+(tshow?' · '+escD(tshow):'')+'</span></span>'+
      badge+'</button>';
  }).join('');
}
async function openGroupFromDrawer(gid){
  closeDbDrawer();
  window.__groupsJustCreated = true;
  
  await refreshGroupListFromApi();
  window.__groupsJustCreated = false;
  const sid = String(gid);
  let row = null;
  for(let i = 0; i < 14; i++){
    row = document.querySelector('.ig-g-item[data-gid="'+sid+'"]');
    if(row) break;
    await new Promise(function(r){ setTimeout(r, 80); });
  }
  if(row) selectGroupChat(gid, row);
  else showNotification('Не удалось открыть чат. Откройте раздел «Чаты» внизу.','error');
}
function finishGroupChatOverlayUI(){
  document.body.classList.remove('tg-chat-open');
  document.body.classList.remove('tg-group-mobile-chat');
  stopGroupPoll();
  selectedGroupId = null;
  clearGroupReply();
  window.__groupMsgsCache = [];
  window.__groupChatAudioBlob = null;
  window.__groupVoiceRec = null;
  document.querySelectorAll('.ig-g-item').forEach(function(x){ x.classList.remove('on'); });
  const panel = document.getElementById('groupChatPanel');
  const ph = document.getElementById('groupChatPlaceholder');
  if(panel) panel.style.display = 'none';
  if(ph) ph.style.display = 'flex';
  const back = document.getElementById('groupMobileBack');
  if(back) back.style.display = 'none';
}
function clearGroupReply(){
  window.__groupReplyToId = null;
  const bar = document.getElementById('groupReplyBar');
  if(bar){
    bar.style.display = 'none';
    const t = bar.querySelector('.group-reply-text');
    if(t) t.textContent = '';
  }
}

async function loadGroupParticipants(gid){
  const sel = document.getElementById('groupAddressedSelect');
  const search = document.getElementById('groupAddressedSearch');
  if(!sel) return;
  try{
    const r = await fetch('/community/groups/'+gid+'/participants', { credentials:'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(!r.ok || !d.ok || !Array.isArray(d.participants)){ sel.style.display='none'; return; }
    sel.innerHTML = '<option value="">Кому адресовать…</option>' + d.participants.map(function(p){
      return '<option value="'+p.id+'">'+esc(p.name||('ID '+p.id))+'</option>';
    }).join('');
    sel.style.display = d.participants.length ? 'inline-block' : 'none';
    if(search){
      search.style.display = d.participants.length ? 'inline-block' : 'none';
      search.value = '';
      search.oninput = function(){
        const q = (search.value || '').trim().toLowerCase();
        Array.from(sel.options).forEach(function(opt, idx){
          if(idx === 0){ opt.hidden = false; return; }
          opt.hidden = q && String(opt.text || '').toLowerCase().indexOf(q) < 0;
        });
      };
    }
  }catch(e){
    sel.style.display='none';
    if(search) search.style.display='none';
  }
}
function setGroupReplyTo(mid){
  const msgs = window.__groupMsgsCache || [];
  const m = msgs.find(function(x){ return x.id === mid; });
  window.__groupReplyToId = mid;
  const bar = document.getElementById('groupReplyBar');
  const tx = bar && bar.querySelector('.group-reply-text');
  if(tx && m){
    var raw = String(m.text||'').trim();
    if(!raw && m.image_url) raw='📷';
    else if(!raw && m.audio_url) raw='🎤';
    tx.textContent = (m.sender_name||'')+': '+raw.slice(0,100)+(raw.length>100?'…':'');
  } else if(tx){
    tx.textContent = 'Ответ на #'+mid;
  }
  if(bar) bar.style.display = 'flex';
}
function initGroupSwipe(box){
  if(!box || box._gSwipe) return;
  box._gSwipe = true;
  var sx=0,sy=0,rid=0,rowEl=null;
  box.addEventListener('touchstart', function(e){
    var row = e.target.closest('.ig-g-row');
    if(!row) return;
    rowEl = row;
    rid = parseInt(row.dataset.mid||'0',10)||0;
    sx = e.touches[0].clientX;
    sy = e.touches[0].clientY;
  }, {passive:true});
  box.addEventListener('touchend', function(e){
    if(!rid || !rowEl) { rid=0; rowEl=null; return; }
    var dx = e.changedTouches[0].clientX - sx;
    var dy = Math.abs(e.changedTouches[0].clientY - sy);
    if(dx < -55 && dy < 100) setGroupReplyTo(rid);
    rid = 0; rowEl = null;
  }, {passive:true});
}

async function pingGroupTyping(){
  if(!selectedGroupId) return;
  try{
    await fetch('/community/groups/'+selectedGroupId+'/typing', { method:'POST', credentials:'same-origin' });
  }catch(e){}
}
function updateGroupMobileChrome(){
  const back = document.getElementById('groupMobileBack');
  if(!back) return;
  try{
    if(window.matchMedia('(max-width:900px)').matches && selectedGroupId){
      document.body.classList.add('tg-group-mobile-chat');
      back.style.display = 'inline-block';
      if(!window.__groupChatHistoryPushed){
        try{
          history.pushState({ dashShell: 'groupChat' }, '', location.href);
          window.__groupChatHistoryPushed = true;
        }catch(e){}
      }
    }else{
      document.body.classList.remove('tg-group-mobile-chat');
      back.style.display = 'none';
      window.__groupChatHistoryPushed = false;
    }
  }catch(e){
    back.style.display = 'none';
  }
}
window.addEventListener('resize', updateGroupMobileChrome);
if(window.visualViewport) window.visualViewport.addEventListener('resize', updateGroupMobileChrome);
window.addEventListener('popstate', function(){
  if(document.body.classList.contains('tg-group-mobile-chat')){
    window.__groupChatHistoryPushed = false;
    finishGroupChatOverlayUI();
  }
});
window.__dashShellSwipeBack = function(){
  if(document.body.classList.contains('tg-group-mobile-chat')){
    if(window.__groupChatHistoryPushed) history.back();
    else finishGroupChatOverlayUI();
    return false;
  }
  var gdr = document.getElementById('appGlobalDrawer');
  if(gdr && gdr.classList.contains('open')){
    if(typeof closeAppGlobalDrawer === 'function') closeAppGlobalDrawer();
    return false;
  }
  var sm = document.getElementById('sync-modal');
  if(sm && sm.style.display === 'flex'){ sm.style.display = 'none'; return false; }
  var pr = document.getElementById('planReqModal');
  if(pr && pr.style.display === 'flex'){ pr.style.display = 'none'; return false; }
  return true;
};

async function refreshGroupListFromApi(){
  const el = document.getElementById('groupListEl');
  try{
    const r = await fetch('/community/groups', { credentials: 'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(!r.ok || !Array.isArray(d.groups)) return;
    const groups = d.groups;
    window.__groupsLast = groups;
    syncDrawerGroupList(groups);
    if(!el) return;
    const emptyMsg = window.__canCreateGroups ? 'Нет групп — нажмите «Новая группа»' : 'Пока нет групп';
    if(!groups.length){
      /* Не затирать список пустым ответом, если уже есть строки чатов (POST создал группу, а GET вернул [] из‑за рассинхрона/SQL). */
      if(el.querySelector('.ig-g-item')) return;
      el.innerHTML = '<div class="tg-empty-hint" style="color:#666;text-align:center;padding:24px 16px;font-size:13px">'+ emptyMsg +'</div>';
      return;
    }
    function escHtml(t){
      return String(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function escAttr(s){
      return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
    }
    el.innerHTML = groups.map(function(g){
      const on = g.is_member ? ' on' : '';
      const jid = g.join_mode || 'approval';
      const pend = g.pending_join ? '1' : '0';
      const mem = g.is_member ? '1' : '0';
      const memberCount = g.member_count != null ? (parseInt(g.member_count, 10) || 0) : 0;
      const msgCount = g.msg_count != null ? (parseInt(g.msg_count, 10) || 0) : 0;
      const owner = (g.created_by != null && g.created_by !== '') ? String(g.created_by) : '';
      let retAttr = '';
      if(g.message_retention_days != null && g.message_retention_days !== ''){
        retAttr = ' data-retention="'+escAttr(String(g.message_retention_days))+'"';
      }
      let prev = String(g.last_message_text||'').trim().slice(0,140);
      if(!prev){
        if(!g.is_member) prev = 'Вступите, чтобы писать';
        else prev = 'Нет сообщений';
      }
      let statusSuf = '';
      if(g.is_member){}
      else if(g.pending_join) statusSuf = ' · заявка';
      else statusSuf = ' · не в группе';
      const ur = g.is_member ? (parseInt(g.unread_count,10)||0) : 0;
      const iso = g.last_message_at || '';
      const timeStr = formatMsgListTime(iso);
      const rawName = String(g.name||'').trim();
      const letter = rawName ? rawName.charAt(0).toUpperCase() : '?';
      const titleAttr = escAttr(rawName || ('Группа #'+g.id));
      const imgUrl = (g.image_url||'').trim();
      const imgAttr = escAttr(imgUrl);
      const avHtml = (imgUrl && (imgUrl.indexOf('http')===0 || imgUrl.indexOf('/')===0))
        ? '<div class="tg-chat-av tg-chat-av--img"><img src="'+imgAttr+'" alt="" loading="lazy"></div>'
        : '<div class="tg-chat-av">'+escHtml(letter)+'</div>';
      const badgeOn = (g.is_member && ur > 0) ? ' on' : '';
      const badgeTxt = (g.is_member && ur > 0) ? String(ur) : '';
      return '<div class="ig-g-item tg-chat-item tg-ios-row'+on+'" data-title="'+titleAttr+'" data-gid="'+g.id+'" data-member="'+mem+'" data-join="'+escAttr(jid)+'" data-owner="'+escAttr(owner)+'" data-pending="'+pend+'" data-img="'+imgAttr+'" data-last-at="'+escAttr(iso)+'"'+retAttr+' onclick="selectGroupChat('+g.id+',this)">'+
        avHtml+
        '<div class="tg-ios-mid">'+
        '<div class="tg-ios-title-row"><span class="tg-ios-name">'+escHtml(g.name)+'</span><span class="tg-ios-time" data-fmt-time="'+escAttr(iso)+'">'+escHtml(timeStr)+'</span></div>'+
        '<div class="tg-ios-preview-row"><span class="tg-ios-preview">'+escHtml(prev)+statusSuf+' · '+memberCount+' уч.</span><span class="tg-ios-badge'+badgeOn+'">'+escHtml(badgeTxt)+'</span></div>'+
        '<div class="tg-ios-preview-row" style="margin-top:2px"><span class="tg-ios-preview" style="font-size:12px;color:#ffd84f">⭐ '+memberCount+'</span><span class="tg-ios-preview" style="font-size:12px;color:#8be9ff;flex:0 0 auto">💬 '+msgCount+'</span></div>'+
        '</div></div>';
    }).join('');
  }catch(e){}
}
(function(){
  function bootGroupList(){
    if(window.__groupsLast && window.__groupsLast.length) syncDrawerGroupList(window.__groupsLast);
    refreshGroupListFromApi();
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', bootGroupList);
  else bootGroupList();
})();

async function loadOwnerPendingForGroup(gid){
  const bar=document.getElementById('groupOwnerBar');
  const list=document.getElementById('groupPendingList');
  if(!bar||!list)return;
  try{
    const r=await fetch('/community/groups/'+gid+'/join-requests',{credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.requests||!d.requests.length){list.innerHTML='<span style="color:#666">Нет заявок</span>';return;}
    list.innerHTML=d.requests.map(q=>(
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">'+
      '<span>'+esc(q.user_name||'Участник')+' <span style="color:#666">#'+q.user_id+'</span></span>'+
      '<span><button type="button" class="btn-o" style="font-size:11px;padding:4px 8px" onclick="approveGroupJoin('+gid+','+q.id+')">Одобрить</button> '+
      '<button type="button" class="btn-o" style="font-size:11px;padding:4px 8px;opacity:.8" onclick="rejectGroupJoin('+gid+','+q.id+')">Отклонить</button></span></div>'
    )).join('');
  }catch(e){list.innerHTML='<span style="color:#f87171">Ошибка загрузки</span>';}
}

async function approveGroupJoin(gid, rid){
  try{
    const r=await fetch('/community/groups/'+gid+'/join-requests/'+rid+'/approve',{method:'POST',credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){showNotification('Участник добавлен','success');loadOwnerPendingForGroup(gid);}
    else showNotification(d.error||'Ошибка','error');
  }catch(e){showNotification('Сеть','error');}
}

async function rejectGroupJoin(gid, rid){
  try{
    const r=await fetch('/community/groups/'+gid+'/join-requests/'+rid+'/reject',{method:'POST',credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){showNotification('Отклонено','success');loadOwnerPendingForGroup(gid);}
    else showNotification(d.error||'Ошибка','error');
  }catch(e){showNotification('Сеть','error');}
}

async function uploadGroupImage(inp){
  if(!selectedGroupId||!inp||!inp.files||!inp.files[0]) return;
  const st=document.getElementById('groupImageSt');
  if(st) st.textContent='Загрузка…';
  let file = inp.files[0];
  try{
    if(window.MAIImageCropper && /^image\//i.test(file.type||'')){
      const cropped = await window.MAIImageCropper.open(file,{aspectRatio:1});
      if(cropped){
        file = new File([cropped], 'group.jpg', {type:'image/jpeg'});
      }
    }
  }catch(e){
    if(st) st.textContent='';
    inp.value='';
    return;
  }
  const fd=new FormData();
  fd.append('file', file);
  try{
    const r=await fetch('/community/groups/'+selectedGroupId+'/upload-image',{method:'POST',body:fd,credentials:'same-origin'});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){
      if(st) st.textContent='';
      showNotification('Фото обновлено','success');
      await refreshGroupListFromApi();
      const row=document.querySelector('.ig-g-item[data-gid="'+String(selectedGroupId)+'"]');
      if(row) selectGroupChat(selectedGroupId, row);
    }else{
      if(st) st.textContent='';
      showNotification(d.error||'Ошибка загрузки','error');
    }
  }catch(e){
    if(st) st.textContent='';
    showNotification('Сеть','error');
  }
  inp.value='';
}
async function clearGroupImage(){
  if(!selectedGroupId) return;
  try{
    const r=await fetch('/community/groups/'+selectedGroupId+'/settings',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({image_url:''})});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok){
      showNotification('Фото сброшено','success');
      await refreshGroupListFromApi();
      const row=document.querySelector('.ig-g-item[data-gid="'+String(selectedGroupId)+'"]');
      if(row) selectGroupChat(selectedGroupId, row);
    }else showNotification(d.error||'Ошибка','error');
  }catch(e){ showNotification('Сеть','error'); }
}
async function saveGroupRetention(){
  if(!selectedGroupId)return;
  const inp=document.getElementById('groupRetentionInp');
  const raw=(inp&&inp.value||'').trim();
  let body={message_retention_days:null};
  if(raw!==''){
    const n=parseInt(raw,10);
    if(!Number.isFinite(n)||n<1){showNotification('Укажите число дней ≥ 1 или оставьте пусто','error');return;}
    body.message_retention_days=n;
  }
  try{
    const r=await fetch('/community/groups/'+selectedGroupId+'/settings',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.ok)showNotification('Настройки сохранены','success');
    else showNotification(d.error||'Ошибка','error');
  }catch(e){showNotification('Сеть','error');}
}

function selectGroupChat(id, el){
  stopGroupPoll();
  const prevG = selectedGroupId;
  if(prevG != null && String(prevG) !== String(id)) delete _lastGroupMsgSig[String(prevG)];
  selectedGroupId = id;
  document.querySelectorAll('.ig-g-item').forEach(x=>x.classList.remove('on'));
  if(el) el.classList.add('on');
  const titleEl = document.getElementById('groupChatTitle');
  let nm = 'Группа #' + id;
  if(el){
    if(el.dataset && el.dataset.title) nm = el.dataset.title;
    else if(el.querySelector('.tg-chat-item-title')) nm = el.querySelector('.tg-chat-item-title').textContent.trim();
    else if(el.querySelector('div')) nm = el.querySelector('div').textContent.trim();
  }
  if(titleEl) titleEl.textContent = nm;
  const ph = document.getElementById('groupChatPlaceholder');
  const panel = document.getElementById('groupChatPanel');
  const joinBtn = document.getElementById('groupJoinBtn');
  const leaveBtn = document.getElementById('groupLeaveBtn');
  const notifyBtn = document.getElementById('groupNotifyBtn');
  const inp = document.getElementById('groupMsgInput');
  const snd = document.getElementById('groupMsgSend');
  const ownerBar=document.getElementById('groupOwnerBar');
  const retRow=document.getElementById('groupRetentionRow');
  const retInp=document.getElementById('groupRetentionInp');
  const canManage = !!window.__canManageGroupSettings;
  const joinMode=(el&&el.dataset&&el.dataset.join)||'approval';
  const pending=el&&el.dataset&&el.dataset.pending==='1';
  if(ph) ph.style.display = 'none';
  if(panel){ panel.style.display = 'flex'; panel.style.flexDirection = 'column'; }
  document.body.classList.add('tg-chat-open');
  if(ownerBar){
    ownerBar.style.display=canManage?'block':'none';
    if(canManage) loadOwnerPendingForGroup(id);
  }
  if(retRow){
    retRow.style.display=canManage?'flex':'none';
    if(retInp){
      const rv=el&&el.dataset?el.dataset.retention||'':'';
      retInp.value=rv;
    }
  }
  const imgRow=document.getElementById('groupImageRow');
  const imgSt=document.getElementById('groupImageSt');
  if(imgRow){
    const uid=_dashUid();
    let oid=0;
    if(el&&el.dataset&&el.dataset.owner) oid=parseInt(el.dataset.owner,10)||0;
    const canImg=!!(window.__canManageGroupSettings || (oid && oid===uid));
    imgRow.style.display=canImg?'flex':'none';
    if(imgSt) imgSt.textContent='';
  }
  const isMember = el && el.dataset && el.dataset.member === '1';
  if(leaveBtn) leaveBtn.style.display = isMember ? 'inline-block' : 'none';
  if(notifyBtn) notifyBtn.style.display = isMember ? 'inline-block' : 'none';
  if(joinBtn){
    if(isMember){ joinBtn.style.display='none'; }
    else if(pending&&joinMode!=='open'){
      joinBtn.style.display='inline-block';
      joinBtn.textContent='Заявка отправлена';
      joinBtn.disabled=true;
    }else{
      joinBtn.disabled=false;
      joinBtn.style.display='inline-block';
      joinBtn.textContent=(joinMode==='open')?'Вступить':'Подать заявку';
    }
  }
  if(inp){ inp.disabled = !isMember; if(isMember) setTimeout(()=>inp.focus(), 50); }
  if(snd) snd.disabled = !isMember;
  const box = document.getElementById('groupMsgBox');
  if(!isMember){
    let msg='';
    if(pending&&joinMode!=='open') msg='Ожидайте решения администратора.';
    else if(joinMode!=='open') msg='Нажмите «Подать заявку», чтобы вступить. После одобрения администратором вы сможете читать и писать.';
    else msg='Нажмите «Вступить», чтобы видеть переписку и писать в группе.';
    if(box) box.innerHTML = '<div style="color:#888;text-align:center;padding:24px;font-size:14px">'+msg+'</div>';
    const ap = document.getElementById('groupAddressedSelect'); if(ap) ap.style.display='none';
    const as = document.getElementById('groupAddressedSearch'); if(as) as.style.display='none';
    const ur = document.getElementById('groupAddressedUnread'); if(ur) ur.style.display='none';
    const tp = document.getElementById('groupTypingNow'); if(tp) tp.style.display='none';
    updateGroupMobileChrome();
    return;
  }
  loadGroupParticipants(id);
  loadGroupMessages(id).then(()=>{ startGroupPoll(); });
  updateGroupMobileChrome();
}

async function joinSelectedGroup(){
  if(!selectedGroupId) return;
  const joinBtn=document.getElementById('groupJoinBtn');
  if(joinBtn&&joinBtn.disabled) return;
  try{
    const r = await fetch('/community/groups/' + selectedGroupId + '/join', { method: 'POST', credentials: 'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(!r.ok){ showNotification(d.error||'Не удалось','error'); return; }
    if(d.pending){
      showNotification('Заявка отправлена владельцу группы','success');
      const item = document.querySelector('.ig-g-item[data-gid="'+selectedGroupId+'"]');
      if(item){ item.dataset.pending='1'; }
      if(joinBtn){ joinBtn.textContent='Заявка отправлена'; joinBtn.disabled=true; }
      const box=document.getElementById('groupMsgBox');
      if(box) box.innerHTML='<div style="color:#888;text-align:center;padding:24px;font-size:14px">Ожидайте решения владельца группы.</div>';
      return;
    }
    const item = document.querySelector('.ig-g-item[data-gid="'+selectedGroupId+'"]');
    if(item){ item.dataset.member = '1'; item.dataset.pending='0'; item.classList.add('on'); }
    if(joinBtn){ joinBtn.style.display = 'none'; joinBtn.disabled=false; }
    const inp = document.getElementById('groupMsgInput');
    const snd = document.getElementById('groupMsgSend');
    if(inp){ inp.disabled = false; inp.focus(); }
    if(snd) snd.disabled = false;
    const leaveBtn = document.getElementById('groupLeaveBtn');
    const notifyBtn = document.getElementById('groupNotifyBtn');
    if(leaveBtn) leaveBtn.style.display = 'inline-block';
    if(notifyBtn) notifyBtn.style.display = 'inline-block';
    loadGroupParticipants(selectedGroupId);
    const sub = item && item.querySelector('div + div');
    if(sub && sub.textContent.indexOf('вы в чате')<0){
      sub.textContent = (sub.textContent.replace(/\s*·\s*(вы в чате|заявка)\s*$/,'').trim()) + ' · вы в чате';
    }
    await loadGroupMessages(selectedGroupId);
    startGroupPoll();
  }catch(e){ showNotification('Ошибка сети','error'); }
}

async function leaveSelectedGroup(){
  if(!selectedGroupId) return;
  if(!confirm('Уйти из этого чата?')) return;
  try{
    const r = await fetch('/community/groups/'+selectedGroupId+'/leave', { method:'POST', credentials:'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(!r.ok || !d.ok){ showNotification(d.error||'Ошибка','error'); return; }
    const item = document.querySelector('.ig-g-item[data-gid="'+selectedGroupId+'"]');
    if(item){ item.dataset.member='0'; item.classList.remove('on'); }
    selectGroupChat(selectedGroupId, item);
    showNotification('Вы вышли из чата','success');
  }catch(e){ showNotification('Ошибка сети','error'); }
}

async function toggleGroupNotifications(){
  if(!selectedGroupId) return;
  const next = !_groupNotificationsEnabled;
  try{
    const r = await fetch('/community/groups/'+selectedGroupId+'/notifications', {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ enabled: next })
    });
    const d = await r.json().catch(()=>({}));
    if(!r.ok || !d.ok){ showNotification(d.error||'Ошибка','error'); return; }
    _groupNotificationsEnabled = !!d.enabled;
    const btn = document.getElementById('groupNotifyBtn');
    if(btn) btn.textContent = _groupNotificationsEnabled ? 'Уведомления: ВКЛ' : 'Уведомления: ВЫКЛ';
  }catch(e){ showNotification('Ошибка сети','error'); }
}

async function loadGroupMessages(gid, opts){
  const silent = opts && opts.silent;
  const forceBottom = !!(opts && opts.forceBottom);
  const box = document.getElementById('groupMsgBox');
  if(!box) return;
  const prev = box.scrollHeight - box.scrollTop;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  function escUrlAttr(s){ return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }
  function nfGroupMsgHtml(t){
    if(typeof renderCallInviteMessageHtml==='function' && typeof isCallInviteMessageText==='function' && isCallInviteMessageText(t)){
      var ch = renderCallInviteMessageHtml(t);
      if(ch) return ch;
    }
    if(typeof linkifyChatPlain==='function') return linkifyChatPlain(t);
    if(typeof linkifyCommunityMentionsPlain==='function') return linkifyCommunityMentionsPlain(t);
    return esc(String(t||''));
  }
  try{
    const r = await fetch('/community/groups/' + gid + '/messages', { credentials: 'same-origin' });
    let d = {};
    try{ d = await r.json(); }catch(_){}
    if(!r.ok){
      const err = d.detail || d.error || ('HTTP '+r.status);
      if(!silent) box.innerHTML = '<div style="color:#f87171;text-align:center;padding:16px">'+esc(String(err))+'</div>';
      return;
    }
    if(d.error){ if(!silent) box.innerHTML = '<div style="color:#f87171;text-align:center;padding:16px">'+esc(d.error)+'</div>'; return; }
    const msgs = d.messages || [];
    const sig = JSON.stringify(msgs.map(function(m){
      return [m.id, m.text||'', m.created_at||'', m.likes_count||0, m.image_url||'', m.audio_url||'', m.liked?1:0];
    }));
    if(silent && _lastGroupMsgSig[String(gid)] === sig) return;
    _lastGroupMsgSig[String(gid)] = sig;
    window.__groupMsgsCache = msgs;
    if(d.group){
      _groupNotificationsEnabled = !!d.group.notifications_enabled;
      const nbtn = document.getElementById('groupNotifyBtn');
      if(nbtn) nbtn.textContent = _groupNotificationsEnabled ? 'Уведомления: ВКЛ' : 'Уведомления: ВЫКЛ';
      const ur = document.getElementById('groupAddressedUnread');
      if(ur){
        const n = parseInt(d.group.addressed_unread_count||0,10)||0;
        ur.style.display = n>0 ? 'inline-block' : 'none';
        ur.textContent = n>0 ? ('Адресовано вам: '+n) : '';
      }
      const tp = document.getElementById('groupTypingNow');
      if(tp){
        const tu = Array.isArray(d.group.typing_users) ? d.group.typing_users : [];
        if(tu.length){
          tp.style.display = 'inline-block';
          tp.textContent = 'Печатает: ' + tu.join(', ');
        }else{
          tp.style.display = 'none';
          tp.textContent = '';
        }
      }
      const pin = document.getElementById('groupPinnedRow');
      if(pin){
        const txt = String(d.group.pinned_message_text||'').trim();
        if(txt){
          pin.style.display='block';
          pin.innerHTML='📌 '+(typeof linkifyChatPlain==='function'?linkifyChatPlain(txt):(typeof linkifyCommunityMentionsPlain==='function'?linkifyCommunityMentionsPlain(txt):esc(txt)));
          pin.title='Нажмите, чтобы открыть';
          pin.onclick = function(){ alert(txt); };
        }
        else { pin.style.display='none'; pin.textContent=''; }
      }
      const imgBtn = document.getElementById('groupChatImgInp');
      const micBtn = document.getElementById('groupMicBtn');
      if(imgBtn) imgBtn.disabled = d.group.allow_photo === false;
      if(micBtn) micBtn.disabled = d.group.allow_audio === false;
    }
    box.innerHTML = msgs.map(function(m){
      var replyHtml = '';
      if(m.reply_to && m.reply_to.sender_name){
        replyHtml = '<div class="ig-g-reply">↩ '+esc(m.reply_to.sender_name)+': '+nfGroupMsgHtml(m.reply_to.preview||'')+'</div>';
      }
      var addressedHtml = m.addressed_user_id ? '<div class="ig-g-reply" style="color:#8be9ff">→ адресно</div>' : '';
      var imgHtml = m.image_url ? '<img class="ig-g-img" src="'+escUrlAttr(m.image_url)+'" alt="">' : '';
      var audHtml = m.audio_url ? '<audio class="ig-g-audio" controls playsinline preload="metadata" src="'+escUrlAttr(m.audio_url)+'">Ваш браузер не воспроизводит аудио</audio>' : '';
      var txtHtml = (m.text && String(m.text).trim()) ? '<div class="ig-g-text">'+nfGroupMsgHtml(m.text)+'</div>' : '';
      var likeN = Number(m.likes_count||0);
      var liked = !!m.liked;
      var likedUsers = Array.isArray(m.liked_users) ? m.liked_users : [];
      var likedUsersHtml = likedUsers.length ? '<span style="display:inline-flex;gap:3px;margin-left:6px;vertical-align:middle">'+likedUsers.map(function(u){
        if(u.avatar) return '<img src="'+escUrlAttr(u.avatar)+'" alt="" style="width:14px;height:14px;border-radius:50%;object-fit:cover">';
        return '<span style="width:14px;height:14px;border-radius:50%;background:#223;color:#9ee;font-size:9px;display:inline-flex;align-items:center;justify-content:center">'+esc(String((u.name||'?')).slice(0,1).toUpperCase())+'</span>';
      }).join('')+'</span>' : '';
      var avHtml = '';
      if(!m.is_mine){
        if(m.sender_avatar) avHtml = '<span class="tg-msg-av"><img src="'+escUrlAttr(m.sender_avatar)+'" alt=""></span>';
        else avHtml = '<span class="tg-msg-av">'+esc(String((m.sender_name||'?')).slice(0,1).toUpperCase())+'</span>';
      }else{
        avHtml = '<span class="tg-msg-av"></span>';
      }
      return '<div class="ig-g-row '+(m.is_mine?'me':'them')+'" data-mid="'+m.id+'">'+avHtml+
        '<div class="ig-g-bubble '+(m.is_mine?'me':'them')+'" ondblclick="toggleGroupLike('+m.id+', this.closest(\'.ig-g-row\').querySelector(\'.ig-g-like\'))">'+
        '<div class="ig-g-meta"><span class="ig-g-name">'+esc(m.sender_name)+'</span>'+
        (m.can_delete ? '<button type="button" class="ig-g-del" onclick="deleteGroupMessage('+m.id+')" title="Удалить" aria-label="Удалить">×</button>' : '')+
        '<button type="button" class="ig-g-reply-btn" onclick="setGroupReplyTo('+m.id+')" title="Ответить" aria-label="Ответить">↩ Ответить</button>'+
        '</div>'+replyHtml+addressedHtml+imgHtml+audHtml+txtHtml+
        '<div class="ig-g-like-row"><button type="button" class="ig-g-like" onclick="toggleGroupLike('+m.id+',this)">'+(liked?'⭐':'✩')+'</button> <span class="ig-g-lc">'+likeN+'</span>'+likedUsersHtml+'</div>'+
        '<div class="ig-g-time">'+esc(m.created_at)+'</div></div></div>';
    }).join('');
    initGroupSwipe(box);
    if(forceBottom || atBottom) box.scrollTop = box.scrollHeight;
    else box.scrollTop = Math.max(0, box.scrollHeight - prev);
    if(!silent) fetch('/community/groups/'+gid+'/mark-read',{method:'POST',credentials:'same-origin'}).then(function(){
      try{ if(typeof updateUnreadCount==='function') updateUnreadCount(); }catch(e){}
      try{ if(typeof refreshAppHeaderBadges==='function') refreshAppHeaderBadges(); }catch(e){}
    }).catch(function(){});
  }catch(e){ if(!silent) box.innerHTML = '<div style="color:#f87171;text-align:center">Ошибка загрузки</div>'; }
}

async function toggleGroupLike(mid, btn){
  if(!selectedGroupId || !mid) return;
  try{
    const r = await fetch('/community/groups/'+selectedGroupId+'/messages/'+mid+'/like', { method:'POST', credentials:'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(r.ok && d.ok){
      var row = btn && btn.closest('.ig-g-like-row');
      var sp = row && row.querySelector('.ig-g-lc');
