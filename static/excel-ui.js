let excelView=null,excelRequest=0;
function excelCards(books){return `<div class="excel-cards">${books.map(b=>`<button type="button" class="excel-card" data-excel="${esc(b.id)}"><span class="excel-symbol">X</span><span><strong>${esc(b.name)}</strong><small>${b.kind==='original'?'原始 Excel 快照':b.kind==='cleaned'?'清洗后 Excel':'编辑副本'}</small></span><span>↗</span></button>`).join('')}</div>`;}
function executionExcelHTML(g){
 if(g.record?.excel_books?.length)return excelCards(g.record.excel_books);
 // Historic turns lack immutable files: label the current dataset explicitly.
 const last=state.messages.findLastIndex(m=>m.text?.startsWith('数据技能已完成'));
 if(last>=0&&g.messages.includes(state.messages[last]))return `<p class="excel-history-note">本条旧记录未保存当时的 Excel 快照。可查看当前工程的数据快照。</p>${state.dataset?.excel_books?.length?excelCards(state.dataset.excel_books):'<button class="button" type="button" data-excel-prepare>查看当前原表和清洗后 Excel</button>'}`;
 return '';
}
async function excelApi(op,data={}){
 const response=await fetch('/api/excel/'+op,{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Demo':'1'},body:JSON.stringify(data)});if(response.status===401){location.href='/login';throw Error('登录已失效，请重新登录');}
 const result=await response.json();if(!response.ok)throw Error(result.error||'Excel操作失败');return result;
}
function ensureExcelDialog(){
 if($('excelDialog'))return;
 const dialog=document.createElement('dialog');dialog.id='excelDialog';dialog.className='excel-dialog';
 dialog.innerHTML='<div class="dialog-head"><h2 id="excelTitle">Excel工作簿</h2><button type="button" aria-label="关闭表格" onclick="document.getElementById(\'excelDialog\').close()">×</button></div><div id="excelBody"></div>';
 document.body.append(dialog);
}
async function openExcel(id,sheet=null,row=1,column=1){
 ensureExcelDialog();if(!$('excelDialog').open)$('excelDialog').showModal();
 const sequence=++excelRequest;$('excelBody').textContent='正在读取工作表…';
 try{const data=await excelApi('view',{id,sheet,row,column});if(sequence!==excelRequest)return;excelView=data;renderExcel();}
 catch(e){if(sequence===excelRequest)$('excelBody').textContent=e.message;}
}
function excelColumn(n){let result='';for(;n>0;n=Math.floor((n-1)/26))result=String.fromCharCode(65+(n-1)%26)+result;return result;}
function renderExcel(){
 const d=excelView;$('excelTitle').textContent=d.book.name;
 $('excelBody').innerHTML=`<div class="excel-toolbar"><label>工作表 <select id="excelSheet">${d.sheets.map(s=>`<option value="${esc(s.name)}" ${s.name===d.sheet?'selected':''}>${esc(s.name)}（${s.rows}行 × ${s.columns}列）</option>`).join('')}</select></label><a class="button" href="/api/artifacts/${encodeURIComponent(d.book.id)}" download="${esc(d.book.name)}">下载 Excel</a><button class="button" id="excelCopy">另存XLSX副本</button></div><p class="excel-note">${esc(d.note)}${d.book.note?' '+esc(d.book.note):''}</p><div class="excel-pagination"><button class="button" id="excelPrev" ${d.row<=1?'disabled':''}>上一页</button><button class="button" id="excelNext" ${d.row+d.page_size>d.rows?'disabled':''}>下一页</button><label>定位行 <input id="excelRow" type="number" min="1" max="${Math.max(1,d.rows)}" value="${d.row}"></label><button class="button" id="excelGo">跳转</button><button class="button" id="excelLeft" ${d.column<=1?'disabled':''}>前40列</button><button class="button" id="excelRight" ${d.column+d.column_size>d.columns?'disabled':''}>后40列</button><span>共 ${d.rows} 行 / ${d.columns} 列</span></div><div class="excel-grid" tabindex="0"><table><thead><tr><th>行号</th>${(d.cells[0]||[]).map((_,c)=>`<th>${excelColumn(d.column+c)}</th>`).join('')}</tr></thead><tbody>${d.cells.map((cells,r)=>`<tr><th>${d.row+r}</th>${cells.map((cell,c)=>`<td tabindex="0" data-cell="${excelColumn(d.column+c)}${d.row+r}"><div>${esc(cell.value)}</div>${cell.formula?`<small class="excel-formula">${esc(cell.formula)}${cell.value===''?'（无缓存值）':''}</small>`:''}</td>`).join('')}</tr>`).join('')}</tbody></table></div><form id="excelEdit" class="excel-editor"><label>单元格 <input id="excelCell" placeholder="例如 B12" required></label><label>类型 <select id="excelType"><option value="text">文本</option><option value="number">数值</option><option value="blank">清空</option></select></label><label class="excel-value">新内容 <textarea id="excelValue" rows="2"></textarea></label><button class="primary">修改并另存副本</button></form><p class="excel-note">点击单元格可编辑。修改保存到新文件，原表和清洗基准保留；编辑副本不自动替换物资核对数据。合并区域请编辑左上角。XLS文件请先转换副本。</p><p id="excelStatus" role="status"></p>`;
 $('excelSheet').onchange=()=>openExcel(d.book.id,$('excelSheet').value);
 $('excelPrev').onclick=()=>openExcel(d.book.id,d.sheet,Math.max(1,d.row-d.page_size),d.column);
 $('excelNext').onclick=()=>openExcel(d.book.id,d.sheet,d.row+d.page_size,d.column);
 $('excelGo').onclick=()=>openExcel(d.book.id,d.sheet,Math.max(1,Math.min(d.rows,Number($('excelRow').value)||1)),d.column);
 $('excelLeft').onclick=()=>openExcel(d.book.id,d.sheet,d.row,Math.max(1,d.column-d.column_size));
 $('excelRight').onclick=()=>openExcel(d.book.id,d.sheet,d.row,d.column+d.column_size);
 document.querySelectorAll('#excelBody [data-cell]').forEach(cell=>{const select=()=>{$('excelCell').value=cell.dataset.cell;$('excelValue').value=cell.querySelector('div').textContent;$('excelType').value='text';};cell.onclick=select;cell.onkeydown=e=>{if(e.key==='Enter')select();};});
 $('excelCopy').onclick=()=>saveExcelCopy('copy',{id:d.book.id});
 $('excelEdit').onsubmit=e=>{e.preventDefault();saveExcelCopy('edit',{id:d.book.id,sheet:d.sheet,cell:$('excelCell').value,value:$('excelValue').value,type:$('excelType').value});};
}
async function saveExcelCopy(op,data){
 const body=$('excelBody');body.querySelectorAll('button').forEach(b=>b.disabled=true);
 try{const result=await excelApi(op,data);await refresh(true);await openExcel(result.book.id,excelView.sheet,excelView.row,excelView.column);$('excelStatus').textContent='副本已保存：'+result.book.name;}
 catch(e){renderExcel();$('excelStatus').textContent=e.message;}
}
document.addEventListener('click',async e=>{
 const card=e.target.closest('[data-excel]');if(card){openExcel(card.dataset.excel);return;}
 const prepare=e.target.closest('[data-excel-prepare]');if(prepare){prepare.disabled=true;try{const result=await excelApi('prepare');prepare.outerHTML=excelCards(result.books);await refresh(true);}catch(error){toast(error.message);prepare.disabled=false;}}
});
const excelDataHTML=dataHTML;
dataHTML=function(){return excelDataHTML()+resultDownloadsHTML()+`<div class="card"><h3>原始与清洗后 Excel</h3>${state.dataset?.excel_books?.length?excelCards(state.dataset.excel_books):state.dataset?'<button class="button" data-excel-prepare>生成并查看表格快照</button>':'<p>请先清洗工程资料。</p>'}</div>`;};
// 本次评审的成果下载：成果报告步骤生成的 Word 成果报告，以及由本工程原表另存的 Excel 副本
function resultDownloadsHTML(){
 const books=new Set((state.dataset?.excel_books||[]).map(b=>b.id));
 const files=[...(state.material_artifacts||[]),...(state.artifacts||[]).filter(a=>a.kind==='edited'&&books.has(a.parent))];
 const items=files.map(a=>`<p><a class="button" href="/api/artifacts/${encodeURIComponent(a.id)}" download="${esc(a.name)}">↓ ${esc(a.name)}</a></p>`).join('');
 return `<div class="card"><h3>成果下载</h3>${items||'<p class="hint">完成“成果报告”步骤后，在这里下载 Word 成果报告。</p>'}</div>`;
}

async function openRowSource(id,target){const r=state.rows.find(r=>r.id===id);if(r)await openMaterialSource(target?r.evidence:r.source);}
async function openMaterialSource(source){
 if(!source?.file||!source?.sheet||!Number(source.row))return toast('该物资未保留完整的原表位置，无法精确定位。');
 try{
  let books=state.dataset?.excel_books||[];
  if(!books.length){const result=await excelApi('prepare');books=result.books;state.dataset.excel_books=books;}
  const normalize=p=>String(p||'').replaceAll('\\','/');
  const book=books.find(b=>b.kind==='original'&&normalize(b.source)===normalize(source.file));
  if(!book)return toast('没有找到此物资对应的原始工作簿快照。');
  await openExcel(book.id,source.sheet,Number(source.row),1);
  if(excelView?.book.id===book.id&&excelView.sheet===source.sheet){
   const line=document.querySelector('#excelBody tbody tr');if(line)line.classList.add('excel-source-row');
   const note=document.createElement('p');note.className='excel-note';note.textContent='已定位原表第 '+source.row+' 行（高亮）。当前显示原始文件快照。';$('excelBody').prepend(note);
  }
 }catch(e){toast(e.message)}
}
let parentComparison=[],parentComparisonRequest=0,parentComparisonNode=null,comparisonIndex=0,comparisonHits=[],comparisonActive=-1,comparisonMenu=null;
// 原表对照：整表缓存（按工作簿和工作表），每侧各自保留筛选与排序，切换同一工作表内的物资时沿用
const comparisonSheets=new Map(),comparisonViews={};
// 结果表“查看原表”：打开该物资所在父级的两侧原表对照，并定位到这一对
async function openRowComparison(id,target){
 const row=state.rows.find(r=>r.id===id);if(!row)return;
 clusterGraphHTML();  // 生成父级分组（与可视化一致）
 const side=target?'清册':'造价',field=target?'target_id':'source_id';
 const parent=clusterDetails.find(b=>b.side===side&&b.items.some(n=>String(n.id)===String(row[field])));
 if(!parent)return openRowSource(id,target);
 await openParentComparison(parent,id);
}
async function openParentComparison(parent,rowId=null){
 parentComparison=[];parentComparisonNode=parent;const seen=new Set();
 for(const item of parent.items){
  const rows=item.rows.length?item.rows:[null];
  for(const row of rows){const key=row?String(row.id):item.side+item.id;if(seen.has(key))continue;seen.add(key);
   const band=row?(row.band||'待复核'):'未匹配';
   // 搜索范围含规格及另一侧配对物资的名称、规格，结果中第二行显示配对物资
   const named=v=>v&&!/^\s*—/.test(v)?v:'',other=row?named(item.side==='清册'?row.name:row.target):'',search=[item.name,band,named(row?.name),named(row?.target),row?.spec,row?.target_spec,item.record?.['型号及规范']].filter(Boolean).join(' ');
   parentComparison.push({rowId:row?.id,band,label:item.name+' · '+band,other,otherSide:item.side==='清册'?'造价':'清册',search,target:row?.evidence||(item.side==='清册'?item.record._source:null),source:row?.source||(item.side==='造价'?item.record._source:null)});
  }
 }
 let dialog=$('parentComparisonDialog');
 if(!dialog){dialog=document.createElement('dialog');dialog.id='parentComparisonDialog';dialog.className='parent-comparison';document.body.append(dialog);
  dialog.addEventListener('click',comparisonClick);dialog.addEventListener('cancel',comparisonCancel);
  dialog.addEventListener('keydown',e=>{if(e.key==='Escape'&&(comparisonMenu||!$('comparisonResults')?.hidden)){e.stopPropagation();comparisonCancel(e);}},true);}
 closeColumnMenu();
 dialog.innerHTML=`<div class="dialog-head"><h2>${esc(parent.side+' · '+parent.name)} · 原表对照</h2><div class="dialog-head-actions"><button class="button ask-button" onclick="askParent(parentComparisonNode)">追问此父级</button><button aria-label="关闭原表对照" onclick="document.getElementById('parentComparisonDialog').close()">×</button></div></div>
 <div class="comparison-toolbar"><div class="comparison-search"><label for="comparisonSearch">下一级物资</label><div class="comparison-combo"><input id="comparisonSearch" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="comparisonResults" autocomplete="off" spellcheck="false" placeholder="模糊搜索名称、规格或结论，空格分隔多个词"><ul id="comparisonResults" role="listbox" aria-label="下一级物资" hidden></ul></div><span class="comparison-count" id="comparisonCount">${parentComparison.length} 项</span></div>
 <div class="comparison-legend" title="两侧显示原始文件快照的单元格内容；选中行居中高亮，可独立滚动查看上下文。图表、图片和原表样式暂不还原。">行着色：${[...clusterBands,'待复核','未匹配'].map(b=>`<span><i style="background:${comparisonTint(b)};box-shadow:inset 3px 0 ${comparisonColor(b)}"></i>${b}</span>`).join('')}</div></div>
 <div class="comparison-panes"><section><h3>清册原表（基准）</h3><div id="comparisonTarget"></div></section><section><h3>造价原表</h3><div id="comparisonSource"></div></section></div>`;
 const input=$('comparisonSearch');
 input.addEventListener('focus',()=>{input.dataset.typed='0';input.select();showComparisonResults();});
 input.addEventListener('input',()=>{input.dataset.typed='1';showComparisonResults();});
 input.addEventListener('keydown',comparisonSearchKey);
 input.addEventListener('blur',()=>setTimeout(hideComparisonResults,120));
 $('comparisonResults').addEventListener('mousedown',e=>{const li=e.target.closest('li[data-index]');e.preventDefault();if(li)chooseComparison(Number(li.dataset.index));});
 const start=Math.max(0,parentComparison.findIndex(p=>rowId!==null&&p.rowId===rowId));
 if(!dialog.open)dialog.showModal();
 dialog.querySelector('.dialog-head-actions .button').focus();
 await renderParentComparison(start);
}
const comparisonColor=band=>clusterBandColors[band]||'#94a3b8';
const comparisonTint=band=>`color-mix(in srgb,${comparisonColor(band)} 22%,white)`;
const comparisonRank=band=>{const i=['不一致','部分一致','未匹配','待复核','基本一致','一致'].indexOf(band);return i<0?99:i;};
// 模糊搜索：空格分隔的每个词都须出现在名称中（连续出现优先，其次按顺序出现的字），按匹配程度排序
function comparisonMatches(query){
 const tokens=query.toLowerCase().split(/\s+/).filter(Boolean);
 if(!tokens.length)return parentComparison.map((_,i)=>i);
 const scored=[];
 parentComparison.forEach((p,i)=>{
  const text=p.search.toLowerCase().replace(/\s+/g,'');let score=0;
  for(const token of tokens){
   const at=text.indexOf(token);if(at>=0){score+=3-Math.min(2,at/20);continue;}
   let k=0;for(const c of text)if(k<token.length&&c===token[k])k++;
   if(k<token.length)return;score+=1;
  }
  scored.push({i,score});
 });
 return scored.sort((a,b)=>b.score-a.score||a.i-b.i).map(s=>s.i);
}
function markTokens(text,query){
 const tokens=query.toLowerCase().split(/\s+/).filter(Boolean);if(!tokens.length)return esc(text);
 const lower=text.toLowerCase(),hit=new Array(text.length).fill(false);
 for(const t of tokens)for(let at=lower.indexOf(t);at>=0;at=lower.indexOf(t,at+t.length))for(let k=at;k<at+t.length;k++)hit[k]=true;
 let html='',open=false;
 for(let k=0;k<text.length;k++){if(hit[k]!==open){html+=hit[k]?'<mark>':'</mark>';open=hit[k];}html+=esc(text[k]);}
 return html+(open?'</mark>':'');
}
function showComparisonResults(){
 const input=$('comparisonSearch'),list=$('comparisonResults');if(!input||!list)return;
 const query=input.dataset.typed==='1'?input.value.trim():'';
 comparisonHits=comparisonMatches(query);
 comparisonActive=!query&&comparisonHits.includes(comparisonIndex)?comparisonIndex:comparisonHits[0]??-1;
 list.innerHTML=comparisonHits.length?comparisonHits.map(i=>{const p=parentComparison[i];return `<li role="option" id="comparisonOption${i}" data-index="${i}" aria-selected="${i===comparisonActive}" class="${i===comparisonIndex?'current':''}" style="--band:${comparisonColor(p.band)}">${markTokens(p.label,query)}${p.other?`<small>${p.otherSide}：${markTokens(p.other,query)}</small>`:''}</li>`;}).join(''):'<li class="empty">没有匹配的物资</li>';
 list.hidden=false;input.setAttribute('aria-expanded','true');
 if(comparisonActive>=0)input.setAttribute('aria-activedescendant','comparisonOption'+comparisonActive);
 $('comparisonCount').textContent=(query?comparisonHits.length+' / ':'')+parentComparison.length+' 项';
 list.querySelector('[aria-selected=true]')?.scrollIntoView({block:'nearest'});
}
function hideComparisonResults(){
 const input=$('comparisonSearch'),list=$('comparisonResults');if(!input||!list)return;
 list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');
 if(document.activeElement!==input)setComparisonInput();
 $('comparisonCount').textContent=parentComparison.length+' 项';
}
function setComparisonInput(){
 const input=$('comparisonSearch'),pair=parentComparison[comparisonIndex];if(!input||!pair)return;
 input.value=pair.label;input.dataset.typed='0';input.style.setProperty('--band',comparisonColor(pair.band));
}
function comparisonSearchKey(e){
 const list=$('comparisonResults');
 if(e.key==='ArrowDown'||e.key==='ArrowUp'){
  e.preventDefault();if(list.hidden)return showComparisonResults();if(!comparisonHits.length)return;
  const at=Math.max(0,comparisonHits.indexOf(comparisonActive)),next=(at+(e.key==='ArrowDown'?1:-1)+comparisonHits.length)%comparisonHits.length;
  comparisonActive=comparisonHits[next];
  list.querySelectorAll('li[data-index]').forEach(li=>li.setAttribute('aria-selected',Number(li.dataset.index)===comparisonActive));
  $('comparisonSearch').setAttribute('aria-activedescendant','comparisonOption'+comparisonActive);
  list.querySelector('[aria-selected=true]')?.scrollIntoView({block:'nearest'});
 }else if(e.key==='Enter'){e.preventDefault();if(comparisonActive>=0)chooseComparison(comparisonActive);}
}
function chooseComparison(index){
 comparisonIndex=index;hideComparisonResults();setComparisonInput();$('comparisonSearch').select();renderParentComparison(index);
}
function comparisonCancel(e){
 // Esc 先收起列菜单或搜索结果，再次按下才关闭对话框
 if(comparisonMenu){e.preventDefault();closeColumnMenu();return;}
 if($('comparisonResults')&&!$('comparisonResults').hidden){e.preventDefault();hideComparisonResults();$('comparisonSearch').blur();}
}
// 同一原表行可能对应多个下级物资：取差异最严重的结论着色
function comparisonBands(side,file,sheet){
 const normalize=p=>String(p||'').replaceAll('\\','/'),bands=new Map();
 for(const p of parentComparison){const at=p[side];if(!at||normalize(at.file)!==normalize(file)||at.sheet!==sheet)continue;
  const row=Number(at.row),old=bands.get(row);if(!old||comparisonRank(p.band)<comparisonRank(old.band))bands.set(row,p);}
 return bands;
}
async function renderParentComparison(index){
 const pair=parentComparison[index],sequence=++parentComparisonRequest;if(!pair)return;
 comparisonIndex=index;setComparisonInput();closeColumnMenu();
 drawComparisonLinks();
 try{
  let books=state.dataset?.excel_books||[];
  if(!books.length){books=(await excelApi('prepare')).books;state.dataset.excel_books=books;}
  if(sequence!==parentComparisonRequest)return;
  await Promise.all([['comparisonTarget',pair.target,'target'],['comparisonSource',pair.source,'source']].map(async([id,source,side])=>{
   const pane=$(id),empty=text=>{pane.innerHTML=`<p class="comparison-empty">${esc(text)}</p>`;delete pane.dataset.file;delete comparisonViews[side];};
   if(!source?.file||!source?.sheet||!Number(source.row))return empty(source?'缺少原表位置，无法定位。':'未找到对应物资。');
   const normalize=p=>String(p||'').replaceAll('\\','/');const book=books.find(b=>b.kind==='original'&&normalize(b.source)===normalize(source.file));
   if(!book)return empty('未找到原始工作簿快照。');
   const key=book.id+'|'+source.sheet;
   if(!comparisonSheets.has(key)){if(comparisonViews[side]?.key!==key)pane.innerHTML='<p class="comparison-empty">正在读取原表…</p>';comparisonSheets.set(key,excelApi('sheet',{id:book.id,sheet:source.sheet}));}
   let data;
   try{data=await comparisonSheets.get(key);}catch(e){comparisonSheets.delete(key);if(sequence===parentComparisonRequest)empty(e.message);return;}
   if(sequence!==parentComparisonRequest)return;
   if(comparisonViews[side]?.key!==key)comparisonViews[side]={key,filters:{},sort:null};
   Object.assign(comparisonViews[side],{side,data,source,pane:id});
   renderComparisonPane(side,true);
  }));
  if(sequence===parentComparisonRequest){drawComparisonLinks();requestAnimationFrame(()=>requestAnimationFrame(drawComparisonLinks));}
 }catch(e){if(sequence===parentComparisonRequest)for(const id of ['comparisonTarget','comparisonSource'])$(id).textContent=e.message;}
}
// 像 Excel 自动筛选一样：每列可按取值勾选筛选、升序/降序排序；行号始终是原表行号
const comparisonText=v=>v===''||v===null||v===undefined?'':String(v);
function comparisonRows(view,skip=-1){
 let rows=view.data.cells.map((cells,i)=>({n:i+1,cells}));
 for(const [col,values] of Object.entries(view.filters))if(Number(col)!==skip)rows=rows.filter(r=>values.has(comparisonText(r.cells[col])));
 if(!view.sort||skip>=0)return rows;
 const {col,dir}=view.sort,number=v=>typeof v==='number'?v:/^\s*-?\d+(\.\d+)?\s*$/.test(String(v))?Number(v):null;
 return rows.map((r,i)=>({r,i})).sort((a,b)=>{
  const x=a.r.cells[col],y=b.r.cells[col],ex=comparisonText(x)==='',ey=comparisonText(y)==='';
  if(ex||ey)return ex-ey||a.i-b.i;  // 空白单元格总在最后
  const nx=number(x),ny=number(y),c=nx!==null&&ny!==null?nx-ny:nx!==null?-1:ny!==null?1:String(x).localeCompare(String(y),'zh-CN',{numeric:true});  // 与 Excel 相同，升序时数字在文本之前
  return (dir==='desc'?-c:c)||a.i-b.i;
 }).map(x=>x.r);
}
function renderComparisonPane(side,center=false){
 const view=comparisonViews[side];if(!view)return;
 const pane=$(view.pane),{data,source}=view,current=Number(source.row),old=pane.querySelector('.comparison-grid');
 const scroll=old&&!center?[old.scrollTop,old.scrollLeft]:null;
 const rows=comparisonRows(view),bands=comparisonBands(side,source.file,source.sheet),active=Object.keys(view.filters).length||view.sort;
 const hidden=!rows.some(r=>r.n===current);
 const bandAttr=n=>{const p=bands.get(n);return p?` style="--band:${comparisonColor(p.band)}" title="${esc(p.label)}"`:'';};
 const head=Array.from({length:data.columns},(_,c)=>{const filtered=view.filters[c],dir=view.sort?.col===c?view.sort.dir:'';
  return `<th><button type="button" class="column-menu-button${filtered||dir?' active':''}" data-side="${side}" data-col="${c}" aria-haspopup="dialog" aria-label="${excelColumn(c+1)} 列筛选与排序${filtered?'（已筛选）':''}${dir?'（已排序）':''}">${excelColumn(c+1)}<span class="column-menu-icon" aria-hidden="true">${dir?comparisonIcon(dir):''}${comparisonIcon(filtered?'filter':'menu')}</span></button></th>`;}).join('');
 pane.dataset.file=source.file;pane.dataset.sheet=data.sheet;pane.dataset.side=side;
 pane.innerHTML=`<div class="comparison-pane-bar"><span class="comparison-location" title="${esc(source.file+' · '+data.sheet)}">${esc(String(source.file).split(/[\\/]/).pop())} · ${esc(data.sheet)} · 第 ${current} 行</span><span class="comparison-shown${hidden?' warn':''}">${hidden?'当前物资所在行已被筛选隐藏 · ':''}${rows.length===data.cells.length?`共 ${data.cells.length} 行`:`显示 ${rows.length} / ${data.cells.length} 行`}</span><button type="button" class="button" data-act="locate" data-side="${side}"${hidden?' disabled':''}>定位当前行</button>${active?`<button type="button" class="button" data-act="reset" data-side="${side}">清除筛选和排序</button>`:''}</div>${data.truncated?'<p class="excel-note">表格过大，仅载入前 20000 行、80 列。</p>':''}<div class="excel-grid comparison-grid"><table><thead><tr><th>行号</th>${head}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr data-row="${r.n}" class="${r.n===current?'excel-source-row':''}${bands.has(r.n)?' comparison-band-row':''}"${r.n===current?' aria-selected="true"':''}${bandAttr(r.n)}><th>${r.n}</th>${r.cells.map(v=>`<td>${esc(comparisonText(v))}</td>`).join('')}</tr>`).join(''):`<tr><td class="comparison-no-rows" colspan="${data.columns+1}">没有符合筛选条件的行</td></tr>`}</tbody></table></div>`;
 const grid=pane.querySelector('.comparison-grid');
 grid.addEventListener('scroll',scheduleComparisonLinks,{passive:true});
 if(scroll){[grid.scrollTop,grid.scrollLeft]=scroll;}else requestAnimationFrame(()=>centerComparisonRow(side));
 scheduleComparisonLinks();
}
// 列标题右侧的筛选按钮图标：下拉箭头；已筛选为漏斗，已排序加方向箭头
function comparisonIcon(kind){
 const d={menu:'M6 9l6 6 6-6',filter:'M4 5h16l-6 7.5V18l-4 2v-7.5z',asc:'M12 19V5M6 11l6-6 6 6',desc:'M12 5v14M6 13l6 6 6-6'}[kind];
 return `<svg viewBox="0 0 24 24" class="${kind}"><path d="${d}"/></svg>`;
}
function centerComparisonRow(side){
 const view=comparisonViews[side];if(!view)return;
 const grid=$(view.pane).querySelector('.comparison-grid'),line=grid?.querySelector('.excel-source-row');if(!grid||!line)return;
 grid.scrollTop=line.offsetTop-grid.clientHeight/2+line.clientHeight/2;
 const cell=[...line.querySelectorAll('td')].find(c=>c.textContent.trim().length>3);
 grid.scrollLeft=cell?Math.max(0,cell.offsetLeft-grid.clientWidth/2+cell.clientWidth/2):0;
 scheduleComparisonLinks();
}
function comparisonClick(e){
 const menuButton=e.target.closest('.column-menu-button'),act=e.target.closest('[data-act]');
 if(menuButton){
  const same=comparisonMenu&&comparisonMenu.side===menuButton.dataset.side&&comparisonMenu.col===Number(menuButton.dataset.col);
  closeColumnMenu();if(!same)openColumnMenu(menuButton.dataset.side,Number(menuButton.dataset.col),menuButton);return;
 }
 if(comparisonMenu&&!e.target.closest('.column-menu'))closeColumnMenu();
 if(act?.dataset.act==='locate')centerComparisonRow(act.dataset.side);
 if(act?.dataset.act==='reset'){const view=comparisonViews[act.dataset.side];view.filters={};view.sort=null;renderComparisonPane(view.side,true);}
}
function openColumnMenu(side,col,button){
 const view=comparisonViews[side],counts=new Map();if(!view)return;
 for(const r of comparisonRows(view,col)){const t=comparisonText(r.cells[col]);counts.set(t,(counts.get(t)||0)+1);}
 for(const t of view.filters[col]||[])if(!counts.has(t))counts.set(t,0);
 const values=[...counts.keys()].sort((a,b)=>a===''?1:b===''?-1:a.localeCompare(b,'zh-CN',{numeric:true}));
 comparisonMenu={side,col,values,counts,checked:new Set(view.filters[col]||values)};
 const dialog=$('parentComparisonDialog'),menu=document.createElement('div'),name=excelColumn(col+1);
 menu.className='column-menu';menu.setAttribute('role','dialog');menu.setAttribute('aria-label',name+' 列筛选与排序');
 menu.innerHTML=`<button type="button" data-sort="asc">↑ 升序排序</button><button type="button" data-sort="desc">↓ 降序排序</button>${view.sort?.col===col?'<button type="button" data-sort="">取消排序</button>':''}<hr><input class="column-menu-search" type="search" placeholder="搜索 ${name} 列内容" aria-label="搜索 ${name} 列内容"><label class="column-menu-all"><input type="checkbox" data-all> （全选）</label><div class="column-menu-values" role="group" aria-label="${name} 列的取值"></div><div class="column-menu-actions"><button type="button" class="button" data-clear${view.filters[col]?'':' disabled'}>清除筛选</button><button type="button" class="primary" data-apply>确定</button></div>`;
 dialog.append(menu);comparisonMenu.element=menu;
 const box=dialog.getBoundingClientRect(),at=button.getBoundingClientRect();
 menu.style.top=(at.bottom-box.top+4)+'px';menu.style.left=Math.max(8,Math.min(at.left-box.left,box.width-menu.offsetWidth-8))+'px';
 menu.style.maxHeight=Math.max(160,box.bottom-at.bottom-16)+'px';
 const search=menu.querySelector('.column-menu-search');
 search.addEventListener('input',renderColumnMenuValues);
 search.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();applyColumnMenu();}});
 menu.addEventListener('change',e=>{
  const box=e.target;if(box.type!=='checkbox')return;
  if(box.dataset.all!==undefined){for(const v of visibleColumnValues())box.checked?comparisonMenu.checked.add(v):comparisonMenu.checked.delete(v);}
  else{const v=comparisonMenu.values[Number(box.dataset.value)];box.checked?comparisonMenu.checked.add(v):comparisonMenu.checked.delete(v);}
  renderColumnMenuValues();
 });
 menu.addEventListener('click',e=>{
  const sort=e.target.closest('[data-sort]');
  if(sort){view.sort=sort.dataset.sort?{col,dir:sort.dataset.sort}:null;closeColumnMenu();renderComparisonPane(side,true);}
  else if(e.target.closest('[data-clear]')){delete view.filters[col];closeColumnMenu();renderComparisonPane(side,true);}
  else if(e.target.closest('[data-apply]'))applyColumnMenu();
 });
 renderColumnMenuValues();search.focus({preventScroll:true});
}
function visibleColumnValues(){
 const query=comparisonMenu.element.querySelector('.column-menu-search').value.trim().toLowerCase();
 return comparisonMenu.values.filter(v=>!query||v.toLowerCase().includes(query));
}
function renderColumnMenuValues(){
 const menu=comparisonMenu.element,shown=visibleColumnValues(),limit=1000,{checked,counts,values}=comparisonMenu;
 menu.querySelector('.column-menu-values').innerHTML=shown.slice(0,limit).map(v=>`<label title="${esc(v||'（空白）')}"><input type="checkbox" data-value="${values.indexOf(v)}"${checked.has(v)?' checked':''}><span>${esc(v||'（空白）')}</span><small>${counts.get(v)}</small></label>`).join('')+(shown.length>limit?`<p class="hint">仅列出前 ${limit} 个取值，请输入关键字缩小范围</p>`:'')+(shown.length?'':'<p class="hint">没有匹配的取值</p>');
 const all=menu.querySelector('[data-all]'),on=shown.filter(v=>checked.has(v)).length;
 all.checked=shown.length>0&&on===shown.length;all.indeterminate=on>0&&on<shown.length;
 menu.querySelector('[data-apply]').disabled=!shown.some(v=>checked.has(v));
}
function applyColumnMenu(){
 const {side,col,values,counts}=comparisonMenu,view=comparisonViews[side];
 // 与 Excel 相同：搜索框有内容时，只保留搜索结果中勾选的取值；全部勾选即取消该列筛选
 const keep=new Set(visibleColumnValues().filter(v=>comparisonMenu.checked.has(v)));
 if(!keep.size)return;
 if(keep.size===values.length&&values.every(v=>counts.get(v)>0))delete view.filters[col];
 else view.filters[col]=keep;
 closeColumnMenu();renderComparisonPane(side,true);
}
function closeColumnMenu(){comparisonMenu?.element?.remove();comparisonMenu=null;}
// 两侧原表中已配对的下级物资行之间画连接线，颜色与结论一致；当前选中的物资加粗；一端滚出可视区时贴边虚线显示，两端都滚出时不画
let comparisonLinksFrame=0;
function scheduleComparisonLinks(){cancelAnimationFrame(comparisonLinksFrame);comparisonLinksFrame=requestAnimationFrame(drawComparisonLinks);}
function drawComparisonLinks(){
 const panes=document.querySelector('#parentComparisonDialog .comparison-panes');if(!panes)return;
 let svg=panes.querySelector('.comparison-links');
 if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','comparison-links');svg.setAttribute('aria-hidden','true');panes.append(svg);
  new ResizeObserver(scheduleComparisonLinks).observe(panes);}
 const left=$('comparisonTarget'),right=$('comparisonSource'),leftGrid=left?.querySelector('.comparison-grid'),rightGrid=right?.querySelector('.comparison-grid');
 svg.innerHTML='';if(!leftGrid||!rightGrid)return;
 const box=panes.getBoundingClientRect(),normalize=p=>String(p||'').replaceAll('\\','/');
 svg.setAttribute('width',box.width);svg.setAttribute('height',box.height);
 const visible=grid=>{const r=grid.getBoundingClientRect(),head=grid.querySelector('thead')?.getBoundingClientRect().height||0;return{top:r.top+head-box.top,bottom:r.bottom-box.top-(grid.offsetHeight-grid.clientHeight),left:r.left-box.left,right:r.right-box.left};};
 const shows=(pane,at)=>at&&normalize(at.file)===normalize(pane.dataset.file)&&at.sheet===pane.dataset.sheet;
 const point=(grid,at,view)=>{const tr=grid.querySelector(`tr[data-row="${Number(at.row)}"]`);if(!tr)return null;const r=tr.getBoundingClientRect(),y=r.top+r.height/2-box.top;
  return{y:Math.min(view.bottom-3,Math.max(view.top+3,y)),out:r.bottom-box.top<view.top||r.top-box.top>view.bottom};};
 const current=parentComparison[comparisonIndex],links=new Map();
 for(const p of parentComparison){
  if(p.band==='未匹配'||!shows(left,p.target)||!shows(right,p.source))continue;
  const key=p.target.row+'|'+p.source.row,old=links.get(key);
  if(!old||p===current||(old!==current&&comparisonRank(p.band)<comparisonRank(old.band)))links.set(key,p);
 }
 const L=visible(leftGrid),R=visible(rightGrid),x1=L.right,x2=R.left,mid=(x1+x2)/2;let html='';
 for(const p of [...links.values()].sort((a,b)=>(a===current)-(b===current))){
  const a=point(leftGrid,p.target,L),b=point(rightGrid,p.source,R);if(!a||!b||a.out&&b.out)continue;
  const color=comparisonColor(p.band),active=p===current,faded=a.out||b.out;
  html+=`<g stroke="${color}" fill="${color}" opacity="${faded?0.4:active?1:0.85}"><title>${esc(p.label)}</title><path d="M${x1} ${a.y}C${mid} ${a.y} ${mid} ${b.y} ${x2} ${b.y}" fill="none" stroke-width="${active?3:1.6}"${faded?' stroke-dasharray="4 3"':''}/><circle cx="${x1}" cy="${a.y}" r="${active?3.5:2.5}"/><circle cx="${x2}" cy="${b.y}" r="${active?3.5:2.5}"/></g>`;
 }
 svg.innerHTML=html;
}
