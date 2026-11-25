// 右侧结果面板的“文档”页签：预览对话中导出的 Excel、Word 成果报告，以及原表和清洗后表格（只读，可下载）。
// 原有的清洗结果、数量评审、价格评审页签不变。对话回答带文件时显示文件卡片，回答到达时自动在这里打开。
let docOpen='',docView=null,docRequest=0,docSeenMessages=null;
function docList(){
 const seen=new Set(),out=[];
 for(const [group,items] of [['对话生成',state.documents||[]],['成果报告',state.material_artifacts||[]],['原表与清洗后',state.dataset?.excel_books||[]]])
  for(const d of items)if(d?.id&&!seen.has(d.id)){seen.add(d.id);out.push({...d,group});}
 return out;
}
const docKind=d=>/\.docx$/i.test(d.id)?'docx':/\.xlsx?$/i.test(d.id)?'xlsx':'';
const docNote=d=>d.summary||({original:'原始 Excel 快照',cleaned:'清洗后 Excel',edited:'编辑副本'}[d.kind])||'';
function openDocument(id){
 docOpen=id;docView=null;
 if(view!=='quantity')switchView('quantity');
 if(!document.body.classList.contains('panel-open')&&typeof togglePanel==='function')togglePanel(true);
 setTab('docs');
}
async function loadDocument(d,sheet=null,row=1,column=1){
 const seq=++docRequest;docView={id:d.id,loading:true};
 try{
  let data;
  if(docKind(d)==='docx'){const r=await fetch('/api/docs/preview?id='+encodeURIComponent(d.id));data=await r.json();if(!r.ok)throw Error(data.error||'无法预览');}
  else data=await excelApi('view',{id:d.id,sheet,row,column});
  if(seq===docRequest)docView={id:d.id,kind:docKind(d),data};
 }catch(e){if(seq===docRequest)docView={id:d.id,error:e.message};}
 if(seq===docRequest&&tab==='docs')renderCanvas();
}
function docsHTML(){
 const docs=docList();
 if(!docs.length)return '<div class="card empty"><div class="empty-icon">▤</div><h3>还没有文档</h3><p>在对话中让助手导出 Excel 或生成成果报告，文件会出现在这里。</p></div>';
 if(!docs.some(d=>d.id===docOpen))docOpen=(docs.filter(d=>d.group==='对话生成').at(-1)||docs[0]).id;
 const current=docs.find(d=>d.id===docOpen);
 if(!docView||docView.id!==docOpen)loadDocument(current);
 const groups=[...new Set(docs.map(d=>d.group))];
 return `<div class="docs-layout"><nav class="doc-list" aria-label="文档">${groups.map(g=>`<p class="doc-group">${esc(g)}</p>${docs.filter(d=>d.group===g).map(d=>`<button type="button" class="doc-item${d.id===docOpen?' selected':''}" data-doc-open="${esc(d.id)}"><span class="doc-icon ${docKind(d)}">${docKind(d)==='docx'?'W':'X'}</span><span><b>${esc(d.name)}</b><small>${esc(docNote(d))}</small></span></button>`).join('')}`).join('')}</nav><section class="doc-viewer">${docViewerHTML(current)}</section></div>`;
}
function docViewerHTML(d){
 const head=`<div class="doc-head"><div><h3>${esc(d.name)}</h3><small>${esc(docNote(d))}${d.time?' · '+esc(d.time):''}</small></div><a class="button" href="/api/artifacts/${encodeURIComponent(d.id)}" download="${esc(d.name)}">下载</a></div>`;
 if(!docView||docView.loading)return head+'<p class="muted doc-loading">正在读取文件…</p>';
 if(docView.error)return head+`<div class="error">${esc(docView.error)}</div>`;
 return head+(docView.kind==='docx'?docxHTML(docView.data):xlsxHTML(docView.data));
}
function docxHTML(data){
 return `<div class="doc-body docx-preview">${(data.blocks||[]).map(b=>b.type==='heading'?`<h${Math.min(6,b.level+3)}>${esc(b.text)}</h${Math.min(6,b.level+3)}>`:b.type==='table'?`<table>${b.rows.map((r,i)=>`<tr>${r.map(c=>i?`<td>${esc(c)}</td>`:`<th>${esc(c)}</th>`).join('')}</tr>`).join('')}</table>`:`<p${b.type==='note'?' class="muted"':''}>${esc(b.text)}</p>`).join('')||'<p class="muted">文档没有正文内容。</p>'}</div><p class="hint">只读预览：显示标题、段落和表格，页眉页脚、图片和域代码不显示，完整排版请下载后查看。</p>`;
}
function xlsxHTML(d){
 const col=n=>typeof excelColumn==='function'?excelColumn(n):n;
 const pages=Math.max(1,Math.ceil(d.rows/d.page_size)),pageNo=Math.floor((d.row-1)/d.page_size)+1;
 return `<div class="doc-toolbar"><label>工作表 <select data-doc-sheet>${d.sheets.map(s=>`<option value="${esc(s.name)}" ${s.name===d.sheet?'selected':''}>${esc(s.name)}（${s.rows}行）</option>`).join('')}</select></label><span>第 ${pageNo}/${pages} 页</span><button type="button" class="button" data-doc-page="${Math.max(1,d.row-d.page_size)}" ${d.row<=1?'disabled':''}>上一页</button><button type="button" class="button" data-doc-page="${d.row+d.page_size}" ${d.row+d.page_size>d.rows?'disabled':''}>下一页</button></div>
 <div class="doc-body xlsx-preview"><table><tr><th></th>${(d.cells[0]||[]).map((_,i)=>`<th>${col(d.column+i)}</th>`).join('')}</tr>${d.cells.map((cells,i)=>`<tr><th>${d.row+i}</th>${cells.map(c=>`<td title="${esc(String(c.value??'')+(c.formula?'\n公式：'+c.formula:''))}">${esc(c.value)}</td>`).join('')}</tr>`).join('')}</table></div>`;
}
document.addEventListener('click',e=>{
 const open=e.target.closest?.('[data-doc-open]');if(open){e.preventDefault();openDocument(open.dataset.docOpen);return;}
 const page=e.target.closest?.('[data-doc-page]');if(page&&docView?.data){const d=docList().find(x=>x.id===docOpen);if(d)loadDocument(d,docView.data.sheet,Number(page.dataset.docPage));}
});
document.addEventListener('change',e=>{const s=e.target.closest?.('[data-doc-sheet]');if(!s)return;const d=docList().find(x=>x.id===docOpen);if(d)loadDocument(d,s.value);});
// 回答中的文件卡片：预览（在右侧“文档”页签打开）与下载
function filesHTML(m){
 if(!m.files?.length)return '';
 return `<div class="file-cards">${m.files.map(f=>`<div class="file-card"><span class="doc-icon ${docKind(f)}">${docKind(f)==='docx'?'W':'X'}</span><span class="file-card-text"><b>${esc(f.name)}</b><small>${esc(f.summary||'')}</small></span><button type="button" class="button" data-doc-open="${esc(f.id)}">预览</button><a class="button" href="/api/artifacts/${encodeURIComponent(f.id)}" download="${esc(f.name)}">下载</a></div>`).join('')}</div>`;
}
// 新到达的回答带文件时自动在右侧打开；页面刚打开时已有的回答不自动打开
const renderBeforeDocs=render;
render=function(){
 renderBeforeDocs();
 const messages=state?.messages||[];
 if(docSeenMessages===null){docSeenMessages=messages.length;return;}
 const fresh=messages.slice(docSeenMessages).filter(m=>m.open_file);docSeenMessages=messages.length;
 if(fresh.length)openDocument(fresh.at(-1).open_file);
};
// 状态刷新会重绘页签：保留文档预览的滚动位置
const renderCanvasBeforeDocs=renderCanvas;
renderCanvas=function(){
 const body=document.querySelector('.doc-body'),top=body?.scrollTop,left=body?.scrollLeft,list=document.querySelector('.doc-list')?.scrollTop;
 renderCanvasBeforeDocs();
 const next=document.querySelector('.doc-body');if(next&&body){next.scrollTop=top;next.scrollLeft=left;}
 const nav=document.querySelector('.doc-list');if(nav&&list)nav.scrollTop=list;
};
