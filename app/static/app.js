function byId(x){return document.getElementById(x)}
let META={server_types:[],locations:[],snapshots:[]}
let CURRENT_SERVERS=[]
let DAILY_MAP={}
let QB_NODES={}

const toast=(msg)=>{const t=byId('toast');t.textContent=msg;t.classList.remove('hidden');clearTimeout(window.__toastT);window.__toastT=setTimeout(()=>t.classList.add('hidden'),2200)}

function toggleTheme(){const b=document.body;const n=b.dataset.theme==='dark'?'light':'dark';b.dataset.theme=n;localStorage.setItem('theme',n);byId('themeBtn').textContent=n==='dark'?'☀️ 浅色':'🌙 深色'}
function initTheme(){const t=localStorage.getItem('theme')||'dark';document.body.dataset.theme=t;byId('themeBtn').textContent=t==='dark'?'☀️ 浅色':'🌙 深色'}

function renderCards(data){
  const total=data.length,warn=data.filter(x=>x.over_threshold).length
  const used=data.reduce((a,b)=>a+(b.used_tb||0),0).toFixed(2)
  const avg=total?(data.reduce((a,b)=>a+(b.ratio||0),0)/total*100).toFixed(1):'0.0'
  byId('cards').innerHTML=`<div class="card"><div class="k">服务器总数</div><div class="v">${total}</div></div>
  <div class="card"><div class="k">超阈值数量</div><div class="v">${warn}</div></div>
  <div class="card"><div class="k">总已用流量(TB)</div><div class="v">${used}</div></div>
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
    const badge=level==='ok'?'':`<span class='badge-traffic ${level==='crit'?'badge-crit':'badge-warn'}'>${level==='crit'?'异常峰值':'高于均值'}</span>`
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
  let s=n.toFixed(8)
  s=s.replace(/\.0+$/,'').replace(/(\.\d*?)0+$/,'$1')
  return `${s} TB`
}

function rowHtml(r){
  const pct=Math.min(100,(r.ratio||0)*100),warn=r.over_threshold
  const daily=DAILY_MAP[r.id]||[]
  const gb=daily.map(x=>x.bytes/1024/1024/1024)
  const avg=gb.length?gb.reduce((a,b)=>a+b,0)/gb.length:0
  const today=Number(r.today_gb||0)
  const anomaly=avg>0 && today>=avg*2 ? 'crit' : (avg>0 && today>=avg*1.5 ? 'warn' : '')
  const todayCell=`${formatIEC(r.today_bytes)} (${formatTB2(r.today_bytes)}) ${anomaly?`<span class='badge-traffic ${anomaly==='crit'?'badge-crit':'badge-warn'}'>${anomaly==='crit'?'异常':'偏高'}</span>`:''}`

  const q=r.qb||{}
  const qbCell = q.enabled
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

  return `<tr>
    <td><span title="点击复制ID" onclick="copyText('${r.id}')" style="cursor:pointer">${r.id}</span></td>
    <td>${r.name}</td>
    <td>${r.server_type || '-'} · ${r.cores||0}C/${r.memory_gb||0}GB/${r.disk_gb||0}GB</td>
    <td>${r.ip||''}</td>
    <td><span class="badge ${r.status==='running'?'running':'other'}">${r.status}</span></td>
    <td>${qbCell}</td>
    <td>${formatIEC(r.used_bytes)} (${formatTBPrecise(r.used_tb)})</td><td>${todayCell}</td><td>${formatTBPrecise(r.limit_tb)}</td>
    <td><div class="progress"><div class="bar ${warn?'warn':''}" style="width:${pct}%"></div></div><div class="ratio-text">${pct.toFixed(1)}%</div></td>
    <td>
      <button class="btn action" onclick="openQBModal(${r.id})">配置qB</button>
      <button class="btn action" onclick="renameServer(${r.id}, '${(r.name||'').replace(/'/g,"\\'")}')">改名</button>
      <button class="btn action" onclick="rebootServer(${r.id})">重启</button>
      <button class="btn action" onclick="hardRebootServer(${r.id})">强制重启</button>
      <button class="btn btn-danger action" onclick="openRebuildModal(${r.id})">重建</button>
      <button class="btn snapshot action" onclick="snapshot(${r.id})">创建快照</button>
    </td>
  </tr>`
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
  byId('c_location').innerHTML=META.locations.map(l=>`<option value="${l.name}">${l.name} (${l.city||''})</option>`).join('')
  const fams=[...new Set(META.server_types.map(t=>typeFamily(t.name)).filter(Boolean))].sort()
  byId('f_family').innerHTML=['<option value="">全部系列</option>'].concat(fams.map(f=>`<option value="${f}">${f}</option>`)).join('')
  const snaps=(META.snapshots||[]).map(s=>`<option value="${s.id}">snapshot#${s.id} - ${s.name||''} (${s.size_gb||0}GB)</option>`)
  byId('c_image').innerHTML=['<option value="debian-12">debian-12 (官方镜像)</option>'].concat(snaps).join('')
  byId('c_location').onchange=()=>{renderTypeOptions();showTypePrice()}
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

function preset(k){if(k==='basic'){byId('f_cores').value='2';byId('f_mem').value='2';byId('f_family').value='cpx'} if(k==='balanced'){byId('f_cores').value='4';byId('f_mem').value='8';byId('f_family').value='cpx'} if(k==='pro'){byId('f_cores').value='8';byId('f_mem').value='16';byId('f_family').value=''} renderTypeOptions()}

async function loadData(showToast=false){
  const kw=(byId('kw').value||'').trim().toLowerCase(),r=await fetch('/api/servers'),data=await r.json(); CURRENT_SERVERS=data
  renderCards(data)
  const f=data.filter(x=>!kw||String(x.name).toLowerCase().includes(kw)||String(x.ip||'').toLowerCase().includes(kw)||String(x.id).includes(kw))
  byId('tb').querySelector('tbody').innerHTML=f.map(rowHtml).join('')
  if(showToast) toast('已刷新')
}
async function loadDaily(showToast=false){const r=await fetch('/api/daily_stats?days=7');renderDailyStats(await r.json()); if(showToast) toast('统计已刷新')}

async function loadQBNodes(){
  const r=await fetch('/api/qb_nodes')
  QB_NODES = await r.json()
}

async function loadAll(showToast=false){await Promise.all([loadMeta(false),loadQBNodes(),loadData(false),loadDaily(false)]); if(showToast) toast('全部数据已刷新')}

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
  byId('rebuild_snapshot').innerHTML = snaps.length
    ? snaps.map(s=>`<option value="${s.id}">#${s.id} ${s.name||''} (${s.size_gb||0}GB)</option>`).join('')
    : '<option value="">暂无可用快照</option>'
}
function closeRebuildModal(){ byId('rebuildModal').classList.add('hidden') }

async function submitRebuild(){
  const sid=Number(byId('rebuild_server_id').value)
  const imageId=Number(byId('rebuild_snapshot').value)
  if(!imageId){alert('请先选择已有快照');return}
  if(!confirm(`二次确认：将使用快照 #${imageId} 原地重建服务器 ${sid}（保留IP），继续吗？`)) return
  const verify = prompt('请输入 REBUILD 确认执行：','')
  if((verify||'').trim().toUpperCase() !== 'REBUILD'){ alert('未确认，已取消'); return }
  const r=await fetch(`/api/rebuild/${sid}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({image_id:imageId})})
  const d=await r.json()
  if(!r.ok||!d?.ok){alert(d?.detail||d?.error||'重建失败');return}
  toast('重建任务已提交（原地重建，保留IP）')
  closeRebuildModal(); loadAll(false)
}

async function snapshot(id){
  const e=await fetch(`/api/snapshot_estimate/${id}`),est=await e.json();
  if(!e.ok||!est?.ok){alert('无法获取快照费用预估');return}
  const defaultName=`manual-snap-${id}-${new Date().toISOString().slice(0,19).replace(/[-:T]/g,'')}`
  const snapName=prompt('请输入快照名称：', defaultName)
  if(!snapName) return
  const msg=`服务器: ${est.server_name}\n磁盘总量: ${Number(est.disk_gb||0).toFixed(2)} GB\n预估快照体积: ${Number(est.estimated_snapshot_size_gb||0).toFixed(2)} GB\n预估月费用: €${Number(est.estimated_monthly_eur||0).toFixed(2)}\n说明: ${est.estimation_note}\n\n快照名称: ${snapName}\n\n确认创建快照？`
  if(!confirm(msg)) return
  const r=await fetch(`/api/snapshot/${id}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({description:snapName})})
  const d=await r.json(); if(!r.ok||!d?.ok){alert('快照创建失败');return}
  toast('快照任务已提交'); setTimeout(()=>{loadMeta();loadSnapshotsList()},3000)
}

function openCreateModal(){byId('createModal').classList.remove('hidden')}
function closeCreateModal(){byId('createModal').classList.add('hidden')}
async function refreshInventory(){await loadMeta(true)}
async function submitCreate(){const body={name:byId('c_name').value||`srv-${Date.now()}`,server_type:byId('c_type').value,location:byId('c_location').value,image:byId('c_image').value};const r=await fetch('/api/create_server',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}),d=await r.json();if(!r.ok){alert(d?.detail||d?.error||'创建失败');return}toast('创建任务已提交');closeCreateModal();loadData()}

function openSnapshotsModal(){byId('snapshotsModal').classList.remove('hidden');loadSnapshotsList()}
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

byId('kw').addEventListener('input',()=>loadData(false))
initTheme(); loadAll(false); setInterval(()=>loadData(false),5000)
