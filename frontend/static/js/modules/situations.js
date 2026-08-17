import { apiFetch as requestJson, ApiError } from './api-client.js';
import {
  airportItem,
  bindSituationDom,
  byId as $,
  clone as deep,
  damageScenario,
  escapeHtml as esc,
  fieldValue as val,
  integerValue as int,
  missionItem,
  numberValue as num,
  refs,
  releaseSituationDom,
  resetSituationState,
  state,
  writable,
} from './situation-state.js';
import {
  beginMissionLocationPick,
  configureMap,
  drawMap,
  fitMap,
  focusObject,
  initMap,
  destroyMap,
} from './situation-map.js';
import {
  clearConflict,
  collapseOverview,
  configurePanels,
  destroyPanels,
  initPanels,
  setInspectorOpen,
  showConflict,
  syncWorkspaceChrome,
} from './situation-panels.js';
let lifecycleController = null;
let mounted = false;
let pendingPanelTransition = null;

function wasAborted(error) {
  return error?.name === 'AbortError' || error?.body?.name === 'AbortError';
}

async function apiFetch(path, options = {}) {
  try {
    return await requestJson(path, { ...options, signal: options.signal || lifecycleController?.signal });
  } catch (error) {
    if (!mounted && wasAborted(error)) return new Promise(() => {});
    throw error;
  }
}

function showMessage(text,type='info'){if(!mounted||!refs.msg)return;refs.msg.textContent=text;refs.msg.className=`workspace-message ${type}`;refs.msg.classList.remove('hidden');clearTimeout(showMessage.t);showMessage.t=setTimeout(()=>refs.msg?.classList.add('hidden'),4200)}
function errText(e){return e instanceof ApiError?`${e.message}${e.field?`（${e.field}）`:''}`:'操作失败'}
function setModal(el,open){el.classList.toggle('open',open);el.setAttribute('aria-hidden',open?'false':'true')}
function confirmAction(text,label='确认'){return new Promise(resolve=>{refs.confirmBody.innerHTML=`<div class="inline-message warning">${esc(text)}</div>`;refs.confirmAction.textContent=label;setModal(refs.confirmModal,true);const done=v=>{setModal(refs.confirmModal,false);refs.confirmAction.onclick=null;refs.confirmCancel.onclick=null;resolve(v)};refs.confirmAction.onclick=()=>done(true);refs.confirmCancel.onclick=()=>done(false)})}
function markDirty(){if(!state.working)return;state.dirty=true;renderHeader();renderOverview();drawMap()}
async function canonicalizeWorking(candidate){const d=await apiFetch('/api/situations/working-copy/canonicalize',{method:'POST',body:{situation:candidate}});return deep(d.situation)}
function aircraftName(id){const x=state.aircraft.find(v=>v.aircraft_type.aircraft_type_id===id);return x?.aircraft_type?.name||id} function resourceName(id){const x=state.resources.find(v=>v.resource_type.resource_type_id===id);return x?.resource_type?.name||id}
function savedTimeLabel(value){if(!value)return'';const parsed=new Date(value);if(!Number.isNaN(parsed.getTime()))return`${String(parsed.getHours()).padStart(2,'0')}:${String(parsed.getMinutes()).padStart(2,'0')}`;const match=String(value).match(/(?:T|\s)(\d{2}:\d{2})/);return match?.[1]||String(value)}
function renderHeader(){if(!state.working){refs.meta.textContent='';refs.saveState.textContent='未打开';refs.saveState.className='situation-save-state';refs.save.disabled=true;refs.del.disabled=true;return}const savedTime=savedTimeLabel(state.meta?.updated_at);refs.meta.textContent=savedTime?`最近保存 ${savedTime}`:'';refs.saveState.textContent=state.persisted?(state.dirty?'未保存':'已保存'):'新建未保存';refs.saveState.className=`situation-save-state ${state.dirty||!state.persisted?'warning':'success'}`;refs.save.disabled=!writable()||(!state.dirty&&state.persisted);refs.del.disabled=!writable()||!state.persisted}
async function loadSituationList(selectId=null){const d=await apiFetch('/api/situations?limit=500&offset=0');state.list=d.items||[];refs.select.innerHTML='<option value="">选择情境…</option>'+state.list.map(x=>`<option value="${esc(x.situation_id)}">${esc(x.name)}（${esc(x.situation_id)}）</option>`).join('');if(selectId)refs.select.value=selectId}
async function canDiscardSituation(){if(!state.dirty&&!state.panelDraftDirty)return true;return confirmAction('当前情境有未保存修改。继续会丢弃这些修改。','放弃修改')}
function setPanelDraftDirty(dirty=true){state.panelDraftDirty=dirty;refs.panelDraftStatus?.classList.toggle('hidden',!dirty);if(!dirty){pendingPanelTransition=null;refs.body?.querySelector('#panelDraftWarning')?.remove()}}
function bindPanelDraft(){refs.body.querySelectorAll('input,select,textarea').forEach(el=>{const mark=()=>setPanelDraftDirty(true);el.addEventListener('input',mark);el.addEventListener('change',mark)})}
function showPanelDraftWarning(){let warning=$('panelDraftWarning');if(!warning){refs.body.insertAdjacentHTML('afterbegin',`<div id="panelDraftWarning" class="inline-message warning panel-draft-warning"><strong>当前修改尚未应用</strong><span>先继续当前编辑，或明确放弃后再切换。</span><div class="panel-draft-actions"><button id="continuePanelEditing" class="btn ghost" type="button">继续编辑</button><button id="discardPanelAndSwitch" class="btn danger" type="button">放弃并切换</button></div></div>`);warning=$('panelDraftWarning')}$('continuePanelEditing').onclick=()=>{pendingPanelTransition=null;warning.remove()};$('discardPanelAndSwitch').onclick=()=>{const transition=pendingPanelTransition;clearPanelDraft();Promise.resolve(transition?.()).catch(error=>showMessage(errText(error),'error'))}}
function requestPanelTransition(transition){if(!state.panelDraftDirty){clearPanelDraft();return transition()}pendingPanelTransition=transition;showPanelDraftWarning();return false}
function clearPanelDraft(){setPanelDraftDirty(false);state.draftMissionCoord=null}
async function openSituation(id,{force=false}={}){if(!id)return;if(!force&&!(await canDiscardSituation())){refs.select.value=state.working?.situation_id||'';return}try{const d=await apiFetch(`/api/situations/${encodeURIComponent(id)}`);clearPanelDraft();clearConflict();state.working=deep(d.situation);state.savedHash=d.content_hash;state.persisted=true;state.meta=d;state.dirty=false;state.mode='select';state.selected=null;refs.tools.querySelectorAll('[data-mode]').forEach(b=>b.classList.remove('active'));renderAll();fitMap()}catch(e){showMessage(errText(e),'error')}}
async function newSituation(){if(!writable()){showMessage('当前账号没有情境维护权限。','error');return}if(!(await canDiscardSituation()))return;clearPanelDraft();renderNewSituationEditor();setTimeout(()=>$('newSituationId')?.focus(),30)}
function renderNewSituationEditor(){state.mode='select';state.selected=null;refs.tools.querySelectorAll('[data-mode]').forEach(button=>button.classList.remove('active'));refs.inspector.dataset.kind='situation-editor';refs.inspectorTitle.textContent='新建情境';refs.inspectorSubtitle.textContent='创建未保存的 Working Copy';refs.body.innerHTML=`<div class="compact-grid"><div class="field wide required"><label>情境编号</label><input id="newSituationId" class="control" placeholder="例如 SITUATION-01"></div><div class="field wide required"><label>名称</label><input id="newSituationName" class="control"></div><div class="field wide"><label>说明</label><textarea id="newSituationDescription" class="control textarea-control" rows="4"></textarea></div></div><div id="newSituationMessage" class="inline-message hidden"></div><div class="inspector-footer"><button id="cancelNewSituation" class="btn ghost" type="button">取消</button><button id="createNewSituation" class="btn primary" type="button">创建</button></div>`;setInspectorOpen(true);collapseOverview();bindPanelDraft();$('cancelNewSituation').onclick=()=>{clearPanelDraft();renderInspector();syncWorkspaceChrome()};$('createNewSituation').onclick=createWorking}
async function createWorking(){const id=$('newSituationId').value.trim(),name=$('newSituationName').value.trim(),message=$('newSituationMessage');if(!id||!name){message.textContent='情境编号和名称不能为空。';message.className='inline-message error';message.classList.remove('hidden');return}state.working={situation_id:id,name,description:$('newSituationDescription').value.trim()||null,airports:[],missions:[],damage_scenarios:[]};state.savedHash=null;state.persisted=false;state.meta=null;state.dirty=true;state.selected=null;refs.select.value='';clearPanelDraft();renderAll()}
async function saveSituation(){if(!state.working||!writable())return;if(state.panelDraftDirty){showMessage('请先将右侧表单“应用”到当前情境，再保存。','error');return}try{let d;if(state.persisted)d=await apiFetch(`/api/situations/${encodeURIComponent(state.working.situation_id)}`,{method:'PUT',body:{situation:state.working,expected_content_hash:state.savedHash}});else d=await apiFetch('/api/situations',{method:'POST',body:{situation:state.working}});state.working=deep(d.situation);state.savedHash=d.content_hash;state.persisted=true;state.meta={...(state.meta||{}),...d};state.dirty=false;clearConflict();await loadSituationList(state.working.situation_id);renderAll();showMessage('情境已保存。','success')}catch(e){if(e instanceof ApiError&&e.status===409){showConflict();showMessage('保存失败：服务器中的情境已经变化，本地修改仍保留。','error')}else showMessage(errText(e),'error')}}
async function deleteSituation(){if(!state.persisted||!state.working)return;const active=state.meta?.active_run_count||0,hist=state.meta?.historical_run_count||0;if(!(await confirmAction(`删除情境 ${state.working.name}？当前关联 ${active} 个活动 Run、${hist} 个历史 Run。历史 Run 的冻结快照不会被修改；活动 Run 存在时后端可能拒绝删除。`,'删除情境')))return;try{await apiFetch(`/api/situations/${encodeURIComponent(state.working.situation_id)}`,{method:'DELETE',body:{expected_content_hash:state.savedHash}});clearPanelDraft();state.working=null;state.persisted=false;state.savedHash=null;state.meta=null;state.dirty=false;state.selected=null;await loadSituationList();renderAll();showMessage('情境已删除。','success')}catch(e){showMessage(errText(e),'error')}}
function setMode(mode){state.mode=mode;state.selected=null;state.draftMissionCoord=null;if(mode==='airport')state.tempAirportIds=new Set();if(mode!=='layers')collapseOverview();refs.tools.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));setInspectorOpen(true);renderInspector()}
function lockEditorForReadOnly(){if(writable())return;refs.body.querySelectorAll('input,select,textarea,button').forEach(el=>{el.disabled=true});refs.inspectorSubtitle.textContent=`${refs.inspectorSubtitle.textContent} · 只读`; }
function applyPermissionUi(){const can=writable();refs.newBtn.disabled=!can;document.getElementById('overviewEditSituationInfo').disabled=!can;for(const mode of ['airport','mission','damage']){const b=refs.tools.querySelector(`[data-mode="${mode}"]`);if(b){b.disabled=!can;b.title=can?'':'当前账号为只读权限';}}}
function renderAll(){applyPermissionUi();renderHeader();renderOverview();renderInspector();drawMap();syncWorkspaceChrome()}
function renderOverview(){const s=state.working;const ac=s?.airports?.length||0,mc=s?.missions?.length||0,dc=s?.damage_scenarios?.length||0;refs.overviewCounts.textContent=`机场 ${ac} · 任务 ${mc} · 损毁 ${dc}`;const col=(title,items)=>`<div class="overview-column"><h3>${title}</h3>${items.length?items.map(item=>`<div class="overview-item"><span class="overview-item-key">${esc(item[0])}</span><span class="overview-item-value"><span>${esc(item[1])}</span>${item[2]?`<small>${esc(item[2])}</small>`:''}</span></div>`).join(''):'<div class="overview-empty">暂无</div>'}</div>`;refs.overviewContent.innerHTML=col('机场',(s?.airports||[]).map(item=>[item.airport.airport_name,item.airport.airport_id]))+col('任务',(s?.missions||[]).map(mission=>[mission.mission_id,mission.name]))+col('损毁场景',(s?.damage_scenarios||[]).map(scenario=>[scenario.damage_scenario_id,scenario.name,`${damageCategoryLabel(scenario.category)} · ${scenario.events.length} 事件`]));}
function objectCard(type,id,title,sub){return `<button class="object-card ${state.selected?.type===type&&state.selected?.id===id?'selected':''}" type="button" data-object-type="${type}" data-object-id="${esc(id)}"><span><strong>${esc(title)}</strong><small>${esc(sub)}</small></span><svg class="ui-icon"><use href="#i-arrow-right"></use></svg></button>`}
function renderInspector(){if(!state.working){refs.body.replaceChildren();setInspectorOpen(false);return}if(state.mode==='airport'){setInspectorOpen(true);renderAirportCandidates();return}if(state.mode==='mission'){setInspectorOpen(true);renderMissionMode();return}if(state.mode==='damage'){setInspectorOpen(true);renderDamageMode();return}if(state.selected?.type==='airport'){setInspectorOpen(true);renderAirportEditor(state.selected.id);return}if(state.selected?.type==='mission'){setInspectorOpen(true);renderMissionEditor(state.selected.id);return}refs.body.replaceChildren();setInspectorOpen(false)}
function renderSituationInfoEditor(){collapseOverview();refs.inspectorTitle.textContent='情境信息';refs.inspectorSubtitle.textContent=state.working.situation_id;refs.body.innerHTML=`<div class="compact-grid"><div class="field wide"><label>情境编号</label><input class="control" value="${esc(state.working.situation_id)}" readonly></div><div class="field wide"><label>名称</label><input id="editSituationName" class="control" value="${esc(state.working.name)}"></div><div class="field wide"><label>说明</label><textarea id="editSituationDescription" class="control textarea-control" rows="4">${esc(state.working.description||'')}</textarea></div></div><div class="inspector-footer"><button id="cancelSituationInfo" class="btn ghost" type="button">取消</button><button id="applySituationInfo" class="btn primary" type="button">应用到情境</button></div>`;bindPanelDraft();$('cancelSituationInfo').onclick=()=>{clearPanelDraft();renderInspector()};$('applySituationInfo').onclick=async()=>{const name=$('editSituationName').value.trim();if(!name){showMessage('情境名称不能为空。','error');return}try{const candidate=deep(state.working);candidate.name=name;candidate.description=$('editSituationDescription').value.trim()||null;state.working=await canonicalizeWorking(candidate);clearPanelDraft();markDirty();renderSituationInfoEditor();showMessage('情境信息已应用到当前情境。','success')}catch(e){showMessage(errText(e),'error')}};lockEditorForReadOnly()}
async function ensureAirportCatalog(){if(state.airportCatalog.length)return;let offset=0,total=1,out=[];while(offset<total){const d=await apiFetch(`/api/airports?limit=500&offset=${offset}`);out.push(...(d.items||[]));total=d.total||0;offset+=500}state.airportCatalog=out}
async function renderAirportCandidates() {
  refs.inspector.dataset.kind = 'airport-candidates';
  refs.inspectorTitle.textContent = '添加机场';
  refs.inspectorSubtitle.textContent = `当前情境已有 ${state.working.airports.length} 个机场`;
  refs.body.innerHTML = '<div class="empty-state">正在读取机场基础库…</div>';
  try {
    await ensureAirportCatalog();
    const existing = new Set(state.working.airports.map((item) => item.airport.airport_id));
    const regions = [...new Set(state.airportCatalog.map((item) => item.region).filter(Boolean))]
      .sort((left, right) => String(left).localeCompare(String(right), 'zh-CN'));
    refs.body.innerHTML = `
      <div class="candidate-toolbar">
        <input id="airportCandidateSearch" class="control" placeholder="名称或编号">
        <select id="airportCandidateRole" class="control">
          <option value="">全部类型</option><option value="military">军用</option>
          <option value="joint">军民合用</option><option value="civil">民用</option>
        </select>
        <select id="airportCandidateRegion" class="control">
          <option value="">全部区域</option>
          ${regions.map((region) => `<option value="${esc(region)}">${esc(region)}</option>`).join('')}
        </select>
      </div>
      <div id="airportCandidateList" class="candidate-list"></div>
      <div class="inspector-footer"><button id="addAirportsToSituation" class="btn primary" type="button">加入当前情境（0）</button></div>`;

    const updateCount = () => {
      $('addAirportsToSituation').textContent = `加入当前情境（${state.tempAirportIds.size}）`;
    };
    const draw = () => {
      const query = $('airportCandidateSearch').value.trim().toLowerCase();
      const role = $('airportCandidateRole').value;
      const region = $('airportCandidateRegion').value;
      state.candidateQuery = query;
      state.candidateRole = role;
      state.candidateRegion = region;
      const rows = state.airportCatalog.filter((item) =>
        (!query || `${item.airport_id} ${item.airport_name}`.toLowerCase().includes(query)) &&
        (!role || item.role === role) &&
        (!region || String(item.region || '') === region));
      $('airportCandidateList').innerHTML = rows.map((item) => {
        const alreadyAdded = existing.has(item.airport_id);
        const checked = state.tempAirportIds.has(item.airport_id);
        const status = item.configuration_complete === true ? '运行数据已配置' : '运行数据待配置';
        return `<label class="candidate-row${alreadyAdded ? ' disabled' : ''}${checked ? ' selected' : ''}">
          <input type="checkbox" value="${esc(item.airport_id)}" ${checked ? 'checked' : ''} ${alreadyAdded ? 'disabled' : ''}>
          <span><strong>${esc(item.airport_name)}</strong><small>${esc(item.airport_id)} · ${esc(item.region || '未分区')} · ${status}</small></span>
          <span>${alreadyAdded ? '已加入' : '未加入'}</span>
        </label>`;
      }).join('') || '<div class="empty-state">没有匹配机场。</div>';
      refs.body.querySelectorAll('#airportCandidateList input').forEach((checkbox) => {
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) state.tempAirportIds.add(checkbox.value);
          else state.tempAirportIds.delete(checkbox.value);
          updateCount();
          drawMap();
        });
      });
      updateCount();
      drawMap();
    };
    $('airportCandidateSearch').addEventListener('input', draw);
    $('airportCandidateRole').addEventListener('change', draw);
    $('airportCandidateRegion').addEventListener('change', draw);
    $('addAirportsToSituation').addEventListener('click', addSelectedAirports);
    draw();
  } catch (error) {
    refs.body.innerHTML = `<div class="inline-message error">${esc(errText(error))}</div>`;
  }
}
async function addSelectedAirports(){const ids=[...state.tempAirportIds];if(!ids.length)return;try{let s=state.working;for(const id of ids){const d=await apiFetch('/api/situations/working-copy/copy-airport',{method:'POST',body:{situation:s,airport_id:id}});s=d.situation}state.working=deep(s);state.tempAirportIds=new Set();markDirty();renderAirportCandidates();showMessage(`已加入 ${ids.length} 个机场，尚未保存。`,'success')}catch(e){showMessage(errText(e),'error')}}
function opt(items,current,getId,getName){return `<option value="">请选择…</option>${items.map(x=>{const id=getId(x),name=getName(x);return `<option value="${esc(id)}" ${String(id)===String(current)?'selected':''}>${esc(name)}（${esc(id)}）</option>`}).join('')}`}
function removeRowButton(){return '<button class="mini-button remove-row" type="button" aria-label="删除此行"><svg class="ui-icon"><use href="#i-close"></use></svg></button>'}
function supportRow(r={}){return `<div class="dynamic-row support-row"><div class="field"><label>机型</label><select class="control row-aircraft">${opt(state.aircraft,r.aircraft_type_id,x=>x.aircraft_type.aircraft_type_id,x=>x.aircraft_type.name)}</select></div><div class="field"><label>数量</label><input class="control row-initial" type="number" min="0" value="${esc(val(r.initial_quantity))}"></div><div class="field"><label>整备/窗</label><input class="control row-reset" type="number" min="0" value="${esc(val(r.tau_reset_windows))}"></div>${removeRowButton()}</div>`}
function stockRow(r={}){return `<div class="dynamic-row stock-row"><div class="field"><label>资源</label><select class="control row-resource">${opt(state.resources,r.resource_type_id,x=>x.resource_type.resource_type_id,x=>x.resource_type.name)}</select></div><div class="field"><label>库存</label><input class="control row-stock" type="number" min="0" step="any" value="${esc(val(r.initial_quantity))}"></div><div class="field"><label>补给/窗</label><input class="control row-cap" type="number" min="0" step="any" value="${esc(val(r.replenishment_capacity_per_window))}"></div>${removeRowButton()}</div>`}
function replenishRow(r={}){return `<div class="dynamic-row replenish-row"><div class="field"><label>资源</label><select class="control row-resource">${opt(state.resources,r.resource_type_id,x=>x.resource_type.resource_type_id,x=>x.resource_type.name)}</select></div><div class="field"><label>时间窗</label><input class="control row-slot" type="number" min="0" value="${esc(val(r.slot))}"></div><div class="field"><label>实际补给</label><input class="control row-qty" type="number" min="0" step="any" value="${esc(val(r.quantity))}"></div>${removeRowButton()}</div>`}
function bindDynamic(addId,target,fn){const b=$(addId);if(b)b.onclick=()=>{setPanelDraftDirty(true);$(target).insertAdjacentHTML('beforeend',fn({}));bindRemoves()};bindRemoves()}function bindRemoves(){refs.body.querySelectorAll('.remove-row').forEach(b=>b.onclick=()=>{setPanelDraftDirty(true);b.closest('.dynamic-row').remove()})}
function renderAirportEditor(id) {
  refs.inspector.dataset.kind = 'airport-editor';
  collapseOverview();
  const item = airportItem(id);
  if (!item) return;
  const airport = item.airport;
  const profile = item.operational_profile;
  refs.inspectorTitle.textContent = airport.airport_name;
  refs.inspectorSubtitle.textContent = `${airport.airport_id} · ${profile.configuration_complete ? '运行数据已配置' : '运行数据待配置'}`;
  refs.body.innerHTML = `
    <section class="editor-section">
      <h3>基础信息</h3>
      <dl class="airport-facts">
        <div><dt>编号</dt><dd>${esc(airport.airport_id)}</dd></div>
        <div><dt>类型</dt><dd>${esc(airport.facility_type || airport.role || '—')}</dd></div>
        <div><dt>区域</dt><dd>${esc(airport.region || '—')}</dd></div>
        <div><dt>坐标</dt><dd>${esc(airport.longitude)}, ${esc(airport.latitude)}</dd></div>
      </dl>
    </section>
    <section class="editor-section">
      <h3>运行保障</h3>
      <div class="compact-grid">
        <div class="field"><label>保障等级</label><input id="sitSupportLevel" class="control" value="${esc(val(profile.support_level))}"></div>
        <div class="field"><label>单时间窗容量</label><input id="sitCapacity" class="control" type="number" min="0" value="${esc(val(profile.capacity_per_window))}"></div>
        <label class="check-row"><input id="sitConfigComplete" type="checkbox" ${profile.configuration_complete ? 'checked' : ''}> 运行保障配置完整</label>
      </div>
    </section>
    <section class="editor-section">
      <h3>支持机型</h3><div id="sitSupportRows">${(profile.aircraft_support || []).map(supportRow).join('')}</div>
      <button id="sitAddSupport" class="btn ghost" type="button">添加机型</button>
    </section>
    <section class="editor-section">
      <h3>保障资源</h3><div id="sitStockRows">${(profile.resource_stocks || []).map(stockRow).join('')}</div>
      <button id="sitAddStock" class="btn ghost" type="button">添加资源</button>
    </section>
    <section class="editor-section">
      <h3>补给计划</h3><div id="sitReplenishRows">${(item.resource_replenishments || []).map(replenishRow).join('')}</div>
      <button id="sitAddReplenish" class="btn ghost" type="button">添加补给</button>
    </section>
    <div class="inspector-footer">
      <button id="cancelAirportEdit" class="btn ghost" type="button">取消</button>
      <button id="restoreAirportBase" class="btn" type="button">恢复 Base Data 基线</button>
      <button id="removeAirport" class="btn danger" type="button">移出情境</button>
      <button id="applyAirport" class="btn primary" type="button">应用</button>
    </div>`;
  bindDynamic('sitAddSupport', 'sitSupportRows', supportRow);
  bindDynamic('sitAddStock', 'sitStockRows', stockRow);
  bindDynamic('sitAddReplenish', 'sitReplenishRows', replenishRow);
  bindPanelDraft();
  $('cancelAirportEdit').onclick = () => { clearPanelDraft(); setMode('select'); };
  $('applyAirport').onclick = applyAirport;
  $('removeAirport').onclick = removeAirport;
  $('restoreAirportBase').onclick = restoreAirportBase;
  lockEditorForReadOnly();
}
function collectRows(sel,fn){return [...refs.body.querySelectorAll(sel)].map(fn)}
async function applyAirport(){try{const candidate=deep(state.working);const x=candidate.airports.find(v=>v.airport.airport_id===state.selected.id);if(!x)return;x.operational_profile={...x.operational_profile,configuration_complete:$('sitConfigComplete').checked,capacity_per_window:int($('sitCapacity').value),support_level:$('sitSupportLevel').value.trim()||null,aircraft_support:collectRows('.support-row',r=>({aircraft_type_id:r.querySelector('.row-aircraft').value,initial_quantity:int(r.querySelector('.row-initial').value),tau_reset_windows:int(r.querySelector('.row-reset').value)})),resource_stocks:collectRows('.stock-row',r=>({resource_type_id:r.querySelector('.row-resource').value,initial_quantity:num(r.querySelector('.row-stock').value),replenishment_capacity_per_window:num(r.querySelector('.row-cap').value)}))};x.resource_replenishments=collectRows('.replenish-row',r=>({resource_type_id:r.querySelector('.row-resource').value,slot:int(r.querySelector('.row-slot').value),quantity:num(r.querySelector('.row-qty').value)}));state.working=await canonicalizeWorking(candidate);clearPanelDraft();markDirty();showMessage('机场配置已应用到当前情境，尚未保存。','success');renderInspector()}catch(e){showMessage(errText(e),'error')}}
async function restoreAirportBase(){if(!(await confirmAction('恢复会用当前 Base Data 的机场及运行配置替换该情境机场配置；现有本情境补给安排也会被清除。','恢复基础配置')))return;try{const d=await apiFetch('/api/situations/working-copy/copy-airport',{method:'POST',body:{situation:state.working,airport_id:state.selected.id}});state.working=deep(d.situation);clearPanelDraft();markDirty();renderInspector();showMessage('已恢复基础配置，尚未保存。','success')}catch(e){showMessage(errText(e),'error')}}
async function removeAirport(){const id=state.selected.id;const refsDamage=state.working.damage_scenarios.flatMap(s=>s.events).filter(e=>e.target.airport_id===id);if(refsDamage.length){showMessage(`该机场仍被 ${refsDamage.length} 个损毁事件引用，请先删除相关事件。`,'error');return}if(!(await confirmAction(`从当前情境移除机场 ${id}？只影响当前情境，不删除 Base Data。`,'移出情境')))return;state.working.airports=state.working.airports.filter(x=>x.airport.airport_id!==id);clearPanelDraft();state.selected=null;markDirty();renderInspector()}
async function ensureMissionData(){if(!state.missionCatalog.length){let offset=0,total=1,out=[];while(offset<total){const d=await apiFetch(`/api/missions?limit=500&offset=${offset}`);out.push(...(d.items||[]));total=d.total||0;offset+=500}state.missionCatalog=out}if(!state.missionHistory.length){const h=await apiFetch('/api/missions/history?limit=500');state.missionHistory=h.items||[]}}
async function renderMissionMode(){refs.inspector.dataset.kind='mission-editor';refs.inspectorTitle.textContent='任务';refs.inspectorSubtitle.textContent='新建、从基础库或历史 Run 复制';refs.body.innerHTML='<div class="empty-state">正在读取任务来源…</div>';try{await ensureMissionData();refs.body.innerHTML=`<div class="mode-actions"><button id="newMissionAction" class="btn primary" type="button">新建任务</button></div><div class="inspector-section"><h3>任务</h3><div class="object-list">${state.missionCatalog.map(x=>objectCard('template',x.mission.mission_id,x.mission.name,`${x.mission.mission_id} · T${x.mission.window_start_slot}–T${x.mission.window_end_slot}`)).join('')||'<div class="field-note">暂无任务。</div>'}</div></div><div class="inspector-section"><h3>历史 Run Snapshot</h3><div class="object-list">${state.missionHistory.slice(0,50).map((x,i)=>objectCard('history',String(i),x.mission.name,`${x.mission.mission_id} · ${x.source_run_id}`)).join('')||'<div class="field-note">暂无历史任务。</div>'}</div></div>`;$('newMissionAction').onclick=()=>renderMissionEditor(null);refs.body.querySelectorAll('[data-object-type="template"]').forEach(b=>b.onclick=()=>copyMissionTemplate(b.dataset.objectId));refs.body.querySelectorAll('[data-object-type="history"]').forEach(b=>b.onclick=()=>copyHistoricalMission(Number(b.dataset.objectId)))}catch(e){refs.body.innerHTML=`<div class="inline-message error">${esc(errText(e))}</div>`}}
async function copyMissionTemplate(id){if(state.working.missions.some(x=>x.mission_id===id)){showMessage('当前情境已存在同编号任务。','error');return}try{const d=await apiFetch('/api/situations/working-copy/copy-mission',{method:'POST',body:{situation:state.working,mission_id:id}});state.working=deep(d.situation);markDirty();showMessage('任务已复制到情境，尚未保存。','success');renderMissionMode()}catch(e){showMessage(errText(e),'error')}}
async function copyHistoricalMission(index){const m=state.missionHistory[index]?.mission;if(!m)return;if(state.working.missions.some(x=>x.mission_id===m.mission_id)){showMessage('当前情境已存在同编号任务；可先打开已有任务编辑。','error');return}try{const d=await apiFetch('/api/situations/working-copy/copy-mission',{method:'POST',body:{situation:state.working,mission:m}});state.working=deep(d.situation);markDirty();showMessage('历史任务快照已复制到情境。','success');renderMissionMode()}catch(e){showMessage(errText(e),'error')}}
function missionReqRow(r={}){return `<div class="dynamic-row mission-req"><div class="field"><label>机型</label><select class="control row-aircraft">${opt(state.aircraft,r.aircraft_type_id,x=>x.aircraft_type.aircraft_type_id,x=>x.aircraft_type.name)}</select></div><div class="field"><label>架次</label><input class="control row-sorties" type="number" min="1" value="${esc(val(r.required_sorties))}"></div><div class="field"><label>作业/窗</label><input class="control row-work" type="number" min="0" value="${esc(val(r.tau_work_windows))}"></div>${removeRowButton()}</div>`}
function renderMissionEditor(id){refs.inspector.dataset.kind='mission-editor';collapseOverview();const m=id?missionItem(id):{mission_id:'',name:'',longitude:'',latitude:'',window_start_slot:0,window_end_slot:1,aircraft_requirements:[]};refs.inspectorTitle.textContent=id?m.name:'新建任务';refs.inspectorSubtitle.textContent=id?`${m.mission_id} · 情境任务快照`:'新任务只进入当前情境';refs.body.innerHTML=`<div class="compact-grid"><div class="field"><label>任务编号</label><input id="sitMissionId" class="control" value="${esc(m.mission_id)}" ${id?'readonly':''}></div><div class="field"><label>名称</label><input id="sitMissionName" class="control" value="${esc(m.name)}"></div><div class="field"><label>经度</label><input id="sitMissionLon" class="control" type="number" step="any" value="${esc(val(m.longitude))}"></div><div class="field"><label>纬度</label><input id="sitMissionLat" class="control" type="number" step="any" value="${esc(val(m.latitude))}"></div><div class="field wide"><button id="pickMissionLocation" class="btn ghost" type="button">从地图取点</button></div><div class="field"><label>开始窗</label><input id="sitMissionStart" class="control" type="number" min="0" value="${esc(val(m.window_start_slot))}"></div><div class="field"><label>结束窗（不含）</label><input id="sitMissionEnd" class="control" type="number" min="1" value="${esc(val(m.window_end_slot))}"></div></div><div class="inspector-section"><h3>各机型需求与作业时间</h3><div id="sitMissionReqs">${(m.aircraft_requirements||[]).map(missionReqRow).join('')}</div><button id="sitAddMissionReq" class="btn ghost" type="button">添加机型需求</button></div><div class="inspector-footer"><button id="cancelMissionEdit" class="btn ghost" type="button">取消</button>${id?'<button id="removeMission" class="btn danger" type="button">移出情境</button>':''}<button id="applyMission" class="btn primary" type="button">${id?'应用':'加入情境'}</button></div>`;bindDynamic('sitAddMissionReq','sitMissionReqs',missionReqRow);const syncDraft=()=>{const lonRaw=$('sitMissionLon').value.trim(),latRaw=$('sitMissionLat').value.trim();const lon=lonRaw===''?null:Number(lonRaw),lat=latRaw===''?null:Number(latRaw);state.draftMissionCoord=Number.isFinite(lon)&&Number.isFinite(lat)?{lon,lat}:null;drawMap()};$('sitMissionLon').oninput=syncDraft;$('sitMissionLat').oninput=syncDraft;state.draftMissionCoord=(m.longitude===''||m.longitude==null||m.latitude===''||m.latitude==null)?null:{lon:Number(m.longitude),lat:Number(m.latitude)};bindPanelDraft();$('cancelMissionEdit').onclick=()=>{clearPanelDraft();setMode('select')};$('pickMissionLocation').onclick=beginMissionLocationPick;$('applyMission').onclick=()=>applyMission(id);if(id)$('removeMission').onclick=()=>removeMission(id);lockEditorForReadOnly()}
async function applyMission(oldId){const id=$('sitMissionId').value.trim(),name=$('sitMissionName').value.trim();if(!id||!name){showMessage('任务编号和名称不能为空。','error');return}if(!oldId&&state.working.missions.some(x=>x.mission_id===id)){showMessage('当前情境已存在同编号任务。','error');return}try{const candidate=deep(state.working);const m={mission_id:id,name,longitude:num($('sitMissionLon').value),latitude:num($('sitMissionLat').value),window_start_slot:int($('sitMissionStart').value),window_end_slot:int($('sitMissionEnd').value),aircraft_requirements:collectRows('.mission-req',r=>({aircraft_type_id:r.querySelector('.row-aircraft').value,required_sorties:int(r.querySelector('.row-sorties').value),tau_work_windows:int(r.querySelector('.row-work').value)}))};if(oldId)candidate.missions=candidate.missions.map(x=>x.mission_id===oldId?m:x);else candidate.missions.push(m);state.working=await canonicalizeWorking(candidate);clearPanelDraft();state.selected={type:'mission',id};markDirty();renderMissionEditor(id);showMessage('任务已应用到当前情境。','success')}catch(e){showMessage(errText(e),'error')}}
async function removeMission(id){if(!(await confirmAction(`从当前情境移除任务 ${id}？`,'移出情境')))return;state.working.missions=state.working.missions.filter(x=>x.mission_id!==id);clearPanelDraft();state.selected=null;markDirty();setMode('select')}
const DAMAGE_CATEGORY_LABELS = { low: '低', medium: '中', high: '高', custom: '自定义' };
const damageCategoryLabel = (category) => DAMAGE_CATEGORY_LABELS[category] || category;
function renderDamageMode(){refs.inspector.dataset.kind='damage-editor';refs.inspectorTitle.textContent='损毁场景';refs.inspectorSubtitle.textContent='新增、编辑或删除损毁场景';refs.body.innerHTML=`<div class="mode-actions"><button id="newDamageScenario" class="btn primary" type="button">新建损毁场景</button></div><div class="object-list">${state.working.damage_scenarios.map(s=>objectCard('damage',s.damage_scenario_id,s.name,`${damageCategoryLabel(s.category)} · ${s.events.length} 个事件`)).join('')||'<div class="empty-state">当前情境还没有损毁场景。</div>'}</div>`;$('newDamageScenario').onclick=()=>renderDamageEditor(null);refs.body.querySelectorAll('[data-object-type="damage"]').forEach(b=>b.onclick=()=>renderDamageEditor(b.dataset.objectId))}
function damageEventRow(e={},idx=0){const t=e.damage_type||'capacity_damage',airport=e.target?.airport_id||'',rec=e.recovery_mode||'instant';let effect='';if(t==='capacity_damage'){effect=`<div class="damage-effect-grid"><div class="field"><label>剩余容量/窗</label><input class="control ev-cap" type="number" min="0" value="${esc(val(e.effect?.remaining_capacity_per_window??0))}"></div><div class="field"><label>关闭</label><select class="control ev-closed"><option value="false" ${e.effect?.closed?'':'selected'}>否</option><option value="true" ${e.effect?.closed?'selected':''}>是</option></select></div></div>`}else if(t==='navigation_delay'){effect=`<div class="damage-effect-grid"><div class="field"><label>离场延迟/窗</label><input class="control ev-dep-delay" type="number" min="0" value="${esc(val(e.effect?.departure_delay_slots??0))}"></div><div class="field"><label>返航延迟/窗</label><input class="control ev-ret-delay" type="number" min="0" value="${esc(val(e.effect?.return_delay_slots??0))}"></div></div>`}else if(t==='aircraft_damage'){const entries=Object.entries(e.effect?.aircraft_loss||{});effect=`<div class="effect-rows aircraft-loss-rows">${(entries.length?entries:[['',1]]).map(([id,q])=>`<div class="damage-effect-grid effect-row"><select class="control loss-aircraft">${opt(state.aircraft,id,x=>x.aircraft_type.aircraft_type_id,x=>x.aircraft_type.name)}</select><input class="control loss-qty" type="number" min="1" value="${esc(val(q))}"></div>`).join('')}</div><button class="btn ghost add-loss-row" type="button"><svg class="ui-icon"><use href="#i-plus"></use></svg>机型损失</button>`}else{const entries=Object.entries(e.effect?.remaining_quantity||{});effect=`<div class="effect-rows resource-loss-rows">${(entries.length?entries:[['',0]]).map(([id,q])=>`<div class="damage-effect-grid effect-row"><select class="control loss-resource">${opt(state.resources,id,x=>x.resource_type.resource_type_id,x=>x.resource_type.name)}</select><input class="control loss-qty" type="number" min="0" step="any" value="${esc(val(q))}"></div>`).join('')}</div><button class="btn ghost add-resource-row" type="button"><svg class="ui-icon"><use href="#i-plus"></use></svg>资源余量</button>`}return `<div class="damage-event" data-event-index="${idx}"><div class="damage-event-head"><strong>事件 ${idx+1}</strong><button class="mini-button remove-event" type="button" aria-label="删除事件"><svg class="ui-icon"><use href="#i-close"></use></svg></button></div><div class="compact-grid"><div class="field"><label>事件编号</label><input class="control ev-id" value="${esc(e.event_id||`E${idx+1}`)}"></div><div class="field"><label>顺序</label><input class="control ev-seq" type="number" min="0" value="${esc(val(e.sequence??idx))}"></div><div class="field wide"><label>目标机场</label><select class="control ev-airport">${opt(state.working.airports,airport,x=>x.airport.airport_id,x=>x.airport.airport_name)}</select></div><div class="field"><label>类型</label><select class="control ev-type"><option value="capacity_damage" ${t==='capacity_damage'?'selected':''}>起降能力变化</option><option value="resource_damage" ${t==='resource_damage'?'selected':''}>资源变化</option><option value="navigation_delay" ${t==='navigation_delay'?'selected':''}>调度延迟</option><option value="aircraft_damage" ${t==='aircraft_damage'?'selected':''}>初始航空器损失</option></select></div><div class="field"><label>恢复</label><select class="control ev-recovery" ${t==='aircraft_damage'?'disabled':''}><option value="instant" ${rec==='instant'?'selected':''}>结束后立即恢复</option><option value="average" ${rec==='average'?'selected':''}>平均恢复</option><option value="none" ${rec==='none'?'selected':''}>不恢复</option></select></div><div class="field"><label>开始窗</label><input class="control ev-start" type="number" min="0" value="${esc(val(e.start_slot??0))}"></div><div class="field"><label>结束窗（不含）</label><input class="control ev-end" type="number" min="1" value="${esc(val(e.end_slot??1))}"></div><div class="field wide"><label>平均恢复时长/窗</label><input class="control ev-duration" type="number" min="1" value="${esc(val(e.recovery_duration_slots))}" ${rec==='average'?'':'disabled'}></div></div><div class="inspector-section effect-editor">${effect}</div></div>`}
const DAMAGE_PRESETS = {
  low: { label: '低', category: 'low', segments: [{ offset: 0, duration: 4, ratio: 0.80 }] },
  medium: { label: '中', category: 'medium', segments: [{ offset: 0, duration: 4, ratio: 0.50 }] },
  high: { label: '高', category: 'high', segments: [{ offset: 0, duration: 4, ratio: 0.20 }] },
  sustained: {
    label: '持续', category: 'custom',
    segments: [
      { offset: 0, duration: 4, ratio: 0.50 },
      { offset: 5, duration: 4, ratio: 0.50 },
      { offset: 10, duration: 4, ratio: 0.50 },
    ],
  },
  extreme: {
    label: '极端', category: 'custom',
    segments: [
      { offset: 0, duration: 4, ratio: 0.20 },
      { offset: 5, duration: 4, closed: true },
      { offset: 10, duration: 4, ratio: 0.20 },
    ],
  },
};

function damagePresetMarkup() {
  const airportOptions = state.working.airports.map((item) => {
    const capacity = Number(item.operational_profile.capacity_per_window);
    const available = Number.isInteger(capacity) && capacity > 0;
    const airport = item.airport;
    return `<option value="${esc(airport.airport_id)}" ${available ? '' : 'disabled'}>${esc(airport.airport_name)} · ${available ? `${capacity}/窗` : '该机场尚未配置容量'}</option>`;
  }).join('');
  return `<section class="editor-section damage-preset">
    <h3>快速预设</h3>
    <div class="damage-preset-kinds" role="group" aria-label="损毁快速预设">
      ${Object.entries(DAMAGE_PRESETS).map(([kind, preset], index) => `<button class="preset-kind${index === 0 ? ' active' : ''}" type="button" data-preset-kind="${kind}" aria-pressed="${index === 0 ? 'true' : 'false'}">${preset.label}</button>`).join('')}
    </div>
    <div class="field"><label>作用机场</label><select id="damagePresetAirports" class="control" multiple size="4">
      ${airportOptions}
    </select></div>
    <div class="damage-preset-footer">
      <div class="field"><label>开始窗</label><input id="damagePresetStart" class="control" type="number" min="0" step="1" value="0"></div>
      <button id="applyDamagePreset" class="btn" type="button">生成预设事件</button>
    </div>
    <div id="damagePresetStatus" class="field-note">生成后可逐项修改。</div>
  </section>`;
}

function bindDamagePreset() {
  const buttons = [...refs.body.querySelectorAll('[data-preset-kind]')];
  buttons.forEach((button) => button.addEventListener('click', () => {
    buttons.forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    setPanelDraftDirty(true);
  }));
  const available = [...$('damagePresetAirports').options].some((option) => !option.disabled);
  $('applyDamagePreset').disabled = !available;
  if (!available && state.working.airports.length) $('damagePresetStatus').textContent = '该机场尚未配置容量';
}

async function applyDamagePresetDraft() {
  const presetKind = refs.body.querySelector('[data-preset-kind].active')?.dataset.presetKind;
  const preset = DAMAGE_PRESETS[presetKind];
  if (!preset) return;
  const airportIds = [...$('damagePresetAirports').selectedOptions]
    .filter((option) => !option.disabled)
    .map((option) => option.value);
  if (!airportIds.length) {
    $('damagePresetStatus').textContent = '请至少选择一个机场。';
    return;
  }
  const start = Number($('damagePresetStart').value);
  if (!Number.isInteger(start) || start < 0) {
    $('damagePresetStatus').textContent = '请检查开始窗。';
    return;
  }
  if ($('damageEventRows').children.length && !(await confirmAction('预设将替换当前草稿中的事件，是否继续？', '替换事件'))) return;
  const events = [];
  for (const segment of preset.segments) {
    for (const airportId of airportIds) {
      const capacity = Number(airportItem(airportId).operational_profile.capacity_per_window);
      const closed = segment.closed === true;
      const remaining = closed ? 0 : Math.max(1, Math.floor(capacity * segment.ratio));
      const sequence = events.length;
      events.push({
        event_id: `P${sequence + 1}`,
        sequence,
        target: { airport_id: airportId, target_type: 'airport', target_id: null },
        damage_type: 'capacity_damage',
        start_slot: start + segment.offset,
        end_slot: start + segment.offset + segment.duration,
        effect: { closed, remaining_capacity_per_window: remaining },
        recovery_mode: 'instant',
        recovery_duration_slots: null,
      });
    }
  }
  $('damageScenarioCategory').value = preset.category;
  $('damageEventRows').innerHTML = events.map(damageEventRow).join('');
  setPanelDraftDirty(true);
  bindDamageEvents();
  bindPanelDraft();
  $('damagePresetStatus').textContent = '生成后可逐项修改。';
}

function renderDamageEditor(id) {
  refs.inspector.dataset.kind = 'damage-editor';
  collapseOverview();
  const scenario = id ? deep(damageScenario(id)) : { damage_scenario_id: '', name: '', category: 'custom', events: [] };
  refs.inspectorTitle.textContent = id ? scenario.name : '新建损毁场景';
  refs.inspectorSubtitle.textContent = '配置一个或多个损毁事件';
  refs.body.innerHTML = `
    <div class="compact-grid">
      <div class="field"><label>场景编号</label><input id="damageScenarioId" class="control" value="${esc(scenario.damage_scenario_id)}" ${id ? 'readonly' : ''}></div>
      <div class="field"><label>名称</label><input id="damageScenarioName" class="control" value="${esc(scenario.name)}"></div>
      <div class="field wide"><label>分类</label><select id="damageScenarioCategory" class="control">
        <option value="low" ${scenario.category === 'low' ? 'selected' : ''}>低</option>
        <option value="medium" ${scenario.category === 'medium' ? 'selected' : ''}>中</option>
        <option value="high" ${scenario.category === 'high' ? 'selected' : ''}>高</option>
        <option value="custom" ${scenario.category === 'custom' ? 'selected' : ''}>自定义</option>
      </select></div>
    </div>
    ${id ? '' : damagePresetMarkup()}
    <section class="editor-section">
      <div class="mode-actions"><h3>损毁事件</h3><button id="addDamageEvent" class="btn ghost" type="button">添加事件</button></div>
      <div id="damageEventRows">${scenario.events.map(damageEventRow).join('')}</div>
    </section>
    <div class="inspector-footer">
      <button id="cancelDamageEdit" class="btn ghost" type="button">取消</button>
      ${id ? '<button id="removeDamageScenario" class="btn danger" type="button">删除场景</button>' : ''}
      <button id="applyDamageScenario" class="btn primary" type="button">应用到情境</button>
    </div>`;
  bindDamageEvents();
  bindPanelDraft();
  if (!id) {
    bindDamagePreset();
    $('applyDamagePreset').addEventListener('click', applyDamagePresetDraft);
  }
  $('cancelDamageEdit').onclick = () => { clearPanelDraft(); renderDamageMode(); };
  $('addDamageEvent').onclick = () => {
    setPanelDraftDirty(true);
    $('damageEventRows').insertAdjacentHTML('beforeend', damageEventRow({}, refs.body.querySelectorAll('.damage-event').length));
    bindDamageEvents();
  };
  $('applyDamageScenario').onclick = () => applyDamageScenario(id);
  if (id) $('removeDamageScenario').onclick = async () => {
    if (await confirmAction(`删除损毁场景 ${id}？`, '删除场景')) {
      state.working.damage_scenarios = state.working.damage_scenarios.filter((item) => item.damage_scenario_id !== id);
      clearPanelDraft();
      markDirty();
      renderDamageMode();
    }
  };
  lockEditorForReadOnly();
}
function bindDamageEvents(){refs.body.querySelectorAll('.remove-event').forEach(b=>b.onclick=()=>{setPanelDraftDirty(true);b.closest('.damage-event').remove()});refs.body.querySelectorAll('.ev-type').forEach(s=>s.onchange=()=>{const card=s.closest('.damage-event');const ev={event_id:card.querySelector('.ev-id').value,sequence:int(card.querySelector('.ev-seq').value),target:{airport_id:card.querySelector('.ev-airport').value,target_type:'airport',target_id:null},damage_type:s.value,start_slot:int(card.querySelector('.ev-start').value),end_slot:int(card.querySelector('.ev-end').value),effect:{},recovery_mode:s.value==='aircraft_damage'?'none':'instant',recovery_duration_slots:null};card.outerHTML=damageEventRow(ev,Number(card.dataset.eventIndex));bindDamageEvents();bindPanelDraft()});refs.body.querySelectorAll('.ev-recovery').forEach(s=>s.onchange=()=>{const input=s.closest('.damage-event').querySelector('.ev-duration');input.disabled=s.value!=='average';if(input.disabled)input.value=''});refs.body.querySelectorAll('.add-loss-row').forEach(b=>b.onclick=()=>{setPanelDraftDirty(true);b.previousElementSibling.insertAdjacentHTML('beforeend',`<div class="damage-effect-grid effect-row"><select class="control loss-aircraft">${opt(state.aircraft,'',x=>x.aircraft_type.aircraft_type_id,x=>x.aircraft_type.name)}</select><input class="control loss-qty" type="number" min="1" value="1"></div>`)});refs.body.querySelectorAll('.add-resource-row').forEach(b=>b.onclick=()=>{setPanelDraftDirty(true);b.previousElementSibling.insertAdjacentHTML('beforeend',`<div class="damage-effect-grid effect-row"><select class="control loss-resource">${opt(state.resources,'',x=>x.resource_type.resource_type_id,x=>x.resource_type.name)}</select><input class="control loss-qty" type="number" min="0" step="any" value="0"></div>`)});}
function eventFromCard(card){const type=card.querySelector('.ev-type').value;let effect;if(type==='capacity_damage'){const closed=card.querySelector('.ev-closed').value==='true';effect={closed,remaining_capacity_per_window:closed?0:int(card.querySelector('.ev-cap').value)}}else if(type==='navigation_delay'){effect={departure_delay_slots:int(card.querySelector('.ev-dep-delay').value)||0,return_delay_slots:int(card.querySelector('.ev-ret-delay').value)||0}}else if(type==='aircraft_damage'){const loss={};card.querySelectorAll('.effect-row').forEach(r=>{const id=r.querySelector('.loss-aircraft').value;if(id)loss[id]=int(r.querySelector('.loss-qty').value)});effect={aircraft_loss:loss}}else{const rem={};card.querySelectorAll('.effect-row').forEach(r=>{const id=r.querySelector('.loss-resource').value;if(id)rem[id]=Number(r.querySelector('.loss-qty').value)});effect={remaining_quantity:rem}}const recovery=type==='aircraft_damage'?'none':card.querySelector('.ev-recovery').value;return {event_id:card.querySelector('.ev-id').value.trim(),sequence:int(card.querySelector('.ev-seq').value),target:{airport_id:card.querySelector('.ev-airport').value,target_type:'airport',target_id:null},damage_type:type,start_slot:int(card.querySelector('.ev-start').value),end_slot:int(card.querySelector('.ev-end').value),effect,recovery_mode:recovery,recovery_duration_slots:recovery==='average'?int(card.querySelector('.ev-duration').value):null}}
async function applyDamageScenario(oldId){const id=$('damageScenarioId').value.trim(),name=$('damageScenarioName').value.trim();if(!id||!name){showMessage('损毁场景编号和名称不能为空。','error');return}if(!oldId&&state.working.damage_scenarios.some(x=>x.damage_scenario_id===id)){showMessage('当前情境已存在同编号损毁场景。','error');return}try{const candidate=deep(state.working);const scenario={damage_scenario_id:id,name,category:$('damageScenarioCategory').value,events:[...refs.body.querySelectorAll('.damage-event')].map(eventFromCard)};if(oldId)candidate.damage_scenarios=candidate.damage_scenarios.map(x=>x.damage_scenario_id===oldId?scenario:x);else candidate.damage_scenarios.push(scenario);state.working=await canonicalizeWorking(candidate);clearPanelDraft();markDirty();showMessage('损毁场景已应用到当前情境。','success');renderDamageEditor(id)}catch(e){showMessage(errText(e),'error')}}
function visibleCandidateAirports(){if(state.mode!=='airport')return[];const existing=new Set((state.working?.airports||[]).map(x=>x.airport.airport_id));return state.airportCatalog.filter(x=>!existing.has(x.airport_id)&&(!state.candidateQuery||`${x.airport_id} ${x.airport_name}`.toLowerCase().includes(state.candidateQuery))&&(!state.candidateRole||x.role===state.candidateRole)&&(!state.candidateRegion||String(x.region||'')===state.candidateRegion))}
function toggleCandidate(id){const row=state.airportCatalog.find(x=>x.airport_id===id);if(!row)return;if(state.tempAirportIds.has(id))state.tempAirportIds.delete(id);else state.tempAirportIds.add(id);const input=refs.body.querySelector(`#airportCandidateList input[value="${CSS.escape(id)}"]`);if(input){input.checked=state.tempAirportIds.has(id);input.closest('.candidate-row')?.classList.toggle('selected',input.checked)}const add=$('addAirportsToSituation');if(add)add.textContent=`加入当前情境（${state.tempAirportIds.size}）`;drawMap()}
async function selectObject(type,id,{locate=false}={}){return requestPanelTransition(()=>{collapseOverview();state.mode='select';refs.tools.querySelectorAll('[data-mode]').forEach(b=>b.classList.remove('active'));state.selected={type,id};renderInspector();drawMap();if(locate)focusObject(type,id)})}
function bind(signal) {
  refs.select.addEventListener('change', () => openSituation(refs.select.value), { signal });
  refs.newBtn.addEventListener('click', newSituation, { signal });
  refs.save.addEventListener('click', saveSituation, { signal });
  refs.del.addEventListener('click', deleteSituation, { signal });
  refs.tools.querySelectorAll('[data-mode]').forEach((button) => {
    button.addEventListener('click', () => requestPanelTransition(() => setMode(button.dataset.mode)), { signal });
  });
  refs.close.addEventListener('click', () => requestPanelTransition(() => {
    state.mode = 'select';
    state.selected = null;
    refs.tools.querySelectorAll('[data-mode]').forEach((button) => button.classList.remove('active'));
    setInspectorOpen(false);
    drawMap();
  }), { signal });
  refs.overviewTrigger.addEventListener('click', () => {
    const open = !refs.overview.classList.contains('open');
    refs.overview.classList.toggle('open', open);
    refs.overviewTrigger.setAttribute('aria-expanded', String(open));
    refs.overviewTrigger.setAttribute('aria-label', open ? '收起情境摘要' : '展开情境摘要');
  }, { signal });
  window.addEventListener('beforeunload', (event) => {
    if (state.dirty || state.panelDraftDirty) {
      event.preventDefault();
      event.returnValue = '';
    }
  }, { signal });
}

async function init(signal) {
  configureMap({ selectObject, toggleCandidate, visibleCandidateAirports, message: showMessage, markPanelDraft: () => setPanelDraftDirty(true), signal });
  configurePanels({
    selectObject,
    setMode,
    editSituationInfo: () => requestPanelTransition(() => {
      if (!state.working) return;
      state.mode = 'select';
      state.selected = null;
      setInspectorOpen(true);
      renderSituationInfoEditor();
      syncWorkspaceChrome();
    }),
    reloadSituation: async () => {
      if (!state.working || !(await canDiscardSituation())) return;
      await openSituation(state.working.situation_id, { force: true });
    },
  });
  initPanels({ signal });
  try {
    state.me = await apiFetch('/api/me');
    const [aircraft, resources] = await Promise.all([
      apiFetch('/api/aircraft-types'),
      apiFetch('/api/resource-types'),
    ]);
    state.aircraft = aircraft.items || [];
    state.resources = resources.items || [];
    bind(signal);
    await loadSituationList();
    await initMap();
    renderAll();
    if (state.list.length) await openSituation(state.list[0].situation_id);
  } catch (error) {
    showMessage(errText(error), 'error');
    renderAll();
  }
}

export async function mount(root) {
  if (mounted) unmount();
  pendingPanelTransition = null;
  resetSituationState();
  bindSituationDom(root);
  lifecycleController = new AbortController();
  mounted = true;
  await init(lifecycleController.signal);
}

export async function beforeLeave() {
  if (!mounted || (!state.dirty && !state.panelDraftDirty)) return true;
  return confirmAction('当前情境有尚未保存的修改。离开会丢弃这些修改。', '放弃修改并离开');
}

export function unmount() {
  if (!mounted) return;
  mounted = false;
  pendingPanelTransition = null;
  lifecycleController?.abort();
  clearTimeout(showMessage.t);
  destroyPanels();
  destroyMap();
  resetSituationState();
  releaseSituationDom();
  lifecycleController = null;
}
