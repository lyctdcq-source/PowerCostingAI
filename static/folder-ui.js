// Folder discovery audit and actual report downloads.
const folderDataHTML=dataHTML;
dataHTML=function(){
 let html=folderDataHTML();const d=state.dataset;
 if(!d?.cleaning_plan)return html;
 html=html.replace(/<div class="card"><h3>清洗工作表台账<\/h3>[\s\S]*$/,'');
 const auditRow=s=>`<details><summary>${esc(s.status)} · ${esc(s.sheet)} · ${s.rows} 条</summary><p>${esc(s.file)}</p><p>${esc(s.reason)}</p>${s.quantity_column?`<p>数量列：第 ${s.quantity_column} 列 · ${esc(s.quantity_basis)}</p>`:''}</details>`;
 html=`<div class="card"><h3>本地规则选表依据</h3><p>以清册为基准，在本机按规则查找对应的造价四类物资汇总表，未调用在线模型。有安装明细时汇总表用于追溯，安装明细提供设计数量。</p><p>造价 ${d.sources.length} 条 · 清册 ${d.targets.length} 条 · 清洗处理记录 ${(d.rejected_rows||[]).length} 条</p><details><summary>本次清洗规则</summary><pre>${esc(d.cleaning_rules)}</pre></details>
 ${d.sheet_audit.filter(s=>s.status!=='已排除').map(auditRow).join('')}<details><summary>查看排除的 ${d.sheet_audit.filter(s=>s.status==='已排除').length} 张工作表</summary>${d.sheet_audit.filter(s=>s.status==='已排除').map(auditRow).join('')}</details></div>`+html;
 html+=`<div class="card"><h3>清洗处理记录 · ${(d.rejected_rows||[]).length} 条</h3><p>包含按规则排除的条目、待核查行，以及已保留但单价为空的条目。</p>${(d.rejected_rows||[]).map(r=>`<details><summary>${r.retained?'已保留·待确认':esc(r.status||'未提取')} · ${esc(r.sheet)} · 行 ${r.row} · ${esc(r.name)}</summary><p>${esc(r.file)}</p><p>${esc(r.reason)}；原数量：${esc(r.quantity)} ${esc(r.unit)}</p></details>`).join('')||'<p>无异常行</p>'}</div>`;
 if(d.reference_report){
  const report=d.reference_report;
  html=`<div class="card"><h3>人工清洗基准核验</h3><p>核心字段：物资名称、规格、单位、数量及供货类型。单价与备注差异单独保留。</p><p>${esc(report.policy)}</p>${[['sources','造价'],['targets','清册']].map(([key,label])=>{const r=report.results[key];return `<h4>${label}：人工 ${r.manual_count} 条 / 自动 ${r.actual_count} 条</h4><p>核心字段一致 ${r.core_equivalent_count ?? (r.matched_count-r.differences.filter(c=>c.fields.some(f=>!['Estimation_Price','备注'].includes(f.field))).length)} 条 · 全字段一致 ${r.equivalent_count} 条 · 字段差异 ${r.differences.length} 条 · 人工独有 ${r.missing.length} 条 · 自动独有 ${r.extra.length} 条</p>${r.differences.map(c=>`<details><summary>差异 #${c.manual_id} ${esc(c.name)}</summary><p>${esc(c.source?.sheet)} · 行 ${c.source?.row}</p>${c.fields.map(f=>`<p>${esc(f.field)}：人工 ${esc(f.manual)||'空'} / 自动 ${esc(f.actual)||'空'}</p>`).join('')}</details>`).join('')}${r.missing.length?`<details><summary>人工独有条目</summary><pre>${esc(JSON.stringify(r.missing,null,2))}</pre></details>`:''}${r.extra.length?`<details><summary>自动独有条目</summary><pre>${esc(JSON.stringify(r.extra,null,2))}</pre></details>`:''}`}).join('')}</div>`+html;
 }
 return html;
};
const folderQuantityHTML=quantityHTML;
quantityHTML=function(){
 let html=folderQuantityHTML();
 if(state.stage===4&&(state.material_artifacts||[]).length)html+=`<div class="card"><h3>本次核对成果</h3>${state.material_artifacts.map(a=>`<p><a class="button" href="/api/artifacts/${encodeURIComponent(a.id)}" download="${esc(a.name)}">↓ ${esc(a.name)}</a></p>`).join('')}</div>`;
 return html;
};
// Composer folder switcher. Selecting a folder never starts a paid model call.
let selectedFolder = '';
let recentFolders = [];
try { recentFolders = JSON.parse(localStorage.getItem('power-cost-recent-folders') || '[]'); } catch {}
if (!Array.isArray(recentFolders)) recentFolders = [];
recentFolders = recentFolders.filter(p => typeof p === 'string').slice(0,20);
let removedFolders = [], lastRemovedFolder = '';
try { removedFolders = JSON.parse(localStorage.getItem('power-cost-removed-folders') || '[]'); } catch {}
if (!Array.isArray(removedFolders)) removedFolders = [];
removedFolders = removedFolders.filter(p=>typeof p==='string');
const folderKey = path => path.replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();
function saveFolderRecords(){
 try{
  localStorage.setItem('power-cost-recent-folders',JSON.stringify(recentFolders));
  localStorage.setItem('power-cost-removed-folders',JSON.stringify(removedFolders));
 }catch{}
}
function folderName(path) { return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path; }
let folderCloseTimer;
function closeFolderChoices(focus=false) {
 const panel=$('folderPopover');clearTimeout(folderCloseTimer);panel.classList.remove('entering');panel.classList.add('leaving');
 $('folderTrigger').setAttribute('aria-expanded','false');
 folderCloseTimer=setTimeout(()=>{panel.hidden=true;panel.classList.remove('leaving');},matchMedia('(prefers-reduced-motion: reduce)').matches?0:160);
 if(focus)$('folderTrigger').focus();
}
openFolder=function(){
 showConversation();
 const panel=$('folderPopover'),opening=$('folderTrigger').getAttribute('aria-expanded')!=='true';
 if(!opening){closeFolderChoices(true);return;}
 clearTimeout(folderCloseTimer);panel.classList.remove('leaving');panel.classList.add('entering');panel.hidden=false;
 $('folderTrigger').setAttribute('aria-expanded','true');
 $('folderSearch').value='';renderFolderChoices();$('folderSearch').focus();
 requestAnimationFrame(()=>requestAnimationFrame(()=>panel.classList.remove('entering')));
};
function renderFolderChoices(){
 const current=state?.dataset?.origin;
 const paths=[...new Map([selectedFolder,current,...recentFolders].filter(p=>typeof p==='string'&&/^[A-Za-z]:[\\/]/.test(p)&&!removedFolders.includes(folderKey(p))).map(p=>[folderKey(p),p])).values()];
 const query=$('folderSearch').value.trim().toLowerCase();
 const list=$('folderChoices');list.replaceChildren();
 for(const path of paths.filter(p=>p.toLowerCase().includes(query))){
  const button=document.createElement('button');button.type='button';button.className='folder-choice';button.title=path;
  button.innerHTML=`<span class="folder-glyph">▱</span><span><strong>${esc(folderName(path))}</strong><small>${esc(path)}</small></span>${path===selectedFolder?'<span>✓</span>':''}`;
  button.onclick=()=>selectEngineeringFolder(path);
  const row=document.createElement('div');row.className='folder-choice-row';
  const remove=document.createElement('button');remove.type='button';remove.className='folder-remove';remove.textContent='移除';
  remove.title='从工作空间列表移除，保留本地文件';remove.setAttribute('aria-label','移除工作空间 '+folderName(path));
  remove.onclick=e=>{e.stopPropagation();removeEngineeringFolder(path);};
  row.append(button,remove);list.append(row);
 }
 if(!list.children.length){const p=document.createElement('p');p.className='folder-empty';p.textContent=query?'没有找到匹配的工作空间':'尚未添加工作空间';list.append(p);}
 if(lastRemovedFolder){const undo=document.createElement('button');undo.type='button';undo.className='folder-undo';undo.textContent='撤销移除';undo.onclick=()=>{const path=lastRemovedFolder;lastRemovedFolder='';selectEngineeringFolder(path);};list.append(undo);}
}
function removeEngineeringFolder(path){
 if(state?.running){toast('当前任务正在执行，请结束后再移除工作空间。');return;}
 const key=folderKey(path);
 recentFolders=recentFolders.filter(p=>folderKey(p)!==key);
 removedFolders=[...new Set([...removedFolders,key])];lastRemovedFolder=path;
 if(folderKey(selectedFolder)===key){
  selectedFolder='';$('selectedFolderLabel').textContent='选择工作空间';$('folderTrigger').removeAttribute('title');$('cleanSelectedFolder').hidden=true;
 }
 saveFolderRecords();renderFolderChoices();$('folderSearch').focus();toast('已移除工作空间，本地文件和已有清洗结果保留。');
}
async function selectEngineeringFolder(path){
 if(state?.running){toast('当前任务正在执行，请完成或暂停后再切换文件夹。');return;}
 // 换到另一个工作空间时另起一次评审会话，原会话保留在“物资智能评审”历史中
 if(folderKey(path)!==folderKey(currentWorkspace())){
  try{await api('session/new',{workspace:path});}catch(e){toast(e.message);return;}
  showConversation();
 }
 selectedFolder=path;recentFolders=[path,...recentFolders.filter(p=>folderKey(p)!==folderKey(path))].slice(0,20);
 removedFolders=removedFolders.filter(p=>p!==folderKey(path));saveFolderRecords();
 $('selectedFolderLabel').textContent=folderName(path);$('folderTrigger').title=path;
 $('cleanSelectedFolder').hidden=false;closeFolderChoices(true);
}
async function pickLocalFolder(){
 const button=$('openLocalFolder');button.disabled=true;
 try{
  const native=window.pywebview?.api?.choose_folder;
  const result=native?await window.pywebview.api.choose_folder():await api('browse');
  if(result.path)selectEngineeringFolder(result.path);
 }catch(e){toast('请在桌面客户端打开本地文件夹。浏览器测试可在对话中输入“读取文件夹 完整路径”。');}
 finally{button.disabled=false;}
}
async function cleanSelectedFolder(){
 if(!selectedFolder)return openFolder();
 if(!state?.config?.has_key){toast('请先填写模型 API Key，再开始清洗。');return openSettings();}
 if(state?.running)return toast('当前任务正在执行，请稍后再试。');
 $('cleanSelectedFolder').disabled=true;
 try{const result=await action('load',{mode:'folder',path:selectedFolder});if(result)toast('已开始读取工程文件夹，进度将在对话中显示。');}
 finally{$('cleanSelectedFolder').disabled=false;}
}
document.addEventListener('click',e=>{if(!$('folderPopover').hidden&&!e.target.closest('.folder-bar')&&!e.target.closest('[onclick="openFolder()"]'))closeFolderChoices();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('folderPopover').hidden){e.preventDefault();closeFolderChoices(true);}});

// 评审历史：每个工作空间的每次评审是一个会话，可切换回看对话和结果
const currentWorkspace=()=>state?.workspace||state?.dataset?.origin||'';
// 打开评审任务时结果面板切回物资评审，不沿用之前打开的价格库、图纸或知识库视图；keepPanel 时面板保持原来的开合状态
// 换到另一个评审任务：清掉上一个项目的翻页、筛选、搜索和勾选，结果从第一页显示
function resetResultView(){page=0;dataPage=0;filter='';search='';selectedRow=null;selectedKeys.clear();chatRefs=[];renderChatRefs();render();}
function showReviewConversation(keepPanel=false){
 const open=document.body.classList.contains('panel-open');
 view='quantity';$('canvasTitle').textContent='物资评审工作台';renderCanvas();showConversation();
 if(keepPanel&&open)togglePanel(true);
}
const sessionKindOf=s=>s.kind||((String(s.stage||'').startsWith('杆塔提资')||String(s.stage||'').startsWith('图纸提资'))?'drawing':'quantity');
let reviewHistoryExpanded=false,reviewHistoryCollapsed=false;
try{reviewHistoryCollapsed=localStorage.getItem('power-cost-review-history-collapsed')==='1';}catch{}
// 点“物资智能评审”仅在已处于该视图或点击小三角时折叠/展开评审历史；从其他视图切回时保持展开并切换视图
const reviewNav=document.querySelector('.nav[data-view="quantity"]');
reviewNav.insertAdjacentHTML('beforeend','<b class="nav-caret" aria-hidden="true"></b>');
reviewNav.setAttribute('aria-controls','reviewHistory');
let reviewNavWasActive=false;
reviewNav.addEventListener('pointerdown',()=>{reviewNavWasActive=(view==='quantity');});
reviewNav.addEventListener('click',e=>{
 const reviewSessions=(state?.sessions||[]).filter(s=>sessionKindOf(s)==='quantity');
 if(!reviewSessions.length)return;
 if(e.target?.closest?.('.nav-caret')||reviewNavWasActive){
  reviewHistoryCollapsed=!reviewHistoryCollapsed;
  try{localStorage.setItem('power-cost-review-history-collapsed',reviewHistoryCollapsed?'1':'0');}catch{}
  renderReviewHistory();
 }
});
function renderReviewHistory(){
 const box=$('reviewHistory');if(!box)return;
 const sessions=(state?.sessions||[]).filter(s=>sessionKindOf(s)==='quantity'),shown=reviewHistoryExpanded?sessions:sessions.slice(0,6);
 reviewNav.classList.toggle('has-history',sessions.length>0);
 if(sessions.length)reviewNav.setAttribute('aria-expanded',String(!reviewHistoryCollapsed));else reviewNav.removeAttribute('aria-expanded');
 reviewNav.title=sessions.length?(reviewHistoryCollapsed?'展开':'折叠')+'评审历史':'';
 box.hidden=reviewHistoryCollapsed;
 box.replaceChildren();
 for(const s of shown){
  const row=document.createElement('div');row.className='review-item'+(s.current?' current':'');
  const open=document.createElement('button');open.type='button';open.className='review-open';open.title=s.workspace||s.project;
  open.innerHTML=`<strong>${esc(s.project)}</strong><small>${esc(s.stage)}${s.rows?' · '+s.rows+' 条':''} · ${esc((s.updated||'').slice(5))}</small>`;
  if(s.current)open.setAttribute('aria-current','true');
  open.onclick=async()=>{if(!s.current){if(state?.running||window.__TIZI__?.isRunning?.())return toast('当前任务正在执行，请完成或暂停后再切换。');if(await action('session/open',{id:s.id}))resetResultView();else return;}showReviewConversation(true);};
  const remove=document.createElement('button');remove.type='button';remove.className='review-remove';remove.textContent='×';
  remove.title='删除这次评审记录';remove.setAttribute('aria-label','删除评审记录 '+s.project);
  remove.onclick=async e=>{e.stopPropagation();if(confirm(`删除“${s.project}”的这次评审记录？对话和匹配结果将无法恢复，工程源文件不受影响。`))await action('session/delete',{id:s.id});};
  row.append(open,remove);box.append(row);
 }
 if(sessions.length>6){const more=document.createElement('button');more.type='button';more.className='review-more';more.textContent=reviewHistoryExpanded?'收起':'全部 '+sessions.length+' 次评审';more.onclick=()=>{reviewHistoryExpanded=!reviewHistoryExpanded;renderReviewHistory();};box.append(more);}
}
let drawingHistoryExpanded=false,drawingHistoryCollapsed=false;
try{drawingHistoryCollapsed=localStorage.getItem('power-cost-drawing-history-collapsed')==='1';}catch{}
const drawingNav=document.querySelector('.nav[data-view="drawing"]');
drawingNav.insertAdjacentHTML('beforeend','<b class="nav-caret" aria-hidden="true"></b>');
drawingNav.setAttribute('aria-controls','drawingHistory');
let drawingNavWasActive=false;
drawingNav.addEventListener('pointerdown',()=>{drawingNavWasActive=(view==='drawing');});
drawingNav.addEventListener('click',e=>{
 const drawingSessions=(state?.sessions||[]).filter(s=>sessionKindOf(s)==='drawing');
 if(!drawingSessions.length)return;
 if(e.target?.closest?.('.nav-caret')||drawingNavWasActive){
  drawingHistoryCollapsed=!drawingHistoryCollapsed;
  try{localStorage.setItem('power-cost-drawing-history-collapsed',drawingHistoryCollapsed?'1':'0');}catch{}
  renderDrawingHistory();
 }
});
function renderDrawingHistory(){
 const box=$('drawingHistory');if(!box)return;
 const sessions=(state?.sessions||[]).filter(s=>sessionKindOf(s)==='drawing'),shown=drawingHistoryExpanded?sessions:sessions.slice(0,6);
 drawingNav.classList.toggle('has-history',sessions.length>0);
 if(sessions.length)drawingNav.setAttribute('aria-expanded',String(!drawingHistoryCollapsed));else drawingNav.removeAttribute('aria-expanded');
 drawingNav.title=sessions.length?(drawingHistoryCollapsed?'展开':'折叠')+'提资历史':'';
 box.hidden=drawingHistoryCollapsed;
 box.replaceChildren();
 for(const s of shown){
  const row=document.createElement('div');row.className='review-item'+(s.current?' current':'');
  const open=document.createElement('button');open.type='button';open.className='review-open';open.title=s.workspace||s.project;
  open.innerHTML=`<strong>${esc(s.project)}</strong><small>${esc(s.stage)} · ${esc((s.updated||'').slice(5))}</small>`;
  if(s.current)open.setAttribute('aria-current','true');
  open.onclick=async()=>{
   if(!s.current){
    if(state?.running||window.__TIZI__?.isRunning?.())return toast('当前任务正在执行，请完成或暂停后再切换。');
    if(await action('session/open',{id:s.id}))resetResultView();else return;
   }
   switchView('drawing');
  };
  const remove=document.createElement('button');remove.type='button';remove.className='review-remove';remove.textContent='×';
  remove.title='删除这次提资记录';remove.setAttribute('aria-label','删除提资记录 '+s.project);
  remove.onclick=async e=>{e.stopPropagation();if(confirm(`删除“${s.project}”的这次提资记录？对话和提资结果将无法恢复，工程源文件不受影响。`))await action('session/delete',{id:s.id});};
  row.append(open,remove);box.append(row);
 }
 if(sessions.length>6){const more=document.createElement('button');more.type='button';more.className='review-more';more.textContent=drawingHistoryExpanded?'收起':'全部 '+sessions.length+' 次提资';more.onclick=()=>{drawingHistoryExpanded=!drawingHistoryExpanded;renderDrawingHistory();};box.append(more);}
}
function syncWorkspaceLabel(){
 const path=currentWorkspace();if(folderKey(path)===folderKey(selectedFolder))return;
 selectedFolder=path;$('selectedFolderLabel').textContent=path?folderName(path):'选择工作空间';
 if(path)$('folderTrigger').title=path;else $('folderTrigger').removeAttribute('title');
 $('cleanSelectedFolder').hidden=!path;
}
const sessionRender=render;
render=function(){sessionRender();renderReviewHistory();renderDrawingHistory();syncWorkspaceLabel();};
