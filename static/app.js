let state = null, view = 'quantity', tab = 'quantity', page = 0, filter = '', search = '', selectedRow = null, lastRevision = -1, dataSide='sources', dataPage=0, chatRefs=[], selectedKeys=new Set();
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const colors = {'一致':'#7da474','基本一致':'#a2bdb0','部分一致':'#d3b981','不一致':'#cba58e','待复核':'#dce4d4','评审未完成':'#e6d3d8','不参与核对':'#e4e4e0','含于成套项':'#c9d9e8'};
const badge = (b, done=false) => `<span class="badge ${b==='一致'?'good':b==='基本一致'?'basic':['部分一致','偏差','待复核','评审未完成'].includes(b)?'warn':['不一致','显著偏差','不可比'].includes(b)?'bad':''}">${done?'✓ ':''}${esc(b)}</span>`;
function toast(t){$('toast').textContent=t;$('toast').style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('toast').style.display='none',5500);}
async function api(path,data={}){const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Demo':'1'},body:JSON.stringify(data)});if(r.status===401){location.href='/login';throw Error('登录已失效，请重新登录');}const b=await r.json();if(!r.ok)throw Error(b.error);await refresh(true);return b;}
async function action(path,data={}){try{return await api(path,data)}catch(e){toast(e.message)}}
async function refresh(force=false){try{const r=await fetch('/api/state'+(!force&&lastRevision>=0?'?since='+lastRevision:''));if(r.status===401){location.href='/login';return;}const incoming=await r.json();if(incoming.unchanged)return;state=incoming;if(force||state.revision!==lastRevision){lastRevision=state.revision;render();}}catch(e){if(!state)toast('本地服务未连接，请重新启动工作台。')}}
function render(){
 $('projectName').innerHTML=esc(state.project)+'<small>'+esc(state.dataset?'本地资料 · '+state.dataset.files.length+' 个文件':'等待导入工程资料')+'</small>';
 
 $('runStatus').textContent=state.running?(state.pause?'正在等待检查点':'正在执行…'):['等待资料','待确认清洗结果','数量评审未完成','待复核结论','报告草稿就绪'][state.stage];
 renderConversation();
 renderCanvas();
}
function switchView(v){view=v;document.querySelectorAll('.nav[data-view]').forEach(e=>e.classList.toggle('active',e.dataset.view===v));$('viewTitle').textContent={quantity:'物资智能评审',drawing:'图纸识别与提资',database:'物资价格数据库'}[v];$('canvasTitle').textContent={quantity:'物资评审工作台',drawing:'图纸提资工作台',database:'价格库管理工作台'}[v];renderCanvas();}
function setTab(t){tab=t;document.querySelectorAll('[data-tab]').forEach(e=>e.classList.toggle('selected',e.dataset.tab===t));renderCanvas();}
// 勾选、展开折叠等操作会重绘结果表；筛选、搜索、视图都没变时保留表格滚动位置，不跳回第一条
let matchScrollKey='';
function renderCanvas(){if(!state)return;$('quantityView').hidden=view!=='quantity';$('drawingView').hidden=view!=='drawing';$('databaseView').hidden=view!=='database';if(view==='drawing')return renderDrawing();if(view==='database')return renderDatabase();
 const key=JSON.stringify([tab,matchingView,filter,search,matchingExpanded]),box=document.querySelector('.match-scroll'),keep=box&&key===matchScrollKey?[box.scrollTop,box.scrollLeft]:null;
 // 以视野内第一条物资为锚点：重绘后它仍在原来的位置
 const top=box?.getBoundingClientRect().top,anchor=keep&&[...box.querySelectorAll('input[data-key]')].find(c=>c.getBoundingClientRect().top>=top),offset=anchor&&anchor.getBoundingClientRect().top-top;
 $('tabContent').innerHTML=(state.error?`<div class="error">${esc(state.error)}</div>`:'')+({clean:cleanHTML,quantity:quantityHTML,price:priceHTML,docs:docsHTML,data:dataHTML,history:historyHTML,skills:skillsHTML}[tab])();
 const next=document.querySelector('.match-scroll');
 if(next&&keep){[next.scrollTop,next.scrollLeft]=keep;const same=anchor&&next.querySelector(`input[data-key="${CSS.escape(anchor.dataset.key)}"]`);if(same)next.scrollTop+=same.getBoundingClientRect().top-next.getBoundingClientRect().top-offset}
 matchScrollKey=key;}
// 结果面板的流程：资料清洗 → 数量评审（候选配对与逐项核对）→ 价格评审 → 成果报告
function workflow(){const stage=Number(state.stage||0),priced=(state.price_results||[]).length>0;
 const steps=[['资料清洗',stage>=1,!stage],['数量评审',stage>=3,stage===1||stage===2],['价格评审',priced,stage>=1&&!priced],['成果报告',stage>=4,stage>=3]];
 return '<div class="workflow">'+steps.map(([n,done,current],i)=>`<div class="step ${done?'done':current?'current':''}"><em>${done?'✓':i+1}</em>${n}</div>`).join('')+'</div>'}
function metrics(items){return '<div class="summary-cards">'+items.map(([label,value,sub,cl])=>`<div class="metric"><label>${label}</label><strong class="${cl||''}">${value}</strong><small>${sub}</small></div>`).join('')+'</div>'}
function quantityHTML(){
 let html='';
 if(!state.dataset)return html+`<div class="card empty"><div class="empty-icon">▱</div><h3>从一个工程文件夹开始</h3><p>添加本地资料，依次完成资料清洗（本机规则）、数量评审和价格评审。</p><button class="primary" onclick="openFolder()">＋ 添加工程文件夹</button></div><div class="notice">三个业务工作区，覆盖方案四项研究内容。资料清洗在本机按规则完成，不调用在线模型；数量评审只发送脱敏后的清洗结果。</div>`;
 if(state.stage===1)html+=`<div class="card"><h3>清洗草稿已生成 <small>检查点 01</small></h3><p class="data-count">造价与清册已在本机清洗。请在“清洗结果”中确认数量、单位，并修改或删除不应外发的敏感信息；选表依据见“数据与来源”。</p><div class="notice warning">${state.dataset.warnings.map(esc).join('<br>')}</div><button class="button" onclick="setTab('clean')">查看清洗结果 →</button></div>`;
 if(state.rows.length){
 html+=matchingResultsHTML();
 }
 html+=`<div class="action-bar"><small>${state.running?'处理完成后自动停在检查点':'每步结束后等待你确认'}</small><div>${state.running?'<button class="button" onclick="sendText(\'暂停\')">暂停</button>':`<button class="primary" onclick="advanceFlow()" ${state.stage===4?'disabled':''}>${{1:'确认清洗 · 开始数量评审',2:'继续数量评审',3:'生成报告草稿',4:'流程已完成'}[state.stage]} →</button>`}</div></div>`;return html;
}
function dataHTML(){if(!state.dataset)return '<div class="empty">请先添加工程资料</div>';const d=state.dataset,items=d[dataSide];dataPage=Math.min(dataPage,Math.max(0,Math.ceil(items.length/20)-1));return `<div class="notice warning">${d.warnings.map(esc).join('<br>')}</div><div class="card"><h3>来源文件 <small>${d.files.length} 个文件</small></h3>${d.files.map(f=>`<div class="file-row"><span>${esc(f.type.slice(1).toUpperCase())}</span>${esc(f.name)}<small>${Math.round(f.size/1024)} KB</small></div>`).join('')}</div><div class="card"><div class="card-head"><h3>标准化数据 · 点击修正</h3><select onchange="dataSide=this.value;dataPage=0;renderCanvas()"><option value="sources" ${dataSide==='sources'?'selected':''}>估算数据</option><option value="targets" ${dataSide==='targets'?'selected':''}>清册数据</option></select></div><div class="table-scroll"><table><tr><th>序号</th><th>名称</th><th>数量</th><th>来源</th></tr>${items.slice(dataPage*20,dataPage*20+20).map(r=>`<tr class="clickable" onclick="editData(${r['序号']})"><td>${r['序号']}</td><td>${esc(r.Estimation_Name||r['设备材料名称'])}</td><td>${esc(r.Estimation_Quantity??r['数量'])} ${esc(r.Estimation_Unit||r['单位'])}</td><td>${esc(r._source?.sheet)} · ${r._source?.row}</td></tr>`).join('')}</table></div><div class="table-footer"><span>共 ${items.length} 条 · 第 ${dataPage+1} 页</span><button onclick="dataPage=Math.max(0,dataPage-1);renderCanvas()">← 上页</button><button onclick="dataPage++;renderCanvas()">下页 →</button></div><div class="action-bar"><button class="button" onclick="downloadURL('/api/estimates','估算数据.txt')">↓ 估算数据.txt</button><button class="button" onclick="downloadURL('/api/inventory','清册数据.txt')">↓ 清册数据.txt</button></div></div><div class="card"><h3>清洗工作表台账</h3>${d.sheet_audit.map(s=>`<div class="file-row">${esc(s.sheet)}<small>${s.rows} 条</small></div>`).join('')||'<p class="muted">本次导入已有 JSON 数据</p>'}</div>`;}
function editData(id){const r=state.dataset[dataSide].find(r=>r['序号']===id);$('detailContent').innerHTML=`<div class="dialog-head"><h2>修正清洗结果 #${id}</h2><button onclick="closeDialog('detailDialog')">×</button></div><p class="hint">来源：${esc(r._source?.file)} / ${esc(r._source?.sheet)} / 行 ${r._source?.row}。保存后自动使下游结果失效。</p><label>物资名称</label><input id="editName" value="${esc(r.Estimation_Name||r['设备材料名称'])}"><label>型号规格</label><input id="editSpec" value="${esc(r['型号及规范'])}"><div class="detail-grid"><div><label>数量</label><input id="editQuantity" value="${esc(r.Estimation_Quantity??r['数量'])}"></div><div><label>单位</label><input id="editUnit" value="${esc(r.Estimation_Unit||r['单位'])}"></div></div><label>修改依据</label><input id="editNote" placeholder="例如：按原表工程列修正数量"><div class="dialog-actions"><button class="primary" onclick="saveData(${id})">保存修正并重新匹配</button></div>`;$('detailDialog').showModal()}
async function saveData(id){const ok=await action('data/edit',{side:dataSide,id,name:$('editName').value,spec:$('editSpec').value,quantity:$('editQuantity').value,unit:$('editUnit').value,note:$('editNote').value});if(ok)closeDialog('detailDialog')}
function historyHTML(){return `<div class="card"><h3>检查点与回退 <small>本地持久化</small></h3><div class="timeline">${state.history.slice().reverse().map(h=>`<div><time>${esc(h.time)}</time>${esc(h.label)}<button onclick="action('restore',{id:'${h.id}'})">恢复</button></div>`).join('')||'<p class="muted">完成首个步骤后生成检查点</p>'}</div></div><div class="card"><h3>执行日志</h3><div class="log">${state.logs.map(l=>esc(l.time+'  '+l.text)).join('\n')||'尚未调用在线匹配引擎'}</div></div>`;}
// 价格评审页签：按结论分档与不可比原因分类，点开看完整换算过程与候选条目
let priceFilter='';
function priceCounts(){const out={};(state.price_results||[]).forEach(r=>{const k=r.delta!==null?'已比较':(r.status||'不可比');out[k]=(out[k]||0)+1});return out;}
function priceFilterHTML(){const counts=priceCounts(),keys=Object.keys(counts).sort((a,b)=>counts[b]-counts[a]);if(!keys.length)return '';
 return `<div class="action-bar" style="margin:0 0 12px;flex-wrap:wrap">${['',...keys].map(k=>`<button class="button${priceFilter===k?' primary':''}" onclick="setPriceFilter('${esc(k)}')">${k?esc(k):'全部'} ${k?counts[k]:(state.price_results||[]).length}</button>`).join('')}</div>`;}
function setPriceFilter(k){priceFilter=priceFilter===k?'':k;renderCanvas();}
function priceHTML(){const all=state.price_results||[],compared=all.filter(r=>r.delta!==null),
 attention=compared.filter(r=>!['一致','基本一致'].includes(r.band)),high=attention.filter(r=>r.direction==='偏高'),
 rows=all.map((r,i)=>[r,i]).filter(([r])=>!priceFilter||(r.delta!==null?'已比较':(r.status||'不可比'))===priceFilter);
 return `<div class="notice">默认分级：≤2% 一致，≤5% 基本一致，≤${state.threshold}% 偏差，超过 ${state.threshold}% 显著偏差（对话里发“价格阈值 15”可改）。信息价按导入时声明的税口径与构成系数换算后，与造价单价比较：甲供设备的表三甲单价是含税市场价，直接比；乙供设备与装置性材料是不含税${state.cost_tax?`（当前已统一指定为${esc(state.cost_tax)}价）`:'（发“造价税口径 含税”可统一指定）'}；单位不同量纲、同名不同型号、一名多价都不自动取值。</div>
 ${metrics([['已比较',compared.length,`共 ${all.length} 条造价物资`],['需关注',attention.length,`偏高 ${high.length} · 偏低 ${attention.length-high.length}`,'amber'],['未能比较',all.length-compared.length,'按原因分类，见下方筛选','green']])}
 ${priceJudgeHTML(all)}
 ${priceFilterHTML()}
 <div class="card table-card"><div class="card-head"><h3>价格偏差分析</h3><span>${priceFilter?`筛选：${esc(priceFilter)} ${rows.length} 条`:`共 ${all.length} 条`}</span></div><div class="table-scroll"><table><tr><th>造价物资</th><th>造价 / 换算后信息价</th><th>偏差率</th><th>结论</th></tr>${rows.map(([r,i])=>`<tr class="clickable" onclick="priceDetail(${i})"><td class="name-cell"><b>${esc(r.item.name)}</b><small>${esc(r.item.spec||'')}</small><small>${esc(r.item.unit||'')}${r.item.supply?' · '+esc(r.item.supply):''}${r.item.group?' · '+esc(r.item.group):''}</small></td><td>${esc(r.cost_price??r.item.price??'—')}${r.reference?` / ${Number(r.reference.price).toLocaleString()}`:' / —'}${r.conversion&&r.delta!==null?`<small class="muted">换算后 ${(Number(r.cost_price)/(1+r.delta/100)).toFixed(2)}</small>`:''}${r.cost_price!=null&&r.item.price!=null&&Number(r.cost_price)!==Number(r.item.price)?`<small class="muted">表三甲 ${esc(r.item.price)}（预算价）</small>`:''}</td><td>${r.delta===null?'—':(r.delta>0?'+':'')+r.delta.toFixed(2)+'%'}${r.delta===null?'':`<div class="price-bar"><span style="width:${Math.min(100,Math.abs(r.delta)*5)}%"></span></div>`}</td><td>${badge(r.band+(r.direction||''))}${r.delta===null&&r.status?`<small class="muted">${esc(r.status)}</small>`:''}${r.basis_note?`<small class="warn">基准待确认</small>`:''}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">先在“物资价格数据库”导入信息价，再执行价格评审。</td></tr>'}</table></div></div>`;}
const PRICE_PENDING=['无对应条目','型号不同','候选多价'];
function priceJudgeHTML(all){const pending=all.filter(r=>r.delta===null&&PRICE_PENDING.includes(r.status)).length;
 if(!pending)return '';
 const picked=all.filter(r=>r.pick).length;
 return `<div class="notice warning">还有 ${pending} 条是匹配问题（名称或型号写法对不上），本机规则拿不下。可以让模型在程序召回的候选里判定是不是同一物资；判定结果写成“指定参考条目”，逐条可改可撤。${picked?` 当前已有 ${picked} 条指定了参考条目。`:''}<div class="action-bar" style="margin-top:10px"><small>发送前在本机脱敏，与数量评审同一份同意记录</small><button class="primary" onclick="judgePrices()">用模型判定 ${pending} 条</button></div></div>`;}
async function judgePrices(){if(state?.running)return toast('当前任务正在执行，请完成或停止后再试。');
 if(!await confirmSendToModel())return;
 await action('prices/judge',{consent:true});}
async function pickPrice(source,code){if(!code&&!confirm('取消指定后，这一条回到程序自动匹配的结论。继续？'))return;
 // 依据弹窗里的内容是点开那一刻生成的，指定后关掉它，让用户看到重算后的结论
 if(await action('prices/pick',{source_id:source,code}))closeDialog('detailDialog');}
function priceDetail(i){const r=(state.price_results||[])[i];if(!r)return;
 const source=esc(r.item.source_id||''),chosen=r.pick?r.pick.code:(r.reference?r.reference.code:'');
 const cand=(r.candidates||[]).map(c=>`<p>${esc(c.name)} ${esc(c.spec||'')} · ${esc(c.price)} 元/${esc(c.unit||'？')} · 块${esc(c.block||'—')}${esc(c.block_label||'')}${c.score?` · 相关度 ${esc(c.score)}`:''}${c.comparable?` · 换算后 ${Number(c.comparable).toFixed(2)}`:''}${c.notes?`<br><span class="muted">备注：${esc(c.notes)}</span>`:''}${c.reason?`<br><span class="muted">${esc(c.reason)}</span>`:''}${c.code===chosen?' <b>· 当前采用</b>':` <button class="button" onclick="pickPrice('${source}','${esc(c.code)}')">选它</button>`}</p>`).join('');
 $('detailContent').innerHTML=`<div class="dialog-head"><h2>价格评审依据</h2><button onclick="closeDialog('detailDialog')">×</button></div>
 <p>造价：${esc(r.item.name)} ${esc(r.item.spec||'')} · ${esc(r.item.price??'—')} 元/${esc(r.item.unit||'？')}${r.item.supply?' · '+esc(r.item.supply):''}</p>
 <p>比价基准：${esc(r.item.price_basis||('表三甲单价（'+(r.cost_tax||'')+'）'))}${r.cost_price!=null&&r.item.price!=null&&Number(r.cost_price)!==Number(r.item.price)?` · 实际按 ${esc(r.cost_price)} 元比较`:''}${r.basis_note?`<br><span class="muted">${esc(r.basis_note)}</span>`:''}</p>
 <div class="evidence-block">${esc(r.reason)}</div>
 ${r.pick?`<p>参考条目由${esc(r.pick.by||'人工')}指定${r.pick.confidence?`（置信度 ${esc(r.pick.confidence)}）`:''}${r.pick.reason?`：${esc(r.pick.reason)}`:''} <button class="button" onclick="pickPrice('${esc(r.item.source_id||'')}','')">取消指定</button></p>`:''}
 ${r.conversion?`<p>换算过程：${esc(r.conversion)}</p>`:''}
 ${r.reference?`<p>参考条目：${esc(r.reference.name)} ${esc(r.reference.spec||'')} · ${esc(r.reference.price)} 元/${esc(r.reference.unit||'')}<br>出处：${esc(r.reference.source||'')} 第${esc(r.reference.row||r.reference.page||'—')}行 · 块${esc(r.reference.block||'—')}${esc(r.reference.block_label||'')}<br>口径：${esc(r.reference.period||'')} / ${esc(r.reference.region||'')} / ${esc(r.reference.tax||'')}${r.reference.tax_rate?'（税率'+esc(r.reference.tax_rate)+'%）':''} / ${esc(r.reference.basis||'')}${r.reference.composition&&r.reference.composition!=1?' / 构成系数 '+esc(r.reference.composition):''}${(r.reference.factors||[]).length?'<br>折算系数：'+r.reference.factors.map(f=>esc(f.label)+' '+esc(f.factor)).join('，'):''}</p>`:''}
 ${cand?`<h3 style="font-size:12px;margin-top:16px">候选条目 ${(r.candidates||[]).length} 条</h3>${cand}`:''}
 <p class="hint">信息价只是参考价：期次、区域、税口径与价格构成要与工程编制基准期对齐，结论须由造价人员复核。</p>`;$('detailDialog').showModal()}
// 价格库工作台：导入一个季度的信息价 Excel → 为拼接进来的分表块打标签 → 看重复条目与未导入行 → 按块审核 → 发布期次版本
let priceSearch={q:'',total:0,items:null};
function priceBlocksHTML(){const blocks=state.price_blocks||[];if(!blocks.length)return '';return `<div class="card table-card"><div class="card-head"><h3>分表块标签 <small>标签已按一级类目暂填；类目名分不出主配网与电压等级，逐块核对后确认</small></h3><span>按序号与层级重启自动切块</span></div><div class="table-scroll"><table><tr><th>分表块</th><th>标签</th><th>审核</th></tr>${blocks.map(b=>`<tr><td class="name-cell"><b>块${b.id} · ${b.count} 条</b><small>原表第 ${b.first_row}–${b.last_row} 行</small><small>${esc(b.suggestion||'')}</small></td><td style="min-width:180px"><input id="blockLabel${b.id}" value="${esc(b.label||'')}" placeholder="如 主网设备·35kV及以上" style="width:100%">${b.label_auto?'<small class="warn">按类目暂填，待确认</small>':'<small class="muted">已确认</small>'}</td><td>${b.label&&!b.label_auto?`<button class="button" onclick="approveBlock(${b.id})">整块审核</button>`:'<span class="muted">先确认标签</span>'}</td></tr>`).join('')}</table></div><div class="action-bar"><small>确认后标签随条目一起保存，比价时用来区分同名同型号</small><button class="primary" onclick="saveBlockLabels()">确认标签</button></div></div>`;}
function priceDuplicatesHTML(){const list=state.price_duplicates||[],total=state.price_duplicate_total||0;if(!total)return '';return `<div class="card" style="margin-top:20px"><h3>重复条目 <small>名称、型号、单位相同而价格不同，比价时不自动取值，须人工指定</small></h3><p class="muted">共 ${total} 组，按倍差排序显示前 ${list.length} 组</p>${list.map(d=>`<details><summary>×${d.ratio} ${esc(d.name)} ${esc(d.spec)}（${esc(d.unit)}）· ${d.count} 条</summary>${d.entries.map(e=>`<p>块${e.block} · 原表第${e.row}行 · ${esc(e.price)} 元${e.notes?' · 备注：'+esc(e.notes):''}</p>`).join('')}</details>`).join('')}</div>`;}
function priceRejectedHTML(){const list=state.price_rejected||[],total=state.price_rejected_total||0;if(!total)return '';return `<div class="card" style="margin-top:20px"><h3>未导入的行 <small>原表无名称或无有效单价，保留行号备查</small></h3><p class="muted">共 ${total} 行，显示前 ${list.length} 行</p>${list.map(r=>`<p>第${r.row}行 · ${esc(r.name||'（无名称）')} · ${esc(r.reason)}</p>`).join('')}</div>`;}
function renderDatabase(){const rows=priceSearch.items||state.prices||[],total=state.price_total||0,changes=rows.filter(p=>(state.anomaly_codes||[]).includes(p.code)),pending=(state.price_blocks||[]).filter(b=>!b.label||b.label_auto).length;$('databaseView').innerHTML=`<div class="notice">导入信息价 Excel → 为分表块打标签 → 核对重复条目与未导入行 → 按块审核 → 发布期次版本。税口径、税率与构成系数由导入时声明，表里没有这些信息；价格评审按工程编制基准期选用对应期次。</div>${metrics([['信息价条目',total,'期次、出处与原表行号完整保留'],['待确认标签',pending,pending?'按类目暂填，需逐块核对':'分表块标签已确认',pending?'amber':'green'],['重复条目',state.price_duplicate_total||0,'同名同型号不同价，须人工指定','amber'],['已审核',state.price_approved||0,'全部审核且标签齐全后可发布','green']])}<div class="action-bar" style="margin-bottom:18px"><button class="primary" onclick="openPriceImport()">＋ 导入信息价 Excel</button><button class="button" onclick="uploadJSON('prices/import')">导入价格 JSON</button><button class="button" onclick="downloadPriceTemplate()">JSON 格式示例</button></div>${priceBlocksHTML()}<div class="card table-card" style="margin-top:20px"><div class="card-head"><h3>本期价格审核</h3><span>${priceSearch.items?`检索“${esc(priceSearch.q)}”命中 ${priceSearch.total} 条`:`共 ${total} 条，显示前 ${rows.length} 条`}</span></div><div class="action-bar" style="margin:0 0 12px"><input id="priceQuery" value="${esc(priceSearch.q)}" placeholder="按名称或型号检索信息价" style="flex:1" onkeydown="if(event.key==='Enter')searchPrices()"><button class="button" onclick="searchPrices()">检索</button>${priceSearch.items?'<button class="button" onclick="clearPriceSearch()">清除</button>':''}</div><div class="table-scroll"><table><tr><th>信息价条目</th><th>本期单价</th><th>环比</th><th>审核</th></tr>${rows.map(p=>`<tr><td class="name-cell"><b>${esc(p.name)}</b><small>${esc(p.spec||'')}</small><small>块${esc(p.block||'—')} ${esc(p.block_label||'待打标签')}${(state.price_blocks||[]).some(b=>b.id===p.block&&b.label_auto)?'（待确认）':''} · ${esc(p.category||'')}${p.subcategory?' / '+esc(p.subcategory):''}</small><small>${esc(p.source)} 第${esc(p.row||p.page)}行 · ${esc(p.period)} · ${esc(p.tax)}${p.tax_rate?'（税率'+esc(p.tax_rate)+'%）':''}</small>${p.price_warning?`<small class="warn">${esc(p.price_warning)}</small>`:''}${(p.factors||[]).length?`<small>折算系数：${p.factors.map(f=>esc(f.label)+' '+esc(f.factor)).join('，')}</small>`:''}</td><td>${esc(p.price)}<br><span class="muted">元/${esc(p.unit||'？')}</span></td><td>${p.previous?((p.price-p.previous)/p.previous*100).toFixed(1)+'%':'—'}</td><td>${p.approved?badge('已审核',true):`<button class="button" onclick="approvePrice('${esc(p.code)}')">审核</button>`}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">等待导入信息价</td></tr>'}</table></div></div>${priceDuplicatesHTML()}${priceRejectedHTML()}<div class="action-bar"><small>${changes.length?`当前页有 ${changes.length} 条环比超过 ${state.threshold}%`:'推算价格必须保留参考标识'}</small><button class="primary" onclick="action('prices/publish')">审核完成 · 发布期次版本</button></div><div class="card" style="margin-top:20px"><h3>期次版本 <small>点击恢复对应版本</small></h3><div class="versions">${(state.versions||[]).map(v=>`<button onclick="action('prices/rollback',{id:${v.id}})">v${v.id} · ${esc(v.period||'')} · ${esc(v.time)} · ${v.rows} 条</button>`).join('')||'<span class="muted">尚未发布版本</span>'}</div></div>`;}
async function searchPrices(){const q=($('priceQuery')?.value||'').trim();try{const r=await api('prices/search',{q});priceSearch={q,total:r.total,items:r.items};renderDatabase();}catch(e){toast(e.message)}}
function clearPriceSearch(){priceSearch={q:'',total:0,items:null};renderDatabase();}
function saveBlockLabels(){const labels={};(state.price_blocks||[]).forEach(b=>{const v=$('blockLabel'+b.id)?.value.trim();if(v)labels[b.id]=v});if(!Object.keys(labels).length)return toast('请先填写至少一个分表块的标签。');if(!confirm('确认 '+Object.keys(labels).length+' 个分表块的标签？确认后这些块可整块审核，并计入可发布的期次版本。'))return;action('prices/label',{labels})}
function approveBlock(id){const note=prompt('核对这一块的出处、期次、税口径与构成系数后填写审核意见：','已核对本块出处与口径');if(note)action('prices/approve_block',{block:id,note})}
// 导入信息价 Excel：表里只有名称、型号、单位、单价和备注，期次与口径必须在这里声明
function openPriceImport(){let dialog=$('priceImportDialog');if(!dialog){dialog=document.createElement('dialog');dialog.id='priceImportDialog';dialog.className='model-dialog';document.body.append(dialog);}dialog.innerHTML=`<form onsubmit="submitPriceImport(event)"><div class="dialog-head"><h2>导入信息价 Excel</h2><button type="button" aria-label="关闭" onclick="closeDialog('priceImportDialog')">×</button></div><label>信息价文件</label><div class="action-bar" style="margin:0 0 12px"><input id="priceFile" placeholder="选择或粘贴 .xlsx 路径" style="flex:1"><button type="button" class="button" onclick="pickPriceFile()">选择文件</button></div><div class="detail-grid"><div><label>期次 <small>按工程编制基准期选用</small></label><input id="pricePeriod" placeholder="如 2026Q4 或 20261031"></div><div><label>区域 / 发布单位</label><input id="priceRegion" placeholder="如 XX公司"></div></div><div class="detail-grid"><div><label>税口径</label><select id="priceTax"><option value="含税">含税</option><option value="除税">除税</option></select></div><div><label>税率（%）</label><input id="priceTaxRate" type="number" min="0" max="30" step="0.5" value="13"></div><div><label>构成系数</label><input id="priceComposition" type="number" min="0.5" max="2" step="0.01" value="1"></div></div><label>交货口径</label><input id="priceBasis" placeholder="如 到现场仓库出库价 / 出厂价"><p class="hint">表头写“含税单价”时选含税：比价前按税率换算成除税价再与造价侧单价相比，换算过程写进结论。构成系数用于补运杂费与采购保管费，不确定时填 1，并在交货口径里写明原表口径。</p><div class="dialog-actions"><button type="button" class="button" onclick="closeDialog('priceImportDialog')">取消</button><button class="primary">解析并导入</button></div></form>`;dialog.showModal();}
async function pickPriceFile(){try{const r=await api('browse_file');if(r.path)$('priceFile').value=r.path}catch(e){toast(e.message)}}
async function submitPriceImport(e){e.preventDefault();const body={path:$('priceFile').value.trim(),period:$('pricePeriod').value.trim(),region:$('priceRegion').value.trim(),tax:$('priceTax').value,tax_rate:$('priceTaxRate').value,composition:$('priceComposition').value,basis:$('priceBasis').value.trim()};if(!body.path)return toast('请先选择信息价文件。');priceSearch={q:'',total:0,items:null};if(await action('prices/import_excel',body)){closeDialog('priceImportDialog');renderDatabase();}}
function renderDrawing(){let grounding=state.drawing.find(r=>r.field==='接地装置套数'),towers=state.drawing.find(r=>r.field==='杆塔总基数');let conflict=(state.drawing_conflicts||[]).length>0;$('drawingView').innerHTML=`<div class="notice warning">上传真实图纸进行识别与校核。</div><div class="card"><div class="card-head"><h3>字段与图纸对照</h3></div><div class="drawing-canvas"><p>上传真实图纸后可查看原图及识别字段。</p></div></div>${conflict?'<div class="notice warning">交叉校核冲突：接地装置套数与杆塔总基数不同。请核查图纸后修正字段，不能直接采用。</div>':''}<div class="card table-card"><div class="card-head"><h3>提资字段清单</h3><span>点击数值进入人工校核</span></div><table><tr><th>提资字段</th><th>识别值</th><th>出处 / 置信度</th><th>状态</th></tr>${state.drawing.map((r,i)=>`<tr><td>${esc(r.field)}</td><td><button class="value-button" onclick="editDrawing(${i})">${esc(r.value)} ${esc(r.unit)} ↗</button></td><td>${esc(r.source)}<div class="mini-bar"><i style="width:${r.confidence*100}%"></i></div></td><td>${badge(r.confirmed?'已复核':r.confidence<.8?'待复核':'待确认',r.confirmed)}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">上传图纸后可查看识别字段并逐项复核。</td></tr>'}</table></div><div class="action-bar"><small>全部复核且冲突消除后可导出确认版</small><button class="primary" onclick="exportDrawing()">↓ 导出提资单 CSV</button></div>`;}
function openDetail(id){selectedRow=id;const r=state.rows.find(x=>x.id===id);const source=x=>x?`${x.file} / ${x.sheet} / 第 ${x.row} 行（原序号 ${x.original_id??'—'}）`:'无对应来源';$('detailContent').innerHTML=`<div class="dialog-head"><h2>物资证据与人工复核</h2><button onclick="closeDialog('detailDialog')">×</button></div><div class="detail-grid"><div><small>估算条目 #${esc(r.source_id)}</small>${esc(r.name)}<p>${esc(r.spec)}</p><b>${esc(r.quantity)} ${esc(r.unit)}</b><p>${esc(source(r.source))}</p></div><div><small>清册条目 #${esc(r.target_id)}</small>${esc(r.target)}<p>${esc(r.target_spec)}</p><b>${esc(r.target_quantity)} ${esc(r.target_unit)}</b><p>${esc(source(r.evidence))}</p></div></div><div class="evidence-block">${badge(r.band)}<br>${esc(r.reason)}</div>${r.edits?.length?`<div class="edit-history"><small>对话修改记录</small>${r.edits.map(e=>`<p><time>${esc(e.time)}</time><b>${esc(e.id)}</b> ${esc(e.summary)}</p>`).join('')}</div>`:''}${r.source_id?`<label>修改对应清册序号（留空解除；会重新校核）</label><div class="input-row"><input id="pairTarget" value="${esc(r.target_id)}"><button class="button" onclick="changePair()">应用配对</button></div>`:''}<label>人工复核意见</label><input id="humanNote" placeholder="填写查证依据和结论" value="${esc(r.human_note||'')}"><div class="dialog-actions"><button class="button" onclick="askRow(${Number(r.id)})">追问此条</button><button class="primary" onclick="confirmRow()">确认并保存复核记录</button></div>`;$('detailDialog').showModal();}
async function confirmRow(){const ok=await action('review',{id:selectedRow,note:$('humanNote').value});if(ok){closeDialog('detailDialog');toast('复核记录已保存')}}
async function changePair(){const r=state.rows.find(x=>x.id===selectedRow);const ok=await action('pair',{source_id:r.source_id,target_id:$('pairTarget').value});if(ok)closeDialog('detailDialog')}
function approvePrice(code){const note=prompt('核对出处、期次、规格与价格后填写审核意见：','已核对本条信息价');if(note)action('prices/approve',{code,note})}
function editDrawing(i){const r=state.drawing[i];const value=prompt('核查 '+r.field+' 后填写正确值（'+r.unit+'）：',r.value);if(value!==null)action('drawing/review',{index:i,value,note:'用户对照资料人工修改'})}
function openFolder(){$('folderPath').value=state?.default_folder||'';$('folderDialog').showModal()}
async function browseFolder(){try{const r=await api('browse');if(r.path)$('folderPath').value=r.path}catch(e){toast(e.message)}}
async function submitFolder(e){e.preventDefault();const ok=await action('load',{mode:'folder',path:$('folderPath').value});if(ok){closeDialog('folderDialog');switchView('quantity');setTab('quantity')}}
function closeDialog(id){$(id).close()}
function openSettings(){const c=state.config;$('urlInput').value=c.base_url;$('modelInput').value=c.model;$('batchInput').value=c.batch;$('webSearchInput').checked=c.web_search!==false;$('keyInput').value='';$('settingsDialog').showModal()}
function preset(v){if(v==='deepseek'){$('urlInput').value='https://api.deepseek.com';$('modelInput').value='deepseek-flash'}else{$('urlInput').value='';$('modelInput').value=''}}
async function saveSettings(e){e.preventDefault();const ok=await action('config',{mode:'online',base_url:$('urlInput').value,model:$('modelInput').value,api_key:$('keyInput').value,batch:Number($('batchInput').value),web_search:$('webSearchInput').checked});if(ok){$('keyInput').value='';closeDialog('settingsDialog')}}
async function sendText(t,refs=[]){t=t.trim()||(refs.length?'这些结果的总体匹配情况如何？差异原因是什么？':'');if(!t)return;const ok=await action('chat',{text:t,refs:refs.map(({id,...ref})=>ref)});if(ok&&refs.length){chatRefs=[];renderChatRefs()}$('chat').scrollTop=$('chat').scrollHeight}
$('chatForm').onsubmit=e=>{e.preventDefault();const t=$('chatInput').value;$('chatInput').value='';sendText(t,chatRefs)};
$('chatInput').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chatForm').requestSubmit()}};
function download(name,content,type='application/json'){const blob=new Blob([content],{type});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
async function downloadURL(url,name){const r=await fetch(url);download(name,JSON.stringify(await r.json(),null,2))}
function exportAudit(){downloadURL('/api/export','造价评审审计包.json')}
// 结果导出：数量评审明细、结论汇总、价格评审与清洗后的两侧数据，一个 Excel 工作簿
async function exportResults(){try{const r=await fetch('/api/results/export');if(!r.ok)throw Error((await r.json()).error||'导出失败');download(`${state.project||'工程'}_评审结果.xlsx`,await r.blob(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');}catch(e){toast(e.message)}}
// 数量评审会把清洗结果发给在线模型：先弹出确认框，同意后才开始
async function advanceFlow(){if(state.stage===1||state.stage===2){if(!await confirmSendToModel())return;return action('advance',{consent:true});}return action('advance');}
function skillsHTML(){return `<div class="notice">直接在对话中描述修改需求，LLM 会修改下列真实 Python 技能文件。通过语法、接口和需求样例校验后落盘，并自动重算受影响的步骤。当前支持这些纯业务函数，不是任意修改整个应用。</div>${(state.skill_versions||[]).slice().reverse().map(v=>`<div class="card"><div class="card-head"><h3>${esc(v.skill_id)} · ${esc(v.time)}</h3><button class="button" onclick="action('skills/rollback',{id:'${v.id}'})">回退本次修改</button></div><p class="data-count">${esc(v.summary)}</p><p class="muted">${esc(v.tests.join(' · '))}</p><details open><summary>查看实际代码差异</summary><pre class="code-diff">${esc(v.diff)}</pre></details></div>`).join('')}<div class="card"><h3>可编辑技能源码</h3>${(state.skill_files||[]).map(f=>`<details class="skill-source"><summary>${esc(f.name)} <small>${esc(f.id)}.py</small></summary><p class="data-count">${esc(f.effect)}</p><p class="source-path">${esc(f.path)}</p><pre class="code-diff">${esc(f.code)}</pre></details>`).join('')}</div><div class="notice warning">原有匹配引擎的全部实现、依赖安装与系统命令不在当前自动改写范围内。超出技能契约的修改会明确提示。</div>`;}
function shutdownApp(){if(state.running)return toast('请等待当前步骤完成后退出');if(confirm('退出本地服务？检查点已自动保存。'))fetch('/api/shutdown',{method:'POST',headers:{'X-Agent-Demo':'1','Content-Type':'application/json'},body:'{}'}).then(()=>toast('服务已退出，可关闭页面。下次从桌面快捷方式启动。'))}
function csv(rows){return '\ufeff'+rows.map(r=>r.map(v=>'"'+String(v??'').replace(/^[=+@\-]/,"'$&").replace(/"/g,'""')+'"').join(',')).join('\r\n')}
function exportCSV(){const records=treeRecords();download('数量核对明细.csv',csv([['匹配情况','父级','数据模式','估算序号','清册序号','估算名称','清册名称','估算数量','单位','清册数量','单位','档位','依据','人工确认','复核意见'],...state.rows.map(r=>[rowCategory(r),rowTreePath(r,records).join(' > '),r.mode,r.source_id,r.target_id,r.name,r.target,r.quantity,r.unit,r.target_quantity,r.target_unit,r.band,r.reason,r.confirmed?'是':'否',r.human_note])]),'text/csv;charset=utf-8')}
function exportDrawing(){if(!state.drawing.length)return toast('请先载入提资数据');const conflict=state.drawing.find(r=>r.field==='接地装置套数')?.value!==state.drawing.find(r=>r.field==='杆塔总基数')?.value;const confirmed=state.drawing.every(r=>r.confirmed)&&!conflict;download('演示提资单_'+(confirmed?'人工确认版':'待复核草稿')+'.csv',csv([['字段','数值','单位','出处','已复核','数据性质'],...state.drawing.map(r=>[r.field,r.value,r.unit,r.source,r.confirmed?'是':'否','合成演示'])]),'text/csv;charset=utf-8')}
function uploadJSON(route){const input=$('jsonUpload');input.value='';input.onchange=async()=>{try{const f=input.files[0];if(!f)return;const items=JSON.parse(await f.text());await action(route,{items})}catch(e){toast('JSON 文件无效：'+e.message)}};input.click()}
function downloadPriceTemplate(){download('信息价导入格式示例.json',JSON.stringify([{code:'DEMO-001',name:'钢芯铝绞线',spec:'JL/G1A-300/40',unit:'t',price:21800,previous:21000,period:'2026-09',region:'云南·演示区域',tax:'除税',basis:'出厂',source:'演示信息价（请替换为真实文件名）',page:1,inferred:false}],null,2))}
refresh();setInterval(()=>refresh(),1500);


let matchingView='table', matchingExpanded=false;
function setMatchingView(value){matchingView=value;renderCanvas()}
function matchMaterial(r,target){
 const exists=target?r.target_id:r.source_id;
 if(!exists)return '<span class="match-missing">未匹配</span>';
 const name=target?r.target:r.name, spec=target?r.target_spec:r.spec;
 const qty=target?r.target_quantity:r.quantity, unit=target?r.target_unit:r.unit;
 return `<b>${esc(name)}</b><small>${esc(spec||'未注明规格')}</small><span>${esc(qty)} ${esc(unit)}</span><small>编号 ${esc(exists)}</small>`;
}
const rowKey=r=>JSON.stringify([String(r.source_id||''),String(r.target_id||'')]);
function rowLabel(r){const n=r.target_id?`清册${r.target_id} ${r.target}`:`造价${r.source_id} ${r.name}`;return n.length>26?n.slice(0,26)+'…':n}
// 匹配情况：先分出两侧未匹配，再按评审结论区分已匹配物资；成套项与组成件按多对多匹配，组成件随成套项结论归类
const CATEGORY_ORDER=['清册未匹配','造价未匹配','一致','基本一致','部分一致','不一致','待复核','评审未完成','不参与核对'];
const categoryColors={...colors,'清册未匹配':'#d9a35b','造价未匹配':'#a993c9'};
function rowCategory(r){
 if(r.band==='不参与核对')return '不参与核对';
 if(r.covered_by)return setAnchorRow(r)?(r.band||'待复核'):(r.target_id?'清册未匹配':'造价未匹配');
 // 单侧条目若被评审判为附件已含在对方某项物资中（结论为一致或基本一致等），按其结论归类，不算未匹配
 const missing=['不一致','待复核',''].includes(r.band||'');
 if(r.target_id&&!r.source_id&&missing)return '清册未匹配';
 if(r.source_id&&!r.target_id&&!isSetAnchor(r)&&missing)return '造价未匹配';
 return r.band||'待复核';
}
function categoryCounts(rows){const counts=Object.fromEntries(CATEGORY_ORDER.map(k=>[k,0]));for(const r of rows)counts[rowCategory(r)]=(counts[rowCategory(r)]||0)+1;return counts;}
function setResultFilter(value){filter=filter===value?'':value;page=0;matchingView='table';renderCanvas();}
function filteredRows(){return state.rows.filter(r=>(!filter||(filter===EDITED_FILTER?Boolean(r.edited):rowCategory(r)===filter))&&(!search||JSON.stringify(r).toLowerCase().includes(search.toLowerCase())))}
function toggleRowSelection(input){if(input.checked)selectedKeys.add(input.dataset.key);else selectedKeys.delete(input.dataset.key);renderCanvas()}
function togglePageSelection(checked){filteredRows().forEach(r=>checked?selectedKeys.add(rowKey(r)):selectedKeys.delete(rowKey(r)));renderCanvas()}
// Follow-up questions quote results into the single conversation; the server rebuilds evidence from these keys.
function addChatRef(ref){
 ref.id=ref.kind+':'+JSON.stringify(ref.keys||[ref.side,ref.ids]);
 if(!chatRefs.some(r=>r.id===ref.id))chatRefs.push(ref);
 renderChatRefs();document.querySelectorAll('dialog[open]').forEach(d=>d.close());
 if(matchingExpanded){matchingExpanded=false;renderCanvas()}
 if(matchMedia('(max-width:950px)').matches)togglePanel(false);
 $('chatInput').focus();toast('已引用到对话，输入问题后发送');
}
function renderChatRefs(){const box=$('chatRefs');box.hidden=!chatRefs.length;box.innerHTML=chatRefs.map((r,i)=>`<span class="ref-chip" title="${esc(r.label)}"><span>${esc(r.label)}</span><button type="button" aria-label="移除引用 ${esc(r.label)}" onclick="removeChatRef(${i})">×</button></span>`).join('');$('chatInput').placeholder=chatRefs.length?'针对已引用的匹配结果提问，例如：差异原因是什么？需要核对哪些？':'向造价助手发送消息，输入任务或修改要求…'}
function removeChatRef(i){chatRefs.splice(i,1);renderChatRefs()}
function askRows(rows,label){if(!rows.length)return toast('没有可引用的条目');addChatRef({kind:'rows',keys:rows.map(r=>JSON.parse(rowKey(r))),label})}
function askSelection(){const rows=state.rows.filter(r=>selectedKeys.has(rowKey(r)));askRows(rows,`所选 ${rows.length} 条`);selectedKeys.clear();renderCanvas()}
function askFiltered(){const rows=filteredRows();askRows(rows,`${filter||'全部结论'}${search?' · “'+search+'”':''} · ${rows.length} 条`)}
function askRow(id){const r=state.rows.find(x=>x.id===id);if(r)askRows([r],rowLabel(r))}
function askParent(parent){if(parent)addChatRef({kind:'group',side:parent.side,ids:parent.items.map(i=>i.id),label:`${parent.side} · ${parent.name} · ${parent.items.length} 项`})}
function matchingResultsHTML(){
 const results=filteredRows(),picked=state.rows.filter(r=>selectedKeys.has(rowKey(r)));
 const content=matchingView==='table'?resultTreeTableHTML(results):
 clusterGraphHTML();
 return `<div class="card table-card matching-results ${matchingExpanded?'match-expanded':''}"><div class="card-head"><h3>智能匹配结果</h3><div class="match-switch"><button class="button" aria-pressed="${matchingView==='table'}" onclick="setMatchingView('table')">表格</button><button class="button" aria-pressed="${matchingView==='graph'}" onclick="setMatchingView('graph')">可视化</button><button class="button" onclick="exportResults()">结果导出</button><button class="button" onclick="matchingExpanded=!matchingExpanded;renderCanvas()">${matchingExpanded?'退出全屏':'全屏查看'}</button></div></div><div class="table-controls"><input value="${esc(search)}" id="searchRows" placeholder="搜索物资、型号或来源" onchange="search=this.value;page=0;renderCanvas()"><button class="button" onclick="exportCSV()">↓ CSV</button><button class="button" onclick="askSelection()" ${picked.length?'':'disabled'} ${matchingView==='graph'?'hidden':''}>追问所选${picked.length?' ('+picked.length+')':''}</button><button class="button" onclick="askFiltered()" ${results.length?'':'disabled'}>追问当前筛选</button></div>${resultFiltersHTML()}${content}<div class="table-footer" ${matchingView==='graph'?'hidden':''}><span>共 ${results.length} 条${filter?' · '+esc(filter):''} · 按清册层级分组${results.some(r=>!r.target_id)?'，无清册对应的条目按造价层级列在最后':''}</span><div><button onclick="expandResultTree(true)">全部展开</button><button onclick="expandResultTree(false)">全部折叠</button></div></div></div>`;
}
// 对话中修改过的条目：筛选条单列一项，表格中标“已修改”，最近一次修改的行短暂高亮
const EDITED_FILTER='对话修改';let seenEdit='';
function editedChipHTML(){const n=state.rows.filter(r=>r.edited).length;return n?`<span class="result-chip-group"><em>修改</em><button type="button" class="result-chip" aria-pressed="${filter===EDITED_FILTER}" onclick="setResultFilter('${EDITED_FILTER}')"><i style="background:#5b7fb0"></i>${EDITED_FILTER}<b>${n}</b></button></span>`:''}
function editedBadge(r){if(!r.edited)return '';const last=(r.edits||[]).at(-1);return `<span class="badge edited" title="${esc((last?.id||r.edited)+' '+(last?.summary||'')+' '+(last?.time||''))}">已修改 ${esc(r.edited)}</span>`}
// 筛选条：两侧未匹配、已匹配各结论的条数，点击筛选，再点取消
function resultFiltersHTML(){
 const counts=categoryCounts(state.rows),chip=k=>counts[k]?`<button type="button" class="result-chip" aria-pressed="${filter===k}" onclick="setResultFilter('${k}')"><i style="background:${categoryColors[k]}"></i>${k}<b>${counts[k]}</b></button>`:'';
 const group=(label,keys)=>{const chips=keys.map(chip).join('');return chips?`<span class="result-chip-group"><em>${label}</em>${chips}</span>`:''};
 return `<div class="result-filters" role="group" aria-label="按匹配情况筛选"><button type="button" class="result-chip" aria-pressed="${!filter}" onclick="filter='';renderCanvas()">全部<b>${state.rows.length}</b></button>${group('未匹配',['清册未匹配','造价未匹配'])}${group('已匹配',['一致','基本一致','部分一致','不一致','待复核'])}${group('处理状态',['评审未完成'])}${editedChipHTML()}${group('其他',['不参与核对'])}</div>`;
}
// 层级树：清册物资按清册原表父级分组；没有清册对应的造价条目按造价父级列在最后。父级可逐级折叠
// 展开状态按结论筛选分别记住：首次进入某类默认折叠，切换回来保持人工展开的父级
let treeNodes=[];const treeStates=new Map();
function treeState(){if(!treeStates.has(filter))treeStates.set(filter,{open:new Map(),all:null});return treeStates.get(filter);}
// 成套对应组：主项行带 covers/covers_sources/set_pairs，组成件行带 covered_by，组内逐项配对行带 in_set
const isSetAnchor=r=>Boolean(r.covers?.length||r.covers_sources?.length||r.set_pairs?.length);
function setAnchorRow(r){
 const key=isSetAnchor(r)?String(r.source_id):String(r.covered_by||r.in_set||'');
 if(!key)return null;
 if(setAnchorRow.rev!==state.revision){setAnchorRow.rev=state.revision;setAnchorRow.map=new Map(state.rows.filter(isSetAnchor).map(x=>[String(x.source_id),x]));}
 return setAnchorRow.map.get(key)||null;
}
// 节点名带上主项数量，同名的成套（如 12 串与 6 串耐张绝缘子串）各成一个节点
const setName=anchor=>String((anchor.target_id?anchor.target:anchor.name)||'').split(/[：:　]/)[0].trim().slice(0,30)+(anchor.target_id?`（${anchor.target_quantity}${anchor.target_unit}）`:`（${anchor.quantity}${anchor.unit}）`);
function rowTreePath(r,records){
 const anchor=setAnchorRow(r),base=anchor||r;
 const side=base.target_id?'清册':'造价',record=records[side].get(String(base.target_id||base.source_id));
 const parents=record?.['层级路径']?.length?record['层级路径']:[record?.['层级']||'未记录父级'];
 const path=side==='清册'?parents:['造价侧条目（无对应清册物资）',...parents];
 // 同一成套对应组的两侧条目归在一个“成套”节点下，跨章节的组成件也集中显示
 return anchor?[...path,'成套 · '+setName(anchor)]:path;
}
function setMemberNote(r,side){
 const anchor=r.covered_by?setAnchorRow(r):null;
 if(!anchor)return '未匹配';
 return `成套对应「${esc(side==='造价'?String(anchor.name||'').split(/[：:　]/)[0]:anchor.target)}」<small class="set-many">多对多</small>`;
}
function treeRecords(){return {'清册':new Map((state.dataset?.targets||[]).map(x=>[String(x['序号']),x])),'造价':new Map((state.dataset?.sources||[]).map(x=>[String(x['序号']),x]))};}
function buildResultTree(rows){
 const records=treeRecords();
 const ordered=[...rows].sort((a,b)=>(!a.target_id-!b.target_id)||(Number(a.target_id||a.source_id)-Number(b.target_id||b.source_id)));
 const root={key:'',children:new Map(),rows:[],all:[]};
 for(const r of ordered){let node=root;for(const name of rowTreePath(r,records)){if(!node.children.has(name))node.children.set(name,{key:node.key+'\u0001'+name,name,children:new Map(),rows:[],all:[]});node=node.children.get(name);node.all.push(r);}node.rows.push(r);}
 return root;
}
function treeIsOpen(node){const t=treeState();return t.open.has(node.key)?t.open.get(node.key):t.all!==null?t.all:Boolean(search);}
function toggleTreeNode(i){const node=treeNodes[i];if(node){treeState().open.set(node.key,!treeIsOpen(node));renderCanvas();}}
function expandResultTree(open){const t=treeState();t.all=open;t.open.clear();renderCanvas();}
function toggleGroupSelection(i,checked){(treeNodes[i]?.all||[]).forEach(r=>checked?selectedKeys.add(rowKey(r)):selectedKeys.delete(rowKey(r)));renderCanvas();}
function groupSummary(rows){return Object.entries(categoryCounts(rows)).filter(([,n])=>n).map(([k,n])=>`<span class="tree-count"><i style="background:${categoryColors[k]}"></i>${k} ${n}</span>`).join('');}
// 树形连线：每一层一条竖向虚线贯穿其下级行，下级用横向虚线接出（中间项 ├，末项 └）。
// rails[i+1] 表示第 i 层的竖线在本行下方是否继续（即该层下还有后续兄弟项）
const TREE_STEP=22,TREE_RAIL=16;
function treeGuides(depth,rails,last,leaf,open){
 const x=i=>TREE_RAIL+i*TREE_STEP;let html='';
 for(let i=0;i<depth-1;i++)if(rails[i+1])html+=`<i class="tg-v" style="left:${x(i)}px"></i>`;
 if(depth>0)html+=`<i class="tg-v${last?' tg-end':''}" style="left:${x(depth-1)}px"></i><i class="tg-h" style="left:${x(depth-1)}px;width:${leaf?TREE_STEP+12:TREE_STEP-6}px"></i>`;
 if(open)html+=`<i class="tg-v tg-start" style="left:${x(depth)}px"></i>`;
 return `<span class="tree-guides" aria-hidden="true">${html}</span>`;
}
function treeLeafHTML(r,depth,rails=[],last=false){
 const category=rowCategory(r);
 return `<tr class="tree-leaf${r.edited?' row-edited':''}${r.edited&&r.edited===state.last_edit&&state.last_edit!==seenEdit?' row-flash':''}" style="--depth:${depth}"><td class="match-pick"><input type="checkbox" aria-label="选择 ${esc(rowLabel(r))}" data-key="${esc(rowKey(r))}" ${selectedKeys.has(rowKey(r))?'checked':''} onchange="toggleRowSelection(this)"></td><td class="tree-indent">${treeGuides(depth,rails,last,true,false)}<button class="match-open" onclick="openDetail(${Number(r.id)})">${r.target_id?esc(r.target):setMemberNote(r,'清册')}<small>${esc(r.target_spec)}</small></button>${r.target_id?`<button class="match-source button" onclick="openRowComparison(${Number(r.id)},true)">查看原表 ↗</button>`:''}</td><td>${esc(r.target_quantity)} ${esc(r.target_unit)}</td><td><button class="match-open" onclick="openDetail(${Number(r.id)})">${r.source_id?esc(r.name):setMemberNote(r,'造价')}<small>${esc(r.spec)}</small></button>${r.source_id?`<button class="match-source button" onclick="openRowComparison(${Number(r.id)},false)">查看原表 ↗</button>`:''}</td><td>${esc(r.quantity)} ${esc(r.unit)}</td><td>${category.endsWith('未匹配')?`<span class="badge unmatched">${category}</span>`:badge(r.band,r.confirmed)}${editedBadge(r)}</td><td class="match-reason">${esc(r.reason)}</td></tr>`;
}
function treeRowsHTML(node,depth,rails=[]){
 const children=[...node.children.values()];
 let html=node.rows.map((r,i)=>treeLeafHTML(r,depth,rails,i===node.rows.length-1&&!children.length)).join('');
 children.forEach((child,i)=>{
  const index=treeNodes.push(child)-1,open=treeIsOpen(child),last=i===children.length-1,all=child.all.length&&child.all.every(r=>selectedKeys.has(rowKey(r)));
  html+=`<tr class="tree-group" style="--depth:${depth}"><td class="match-pick"><input type="checkbox" aria-label="选择父级 ${esc(child.name)} 下全部条目" ${all?'checked':''} onchange="toggleGroupSelection(${index},this.checked)"></td><td colspan="6" class="tree-indent">${treeGuides(depth,rails,last,false,open)}<button type="button" class="tree-toggle" aria-expanded="${open}" aria-label="${open?'折叠':'展开'} ${esc(child.name)}" onclick="toggleTreeNode(${index})"><span class="tree-box" aria-hidden="true"></span><b>${esc(child.name)}</b><small>${child.all.length} 项</small></button>${groupSummary(child.all)}</td></tr>`;
  if(open)html+=treeRowsHTML(child,depth+1,[...rails,!last]);
 });
 return html;
}
function resultTreeTableHTML(results){
 treeNodes=[];const body=treeRowsHTML(buildResultTree(results),0);
 if(state.last_edit&&state.last_edit!==seenEdit){const id=state.last_edit;setTimeout(()=>{seenEdit=id},2600)}
 const all=results.length&&results.every(r=>selectedKeys.has(rowKey(r))),allOpen=treeNodes.length>0&&treeNodes.every(treeIsOpen);
 return `<div class="match-scroll"><table class="match-table tree-table"><thead><tr><th class="match-pick"><input type="checkbox" aria-label="选择当前筛选的全部条目" ${all?'checked':''} onchange="togglePageSelection(this.checked)"></th><th><span class="tree-head"><button type="button" class="tree-toggle tree-all" aria-expanded="${allOpen}" aria-label="${allOpen?'全部折叠':'全部展开'}" title="${allOpen?'全部折叠':'全部展开'}" onclick="expandResultTree(${!allOpen})" ${treeNodes.length?'':'disabled'}><span class="tree-box" aria-hidden="true"></span></button>清册物资（基准）</span></th><th>清册数量 / 单位</th><th>造价物资</th><th>造价数量 / 单位</th><th>评审结论</th><th>差异与依据</th></tr></thead><tbody>${body||'<tr><td colspan="7" class="empty">无符合条件的条目</td></tr>'}</tbody></table></div>`;
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&matchingExpanded){matchingExpanded=false;renderCanvas()}});

// Overview groups records by each record's immediate original parent (file · sheet · heading path).
// Dashed links are containment relations recognised by domain knowledge (set components, declared accessories).
let clusterDetails=[];
const clusterBands=['一致','基本一致','部分一致','不一致'];
const clusterBandColors={'一致':'#26845b','基本一致':'#368aca','部分一致':'#dc9a22','不一致':'#ce5353','待复核':'#94a3b8','评审未完成':'#b07a8c','含于成套项':'#5b8fb9'};
function clusterGraphHTML(){
 const nodes=new Map(),edges=[],relations=[],buckets=new Map(),seen=new Set();clusterDetails=[];
 for(const [side,items] of [['清册',state.dataset?.targets||[]],['造价',state.dataset?.sources||[]]])for(const record of items){
  const id=String(record['序号']),e=record._source||{},parents=record['层级路径']?.length?record['层级路径']:[record['层级']||'未记录父级'];
  const path=[e.file||'来源未记录',e.sheet||'工作表未记录',...parents];
  nodes.set(side+':'+id,{side,id,record,name:record['设备材料名称']||record.Estimation_Name||'未命名',path,rows:[],matched:false,linked:false,excluded:false});
 }
 for(const row of state.rows){
  const a=nodes.get('清册:'+row.target_id),b=nodes.get('造价:'+row.source_id);
  if(a)a.rows.push(row);if(b)b.rows.push(row);
  if(row.band==='不参与核对'){if(a)a.excluded=true;if(b)b.excluded=true;continue}
  const key=JSON.stringify([a?.id,b?.id]);
  if(a&&b&&!seen.has(key)){seen.add(key);edges.push([a,b,row]);a.matched=b.matched=true;continue}
  const rel=row.covered_by?{related:row.covered_by,type:'成套整体核对'}:(row.domain||[]).find(d=>d.related!==undefined&&d.related!==null&&d.related!=='');
  if(!rel)continue;
  const ta=a||nodes.get('清册:'+rel.related),sb=b||nodes.get('造价:'+rel.related);
  if(ta&&sb){relations.push([ta,sb,row,rel.type]);(a||b).linked=true}
 }
 for(const n of nodes.values()){
  const key=JSON.stringify([n.side,...n.path]);n.bucket=key;
  if(!buckets.has(key))buckets.set(key,{key,side:n.side,name:n.path.at(-1),path:n.path,items:[]});
  buckets.get(key).items.push(n);
 }
 const left=[...buckets.values()].filter(n=>n.side==='清册'),right=[...buckets.values()].filter(n=>n.side==='造价');
 const aggregate=new Map(),relationAggregate=new Map();
 for(const [list,target] of [[edges,aggregate],[relations,relationAggregate]])for(const [a,b,row,type] of list){const key=JSON.stringify([a.bucket,b.bucket]);if(!target.has(key))target.set(key,{count:0,bands:[],types:new Set()});const edge=target.get(key);edge.count++;edge.bands.push(row.band);if(type)edge.types.add(type);}
 // Place connected parents near each other to reduce crossings, retaining source order on ties.
 const leftOrder=new Map(left.map((n,i)=>[n.key,i]));
 for(const b of right){let total=0,weight=0;for(const [key,edge] of [...aggregate,...relationAggregate]){const count=edge.count;const [l,r]=JSON.parse(key);if(r===b.key){total+=leftOrder.get(l)*count;weight+=count;}}b.order=weight?total/weight:Infinity;}
 right.sort((a,b)=>a.order-b.order);
 let shapes='',links='';const height=Math.max(360,Math.max(left.length,right.length)*26+70);
 for(const [col,list] of [left,right].entries()){
  shapes+=`<text x="${30+col*790}" y="30" font-size="20" fill="#334155">${col?'造价物资':'清册物资（基准）'} · ${list.length} 个父级</text>`;
  for(const [j,bucket] of list.entries()){
   bucket.x=30+col*790;bucket.y=60+j*26;bucket.matched=bucket.items.filter(n=>n.matched).length;bucket.linked=bucket.items.filter(n=>!n.matched&&n.linked).length;bucket.excluded=bucket.items.filter(n=>!n.matched&&!n.linked&&n.excluded).length;bucket.unmatched=bucket.items.length-bucket.matched-bucket.linked-bucket.excluded;
   bucket.active=bucket.items.some(n=>(!filter||n.rows.some(r=>rowCategory(r)===filter))&&(!search||JSON.stringify(n.record).toLowerCase().includes(search.toLowerCase())));
   const index=clusterDetails.push(bucket)-1,color=bucket.unmatched?'#b7791f':'#427d6b',covered=bucket.matched+bucket.linked;
   const stats=`${bucket.items.length}项 · 已配${bucket.matched}`+(bucket.linked?` · 关联${bucket.linked}`:'')+(bucket.excluded?` · 不核对${bucket.excluded}`:'')+` · 未配${bucket.unmatched}`;
   shapes+=`<g class="cluster-node" data-node="${index}" tabindex="0" role="button" aria-label="${esc(bucket.side+' '+bucket.name+'，'+stats)}" opacity="${bucket.active?1:.15}"><rect x="${bucket.x}" y="${bucket.y-16}" width="570" height="23" rx="4" fill="white" stroke="${color}"/><text x="${bucket.x+14}" y="${bucket.y}" fill="#263b33" font-size="16">${esc(bucket.name.length>20?bucket.name.slice(0,20)+'…':bucket.name)}</text><text x="${bucket.x+300}" y="${bucket.y}" fill="#64746b" font-size="12">${esc(stats)}</text><rect x="${bucket.x+14}" y="${bucket.y+4}" width="542" height="2" rx="1" fill="#dfb56f"/><rect x="${bucket.x+14}" y="${bucket.y+4}" width="${542*covered/bucket.items.length}" height="2" rx="1" fill="#579b83"/></g>`;
  }
 }
 const worst=bands=>{const rank=Math.max(...bands.map(b=>clusterBands.indexOf(b)));return rank>=0?clusterBands[rank]:bands.includes('含于成套项')?'含于成套项':'待复核'};
 for(const [key,edge] of aggregate){const band=worst(edge.bands);const [a,b]=JSON.parse(key).map(k=>buckets.get(k));links+=`<path d="M${a.x+570},${a.y} C690,${a.y} 730,${b.y} ${b.x},${b.y}" fill="none" stroke="${clusterBandColors[band]||'#94a3b8'}" stroke-opacity="${a.active&&b.active ? .55 : .08}" stroke-width="3"><title>${esc(a.name)} ↔ ${esc(b.name)}：${edge.count} 对匹配 · ${band}（按差异最严重的子项汇总）</title></path>`;}
 for(const [key,edge] of relationAggregate){const band=worst(edge.bands);const [a,b]=JSON.parse(key).map(k=>buckets.get(k));links+=`<path d="M${a.x+570},${a.y} C690,${a.y} 730,${b.y} ${b.x},${b.y}" fill="none" stroke="${clusterBandColors[band]||'#94a3b8'}" stroke-opacity="${a.active&&b.active ? .7 : .08}" stroke-width="2" stroke-dasharray="7 5"><title>${esc(a.name)} ⊂ ${esc(b.name)}：${edge.count} 条包含关系（${esc([...edge.types].join('、'))}）· ${band}</title></path>`;}
 const box=`0 0 1420 ${height}`;
 return `<div class="cluster-toolbar"><span></span><button class="button" onclick="clusterZoom(.75)">＋ 放大</button><button class="button" onclick="clusterZoom(1.33)">－ 缩小</button><button class="button" onclick="clusterFit()">适应全图</button></div><div class="cluster-stage"><svg id="clusterCanvas" viewBox="${box}" data-fit="${box}" aria-label="直接父级匹配概览">${links}${shapes}</svg><div id="clusterTooltip" hidden></div><div class="cluster-legend">${clusterBands.map(b=>`<span><i style="background:${clusterBandColors[b]}"></i>${b}</span>`).join('')}<span><i style="background:repeating-linear-gradient(90deg,#64748b 0 6px,transparent 6px 10px)"></i>包含关系</span><small>父级结论取子项最严重差异</small></div></div>`;
}
function clusterFit(){const s=document.getElementById('clusterCanvas');if(s)s.setAttribute('viewBox',s.dataset.fit)}
function clusterZoom(factor,cx=.5,cy=.5){const s=document.getElementById('clusterCanvas');if(!s)return;const b=s.viewBox.baseVal,fit=s.dataset.fit.split(' ').map(Number);const w=Math.max(fit[2]/40,Math.min(fit[2]*2,b.width*factor)),h=b.height*w/b.width;s.setAttribute('viewBox',[b.x+(b.width-w)*cx,b.y+(b.height-h)*cy,w,h].join(' '))}
function clusterShow(node){const n=clusterDetails[Number(node.dataset.node)],tip=document.getElementById('clusterTooltip');if(!n||!tip)return;tip.hidden=false;tip.textContent=n.side+' · '+n.name+'\n来源：'+n.path.join(' → ')+'\n共 '+n.items.length+' 项 · 已匹配 '+n.matched+' · 未匹配 '+n.unmatched+'\n点击查看本父级下的物资及原表';}
function clusterActivate(index){const n=clusterDetails[index];if(n)openParentComparison(n);}
function clusterOpenSource(index,item){const source=clusterDetails[index]?.items[item]?.record._source;if(source)openMaterialSource(source);}
document.addEventListener('mouseover',e=>{const n=e.target.closest?.('.cluster-node');if(n)clusterShow(n);else if(!e.target.closest?.('#clusterTooltip')){const t=document.getElementById('clusterTooltip');if(t)t.hidden=true}});
document.addEventListener('focusin',e=>{if(e.target.matches?.('.cluster-node'))clusterShow(e.target)});
document.addEventListener('click',e=>{const n=e.target.closest?.('.cluster-node');if(n&&!clusterDragged){clusterActivate(Number(n.dataset.node))}});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches?.('.cluster-node')){clusterActivate(Number(e.target.dataset.node))}});
document.addEventListener('wheel',e=>{const s=e.target.closest?.('#clusterCanvas');if(!s)return;e.preventDefault();const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(s.getScreenCTM().inverse()),b=s.viewBox.baseVal;clusterZoom(e.deltaY>0?1.12:.89,(p.x-b.x)/b.width,(p.y-b.y)/b.height)},{passive:false});
let clusterDrag=null,clusterDragged=false;
document.addEventListener('pointerdown',e=>{const s=e.target.closest?.('#clusterCanvas');if(!s||e.button!==0)return;clusterDragged=false;clusterDrag={s,x:e.clientX,y:e.clientY,box:s.getAttribute('viewBox').split(' ').map(Number),scale:s.getScreenCTM().a}});
document.addEventListener('pointermove',e=>{if(!clusterDrag)return;const d=clusterDrag,dx=(e.clientX-d.x)/d.scale,dy=(e.clientY-d.y)/d.scale;if(Math.abs(e.clientX-d.x)+Math.abs(e.clientY-d.y)>4)clusterDragged=true;d.s.setAttribute('viewBox',[d.box[0]-dx,d.box[1]-dy,d.box[2],d.box[3]].join(' '))});
document.addEventListener('pointerup',()=>{clusterDrag=null});document.addEventListener('pointercancel',()=>{clusterDrag=null});
