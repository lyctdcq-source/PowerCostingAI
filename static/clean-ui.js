// 清洗结果标签：逐条查看、表格内修改和删除清洗后的造价、清册数据；以及数量评审前的发送确认框。
let cleanSide='targets',cleanSearch='',cleanPage=0,cleanExpanded=false,cleanEdits={sources:{},targets:{}},cleanDeletes={sources:new Set(),targets:new Set()};
const CLEAN_PAGE=100;
const CLEAN_COLUMNS={
 sources:[['group','层级',r=>(r['层级路径']||[]).join(' / ')||r['层级']||''],['name','名称',r=>r.Estimation_Name],['spec','型号及规范',r=>r['型号及规范']],['unit','单位',r=>r.Estimation_Unit],['quantity','数量',r=>r.Estimation_Quantity],['price','单价',r=>r.Estimation_Price]],
 targets:[['group','层级',r=>(r['层级路径']||[]).join(' / ')||r['层级']||''],['name','名称',r=>r['设备材料名称']],['spec','型号及规范',r=>r['型号及规范']],['unit','单位',r=>r['单位']],['quantity','数量',r=>r['数量']],['notes','备注',r=>r['备注']]],
};
function cleanPending(side=cleanSide){return Object.keys(cleanEdits[side]).length+cleanDeletes[side].size}
function cleanRecords(){
 const items=state.dataset?.[cleanSide]||[],q=cleanSearch.trim().toLowerCase();
 if(!q)return items;
 return items.filter(r=>JSON.stringify([r.Estimation_Name,r['设备材料名称'],r['型号及规范'],r['层级'],r['备注'],r._source?.sheet,String(r['序号'])]).toLowerCase().includes(q));
}
function cleanHTML(){
 const d=state.dataset;
 if(!d)return '<div class="card empty"><div class="empty-icon">▱</div><h3>尚未清洗资料</h3><p>在输入框下方选择工作空间，点击“资料清洗”。清洗在本机完成，不调用在线模型。</p></div>';
 const items=cleanRecords(),pages=Math.max(1,Math.ceil(items.length/CLEAN_PAGE));cleanPage=Math.min(cleanPage,pages-1);
 const cols=CLEAN_COLUMNS[cleanSide],edits=cleanEdits[cleanSide],deletes=cleanDeletes[cleanSide],pending=cleanPending();
 const rows=items.slice(cleanPage*CLEAN_PAGE,(cleanPage+1)*CLEAN_PAGE).map(r=>{
  const id=String(r['序号']),gone=deletes.has(id),changed=edits[id]||{};
  return `<tr class="${gone?'clean-deleted':''}" data-id="${esc(id)}"><td class="clean-no">${esc(id)}</td>${cols.map(([key,,get])=>{const value=key in changed?changed[key]:get(r)??'';return `<td class="clean-cell${key in changed?' edited':''}${['quantity','price','unit'].includes(key)?' narrow':''}" contenteditable="${gone?'false':'plaintext-only'}" spellcheck="false" data-key="${key}">${esc(value)}</td>`}).join('')}<td class="clean-src"><button type="button" class="match-source" title="在原始表格中定位：${esc((r._source?.file||'')+' / '+(r._source?.sheet||'')+' 第 '+(r._source?.row??'')+' 行')}" onclick="openCleanSource('${esc(id)}')">${esc(r._source?.sheet||'')} · ${esc(r._source?.row??'')} ↗</button></td><td class="clean-op"><button type="button" class="link-button" onclick="toggleCleanDelete('${esc(id)}')">${gone?'恢复':'删除'}</button></td></tr>`}).join('');
 return `<div class="card table-card clean-results${cleanExpanded?' match-expanded':''}"><div class="card-head"><h3>数据清洗结果</h3><div class="match-switch"><button class="button" aria-pressed="${cleanSide==='sources'}" onclick="setCleanSide('sources')">造价数据 ${d.sources.length}</button><button class="button" aria-pressed="${cleanSide==='targets'}" onclick="setCleanSide('targets')">清册数据 ${d.targets.length}</button><button class="button" onclick="cleanExpanded=!cleanExpanded;renderCanvas()">${cleanExpanded?'退出全屏':'全屏查看'}</button></div></div>
 <div class="table-controls"><input id="cleanSearch" value="${esc(cleanSearch)}" placeholder="搜索名称、型号、层级或来源" onchange="cleanSearch=this.value;cleanPage=0;renderCanvas()"><button class="button" onclick="discardCleanEdits()" ${pending?'':'disabled'}>撤销修改</button><button class="primary" onclick="saveCleanEdits()" ${pending?'':'disabled'}>保存修改${pending?' ('+pending+')':''}</button></div>
 <div class="table-scroll clean-scroll"><table class="clean-table"><thead><tr><th>序号</th>${cols.map(([,label])=>`<th>${label}</th>`).join('')}<th>来源</th><th></th></tr></thead><tbody>${rows||`<tr><td colspan="${cols.length+3}" class="empty">没有符合条件的条目</td></tr>`}</tbody></table></div>
 <div class="table-footer"><span>共 ${items.length} 条${cleanSearch?' · 搜索“'+esc(cleanSearch)+'”':''} · 第 ${cleanPage+1}/${pages} 页${pending?' · 有 '+pending+' 处未保存':''}</span><div><button onclick="cleanPage=Math.max(0,cleanPage-1);renderCanvas()" ${cleanPage?'':'disabled'}>← 上页</button><button onclick="cleanPage=Math.min(${pages-1},cleanPage+1);renderCanvas()" ${cleanPage<pages-1?'':'disabled'}>下页 →</button></div></div></div>`;
}
// 在原始工作簿快照中定位并高亮该条清洗数据的来源行
function openCleanSource(id){const r=(state.dataset?.[cleanSide]||[]).find(x=>String(x['序号'])===String(id));if(r)openMaterialSource(r._source);}
function setCleanSide(side){cleanSide=side;cleanPage=0;renderCanvas()}
function toggleCleanDelete(id){const set=cleanDeletes[cleanSide];set.has(id)?set.delete(id):set.add(id);renderCanvas()}
function discardCleanEdits(){cleanEdits[cleanSide]={};cleanDeletes[cleanSide].clear();renderCanvas()}
// 输入时与原值比较，记下改动；改回原值即取消该处修改
function noteCleanEdit(e){
 const cell=e.target.closest?.('.clean-cell');if(!cell)return;
 const id=cell.closest('tr').dataset.id,key=cell.dataset.key,record=(state.dataset?.[cleanSide]||[]).find(r=>String(r['序号'])===id);if(!record)return;
 const original=String(CLEAN_COLUMNS[cleanSide].find(c=>c[0]===key)[2](record)??''),value=cell.textContent.trim(),edits=cleanEdits[cleanSide];
 if(value===original){if(edits[id]){delete edits[id][key];if(!Object.keys(edits[id]).length)delete edits[id];}}
 else (edits[id]||(edits[id]={}))[key]=value;
 cell.classList.toggle('edited',value!==original);renderCleanFooter();
}
document.addEventListener('input',noteCleanEdit);
document.addEventListener('focusout',noteCleanEdit);
document.addEventListener('keydown',e=>{if(e.target.closest?.('.clean-cell')&&e.key==='Enter'&&!e.shiftKey){e.preventDefault();e.target.blur();}});
function renderCleanFooter(){const n=cleanPending(),save=document.querySelector('.clean-results .primary'),undo=document.querySelector('.clean-results .table-controls .button');if(save){save.disabled=!n;save.textContent='保存修改'+(n?' ('+n+')':'');}if(undo)undo.disabled=!n;}
async function saveCleanEdits(){
 document.activeElement?.blur?.();
 const side=cleanSide,changes=Object.entries(cleanEdits[side]).map(([id,fields])=>({id,fields})),deletes=[...cleanDeletes[side]];
 if(!changes.length&&!deletes.length)return;
 if(state.stage>=2&&!confirm('修改清洗结果后，已有的数量评审结果将失效（保留在检查点中），需要重新执行数量评审。继续保存？'))return;
 const ok=await action('data/batch',{side,changes,deletes});
 if(ok){cleanEdits[side]={};cleanDeletes[side].clear();toast('清洗结果已保存');renderCanvas();}
}

// 数量评审前的确认框：说明发送内容与脱敏处理，用户点“是”才开始
async function confirmSendToModel(){
 let info;try{const r=await fetch('/api/privacy/preview',{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Demo':'1'},body:'{}'});info=await r.json();if(!r.ok)throw Error(info.error);}catch(e){toast(e.message||'无法生成脱敏摘要');return false;}
 if(cleanPending('sources')+cleanPending('targets')){toast('清洗结果有未保存的修改，请先保存或撤销。');setTab('clean');togglePanel(true);return false;}
 const dialog=$('consentDialog'),stations=info.stations.map(s=>`${esc(s.name)} → ${esc(s.alias)}`).join('、');
 dialog.querySelector('.consent-body').innerHTML=`<p class="consent-warn">数量评审将把<b>本机清洗后的物资数据</b>发送给在线模型（${esc(state.config?.model||'未配置模型')}）。原始资料可能含敏感信息，务必确认已脱敏。</p>
 <ul class="consent-list"><li>发送：造价 ${info.sources} 条、清册 ${info.targets} 条，字段为${info.fields.join('、')}。</li>
 <li>不发送：${info.withheld.join('、')}。</li>
 <li>自动脱敏：${stations?'站名替换为代号（'+stations+'），结果返回后在本机换回原名；':'未发现站名；'}单位名称 ${info.orgs} 处替换为“某单位”；联系方式、证件号 ${info.contacts} 处已遮盖。</li></ul>
 <p class="hint">其他需要隐去的内容，请先在结果面板“清洗结果”中修改或删除，再开始数量评审。</p>`;
 dialog.returnValue='';dialog.showModal();
 return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='yes'),{once:true}));
}
function reviewCleanFirst(){$('consentDialog').close('');setTab('clean');togglePanel(true);}

// 步骤结束后自动打开结果面板：清洗完成看清洗结果，数量评审完成看评审结果，价格评审完成看价格结果
const FLOW_TABS={'数据清洗草稿':'clean','数量评审完成':'quantity','价格评审完成':'price'};
let lastFlow=null;
function autoOpenResults(){
 if(!state)return;
 // 页面加载时只记下当前检查点；之后出现新的步骤检查点才打开（价格评审很快，未必能看到“执行中”）
 const last=(state.history||[]).at(-1),flow={session:state.session_id,checkpoint:last?last.id:''};
 const before=lastFlow;lastFlow=flow;
 if(!before||before.session!==flow.session||flow.checkpoint===before.checkpoint)return;
 const target=FLOW_TABS[last?.label];
 if(!target)return;
 if(view!=='quantity')switchView('quantity');
 setTab(target);togglePanel(true);
}
const cleanRender=render;
render=function(){cleanRender();autoOpenResults();};
// 正在修改单元格时，后台状态刷新不重绘表格，避免打断输入
const cleanCanvas=renderCanvas;
renderCanvas=function(){if(tab==='clean'&&document.activeElement?.classList?.contains('clean-cell'))return;cleanCanvas();};
