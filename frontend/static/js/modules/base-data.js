import { apiFetch as requestJson, apiText as requestText, ApiError } from './api-client.js';

let pageRoot = null;
let lifecycleController = null;
let mounted = false;
const $ = (id) => pageRoot?.querySelector(`#${CSS.escape(id)}`) || document.getElementById(id);

function initialState() { return {
  tab:'airports', page:0, limit:30, total:0, items:[],
  selected:null, detail:null, detailError:null,
  me:null, editing:false, creating:false, editorDirty:false,
  aircraft:[], resources:[], requirements:[], lookupsLoaded:false,
  regionOptions:null,
  filters:{
    airports:{ q:'', roles:[], regions:[] },
    missions:{ q:'' },
    aircraft_types:{ q:'' },
    resource_types:{ q:'' },
  },
  listSeq:0, detailSeq:0,
}; }
const state = initialState();
const refs = {};

function bindRefs(){ Object.assign(refs, { tabs:$('baseDataTabs'), search:$('baseDataSearch'), searchBtn:$('baseDataSearchButton'), searchClear:$('baseDataSearchClear'), chips:$('baseDataFilterChips'), popover:$('baseDataFilterPopover'), title:$('baseDataTableTitle'), count:$('baseDataCount'), wrap:$('baseDataTableWrap'), prev:$('baseDataPrev'), next:$('baseDataNext'), pageInfo:$('baseDataPageInfo'), detailTitle:$('baseDataDetailTitle'), detail:$('baseDataDetailBody'), edit:$('baseDataEditButton'), del:$('baseDataDeleteButton'), add:$('baseDataAddButton'), importBtn:$('baseDataImportButton'), exportBtn:$('baseDataExportButton'), msg:$('baseDataMessage'), importModal:$('baseDataImportModal'), importDataset:$('importDataset'), importFormat:$('importFormat'), importFile:$('importFile'), importMsg:$('importMessage'), importCancel:$('importCancel'), importConfirm:$('importConfirm'), confirmModal:$('baseDataConfirmModal'), confirmBody:$('baseDataConfirmBody'), confirmCancel:$('baseDataConfirmCancel'), confirmAction:$('baseDataConfirmAction') }); }
function wasAborted(error){ return error?.name==='AbortError'||error?.body?.name==='AbortError'; }
async function apiFetch(path,options={}){try{return await requestJson(path,{...options,signal:options.signal||lifecycleController?.signal});}catch(error){if(!mounted&&wasAborted(error))return new Promise(()=>{});throw error;}}
async function apiText(path,options={}){try{return await requestText(path,{...options,signal:options.signal||lifecycleController?.signal});}catch(error){if(!mounted&&wasAborted(error))return new Promise(()=>{});throw error;}}
const TAB_META = {
  airports:{title:'机场基础库', singular:'机场', empty:'选择一条机场查看详细信息', searchPlaceholder:'搜索机场编号或名称', endpoint:'/api/airports'},
  missions:{title:'任务库', singular:'任务', empty:'选择一条任务查看详细信息', searchPlaceholder:'搜索任务编号或名称', endpoint:'/api/missions'},
  aircraft_types:{title:'机型基础库', singular:'机型', empty:'选择一条机型查看详细信息', searchPlaceholder:'搜索机型编号或名称', endpoint:'/api/aircraft-types'},
  resource_types:{title:'保障资源类型', singular:'资源类型', empty:'选择一条资源类型查看详细信息', searchPlaceholder:'搜索资源编号或名称', endpoint:'/api/resource-types'},
};
const ROLE_OPTIONS = [['military','军用'],['joint','军民合用'],['civil','民用']];
const roleLabel = (v) => (ROLE_OPTIONS.find(([k])=>k===v)||[v,v])[1];
const esc = (v) => String(v ?? '—').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const val = (v) => v === null || v === undefined ? '' : String(v);
const num = (v) => v === '' || v === null || v === undefined ? null : Number(v);
const int = (v) => v === '' || v === null || v === undefined ? null : Number.parseInt(v,10);
const writable = () => state.me?.permissions?.includes('catalog.write');
function showMessage(text,type='info'){ if(!mounted||!refs.msg)return; refs.msg.textContent=text; refs.msg.className=`workspace-message ${type}`; refs.msg.classList.remove('hidden'); clearTimeout(showMessage.t); showMessage.t=setTimeout(()=>refs.msg?.classList.add('hidden'),4200); }
function fieldError(error){ return error instanceof ApiError ? `${error.message}${error.field?`（${error.field}）`:''}` : '操作失败'; }
function setModal(el,open){ el.classList.toggle('open',open); el.setAttribute('aria-hidden',open?'false':'true'); }
function confirmAction(text, label='确认'){ return new Promise(resolve=>{ refs.confirmBody.innerHTML=`<div class="inline-message warning">${esc(text)}</div>`; refs.confirmAction.textContent=label; setModal(refs.confirmModal,true); const done=(v)=>{setModal(refs.confirmModal,false); refs.confirmAction.onclick=null; refs.confirmCancel.onclick=null; resolve(v)}; refs.confirmAction.onclick=()=>done(true); refs.confirmCancel.onclick=()=>done(false); }); }
function downloadBlob(name,text,type){ const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([text],{type})); a.download=name; document.body.append(a); a.click(); URL.revokeObjectURL(a.href); a.remove(); }
function bindEditorDirty(){refs.detail.querySelectorAll('input,select,textarea').forEach(el=>{const mark=()=>{state.editorDirty=true};el.addEventListener('input',mark);el.addEventListener('change',mark)})}
async function canLeaveEditor(){if(!(state.editing&&state.editorDirty))return true;return confirmAction('当前基础数据表单有未保存修改。继续会放弃这些编辑。','放弃修改')}

/* ---- applied query state: the ONLY source for list requests ---- */
function appliedFilters(){ return state.filters[state.tab]; }
function roles(){ return state.filters.airports.roles; }
function regions(){ return state.filters.airports.regions; }
function hasColumnFilters(){ return roles().length>0 || regions().length>0; }
function updateSearchClear(){ refs.searchClear.classList.toggle('hidden',!refs.search.value); }
function syncToolbarFromFilters(){ refs.search.value=appliedFilters().q||''; updateSearchClear(); }
function submitSearch(){ const f=appliedFilters(); f.q=refs.search.value.trim(); state.page=0; return load(); }
function applyColumnFilters(nextRoles, nextRegions){ state.filters.airports.roles=nextRoles.slice(); state.filters.airports.regions=nextRegions.slice(); state.page=0; return load(); }
function endpointQuery(){ const f=appliedFilters(); const p=new URLSearchParams(); if(['airports','missions'].includes(state.tab)){ p.set('limit',state.limit); p.set('offset',state.page*state.limit); if(f.q)p.set('q',f.q); } if(state.tab==='airports'){ roles().forEach(r=>p.append('role',r)); regions().forEach(r=>p.append('region',r)); } return p.toString(); }

/* ---- region options: derived from the airport catalog, cached ---- */
async function ensureRegionOptions(){
  if(state.regionOptions)return state.regionOptions;
  const seen=new Set(); let offset=0, total=1;
  while(offset<total){
    const d=await apiFetch(`/api/airports?limit=500&offset=${offset}`);
    (d.items||[]).forEach(x=>{ if(x.region)seen.add(String(x.region)); });
    total=Number.isFinite(d.total)?d.total:0; offset+=500;
  }
  state.regionOptions=[...seen].sort((a,b)=>String(a).localeCompare(String(b),'zh-CN'));
  return state.regionOptions;
}

/* ---- column filter popover (airports only) ---- */
let filterDraft=[]; let filterCol='';
function closeFilterPopover(){ refs.popover.classList.add('hidden'); refs.popover.innerHTML=''; }
function renderFilterPopover(col){
  filterCol=col;
  const current=col==='role'?roles():regions();
  filterDraft=current.slice();
  const all=col==='role'?ROLE_OPTIONS.map(([k])=>k):(state.regionOptions||[]);
  const rows=col==='role'
    ? ROLE_OPTIONS.map(([k,n])=>[k,n])
    : all.map(v=>[v,v]);
  const body=rows.map(([k,n])=>`<label class="filter-option"><input type="checkbox" value="${esc(k)}" ${filterDraft.includes(k)?'checked':''}> <span>${esc(n)}</span></label>`).join('');
  refs.popover.innerHTML=`
    <div class="filter-popover-title">${col==='role'?'机场类型':'所属区域'}</div>
    ${col==='region'?'<div class="filter-popover-search"><input id="filterRegionSearch" class="control" placeholder="搜索区域……"></div>':''}
    <label class="filter-option filter-select-all"><input id="filterSelectAll" type="checkbox" ${all.length?'':'disabled'}> <span>全选</span></label>
    <div class="filter-option-divider" aria-hidden="true"></div>
    <div class="filter-options">${body}</div>
    <div class="filter-popover-footer">
      <button id="filterPopoverCancel" class="btn ghost" type="button">取消</button>
      <button id="filterPopoverApply" class="btn primary" type="button">应用</button>
    </div>`;
  const optionInputs=[...refs.popover.querySelectorAll('.filter-options .filter-option input')];
  const selectAll=$('filterSelectAll');
  const syncSelectAll=()=>{
    const selectedCount=all.filter(value=>filterDraft.includes(value)).length;
    selectAll.checked=all.length>0&&selectedCount===all.length;
    selectAll.indeterminate=selectedCount>0&&selectedCount<all.length;
  };
  optionInputs.forEach(cb=>cb.onchange=()=>{
    const v=cb.value;
    filterDraft=cb.checked?[...new Set([...filterDraft,v])]:filterDraft.filter(x=>x!==v);
    syncSelectAll();
  });
  selectAll.onchange=()=>{
    filterDraft=selectAll.checked?all.slice():[];
    optionInputs.forEach(cb=>{cb.checked=selectAll.checked});
    selectAll.indeterminate=false;
  };
  syncSelectAll();
  const s=$('filterRegionSearch');
  if(s)s.oninput=()=>{ const n=s.value.trim().toLowerCase(); refs.popover.querySelectorAll('.filter-options .filter-option').forEach(el=>el.classList.toggle('hidden',n!==''&&!(el.textContent||'').toLowerCase().includes(n))); };
  $('filterPopoverCancel').onclick=closeFilterPopover;
  $('filterPopoverApply').onclick=()=>{ closeFilterPopover(); if(filterCol==='role')applyColumnFilters(filterDraft,regions()).catch(e=>showMessage(fieldError(e),'error')); else applyColumnFilters(roles(),filterDraft).catch(e=>showMessage(fieldError(e),'error')); };
  refs.popover.classList.remove('hidden');
}
async function openFilterPopover(col, btn){
  if(col==='region'&&!state.regionOptions){
    try{ await ensureRegionOptions(); }catch(e){ showMessage(fieldError(e),'error'); return; }
  }
  const r=btn.getBoundingClientRect();
  const pop=refs.popover;
  renderFilterPopover(col);
  const width=col==='role'?220:240;
  pop.style.left=`${Math.max(8,Math.min(r.left,window.innerWidth-width-12))}px`;
  pop.style.top=`${r.bottom+6}px`;
  pop.style.width=`${width}px`;
}

/* ---- filter chips ---- */
function renderChips(){
  const chips=[];
  roles().forEach(r=>chips.push({label:`类型：${roleLabel(r)}`, remove:()=>applyColumnFilters(roles().filter(x=>x!==r),regions())}));
  regions().forEach(r=>chips.push({label:`区域：${r}`, remove:()=>applyColumnFilters(roles(),regions().filter(x=>x!==r))}));
  refs.chips.innerHTML=chips.map(c=>`<button class="filter-chip" type="button">${esc(c.label)} <svg class="ui-icon"><use href="#i-close"></use></svg></button>`).join('')
    + (hasColumnFilters()?`<button class="clear-filters" type="button">清除筛选</button>`:'');
  refs.chips.querySelectorAll('.filter-chip').forEach((b,i)=>b.onclick=()=>chips[i].remove().catch(e=>showMessage(fieldError(e),'error')));
  const clear=refs.chips.querySelector('.clear-filters');
  if(clear)clear.onclick=()=>applyColumnFilters([],[]).catch(e=>showMessage(fieldError(e),'error'));
}

/* ---- lookups: optional for the list, loaded on editor/detail demand ---- */
async function loadLookups(){ const [a,r,q]=await Promise.all([apiFetch('/api/aircraft-types'),apiFetch('/api/resource-types'),apiFetch('/api/aircraft-resource-requirements')]); state.aircraft=a.items||[];state.resources=r.items||[];state.requirements=q.items||[]; }
async function ensureLookups(){ if(state.lookupsLoaded)return; try{ await loadLookups(); state.lookupsLoaded=true; }catch(e){ state.lookupsLoaded=false; } }

/* ---- list loading: seq-guarded, selection never triggers it ---- */
async function load(){
  const seq=++state.listSeq; const meta=TAB_META[state.tab]; const f=appliedFilters();
  try{
    let items=[]; let total=0;
    if(['airports','missions'].includes(state.tab)){
      const q=endpointQuery(); const data=await apiFetch(`${meta.endpoint}${q?`?${q}`:''}`);
      items=data.items||[]; total=Number.isFinite(data.total)?data.total:items.length;
    }else{
      const data=await apiFetch(meta.endpoint);
      items=data.items||[];
      if(f.q){ const needle=f.q.toLowerCase(); items=items.filter(x=>{ const obj=state.tab==='aircraft_types'?x.aircraft_type:x.resource_type; const id=state.tab==='aircraft_types'?obj.aircraft_type_id:obj.resource_type_id; return `${id} ${obj.name}`.toLowerCase().includes(needle); }); }
      total=items.length;
    }
    if(seq!==state.listSeq)return;
    state.items=items; state.total=total;
    state.selected=null; state.detail=null; state.detailError=null;
    state.editing=false; state.creating=false; state.editorDirty=false;
    render();
  }catch(e){
    if(seq!==state.listSeq)return;
    showMessage(fieldError(e),'error');
    refs.wrap.innerHTML='<div class="empty-state">列表加载失败。</div>';
  }
}

/* ---- selection: only selected/detail/editing state, never list/filters ---- */
function itemId(row){ if(state.tab==='airports')return row.airport_id;if(state.tab==='missions')return row.mission?.mission_id;if(state.tab==='aircraft_types')return row.aircraft_type?.aircraft_type_id;return row.resource_type?.resource_type_id; }
async function selectIndex(i){
  if(!(await canLeaveEditor()))return;
  const row=state.items[i]; if(!row)return;
  state.selected=itemId(row); state.creating=false; state.editing=false; state.detailError=null;
  const seq=++state.detailSeq;
  try{
    let detail=row;
    if(state.tab==='airports') detail=await apiFetch(`/api/airports/${encodeURIComponent(state.selected)}`);
    else if(state.tab==='missions') detail=await apiFetch(`/api/missions/${encodeURIComponent(state.selected)}`);
    else if(state.tab==='aircraft_types') await ensureLookups();
    if(seq!==state.detailSeq)return;
    state.detail=detail; renderDetail(); render();
  }catch(e){
    if(seq!==state.detailSeq)return;
    state.detail=null; state.detailError=fieldError(e); renderDetail(); render();
  }
}

/* ---- table ---- */
function colFilterBtn(col){
  const active=col==='role'?roles().length>0:regions().length>0;
  return `<button class="col-filter ${active?'active':''}" data-col="${col}" type="button" aria-label="筛选${col==='role'?'类型':'区域'}"><svg class="ui-icon"><use href="#i-filter"></use></svg></button>`;
}
function renderTable(){
  let heads=[], body=[];
  if(state.tab==='airports'){heads=['编号','名称',`类型 ${colFilterBtn('role')}`,`区域 ${colFilterBtn('region')}`,'容量/窗','支持机型','更新时间']; body=state.items.map(x=>[x.airport_id,x.airport_name,x.role,x.region||'—',x.capacity_per_window??'—',x.supported_aircraft_type_count,x.updated_at||'—']);}
  else if(state.tab==='missions'){heads=['编号','名称','任务窗','需求机型','更新时间'];body=state.items.map(x=>[x.mission.mission_id,x.mission.name,`T${x.mission.window_start_slot}–T${x.mission.window_end_slot}`,x.mission.aircraft_requirements.length,x.metadata.updated_at||'—']);}
  else if(state.tab==='aircraft_types'){heads=['编号','名称','速度 km/h','最大航程 km','安全余油','更新时间'];body=state.items.map(x=>[x.aircraft_type.aircraft_type_id,x.aircraft_type.name,x.aircraft_type.speed_kmh??'—',x.aircraft_type.max_range_km??'—',x.aircraft_type.reserve_ratio==null?'—':`${(x.aircraft_type.reserve_ratio*100).toFixed(1)}%`,x.metadata.updated_at||'—']);}
  else {heads=['编号','名称','类别','单位','更新时间'];body=state.items.map(x=>[x.resource_type.resource_type_id,x.resource_type.name,x.resource_type.category,x.resource_type.unit,x.metadata.updated_at||'—']);}
  refs.wrap.innerHTML=`<table class="data-table"><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${body.map((cols,i)=>`<tr data-index="${i}" class="${itemId(state.items[i])===state.selected?'selected':''}">${cols.map(v=>`<td title="${esc(v)}">${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  refs.wrap.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>selectIndex(Number(tr.dataset.index)));
  refs.wrap.querySelectorAll('.col-filter').forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); openFilterPopover(b.dataset.col,b); });
}
function render(){
  const meta=TAB_META[state.tab];
  refs.title.textContent=meta.title;
  refs.count.textContent=`${state.total} 条 · 当前态`;
  refs.search.placeholder=meta.searchPlaceholder;
  renderChips();
  refs.add.textContent=`新增${meta.singular}`;
  refs.add.disabled=!writable(); refs.importBtn.disabled=!writable(); refs.exportBtn.disabled=false;
  refs.edit.disabled=!writable()||!state.detail||state.editing||state.creating;
  refs.del.disabled=!writable()||!state.detail||state.editing||state.creating;
  renderTable();
  const pages=Math.max(1,Math.ceil(state.total/state.limit));
  refs.pageInfo.textContent=`第 ${Math.min(state.page+1,pages)} / ${pages} 页`;
  refs.prev.disabled=state.page<=0;
  refs.next.disabled=(state.page+1)*state.limit>=state.total || !['airports','missions'].includes(state.tab);
  if(!state.editing&&!state.creating)renderDetail();
}

/* ---- detail ---- */
function kv(rows){return `<dl class="detail-grid">${rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`}
function paneTabs(activePane='basic'){
  return `<div class="airport-data-panes" role="tablist">
    <button type="button" class="${activePane==='basic'?'active':''}" data-airport-pane="basic">基础信息</button>
    <button type="button" class="${activePane==='operations'?'active':''}" data-airport-pane="operations">运行保障数据</button>
  </div>`;
}
function bindAirportPanes(){
  const tabs=refs.detail.querySelector('.airport-data-panes'); if(!tabs)return;
  tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>{
    tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));
    refs.detail.querySelectorAll('[data-airport-section]').forEach(s=>s.classList.toggle('hidden',s.dataset.airportSection!==b.dataset.airportPane));
  });
  refs.detail.querySelectorAll('[data-airport-section="operations"]').forEach(s=>s.classList.add('hidden'));
}
function detailAirports(x){
  const a=x.airport,p=x.operational_profile,m=x.metadata;
  const runways=a.runways==null?'<div class="field-note">跑道结构数据未知。</div>'
    :(a.runways.length?`<div>${a.runways.map(r=>`<div class="overview-item"><span>${esc(r.runway_id)}</span><span>${esc(r.length_m!=null?`${r.length_m} m`:'' )} ${esc(r.surface||'')}</span></div>`).join('')}</div>`:'<div class="field-note">已确认无跑道。</div>');
  const ops=p?`${kv([['配置完整',p.configuration_complete?'是':'否'],['容量/窗',p.capacity_per_window],['保障等级',p.support_level]])}
    <h4>支持机型</h4>${p.aircraft_support.length?p.aircraft_support.map(r=>`<div class="overview-item"><span>${esc(r.aircraft_type_id)}</span><span>${r.initial_quantity} 架 · 整备 ${r.tau_reset_windows} 窗</span></div>`).join(''):'<div class="field-note">暂无支持机型。</div>'}
    <h4>资源库存与补给能力</h4>${p.resource_stocks.length?p.resource_stocks.map(r=>`<div class="overview-item"><span>${esc(r.resource_type_id)}</span><span>库存 ${r.initial_quantity} · 补给 ${r.replenishment_capacity_per_window}/窗</span></div>`).join(''):'<div class="field-note">暂无资源库存。</div>'}`
    :`<div class="field-note">尚未建立运行配置。</div><button class="btn primary configure-operations-action" type="button">配置运行保障数据</button>`;
  return paneTabs('basic')
    + `<section class="detail-section" data-airport-section="basic"><h3>基本信息</h3>${kv([['编号',a.airport_id],['名称',a.airport_name],['设施类型',a.facility_type],['机场角色',a.role],['ICAO',a.icao_code],['IATA',a.iata_code],['区域',a.region],['城市',a.municipality],['经纬度',`${a.longitude}, ${a.latitude}`],['高程 m',a.elevation_m],['定期服务',a.scheduled_service?'是':'否']])}</section>`
    + `<section class="detail-section" data-airport-section="basic"><h3>跑道</h3>${runways}</section>`
    + `<section class="detail-section" data-airport-section="operations"><h3>运行保障配置</h3>${ops}</section>`
    + `<section class="detail-section" data-airport-section="basic"><h3>维护信息</h3>${kv([['revision',m.revision],['更新时间',m.updated_at]])}</section>`;
}
function detailMissions(x){
  const m=x.mission;
  return `<section class="detail-section"><h3>基本信息</h3>${kv([['编号',m.mission_id],['名称',m.name],['经纬度',`${m.longitude}, ${m.latitude}`],['更新时间',x.metadata.updated_at]])}</section>`
    + `<section class="detail-section"><h3>任务窗</h3>${kv([['开始窗',`T${m.window_start_slot}`],['结束窗（不含）',`T${m.window_end_slot}`]])}</section>`
    + `<section class="detail-section"><h3>机型需求</h3>${m.aircraft_requirements.length?m.aircraft_requirements.map(r=>`<div class="overview-item"><span>${esc(r.aircraft_type_id)}</span><span>${r.required_sorties} 架次 · 作业 ${r.tau_work_windows} 窗</span></div>`).join(''):'<div class="field-note">暂无需求行。</div>'}</section>`;
}
function detailAircraftTypes(x){
  const a=x.aircraft_type; const req=state.requirements.filter(r=>r.aircraft_type_id===a.aircraft_type_id);
  return `<section class="detail-section"><h3>基本信息</h3>${kv([['编号',a.aircraft_type_id],['名称',a.name],['更新时间',x.metadata.updated_at]])}</section>`
    + `<section class="detail-section"><h3>性能参数</h3>${kv([['速度 km/h',a.speed_kmh],['最大航程 km',a.max_range_km],['安全余油',a.reserve_ratio],['离场容量占用',a.departure_capacity_occupancy_factor],['到场容量占用',a.arrival_capacity_occupancy_factor]])}</section>`
    + `<section class="detail-section"><h3>资源消耗关系</h3>${req.length?req.map(r=>`<div class="overview-item"><span>${esc(r.resource_type_id)} / ${esc(r.basis)}</span><span>${esc(r.quantity)}</span></div>`).join(''):'<div class="field-note">暂无消耗关系。</div>'}</section>`;
}
function detailResourceTypes(x){
  const r=x.resource_type;
  return `<section class="detail-section"><h3>基本信息</h3>${kv([['编号',r.resource_type_id],['名称',r.name],['类别',r.category],['单位',r.unit],['更新时间',x.metadata.updated_at]])}</section>`;
}
function renderDetail(){
  const meta=TAB_META[state.tab];
  refs.detailTitle.textContent=`${meta.singular}详情`;
  if(state.editing||state.creating){ refs.detailTitle.textContent=`${state.creating?'新增':'编辑'}${meta.singular}`; renderEditor(); return; }
  if(state.detailError){ refs.detail.innerHTML=`<div class="detail-error">详情加载失败：${esc(state.detailError)}</div>`; return; }
  const x=state.detail;
  if(!x){ refs.detail.innerHTML=`<div class="detail-empty">${meta.empty}</div>`; return; }
  let html='';
  if(state.tab==='airports')html=detailAirports(x);
  else if(state.tab==='missions')html=detailMissions(x);
  else if(state.tab==='aircraft_types')html=detailAircraftTypes(x);
  else html=detailResourceTypes(x);
  refs.detail.innerHTML=html;
  if(state.tab==='airports'){ bindAirportPanes(); const cta=refs.detail.querySelector('.configure-operations-action'); if(cta)cta.onclick=()=>refs.edit.click(); }
}

/* ---- editor ---- */
function options(items,idKey,nameKey,current=''){return `<option value="">请选择…</option>${items.map(x=>{const v=x[idKey]??x.aircraft_type?.[idKey]??x.resource_type?.[idKey], n=x[nameKey]??x.aircraft_type?.[nameKey]??x.resource_type?.[nameKey];return `<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(n||v)}（${esc(v)}）</option>`}).join('')}`}
function inputField(label,id,value='',type='text',wide=false,extra=''){return `<div class="field ${wide?'wide':''}"><label>${esc(label)}</label><input id="${id}" class="control" type="${type}" value="${esc(val(value))}" ${extra}></div>`}
function selectField(label,id,opts,current,wide=false){return `<div class="field ${wide?'wide':''}"><label>${esc(label)}</label><select id="${id}" class="control">${opts.map(([v,n])=>`<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(n)}</option>`).join('')}</select></div>`}
function rowAirportSupport(r={}){return `<div class="dynamic-row support-row"><div class="field"><label>机型</label><select class="control row-aircraft">${options(state.aircraft,'aircraft_type_id','name',r.aircraft_type_id)}</select></div><div class="field"><label>初始数量</label><input class="control row-initial" type="number" min="0" value="${esc(val(r.initial_quantity))}"></div><div class="field"><label>整备用时/窗</label><input class="control row-reset" type="number" min="0" value="${esc(val(r.tau_reset_windows))}"></div><button class="mini-button remove-row" type="button">×</button></div>`}
function rowResourceStock(r={}){return `<div class="dynamic-row resource stock-row"><div class="field"><label>资源</label><select class="control row-resource">${options(state.resources,'resource_type_id','name',r.resource_type_id)}</select></div><div class="field"><label>初始库存</label><input class="control row-stock" type="number" min="0" value="${esc(val(r.initial_quantity))}"></div><div class="field"><label>补给能力/窗</label><input class="control row-replenish" type="number" min="0" value="${esc(val(r.replenishment_capacity_per_window))}"></div><button class="mini-button remove-row" type="button">×</button></div>`}
function runwayEndFields(prefix,end={}){return `<div class="runway-end"><strong>${prefix==='low'?'低端':'高端'}</strong><div class="runway-end-grid"><input class="control ${prefix}-ident" placeholder="端标识" value="${esc(val(end?.ident))}"><input class="control ${prefix}-lat" type="number" step="any" placeholder="纬度" value="${esc(val(end?.latitude))}"><input class="control ${prefix}-lon" type="number" step="any" placeholder="经度" value="${esc(val(end?.longitude))}"><input class="control ${prefix}-elev" type="number" step="any" placeholder="高程 m" value="${esc(val(end?.elevation_m))}"><input class="control ${prefix}-heading" type="number" step="any" min="0" max="360" placeholder="真航向°" value="${esc(val(end?.heading_deg_true))}"><input class="control ${prefix}-disp" type="number" step="any" min="0" placeholder="移位入口 m" value="${esc(val(end?.displaced_threshold_m))}"></div></div>`}
function rowRunway(r={}){return `<details class="runway-row"><summary><span>${esc(r.runway_id||'新跑道')}</span><button class="mini-button remove-runway" type="button" aria-label="删除跑道">×</button></summary><div class="runway-editor"><div class="edit-grid"><div class="field"><label>跑道编号</label><input class="control rw-id" value="${esc(val(r.runway_id))}"></div><div class="field"><label>道面</label><input class="control rw-surface" value="${esc(val(r.surface))}"></div><div class="field"><label>长度 m</label><input class="control rw-length" type="number" min="0" step="any" value="${esc(val(r.length_m))}"></div><div class="field"><label>宽度 m</label><input class="control rw-width" type="number" min="0" step="any" value="${esc(val(r.width_m))}"></div><div class="field wide"><label>灯光</label><select class="control rw-lighted"><option value="" ${r.lighted==null?'selected':''}>未知</option><option value="true" ${r.lighted===true?'selected':''}>有</option><option value="false" ${r.lighted===false?'selected':''}>无</option></select></div></div>${runwayEndFields('low',r.low_end)}${runwayEndFields('high',r.high_end)}</div></details>`}
function rowMissionReq(r={}){return `<div class="dynamic-row requirement mission-req-row"><div class="field"><label>机型</label><select class="control row-aircraft">${options(state.aircraft,'aircraft_type_id','name',r.aircraft_type_id)}</select></div><div class="field"><label>需求架次</label><input class="control row-sorties" type="number" min="1" value="${esc(val(r.required_sorties))}"></div><div class="field"><label>作业/窗</label><input class="control row-work" type="number" min="0" value="${esc(val(r.tau_work_windows))}"></div><button class="mini-button remove-row" type="button">×</button></div>`}
function rowAircraftResource(r={}){return `<div class="dynamic-row requirement aircraft-res-row"><div class="field"><label>资源</label><select class="control row-resource">${options(state.resources,'resource_type_id','name',r.resource_type_id)}</select></div><div class="field"><label>计量基础</label><select class="control row-basis"><option value="per_sortie" ${r.basis==='per_sortie'?'selected':''}>每架次</option><option value="per_hour" ${r.basis==='per_hour'?'selected':''}>每飞行小时</option></select></div><div class="field"><label>数量</label><input class="control row-quantity" type="number" min="0" step="any" value="${esc(val(r.quantity))}"></div><button class="mini-button remove-row" type="button">×</button></div>`}
function editorAirports(create){
  const a=state.detail?.airport||{},p=state.detail?.operational_profile||{airport_id:a.airport_id||'',configuration_complete:false,aircraft_support:[],resource_stocks:[]};
  const basic=`<div class="edit-grid">${inputField('机场编号','edAirportId',a.airport_id,'text',false,create?'':'readonly')}${inputField('名称','edAirportName',a.airport_name)}${selectField('设施类型','edFacilityType',[['large_airport','大型机场'],['medium_airport','中型机场'],['small_airport','小型机场']],a.facility_type||'medium_airport')}${selectField('机场角色','edAirportRole',[['military','军用'],['joint','军民合用'],['civil','民用']],a.role||'military')}${inputField('ICAO','edIcao',a.icao_code)}${inputField('IATA','edIata',a.iata_code)}${inputField('区域','edRegion',a.region)}${inputField('城市','edMunicipality',a.municipality)}${inputField('经度','edLon',a.longitude,'number',false,'step="any"')}${inputField('纬度','edLat',a.latitude,'number',false,'step="any"')}${inputField('高程 m','edElevation',a.elevation_m,'number',false,'step="any"')}${selectField('定期服务','edScheduled',[['true','是'],['false','否']],String(a.scheduled_service??false))}</div>`;
  const runway=`<div class="form-section"><div class="form-section-head"><strong>跑道基础数据</strong><div><label><input id="edRunwaysKnown" type="checkbox" ${a.runways!==null?'checked':''}> 结构已知</label> <button id="addRunwayRow" class="btn ghost" type="button">+ 添加跑道</button></div></div><div id="runwayRows">${(a.runways||[]).map(rowRunway).join('')}</div><div class="field-note">取消“结构已知”表示跑道结构数据未知，不等同于机场无跑道。</div></div>`;
  const missing=[]; if(!state.aircraft.length)missing.push('机型'); if(!state.resources.length)missing.push('保障资源');
  const operations=`<div class="form-section"><div class="form-section-head"><strong>运行保障数据</strong><label><input id="edConfigComplete" type="checkbox" ${p.configuration_complete?'checked':''}> 配置完整</label></div><div class="edit-grid">${inputField('容量/时间窗','edCapacity',p.capacity_per_window,'number')}${inputField('保障等级','edSupportLevel',p.support_level)}</div><div class="form-section-head"><strong>支持机型</strong><button id="addSupportRow" class="btn ghost" type="button">+ 添加</button></div><div id="supportRows">${(p.aircraft_support||[]).map(rowAirportSupport).join('')}</div><div class="form-section-head"><strong>资源库存与补给能力</strong><button id="addStockRow" class="btn ghost" type="button">+ 添加</button></div><div id="stockRows">${(p.resource_stocks||[]).map(rowResourceStock).join('')}</div></div>`;
  const note=missing.length?`<div class="operations-dependency-note">${missing.join('、')}基础库为空。先在上方对应页签建立类型，随后即可在本机场运行保障数据中引用。</div>`:'';
  return paneTabs('basic')
    + `<div class="airport-data-pane-body" data-airport-section="basic">${basic}${runway}</div>`
    + `<div class="airport-data-pane-body" data-airport-section="operations">${note}<div class="operations-explainer"><strong>可复用运行基线</strong><span>容量、保障等级、支持机型、初始数量、整备用时、资源库存和补给能力在这里维护；复制到 Situation 后成为独立 Working Copy。</span></div>${operations}</div>`;
}
function renderEditor(){
  let html=''; const create=state.creating;
  if(state.tab==='airports')html=editorAirports(create);
  else if(state.tab==='missions'){const m=state.detail?.mission||{};html=`<div class="edit-grid">${inputField('任务编号','edMissionId',m.mission_id,'text',false,create?'':'readonly')}${inputField('名称','edMissionName',m.name)}${inputField('经度','edMissionLon',m.longitude,'number',false,'step="any"')}${inputField('纬度','edMissionLat',m.latitude,'number',false,'step="any"')}${inputField('开始时间窗','edMissionStart',m.window_start_slot,'number')}${inputField('结束时间窗（不含）','edMissionEnd',m.window_end_slot,'number')}</div><div class="form-section"><div class="form-section-head"><strong>机型需求</strong><button id="addMissionReq" class="btn ghost" type="button">+ 添加</button></div><div id="missionReqRows">${(m.aircraft_requirements||[]).map(rowMissionReq).join('')}</div></div>`;}
  else if(state.tab==='aircraft_types'){const a=state.detail?.aircraft_type||{};const req=state.detail?state.requirements.filter(r=>r.aircraft_type_id===a.aircraft_type_id):[];html=`<div class="edit-grid">${inputField('机型编号','edAircraftId',a.aircraft_type_id,'text',false,create?'':'readonly')}${inputField('名称','edAircraftName',a.name)}${inputField('速度 km/h','edSpeed',a.speed_kmh,'number',false,'step="any"')}${inputField('最大航程 km','edRange',a.max_range_km,'number',false,'step="any"')}${inputField('安全余油比例','edReserve',a.reserve_ratio,'number',false,'min="0" max="0.999999" step="0.01"')}${inputField('离场容量占用因子','edDepFactor',a.departure_capacity_occupancy_factor,'number',false,'step="any"')}${inputField('到场容量占用因子','edArrFactor',a.arrival_capacity_occupancy_factor,'number',false,'step="any"')}</div><div class="form-section"><div class="form-section-head"><strong>资源消耗关系</strong><button id="addAircraftRes" class="btn ghost" type="button">+ 添加</button></div><div id="aircraftResRows">${req.map(rowAircraftResource).join('')}</div></div>`;}
  else {const r=state.detail?.resource_type||{};html=`<div class="edit-grid">${inputField('资源编号','edResourceId',r.resource_type_id,'text',false,create?'':'readonly')}${inputField('名称','edResourceName',r.name)}${selectField('类别','edResourceCategory',[['fuel','燃油'],['material','航材'],['munition','航弹']],r.category||'fuel')}${inputField('单位','edResourceUnit',r.unit)}</div>`;}
  refs.detail.innerHTML=html+`<div id="editorMessage" class="inline-message hidden"></div><div class="editor-footer"><button id="editorCancel" class="btn ghost" type="button">取消</button><button id="editorSave" class="btn primary" type="button">保存</button></div>`;
  if(state.tab==='airports')bindAirportPanes();
  bindEditorRows(); bindEditorDirty();
  $('editorCancel').onclick=()=>{state.editorDirty=false;state.editing=false;if(state.creating){state.creating=false;state.detail=null;}renderDetail();render();};
  $('editorSave').onclick=saveEditor;
}
function bindEditorRows(){const add=(id,target,fn)=>{const b=$(id);if(b)b.onclick=()=>{state.editorDirty=true;$(target).insertAdjacentHTML('beforeend',fn({}));bindRemoves();};};add('addSupportRow','supportRows',rowAirportSupport);add('addStockRow','stockRows',rowResourceStock);const ar=$('addRunwayRow');if(ar)ar.onclick=()=>{state.editorDirty=true;$('runwayRows').insertAdjacentHTML('beforeend',rowRunway({}));bindRemoves();};add('addMissionReq','missionReqRows',rowMissionReq);add('addAircraftRes','aircraftResRows',rowAircraftResource);bindRemoves();}
function bindRemoves(){refs.detail.querySelectorAll('.remove-row').forEach(b=>b.onclick=()=>{state.editorDirty=true;b.closest('.dynamic-row').remove()});refs.detail.querySelectorAll('.remove-runway').forEach(b=>b.onclick=(e)=>{e.preventDefault();state.editorDirty=true;b.closest('.runway-row').remove();});}
function rows(selector, mapper){return [...refs.detail.querySelectorAll(selector)].map(mapper);}
function nullableText(id){const x=$(id)?.value.trim();return x?x:null;}
function runwayEnd(row,prefix){const ident=row.querySelector(`.${prefix}-ident`).value.trim(),lat=num(row.querySelector(`.${prefix}-lat`).value),lon=num(row.querySelector(`.${prefix}-lon`).value),elev=num(row.querySelector(`.${prefix}-elev`).value),heading=num(row.querySelector(`.${prefix}-heading`).value),disp=num(row.querySelector(`.${prefix}-disp`).value);if(!ident&&lat==null&&lon==null&&elev==null&&heading==null&&disp==null)return null;return {ident:ident||null,latitude:lat,longitude:lon,elevation_m:elev,heading_deg_true:heading,displaced_threshold_m:disp}}
function airportPayload(){const id=$('edAirportId').value.trim();const runwaysKnown=$('edRunwaysKnown').checked;const runways=runwaysKnown?[...refs.detail.querySelectorAll('.runway-row')].map(r=>({runway_id:r.querySelector('.rw-id').value.trim(),length_m:num(r.querySelector('.rw-length').value),width_m:num(r.querySelector('.rw-width').value),surface:r.querySelector('.rw-surface').value.trim()||null,lighted:r.querySelector('.rw-lighted').value===''?null:r.querySelector('.rw-lighted').value==='true',low_end:runwayEnd(r,'low'),high_end:runwayEnd(r,'high')})):null;const runwayCount=runways===null?null:runways.length;const lengths=(runways||[]).map(r=>r.length_m).filter(v=>v!=null).map(Number);return {airport:{airport_id:id,airport_name:$('edAirportName').value.trim(),facility_type:$('edFacilityType').value,role:$('edAirportRole').value,icao_code:nullableText('edIcao'),iata_code:nullableText('edIata'),region:nullableText('edRegion'),municipality:nullableText('edMunicipality'),longitude:num($('edLon').value),latitude:num($('edLat').value),elevation_m:num($('edElevation').value),scheduled_service:$('edScheduled').value==='true',runway_count:runwayCount,max_runway_length_m:lengths.length?Math.max(...lengths):null,runways},operational_profile:{airport_id:id,configuration_complete:$('edConfigComplete').checked,capacity_per_window:int($('edCapacity').value),support_level:nullableText('edSupportLevel'),aircraft_support:rows('.support-row',r=>({aircraft_type_id:r.querySelector('.row-aircraft').value,initial_quantity:int(r.querySelector('.row-initial').value),tau_reset_windows:int(r.querySelector('.row-reset').value)})),resource_stocks:rows('.stock-row',r=>({resource_type_id:r.querySelector('.row-resource').value,initial_quantity:num(r.querySelector('.row-stock').value),replenishment_capacity_per_window:num(r.querySelector('.row-replenish').value)}))}};}
function missionPayload(){return {mission:{mission_id:$('edMissionId').value.trim(),name:$('edMissionName').value.trim(),longitude:num($('edMissionLon').value),latitude:num($('edMissionLat').value),window_start_slot:int($('edMissionStart').value),window_end_slot:int($('edMissionEnd').value),aircraft_requirements:rows('.mission-req-row',r=>({aircraft_type_id:r.querySelector('.row-aircraft').value,required_sorties:int(r.querySelector('.row-sorties').value),tau_work_windows:int(r.querySelector('.row-work').value)}))}};}
function aircraftPayload(){return {aircraft_type:{aircraft_type_id:$('edAircraftId').value.trim(),name:$('edAircraftName').value.trim(),speed_kmh:num($('edSpeed').value),max_range_km:num($('edRange').value),reserve_ratio:num($('edReserve').value),departure_capacity_occupancy_factor:num($('edDepFactor').value),arrival_capacity_occupancy_factor:num($('edArrFactor').value)},requirements:rows('.aircraft-res-row',r=>({resource_type_id:r.querySelector('.row-resource').value,basis:r.querySelector('.row-basis').value,quantity:Number(r.querySelector('.row-quantity').value)}))};}
function resourcePayload(){return {resource_type:{resource_type_id:$('edResourceId').value.trim(),name:$('edResourceName').value.trim(),category:$('edResourceCategory').value,unit:$('edResourceUnit').value.trim()}};}
async function refreshAfterChange(){ await load(); try{ await loadLookups(); state.lookupsLoaded=true; }catch(e){ state.lookupsLoaded=false; } }
async function saveEditor(){const msg=$('editorMessage');msg.classList.add('hidden');try{let result;if(state.tab==='airports'){const payload=airportPayload();if(state.creating)result=await apiFetch('/api/airports',{method:'POST',body:payload});else result=await apiFetch(`/api/airports/${encodeURIComponent(state.selected)}`,{method:'PUT',body:{...payload,expected_revision:state.detail.metadata.revision}});state.selected=result.airport.airport_id;state.detail=result;}
 else if(state.tab==='missions'){const payload=missionPayload();if(state.creating)result=await apiFetch('/api/missions',{method:'POST',body:payload});else result=await apiFetch(`/api/missions/${encodeURIComponent(state.selected)}`,{method:'PUT',body:{...payload,expected_revision:state.detail.metadata.revision}});state.selected=result.mission.mission_id;state.detail=result;}
 else if(state.tab==='aircraft_types'){const payload=aircraftPayload();if(state.creating)result=await apiFetch('/api/aircraft-types',{method:'POST',body:{aircraft_type:payload.aircraft_type}});else result=await apiFetch(`/api/aircraft-types/${encodeURIComponent(state.selected)}`,{method:'PUT',body:{aircraft_type:payload.aircraft_type,expected_revision:state.detail.metadata.revision}});let revision=result.metadata.revision;const id=result.aircraft_type.aircraft_type_id;if(payload.requirements.length){const rr=payload.requirements.map(r=>({aircraft_type_id:id,...r}));const replaced=await apiFetch(`/api/aircraft-types/${encodeURIComponent(id)}/resource-requirements`,{method:'PUT',body:{requirements:rr,expected_revision:revision}});result={aircraft_type:replaced.aircraft_type,metadata:replaced.metadata};revision=replaced.metadata.revision;} else if(!state.creating || state.requirements.some(r=>r.aircraft_type_id===id)){const replaced=await apiFetch(`/api/aircraft-types/${encodeURIComponent(id)}/resource-requirements`,{method:'PUT',body:{requirements:[],expected_revision:revision}});result={aircraft_type:replaced.aircraft_type,metadata:replaced.metadata};} state.selected=id;state.detail=result;}
 else {const payload=resourcePayload();if(state.creating)result=await apiFetch('/api/resource-types',{method:'POST',body:payload});else result=await apiFetch(`/api/resource-types/${encodeURIComponent(state.selected)}`,{method:'PUT',body:{...payload,expected_revision:state.detail.metadata.revision}});state.selected=result.resource_type.resource_type_id;state.detail=result;}
 state.creating=false;state.editing=false;state.editorDirty=false;await refreshAfterChange();showMessage('基础数据已保存。','success');}catch(e){msg.textContent=fieldError(e);msg.className='inline-message error';msg.classList.remove('hidden');}}
async function removeSelected(){if(!state.detail)return;const id=state.selected;if(!(await confirmAction(`删除 ${TAB_META[state.tab].singular} ${id}？若当前保存的 Situation 仍引用它，后端会拒绝删除。`,'确认删除')))return;try{const rev=state.detail.metadata.revision;let path;if(state.tab==='airports')path=`/api/airports/${encodeURIComponent(id)}`;else if(state.tab==='missions')path=`/api/missions/${encodeURIComponent(id)}`;else if(state.tab==='aircraft_types')path=`/api/aircraft-types/${encodeURIComponent(id)}`;else path=`/api/resource-types/${encodeURIComponent(id)}`;await apiFetch(path,{method:'DELETE',body:{expected_revision:rev}});state.detail=null;state.selected=null;await refreshAfterChange();showMessage('已删除当前基础数据记录。','success');}catch(e){showMessage(fieldError(e),'error');}}
async function doImport(){refs.importMsg.classList.add('hidden');const file=refs.importFile.files[0];if(!file){refs.importMsg.textContent='请选择 JSON 或 CSV 文件。';refs.importMsg.className='inline-message error';refs.importMsg.classList.remove('hidden');return;}try{const text=await file.text();let result;if(refs.importFormat.value==='csv'){result=await apiText(`/api/base-data/import?dataset=${encodeURIComponent(refs.importDataset.value)}`,{method:'POST',text,contentType:'text/csv'});}else{const parsed=JSON.parse(text);let body;if(parsed&&parsed.schema==='airport_master_v1'){body=parsed;}else if(Array.isArray(parsed)){body={dataset:refs.importDataset.value,items:parsed};}else if(parsed&&Array.isArray(parsed.items)){body={dataset:refs.importDataset.value,items:parsed.items};}else{throw new Error('JSON 必须是机场主数据文档（airport_master_v1）、对象数组或包含 items 数组。');}result=await apiFetch('/api/base-data/import',{method:'POST',body});}setModal(refs.importModal,false);state.page=0;await load();try{await loadLookups();state.lookupsLoaded=true;}catch(e){state.lookupsLoaded=false;}showMessage(`覆盖完成：新增 ${result.added}，更新 ${result.updated}，删除 ${result.deleted}，当前 ${result.total}。`,'success');}catch(e){refs.importMsg.textContent=e instanceof ApiError?fieldError(e):String(e.message||e);refs.importMsg.className='inline-message error';refs.importMsg.classList.remove('hidden');}}
async function allRowsForTab(){if(state.tab==='airports'||state.tab==='missions'){let out=[],offset=0,total=1;while(offset<total){const p=new URLSearchParams({limit:'500',offset:String(offset)});const d=await apiFetch(`${TAB_META[state.tab].endpoint}?${p}`);out.push(...(d.items||[]));total=d.total||0;offset+=500;}if(state.tab==='airports'){const full=[];for(const row of out)full.push(await apiFetch(`/api/airports/${encodeURIComponent(row.airport_id)}`));return full;}return out;}return state.items;}
function exportRows(rows){let items;if(state.tab==='airports')items=rows.map(x=>({airport:x.airport,operational_profile:x.operational_profile}));else if(state.tab==='missions')items=rows.map(x=>x.mission);else if(state.tab==='aircraft_types')items=rows.map(x=>x.aircraft_type);else items=rows.map(x=>x.resource_type);downloadBlob(`${state.tab}-current.json`,JSON.stringify({dataset:state.tab,exported_at:new Date().toISOString(),items},null,2),'application/json;charset=utf-8');}
async function exportCurrent(){try{const rows=await allRowsForTab();exportRows(rows);showMessage('已导出当前数据集 JSON。','success');}catch(e){showMessage(fieldError(e),'error');}}

function bind(signal){
  refs.tabs.querySelectorAll('button').forEach(b=>b.onclick=async()=>{
    if(!(await canLeaveEditor()))return;
    refs.tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));
    state.tab=b.dataset.tab; state.page=0;
    state.selected=null; state.detail=null; state.detailError=null;
    state.editing=false; state.creating=false; state.editorDirty=false;
    state.detailSeq++; closeFilterPopover();
    syncToolbarFromFilters();
    load().catch(e=>showMessage(fieldError(e),'error'));
  });
  refs.searchBtn.onclick=async()=>{ if(!(await canLeaveEditor()))return; submitSearch().catch(e=>showMessage(fieldError(e),'error')); };
  refs.search.onkeydown=e=>{ if(e.key==='Enter')refs.searchBtn.click(); };
  refs.search.oninput=()=>updateSearchClear();
  refs.searchClear.onclick=async()=>{ refs.search.value=''; updateSearchClear(); if(appliedFilters().q){ await submitSearch().catch(e=>showMessage(fieldError(e),'error')); } };
  document.addEventListener('pointerdown',(e)=>{ if(refs.popover&&!refs.popover.classList.contains('hidden')&&!refs.popover.contains(e.target)&&!e.target.closest?.('.col-filter'))closeFilterPopover(); },{signal});
  document.addEventListener('keydown',(e)=>{ if(e.key==='Escape')closeFilterPopover(); },{signal});
  refs.prev.onclick=async()=>{ if(state.page>0&&await canLeaveEditor()){ state.page--; load().catch(e=>showMessage(fieldError(e),'error')); } };
  refs.next.onclick=async()=>{ if(await canLeaveEditor()){ state.page++; load().catch(e=>showMessage(fieldError(e),'error')); } };
  refs.edit.onclick=async()=>{ if(!state.detail||state.editing||state.creating)return; await ensureLookups(); state.editing=true; state.editorDirty=false; renderDetail(); };
  refs.del.onclick=removeSelected;
  refs.add.onclick=async()=>{ if(!(await canLeaveEditor()))return; await ensureLookups(); state.creating=true; state.editing=true; state.editorDirty=false; state.selected=null; state.detail=null; renderDetail(); render(); };
  refs.importBtn.onclick=async()=>{ if(!(await canLeaveEditor()))return; refs.importDataset.value=state.tab; refs.importFile.value=''; refs.importMsg.classList.add('hidden'); setModal(refs.importModal,true); };
  refs.importCancel.onclick=()=>setModal(refs.importModal,false);
  refs.importConfirm.onclick=doImport;
  refs.exportBtn.onclick=exportCurrent;
  window.addEventListener('beforeunload',e=>{ if(state.editing&&state.editorDirty){ e.preventDefault(); e.returnValue=''; } },{signal});
}

async function init(context){
  try{
    state.me=await apiFetch('/api/me');
    bind(lifecycleController.signal);
    const params=new URL(context?.url||window.location.href,window.location.href).searchParams;
    const requestedTab=params.get('tab'); const requestedId=params.get('id');
    if(requestedTab&&TAB_META[requestedTab]){ state.tab=requestedTab; refs.tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.dataset.tab===state.tab)); }
    syncToolbarFromFilters();
    render(); await load();
    if(requestedId){ const i=state.items.findIndex(row=>String(itemId(row))===requestedId); if(i>=0)await selectIndex(i); }
    ensureLookups();
  }catch(e){ showMessage(fieldError(e),'error'); refs.wrap.innerHTML='<div class="empty-state">基础数据加载失败。</div>'; }
}

export async function mount(root, context = {}){
  if(mounted)unmount();
  pageRoot=root;
  Object.assign(state,initialState());
  bindRefs();
  lifecycleController=new AbortController();
  mounted=true;
  await init(context);
}

export async function beforeLeave(){
  if(!mounted)return true;
  return canLeaveEditor();
}

export function unmount(){
  if(!mounted)return;
  mounted=false;
  state.listSeq++;
  state.detailSeq++;
  lifecycleController?.abort();
  clearTimeout(showMessage.t);
  closeFilterPopover();
  lifecycleController=null;
  pageRoot=null;
  for(const key of Object.keys(refs))delete refs[key];
}
