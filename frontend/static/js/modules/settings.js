import { apiFetch, ApiError } from './api-client.js';

const $ = (id) => document.getElementById(id);
const roleLabel = (role) => ({viewer:'查看用户',operator:'操作用户',admin:'管理员'}[role] || role || '—');
let principal = null;
let users = [];
let resetTarget = null;
let confirmAction = null;

function esc(value){return String(value ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function showMessage(message){const el=$('settingsMessage');el.textContent=message;el.classList.remove('hidden');setTimeout(()=>el.classList.add('hidden'),3500);}
function errorText(error){return error instanceof ApiError ? (error.message || error.code) : (error?.message || '操作失败');}
function openModal(id){const el=$(id);el.classList.add('open');el.setAttribute('aria-hidden','false');}
function closeModal(id){const el=$(id);el.classList.remove('open');el.setAttribute('aria-hidden','true');}
function setInline(id,text=''){const el=$(id);el.textContent=text;el.classList.toggle('hidden',!text);}

function renderAccount(){
  $('settingsRoleBadge').textContent=roleLabel(principal.role);
  const permissions=(principal.permissions||[]).map(p=>`<span class="settings-permission">${esc(p)}</span>`).join('');
  $('settingsAccountBody').innerHTML=`
    <div class="settings-fact"><label>用户编号</label><strong>${esc(principal.user_id)}</strong></div>
    <div class="settings-fact"><label>角色</label><strong>${esc(roleLabel(principal.role))}</strong></div>
    <div class="settings-fact"><label>管理权限</label><strong>${principal.is_admin?'管理员':'普通账户'}</strong></div>
    <div class="settings-permissions">${permissions||'<span class="field-note">没有返回权限项</span>'}</div>`;
  const canAdmin=(principal.permissions||[]).includes('users.admin');
  $('userAdminSection').classList.toggle('hidden',!canAdmin);
  $('settingsNoAdmin').classList.toggle('hidden',canAdmin);
}

function filteredUsers(){
  const q=($('userSearch')?.value||'').trim().toLowerCase();
  if(!q)return users;
  return users.filter(u=>[u.login_name,u.display_name,u.user_id].some(v=>String(v||'').toLowerCase().includes(q)));
}

function renderUsers(){
  const body=$('userTableBody');
  const rows=filteredUsers();
  if(!rows.length){body.innerHTML='<tr><td colspan="5"><div class="empty-state">没有匹配的用户。</div></td></tr>';return;}
  body.innerHTML=rows.map(u=>{
    const self=u.user_id===principal.user_id;
    return `<tr data-user-id="${esc(u.user_id)}">
      <td><div class="user-main"><strong>${esc(u.display_name||u.login_name)}</strong><small>${esc(u.login_name)} · ${esc(u.user_id)}${self?' · 当前账户':''}</small></div></td>
      <td><select class="control role-select" data-action="role" ${self?'disabled':''}>
        ${['viewer','operator','admin'].map(r=>`<option value="${r}" ${u.role===r?'selected':''}>${roleLabel(r)}</option>`).join('')}
      </select></td>
      <td><span class="status-chip ${u.is_disabled?'off':'ok'}">${u.is_disabled?'已停用':'正常'}</span></td>
      <td>${esc(u.last_login_at||'尚未登录')}</td>
      <td><div class="row-actions">
        ${self?'':'<button class="btn" data-action="save-role" type="button">应用角色</button>'}
        ${self?'':`<button class="btn ${u.is_disabled?'':'danger'}" data-action="toggle" type="button">${u.is_disabled?'启用':'停用'}</button>`}
        ${self?'':'<button class="btn" data-action="reset" type="button">重置密码</button>'}
      </div></td>
    </tr>`;
  }).join('');
}

async function loadUsers(){
  const data=await apiFetch('/api/users');
  users=data.users||[];
  renderUsers();
}

function askConfirm(title,body,action,{danger=true}={}){
  $('settingsConfirmTitle').textContent=title;
  $('settingsConfirmBody').textContent=body;
  $('settingsConfirmAction').classList.toggle('danger',danger);
  confirmAction=action;
  openModal('settingsConfirmModal');
}

async function changeRole(userId,role){
  await apiFetch(`/api/users/${encodeURIComponent(userId)}/role`,{method:'PUT',body:{role}});
  await loadUsers();showMessage('角色已更新，目标用户的旧会话已失效。');
}
async function toggleUser(user){
  await apiFetch(`/api/users/${encodeURIComponent(user.user_id)}/disabled`,{method:'PUT',body:{disabled:!user.is_disabled}});
  await loadUsers();showMessage(user.is_disabled?'账户已启用。':'账户已停用，旧会话已失效。');
}

$('settingsChangePassword')?.addEventListener('click',()=> $('changePasswordAction')?.click());
$('userSearch')?.addEventListener('input',renderUsers);
$('createUserButton')?.addEventListener('click',()=>{setInline('createUserMessage');['createLoginName','createDisplayName','createPassword','createPasswordAgain'].forEach(id=>$(id).value='');$('createRole').value='operator';openModal('createUserModal');});
$('createUserCancel')?.addEventListener('click',()=>closeModal('createUserModal'));
$('createUserSave')?.addEventListener('click',async()=>{
  setInline('createUserMessage');
  const password=$('createPassword').value, again=$('createPasswordAgain').value;
  if(password!==again){setInline('createUserMessage','两次输入的密码不一致。');return;}
  try{
    await apiFetch('/api/users',{method:'POST',body:{login_name:$('createLoginName').value.trim(),display_name:$('createDisplayName').value.trim()||null,role:$('createRole').value,password}});
    closeModal('createUserModal');await loadUsers();showMessage('新账号已创建。');
  }catch(error){setInline('createUserMessage',errorText(error));}
});

$('userTableBody')?.addEventListener('click',(event)=>{
  const button=event.target.closest('button[data-action]');if(!button)return;
  const tr=button.closest('tr[data-user-id]');const user=users.find(x=>x.user_id===tr?.dataset.userId);if(!user)return;
  if(button.dataset.action==='save-role'){
    const role=tr.querySelector('[data-action="role"]').value;if(role===user.role){showMessage('角色没有变化。');return;}
    askConfirm('确认调整角色',`将 ${user.display_name||user.login_name} 的角色从“${roleLabel(user.role)}”调整为“${roleLabel(role)}”？`,async()=>changeRole(user.user_id,role));
  }else if(button.dataset.action==='toggle'){
    askConfirm(user.is_disabled?'确认启用账户':'确认停用账户',`${user.is_disabled?'启用':'停用'} ${user.display_name||user.login_name}？${user.is_disabled?'':' 停用后其旧会话立即失效。'}`,async()=>toggleUser(user));
  }else if(button.dataset.action==='reset'){
    resetTarget=user;$('resetPasswordTarget').textContent=`目标账户：${user.display_name||user.login_name}（${user.login_name}）`;$('resetPasswordValue').value='';$('resetPasswordAgain').value='';setInline('resetPasswordMessage');openModal('resetPasswordModal');
  }
});

$('resetPasswordCancel')?.addEventListener('click',()=>closeModal('resetPasswordModal'));
$('resetPasswordSave')?.addEventListener('click',async()=>{
  if(!resetTarget)return;setInline('resetPasswordMessage');
  const password=$('resetPasswordValue').value,again=$('resetPasswordAgain').value;
  if(password!==again){setInline('resetPasswordMessage','两次输入的密码不一致。');return;}
  try{await apiFetch(`/api/users/${encodeURIComponent(resetTarget.user_id)}/reset-password`,{method:'POST',body:{new_password:password}});closeModal('resetPasswordModal');showMessage('密码已重置，目标用户旧会话已失效。');}
  catch(error){setInline('resetPasswordMessage',errorText(error));}
});
$('settingsConfirmCancel')?.addEventListener('click',()=>{confirmAction=null;closeModal('settingsConfirmModal');});
$('settingsConfirmAction')?.addEventListener('click',async()=>{const action=confirmAction;confirmAction=null;closeModal('settingsConfirmModal');if(!action)return;try{await action();}catch(error){showMessage(errorText(error));await loadUsers().catch(()=>{});}});

async function init(){
  try{principal=await apiFetch('/api/me');renderAccount();if((principal.permissions||[]).includes('users.admin'))await loadUsers();}
  catch(error){showMessage(errorText(error));}
}
init();
