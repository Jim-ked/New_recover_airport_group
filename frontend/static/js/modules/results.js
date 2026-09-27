import { apiFetch, apiDownload, saveBlob, ApiError } from './api-client.js';
import { getAccount } from './account-context.js';
import { formatDecimal, formatHhi, formatInteger, formatPercent } from './number-display.js';

const VIEW_STATE = Object.freeze({
  LOADING: 'LOADING',
  ERROR: 'ERROR',
  ZERO_RESULTS: 'ZERO_RESULTS',
  NO_COMPARABLE_SET: 'NO_COMPARABLE_SET',
  READY_TO_SELECT: 'READY_TO_SELECT',
  HAS_COMPARISON: 'HAS_COMPARISON',
});
const createCandidateState = () => ({ status: 'idle', items: [], error: null, requestId: 0 });
const createWorkspaceState = () => ({
  payload: null,
  selection: null,
  draft: createDraft(),
  chartMode: 'all',
  chartObjectId: null,
  seriesKind: 'departures',
  bottomMode: 'airports',
  airportValue: 'sorties',
  objectType: 'task',
  objectId: null,
  objectAirportId: null,
  objectResourceId: null,
  objectAircraftId: null,
  objectPayload: null,
  objectCatalog: null,
});
const state = {
  workspace: 'damage', runs: [], runById: new Map(), damageCandidates: [], payload: null,
  runsStatus: 'loading', runsError: null,
  candidates: { damage: createCandidateState(), multi: createCandidateState(), configuration: createCandidateState(), exploratory: createCandidateState() },
  chartMode: 'all', chartObjectId: null, seriesKind: 'departures', bottomMode: 'airports', airportValue: 'sorties',
  draft: createDraft(),
  selection: null,
  workspaceStates: {
    damage: createWorkspaceState(),
    multi: createWorkspaceState(),
    configuration: createWorkspaceState(),
    exploratory: createWorkspaceState(),
  },
  userId: null,
  canExport: false,
};
const $ = (id) => document.getElementById(id);
const refs = {
  page: $('resultsPage'), metrics: $('resultsMetrics'), conditionFacts: $('conditionFacts'),
  main: $('resultsMain'), bottom: $('resultsBottom'), airportValueControls: $('resultsAirportValueControls'),
  chart: $('resultsChart'), chartTitle: $('resultsChartTitle'), chartControls: $('resultsChartControls'), chartModes: $('resultsChartModes'), seriesKinds: $('resultsSeriesKind'), objectSelect: $('resultsObjectSelect'),
  legend: $('resultsLegend'), diff: $('resultsDiff'), diffTitle: $('resultsDiffTitle'), table: $('resultsTable'),
  overlay: $('resultsOverlay'), overlayTitle: $('resultsOverlayTitle'), overlayBody: $('resultsOverlayBody'), overlaySearch: $('resultsOverlaySearch'), overlayFooter: $('resultsOverlayFooter'), runSearch: $('resultsRunSearch'), apply: $('applyResultsComparison'), error: $('resultsError'), scopeError: $('resultsScopeError'),
  changeCondition: $('changeConditionButton'), rulesButton: $('resultsRulesButton'), runLink: $('resultsRunLink'), retryButton: $('resultsRetryButton'),
  exportWrap: $('resultsExport'), exportButton: $('resultsExportButton'), exportMenu: $('resultsExportMenu'),
  conditionMatrix: $('resultsConditionMatrix'), solverComparison: $('resultsSolverComparison'),
  objectDrilldown: $('resultsObjectDrilldown'), objectType: $('resultsObjectType'), objectId: $('resultsObjectId'),
  objectIdWrap: $('resultsObjectIdWrap'), objectAirport: $('resultsObjectAirport'), objectAirportWrap: $('resultsObjectAirportWrap'),
  objectResource: $('resultsObjectResource'), objectResourceWrap: $('resultsObjectResourceWrap'), objectAircraft: $('resultsObjectAircraft'),
  objectAircraftWrap: $('resultsObjectAircraftWrap'), objectDetail: $('resultsObjectDetail'),
};
const workspaceButtons = [...document.querySelectorAll('[data-workspace]')];
const bottomButtons = [...document.querySelectorAll('[data-bottom-mode]')];
const airportValueButtons = [...document.querySelectorAll('[data-airport-value]')];
const colors = ['#55a8ed','#ef8d34','#70bd61','#9c78d1','#d6b34e','#58c9c2'];

function createDraft(){return {damageTriple:null,baseRunId:null,selectedRunIds:new Set(),searchText:'',comparableRuns:[],comparableLoading:false};}
function captureWorkspaceState(){
  const view=state.workspaceStates[state.workspace];
  Object.assign(view,{
    payload:state.payload,selection:state.selection,draft:state.draft,
    chartMode:state.chartMode,chartObjectId:state.chartObjectId,seriesKind:state.seriesKind,
    bottomMode:state.bottomMode,airportValue:state.airportValue,
  });
}
function activateWorkspaceState(workspace){
  const view=state.workspaceStates[workspace];
  state.payload=view.payload;state.selection=view.selection;state.draft=view.draft;
  state.chartMode=view.chartMode;state.chartObjectId=view.chartObjectId;state.seriesKind=view.seriesKind;
  state.bottomMode=view.bottomMode;state.airportValue=view.airportValue;
}
function normalizeSelection(workspace,selection){
  if(!selection||typeof selection!=='object')return null;
  const stringId=(value)=>typeof value==='string'&&value.trim()?value:null;
  if(workspace==='damage'){
    const r0=stringId(selection.r0_run_id),r1=stringId(selection.r1_run_id),r2=stringId(selection.r2_run_id);
    return r0&&r1&&r2?{r0_run_id:r0,r1_run_id:r1,r2_run_id:r2}:null;
  }
  const runIds=Array.isArray(selection.run_ids)?selection.run_ids.filter((id)=>stringId(id)):[];
  if(runIds.length<2||new Set(runIds).size!==runIds.length)return null;
  if(workspace==='multi')return {run_ids:runIds};
  const baseline=stringId(selection.baseline_run_id);
  return baseline&&runIds.includes(baseline)?{run_ids:runIds,baseline_run_id:baseline}:null;
}
function hydrateDraftFromSelection(workspace,view){
  const selection=view.selection;
  if(!selection)return;
  if(workspace==='damage'){
    view.draft.damageTriple=state.damageCandidates.findIndex((candidate)=>(
      candidate.r0_run_id===selection.r0_run_id&&candidate.r1_run_id===selection.r1_run_id&&candidate.r2_run_id===selection.r2_run_id
    ));
    if(view.draft.damageTriple<0)view.draft.damageTriple=null;
    return;
  }
  view.draft.baseRunId=workspace==='configuration'||workspace==='exploratory'?selection.baseline_run_id:selection.run_ids[0];
  view.draft.selectedRunIds=new Set(selection.run_ids);
}
function buildSessionState(){
  captureWorkspaceState();
  return {
    activeWorkspace:state.workspace,
    workspaces:Object.fromEntries(Object.entries(state.workspaceStates).map(([workspace,view])=>[
      workspace,{
        selection:view.selection,
        chartMode:view.chartMode,
        chartObjectId:view.chartObjectId,
        seriesKind:view.seriesKind,
        bottomMode:view.bottomMode,
        airportValue:view.airportValue,
      },
    ])),
  };
}
function storageKey(){return state.userId?`results.workspace.v1:${state.userId}`:null;}
function persistSessionState(){
  const key=storageKey();if(!key)return;
  try{sessionStorage.setItem(key,JSON.stringify(buildSessionState()));}catch(error){console.warn('Results UI state was not persisted',error);}
}
function restoreSessionState(){
  const key=storageKey();if(!key)return;
  try{
    const saved=JSON.parse(sessionStorage.getItem(key)||'null');
    if(!saved||typeof saved!=='object')return;
    if(['damage','multi','configuration','exploratory'].includes(saved.activeWorkspace))state.workspace=saved.activeWorkspace;
    for(const workspace of ['damage','multi','configuration','exploratory']){
      const source=saved.workspaces?.[workspace];if(!source||typeof source!=='object')continue;
      const view=state.workspaceStates[workspace];
      view.selection=normalizeSelection(workspace,source.selection);
      if(['all','airport','mission','aircraft'].includes(source.chartMode))view.chartMode=source.chartMode;
      view.chartObjectId=typeof source.chartObjectId==='string'?source.chartObjectId:null;
      if(['departures','returns'].includes(source.seriesKind))view.seriesKind=source.seriesKind;
      if(['airports','resources','scheme'].includes(source.bottomMode))view.bottomMode=source.bottomMode;
      if(['sorties','share'].includes(source.airportValue))view.airportValue=source.airportValue;
      hydrateDraftFromSelection(workspace,view);
    }
    activateWorkspaceState(state.workspace);
  }catch(error){console.warn('Results UI state could not be restored',error);}
}
function esc(v){return String(v ?? '—').replace(/[&<>'"]/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function displayValue(value,options){
  const {formatter=(item)=>String(item),notApplicable=false}=options||{};
  if(notApplicable)return '不适用';
  if(value===null||value===undefined||(typeof value==='number'&&!Number.isFinite(value)))return '未记录';
  return formatter(value);
}
function pct(v){return formatPercent(v,{digits:1});}
function number(v,d=0){return d===0?formatInteger(v):formatDecimal(v,{minimumFractionDigits:d,maximumFractionDigits:d});}
function signed(v,{percent=false,unit=''}={}){if(typeof v!=='number'||!Number.isFinite(v))return '—';const absolute=Math.abs(v);const x=percent?formatPercent(absolute,{digits:1}):`${Number.isInteger(absolute)?formatInteger(absolute):formatDecimal(absolute)}${unit}`;return `${v>0?'+':v<0?'−':''}${x}`;}
function shortRunId(id){const match=/^RUN-([a-f0-9]{8})/i.exec(String(id||''));return match?`R-${match[1].toUpperCase()}`:String(id||'—');}
function shortAirportName(value){return String(value||'').replace(/\s+(?:International\s+|General\s+)?(?:Airport|Air Base)$/i,'').trim()||String(value||'—');}
function airportDisplayLabel(id,label){const match=/^AP(\d+)$/i.exec(String(id||''));const number=match?match[1].padStart(3,'0'):'';const fallback=/^[a-z]+:/i.test(String(id||''))?'机场':id;return `${number?`${number} `:''}${shortAirportName(label||fallback)}`;}
function showError(err){console.error(err);refs.error.textContent=err instanceof ApiError?err.message:'结果分析加载失败';refs.error.classList.remove('hidden');setTimeout(()=>refs.error.classList.add('hidden'),6500);}
function clearScopeError(){refs.scopeError.textContent='';refs.scopeError.classList.add('hidden');}
function showComparisonError(err){console.error(err);const clientError=err instanceof ApiError&&err.status>=400&&err.status<500;refs.scopeError.textContent=clientError?`比较条件不可用：${err.message}`:'比较数据读取失败';refs.scopeError.classList.remove('hidden');}
function runLabel(id){const row=state.runById.get(id);if(!row)return shortRunId(id);const cfg=row.run_config||{};const situation=row.situation?.name||'未知情境';const damage=row.damage_scenario?.name||'无损毁';const cluster=cfg.cluster_enabled?'组选开启':'未组选';return `${situation} · ${damage} · ${cluster} · ${shortRunId(id)}`;}
function runMatchesSearch(row,query){const q=String(query||'').trim().toLocaleLowerCase();if(!q)return true;const cfg=row?.run_config||{};return [row?.run_id,row?.situation?.name,row?.situation_id,row?.damage_scenario?.name,cfg.damage_scenario_id].some((value)=>String(value||'').toLocaleLowerCase().includes(q));}
function runHref(id){return `/runs/${encodeURIComponent(id)}`;}
function roleLabel(role){return ({R0:'基准',R1:'损毁',R2:'组选'})[role]||role;}
function labelFor(kind,id){
  const labels=state.payload.labels||Object.values(state.payload.labels_by_run||{})[0]||{};
  const group=({airport:'airports',mission:'missions',aircraft:'aircraft'})[kind];
  const label=group?labels[group]?.[id]:null;
  if(kind==='airport')return airportDisplayLabel(id,label);
  return label&&label!==id?label:(id||'—');
}
function currentRunIds(){if(!state.payload)return[];if(state.workspace==='damage')return ['R0','R1','R2'].map((r)=>state.payload.roles?.[r]).filter(Boolean);return state.payload.run_ids||[];}
function seriesLabel(id){if(state.workspace==='damage'){const role=Object.entries(state.payload?.roles||{}).find(([,rid])=>rid===id)?.[0];return role?`${role} ${roleLabel(role)}`:id;}return id===state.payload?.baseline_run_id?`基准 · ${runLabel(id)}`:runLabel(id);}

function currentCandidateState(){return state.candidates[state.workspace];}
function deriveViewState(){
  if(state.runsStatus === 'error')return VIEW_STATE.ERROR;
  if(state.runsStatus !== 'ready')return VIEW_STATE.LOADING;
  if(state.runs.length === 0)return VIEW_STATE.ZERO_RESULTS;
  if(state.payload)return VIEW_STATE.HAS_COMPARISON;
  const candidate=currentCandidateState();
  if(candidate.status === 'error')return VIEW_STATE.ERROR;
  if(candidate.status !== 'ready')return VIEW_STATE.LOADING;
  return candidate.items.length === 0 ? VIEW_STATE.NO_COMPARABLE_SET : VIEW_STATE.READY_TO_SELECT;
}
function viewCapabilities(viewState,canExport=false){
  return {
    run: viewState===VIEW_STATE.ZERO_RESULTS||viewState===VIEW_STATE.NO_COMPARABLE_SET,
    rules: viewState===VIEW_STATE.NO_COMPARABLE_SET,
    select: viewState===VIEW_STATE.READY_TO_SELECT,
    change: viewState===VIEW_STATE.HAS_COMPARISON,
    export: viewState===VIEW_STATE.HAS_COMPARISON&&canExport,
    retry: viewState===VIEW_STATE.ERROR,
    chart: viewState===VIEW_STATE.HAS_COMPARISON,
  };
}
function workspaceRuleSummary(){
  const summaries={
    damage:'<strong>严格损毁对照</strong><ul><li>R0：无损毁、未开启选组</li><li>R1：同一问题、目标损毁、未开启选组</li><li>R2：与 R1 相同损毁，并开启选组</li></ul><p>组合由系统自动校验可比性。</p>',
    multi:'<strong>严格多损毁对照</strong><ul><li>有效基础问题和运行配置一致</li><li>仅选定损毁条件可变</li></ul>',
    configuration:'<strong>严格配置对照</strong><ul><li>基础情境和选定损毁一致</li><li>允许选组或目标偏好变化，目标可比性单独标明</li></ul>',
    exploratory:'<strong>跨条件描述性探索</strong><ul><li>允许任务压力、情境、时间范围或配置不同</li><li>必须显式列出条件差异</li></ul><p>仅描述，不作因果归因。</p>',
  };
  if(summaries[state.workspace])return summaries[state.workspace];
  if(state.workspace==='damage')return '<strong>损毁影响与优化效果</strong><ul><li>R0：无损毁、未开启组选</li><li>R1：同一问题、目标损毁、未开启组选</li><li>R2：与 R1 相同损毁，并开启组选</li></ul><p>组合由系统自动校验可比性。</p>';
  if(state.workspace==='multi')return '<strong>多场景比较</strong><ul><li>使用同一问题</li><li>保持一致的运行配置</li><li>选择不同损毁场景</li></ul><p>组合由系统自动校验可比性。</p>';
  return '<strong>方案配置比较</strong><ul><li>使用同一情境和损毁条件</li><li>保持公平一致的求解条件</li><li>比较不同方案配置</li></ul><p>组合由系统自动校验可比性。</p>';
}
function conditionText(viewState){
  if(viewState===VIEW_STATE.ZERO_RESULTS)return '暂无成功运行结果';
  if(viewState===VIEW_STATE.NO_COMPARABLE_SET)return '暂无满足条件的比较组合';
  if(viewState===VIEW_STATE.READY_TO_SELECT)return '尚未选择比较条件';
  if(viewState===VIEW_STATE.ERROR)return state.runsStatus==='error'?'运行结果读取失败':'比较条件读取失败';
  return state.runsStatus==='ready'?'正在读取比较条件…':'正在读取运行结果…';
}
function renderWorkbenchState(viewState){
  const content={
    [VIEW_STATE.LOADING]: {chart:'加载中…',diff:'—',bottom:'—'},
    [VIEW_STATE.ERROR]: {chart:'暂时无法读取结果数据',diff:'—',bottom:'—'},
    [VIEW_STATE.ZERO_RESULTS]: {chart:'<strong>当前暂无可用比较结果</strong><span>请先完成相应的算法运行</span>',diff:'等待比较结果',bottom:'暂无比较结果'},
    [VIEW_STATE.NO_COMPARABLE_SET]: {chart:'<strong>当前暂无可用比较结果</strong><span>请先完成相应的算法运行</span>',diff:'等待有效比较条件',bottom:'暂无比较结果'},
    [VIEW_STATE.READY_TO_SELECT]: {chart:'选择比较条件后显示时序比较',diff:'等待比较结果',bottom:'暂无比较结果'},
  }[viewState];
  if(!content)return;
  refs.chartTitle.textContent='时序比较';
  refs.diffTitle.textContent='关键差异';
  refs.chart.innerHTML=`<div class="results-placeholder">${content.chart}</div>`;
  refs.legend.innerHTML='';
  refs.diff.innerHTML=`<div class="results-placeholder">${content.diff}</div>`;
  refs.table.innerHTML=`<div class="results-placeholder">${content.bottom}</div>`;
}
function renderViewState(){
  const viewState=deriveViewState();
  const capabilities=viewCapabilities(viewState,state.canExport&&state.workspace!=='exploratory');
  refs.page.dataset.viewState=viewState;
  refs.metrics.classList.toggle('hidden',!capabilities.chart);
  refs.conditionMatrix.classList.toggle('hidden',!capabilities.chart);
  refs.solverComparison.classList.toggle('hidden',!capabilities.chart);
  refs.objectDrilldown.classList.toggle('hidden',!capabilities.chart);
  refs.chartControls.classList.toggle('hidden',!capabilities.chart);
  refs.changeCondition.classList.toggle('hidden',!capabilities.select&&!capabilities.change);
  refs.rulesButton.classList.toggle('hidden',!capabilities.rules);
  refs.runLink.classList.toggle('hidden',!capabilities.run);
  refs.retryButton.classList.toggle('hidden',!capabilities.retry);
  refs.exportWrap.classList.toggle('hidden',!capabilities.export);
  if(!capabilities.chart){
    refs.conditionFacts.textContent=conditionText(viewState);
    refs.changeCondition.textContent='选择比较条件';
    renderWorkbenchState(viewState);
  }
  renderBottom();
  updateExportState();
}
function runSituationId(run){return run?.situation?.situation_id||run?.situation_id||null;}
function runCreatedAt(run){return Date.parse(run?.created_at||'')||0;}
function rankedBaseRuns(workspace){
  const damageView=state.workspaceStates.damage;
  const preferredRunId=workspace==='multi'||workspace==='exploratory'
    ? damageView.selection?.r0_run_id
    : damageView.selection?.r1_run_id;
  const preferredSituationId=runSituationId(state.runById.get(preferredRunId));
  return [...state.runs].sort((a,b)=>{
    const aCfg=a.run_config||{},bCfg=b.run_config||{};
    const aRank=[
      a.run_id===preferredRunId?1:0,
      preferredSituationId&&runSituationId(a)===preferredSituationId?1:0,
      workspace==='multi'&&aCfg.damage_scenario_id==null?1:0,
      !aCfg.cluster_enabled?1:0,
      runCreatedAt(a),
    ];
    const bRank=[
      b.run_id===preferredRunId?1:0,
      preferredSituationId&&runSituationId(b)===preferredSituationId?1:0,
      workspace==='multi'&&bCfg.damage_scenario_id==null?1:0,
      !bCfg.cluster_enabled?1:0,
      runCreatedAt(b),
    ];
    for(let index=0;index<aRank.length;index+=1){if(aRank[index]!==bRank[index])return bRank[index]-aRank[index];}
    return String(b.run_id).localeCompare(String(a.run_id));
  });
}
async function discoverComparableCandidates(workspace){
  const mode=workspace==='multi'?'multi_scenario':workspace==='exploratory'?'exploratory':'configuration';
  const damageView=state.workspaceStates.damage;
  const preferredOtherId=workspace==='configuration'?damageView.selection?.r2_run_id:null;
  let best=null;
  for(const run of rankedBaseRuns(workspace)){
    const items=await loadComparable(run.run_id,mode);
    if(!items.length)continue;
    const candidate={baseRunId:run.run_id,items};
    if(workspace==='configuration'){
      if(run.run_id===damageView.selection?.r1_run_id&&items.some((item)=>item.run_id===preferredOtherId))return [candidate];
      return [candidate];
    }
    if(!best||items.length>best.items.length)best=candidate;
    if(items.length>=3)return [candidate];
  }
  return best?[best]:[];
}
async function ensureWorkspaceCandidates(workspace,{force=false}={}){
  if(state.runsStatus!=='ready'||state.runs.length===0)return;
  const candidate=state.candidates[workspace];
  if(!force&&(candidate.status==='loading'||candidate.status==='ready'))return;
  const requestId=++candidate.requestId;
  candidate.status='loading';candidate.error=null;
  if(workspace===state.workspace)renderViewState();
  try{
    const items=workspace==='damage'
      ? (await apiFetch('/api/results/damage-candidates')).items||[]
      : await discoverComparableCandidates(workspace);
    if(candidate.requestId!==requestId)return;
    candidate.items=items;candidate.status='ready';
    if(workspace==='damage'){
      state.damageCandidates=items;
      hydrateDraftFromSelection('damage',state.workspaceStates.damage);
    }
  }catch(error){
    if(candidate.requestId!==requestId)return;
    console.error(error);candidate.items=[];candidate.error=error;candidate.status='error';
  }
  if(workspace===state.workspace)renderViewState();
}
async function loadInitial(){
  state.runsStatus='loading';state.runsError=null;
  renderViewState();
  try{
    const runs=await apiFetch('/api/runs?status=succeeded&limit=500');
    state.runs=runs.items||[];state.runById=new Map(state.runs.map((x)=>[x.run_id,x]));state.runsStatus='ready';
    state.candidates={damage:createCandidateState(),multi:createCandidateState(),configuration:createCandidateState(),exploratory:createCandidateState()};
    if(state.runs.length === 0){renderViewState();return;}
    renderViewState();
    await ensureWorkspaceCandidates(state.workspace);
  }catch(error){
    console.error(error);state.runs=[];state.runById=new Map();state.runsError=error;state.runsStatus='error';renderViewState();
  }
}
function updateTitles(){
  refs.chartTitle.textContent=chartTitleText();refs.diffTitle.textContent='关键差异';
}
async function setWorkspace(mode){
  if(!state.workspaceStates[mode]||mode===state.workspace)return;
  captureWorkspaceState();persistSessionState();
  state.workspace=mode;activateWorkspaceState(mode);closeOverlay();clearScopeError();
  workspaceButtons.forEach((b)=>b.classList.toggle('active',b.dataset.workspace===mode));
  bottomButtons.forEach((b)=>b.classList.toggle('active',b.dataset.bottomMode===state.bottomMode));
  airportValueButtons.forEach((b)=>b.classList.toggle('active',b.dataset.airportValue===state.airportValue));
  if(state.payload)renderComparison();else renderViewState();
  await ensureWorkspaceCandidates(mode);
  if(!state.workspaceStates[mode].selection&&!state.workspaceStates[mode].payload){
    try{
      const applied=await autoApplyWorkspaceDefault(mode);
      if(applied){activateWorkspaceState(mode);renderComparison();}
    }catch(error){showComparisonError(error);}
  }
  persistSessionState();
}

function openRules(){refs.overlay.classList.add('rules-only');refs.overlayTitle.textContent='比较规则';refs.overlaySearch.classList.add('hidden');refs.overlayFooter.classList.add('hidden');refs.overlayBody.innerHTML=`<div class="comparison-rules">${workspaceRuleSummary()}</div>`;refs.overlay.classList.add('open');refs.overlay.setAttribute('aria-hidden','false');}
async function openOverlay(){
  const viewState=deriveViewState();if(viewState!==VIEW_STATE.READY_TO_SELECT&&viewState!==VIEW_STATE.HAS_COMPARISON)return;
  clearScopeError();refs.overlay.classList.remove('rules-only');refs.overlaySearch.classList.remove('hidden');refs.overlayFooter.classList.remove('hidden');refs.overlayTitle.textContent=state.payload?'修改比较条件':'选择比较条件';refs.runSearch.value=state.draft.searchText;renderOverlay();refs.overlay.classList.add('open');refs.overlay.setAttribute('aria-hidden','false');
  if(state.workspace==='damage'||!state.draft.baseRunId||state.draft.comparableRuns.length||state.draft.comparableLoading)return;
  const requestDraft=state.draft;const baseRunId=state.draft.baseRunId;
  state.draft.comparableLoading=true;renderOverlay();
  try{
    const mode=state.workspace==='multi'?'multi_scenario':state.workspace==='exploratory'?'exploratory':'configuration';
    const items=await loadComparable(baseRunId,mode);
    if(state.draft===requestDraft&&state.draft.baseRunId===baseRunId)state.draft.comparableRuns=items;
  }catch(error){if(state.draft===requestDraft)showComparisonError(error);}
  finally{if(state.draft===requestDraft){state.draft.comparableLoading=false;renderOverlay();}}
}
function closeOverlay(){refs.overlay.classList.remove('open');refs.overlay.setAttribute('aria-hidden','true');}
function renderOverlay(){
  if(state.workspace==='damage')renderDamageOverlay();
  else renderComparableOverlay(state.workspace==='multi'?'multi_scenario':state.workspace==='exploratory'?'exploratory':'configuration');
}
function renderDamageOverlay(){
  const rows=state.damageCandidates.map((row,index)=>({row,index})).filter(({row})=>[row.r0_run_id,row.r1_run_id,row.r2_run_id].some((id)=>runMatchesSearch(state.runById.get(id)||{run_id:id},state.draft.searchText)));
  refs.overlayBody.innerHTML=`<div class="overlay-section"><span class="overlay-title">可比较的 R0 / R1 / R2 组合</span><div class="candidate-list">${rows.length?rows.map(({row:x,index:i})=>`<label class="candidate-row${state.draft.damageTriple===i?' selected':''}"><input type="radio" name="damageTriple" value="${i}" ${state.draft.damageTriple===i?'checked':''}><span><b>R0 ${esc(shortRunId(x.r0_run_id))} → R1 ${esc(shortRunId(x.r1_run_id))} → R2 ${esc(shortRunId(x.r2_run_id))}</b><small>${esc(runLabel(x.r1_run_id))}</small></span></label>`).join(''):`<div class="candidate-row"><span></span><span>没有匹配的比较组合。</span></div>`}</div></div><div class="candidate-check">R0 为无损毁基准，R1 为同一问题下的目标损毁，R2 在与 R1 相同损毁下开启组选；组合由系统自动校验可比性。</div>`;
  refs.overlayBody.querySelectorAll('input[name="damageTriple"]').forEach((el)=>el.addEventListener('change',()=>{state.draft.damageTriple=Number(el.value);renderDamageOverlay();}));
  refs.apply.disabled=state.draft.damageTriple===null;
}
async function loadComparable(baseRunId,mode){
  if(!baseRunId)return[];
  const q=new URLSearchParams({base_run_id:baseRunId,mode});
  const data=await apiFetch(`/api/results/comparable-runs?${q.toString()}`);return data.items||[];
}
function renderComparableOverlay(mode){
  const base=state.draft.baseRunId;
  const baseRows=state.runs.filter((row)=>row.run_id===base||runMatchesSearch(row,state.draft.searchText));
  const all=[state.runById.get(base),...state.draft.comparableRuns].filter((row,index,items)=>row&&items.findIndex((item)=>item.run_id===row.run_id)===index).filter((row)=>row.run_id===base||runMatchesSearch(state.runById.get(row.run_id)||row,state.draft.searchText));
  const comparableContent=!base?'先选择基准 Run':state.draft.comparableLoading?'正在读取可比 Run…':all.length?all.map((r)=>{const id=r.run_id;const checked=state.draft.selectedRunIds.has(id);const relation=r.comparability?.strict_comparable?`严格可比：${(r.comparability.strict_modes||[]).join(' / ')}`:mode==='exploratory'?'跨条件候选':'系统确认可比';return `<label class="candidate-row${checked?' selected':''}"><input type="checkbox" data-run-id="${esc(id)}" ${checked?'checked':''} ${id===base?'disabled':''}><span><b>${esc(runLabel(id))}</b><small>${id===base?(mode==='configuration'?'基准方案':'比较基准'):esc(relation)}</small></span></label>`}).join(''):'没有匹配的可比 Run。';
  const rule=mode==='multi_scenario'?'有效基础问题和配置一致，仅选定损毁可变。':mode==='exploratory'?'允许条件不同；结果仅用于描述性探索，不作因果归因。':'基础情境和选定损毁一致，允许指定配置变化。';
  const limitText=mode==='multi_scenario'?'选择 2–6 个':mode==='exploratory'?'选择 2–50 个':'选择 2–5 个';
  refs.overlayBody.innerHTML=`<div class="overlay-section"><label>${mode==='configuration'?'基准方案 Run':'比较基准 Run'}</label><select id="comparisonBaseRun" class="overlay-select"><option value="">请选择成功 Run</option>${baseRows.map((r)=>`<option value="${esc(r.run_id)}" ${base===r.run_id?'selected':''}>${esc(runLabel(r.run_id))}</option>`).join('')}</select></div><div id="comparableRunArea" class="overlay-section"><span class="overlay-title">成功 Run（${limitText}）</span><div class="candidate-list">${all.length&&!state.draft.comparableLoading?comparableContent:`<div class="candidate-row"><span></span><span>${comparableContent}</span></div>`}</div></div><div class="candidate-check">${rule}</div>`;
  $('comparisonBaseRun').addEventListener('change',async(e)=>{const next=e.target.value||null;const requestDraft=state.draft;state.draft.baseRunId=next;state.draft.selectedRunIds=new Set(next?[next]:[]);state.draft.comparableRuns=[];state.draft.comparableLoading=Boolean(next);clearScopeError();renderComparableOverlay(mode);if(!next)return;try{const items=await loadComparable(next,mode);if(state.draft!==requestDraft||state.draft.baseRunId!==next)return;state.draft.comparableRuns=items;}catch(err){if(state.draft!==requestDraft||state.draft.baseRunId!==next)return;showComparisonError(err);}finally{if(state.draft===requestDraft&&state.draft.baseRunId===next){state.draft.comparableLoading=false;renderComparableOverlay(mode);}}});
  refs.overlayBody.querySelectorAll('input[type="checkbox"]').forEach((el)=>el.addEventListener('change',()=>{const id=el.dataset.runId;if(el.checked)state.draft.selectedRunIds.add(id);else state.draft.selectedRunIds.delete(id);renderComparableOverlay(mode);}));
  const count=state.draft.selectedRunIds.size;
  const maximum=mode==='multi_scenario'?6:mode==='exploratory'?50:5;
  refs.apply.disabled=state.draft.comparableLoading||count<2||count>maximum;
}

function selectionFromDraft(workspace){
  if(workspace==='damage'){
    const candidate=state.damageCandidates[state.draft.damageTriple];
    return candidate?{r0_run_id:candidate.r0_run_id,r1_run_id:candidate.r1_run_id,r2_run_id:candidate.r2_run_id}:null;
  }
  const runIds=[...state.draft.selectedRunIds];
  if(workspace==='multi')return {run_ids:runIds};
  return {run_ids:runIds,baseline_run_id:state.draft.baseRunId};
}
async function requestComparison(workspace,selection){
  if(workspace==='damage')return apiFetch('/api/results/damage-comparison',{method:'POST',body:selection});
  if(workspace==='multi')return apiFetch('/api/results/scenario-comparison',{method:'POST',body:selection});
  if(workspace==='exploratory')return apiFetch('/api/results/exploratory-comparison',{method:'POST',body:selection});
  return apiFetch('/api/results/config-comparison',{method:'POST',body:selection});
}
async function restoreSavedComparisons(){
  for(const workspace of ['damage','multi','configuration','exploratory']){
    const view=state.workspaceStates[workspace];
    if(!view.selection)continue;
    try{
      view.payload=await requestComparison(workspace,view.selection);
      hydrateDraftFromSelection(workspace,view);
    }catch(error){
      view.payload=null;
      if(error instanceof ApiError&&error.status>=400&&error.status<500){view.selection=null;view.draft=createDraft();}
      if(workspace===state.workspace)showComparisonError(error);
    }
  }
  activateWorkspaceState(state.workspace);
  if(state.payload)renderComparison();else renderViewState();
  persistSessionState();
}

function defaultDamageCandidate(){
  const rows=state.damageCandidates.filter((candidate)=>[
    candidate.r0_run_id,candidate.r1_run_id,candidate.r2_run_id,
  ].every((id)=>state.runById.has(id)));
  const rank=(candidate)=>{
    const runs=[candidate.r0_run_id,candidate.r1_run_id,candidate.r2_run_id].map((id)=>state.runById.get(id));
    const recent=Math.max(...runs.map((run)=>Date.parse(run?.created_at||'')||0));
    return [recent];
  };
  return rows.sort((a,b)=>{const ar=rank(a),br=rank(b);return br[0]-ar[0];})[0]||null;
}
async function autoApplyWorkspaceDefault(workspace){
  const view=state.workspaceStates[workspace];
  if(view.selection||view.payload)return false;
  await ensureWorkspaceCandidates(workspace);
  let selection=null;
  if(workspace==='damage'){
    const candidate=defaultDamageCandidate();if(!candidate)return false;
    selection={r0_run_id:candidate.r0_run_id,r1_run_id:candidate.r1_run_id,r2_run_id:candidate.r2_run_id};
    view.payload=await requestComparison('damage',selection);
    view.draft.damageTriple=state.damageCandidates.indexOf(candidate);
  }else{
    const candidate=state.candidates[workspace].items[0];if(!candidate)return false;
    if(workspace==='multi'){
      const runIds=[candidate.baseRunId,...candidate.items.map((item)=>item.run_id)];
      const uniqueRunIds=[...new Set(runIds)];
      while(uniqueRunIds.length>6)uniqueRunIds.pop();
      selection={run_ids:uniqueRunIds};
      if(selection.run_ids.length<2)return false;
      view.payload=await requestComparison('multi',selection);
    }else if(workspace==='configuration'){
      const damageView=state.workspaceStates.damage;
      const preferred=damageView.selection?.r2_run_id;
      const other=candidate.items.find((item)=>item.run_id===preferred)||candidate.items[0];
      if(!other)return false;
      selection={run_ids:[candidate.baseRunId,other.run_id],baseline_run_id:candidate.baseRunId};
      view.payload=await requestComparison('configuration',selection);
    }else{
      const runIds=[candidate.baseRunId,...candidate.items.filter((_item,index)=>index<5).map((item)=>item.run_id)];
      selection={run_ids:[...new Set(runIds)],baseline_run_id:candidate.baseRunId};
      if(selection.run_ids.length<2)return false;
      view.payload=await requestComparison('exploratory',selection);
    }
  }
  view.selection=selection;
  hydrateDraftFromSelection(workspace,view);
  return true;
}
async function autoApplyDefaultComparison(){
  await ensureWorkspaceCandidates('damage');
  const damageApplied=await autoApplyWorkspaceDefault('damage');
  const multiApplied=await autoApplyWorkspaceDefault('multi');
  const configurationApplied=await autoApplyWorkspaceDefault('configuration');
  const exploratoryApplied=await autoApplyWorkspaceDefault('exploratory');
  const applied=damageApplied||multiApplied||configurationApplied||exploratoryApplied;
  activateWorkspaceState(state.workspace);
  if(state.payload)renderComparison();else renderViewState();
  if(applied)persistSessionState();
  return applied;
}

async function applyComparison(){
  const requestWorkspace=state.workspace;
  const requestDraft=state.draft;
  refs.apply.disabled=true;clearScopeError();
  try{
    const selection=selectionFromDraft(state.workspace);if(!selection)return;
    const payload=await requestComparison(state.workspace,selection);
    if(state.workspace!==requestWorkspace)return;
    if(state.draft!==requestDraft)return;
    state.payload=payload;state.selection=selection;state.chartObjectId=null;closeOverlay();captureWorkspaceState();persistSessionState();renderComparison();
  }catch(err){if(state.workspace!==requestWorkspace)return;if(state.draft!==requestDraft)return;showComparisonError(err);refs.apply.disabled=false;}
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

function renderConditionMatrix(){
  const ids=currentRunIds();
  const comparability=state.payload.comparability||{};
  const configurations=state.payload.configurations||{};
  const timeAxes=state.payload.time_axes||{};
  const descriptiveOnly=Boolean(state.payload.descriptive_only);
  const rows=ids.map((id)=>{
    const relation=comparability[id]||{};
    const strictModes=relation.strict_modes||[];
    const differences=relation.differences||relation.reasons||[];
    const configEntry=configurations[id]||state.runById.get(id)?.run_config||{};
    const config=configEntry.run_config||configEntry;
    const axis=timeAxes[id]||state.payload.timeline||{};
    const windows=axis.windows||[];
    const timeLabel=windows.length?`T${windows[0]} – T${windows[windows.length-1]} · ${windows.length} 窗`:'未记录';
    const scope=descriptiveOnly
      ? (strictModes.length?`同时满足严格 ${strictModes.join(' / ')}`:'跨条件描述')
      : '严格条件对照';
    return `<tr><th><a href="${esc(runHref(id))}">${esc(seriesLabel(id))}</a></th><td>${esc(scope)}</td><td>${config.damage_scenario_id?esc(config.damage_scenario_id):'无选定损毁'}</td><td>${config.cluster_enabled?'选组开启':'选组关闭'}</td><td>${esc(timeLabel)}</td><td>${esc(differences.join('；')||'无已记录条件差异')}</td></tr>`;
  }).join('');
  const warning=descriptiveOnly?'<p class="comparison-scope-note warning"><strong>跨条件探索：</strong>仅描述，不作因果归因。任务需求、时间范围或目标定义不同时，差值只表示历史结果差异。</p>':'<p class="comparison-scope-note">当前组合已按对应的严格规则校验。</p>';
  refs.conditionMatrix.innerHTML=`<header class="results-box-head"><div><strong>比较条件矩阵</strong><small>先确认条件，再解读结果差异</small></div></header>${warning}<div class="results-table-wrap"><table class="results-table"><thead><tr><th>Run</th><th>比较资格</th><th>选定损毁</th><th>选组</th><th>有效时间轴</th><th>相对基准的条件差异</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function renderSolverComparison(){
  const missingLabel='未记录';
  const solver=state.payload.solver_comparison||{};
  const byRun=solver.by_run||{};
  const ids=currentRunIds();
  const fact=(row,key,formatter)=>displayValue(row?.[key],{formatter})||missingLabel;
  const rows=ids.map((id)=>{const row=byRun[id]||{};return `<tr><th>${esc(seriesLabel(id))}</th><td>${esc(fact(row,'solver_status'))}</td><td>${esc(fact(row,'objective',(v)=>number(v,4)))}</td><td>${esc(fact(row,'best_bound',(v)=>number(v,4)))}</td><td>${esc(fact(row,'gap',(v)=>pct(v)))}</td><td>${esc(fact(row,'solve_time_s',(v)=>`${number(v,2)} s`))}</td><td>${esc(fact(row,'cluster_lp_objective',(v)=>number(v,4)))}</td><td>${esc(fact(row,'f1',(v)=>number(v,4)))}</td><td>${esc(fact(row,'f2',(v)=>number(v,4)))}</td><td>${esc(fact(row,'f3',(v)=>number(v,4)))}</td></tr>`;}).join('');
  const proven=solver.proven_strict_orderings||[];
  const objectiveNote=solver.objective_definition_comparable
    ? (proven.length?proven.map((row)=>`有界限证据：${esc(row.higher_run_id)} 的可行下界 ${esc(number(row.higher_lower_bound,4))} 严格高于 ${esc(row.lower_run_id)} 的对偶上界 ${esc(number(row.lower_upper_bound,4))}。`).join('<br>'):'目标定义一致，但当前界限不足以证明最优目标存在严格差异。')
    : '目标定义不一致，不直接比较 objective。';
  refs.solverComparison.innerHTML=`<header class="results-box-head"><div><strong>求解事实与目标区间</strong><small>最大化问题：objective 为当前可行下界，best bound 为对偶上界</small></div></header><div class="results-table-wrap"><table class="results-table solver-table"><thead><tr><th>Run</th><th>终止状态</th><th>可行下界</th><th>对偶上界</th><th>Gap</th><th>求解时间</th><th>选组 LP</th><th>F1</th><th>F2</th><th>F3</th></tr></thead><tbody>${rows}</tbody></table></div><p class="comparison-scope-note">${objectiveNote}<br>F1/F2/F3 是优化目标分项，不是五维综合评价；普通 unmet 也不是独立复核的物理最小缺口。</p>`;
}
function renderComparison(){clearScopeError();renderViewState();updateTitles();renderConditions();renderConditionMatrix();renderMetrics();renderSolverComparison();renderChartControls();renderChart();renderDiff();renderBottom();prepareObjectDrilldown();}
function renderConditions(){
  if(state.workspace==='damage'){
    const roles=state.payload.roles||{};
    refs.conditionFacts.innerHTML=['R0','R1','R2'].map((role)=>`<a href="${esc(runHref(roles[role]))}" title="${esc(runLabel(roles[role]))}" aria-label="查看单次结果 ${role}">${role} ${roleLabel(role)}</a>`).join('<span>·</span>');
  }else{
    const ids=state.payload.run_ids||[];
    refs.conditionFacts.innerHTML=ids.map((id,index)=>`<a href="${esc(runHref(id))}" title="${esc(runLabel(id))}" aria-label="查看单次结果 ${esc(id)}">${id===state.payload.baseline_run_id?'基准':state.workspace==='multi'?`损毁条件 ${index+1}`:state.workspace==='exploratory'?`历史 Run ${index+1}`:`方案 ${index+1}`}</a>`).join('<span>·</span>');
  }
  refs.changeCondition.textContent='修改条件';
}
function summaryFor(id){
  return state.payload.run_summaries?.[id]||{};
}
function metricValue(summary,key){
  if(key==='required')return number(summary.required_sorties_total,0);
  if(key==='fulfilled')return number(summary.fulfilled_sorties_total,0);
  if(key==='unmet')return number(summary.unmet_sorties_total,0);
  if(key==='additional')return number(summary.additional_sorties_total,0);
  if(key==='participants')return number(summary.participating_airport_count,0);
  if(key==='solver')return displayValue(state.payload.solver_comparison?.by_run?.[summary.run_id]?.solver_status);
  return '—';
}
function renderMetrics(){
  const ids=currentRunIds();const cards=[
    ['需求','required'],['实际履行','fulfilled'],['当前缺口','unmet'],['额外调度','additional'],['参与机场','participants'],['求解状态','solver'],
  ];
  refs.metrics.innerHTML=cards.map(([title,key])=>`<article class="results-metric"><h4>${title}</h4>${ids.map((id)=>{const summary={...summaryFor(id),run_id:id};return `<div class="metric-line"><span>${esc(seriesLabel(id))}</span><b>${esc(metricValue(summary,key))}</b></div>`;}).join('')}</article>`).join('');
}
function timelineObjectBlock(){
  const t=state.payload.timeline||{};if(state.chartMode==='airport')return t.by_airport||{};if(state.chartMode==='mission')return t.by_mission||{};if(state.chartMode==='aircraft')return t.by_aircraft||{};return null;
}
function renderChartControls(){
  if(state.workspace==='exploratory'){refs.chartControls.classList.add('hidden');return;}
  refs.chartControls.classList.remove('hidden');
  [...refs.chartModes.querySelectorAll('button')].forEach((b)=>b.classList.toggle('active',b.dataset.chartMode===state.chartMode));
  [...refs.seriesKinds.querySelectorAll('button')].forEach((b)=>b.classList.toggle('active',b.dataset.seriesKind===state.seriesKind));
  if(state.chartMode==='all'){refs.objectSelect.classList.add('hidden');return;}
  const block=timelineObjectBlock();const ids=Object.keys(block||{});if(!ids.includes(state.chartObjectId))state.chartObjectId=ids[0]||null;
  refs.objectSelect.innerHTML=ids.map((id)=>`<option value="${esc(id)}" ${id===state.chartObjectId?'selected':''}>${esc(labelFor(state.chartMode,id))}</option>`).join('');refs.objectSelect.classList.toggle('hidden',!ids.length);
}
function chartTitleText(){const kind=state.seriesKind==='returns'?'返航':'出动';const object=state.chartMode==='all'?'':` · ${state.chartObjectId||'—'}`;return `${kind}架次时序比较${object}`;}
function objectModeLabel(){return ({airport:'机场',mission:'任务',aircraft:'机型'})[state.chartMode]||'对象';}
function comparisonSeries(){
  const t=state.payload.timeline||{},ids=currentRunIds();
  if(state.chartMode==='all'){
    if(state.workspace==='damage')return ids.map((id)=>{const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return {id,label:seriesLabel(id),values:t[state.seriesKind]?.[role]};});
    if(state.workspace==='configuration')return ids.map((id)=>({id,label:seriesLabel(id),values:t[state.seriesKind]?.[id]?.values}));
    return ids.map((id)=>({id,label:seriesLabel(id),values:t[state.seriesKind]?.[id]}));
  }
  const row=(timelineObjectBlock()||{})[state.chartObjectId]||{};
  return ids.map((id)=>({id,label:seriesLabel(id),values:row[id]?.[state.seriesKind]}));
}
function normalizeChartValue(value){
  if(value===null||value===undefined||typeof value==='boolean'||(typeof value==='string'&&!value.trim()))return null;
  const numeric=Number(value);return Number.isFinite(numeric)?numeric:null;
}
function validateChartData(windows,series,context){
  context=context||{};
  const invalid='时序数据无效，无法绘制比较图表。';
  const mismatch='比较时序轴不一致，无法绘制。';
  const fail=(message,detail)=>{console.error('Results chart data validation failed',{...context,...detail});return {ok:false,message};};
  if(!Array.isArray(windows))return fail(invalid,{series:'windows',value:windows,reason:'windows_not_array'});
  if(!Array.isArray(series))return fail(invalid,{series:'visible_series',value:series,reason:'series_not_array'});
  const normalizedWindows=[];
  for(let index=0;index<windows.length;index+=1){
    const value=normalizeChartValue(windows[index]);
    if(value===null)return fail(invalid,{series:'windows',index,value:windows[index],reason:'invalid_window'});
    normalizedWindows.push(value);
  }
  const normalizedSeries=[];
  for(const row of series){
    if(!Array.isArray(row?.values))return fail(invalid,{run_id:row?.id,series:context.series,value:row?.values,reason:'values_not_array'});
    if(row.values.length!==windows.length)return fail(mismatch,{run_id:row.id,series:context.series,expected:windows.length,actual:row.values.length,reason:'length_mismatch'});
    const values=[];
    for(let index=0;index<row.values.length;index+=1){
      const value=normalizeChartValue(row.values[index]);
      if(value===null||value<0)return fail(invalid,{run_id:row.id,series:context.series,index,value:row.values[index],reason:'invalid_value'});
      values.push(value);
    }
    normalizedSeries.push({...row,values});
  }
  return {ok:true,windows:normalizedWindows,series:normalizedSeries};
}
function renderChart(){
  if(!state.payload){refs.chart.innerHTML='<div class="results-placeholder">暂无比较数据</div>';return;}
  if(state.workspace==='exploratory'){refs.chartTitle.textContent='总体差异与对象下钻';refs.chart.innerHTML='<div class="results-placeholder"><strong>跨条件集合不共用单一时间轴</strong><span>请先查看条件矩阵，再在下方选择同一任务、机场、资源、机型或完整航链。</span></div>';refs.legend.innerHTML='';return;}
  refs.chartTitle.textContent=chartTitleText();
  if(state.chartMode!=='all'&&!state.chartObjectId){refs.chart.innerHTML=`<div class="results-placeholder">当前比较无可用${objectModeLabel()}时序</div>`;refs.legend.innerHTML='';return;}
  const checked=validateChartData(state.payload.timeline?.windows,comparisonSeries(),{workspace:state.workspace,series:state.seriesKind,chart_mode:state.chartMode,object_id:state.chartObjectId});
  if(!checked.ok){refs.chart.innerHTML=`<div class="results-placeholder chart-error">${esc(checked.message)}</div>`;refs.legend.innerHTML='';return;}
  const {windows,series}=checked;
  if(!windows.length||!series.length){refs.chart.innerHTML='<div class="results-placeholder">当前比较无可用时序数据</div>';refs.legend.innerHTML='';return;}
  const max=Math.max(1,...series.flatMap((s)=>s.values));
  const w=900,h=265,pad={l:48,r:18,t:18,b:34};
  const plotW=w-pad.l-pad.r, plotH=h-pad.t-pad.b;
  const x=(i)=>pad.l+(windows.length<=1?0:(i/(windows.length-1))*plotW);
  const y=(v)=>h-pad.b-(v/max)*plotH;
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
  refs.chart.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(chartTitleText())}"><g>${yTicks}</g><path d="M${pad.l} ${h-pad.b}H${w-pad.r}M${pad.l} ${pad.t}V${h-pad.b}" stroke="#48687b"/>${paths}<line class="results-hover-line" x1="${pad.l}" x2="${pad.l}" y1="${pad.t}" y2="${h-pad.b}" visibility="hidden"/>${xTicks}</svg><div class="results-chart-tooltip hidden" role="status"></div>`;
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
      tooltip.innerHTML=`<strong>T${esc(windows[index])}</strong>${series.map((row,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(row.label)} <b>${esc(number(row.values[index]))}</b></span>`).join('')}`;
      tooltip.classList.remove('hidden');
      const px=(xx/w)*rect.width;
      tooltip.style.left=`${Math.min(Math.max(px+10,8),Math.max(8,rect.width-190))}px`;
      tooltip.style.top='12px';
    });
    svg.addEventListener('mouseleave',hideTip);
  }
  refs.legend.innerHTML=series.map((s,i)=>`<span><i style="background:${colors[i%colors.length]}"></i>${esc(s.label)}</span>`).join('');
}
function renderDiff(){
  if(state.workspace==='damage'){
    const d=state.payload.difference_overview||{},summary=state.payload.summary||{};const rows=[
      ['已调度架次',signed(summary.scheduled_sorties_total?.damage_delta),signed(summary.scheduled_sorties_total?.cluster_delta)],
      ['峰值出动量变化',signed(d.peak_sorties?.damage_delta),signed(d.peak_sorties?.cluster_delta)],
      ['最大机场承接占比',signed(d.max_airport_departure_share?.damage_delta,{percent:true}),signed(d.max_airport_departure_share?.cluster_delta,{percent:true})],
      ['资源最低余量变化',signed(d.minimum_resource_remaining_ratio?.damage_delta,{percent:true}),signed(d.minimum_resource_remaining_ratio?.cluster_delta,{percent:true})],
      ['参与机场数量变化',signed(d.participating_airport_count?.damage_delta),signed(d.participating_airport_count?.cluster_delta)],
    ];refs.diff.innerHTML='<div class="diff-row"><span></span><span>损毁影响 R1−R0</span><span>组选调整 R2−R1</span></div>'+rows.map((r)=>`<div class="diff-row"><span>${r[0]}</span><span>${r[1]}</span><span>${r[2]}</span></div>`).join('')+'<div class="diff-note">仅报告后端确定性差值，不生成原因性判断或“最佳”结论。</div>';return;
  }
  if(state.workspace==='multi'){
    const d=state.payload.difference_overview||{};const ex=(obj,a,b,fmt=(x)=>esc(x))=>{const lo=obj?.[a],hi=obj?.[b];return [lo?`${fmt(lo.value)} · ${lo.run_ids.join(', ')}`:'—',hi?`${fmt(hi.value)} · ${hi.run_ids.join(', ')}`:'—'];};
    const rows=[['已调度架次',...ex(d.scheduled_sorties_total,'lowest','highest',number)],['峰值出动量',...ex(d.peak_sorties,'lowest','highest',number)],['最大机场承接占比',...ex(d.max_airport_departure_share,'lowest','highest',pct)],['资源最低余量',...ex(d.minimum_resource_remaining_ratio,'lowest','highest',pct)],['参与机场数量',...ex(d.participating_airport_count,'lowest','highest',number)]];
    refs.diff.innerHTML='<div class="diff-row"><span></span><span>低/早</span><span>高/晚</span></div>'+rows.map((r)=>`<div class="diff-row"><span>${r[0]}</span><span>${esc(r[1])}</span><span>${esc(r[2])}</span></div>`).join('')+'<div class="diff-note">极值用于描述场景差异，不代表场景优劣排序。</div>';return;
  }
  if(state.workspace==='exploratory'){
    const deltas=state.payload.descriptive_deltas_vs_baseline||{},base=state.payload.baseline_run_id;
    const ids=(state.payload.run_ids||[]).filter((id)=>id!==base);
    const rows=[['需求','required_sorties_total_delta_vs_baseline'],['实际履行','fulfilled_sorties_total_delta_vs_baseline'],['当前缺口','unmet_sorties_total_delta_vs_baseline'],['额外调度','additional_sorties_total_delta_vs_baseline'],['参与机场','participating_airport_count_delta_vs_baseline']];
    refs.diff.innerHTML=`<div class="diff-note"><b>相对基准 ${esc(base)} 的描述性差值</b></div>${rows.map(([label,key])=>`<div class="diff-row compact"><span>${label}</span><span>${ids.map((id)=>`${esc(shortRunId(id))} ${signed(deltas[id]?.[key])}`).join('<br>')||'—'}</span></div>`).join('')}<div class="diff-note">仅描述，不作因果归因；需求或时间轴不同时，数值差不表示单一因素效应。</div>`;return;
  }
  const deltas=state.payload.summary_deltas_vs_baseline||{},base=state.payload.baseline_run_id;const ids=(state.payload.run_ids||[]).filter((id)=>id!==base);
  const objectiveNote=state.payload.objective_comparable?'Raw objective 使用同一目标定义。':'Raw objective 的系数定义不同，不作直接方案优劣比较。';
  const rows=[
    ['已调度架次','scheduled_sorties_total_delta',(value)=>signed(value)],
    ['峰值架次','peak_sorties_delta',(value)=>signed(value)],
    ['最大承接占比','max_airport_departure_share_delta',(value)=>signed(value,{percent:true})],
    ['资源最低余量','minimum_resource_remaining_ratio_delta',(value)=>signed(value,{percent:true})],
    ['参与机场','participating_airport_count_delta',(value)=>signed(value)],
  ];
  refs.diff.innerHTML=`<div class="diff-note"><b>各方案相对 ${esc(base)}</b></div>`+rows.map(([label,key,format])=>`<div class="diff-row compact"><span>${label}</span><span>${ids.map((id)=>`${esc(id)} ${format((deltas[id]||{})[key])}`).join('<br>')||'—'}</span></div>`).join('')+`<div class="diff-note">全部差值均以后端指定基准 Run 为参照。${objectiveNote}</div>`;
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
  const lacksComparison=deriveViewState() !== VIEW_STATE.HAS_COMPARISON||!state.payload;
  refs.airportValueControls.classList.toggle('hidden',lacksComparison||state.bottomMode !== 'airports');
  if(lacksComparison){
    const viewState=deriveViewState();
    const message=viewState===VIEW_STATE.LOADING||viewState===VIEW_STATE.ERROR?'—':'暂无比较结果';
    refs.table.innerHTML=`<div class="results-placeholder">${message}</div>`;
    return;
  }
  const ids=currentRunIds();
  if(state.bottomMode==='airports'){
    const aids=Object.keys(state.payload.airports||{}).sort();refs.table.innerHTML=`<table class="results-table"><thead><tr><th>机场</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody>${aids.map((aid)=>`<tr><td title="${esc(aid)}">${esc(labelFor('airport',aid))}</td>${ids.map((id)=>`<td>${airportCell(aid,id)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;return;
  }
  if(state.bottomMode==='resources'){
    const cats=[['fuel','燃油'],['material','航材'],['munition','航弹']];refs.table.innerHTML=`<table class="results-table"><thead><tr><th>资源类别最低余量率</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody>${cats.map(([c,n])=>`<tr><td>${n}</td>${ids.map((id)=>`<td>${resourceCell(c,id)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;return;
  }
  const scheme=state.payload.scheme||{};const rowFor=(id)=>{if(state.workspace==='damage'){const role=Object.entries(state.payload.roles||{}).find(([,rid])=>rid===id)?.[0];return scheme[role]||{};}return scheme[id]||{};};
  refs.table.innerHTML=`<table class="results-table"><thead><tr><th>方案结构</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}</th>`).join('')}</tr></thead><tbody><tr><td>组选机场</td>${ids.map((id)=>`<td>${esc((rowFor(id).selected_cluster||[]).map((aid)=>labelFor('airport',aid)).join('、')||'—')}</td>`).join('')}</tr><tr><td>实际参与机场</td>${ids.map((id)=>`<td>${esc((rowFor(id).participating_airports||[]).map((aid)=>labelFor('airport',aid)).join('、')||'—')}</td>`).join('')}</tr><tr><td>出动集中度 HHI</td>${ids.map((id)=>`<td>${formatHhi(rowFor(id).departure_hhi)}</td>`).join('')}</tr><tr><td>跨场返航比例</td>${ids.map((id)=>`<td>${pct(rowFor(id).cross_return_ratio)}</td>`).join('')}</tr></tbody></table>`;
}

function comparisonType(){
  if(state.workspace==='multi')return 'damage';
  if(state.workspace==='configuration')return 'configuration';
  // The R0/R1/R2 workspace contains both a damage step and a configuration step;
  // no single strict object-comparison mode can validly cover all three at once.
  return 'exploratory';
}
function catalogLabels(){return state.payload.labels||Object.values(state.payload.labels_by_run||{})[0]||{};}
async function loadObjectCatalog(){
  const baseline=state.payload.baseline_run_id||currentRunIds()[0];
  if(!baseline)return null;
  const [metrics,situation]=await Promise.all([apiFetch(`/api/runs/${encodeURIComponent(baseline)}/metrics`),apiFetch(`/api/runs/${encodeURIComponent(baseline)}/situation`)]);
  return {metrics,situation};
}
function fillSelect(select,items,selected){
  select.innerHTML=items.map(([value,label])=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
  return items.some(([value])=>value===selected)?selected:items[0]?.[0]||null;
}
async function prepareObjectDrilldown(){
  const workspace=state.workspace;const view=state.workspaceStates[workspace];
  refs.objectType.value=view.objectType;refs.objectDetail.innerHTML='<div class="results-placeholder">正在读取对象目录…</div>';
  try{if(!view.objectCatalog)view.objectCatalog=await loadObjectCatalog();if(state.workspace!==workspace)return;renderObjectControls(view);await requestObjectComparison();}
  catch(error){showComparisonError(error);refs.objectDetail.innerHTML='<div class="results-placeholder chart-error">对象结果读取失败</div>';}
}
function renderObjectControls(view=state.workspaceStates[state.workspace]){
  const metrics=view.objectCatalog?.metrics||{};const labels=catalogLabels();const type=view.objectType;
  refs.objectType.value=type;const needsId=type==='task'||type==='airport'||type==='collaboration';
  refs.objectIdWrap.classList.toggle('hidden',!needsId);refs.objectAirportWrap.classList.toggle('hidden',!['resource','aircraft','collaboration'].includes(type));refs.objectResourceWrap.classList.toggle('hidden',type!=='resource');refs.objectAircraftWrap.classList.toggle('hidden',type!=='aircraft');
  if(needsId){const raw=type==='task'?Object.keys(metrics.tasks||{}):type==='airport'?Object.keys(metrics.airports||{}):Object.keys(metrics.tasks||{});const items=(type==='collaboration'?[['','全部任务']]:[]).concat(raw.sort().map((id)=>[id,type==='airport'?airportDisplayLabel(id,labels.airports?.[id]):labels.missions?.[id]||id]));view.objectId=fillSelect(refs.objectId,items,view.objectId);}
  if(['resource','aircraft','collaboration'].includes(type)){const airportIds=Object.keys(metrics.airports||{}).sort();const items=(type==='collaboration'?[['','所有机场']]:[]).concat(airportIds.map((id)=>[id,airportDisplayLabel(id,labels.airports?.[id])]));view.objectAirportId=fillSelect(refs.objectAirport,items,view.objectAirportId);}
  if(type==='resource'){const resourceRows=metrics.resources?.by_airport?.[view.objectAirportId]||{},meta=metrics.resources?.resource_types||{};view.objectResourceId=fillSelect(refs.objectResource,Object.keys(resourceRows).sort().map((id)=>[id,`${meta[id]?.name||id}${meta[id]?.unit?` (${meta[id].unit})`:''}`]),view.objectResourceId);}
  if(type==='aircraft'){const aircraftRows=metrics.aircraft_inventory?.by_airport?.[view.objectAirportId]||{};view.objectAircraftId=fillSelect(refs.objectAircraft,Object.keys(aircraftRows).sort().map((id)=>[id,labels.aircraft?.[id]||id]),view.objectAircraftId);}
}
async function requestObjectComparison(){
  const view=state.workspaceStates[state.workspace],runIds=currentRunIds(),baseline=state.payload.baseline_run_id||runIds[0];const body={run_ids:runIds,comparison_type:comparisonType(),baseline_run_id:baseline,object_type:view.objectType};
  if(view.objectType==='task'||view.objectType==='airport')body.object_id=view.objectId;
  if(view.objectType==='resource'){body.airport_id=view.objectAirportId;body.resource_type_id=view.objectResourceId;}
  if(view.objectType==='aircraft'){body.airport_id=view.objectAirportId;body.aircraft_type_id=view.objectAircraftId;}
  if(view.objectType==='collaboration'){if(view.objectId)body.object_id=view.objectId;if(view.objectAirportId)body.airport_id=view.objectAirportId;}
  const requiredMissing=view.objectType!=='collaboration'&&Object.entries(body).some(([key,value])=>key!=='object_id'&&key!=='airport_id'&&key!=='resource_type_id'&&key!=='aircraft_type_id'?false:value===null||value===undefined||value==='');
  if(requiredMissing||runIds.length<2){refs.objectDetail.innerHTML='<div class="results-placeholder">当前基准 Run 没有该类对象</div>';return;}
  refs.objectDetail.innerHTML='<div class="results-placeholder">正在读取对象级差异…</div>';const payload=await apiFetch('/api/results/object-comparison',{method:'POST',body});if(state.workspaceStates[state.workspace]!==view)return;view.objectPayload=payload;renderObjectDetail(payload);
}
function objectMetricRows(payload){const type=payload.object?.type;if(type==='task')return [['需求','required_total'],['实际履行','fulfilled_total'],['缺口','unmet_total'],['额外调度','additional_total']];if(type==='airport')return [['出动','departures_total'],['返航','returns_total'],['承接占比','departure_share']];if(type==='resource')return [['初始库存','initial']];if(type==='aircraft')return [['初始可用','baseline_initial_quantity']];return [['跨机场返航','cross_return_sorties'],['跨机场返航比例','cross_return_ratio'],['出动集中度 HHI','departure_hhi']];}
function renderObjectDetail(payload){
  const ids=payload.run_ids||[],type=payload.object?.type,rows=objectMetricRows(payload);const scalar=`<table class="results-table object-summary"><thead><tr><th>指标</th>${ids.map((id)=>`<th>${esc(seriesLabel(id))}<small>绝对值 / 相对基准</small></th>`).join('')}</tr></thead><tbody>${rows.map(([label,key])=>`<tr><th>${label}</th>${ids.map((id)=>{const item=payload.runs?.[id]||{},value=item.absolute?.[key],delta=item.delta_vs_baseline?.[key],isRatio=key.includes('ratio')||key==='departure_share';return `<td>${esc(displayValue(value,{formatter:(v)=>isRatio?pct(v):number(v)}))}<small>${id===payload.baseline_run_id?'基准':`Δ ${signed(delta,{percent:isRatio})}`}</small></td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
  const timelineFields=type==='airport'?['capacity.available','capacity.used_total','capacity.utilization']:type==='resource'?['remaining','replenishment_actual','consumed_increment','permanent_loss']:type==='aircraft'?['available_before_departure','in_use','departures','ready_releases']:type==='task'?['departures_timeline','returns_timeline']:[];
  const timeline=timelineFields.map((path)=>{const parts=path.split('.'),values=(id)=>parts.reduce((value,key)=>value?.[key],payload.runs?.[id]?.absolute);return `<table class="results-table compact-series"><thead><tr><th>${esc(path)}</th>${ids.map((id)=>`<th>${esc(shortRunId(id))}</th>`).join('')}</tr></thead><tbody><tr><td>逐窗绝对值</td>${ids.map((id)=>`<td>${Array.isArray(values(id))?esc(values(id).map((v)=>displayValue(v,{formatter:(x)=>number(x,2)})).join(' · ')):'未记录'}</td>`).join('')}</tr></tbody></table>`;}).join('');
  const permanentNote=type==='resource'&&Object.values(payload.runs||{}).some((run)=>run.absolute?.permanent_loss_recorded===false)?'<p class="comparison-scope-note warning">当前正式结果未记录实际 permanent_loss；不以 damage_adjusted_loss 代替。</p>':'';const chains=type==='collaboration'?renderChainChanges(payload.chain_changes||[],ids):'';
  refs.objectDetail.innerHTML=`${scalar}${timeline}${permanentNote}${chains}<p class="comparison-scope-note">表格保留全部 Run；交互图表仅显示前 6 个 Run，不限制历史比较集合规模。</p>`;
}
function renderChainChanges(changes,ids){if(!changes.length)return '<div class="results-placeholder">当前条件没有完整航链变化</div>';return `<div class="results-table-wrap"><table class="results-table chain-change-table"><thead><tr><th>出发机场</th><th>任务</th><th>返航机场</th><th>机型</th><th>出动/返航/再可用窗</th>${ids.map((id)=>`<th>${esc(shortRunId(id))}</th>`).join('')}</tr></thead><tbody>${changes.map((row)=>{const chain=row.chain||{};return `<tr><td>${esc(chain.origin_airport_id)}</td><td>${esc(chain.mission_id)}</td><td>${esc(chain.return_airport_id)}</td><td>${esc(chain.aircraft_type)}</td><td>T${esc(chain.depart_window)} / T${esc(chain.return_window)} / T${esc(chain.ready_window)}</td>${ids.map((id)=>`<td>${esc(number(row.by_run?.[id]?.sorties))}<small>Δ ${esc(signed(row.by_run?.[id]?.delta_vs_baseline))}</small></td>`).join('')}</tr>`;}).join('')}</tbody></table></div>`;}

workspaceButtons.forEach((b)=>b.addEventListener('click',()=>setWorkspace(b.dataset.workspace)));
refs.changeCondition.addEventListener('click',openOverlay);$('closeResultsOverlay').addEventListener('click',closeOverlay);$('cancelResultsOverlay').addEventListener('click',closeOverlay);refs.apply.addEventListener('click',applyComparison);
refs.rulesButton.addEventListener('click',openRules);
refs.retryButton.addEventListener('click',()=>{if(state.runsStatus==='error')loadInitial();else ensureWorkspaceCandidates(state.workspace,{force:true});});
refs.runSearch.addEventListener('input',()=>{state.draft.searchText=refs.runSearch.value;renderOverlay();});
refs.chartModes.addEventListener('click',(e)=>{const b=e.target.closest('[data-chart-mode]');if(!b||!state.payload)return;state.chartMode=b.dataset.chartMode;state.chartObjectId=null;renderChartControls();renderChart();captureWorkspaceState();persistSessionState();});
refs.seriesKinds.addEventListener('click',(e)=>{const b=e.target.closest('[data-series-kind]');if(!b||!state.payload||b.dataset.seriesKind===state.seriesKind)return;state.seriesKind=b.dataset.seriesKind;renderChartControls();renderChart();captureWorkspaceState();persistSessionState();});
refs.objectSelect.addEventListener('change',()=>{state.chartObjectId=refs.objectSelect.value||null;renderChart();captureWorkspaceState();persistSessionState();});
refs.objectType.addEventListener('change',async()=>{const view=state.workspaceStates[state.workspace];view.objectType=refs.objectType.value;view.objectId=null;view.objectAirportId=null;view.objectResourceId=null;view.objectAircraftId=null;renderObjectControls(view);try{await requestObjectComparison();}catch(error){showComparisonError(error);}});
refs.objectId.addEventListener('change',async()=>{state.workspaceStates[state.workspace].objectId=refs.objectId.value||null;try{await requestObjectComparison();}catch(error){showComparisonError(error);}});
refs.objectAirport.addEventListener('change',async()=>{const view=state.workspaceStates[state.workspace];view.objectAirportId=refs.objectAirport.value||null;view.objectResourceId=null;view.objectAircraftId=null;renderObjectControls(view);try{await requestObjectComparison();}catch(error){showComparisonError(error);}});
refs.objectResource.addEventListener('change',async()=>{state.workspaceStates[state.workspace].objectResourceId=refs.objectResource.value||null;try{await requestObjectComparison();}catch(error){showComparisonError(error);}});
refs.objectAircraft.addEventListener('change',async()=>{state.workspaceStates[state.workspace].objectAircraftId=refs.objectAircraft.value||null;try{await requestObjectComparison();}catch(error){showComparisonError(error);}});
bottomButtons.forEach((b)=>b.addEventListener('click',()=>{state.bottomMode=b.dataset.bottomMode;renderBottom();captureWorkspaceState();persistSessionState();}));airportValueButtons.forEach((b)=>b.addEventListener('click',()=>{if(!state.payload)return;state.airportValue=b.dataset.airportValue;renderBottom();captureWorkspaceState();persistSessionState();}));


refs.exportButton?.addEventListener('click',(e)=>{e.stopPropagation();toggleExportMenu();});
refs.exportMenu?.addEventListener('click',(e)=>{const b=e.target.closest('[data-export-format]');if(b)exportResults(b.dataset.exportFormat);});
document.addEventListener('click',(e)=>{if(!refs.exportMenu?.contains(e.target)&&!refs.exportButton?.contains(e.target))closeExportMenu();});
globalThis.addEventListener('app:account-ready',(e)=>{state.canExport=Boolean(e.detail?.permissions?.includes('results.export'));renderViewState();});
globalThis.addEventListener('pagehide',()=>{captureWorkspaceState();persistSessionState();});

async function init(){
  renderViewState();
  try{
    const account=await getAccount();
    state.userId=account.user_id;
    state.canExport=Boolean(account.permissions?.includes('results.export'));
    restoreSessionState();
  }catch(error){console.warn('Results user-scoped UI state is unavailable',error);}
  workspaceButtons.forEach((button)=>button.classList.toggle('active',button.dataset.workspace===state.workspace));
  await loadInitial();
  await restoreSavedComparisons();
  try{await autoApplyDefaultComparison();}catch(error){showComparisonError(error);}
}

init();
