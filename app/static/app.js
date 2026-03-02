function byId(x){return document.getElementById(x)}

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
    <td>
      <div class="progress"><div class="bar ${warn?'warn':''}" style="width:${pct}%"></div></div>
      <div class="ratio-text">${pct.toFixed(1)}%</div>
    </td>
    <td><button class="btn-danger" onclick="rotate(${r.id})">重建</button></td>
  </tr>`
}

async function loadData(){
  const kw=(byId('kw')?.value||'').trim().toLowerCase()
  const res=await fetch('/api/servers')
  const data=await res.json()
  renderCards(data)
  const filtered=data.filter(r=>{
    if(!kw) return true
    return String(r.name).toLowerCase().includes(kw) || String(r.ip||'').toLowerCase().includes(kw)
  })
  const tb=document.querySelector('#tb tbody')
  tb.innerHTML=filtered.map(rowHtml).join('')
}

async function rotate(id){
  if(!confirm('确认重建该服务器？此操作会删除旧机。')) return
  const res=await fetch(`/api/rotate/${id}`,{method:'POST'})
  const data=await res.json()
  alert(JSON.stringify(data))
  loadData()
}

byId('kw')?.addEventListener('input',()=>loadData())
loadData()
setInterval(loadData, 30000)
