import { apiFetch, apiDownload, saveBlob, ApiError } from './api-client.js';

const state = {
  workspace: 'damage', runs: [], runById: new Map(), damageCandidates: [], payload: null,
  chartMode: 'all', chartObjectId: null, bottomMode: 'airports', airportValue: 'sorties',
  draft: { damageTriple: null, baseRunId: null, selectedRunIds: new Set() },
  canExport: false,
};
const $ = (id) => document.getElementById(id);
const refs = {
  page: $('resultsPage'), top: $('resultsTop'), metrics: $('resultsMetrics'), conditionFacts: $('conditionFacts'),
  chart: $('resultsChart'), chartTitle: $('resultsChartTitle'), chartModes: $('resultsChartModes'), objectSelect: $('resultsObjectSelect'),
  legend: $('resultsLegend'), diff: $('resultsDiff'), diffTitle: $('resultsDiffTitle'), table: $('resultsTable'),
  overlay: $('resultsOverlay'), overlayBody: $('resultsOverlayBody'), apply: $('applyResultsComparison'), error: $('resultsError'),
  exportButton: $('resultsExportButton'), exportMenu: $('resultsExportMenu'),
};
const workspaceButtons = [...document.querySelectorAll('[data-workspace]')];
const bottomButtons = [...document.querySelectorAll('[data-bottom-mode]')];
const airportValueButtons = [...document.querySelectorAll('[data-airport-value]')];
const colors = ['#55a8ed','#ef8d34','#70bd61','#9c78d1','#d6b34e','#58c9c2'];

function esc(v){return String(v ?? '—').replace(/[&<>'"]/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function pct(v){return typeof v==='number'&&Number.isFinite(v)?`${(v*100).toFixed(1)}%`:'—';}
function number(v,d=0){return typeof v==='number'&&Number.isFinite(v)?v.toFixed(d):'—';}
function signed(v,{percent=false,unit=''}={}){if(typeof v!=='number'||!Number.isFinite(v))return '—';const x=percent?`${Math.abs(v*100).toFixed(1)}%`:`${Math.abs(v).toFixed(Number.isInteger(v)?0:2)}${unit}`;return `${v>0?'+':v<0?'−':''}${x}`;}
function showError(err){console.error(err);refs.error.textContent=err instanceof ApiError?err.message:'结果分析加载失败';refs.error.classList.remove('hidden');setTimeout(()=>refs.error.classList.add('hidden'),6500);}
function runLabel(id){const row=state.runById.get(id);if(!row)return id;const cfg=row.run_config||{};const dmg=cfg.damage_scenario_id||'无损毁';const cluster=cfg.cluster_enabled?'组选开启':'未组选';return `${id} · ${dmg} · ${cluster}`;}
function roleLabel(role){return ({R0:'基准',R1:'损毁',R2:'组选'})[role]||role;}
function currentRunIds(){if(!state.payload)return[];if(state.workspace==='damage')return ['R0','R1','R2'].map((r)=>state.payload.roles?.[r]).filter(Boolean);return state.payload.run_ids||[];}
function seriesLabel(id){if(state.workspace==='damage'){const role=Object.entries(state.payload?.roles||{}).find(([,rid])=>rid===id)?.[0];return role?`${role} ${roleLabel(role)}`:id;}return id===state.payload?.baseline_run_id?`${id} 基准`:id;}

async function loadInitial(){
  const [runs, damage] = await Promise.all([
    apiFetch('/api/runs?status=succeeded&limit=500'), apiFetch('/api/results/damage-candidates'),
  ]);
  state.runs=runs.items||[];state.runById=new Map(state.runs.map((x)=>[x.run_id,x]));state.damageCandidates=damage.items||[];
  renderEmpty();
}
function renderEmpty(){
  state.payload=null;updateExportState();refs.top.className='results-top empty-panel';refs.top.textContent='请选择比较条件';refs.metrics.innerHTML='';
  refs.chart.innerHTML='<div class="results-placeholder">暂无比较数据</div>';refs.legend.innerHTML='';
  refs.diff.innerHTML='<div class="results-placeholder">比较后显示确定性差异</div>';refs.table.innerHTML='<div class="results-placeholder">暂无比较数据</div>';
  refs.conditionFacts.textContent='尚未选择可比 Run';updateTitles();
}
function updateTitles(){
  if(state.workspace==='damage'){refs.chartTitle.textContent='三状态任务调度时序比较';refs.diffTitle.textContent='规则提示';}
  else if(state.workspace==='multi'){refs.chartTitle.textContent='多场景任务调度时序比较';refs.diffTitle.textContent='场景差异概览';}
  else{refs.chartTitle.textContent='多方案任务调度时序比较';refs.diffTitle.textContent='方案差异概览';}
}
function setWorkspace(mode){
  state.workspace=mode;state.chartMode='all';state.chartObjectId=null;state.bottomMode='airports';state.airportValue='sorties';state.draft={damageTriple:null,baseRunId:null,selectedRunIds:new Set()};
  workspaceButtons.forEach((b)=>b.classList.toggle('active',b.dataset.workspace===mode));
  bottomButtons.forEach((b)=>b.classList.toggle('active',b.dataset.bottomMode==='airports'));
  airportValueButtons.forEach((b)=>b.classList.toggle('active',b.dataset.airportValue==='sorties'));
  renderEmpty();openOverlay();
}

function openOverlay(){renderOverlay();refs.overlay.classList.add('open');refs.overlay.setAttribute('aria-hidden','false');}
function closeOverlay(){refs.overlay.classList.remove('open');refs.overlay.setAttribute('aria-hidden','true');}
function renderOverlay(){
  if(state.workspace==='damage')renderDamageOverlay();
  else renderComparableOverlay(state.workspace==='multi'?'multi_scenario':'configuration');
}
function renderDamageOverlay(){
  const rows=state.damageCandidates;
  refs.overlayBody.innerHTML=`<div class="overlay-section"><span class="overlay-title">后端已验证的 R0 / R1 / R2 组合</span><div class="candidate-list">${rows.length?rows.map((x,i)=>`<label class="candidate-row${state.draft.damageTriple===i?' selected':''}"><input type="radio" name="damageTriple" value="${i}" ${state.draft.damageTriple===i?'checked':''}><span><b>R0 ${esc(x.r0_run_id)} → R1 ${esc(x.r1_run_id)} → R2 ${esc(x.r2_run_id)}</b><small>损毁 ${esc(x.damage_scenario_id)} · 偏好 ${esc(x.preference_mode)}</small></span></label>`).join(''):'<div class="candidate-row"><span></span><span>当前没有满足固定 R0/R1/R2 规则的成功 Run 组合。</span></div>'}</div></div><div class="candidate-check">这里只显示后端已通过同一冻结 Situation、同一算法种子/时限、R0 无损毁未组选、R1 同配置损毁未组选、R2 同损毁组选开启等规则的组合。页面不会自动评选或替换 Run。</div>`;
  refs.overlayBody.querySelectorAll('input[name="damageTriple"]').forEach((el)=>el.addEventListener('change',()=>{state.draft.damageTriple=Number(el.value);renderDamageOverlay();}));
  refs.apply.disabled=state.draft.damageTriple===null;
}
async function loadComparable(baseRunId,mode){
  if(!baseRunId)return[];
  const q=new URLSearchParams({base_run_id:baseRunId,mode});
  const data=await apiFetch(`/api/results/comparable-runs?${q.toString()}`);return data.items||[];
}
async function renderComparableOverlay(mode){
  const base=state.draft.baseRunId;
  refs.overlayBody.innerHTML=`<div class="overlay-section"><label>${mode==='multi_scenario'?'比较基准 Run':'基准方案 Run'}</label><select id="comparisonBaseRun" class="overlay-select"><option value="">请选择成功 Run</option>${state.runs.map((r)=>`<option value="${esc(r.run_id)}" ${base===r.run_id?'selected':''}>${esc(runLabel(r.run_id))}</option>`).join('')}</select></div><div id="comparableRunArea" class="overlay-section"><span class="overlay-title">可比成功 Run</span><div class="candidate-list"><div class="candidate-row"><span></span><span>${base?'正在读取后端可比 Run…':'先选择基准 Run'}</span></div></div></div><div class="candidate-check">可比性由后端判断。多场景只允许损毁场景不同；方案配置比较固定 Situation、损毁、算法种子与求解时限，只允许业务配置差异。</div>`;
  $('comparisonBaseRun').addEventListener('change',async(e)=>{state.draft.baseRunId=e.target.value||null;state.draft.selectedRunIds=new Set(state.draft.baseRunId?[state.draft.baseRunId]:[]);await renderComparableOverlay(mode);});
  if(!base){refs.apply.disabled=true;return;}
  try{
    const items=await loadComparable(base,mode);const all=[state.runById.get(base),...items].filter(Boolean);const area=$('comparableRunArea');
    area.innerHTML=`<span class="overlay-title">可比成功 Run（${mode==='multi_scenario'?'选择 2–6 个':'选择 2–5 个'}）</span><div class="candidate-list">${all.map((r)=>{const id=r.run_id;const checked=state.draft.selectedRunIds.has(id);return `<label class="candidate-row${checked?' selected':''}"><input type="checkbox" data-run-id="${esc(id)}" ${checked?'checked':''} ${id===base?'disabled':''}><span><b>${esc(runLabel(id))}</b><small>${id===base?(mode==='configuration'?'基准方案':'比较基准'):'后端判定可比'}</small></span></label>`}).join('')}</div>`;
    area.querySelectorAll('input[type="checkbox"]').forEach((el)=>el.addEventListener('change',()=>{const id=el.dataset.runId;if(el.checked)state.draft.selectedRunIds.add(id);else state.draft.selectedRunIds.delete(id);renderComparableOverlay(mode);}));
    const count=state.draft.selectedRunIds.size;refs.apply.disabled=mode==='multi_scenario'?!((count>=2)&&(count<=6)):!((count>=2)&&(count<=5));
  }catch(err){showError(err);refs.apply.disabled=true;}
}

async function applyComparison(){
  refs.apply.disabled=true;
  try{
    let payload;
    if(state.workspace==='damage'){
      const c=state.damageCandidates[state.draft.damageTriple];if(!c)return;
      payload=await apiFetch('/api/results/damage-comparison',{method:'POST',body:{r0_run_id:c.r0_run_id,r1_run_id:c.r1_run_id,r2_run_id:c.r2_run_id}});
    }else if(state.workspace==='multi'){
      payload=await apiFetch('/api/results/scenario-comparison',{method:'POST',body:{run_ids:[...state.draft.selectedRunIds]}});
    }else{
      payload=await apiFetch('/api/results/config-comparison',{method:'POST',body:{run_ids:[...state.draft.selectedRunIds],baseline_run_id:state.draft.baseRunId}});
    }
    state.payload=payload;state.chartMode='all';state.chartObjectId=null;closeOverlay();renderComparison();
  }catch(err){showError(err);refs.apply.disabled=false;}
}


function updateExportState(){
  if (!refs.exportButton) return;
  refs.exportButton.disabled = !state.canExport || !state.payload;
  if (refs.exportButton.disabled) closeExportMenu();
}
function closeExportMenu(){
  refs.exportMenu?.classList.remove('open'); refs.exportMenu?.setAttribute('aria-hidden','true'); refs.exportButton?.setAttribute('aria-expanded','false');
}
function toggleExportMenu(){
  if (refs.exportButton?.disabled) return;
  const open = !refs.exportMenu.classList.contains('open');
  refs.exportMenu.classList.toggle('open', open); refs.exportMenu.setAttribute('aria-hidden', String(!open)); refs.exportButton.setAttribute('aria-expanded', String(open));
}
function exportRequestBody(){
  if (!state.payload) return null;
  if (state.workspace === 'damage') return { kind:'damage_comparison', r0_run_id:state.payload.roles?.R0, r1_run_id:state.payload.roles?.R1, r2_run_id:state.payload.roles?.R2 };
  if (state.workspace === 'multi') return { kind:'scenario_comparison', run_ids:[...(state.payload.run_ids || [])] };
  return { kind:'configuration_comparison', run_ids:[...(state.payload.run_ids || [])], baseline_run_id:state.payload.baseline_run_id };
}
async function exportResults(format){
  closeExportMenu(); const source=exportRequestBody(); if(!source)return;
  refs.exportButton.disabled=true; refs.exportButton.textContent='正在导出…';
  try{ const file=await apiDownload('/api/results/export-file',{method:'POST',body:{...source,format}}); saveBlob(file); }
  catch(err){ showError(err); }
  finally{ refs.exportButton.textContent='导出'; updateExportState(); }
}

function renderComparison(){updateExportState();updateTitles();renderConditions();renderTop();renderMetrics();renderChartControls();renderChart();renderDiff();renderBottom();}
function renderConditions(){
  const ids=currentRunIds();const items=ids.map((id)=>runLabel(id));refs.conditionFacts.textContent=items.join('　|　');
}
function renderTop(){
  if(state.workspace==='damage'){
    const roles=state.payload.roles||{};refs.top.className='results-top results-roles';
    refs.top.innerHTML=['R0','R1','R2'].map((r,i)=>`${i?'<div class="role-arrow">→</div>':''}<div class="result-role"><h3 style="color:${colors[i]}">${r} ${roleLabel(r)} · ${esc(roles[r])}</h3><p>${esc(runLabel(roles[r]))}</p></div>`).join('');return;
  }
  const ids=state.payload.run_ids||[];refs.top.className='results-top results-run-cards';
  refs.top.innerHTML=ids.map((id,i)=>`<div class="results-run-card"><strong>${state.workspace==='configuration'&&id===state.payload.baseline_run_id?'基准方案 · ':state.workspace==='multi'?`场景 ${i+1} · `:`方案 ${i+1} · `}${esc(id)}</strong><span>${esc(runLabel(id))}</span></div>`).join('');
}
function summaryFor(id){
  if(state.workspace==='damage'){const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return state.payload.comparison_summary?.[role]||{};}
  return state.payload.summary?.[id]||{};
}
function metricValue(summary,key){
  if(key==='peak_window')return Number.isInteger(summary.peak_window)?`T${summary.peak_window}`:'—';
  if(key==='peak_sorties')return number(summary.peak_sorties);
  if(key==='max_share')return pct(summary.max_airport_departure?.share);
  if(key==='resource')return pct(summary.minimum_resource_remaining?.ratio);
  return '—';
}
function renderMetrics(){
  const ids=currentRunIds();const cards=[['出动高峰','peak_window'],['峰值出动量','peak_sorties'],['最大机场累计承接占比','max_share'],['资源最低余量','resource']];
  refs.metrics.innerHTML=cards.map(([title,key])=>`<article class="results-metric"><h4>${title}</h4>${ids.map((id)=>`<div class="metric-line"><span>${esc(seriesLabel(id))}</span><b>${esc(metricValue(summaryFor(id),key))}</b></div>`).join('')}</article>`).join('');
}
function timelineObjectBlock(){
  const t=state.payload.timeline||{};if(state.chartMode==='airport')return t.by_airport||{};if(state.chartMode==='mission')return t.by_mission||{};if(state.chartMode==='aircraft')return t.by_aircraft||{};return null;
}
function renderChartControls(){
  [...refs.chartModes.querySelectorAll('button')].forEach((b)=>b.classList.toggle('active',b.dataset.chartMode===state.chartMode));
  if(state.chartMode==='all'){refs.objectSelect.classList.add('hidden');return;}
  const block=timelineObjectBlock();const ids=Object.keys(block||{});if(!ids.includes(state.chartObjectId))state.chartObjectId=ids[0]||null;
  refs.objectSelect.innerHTML=ids.map((id)=>`<option value="${esc(id)}" ${id===state.chartObjectId?'selected':''}>${esc(id)}</option>`).join('');refs.objectSelect.classList.toggle('hidden',!ids.length);
}
function comparisonSeries(){
  const t=state.payload.timeline||{},ids=currentRunIds();
  if(state.chartMode==='all'){
    if(state.workspace==='damage')return ids.map((id)=>{const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return {id,label:seriesLabel(id),values:t.departures?.[role]||[]};});
    if(state.workspace==='configuration')return ids.map((id)=>({id,label:seriesLabel(id),values:t.departures?.[id]?.values||[]}));
    return ids.map((id)=>({id,label:seriesLabel(id),values:t.departures?.[id]||[]}));
  }
  const row=(timelineObjectBlock()||{})[state.chartObjectId]||{};
  return ids.map((id)=>({id,label:seriesLabel(id),values:row[id]?.departures||[]}));
}
function renderChart(){
  if(!state.payload){refs.chart.innerHTML='<div class="results-placeholder">暂无比较数据</div>';return;}
  const windows=state.payload.timeline?.windows||[];
  const series=comparisonSeries();
  const max=Math.max(1,...series.flatMap((s)=>s.values.map((v)=>Number(v)||0)));
  const w=900,h=265,pad={l:48,r:18,t:18,b:34};
  const plotW=w-pad.l-pad.r, plotH=h-pad.t-pad.b;
  const x=(i)=>pad.l+(windows.length<=1?0:(i/(windows.length-1))*plotW);
  const y=(v)=>h-pad.b-(Number(v||0)/max)*plotH;
  const paths=series.map((s,i)=>{
    const pts=s.values.map((v,j)=>`${x(j)},${y(v)}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${colors[i%colors.length]}" stroke-width="2.4" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const yTicks=[0,max/2,max].map((v)=>{
    const yy=y(v);return `<line x1="${pad.l}" x2="${w-pad.r}" y1="${yy}" y2="${yy}" stroke="#27475d" opacity=".45"/><text x="${pad.l-7}" y="${yy+3}" text-anchor="end" fill="#7894a6" font-size="10">${number(v,Number.isInteger(v)?0:1)}</text>`;
  }).join('');
  const tickIndexes=[];
  if(windows.length){
    const count=Math.min(5,windows.length);
    for(let i=0;i<count;i+=1)tickIndexes.push(Math.round(i*(windows.length-1)/Math.max(1,count-1)));
  }
  const xTicks=[...new Set(tickIndexes)].map((i)=>`<text x="${x(i)}" y="${h-9}" text-anchor="middle" fill="#7894a6" font-size="10">T${windows[i]}</text>`).join('');
  refs.chart.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="任务调度时序比较"><g>${yTicks}</g><path d="M${pad.l} ${h-pad.b}H${w-pad.r}M${pad.l} ${pad.t}V${h-pad.b}" stroke="#48687b"/>${paths}<line class="results-hover-line" x1="${pad.l}" x2="${pad.l}" y1="${pad.t}" y2="${h-pad.b}" visibility="hidden"/>${xTicks}</svg><div class="results-chart-tooltip hidden" role="status"></div>`;
  const svg=refs.chart.querySelector('svg');
  const hover=refs.chart.querySelector('.results-hover-line');
  const tooltip=refs.chart.querySelector('.results-chart-tooltip');
  const hideTip=()=>{tooltip.classList.add('hidden');hover.setAttribute('visibility','hidden');};
  if(windows.length&&series.length){
    svg.addEventListener('mousemove',(event)=>{
      const rect=svg.getBoundingClientRect();
      const viewX=(event.clientX-rect.left)/Math.max(1,rect.width)*w;
      const ratio=Math.max(0,Math.min(1,(viewX-pad.l)/plotW));
      const index=Math.max(0,Math.min(windows.length-1,Math.round(ratio*(windows.length-1))));
      const xx=x(index);
      hover.setAttribute('x1',String(xx));hover.setAttribute('x2',String(xx));hover.setAttribute('visibility','visible');
      tooltip.innerHTML=`<strong>T${esc(windows[index])}</strong>${series.map((row,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(row.label)} <b>${esc(number(Number(row.values[index]||0)))}</b></span>`).join('')}`;
      tooltip.classList.remove('hidden');
      const px=(xx/w)*rect.width;
      tooltip.style.left=`${Math.min(Math.max(px+10,8),Math.max(8,rect.width-190))}px`;
      tooltip.style.top='12px';
    });
    svg.addEventListener('mouseleave',hideTip);
  }
  refs.legend.innerHTML=series.map((s,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(s.label)}</span>`).join('');
  refs.chartTitle.textContent=`${state.workspace==='damage'?'三状态':state.workspace==='multi'?'多场景':'多方案'}任务调度时序比较${state.chartMode==='all'?'':` · ${state.chartObjectId||'—'}`}`;
}
function renderDiff(){
  if(state.workspace==='damage'){
    const d=state.payload.difference_overview||{};const rows=[
      ['出动高峰变化',signed(d.peak_time_delta_minutes?.damage_delta,{unit:' min'}),signed(d.peak_time_delta_minutes?.cluster_delta,{unit:' min'})],
      ['峰值出动量变化',signed(d.peak_sorties?.damage_delta),signed(d.peak_sorties?.cluster_delta)],
      ['最大机场承接占比',signed(d.max_airport_departure_share?.damage_delta,{percent:true}),signed(d.max_airport_departure_share?.cluster_delta,{percent:true})],
      ['资源最低余量变化',signed(d.minimum_resource_remaining_ratio?.damage_delta,{percent:true}),signed(d.minimum_resource_remaining_ratio?.cluster_delta,{percent:true})],
      ['参与机场数量变化',signed(d.participating_airport_count?.damage_delta),signed(d.participating_airport_count?.cluster_delta)],
    ];refs.diff.innerHTML='<div class="diff-row"><span></span><span>损毁影响 R1−R0</span><span>组选调整 R2−R1</span></div>'+rows.map((r)=>`<div class="diff-row"><span>${r[0]}</span><span>${r[1]}</span><span>${r[2]}</span></div>`).join('')+'<div class="diff-note">仅报告后端确定性差值，不生成原因性判断或“最佳”结论。</div>';return;
  }
  if(state.workspace==='multi'){
    const d=state.payload.difference_overview||{};const ex=(obj,a,b,fmt=(x)=>esc(x))=>{const lo=obj?.[a],hi=obj?.[b];return [lo?`${fmt(lo.value)} · ${lo.run_ids.join(', ')}`:'—',hi?`${fmt(hi.value)} · ${hi.run_ids.join(', ')}`:'—'];};
    const rows=[['峰值出动量',...ex(d.peak_sorties,'lowest','highest',number)],['主峰时间',...ex(d.peak_window,'earliest','latest',(x)=>`T${x}`)],['最大机场承接占比',...ex(d.max_airport_departure_share,'lowest','highest',pct)],['资源最低余量',...ex(d.minimum_resource_remaining_ratio,'lowest','highest',pct)],['参与机场数量',...ex(d.participating_airport_count,'lowest','highest',number)]];
    refs.diff.innerHTML='<div class="diff-row"><span></span><span>低/早</span><span>高/晚</span></div>'+rows.map((r)=>`<div class="diff-row"><span>${r[0]}</span><span>${esc(r[1])}</span><span>${esc(r[2])}</span></div>`).join('')+'<div class="diff-note">极值用于描述场景差异，不代表场景优劣排序。</div>';return;
  }
  const deltas=state.payload.summary_deltas_vs_baseline||{},base=state.payload.baseline_run_id;const ids=(state.payload.run_ids||[]).filter((id)=>id!==base);
  refs.diff.innerHTML=ids.map((id)=>{const d=deltas[id]||{};return `<div class="diff-note"><b>${esc(id)} 相对 ${esc(base)}</b></div><div class="diff-row"><span>峰值时间</span><span>${signed(d.peak_time_delta_minutes,{unit:' min'})}</span><span></span></div><div class="diff-row"><span>峰值架次</span><span>${signed(d.peak_sorties_delta)}</span><span></span></div><div class="diff-row"><span>最大承接占比</span><span>${signed(d.max_airport_departure_share_delta,{percent:true})}</span><span></span></div><div class="diff-row"><span>资源最低余量</span><span>${signed(d.minimum_resource_remaining_ratio_delta,{percent:true})}</span><span></span></div><div class="diff-row"><span>参与机场</span><span>${signed(d.participating_airport_count_delta)}</span><span></span></div>`;}).join('')+'<div class="diff-note">全部差值均以后端指定基准 Run 为参照。</div>';
}
function airportCell(aid,id){
  const row=state.payload.airports?.[aid];if(!row)return '—';
  if(state.workspace==='damage'){const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];if(state.airportValue==='sorties')return number((row.departures_total||{})?.[role]);return pct((row.departure_share||{})?.[role]);}
  const v=row[id]||{};return state.airportValue==='sorties'?number(v.departures_total):pct(v.departure_share);
}
function resourceCell(category,id){
  const block=state.payload.resources?.category_min_remaining_ratio||{};
  if(state.workspace==='damage'){const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return pct(block[category]?.[role]);}
  if(state.workspace==='multi')return pct(block[category]?.[id]?.ratio);
  return pct(block[category]?.[id]?.detail?.ratio);
}
function renderBottom(){
  bottomButtons.forEach((b)=>b.classList.toggle('active',b.dataset.bottomMode===state.bottomMode));airportValueButtons.forEach((b)=>b.classList.toggle('active',b.dataset.airportValue===state.airportValue));
  airportValueButtons.forEach((b)=>b.classList.toggle('hidden',state.bottomMode!=='airports'));
  const ids=currentRunIds();
  if(state.bottomMode==='airports'){
    const aids=Object.keys(state.payload.airports||{}).sort();refs.table.innerHTML=`<table class="results-table"><thead><tr><th>机场</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody>${aids.map((aid)=>`<tr><td>${esc(aid)}</td>${ids.map((id)=>`<td>${airportCell(aid,id)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;return;
  }
  if(state.bottomMode==='resources'){
    const cats=[['fuel','燃油'],['material','航材'],['munition','航弹']];refs.table.innerHTML=`<table class="results-table"><thead><tr><th>资源类别最低余量率</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody>${cats.map(([c,n])=>`<tr><td>${n}</td>${ids.map((id)=>`<td>${resourceCell(c,id)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;return;
  }
  const scheme=state.payload.scheme||{};const rowFor=(id)=>{if(state.workspace==='damage'){const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return scheme[role]||{};}return scheme[id]||{};};
  refs.table.innerHTML=`<table class="results-table"><thead><tr><th>方案结构</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody><tr><td>组选机场</td>${ids.map((id)=>`<td>${esc((rowFor(id).selected_cluster||[]).join('、')||'—')}</td>`).join('')}</tr><tr><td>实际参与机场</td>${ids.map((id)=>`<td>${esc((rowFor(id).participating_airports||[]).join('、')||'—')}</td>`).join('')}</tr><tr><td>出动集中度 HHI</td>${ids.map((id)=>`<td>${number(rowFor(id).departure_hhi,3)}</td>`).join('')}</tr><tr><td>跨场返航比例</td>${ids.map((id)=>`<td>${pct(rowFor(id).cross_return_ratio)}</td>`).join('')}</tr></tbody></table>`;
}

workspaceButtons.forEach((b)=>b.addEventListener('click',()=>setWorkspace(b.dataset.workspace)));
$('changeConditionButton').addEventListener('click',openOverlay);$('closeResultsOverlay').addEventListener('click',closeOverlay);$('cancelResultsOverlay').addEventListener('click',closeOverlay);refs.apply.addEventListener('click',applyComparison);
refs.chartModes.addEventListener('click',(e)=>{const b=e.target.closest('[data-chart-mode]');if(!b||!state.payload)return;state.chartMode=b.dataset.chartMode;state.chartObjectId=null;renderChartControls();renderChart();});
refs.objectSelect.addEventListener('change',()=>{state.chartObjectId=refs.objectSelect.value||null;renderChart();});
bottomButtons.forEach((b)=>b.addEventListener('click',()=>{if(!state.payload)return;state.bottomMode=b.dataset.bottomMode;renderBottom();}));airportValueButtons.forEach((b)=>b.addEventListener('click',()=>{if(!state.payload)return;state.airportValue=b.dataset.airportValue;renderBottom();}));


refs.exportButton?.addEventListener('click',(e)=>{e.stopPropagation();toggleExportMenu();});
refs.exportMenu?.addEventListener('click',(e)=>{const b=e.target.closest('[data-export-format]');if(b)exportResults(b.dataset.exportFormat);});
document.addEventListener('click',(e)=>{if(!refs.exportMenu?.contains(e.target)&&!refs.exportButton?.contains(e.target))closeExportMenu();});
globalThis.addEventListener('app:account-ready',(e)=>{state.canExport=Boolean(e.detail?.permissions?.includes('results.export'));updateExportState();});
loadInitial().catch(showError);
