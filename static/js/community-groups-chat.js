// Extracted from dashboard/user.html — community group chats
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

/** Текст сообщения / превью: безопасный HTML + ссылки @user_id */
function _nfLm(t){
  if(typeof linkifyCommunityMentionsPlain==='function') return linkifyCommunityMentionsPlain(t);
  if(typeof esc==='function') return esc(t);
  var d=document.createElement('div'); d.textContent=t==null?'':String(t); return d.innerHTML;
}

// ── Group chats ──
let selectedGroupId = null;
let groupPollTimer = null;
let _lastGroupMsgSig = {};
let _groupNotificationsEnabled = true;
let _typingPingTimer = null;
// window.__canCreateGroups / __canManageGroupSettings — задаются в шаблоне до подключения этого файла

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
          pin.innerHTML='📌 '+_nfLm(txt);
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
        replyHtml = '<div class="ig-g-reply">↩ '+esc(m.reply_to.sender_name)+': '+_nfLm(m.reply_to.preview||'')+'</div>';
      }
      var addressedHtml = m.addressed_user_id ? '<div class="ig-g-reply" style="color:#8be9ff">→ адресно</div>' : '';
      var imgHtml = m.image_url ? '<img class="ig-g-img" src="'+escUrlAttr(m.image_url)+'" alt="">' : '';
      var audHtml = m.audio_url ? '<audio class="ig-g-audio" controls playsinline preload="metadata" src="'+escUrlAttr(m.audio_url)+'">Ваш браузер не воспроизводит аудио</audio>' : '';
      var txtHtml = (m.text && String(m.text).trim()) ? '<div class="ig-g-text">'+_nfLm(m.text)+'</div>' : '';
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
    if(!silent) fetch('/community/groups/'+gid+'/mark-read',{method:'POST',credentials:'same-origin'}).catch(function(){});
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
      if(sp) sp.textContent = String(d.likes_count||0);
      btn.textContent = d.liked ? '⭐' : '✩';
      await loadGroupMessages(selectedGroupId, { silent: true });
    }
  }catch(e){}
}

async function deleteGroupMessage(mid){
  if(!selectedGroupId || !mid) return;
  if(!confirm('Удалить это сообщение?')) return;
  try{
    const r = await fetch('/community/groups/' + selectedGroupId + '/messages/' + mid, { method: 'DELETE', credentials: 'same-origin' });
    const d = await r.json().catch(()=>({}));
    if(r.ok && d.ok) await loadGroupMessages(selectedGroupId);
    else showNotification(d.error || 'Не удалось удалить', 'error');
  }catch(e){ showNotification('Ошибка сети', 'error'); }
}

function onGroupChatPickImage(){
  const inp = document.getElementById('groupChatImgInp');
  const file = inp && inp.files && inp.files[0];
  if(!file){
    window.__groupChatImageFile = null;
    return;
  }
  const done = function(f){
    window.__groupChatImageFile = f || file;
    const h = document.getElementById('groupVoiceHint');
    if(h){ h.style.display='inline'; h.textContent='✓ Фото'; h.style.color='#4ade80'; }
  };
  if(window.MAIImageCropper && /^image\//i.test(file.type||'')){
    window.MAIImageCropper.open(file,{aspectRatio:1}).then(function(blob){
      const cropped = new File([blob], 'chat.jpg', {type:'image/jpeg'});
      done(cropped);
    }).catch(function(){
      if(inp) inp.value='';
      window.__groupChatImageFile = null;
    });
    return;
  }
  done(file);
}

function pickGroupVoiceMime(){
  if(typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
  var c = ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/mp4;codecs=mp4a.40.2','audio/aac','audio/ogg;codecs=opus','audio/ogg'];
  for(var i=0;i<c.length;i++){ if(MediaRecorder.isTypeSupported(c[i])) return c[i]; }
  return '';
}

function _groupVoiceFmtSec(sec){
  var s = Math.max(0, Math.floor(sec || 0));
  return String(s).padStart(2, '0');
}

function _groupVoiceHintTextRecording(leftSec){
  return '● Запись… осталось: 00:' + _groupVoiceFmtSec(leftSec);
}

function _groupVoiceSetHint(text, color){
  var h = document.getElementById('groupVoiceHint');
  if(!h) return;
  if(!text){
    h.style.display='none';
    return;
  }
  h.style.display='inline';
  h.textContent=text;
  if(color) h.style.color=color;
}

async function stopGroupVoiceRecordAndWait(){
  var g = window.__groupVoiceRec;
  if(!g || !g.mr || g.mr.state !== 'recording') return;
  await new Promise(function(resolve){
    g._resolveStop = resolve;
    try{ g.mr.stop(); }catch(_){ resolve(); }
  });
}

async function toggleGroupVoiceRecord(){
  var g = window.__groupVoiceRec;
  if(g && g.mr && g.mr.state === 'recording'){
    await stopGroupVoiceRecordAndWait();
    return;
  }
  window.__groupChatAudioBlob = null;
  var mime = pickGroupVoiceMime();
  try{
    var stream = await navigator.mediaDevices.getUserMedia({
      audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}
    });
    var mr;
    try{
      mr = mime ? new MediaRecorder(stream,{mimeType:mime}) : new MediaRecorder(stream);
    }catch(_e){
      mr = new MediaRecorder(stream);
      mime = '';
    }
    var chunks = [];
    mr.ondataavailable = function(e){ if(e.data && e.data.size>0) chunks.push(e.data); };
    var rec = {mr:mr, stream:stream, maxTimer:null, tickTimer:null, startedAt:Date.now(), maxMs:30000, _resolveStop:null};
    var maxMs = rec.maxMs;
    var updateRemain = function(){
      var left = Math.max(0, Math.ceil((rec.maxMs - (Date.now() - rec.startedAt)) / 1000));
      _groupVoiceSetHint(_groupVoiceHintTextRecording(left), '#f87171');
    };
    updateRemain();
    rec.tickTimer = setInterval(updateRemain, 250);
    rec.maxTimer = setTimeout(function(){
      if(rec.mr && rec.mr.state === 'recording'){
        _groupVoiceSetHint('✓ 30 секунд записано — нажмите →', '#4ade80');
        showNotification('Максимум 30 секунд','success');
        rec.mr.stop();
      }
    }, maxMs);
    mr.onstop = function(){
      if(rec.maxTimer){ clearTimeout(rec.maxTimer); rec.maxTimer=null; }
      if(rec.tickTimer){ clearInterval(rec.tickTimer); rec.tickTimer=null; }
      try{ stream.getTracks().forEach(function(t){ t.stop(); }); }catch(_){}
      window.__groupVoiceRec = null;
      var outType = (chunks[0] && chunks[0].type) ? chunks[0].type : (mime || 'audio/webm');
      var blob = new Blob(chunks, {type: outType});
      if(!blob.size){
        window.__groupChatAudioBlob = null;
        showNotification('Пустая запись — проверьте микрофон','error');
      }else{
        window.__groupChatAudioBlob = blob;
      }
      var h = document.getElementById('groupVoiceHint');
      if(h){
        if(window.__groupChatAudioBlob){
          _groupVoiceSetHint('✓ Голосовое готово — нажмите →', '#4ade80');
        }else{
          _groupVoiceSetHint('', '');
        }
      }
      if(typeof rec._resolveStop === 'function'){
        var fn = rec._resolveStop;
        rec._resolveStop = null;
        try{ fn(); }catch(_){}
      }
    };
    window.__groupVoiceRec = rec;
    try{
      mr.start(250);
    }catch(_){
      mr.start();
    }
    _groupVoiceSetHint(_groupVoiceHintTextRecording(30), '#f87171');
  }catch(e){
    showNotification('Нет доступа к микрофону','error');
  }
}

async function sendGroupChatMessage(){
  if(window.__groupVoiceRec && window.__groupVoiceRec.mr && window.__groupVoiceRec.mr.state === 'recording'){
    await stopGroupVoiceRecordAndWait();
  }
  const inp = document.getElementById('groupMsgInput');
  const t = inp && inp.value.trim();
  const imgInp = document.getElementById('groupChatImgInp');
  const file = window.__groupChatImageFile || (imgInp && imgInp.files && imgInp.files[0]);
  const blob = window.__groupChatAudioBlob;
  if(!selectedGroupId) return;
  if(!t && !file && !blob) return;
  const replyId = window.__groupReplyToId || null;
  const addressedSel = document.getElementById('groupAddressedSelect');
  const addressedUserId = addressedSel && addressedSel.value ? parseInt(addressedSel.value, 10) : null;
  const fd = new FormData();
  if(t) fd.append('text', t);
  if(replyId) fd.append('reply_to_id', String(replyId));
  if(addressedUserId) fd.append('addressed_user_id', String(addressedUserId));
  if(file) fd.append('image', file);
  if(blob){
    var mt = blob.type || 'audio/webm';
    var ext = 'webm';
    if(mt.indexOf('mp4')>=0 || mt.indexOf('m4a')>=0 || mt.indexOf('aac')>=0 || mt.indexOf('mp4a')>=0) ext = 'mp4';
    else if(mt.indexOf('ogg')>=0) ext = 'ogg';
    fd.append('audio', new File([blob], 'voice.'+ext, {type: mt}));
  }
  if(inp) inp.value = '';
  if(imgInp) imgInp.value = '';
  window.__groupChatImageFile = null;
  window.__groupChatAudioBlob = null;
  var vh = document.getElementById('groupVoiceHint');
  if(vh) vh.style.display = 'none';
  clearGroupReply();
  if(_typingPingTimer){ clearTimeout(_typingPingTimer); _typingPingTimer = null; }
  const useMultipart = !!(file || blob);
  try{
    const r = await fetch('/community/groups/' + selectedGroupId + '/message', {
      method: 'POST',
      credentials: 'same-origin',
      headers: useMultipart ? {} : { 'Content-Type': 'application/json' },
      body: useMultipart ? fd : JSON.stringify({ text: t || '', reply_to_id: replyId || undefined, addressed_user_id: addressedUserId || undefined })
    });
    if(r.ok){
      await loadGroupMessages(selectedGroupId, { forceBottom: true });
    }else if(r.status===429){
      const d = await r.json().catch(()=>({}));
      showNotification('Подождите '+ (d.wait_sec||'') +' сек. (медленный режим)','error');
    }else{
      const d = await r.json().catch(()=>({}));
      showNotification(d.error || d.detail || ('Ошибка '+r.status),'error');
    }
  }catch(e){
    showNotification('Ошибка сети','error');
  }
}

async function createGroupSubmit(){
  const n = document.getElementById('grpNameInp') && document.getElementById('grpNameInp').value.trim();
  if(!n || n.length < 2){ showNotification('Введите название группы (от 2 символов)','error'); return; }
  const desc = (document.getElementById('grpDescInp') && document.getElementById('grpDescInp').value) || '';
  try{
    const r = await fetch('/community/groups/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ name: n, description: desc })
    });
    const d = await r.json().catch(()=>({}));
    const gidRaw = d.id != null ? d.id : d.group_id;
    const gid = Number(gidRaw);
    const okCreated = r.ok && d.ok !== false && Number.isFinite(gid) && gid > 0;
    if(okCreated){
      document.getElementById('grpCreateModal').style.display = 'none';
      showNotification('Группа создана','success');
      window.__groupsJustCreated = true;
      
      await refreshGroupListFromApi();
      window.__groupsJustCreated = false;
      const row = document.querySelector('.ig-g-item[data-gid="'+gid+'"]');
      if(row) selectGroupChat(gid, row);
      return;
    }
    let hint403 = 'Создание группы недоступно: политика в админке «Группы» или тариф (проверьте ADMIN_TG_ID / ADMIN_EMAIL для операторов)';
    if(d.plan){ hint403 += ' · ваш тариф: '+d.plan; }
    const msg = d.error || d.detail || (r.status===403 ? hint403 : r.status===401 ? 'Войдите в аккаунт' : (r.status===500 ? (d.error||'Ошибка сервера при сохранении') : 'Не удалось создать группу'));
    showNotification(msg,'error');
  }catch(e){ showNotification('Ошибка сети','error'); }
}

(function initGroupInputTyping(){
  function wire(){
    const inp = document.getElementById('groupMsgInput');
    if(!inp || inp._typingWired) return;
    inp._typingWired = true;
    inp.addEventListener('input', function(){
      if(!selectedGroupId) return;
      pingGroupTyping();
      if(_typingPingTimer) clearTimeout(_typingPingTimer);
      _typingPingTimer = setTimeout(pingGroupTyping, 2200);
    });
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
})();
