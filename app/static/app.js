function byId(x){return document.getElementById(x)}
let META={server_types:[],locations:[],snapshots:[]}
let CURRENT_SERVERS=[]
let DAILY_MAP={}
let QB_NODES={}
let AUTO_POLICIES={}
let __rowHtmlCache={}
let __qbHtmlCache={}

const toast=(msg)=>{const t=byId('toast');t.textContent=msg;t.classList.remove('hidden');clearTimeout(window.__toastT);window.__toastT=setTimeout(()=>t.classList.add('hidden'),2200)}

function toggleTheme(){const b=document.body;const n=b.dataset.theme==='dark'?'light':'dark';b.dataset.theme=n;localStorage.setItem('theme',n);byId('themeBtn').textContent=n==='dark'?'☀️ 浅色':'🌙 深色'}
function initTheme(){const t=localStorage.getItem('theme')||'dark';document.body.dataset.theme=t;byId('themeBtn').textContent=t==='dark'?'☀️ 浅色':'🌙 深色'}

function renderCards(data){
  const total=data.length,warn=data.filter(x=>x.over_threshold).length
  const used=data.reduce((a,b)=>a+(b.used_tb||0),0).toFixed(2)
  const todaySumBytes=data.reduce((a,b)=>a+(Number(b.today_bytes||0)),0)
  const todaySumTb=(todaySumBytes/1024/1024/1024/1024).toFixed(4)
  const avg=total?(data.reduce((a,b)=>a+(b.ratio||0),0)/total*100).toFixed(1):'0.0'
  byId('cards').innerHTML=`<div class="card"><div class="k">服务器总数</div><div class="v">${total}</div></div>
  <div class="card"><div class="k">超阈值数量</div><div class="v">${warn}</div></div>
  <div class="card"><div class="k">总已用流量(TB)</div><div class="v">${used}</div></div>
  <div class="card"><div class="k">今日流量汇总</div><div class="v">${todaySumTb} TB</div></div>
  <div class="card"><div class="k">平均占比</div><div class="v">${avg}%</div></div>`
}

function renderDailyStats(items){
  const box=byId('dailyStats')
  if(!items?.length){box.textContent='暂无数据';return}
  DAILY_MAP={}
  box.innerHTML=items.map(s=>{
    const daily=(s.daily||[])
    DAILY_MAP[s.id]=daily
    const gb=daily.map(x=>x.bytes/1024/1024/1024)
    const max=Math.max(...gb,1)
    const avg=gb.length?gb.reduce((a,b)=>a+b,0)/gb.length:0
    const today=gb.length?gb[gb.length-1]:0
    const ratio=avg>0?today/avg:1
    const level=ratio>=2?'crit':(ratio>=1.5?'warn':'ok')
    const bars=daily.slice(-7).map(d=>{
      const v=d.bytes/1024/1024/1024
      const h=Math.max(3,Math.round(v/max*28))
      const cls=v>=avg*2?'crit':(v>=avg*1.5?'hot':'')
      const md=(d.date||'').slice(5)
      const tip=`${md}: ${v.toFixed(2)} GB`
      return `<i class='${cls}' style='height:${h}px' data-tip='${tip.replace(/'/g,"&#39;")}'></i>`
    }).join('')
    const badge=level==='ok'?'':`<span class='badge-traffic ${level==='crit'?'badge-crit':'badge-warn'}'>${level==='crit'?'流量较高':'流量偏高'}</span>`
    return `<div class="daily-item"><b>${s.name}</b>${badge}<div class="spark">${bars||'<span class="daily-mini">无最近数据</span>'}</div></div>`
  }).join('')
}

function formatIEC(bytes){
  const n = Number(bytes||0)
  const units = ['B','KiB','MiB','GiB','TiB','PiB']
  let v = Math.abs(n), i = 0
  while(v >= 1024 && i < units.length-1){ v /= 1024; i++ }
  const sign = n < 0 ? '-' : ''
  const dec = i <= 1 ? 0 : 2
  return `${sign}${v.toFixed(dec)} ${units[i]}`
}

function formatIECps(bytesPerSec){
  return `${formatIEC(bytesPerSec)}/s`
}

function formatTB2(bytes){
  return `${(Number(bytes||0)/1024/1024/1024/1024).toFixed(4)} TB`
}

function formatTBPrecise(v){
  const n=Number(v||0)
  return `${n.toFixed(2)} TB`
}

function policyImageLabel(imageId){
  const v=String(imageId||'')
  if(!v) return ''
  const official={
    'debian-12':'Debian 12',
    'ubuntu-24.04':'Ubuntu 24.04',
    'ubuntu-22.04':'Ubuntu 22.04',
  }
  if(official[v]) return official[v]
  const s=(META.snapshots||[]).find(x=>String(x.id)===v)
  if(s) return `${s.name||'快照'}`
  return v
}

function qbCellHtml(q={}){
  return q.enabled
    ? `<div class='qb-line'>
         <span class='qb-col'>↑ ${formatIECps(q.up_speed)}</span>
         <span class='qb-col'>↓ ${formatIECps(q.dl_speed)}</span>
         <span class='qb-col'>任务 ${q.active_torrents||0}/${q.all_torrents||0}</span>
       </div>
       <div class='qb-line daily-mini'>
         <span class='qb-col'>↑ ${formatIEC(q.up_total)}</span>
         <span class='qb-col'>↓ ${formatIEC(q.dl_total)}</span>
         <span class='qb-col'>${q.connection_status||'unknown'}</span>
       </div>`
    : `<span class='daily-mini'>未配置</span>`
}

function rowHtml(r){
  const pct=Math.min(100,(r.ratio||0)*100),warn=r.over_threshold
  const todayPct = ((Number(r.today_bytes||0) / (Number(r.limit_tb||20)*1024*1024*1024*1024)) * 100)
  const anomaly=todayPct>=8 ? 'crit' : (todayPct>=3 ? 'warn' : '')
  const todayCell=`${formatIEC(r.today_bytes)} ${anomaly?`<span class='badge-traffic ${anomaly==='crit'?'badge-crit':'badge-warn'}'>${anomaly==='crit'?'流量较高':'流量偏高'}</span>`:''}`

  const q=r.qb||{}
  const p=r.auto_policy||{}
  const policyOn=!!p.enabled
  const policyLabel=policyOn ? `策略 ${Math.round((Number(p.threshold||0))*100)}% · ${policyImageLabel(p.image_id)}` : '自动策略'
  const policyBtnClass = policyOn ? 'btn action policy-on' : 'btn action policy-off'
  const usedPct = Math.min(100, ((Number(r.used_tb||0) / Number(r.limit_tb||20)) * 100))
  const hue = Math.max(0, 220 - Math.round(usedPct*2.2))
  const usedCell = `<div class="ratio-text">${usedPct.toFixed(1)}%</div><div class="progress progress-mini" title="${usedPct.toFixed(1)}%"><div class="bar" style="width:${usedPct}%;background:hsl(${hue} 85% 50%)"></div></div><div class="daily-mini">${formatTBPrecise(r.used_tb)} / ${formatTBPrecise(r.limit_tb)}</div>`
  const qbCell = qbCellHtml(q)

  const ipText = (r.ip||'').trim()
  const ipCell = ipText ? `<span class='copy-ip' title='点击复制IP' onclick="copyText('${ipText}')">${ipText}</span>` : ''

  return `<tr data-id="${r.id}">
    <td><span title="点击复制ID" onclick="copyText('${r.id}')" style="cursor:pointer">${r.id}</span></td>
    <td><span class='name-wrap'>${r.name}</span><button class='icon-btn' title='修改名称' onclick="renameServer(${r.id}, '${(r.name||'').replace(/'/g,"\\'")}')">✎</button></td>
    <td>${r.server_type || '-'} · ${r.cores||0}C/${r.memory_gb||0}GB/${r.disk_gb||0}GB</td>
    <td>${ipCell}</td>
    <td><span class="badge ${r.status==='running'?'running':'other'}">${r.status}</span></td>
    <td class="qb-cell" data-id="${r.id}">${qbCell}</td>
    <td>${usedCell}</td><td>${todayCell}</td>
    <td><div class="op-row">
      <button class="btn action" onclick="openQBModal(${r.id})">配置qB</button>
      <button class="btn action" onclick="openQBWeb(${r.id}, '${ipText}')">打开qB</button>
      <button class="${policyBtnClass}" onclick="openAutoPolicyModal(${r.id})" title="${policyLabel}">${policyLabel}</button>
      <button class="btn action" onclick="rebootServer(${r.id})">重启</button>
      <button class="btn action" onclick="hardRebootServer(${r.id})">强制重启</button>
      <button class="btn btn-danger action" onclick="openRebuildModal(${r.id})">重建</button>
      <button class="btn btn-danger action" onclick="openDeleteModal(${r.id})">删除</button>
    </div></td>
  </tr>`
}

function patchTableRows(rows){
  const tbody=byId('tb').querySelector('tbody')
  const oldMap={}
  Array.from(tbody.querySelectorAll('tr[data-id]')).forEach(tr=>{ oldMap[tr.getAttribute('data-id')] = tr })

  const frag=document.createDocumentFragment()
  const newCache={}

  for(const r of rows){
    const id=String(r.id)
    const html=rowHtml(r)
    newCache[id]=html
    const oldTr=oldMap[id]

    if(oldTr && __rowHtmlCache[id]===html){
      frag.appendChild(oldTr)
    }else{
      const tmp=document.createElement('tbody')
      tmp.innerHTML=html
      frag.appendChild(tmp.firstElementChild)
    }
  }

  tbody.innerHTML=''
  tbody.appendChild(frag)
  __rowHtmlCache=newCache
}

async function copyText(v){
  const text=String(v)
  let ok=false
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(text)
      ok=true
    }
  }catch(e){ ok=false }

  if(!ok){
    try{
      const ta=document.createElement('textarea')
      ta.value=text
      ta.style.position='fixed'
      ta.style.opacity='0'
      ta.style.pointerEvents='none'
      document.body.appendChild(ta)
      ta.focus(); ta.select()
      ok=document.execCommand('copy')
      document.body.removeChild(ta)
    }catch(e){ ok=false }
  }

  if(ok){
    toast(`已复制: ${text}`)
  }else{
    // Final fallback for Safari/macOS or restricted clipboard contexts
    window.prompt('当前环境限制自动复制，请手动复制：', text)
    toast('已打开手动复制框')
  }
}

function openQBWeb(serverId, ip){
  const sid=String(serverId)
  const conf=QB_NODES?.[sid]||{}
  let url=(conf.url||'').trim()
  if(!url){
    const host=(ip||'').trim()
    if(!host){
      toast('无可用IP，无法打开qB')
      return
    }
    url=`http://${host}:8080`
  }
  if(!/^https?:\/\//i.test(url)) url=`http://${url}`
  window.open(url, '_blank', 'noopener,noreferrer')
}

function typeFamily(name=''){return name.replace(/[0-9].*$/,'')}
function monthlyPriceForType(t,loc){if(!t?.prices?.length) return Number.POSITIVE_INFINITY;const ex=t.prices.find(p=>p.location===loc);const p=ex||t.prices[0];return Number(p?.price_monthly?.gross||999999)}
function stockState(t,loc){const has=(t.prices||[]).some(p=>p.location===loc);if(!has) return '缺货';if((CURRENT_SERVERS||[]).length>=3) return '紧张';return '有货'}

function renderTypeOptions(){
  const loc=byId('c_location').value,cores=Number(byId('f_cores').value||0),mem=Number(byId('f_mem').value||0),fam=byId('f_family').value
  const arr=[...META.server_types].filter(t=>!cores||t.cores>=cores).filter(t=>!mem||t.memory>=mem).filter(t=>!fam||typeFamily(t.name)===fam)
    .sort((a,b)=>typeFamily(a.name)===typeFamily(b.name)?monthlyPriceForType(a,loc)-monthlyPriceForType(b,loc):typeFamily(a.name).localeCompare(typeFamily(b.name)))
  byId('c_type').innerHTML=arr.map(t=>{const p=monthlyPriceForType(t,loc),ps=Number.isFinite(p)?`€${p.toFixed(2)}/月`:'价格未知',st=stockState(t,loc);return `<option value="${t.name}">[${st}] ${t.name} · ${t.cores}C/${t.memory}GB/${t.disk}GB · ${ps}</option>`}).join('')
  showTypePrice()
}

async function loadMeta(showToast=false){
  const r=await fetch('/api/meta'); META=await r.json()
  if(byId('appVersion')) byId('appVersion').textContent = META.app_version || '--'
  byId('c_location').innerHTML=META.locations.map(l=>`<option value="${l.name}">${l.name} (${l.city||''})</option>`).join('')
  const fams=[...new Set(META.server_types.map(t=>typeFamily(t.name)).filter(Boolean))].sort()
  byId('f_family').innerHTML=['<option value="">全部系列</option>'].concat(fams.map(f=>`<option value="${f}">${f}</option>`)).join('')
  const snaps=(META.snapshots||[]).map(s=>`<option value="${s.id}">snapshot#${s.id} - ${s.name||''} (${s.size_gb||0}GB)</option>`)
  byId('c_image').innerHTML=['<option value="debian-12">debian-12 (官方镜像)</option>'].concat(snaps).join('')
  refreshPrimaryIpOptions()
  byId('c_location').onchange=()=>{renderTypeOptions();showTypePrice();refreshPrimaryIpOptions()}
  byId('c_type').onchange=showTypePrice
  byId('f_cores').onchange=renderTypeOptions
  byId('f_mem').onchange=renderTypeOptions
  byId('f_family').onchange=renderTypeOptions
  renderTypeOptions()
  if(showToast) toast('库存已刷新')
}

function showTypePrice(){
  const v=byId('c_type').value,t=META.server_types.find(x=>x.name===v),loc=byId('c_location').value
  let txt='',est='',st=''
  if(t){const p=t.prices?.find(x=>x.location===loc)||t.prices?.[0],state=stockState(t,loc);st=`库存状态：${state}`;if(p?.price_monthly?.gross){const pm=Number(p.price_monthly.gross||0).toFixed(2);txt=`约 €${pm} /月（${p.location}）`;est=`创建前费用预估：月费 €${pm}（不含超额流量）`}}
  byId('typePrice').textContent=txt;byId('costEst').textContent=est
  byId('typeStock').innerHTML=st.replace('有货','<span class="stock-ok">有货</span>').replace('紧张','<span class="stock-warn">紧张</span>').replace('缺货','<span class="stock-bad">缺货</span>')
}

function refreshPrimaryIpOptions(){
  const loc=byId('c_location')?.value || ''
  const p4all=(META.primary_ipv4s||[])
  const p6all=(META.primary_ipv6s||[])
  const p4loc=p4all.filter(p=>!p.location || p.location===loc)
  const p6loc=p6all.filter(p=>!p.location || p.location===loc)
  const p4=p4loc.filter(p=>!p.occupied)
  const p6=p6loc.filter(p=>!p.occupied)

  byId('c_primary_ip').innerHTML = ['<option value="">自动分配IPv4</option>']
    .concat(p4.map(p=>`<option value="${p.id}">${p.ip}${p.location?` · ${p.location}`:''}${p.datacenter?` (${p.datacenter})`:''}</option>`))
    .concat(p4loc.filter(p=>p.occupied).map(p=>`<option value="" disabled>${p.ip}（已占用：${(p.occupied_by||{}).server_name||('server#'+((p.occupied_by||{}).server_id||'?'))}）</option>`))
    .join('')
  if(!p4.length) byId('c_primary_ip').innerHTML += '<option value="" selected disabled>无可用IPv4</option>'
  byId('c_primary_ip').value = ''

  byId('c_primary_ipv6').innerHTML = ['<option value="">自动分配IPv6</option>']
    .concat(p6.map(p=>`<option value="${p.id}">${p.ip}${p.location?` · ${p.location}`:''}${p.datacenter?` (${p.datacenter})`:''}</option>`))
    .concat(p6loc.filter(p=>p.occupied).map(p=>`<option value="" disabled>${p.ip}（已占用：${(p.occupied_by||{}).server_name||('server#'+((p.occupied_by||{}).server_id||'?'))}）</option>`))
    .join('')
  if(!p6.length) byId('c_primary_ipv6').innerHTML += '<option value="" selected disabled>无可用IPv6</option>'
  byId('c_primary_ipv6').value = ''

  const hint=byId('ipLocationHint')
  if(hint){
    const occ4 = p4loc.filter(p=>p.occupied).length
    const occ6 = p6loc.filter(p=>p.occupied).length
    if(!p4.length && !p6.length){
      hint.textContent=`当前机房 ${loc} 无可用Primary IP（IPv4占用 ${occ4} / IPv6占用 ${occ6}），创建时将自动分配IP。`
    }else{
      hint.textContent=`已按机房 ${loc} 过滤：可用 IPv4 ${p4.length} / IPv6 ${p6.length}；占用 IPv4 ${occ4} / IPv6 ${occ6}（占用项不可选）。`
    }
  }
}

function preset(k){if(k==='basic'){byId('f_cores').value='2';byId('f_mem').value='2';byId('f_family').value='cpx'} if(k==='balanced'){byId('f_cores').value='4';byId('f_mem').value='8';byId('f_family').value='cpx'} if(k==='pro'){byId('f_cores').value='8';byId('f_mem').value='16';byId('f_family').value=''} renderTypeOptions()}

let __loadingData=false
async function loadData(showToast=false){
  if(__loadingData) return
  __loadingData=true
  try{
    const kw=((byId('kw')?.value)||'').trim().toLowerCase(),r=await fetch('/api/servers'),data=await r.json(); CURRENT_SERVERS=data
    renderCards(data)
    const f=data.filter(x=>!kw||String(x.name).toLowerCase().includes(kw)||String(x.ip||'').toLowerCase().includes(kw)||String(x.id).includes(kw))
    patchTableRows(f)
    if(showToast) toast('已刷新')
  } finally { __loadingData=false }
}

let __loadingQB=false
async function loadQBRealtime(){
  if(__loadingQB) return
  __loadingQB=true
  try{
    const r=await fetch('/api/qb_realtime')
    const data=await r.json()
    const tbody=byId('tb')?.querySelector('tbody')
    if(!tbody) return
    for(const [sid,q] of Object.entries(data||{})){
      const cell=tbody.querySelector(`td.qb-cell[data-id="${sid}"]`)
      if(!cell) continue
      const html=qbCellHtml(q)
      if(__qbHtmlCache[sid]!==html){
        cell.innerHTML=html
        __qbHtmlCache[sid]=html
      }
    }
  } catch(e){} finally { __loadingQB=false }
}

async function loadDaily(showToast=false){const r=await fetch('/api/daily_stats?days=7');renderDailyStats(await r.json()); if(showToast) toast('统计已刷新')}

async function loadQBNodes(){
  const r=await fetch('/api/qb_nodes')
  QB_NODES = await r.json()
}

async function loadAutoPolicies(){
  const r=await fetch('/api/auto_policies')
  AUTO_POLICIES = await r.json()
}

async function loadSafeMode(){
  const r=await fetch('/api/safe_mode')
  const d=await r.json()
  const on=!!d.safe_mode
  const b=byId('safeModeBtn')
  if(b){
    b.textContent = on ? '🛡️ 安全ON' : '🛡️ 安全OFF'
    b.classList.toggle('safe-on', on)
    b.classList.toggle('safe-off', !on)
  }
}

async function toggleSafeMode(){
  const r0=await fetch('/api/safe_mode'); const d0=await r0.json(); const next=!d0.safe_mode
  if(!confirm(`确认将 SAFE_MODE 切换为 ${next?'ON':'OFF'} ?`)) return
  const r=await fetch(`/api/safe_mode?enabled=${next}`,{method:'PUT'})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'切换失败');return}
  toast(`SAFE_MODE 已切换为 ${next?'ON':'OFF'}`)
  loadSafeMode()
}

async function loadAll(showToast=false){
  await Promise.all([loadMeta(false),loadQBNodes(),loadAutoPolicies(),loadData(false),loadSafeMode()])
  loadQBRealtime()
  // defer heavy chart data to improve first paint smoothness
  setTimeout(()=>loadDaily(false), 0)
  if(showToast) toast('全部数据已刷新')
}

async function renameServer(id, oldName){
  const n=prompt('请输入新的服务器名称：', oldName||`server-${id}`)
  if(!n) return
  const r=await fetch(`/api/server/${id}/name`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({name:n})})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'改名失败');return}
  toast('服务器名称已更新')
  loadData(false)
}

async function pollAction(actionId, label='操作'){
  if(!actionId) return
  for(let i=0;i<20;i++){
    await new Promise(r=>setTimeout(r,1500))
    const rr=await fetch(`/api/action/${actionId}`)
    const a=await rr.json()
    const st=(a?.status||'').toLowerCase()
    if(st==='success'){ toast(`${label}成功`) ; return }
    if(st==='error'){ toast(`${label}失败`) ; return }
  }
  toast(`${label}处理中，请稍后刷新查看`)
}

async function rebootServer(id){
  if(!confirm(`确认重启服务器 ${id} ?`)) return
  const r=await fetch(`/api/server/${id}/reboot`,{method:'POST'})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'重启失败');return}
  toast('重启指令已提交，正在检查结果...')
  const aid=d?.result?.action?.id
  pollAction(aid, `服务器 ${id} 重启`)
}

async function hardRebootServer(id){
  if(!confirm(`确认强制重启服务器 ${id} ?\n将先关机再开机。`)) return
  const r=await fetch(`/api/server/${id}/hard_reboot`,{method:'POST'})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'强制重启失败');return}
  toast('强制重启已提交，正在检查开机动作...')
  const aid=d?.poweron_action?.id
  pollAction(aid, `服务器 ${id} 强制重启`)
}

function openRebuildModal(serverId){
  byId('rebuildModal').classList.remove('hidden')
  byId('rebuild_server_id').value=serverId
  const snaps=(META.snapshots||[])
  const official=[
    {id:'debian-12',name:'官方镜像 Debian 12'},
    {id:'ubuntu-24.04',name:'官方镜像 Ubuntu 24.04'},
    {id:'ubuntu-22.04',name:'官方镜像 Ubuntu 22.04'},
  ]
  const optsOfficial=official.map(s=>`<option value="${s.id}">${s.name}</option>`).join('')
  const optsSnap=snaps.map(s=>`<option value="${s.id}">快照 #${s.id} ${s.name||''} (${s.size_gb||0}GB)</option>`).join('')
  byId('rebuild_snapshot').innerHTML = optsOfficial + optsSnap
}
function closeRebuildModal(){ byId('rebuildModal').classList.add('hidden') }

function openDeleteModal(serverId){
  byId('deleteModal').classList.remove('hidden')
  byId('del_server_id').value=serverId
  byId('del_make_snapshot').checked=false
  byId('del_keep_ipv4').checked=false
  byId('del_keep_ipv6').checked=false
  byId('del_keep_mode').value='safe'
}
function closeDeleteModal(){ byId('deleteModal').classList.add('hidden') }

async function submitDeleteServer(){
  const sid=Number(byId('del_server_id').value)
  const body={
    create_snapshot: !!byId('del_make_snapshot').checked,
    keep_ipv4: !!byId('del_keep_ipv4').checked,
    keep_ipv6: !!byId('del_keep_ipv6').checked,
    keep_mode: (byId('del_keep_mode')?.value || "safe"),
  }
  if(!confirm(`高风险确认：将删除服务器 ${sid}。请确认选项无误。`)) return
  const verify = prompt('请输入 DELETE 确认执行：','')
  if((verify||'').trim().toUpperCase() !== 'DELETE'){ alert('未确认，已取消'); return }
  const r=await fetch(`/api/server/${sid}/delete`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'删除失败');return}
  toast('删除任务已完成')
  closeDeleteModal(); loadAll(false)
}

async function submitRebuild(){
  const sid=Number(byId('rebuild_server_id').value)
  const imageId=byId('rebuild_snapshot').value
  if(!imageId){alert('请先选择镜像或快照');return}
  if(!confirm(`二次确认：将使用 ${imageId} 原地重建服务器 ${sid}（保留IP），继续吗？`)) return
  const verify = prompt('请输入 REBUILD 确认执行：','')
  if((verify||'').trim().toUpperCase() !== 'REBUILD'){ alert('未确认，已取消'); return }
  const r=await fetch(`/api/rebuild/${sid}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({image_id:imageId})})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'重建失败');return}
  toast('重建任务已提交（原地重建，保留IP）')
  closeRebuildModal(); loadAll(false)
}

async function submitFullRebuild(){
  const sid=Number(byId('rebuild_server_id').value)
  const imageId=byId('rebuild_snapshot').value
  if(!imageId){alert('请先选择镜像或快照');return}
  if(!confirm(`高风险确认：将完全重建服务器 ${sid}，丢弃旧IP并使用新IP，继续吗？`)) return
  const verify = prompt('请输入 FULLREBUILD 确认执行：','')
  if((verify||'').trim().toUpperCase() !== 'FULLREBUILD'){ alert('未确认，已取消'); return }
  const r=await fetch(`/api/rebuild_full/${sid}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({image_id:imageId})})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'完全重建失败');return}
  toast('完全重建已提交（将更换IP）')
  closeRebuildModal(); loadAll(false)
}

async function snapshot(id, snapName){
  const e=await fetch(`/api/snapshot_estimate/${id}`),est=await e.json();
  if(!e.ok||!est?.ok){alert('无法获取快照费用预估');return}
  const defaultName=`manual-snap-${id}-${new Date().toISOString().slice(0,19).replace(/[-:T]/g,'')}`
  const name=(snapName||defaultName).trim()
  const msg=`服务器: ${est.server_name}\n磁盘总量: ${Number(est.disk_gb||0).toFixed(2)} GB\n预估快照体积: ${Number(est.estimated_snapshot_size_gb||0).toFixed(2)} GB\n预估月费用: €${Number(est.estimated_monthly_eur||0).toFixed(2)}\n说明: ${est.estimation_note}\n\n快照名称: ${name}\n\n确认创建快照？`
  if(!confirm(msg)) return
  const r=await fetch(`/api/snapshot/${id}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({description:name})})
  const d=await r.json(); if(!r.ok||!d?.ok){alert('快照创建失败');return}
  toast('快照任务已提交'); setTimeout(()=>{loadMeta();loadSnapshotsList()},3000)
}

async function createSnapshotFromManager(){
  const sid=Number(byId('sm_server').value)
  const name=(byId('sm_name').value||'').trim()
  if(!sid){alert('请先选择服务器');return}
  await snapshot(sid, name)
}

function openCreateModal(){byId('createModal').classList.remove('hidden')}
function closeCreateModal(){byId('createModal').classList.add('hidden')}
async function refreshInventory(){await loadMeta(true)}

async function openTGModal(){
  byId('tgModal').classList.remove('hidden')
  try{
    const r=await fetch('/api/config/telegram')
    const d=await r.json()
    byId('tg_chat').value=d.telegram_chat_id||''
    byId('tg_token').value=''
  }catch(e){}
}
function closeTGModal(){ byId('tgModal').classList.add('hidden') }

async function restartService(){
  if(!confirm('确认重启当前服务？约10-30秒恢复。')) return
  const r=await fetch('/api/service/restart',{method:'POST'})
  const d=await r.json()
  if(!r.ok||!d?.ok){ alert(d?.detail||d?.error||'重启触发失败'); return }
  toast('服务重启已触发，稍后请刷新页面')
}

async function saveTGConfig(restartAfter=false){
  const body={telegram_bot_token:byId('tg_token').value.trim(), telegram_chat_id:byId('tg_chat').value.trim()}
  if(!body.telegram_bot_token || !body.telegram_chat_id){ alert('请填写 Bot Token 和 Chat ID'); return }
  const r=await fetch('/api/config/telegram',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json()
  if(!r.ok||!d?.ok){ alert(d?.detail||d?.error||'保存失败'); return }
  toast('TG配置已保存')
  if(restartAfter){
    await restartService()
  }
  closeTGModal()
}
async function submitCreate(){const v4=byId('c_primary_ip').value,v6=byId('c_primary_ipv6').value;const body={name:byId('c_name').value||`srv-${Date.now()}`,server_type:byId('c_type').value,location:byId('c_location').value,image:byId('c_image').value,primary_ip_id: (v4 && v4!=='__none__') ? Number(v4) : null,primary_ipv6_id: (v6 && v6!=='__none__') ? Number(v6) : null};const r=await fetch('/api/create_server',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}),d=await r.json();if(!r.ok){alert(d?.detail||d?.error||'创建失败');return}toast('创建任务已提交');closeCreateModal();loadData()}

function openSnapshotsModal(){
  byId('snapshotsModal').classList.remove('hidden')
  const opts=(CURRENT_SERVERS||[]).map(s=>`<option value="${s.id}">${s.name} (#${s.id})</option>`).join('')
  byId('sm_server').innerHTML=opts
  if(!byId('sm_name').value && CURRENT_SERVERS?.length){
    const sid=CURRENT_SERVERS[0].id
    byId('sm_name').value=`manual-snap-${sid}-${new Date().toISOString().slice(0,19).replace(/[-:T]/g,'')}`
  }
  loadSnapshotsList()
}
function closeSnapshotsModal(){byId('snapshotsModal').classList.add('hidden')}
async function loadSnapshotsList(showToast=false){
  const r=await fetch('/api/meta'),m=await r.json(),arr=m.snapshots||[]
  if(!arr.length){byId('snapshotsList').innerHTML='暂无快照';return}
  byId('snapshotsList').innerHTML=arr.map(s=>`<div class="daily-item"><b>#${s.id}</b> ${s.name||''} · ${s.size_gb||0}GB
    <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn small" onclick="renameSnapshot(${s.id}, '${(s.name||'').replace(/'/g,"\\'")}')">重命名</button>
      <button class="btn small" onclick="deleteSnapshot(${s.id})">删除</button>
    </div>
  </div>`).join('')
  if(showToast) toast('快照列表已刷新')
}

async function renameSnapshot(id, oldName){
  const n=prompt('新快照名称：', oldName||`snapshot-${id}`)
  if(!n) return
  const r=await fetch(`/api/snapshot/${id}`,{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({description:n})})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'重命名失败');return}
  toast('快照已重命名')
  loadSnapshotsList(); loadMeta()
}

async function deleteSnapshot(id){
  if(!confirm(`确认删除快照 #${id} ?`)) return
  const r=await fetch(`/api/snapshot/${id}`,{method:'DELETE'}),d=await r.json()
  if(!r.ok||!d?.ok){alert('删除失败');return}
  toast('快照已删除')
  loadSnapshotsList(); loadMeta()
}

function openQBModal(serverId){
  byId('qbModal').classList.remove('hidden')
  byId('qb_server_id').value = serverId
  const n = QB_NODES[String(serverId)] || {}
  byId('qb_url').value = n.url || ''
  byId('qb_user').value = n.username || ''
  byId('qb_pass').value = n.password || ''
}
function closeQBModal(){ byId('qbModal').classList.add('hidden') }

function openAutoPolicyModal(serverId){
  byId('autoPolicyModal').classList.remove('hidden')
  byId('ap_server_id').value = serverId
  const p = AUTO_POLICIES[String(serverId)] || {}
  byId('ap_enabled').checked = !!p.enabled
  byId('ap_threshold').value = p.threshold ?? '0.95'
  const official=[
    {id:'debian-12',name:'官方镜像 Debian 12'},
    {id:'ubuntu-24.04',name:'官方镜像 Ubuntu 24.04'},
    {id:'ubuntu-22.04',name:'官方镜像 Ubuntu 22.04'},
  ]
  const snaps=(META.snapshots||[])
  const optsOfficial=official.map(s=>`<option value="${s.id}">${s.name}</option>`).join('')
  const optsSnap=snaps.map(s=>`<option value="${s.id}">快照 #${s.id} ${s.name||''}</option>`).join('')
  byId('ap_image_id').innerHTML = '<option value="">请选择镜像/快照</option>' + optsOfficial + optsSnap
  byId('ap_image_id').value = p.image_id ? String(p.image_id) : ''
}
function closeAutoPolicyModal(){ byId('autoPolicyModal').classList.add('hidden') }

async function saveAutoPolicy(){
  const body={
    server_id:Number(byId('ap_server_id').value),
    enabled:!!byId('ap_enabled').checked,
    threshold:Number(byId('ap_threshold').value||0),
    image_id: byId('ap_image_id').value ? byId('ap_image_id').value : null,
  }
  if(body.enabled && (!body.threshold || body.threshold<=0 || body.threshold>1)){ alert('阈值需在 0~1 之间，例如 0.95'); return }
  if(body.enabled && !body.image_id){ alert('开启自动策略时必须选择用于重建的镜像或快照'); return }
  const r=await fetch('/api/auto_policy',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json()
  if(!r.ok||!d?.ok){ alert(d?.detail||d?.error||'保存失败'); return }
  toast('自动策略已保存')
  closeAutoPolicyModal(); loadAll(false)
}

async function deleteAutoPolicy(){
  const sid=Number(byId('ap_server_id').value)
  if(!confirm(`确认删除服务器 ${sid} 的自动策略？`)) return
  const r=await fetch(`/api/auto_policy/${sid}`,{method:'DELETE'})
  const d=await r.json()
  if(!r.ok||!d?.ok){ alert(d?.detail||d?.error||'删除失败'); return }
  toast('自动策略已删除')
  closeAutoPolicyModal(); loadAll(false)
}

async function saveQBNode(){
  const body={
    server_id:Number(byId('qb_server_id').value),
    url:byId('qb_url').value.trim(),
    username:byId('qb_user').value.trim(),
    password:byId('qb_pass').value,
  }
  const r=await fetch('/api/qb_node',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.error||d?.detail||'保存失败');return}
  toast('qB配置已保存并测试通过')
  closeQBModal(); loadAll(false)
}

async function deleteQBNode(){
  const sid=Number(byId('qb_server_id').value)
  if(!confirm(`确认删除服务器 ${sid} 的qB配置？`)) return
  const r=await fetch(`/api/qb_node/${sid}`,{method:'DELETE'})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert('删除失败');return}
  toast('qB配置已删除')
  closeQBModal(); loadAll(false)
}

if(byId('kw')) byId('kw').addEventListener('input',()=>loadData(false))
initTheme();
loadAll(false)
// layered refresh: qB high-frequency, others lower frequency
setInterval(()=>{ if(!document.hidden) loadQBRealtime() }, 3000)
setInterval(()=>{ if(!document.hidden) loadData(false) }, 15000)
setInterval(()=>{ if(!document.hidden) loadDaily(false) }, 60000)
setInterval(()=>{ if(!document.hidden) loadMeta(false) }, 300000)
