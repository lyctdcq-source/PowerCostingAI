// Fixed actions shared by native menus and keyboard shortcuts.
let lastEditor=null;
document.addEventListener('focusin',e=>{if(e.target.matches('input,textarea,[contenteditable=true]'))lastEditor=e.target;});
function helpDialog(about=false){
 const title=about?'关于电力造价智能体平台':'使用帮助';
 $('detailContent').innerHTML=`<div class="dialog-head"><h2>${title}</h2><button aria-label="关闭" onclick="closeDialog('detailDialog')">×</button></div>${about?'<p>电力造价智能体平台 · v'+esc(state?.version||'')+'</p><p>开发者：云南中合数维科技有限责任公司</p><p>Windows 桌面客户端 · 在线模型 · 本地工程工作区</p><p>源码采用 Git 版本管理；技能和知识分别保留业务版本。</p>':'<ol><li>文件 → 添加工程文件夹，读取本地估算和清册。</li><li>编辑 → 模型与连接，配置文字与视觉 API。</li><li>查看清洗结果，确认后继续匹配与核对。</li><li>通过对话输入范围调整或技能修改要求。</li><li>视图菜单打开知识库、图纸和文档成果。</li><li>文件 → 导出审计包，保存可追溯结果。</li></ol><div class="help-links"><button class="help-link" onclick="helpFlow()"><strong>物资智能评审流程图</strong><small>系统架构，以及资料清洗 → 数量评审 → 价格评审的完整工作流</small></button></div><p>Ctrl+O 添加工程 · Ctrl+Enter 发送指令 · Ctrl+B 折叠侧栏 · Ctrl+J 工作结果 · F1 帮助</p>'}`;
 $('detailDialog').classList.remove('help-flow');
 $('detailDialog').showModal();
}
function helpFlow(){
 // 流程图是本机静态页（static/help），放进对话框里的 iframe，关闭时恢复对话框原尺寸
 const dialog=$('detailDialog');
 $('detailContent').innerHTML=`<div class="dialog-head"><h2>物资智能评审流程图</h2><div class="help-flow-actions"><button class="help-back" onclick="helpDialog(false)">返回帮助</button><button aria-label="关闭" onclick="closeDialog('detailDialog')">×</button></div></div><iframe class="help-frame" src="/help/material-review-flow.html" title="物资智能评审流程图"></iframe>`;
 dialog.classList.add('help-flow');
 if(!dialog.dataset.helpFlowReset){dialog.dataset.helpFlowReset='1';dialog.addEventListener('close',()=>dialog.classList.remove('help-flow'));}
 if(!dialog.open)dialog.showModal();
}
async function desktopAction(action){
 const fixed={skills:()=>openSkillStudio(),logout:()=>logout(),folder:openFolder,export:exportAudit,settings:openSettings,sidebar:toggleSidebar,panel:togglePanel,conversation:showConversation,help:()=>helpDialog(false),about:()=>helpDialog(true)};
 if(fixed[action])return fixed[action]();
 if(['quantity','drawing','database','knowledge','earthwork'].includes(action))return switchView(action);
 if(['undo','redo','cut','copy','paste','selectAll'].includes(action)){
  const el=lastEditor||$('chatInput');el.focus();
  try{if(action==='paste'){const text=await navigator.clipboard.readText();document.execCommand('insertText',false,text);}else document.execCommand(action);}catch(e){toast('请在输入框内使用键盘快捷键完成此操作。');}
 }
}
document.addEventListener('keydown',e=>{
 if(e.key==='F1'){e.preventDefault();desktopAction('help');}
 if(e.ctrlKey&&!e.shiftKey&&!e.altKey){const map={o:'folder',b:'sidebar',j:'panel'};if(map[e.key.toLowerCase()]){e.preventDefault();desktopAction(map[e.key.toLowerCase()]);}if(e.key==='Enter'&&document.activeElement===$('chatInput')){e.preventDefault();$('chatForm').requestSubmit();}}
});
// WebView2 disables the native context menu outside debug mode, so selected text gets a small copy menu here.
function closeContextMenu(){document.querySelector('.ctx-menu')?.remove();}
document.addEventListener('contextmenu',e=>{
 closeContextMenu();
 const editor=e.target.closest('input,textarea,[contenteditable=true]'),text=String(getSelection()).trim()||(editor&&editor.value?.slice(editor.selectionStart,editor.selectionEnd).trim())||'';
 if(!text&&!editor)return;
 e.preventDefault();
 const items=[];
 if(text){
  items.push(['复制','Ctrl+C',()=>{if(!document.execCommand('copy'))navigator.clipboard?.writeText(text);}]);
  if($('searchRows')&&!editor)items.push(['在匹配结果中搜索','',()=>{const box=$('searchRows');box.value=text;box.dispatchEvent(new Event('change'));}]);
 }
 if(editor){
  if(text)items.unshift(['剪切','Ctrl+X',()=>{editor.focus();document.execCommand('cut');}]);
  items.push(['粘贴','Ctrl+V',async()=>{editor.focus();try{document.execCommand('insertText',false,await navigator.clipboard.readText());}catch(err){toast('请使用 Ctrl+V 粘贴。');}}]);
  items.push(['全选','Ctrl+A',()=>{editor.focus();editor.select?.();}]);
 }
 const menu=document.createElement('div');menu.className='ctx-menu';menu.setAttribute('role','menu');
 for(const [label,key,run] of items){const b=document.createElement('button');b.setAttribute('role','menuitem');b.innerHTML=`<span>${label}</span><kbd>${key}</kbd>`;b.onclick=()=>{closeContextMenu();run();};menu.append(b);}
 // Keep the page selection alive while the menu is clicked.
 menu.addEventListener('mousedown',ev=>ev.preventDefault());
 document.body.append(menu);
 menu.style.left=Math.min(e.clientX,innerWidth-menu.offsetWidth-8)+'px';menu.style.top=Math.min(e.clientY,innerHeight-menu.offsetHeight-8)+'px';
});
document.addEventListener('mousedown',e=>{if(!e.target.closest('.ctx-menu'))closeContextMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeContextMenu();});
addEventListener('blur',closeContextMenu);addEventListener('scroll',closeContextMenu,true);
// Dragging to select text inside a result row should not open the row's detail dialog.
document.addEventListener('click',e=>{const open=e.target.closest('.match-open,.match-node');const sel=getSelection();if(open&&!sel.isCollapsed&&open.contains(sel.anchorNode))e.stopPropagation(),e.preventDefault();},true);
