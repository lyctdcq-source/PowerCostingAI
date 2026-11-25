// Public execution summaries and tool progress, never private model reasoning.
let conversationSignature='', conversationOpened=new Set(), conversationStatuses=new Map();
function conversationGroups(messages, executions){
 const records=new Map(executions.map(r=>[r.id,r]));const groups=[];let legacy=null;
 messages.forEach((m,i)=>{
  if(m.run_id){legacy=null;let g=groups.find(g=>g.id===m.run_id);if(!g){g={id:m.run_id,record:records.get(m.run_id),messages:[]};groups.push(g);}g.messages.push(m);return;}
  if(m.role==='user'){legacy=null;groups.push({id:'message-'+i,messages:[m]});return;}
  if(/^正在(执行|用)/.test(m.text)){legacy={id:'legacy-'+i,messages:[m],legacy:true};groups.push(legacy);return;}
  if(legacy){legacy.messages.push(m);legacy=null;}else groups.push({id:'message-'+i,messages:[m]});
 });
 executions.filter(r=>r.status==='running'&&!groups.some(g=>g.id===r.id)).forEach(r=>groups.push({id:r.id,record:r,messages:[]}));
 return groups;
}
// 智能体调用的工具：过程面板里逐步显示（工具名 + 结果说明），回答上方始终显示一行工具轨迹
const TOOL_STATUS={ok:'',error:'失败',refused:'已拦截'};
function processStepHTML(s){
 if(!s.tool)return `<p><time>${esc(s.time||'')}</time>${esc(s.text)}</p>`;
 const detail=s.text.startsWith(s.label+'：')?s.text.slice(s.label.length+1):s.text;
 return `<p class="tool-step ${esc(s.status||'')}"><time>${esc(s.time||'')}</time><span class="tool-name">${esc(s.label)}</span>${TOOL_STATUS[s.status]?`<span class="tool-status">${TOOL_STATUS[s.status]}</span>`:''}${esc(detail)}</p>`;
}
function toolTrailHTML(steps){
 const tools=steps.filter(s=>s.tool);if(!tools.length)return '';
 return `<div class="tool-trail" aria-label="调用的工具">${tools.map(s=>`<span class="tool-chip ${esc(s.status||'')}" title="${esc(s.text)}">${esc(s.label)}</span>`).join('<i aria-hidden="true">›</i>')}</div>`;
}
// 写入类工具执行前的批准卡片（对应 Claude Code 的权限确认）：显示修改前后对照，允许 / 本次对话都允许 / 拒绝
function changesTableHTML(changes){
 return `<table class="edit-changes"><tr><th>条目</th><th>修改前</th><th>修改后</th><th>依据</th></tr>${changes.map(c=>`<tr><td>${esc(c['条目'])}</td><td>${esc(c['修改前']??'')}</td><td><b>${esc(c['修改后']??'')}</b></td><td>${esc(c['依据']||'')}</td></tr>`).join('')}</table>`;
}
function approvalCardHTML(g){
 const a=state.approval;if(!a||!(g.record?.id===a.run_id))return '';
 return `<section class="approval-card" aria-label="需要你批准的修改"><h4>需要你批准：${esc(a.label)}<small>${a.changes.length} 处</small></h4>${changesTableHTML(a.changes)}${a.notes?.length?`<ul class="edit-notes">${a.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}<div class="approval-actions"><button type="button" class="primary" data-approval="${esc(a.id)}" data-decision="allow">允许</button>${a.forced?'':`<button type="button" class="button" data-approval="${esc(a.id)}" data-decision="allow_all">本次对话都允许</button>`}<button type="button" class="button" data-approval="${esc(a.id)}" data-decision="deny">拒绝</button></div>${a.forced?'<p class="hint">按修改校验规则，这类修改每次都需要你批准。</p>':''}</section>`;
}
// 回答里的修改卡片：逐条修改前后对照；最近一次且未撤销的修改可以一键撤销
function editCardsHTML(m){
 if(!m.edits?.length)return '';
 const records=new Map((state.result_edits||[]).map(e=>[e.id,e])),live=(state.result_edits||[]).filter(e=>!e.undone),latest=live.at(-1)?.id;
 return m.edits.map(e=>{const rec=records.get(e.id),undone=rec?.undone;
  return `<section class="edit-card${undone?' undone':''}"><h4>${esc(e.id)} · ${esc(e.label)}<small>${undone?'已撤销':`${e.changes.length} 处`}</small></h4>${changesTableHTML(e.changes)}<div class="approval-actions">${!undone&&e.id===latest?`<button type="button" class="button" data-undo="${esc(e.id)}">撤销本次修改</button>`:''}${state.edit_mode==='auto'?'<button type="button" class="button" data-edit-mode="ask">改回每次修改前询问</button>':''}</div></section>`;}).join('');
}
document.addEventListener('click',async e=>{
 const b=e.target.closest?.('[data-approval],[data-undo],[data-edit-mode]');if(!b)return;b.disabled=true;
 const ok=b.dataset.approval?await action('approval',{id:b.dataset.approval,decision:b.dataset.decision}):
  b.dataset.undo?await action('edits/undo',{id:b.dataset.undo}):await action('edit_mode',{mode:b.dataset.editMode});
 if(ok)refresh(true);else b.disabled=false;
});
function elapsedLabel(seconds){const n=Math.max(0,Math.round(seconds));return n>=60?`${Math.floor(n/60)}分${n%60}秒`:`${n}秒`;}
function renderConversation(){
 const chat=$('chat');const signature=JSON.stringify([state.messages,state.executions,state.dataset?.excel_books,state.approval,state.result_edits,state.edit_mode]);
 if(signature===conversationSignature)return;
 const bottom=chat.scrollHeight-chat.scrollTop-chat.clientHeight<90,oldTop=chat.scrollTop;
 for(const r of state.executions||[]){if(conversationStatuses.get(r.id)==='running'&&r.status!=='running')conversationOpened.delete(r.id);conversationStatuses.set(r.id,r.status);}
 const groups=conversationGroups(state.messages||[],state.executions||[]);
 chat.innerHTML=groups.map(g=>{
  const user=g.messages[0]?.role==='user';
  if(user)return `<div class="message user-message">${g.messages[0].refs?.length?`<div class="message-refs">${g.messages[0].refs.map(l=>`<span class="ref-chip"><span>${esc(l)}</span></span>`).join('')}</div>`:''}<div class="content">${esc(g.messages[0].text)}</div></div>`;
  const active=g.record?.status==='running',hasProcess=Boolean(g.record||g.legacy);
  const final=active?null:g.messages.at(-1);
  const steps=[...(hasProcess?(active?g.messages:g.messages.slice(0,-1)):[]),...(g.record?.steps||[])];
  const label=active?'正在处理…':g.record?`用时 ${elapsedLabel(g.record.elapsed_seconds||0)}${g.record.status==='failed'?' · 未完成':g.record.status==='paused'?' · 已暂停':''}`:'查看执行过程';
  return `<article class="message assistant-turn" data-turn="${esc(g.id)}">${hasProcess?`<button class="process-toggle ${active?'processing':''}" type="button" data-process="${esc(g.id)}" aria-expanded="${conversationOpened.has(g.id)}">${label}<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg></button>${toolTrailHTML(steps)}<div aria-hidden="${!conversationOpened.has(g.id)}" class="process-panel ${conversationOpened.has(g.id)?'expanded':''}" ${conversationOpened.has(g.id)?'':'inert'}><div><div class="process-steps">${steps.length?steps.map(processStepHTML).join(''):'<p>正在等待本步骤执行结果。</p>'}${typeof executionExcelHTML==='function'?executionExcelHTML(g):''}</div></div></div>`:''}${approvalCardHTML(g)}${final?`<div class="content final-answer">${final.mode?`<span class="answer-mode">${{result:'结果追问',project:'项目问答',knowledge:'知识问答',earthwork:'土石方算量',agent:'对话',edit:'结果修改',document:'文档'}[final.mode]||''}</span>`:''}${answerHTML(final)}${editCardsHTML(final)}${typeof filesHTML==='function'?filesHTML(final):''}</div><div class="answer-time">${esc(final.time)}</div>`:''}</article>`;
 }).join('');
 conversationSignature=signature;
 chat.querySelectorAll('[data-process]').forEach(button=>button.onclick=()=>{
  const id=button.dataset.process,open=!conversationOpened.has(id);if(open)conversationOpened.add(id);else conversationOpened.delete(id);
  button.setAttribute('aria-expanded',String(open));const panel=button.nextElementSibling;panel.classList.toggle('expanded',open);panel.inert=!open;panel.setAttribute('aria-hidden',String(!open));
 });
 if(bottom)chat.scrollTop=chat.scrollHeight;else chat.scrollTop=oldTop;
 if(!$('jumpToLatest')){
  const jump=document.createElement('button');jump.id='jumpToLatest';jump.type='button';jump.setAttribute('aria-label','回到最新消息');jump.title='回到最新消息';
  jump.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v16m-6-6 6 6 6-6"/></svg>';
  chat.parentElement.append(jump);jump.onclick=()=>chat.scrollTo({top:chat.scrollHeight,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  chat.addEventListener('scroll',updateJumpButton,{passive:true});new ResizeObserver(updateJumpButton).observe(chat);
 }
 updateJumpButton();
}
// Result answers cite rows as [R行号]; the server keeps only cited rows that were in the evidence package.
// 回答中的网址（联网检索来源等）显示为链接，点击用系统浏览器打开
const URL_RE=/https?:\/\/[A-Za-z0-9\-._~:\/?#\[\]@!$&;%+=,*]+/g;
function linkify(html){return html.replace(URL_RE,url=>{const tail=(url.match(/[.,;:!?\]]+$/)||[''])[0],href=url.slice(0,url.length-tail.length);return `<a class="web-link" href="${href}" target="_blank" rel="noopener noreferrer">${href}</a>${tail}`;});}
document.addEventListener('click',e=>{const a=e.target.closest?.('a.web-link');if(!a||!window.pywebview?.api?.open_url)return;e.preventDefault();window.pywebview.api.open_url(a.href);});
function answerHTML(m){const cites=new Map((m.citations||[]).map(c=>[c.key,c]));return linkify(esc(m.text)).replace(/\[(R\d+)\]/g,(all,key)=>{const c=cites.get(key);return c?`<button type="button" class="cite" data-source="${esc(c.source_id)}" data-target="${esc(c.target_id)}" title="${esc(c.title)}">${esc(c.label)}</button>`:all})}
document.addEventListener('click',e=>{const b=e.target.closest?.('.cite');if(!b)return;const r=(state?.rows||[]).find(x=>String(x.source_id||'')===b.dataset.source&&String(x.target_id||'')===b.dataset.target);if(r)openDetail(r.id);else toast('该条目已不在当前匹配结果中，可能已重新匹配')});
function updateJumpButton(){const chat=$('chat'),button=$('jumpToLatest');if(button){button.hidden=chat.scrollHeight-chat.scrollTop-chat.clientHeight<100;const composer=document.querySelector('.composer-area');button.style.bottom=((composer?.offsetHeight||180)+26)+'px';}}

// Document, knowledge and independent vision panels.
let knowledgeResults=null,knowledgeKind='',knowledgeQuery='';
const originalRenderCanvas=renderCanvas;
renderCanvas=function(){
  $('knowledgeView').hidden=view!=='knowledge';
  if(view==='knowledge'){
    ['quantityView','drawingView','databaseView'].forEach(id=>$(id).hidden=true);
    renderKnowledge();return;
  }
  originalRenderCanvas();
};
const originalSwitchView=switchView;
switchView=function(v){originalSwitchView(v);if(v==='knowledge'){$('viewTitle').textContent='电力知识库';$('canvasTitle').textContent='知识、出处与引用'}};
const originalOpenSettings=openSettings;
openSettings=function(){originalOpenSettings();const c=state.vision_config||{};$('visionUrl').value=c.base_url||'';$('visionModel').value=c.model||'qwen3.8-max';$('visionKey').value='';$('visionStatus').textContent=c.has_key?'已配置密钥（仅本次运行）':'尚未配置视觉密钥'};
saveSettings=async function(e){e.preventDefault();try{
  await api('config',{mode:'online',base_url:$('urlInput').value,model:$('modelInput').value,api_key:$('keyInput').value,batch:Number($('batchInput').value),web_search:$('webSearchInput').checked});
  await api('vision/config',{base_url:$('visionUrl').value,model:$('visionModel').value,api_key:$('visionKey').value});
  $('keyInput').value='';$('visionKey').value='';closeDialog('settingsDialog');
}catch(err){toast(err.message)}};

function fileUpload(route){const input=$('documentUpload');input.accept=route==='drawing/recognize'?'.pdf,.png,.jpg,.jpeg,.webp':'.docx,.xlsx,.xls,.pdf,.txt,.md,.csv,.json';input.value='';input.onchange=async()=>{
 const file=input.files[0];if(!file)return;if(file.size>8000000)return toast('单个文件上限8MB');
 if(route==='drawing/recognize'&&!state.vision_config?.has_key)return toast('请先在模型设置配置独立视觉 API Key');
 const reader=new FileReader();reader.onload=()=>action(route,{name:file.name,data:String(reader.result).split(',')[1]});reader.readAsDataURL(file);
};input.click()}
// 知识分两类：自定义规则（平台口径，可编辑）与规范文件（按条款导入，带标准号、条款号、版本）。
const kindOf=e=>e.kind||'自定义规则';
function knowledgeSource(e){if(kindOf(e)!=='规范文件')return e.source+(e.location?' / '+e.location:'');const c=String(e.clause||'');return `${e.standard||''}《${e.source}》${/^(表|附录|前言|引言)/.test(c)?c:c.startsWith('条文说明 ')?'条文说明第'+c.slice(5)+'条':'第'+c+'条'}${e.edition?'（'+e.edition+'版）':''}${e.location?' · '+e.location:''}`}
function knowledgeCard(e){const long=e.content.length>400;return `<div class="card"><div class="card-head"><h3>${esc(e.title)}</h3><button class="button" onclick="editKnowledge('${e.id}')">编辑</button></div><p>${badge(e.approved?'已审核':'待复核')} <span class="tag kind-tag">${esc(kindOf(e))}</span> <span class="tag">v${e.version} · ${e.active?'启用':'停用'}</span></p><p class="kb-content">${esc(long?e.content.slice(0,400)+'…':e.content)}</p>${long?`<details><summary>展开全文</summary><p class="kb-content">${esc(e.content)}</p></details>`:''}<p class="source-path">[KB:${esc(e.id)}@v${e.version}] ${esc(knowledgeSource(e))}</p><button class="subtle" onclick="knowledgeHistory('${e.id}')">查看历史版本</button></div>`}
function renderKnowledge(){const all=state.knowledge_entries||[],count=k=>all.filter(e=>kindOf(e)===k).length;const entries=(knowledgeResults||all).filter(e=>!knowledgeKind||kindOf(e)===knowledgeKind);$('knowledgeView').innerHTML=`
 <div class="notice">知识分两类：自定义规则是平台内置或你整理的核对口径与换算规则，可编辑；规范文件按条款导入，保留标准号、条款号和版本。每条保留出处、版本和审核状态，审核后才能作为判定依据；使用本地全文检索，无需显卡。</div>
 ${metrics([['自定义规则',count('自定义规则'),'核对口径与换算规则'],['规范文件条款',count('规范文件'),'按条款导入','green'],['已审核',all.filter(e=>e.approved).length,'专家确认']])}
 <div class="action-bar knowledge-tools"><div class="match-switch" role="group" aria-label="知识类型">${[['','全部'],['自定义规则','自定义规则'],['规范文件','规范文件']].map(([k,l])=>`<button class="button" aria-pressed="${knowledgeKind===k}" onclick="knowledgeKind='${k}';renderKnowledge()">${l}</button>`).join('')}</div><input id="kbSearch" value="${esc(knowledgeQuery)}" placeholder="检索标准号、条款、电压、材质、铜母排换算…" aria-label="知识检索" onkeydown="if(event.key==='Enter')searchKnowledge()"><button class="button" onclick="searchKnowledge()">检索</button><button class="button" onclick="knowledgeResults=null;knowledgeQuery='';renderKnowledge()">全部</button><button class="primary" onclick="editKnowledge()">＋ 新建知识</button></div>
 ${knowledgeUploadsHTML()}
 ${entries.length>200?'<p class="hint">当前仅显示前 200 条，请检索或筛选。</p>':''}
 ${entries.slice(0,200).map(knowledgeCard).join('')||'<div class="empty">没有匹配知识；可添加资料后再检索。</div>'}`;}
async function searchKnowledge(){knowledgeQuery=$('kbSearch').value;const r=await action('knowledge/search',{query:knowledgeQuery});if(r){knowledgeResults=r.entries;renderKnowledge()}}
function editKnowledge(id){const e=(state.knowledge_entries||[]).find(x=>x.id===id)||{kind:knowledgeKind||'自定义规则',source:knowledgeKind==='规范文件'?'':'自定义规则'};const std=kindOf(e)==='规范文件';$('detailContent').innerHTML=`<div class="dialog-head"><h2>${id?'编辑':'新建'}知识</h2><button onclick="closeDialog('detailDialog')">×</button></div><form onsubmit="saveKnowledge(event,'${e.id||''}')"><label>类型</label><select id="kbKind" onchange="$('kbStandardFields').hidden=this.value!=='规范文件'"><option ${std?'':'selected'}>自定义规则</option><option ${std?'selected':''}>规范文件</option></select><div id="kbStandardFields" class="detail-grid" ${std?'':'hidden'}><div><label>标准号</label><input id="kbStandard" placeholder="如 GB/T 706-2016" value="${esc(e.standard||'')}"></div><div><label>条款号</label><input id="kbClause" placeholder="如 4.2.1 或 表3" value="${esc(e.clause||'')}"></div><div><label>版本</label><input id="kbEdition" placeholder="如 2016" value="${esc(e.edition||'')}"></div></div><label>标题</label><input id="kbTitle" required value="${esc(e.title||'')}"><label>知识正文</label><textarea id="kbContent" rows="8" required>${esc(e.content||'')}</textarea><label>来源文件 / 标准名称</label><input id="kbSource" required value="${esc(e.source||'')}"><label>页码、章节或工作表行号</label><input id="kbLocation" value="${esc(e.location||'')}"><label>分类</label><input id="kbCategory" value="${esc(e.category||'电力物资')}"><label>审核状态</label><select id="kbApproved"><option value="0">待专家审核</option><option value="1" ${e.approved?'selected':''}>已核对来源与适用性</option></select><label>检索状态</label><select id="kbActive"><option value="1">启用</option><option value="0" ${e.active===0?'selected':''}>停用</option></select><div class="dialog-actions"><button class="primary">保存新版本</button></div></form>`;$('detailDialog').showModal()}
async function saveKnowledge(event,id){event.preventDefault();const ok=await action('knowledge/save',{id:id||undefined,kind:$('kbKind').value,standard:$('kbStandard').value,clause:$('kbClause').value,edition:$('kbEdition').value,title:$('kbTitle').value,content:$('kbContent').value,source:$('kbSource').value,location:$('kbLocation').value,category:$('kbCategory').value,approved:$('kbApproved').value==='1',active:$('kbActive').value==='1'});if(ok){knowledgeResults=null;closeDialog('detailDialog');renderKnowledge()}}
async function knowledgeHistory(id){const r=await action('knowledge/history',{id});if(r){$('detailContent').innerHTML=`<div class="dialog-head"><h2>知识历史版本</h2><button onclick="closeDialog('detailDialog')">×</button></div>`+r.versions.map(v=>`<div class="card"><b>v${v.version} · ${esc(v.updated)}</b><p class="kb-content">${esc(v.entry.content)}</p><p>${esc(v.entry.source)} / ${esc(v.entry.location)}</p></div>`).join('')+(r.versions.length?'':'<p>当前为初始版本，尚无修改历史。</p>');$('detailDialog').showModal()}}
// 规范和资料在知识库中上传：读取后可查看原文，再按条款导入为规范文件或导入为自定义规则。
function knowledgeUploadsHTML(){
 const docs=state.documents||[];
 return `<div class="card knowledge-uploads"><div class="card-head"><h3>上传规范或资料</h3><button class="primary" onclick="fileUpload('documents/import')">＋ 上传文件</button></div><p class="hint">支持 PDF、Word、Excel、TXT、CSV、JSON，单个文件 8MB 以内；扫描件请在图纸识别中用视觉模型识别。读取后可按条款导入为规范文件，或导入为自定义规则，导入的条目均为待审核。</p>${docs.map(d=>`<div class="document-item"><b>${esc(d.name)}</b><p>${d.chunks.length} 个来源片段 · ${Math.round(d.size/1024)} KB</p><button class="button" onclick="previewDocument('${d.id}')">查看原文</button> <button class="button" onclick="importStandard('${d.id}')">按条款导入为规范文件</button> <button class="button" onclick="action('knowledge/import',{id:'${d.id}'})">导入为自定义规则</button></div>`).join('')}</div>`;
}
// 规范文件先预览识别到的标准号和条款，确认后按条款导入。
async function importStandard(id){try{const p=await api('knowledge/standard-preview',{id});$('detailContent').innerHTML=`<div class="dialog-head"><h2>按条款导入规范文件</h2><button onclick="closeDialog('detailDialog')">×</button></div><form onsubmit="confirmStandard(event,'${id}')"><div class="detail-grid"><div><label>标准号</label><input id="stdNumber" required placeholder="如 GB/T 706-2016" value="${esc(p.standard)}"></div><div><label>版本</label><input id="stdEdition" placeholder="如 2016" value="${esc(p.edition)}"></div></div><label>标准名称</label><input id="stdName" required placeholder="如 热轧型钢" value="${esc(p.name)}"><p class="hint">识别到 ${p.count} 条条款，导入后均为待审核。同一标准再次导入时按条款号更新，内容未变的条款不新增版本。</p>${p.warnings.map(w=>`<div class="notice warning">${esc(w)}</div>`).join('')}<div class="clause-preview">${p.clauses.slice(0,80).map(c=>`<p><b>${esc(c.title)}</b><small>${esc(c.location)} · ${c.chars} 字</small></p>`).join('')}${p.count>80?`<p class="hint">另有 ${p.count-80} 条未列出</p>`:''}</div><div class="dialog-actions"><button class="primary" ${p.count?'':'disabled'}>导入 ${p.count} 条</button></div></form>`;$('detailDialog').showModal()}catch(e){toast(e.message)}}
async function confirmStandard(event,id){event.preventDefault();const r=await action('knowledge/import',{id,kind:'规范文件',standard:$('stdNumber').value,name:$('stdName').value,edition:$('stdEdition').value});if(r){closeDialog('detailDialog');toast(`已导入 ${r.count} 条：新增 ${r['新增']}、更新 ${r['更新']}、未变 ${r['未变']}`)}}
function previewDocument(id){const d=state.documents.find(x=>x.id===id);$('detailContent').innerHTML=`<div class="dialog-head"><h2>${esc(d.name)}</h2><button onclick="closeDialog('detailDialog')">×</button></div>`+d.chunks.map(c=>`<details class="skill-source"><summary>${esc(c.location)}</summary><pre class="code-diff">${esc(c.content)}</pre></details>`).join('');$('detailDialog').showModal()}
const legacyDrawing=renderDrawing;
renderDrawing=function(){legacyDrawing();const panel=$('drawingView');const notice=panel.querySelector('.notice');notice.classList.remove('warning');notice.textContent='在线视觉识别 PDF 前3页或单张图片，DWG须先转换为PDF。独立视觉 API负责提取，电力知识与技能规则负责校核。大图请分区上传清晰局部。';const bar=document.createElement('div');bar.className='action-bar';bar.innerHTML=`<button class="primary" onclick="fileUpload('drawing/recognize')">＋ 上传图纸并在线识别</button><button class="button" onclick="openSettings()">视觉模型设置</button><span class="tag">${esc(state.vision_config?.model||'未配置')}</span>`;notice.after(bar);
 if(state.drawing_file){const canvas=panel.querySelector('.drawing-canvas');canvas.innerHTML=`<div class="actual-drawing"><b>${esc(state.drawing_file.name)}</b><p>当前字段来自在线模型识别；请逐项对照原图。</p><a class="button" target="_blank" href="/api/drawing/file">打开原图 / PDF ↗</a></div>`;}
 const warnings=panel.querySelectorAll('.notice.warning');warnings.forEach(e=>e.textContent='交叉校核：'+(state.drawing_conflicts||[]).join('；'));
 const hint=document.createElement('p');hint.className='hint';hint.textContent='置信度由模型自报，不代表经过测量的准确率。识别值、原文证据和页码均须人工核查。';panel.append(hint);
};
exportDrawing=function(){if(!state.drawing.length)return toast('请先识别或载入图纸字段');const confirmed=state.drawing.every(r=>r.confirmed)&&!(state.drawing_conflicts||[]).length;download('提资单_'+(confirmed?'人工确认版':'待复核草稿')+'.csv',csv([['字段','数值','单位','出处','已复核','数据性质'],...state.drawing.map(r=>[r.field,r.value,r.unit,r.source,r.confirmed?'是':'否',r.demo?'合成演示':'在线识别'])]),'text/csv;charset=utf-8')};
const originalOpenDetail=openDetail;
openDetail=function(id){originalOpenDetail(id);const r=state.rows.find(x=>x.id===id);if(r.domain?.length||r.category?.length){const d=document.createElement('div');d.className='card';d.innerHTML='<h3>领域知识依据</h3>'+(r.category?.length?`<p class="hint">物资品类：${esc(r.category.join(' · '))}${r.kind?' · 计量性质：'+esc(r.kind):''}</p>`:'')+(r.domain||[]).map(k=>`<p><span class="tag">${esc(k.type)}</span> ${esc(k.id)} · 出处 ${esc(k.source)} · ${esc(k.evidence)} · ${esc(k.status)}${k.reviewer?'（'+esc(k.reviewer)+'）':''}</p>`).join('')+'<p class="hint">已审核知识可改变结论；“AI核验”条目建议由专业人员复核后改为本人审核。</p>';$('detailContent').append(d)}const box=document.createElement('div');box.className='card';box.innerHTML='<h3>本次匹配检索参考</h3><p class="hint">以下知识被送入候选匹配上下文，并非逐条自动验证的裁决依据。</p>'+(r.knowledge||[]).map(k=>`<details><summary>${esc(k.title)} · v${k.version} · ${k.approved?'已审核':'待审核'}</summary><p>${esc(k.content)}</p><p>${esc(k.source)}${k.location?' / '+esc(k.location):''}</p></details>`).join('');$('detailContent').append(box)};

// Conversation-first shell. Panel visibility is UI-only; workflow data stays intact.
function togglePanel(force){const open=typeof force==='boolean'?force:!document.body.classList.contains('panel-open');document.body.classList.toggle('panel-open',open);document.querySelectorAll('.panel-toggle').forEach(b=>{b.setAttribute('aria-expanded',String(open));b.textContent=open?'◫ 收起结果面板':'◫ 打开结果面板'});if(open)renderCanvas();}
function toggleSidebar(){document.body.classList.toggle('sidebar-hidden')}
function showConversation(){togglePanel(false);$('viewTitle').textContent='当前对话';document.querySelectorAll('.nav[data-view]').forEach(e=>e.classList.remove('active'));document.querySelector('.chat-home').classList.add('active');$('chatInput').focus()}
const workspaceSwitchView=switchView;
switchView=function(v){document.querySelector('.chat-home').classList.remove('active');workspaceSwitchView(v);togglePanel(true);if(innerWidth<=600)document.body.classList.add('sidebar-hidden')};
const shellRender=render;
render=function(){shellRender();$('headerProject').textContent=state?.dataset||state?.workspace?state.project:'本地工作空间';document.querySelector('.conversation-title').hidden=Boolean(state?.messages?.length);};
if(innerWidth<=600)document.body.classList.add('sidebar-hidden');
$('chatInput').addEventListener('input',()=>{$('chatInput').style.height='auto';$('chatInput').style.height=Math.min(180,$('chatInput').scrollHeight)+'px'});
showConversation();

// 拖动竖向分隔线调整侧栏与结果面板宽度；双击恢复默认，宽度记在本机浏览器
const PANE_LIMITS={sidebar:[180,420],panel:[380,Infinity]};
function setPaneWidth(kind,width,save=true){
 const body=document.body,name=kind==='sidebar'?'--sidebar-w':'--panel-w';
 if(width==null){body.style.removeProperty(name);body.classList.toggle('custom-'+kind,false);}
 else{
  const main=document.querySelector('main'),[low,high]=PANE_LIMITS[kind];
  const max=kind==='panel'&&main?Math.max(low,main.clientWidth-300):high;  // 对话区至少保留 300px
  width=Math.round(Math.min(max,Math.max(low,width)));
  body.style.setProperty(name,width+'px');body.classList.toggle('custom-'+kind,true);
 }
 if(save)try{width==null?localStorage.removeItem('power-cost-'+kind+'-width'):localStorage.setItem('power-cost-'+kind+'-width',String(width));}catch{}
 window.dispatchEvent(new Event('resize'));
}
function paneWidth(kind){
 return kind==='sidebar'?document.querySelector('.sidebar').getBoundingClientRect().width:document.querySelector('.workspace').getBoundingClientRect().width;
}
document.querySelectorAll('.pane-resizer').forEach(handle=>{
 const kind=handle.dataset.resize;
 handle.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;e.preventDefault();handle.setPointerCapture(e.pointerId);
  const start=e.clientX,width=paneWidth(kind),sign=kind==='sidebar'?1:-1;
  handle.classList.add('active');document.body.classList.add('pane-resizing');
  const move=ev=>setPaneWidth(kind,width+sign*(ev.clientX-start),false);
  const end=()=>{handle.removeEventListener('pointermove',move);handle.classList.remove('active');document.body.classList.remove('pane-resizing');setPaneWidth(kind,paneWidth(kind));};
  handle.addEventListener('pointermove',move);
  handle.addEventListener('pointerup',end,{once:true});handle.addEventListener('pointercancel',end,{once:true});
 });
 handle.addEventListener('dblclick',()=>setPaneWidth(kind,null));
 handle.addEventListener('keydown',e=>{
  const step={ArrowLeft:-16,ArrowRight:16}[e.key];if(!step)return;e.preventDefault();
  setPaneWidth(kind,paneWidth(kind)+(kind==='sidebar'?step:-step));
 });
});
for(const kind of ['sidebar','panel']){
 let saved=null;try{saved=Number(localStorage.getItem('power-cost-'+kind+'-width'))||null;}catch{}
 if(saved)setPaneWidth(kind,saved,false);
}
