function byId(x){return document.getElementById(x)}
let META={server_types:[],locations:[],snapshots:[]}
let CURRENT_SERVERS=[]

function toggleTheme(){
  const b=document.body
  const next=b.dataset.theme==='dark'?'light':'dark'
  b.dataset.theme=next
  localStorage.setItem('theme',next)
  byId('themeBtn').textContent= next==='dark'?'☀️ 浅色':'🌙 深色'
}

function initTheme(){
  const t=localStorage.getItem('theme')||'dark'
  document.body.dataset.theme=t
  byId('themeBtn').textContent= t==='dark'?'☀️ 浅色':'🌙 深色'
}

function renderCards(data){
  const total=data.length
  const warn=data.filter(x=>x.over_threshold).length
  const used=data.reduce((a,b)=>a+(b.used_tb||0),0).toFixed(2)
  const avg= total? (data.reduce((a,b)=>a+(b.ratio||0),0)/total*100).toFixed(1):'0.0'
  byId('cards').innerHTML=`
    <div class="card"><div class="k">服务器总数</div><div class="v">${total}</div></div>
    <div class="card"><div class="k">超阈值数量</div><div class="v">${warn}</div></div>
    <div class="card"><div class="k">总已用流量(TB)</div><div class="v">${used}</div></div>
    <div class="card"><div class="k">平均占比</div><div class="v">${avg}%</div></div>
  `
}

function rowHtml(r){
  const pct=Math.min(100,(r.ratio||0)*100)
  const warn=r.over_threshold
  return `<tr>
    <td>${r.id}</td>
    <td>${r.name}</td>
    <td>${r.ip||''}</td>
    <td><span class="badge ${r.status==='running'?'running':'other'}">${r.status}</span></td>
    <td>${r.used_tb}</td>
    <td>${r.limit_tb}</td>
    <td><div class="progress"><div class="bar ${warn?'warn':''}" style="width:${pct}%"></div></div><div class="ratio-text">${pct.toFixed(1)}%</div></td>
    <td>
      <button class="btn-danger" onclick="rotate(${r.id})">重建</button>
      <button onclick="snapshot(${r.id})">快照</button>
    </td>
  </tr>`
}

function monthlyPriceForType(t, loc){
  if(!t?.prices?.length) return Number.POSITIVE_INFINITY
  const exact=t.prices.find(p=>p.location===loc)
  const pick=exact || t.prices[0]
  return Number(pick?.price_monthly?.gross || 999999)
}

function stockBadge(t, loc){
  const hasLoc = (t.prices||[]).some(p=>p.location===loc)
  if(!hasLoc) return '缺货'
  if((CURRENT_SERVERS||[]).length>=3) return '紧张'
  return '有货'
}

function renderTypeOptions(){
  const loc=byId('c_location').value
  const cores=Number(byId('f_cores').value || 0)
  const mem=Number(byId('f_mem').value || 0)
  const arr=[...META.server_types]
    .filter(t=>!cores || (t.cores||0)>=cores)
    .filter(t=>!mem || (t.memory||0)>=mem)
    .sort((a,b)=>monthlyPriceForType(a,loc)-monthlyPriceForType(b,loc))

  const sel=byId('c_type')
  sel.innerHTML=arr.map(t=>{
    const p=monthlyPriceForType(t,loc)
    const ps=Number.isFinite(p)?`€${p.toFixed(2)}/月`:'价格未知'
    const st=stockBadge(t,loc)
    return `<option value="${t.name}">[${st}] ${t.name} · ${t.cores}C/${t.memory}GB/${t.disk}GB · ${ps}</option>`
  }).join('')
  showTypePrice()
}

async function loadMeta(){
  const res=await fetch('/api/meta')
  META=await res.json()

  const locSel=byId('c_location')
  locSel.innerHTML=META.locations.map(l=>`<option value="${l.name}">${l.name} (${l.city||''})</option>`).join('')

  const imgSel=byId('c_image')
  const basic=[`<option value="debian-12">debian-12 (官方镜像)</option>`]
  const snaps=META.snapshots.map(s=>`<option value="${s.id}">snapshot#${s.id} - ${s.name||''}</option>`)
  imgSel.innerHTML=basic.concat(snaps).join('')

  byId('c_location').addEventListener('change',()=>{renderTypeOptions(); showTypePrice()})
  byId('c_type').addEventListener('change',showTypePrice)
  byId('f_cores').addEventListener('change',renderTypeOptions)
  byId('f_mem').addEventListener('change',renderTypeOptions)

  renderTypeOptions()
}

function showTypePrice(){
  const v=byId('c_type').value
  const t=(META.server_types||[]).find(x=>x.name===v)
  const loc=byId('c_location').value
  let txt=''
  let est=''
  if(t){
    const p=t.prices?.find(x=>x.location===loc) || t.prices?.[0]
    if(p?.price_monthly?.gross){
      txt=`约 €${p.price_monthly.gross} /月（${p.location}）`
      est=`创建前费用预估：月费 €${p.price_monthly.gross}（不含超额流量）`
    }
    byId('typeStock').textContent=`库存状态：${stockBadge(t, loc)}`
  }
  byId('typePrice').textContent=txt
  byId('costEst').textContent=est
}

function preset(kind){
  if(kind==='basic'){ byId('f_cores').value='2'; byId('f_mem').value='2' }
  if(kind==='balanced'){ byId('f_cores').value='4'; byId('f_mem').value='8' }
  if(kind==='pro'){ byId('f_cores').value='8'; byId('f_mem').value='16' }
  renderTypeOptions()
}

async function loadData(){
  const kw=(byId('kw')?.value||'').trim().toLowerCase()
  const res=await fetch('/api/servers')
  const data=await res.json()
  CURRENT_SERVERS=data
  renderCards(data)
  const filtered=data.filter(r=>!kw || String(r.name).toLowerCase().includes(kw) || String(r.ip||'').toLowerCase().includes(kw))
  const tb=document.querySelector('#tb tbody')
  tb.innerHTML=filtered.map(rowHtml).join('')
}

async function rotate(id){
  if(!confirm('确认重建该服务器？此操作会删除旧机。')) return
  const res=await fetch(`/api/rotate/${id}`,{method:'POST'})
  const data=await res.json()
  if(!res.ok){ alert(data?.detail || data?.error || '重建失败'); return }
  alert('重建任务已提交')
  loadData();loadMeta()
}

async function snapshot(id){
  const eRes=await fetch(`/api/snapshot_estimate/${id}`)
  const est=await eRes.json()
  if(!eRes.ok || !est?.ok){
    alert('无法获取快照费用预估：'+(est?.detail||est?.error||'unknown'))
    return
  }
  const msg=`服务器: ${est.server_name}\n预估快照体积: ${est.disk_gb} GB\n预估月费用: €${est.estimated_monthly_eur} (按 €${est.snapshot_price_per_gb}/GB 估算)\n\n确认创建快照？`
  if(!confirm(msg)) return

  const res=await fetch(`/api/snapshot/${id}`,{method:'POST'})
  const data=await res.json()
  if(!res.ok || !data?.ok){
    alert('快照创建失败：'+(data?.detail||data?.error||'unknown'))
    return
  }
  alert('快照任务已提交成功')
  setTimeout(loadMeta, 5000)
}

function openCreateModal(){ byId('createModal').classList.remove('hidden') }
function closeCreateModal(){ byId('createModal').classList.add('hidden') }

async function refreshInventory(){
  await loadMeta()
  await loadData()
  alert('库存与型号信息已刷新')
}

async function submitCreate(){
  const body={
    name: byId('c_name').value || `srv-${Date.now()}`,
    server_type: byId('c_type').value,
    location: byId('c_location').value,
    image: byId('c_image').value,
  }
  const res=await fetch('/api/create_server',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})
  alert(JSON.stringify(await res.json()))
  closeCreateModal(); loadData()
}

byId('kw')?.addEventListener('input',()=>loadData())
initTheme();
loadMeta();
loadData();
setInterval(loadData, 30000)
