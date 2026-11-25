// 新建任务、任务类型、物资智能评审流程步骤、发送/停止和输入框模型选择。
let taskType='review',pendingStep='';
const TASK_TYPES={
 review:{placeholder:'向造价助手发送消息，输入任务或修改要求…'},
 drawing:{placeholder:'描述图纸识别或提资要求，例如：识别主变基础图中的设备材料…',view:'drawing',action:'打开图纸识别与提资'},
 price:{placeholder:'询问物资价格，或描述价格库导入、核对要求…',view:'database',action:'打开物资价格数据库'},
};
// 每一步：点选后填入输入框的任务描述。资料清洗在本机按规则完成；数量评审（候选配对 + 逐项核对）
// 要把脱敏后的清洗结果发给在线模型，发送前须经确认框同意；价格评审在本机按规则完成，清洗后即可执行，
// 未做数量评审时成套项的组成件会被单独比价，结论里会标注。成果报告不列在这里。
const TASK_STEPS=[
 {key:'clean',name:'资料清洗',text:()=>`资料清洗：在本机读取工作空间“${folderName(currentWorkspace())}”中的估算书和设计清册，按规则识别物资明细表，清洗为造价、清册两侧的物资清单（不调用在线模型）。`},
 {key:'quantity',name:'数量评审',text:()=>'数量评审：以设计清册为基准为造价物资建立配对、识别成套项与组成件，再逐项核对数量、单位和规格，给出一致、基本一致、部分一致、不一致等结论。'},
 {key:'price',name:'价格评审',text:()=>`价格评审：用物资价格数据库中${state?.prices?.[0]?.period?`期次 ${state.prices[0].period} 的`:''}信息价逐条比对造价物资单价，按声明的税口径与构成系数换算后给出偏差率和结论。`},
];
const SEND_ICON='<svg class="app-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V4M5 11l7-7 7 7"/></svg>';
const STOP_ICON='<svg class="app-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>';

async function newTask(){
 if(state?.running)return toast('当前任务正在执行，请完成或停止后再新建任务。');
 if(state?.messages?.length||state?.dataset||state?.workspace||state?.tizi?.folder){try{await api('session/new',{workspace:''});}catch(e){return toast(e.message);}}
 taskType='review';pendingStep='';$('chatInput').value='';
 showReviewConversation();renderTaskUI();
}
function chooseTaskType(type){
 taskType=type;pendingStep='';renderTaskUI();
 const info=TASK_TYPES[type];
 if(info.view)document.querySelector(`.nav[data-view="${info.view}"]`)?.click();
 $('chatInput').focus();
}
document.querySelectorAll('.task-types [data-task]').forEach(b=>b.onclick=()=>chooseTaskType(b.dataset.task));

function stepStatus(step){
 const stage=Number(state?.stage||0);
 if(step.key==='clean')return stage>=1?'done':currentWorkspace()?'ready':'blocked';
 if(step.key==='quantity')return stage>=3?'done':stage>=1?'ready':'blocked';
 return (state?.price_results||[]).length?'done':stage>=1?'ready':'blocked';
}
function pickStep(step){
 const status=stepStatus(step);
 if(step.key==='clean'&&!currentWorkspace()){toast('请先选择工作空间。');return openFolder();}
 // 资料清洗、价格评审完成后仍可重新执行；数量评审完成后不重复执行
 if(step.key==='quantity'&&status==='done')return toast(`“${step.name}”已完成，可在结果面板查看。`);
 if(status==='blocked'){const previous=TASK_STEPS[TASK_STEPS.indexOf(step)-1];return toast(`请先完成“${previous.name}”。`);}
 pendingStep=step.key;$('chatInput').value=step.text();$('chatInput').focus();
 $('chatInput').setSelectionRange($('chatInput').value.length,$('chatInput').value.length);renderTaskSteps();
}
function renderTaskSteps(){
 const box=$('taskSteps');if(!box)return;
 box.hidden=taskType!=='review'&&!state?.messages?.length;
 if(box.hidden)return;
 box.replaceChildren();
 TASK_STEPS.forEach((step,i)=>{
  const status=stepStatus(step),button=document.createElement('button');
  button.type='button';button.className=`task-step ${status}${pendingStep===step.key?' picked':''}${state?.running&&status==='ready'?' running':''}`;
  button.innerHTML=`<em>${status==='done'?'✓':i+1}</em>${step.name}`;
  button.title=status==='done'?(step.key==='quantity'?'已完成':'已完成，可再次执行'):status==='blocked'?(i?`请先完成“${TASK_STEPS[i-1].name}”`:'请先选择工作空间'):'点击填入任务描述，发送后执行';
  button.onclick=()=>pickStep(step);
  if(i)box.append(Object.assign(document.createElement('span'),{className:'task-step-line'}));
  box.append(button);
 });
}
function renderTaskUI(){
 document.querySelectorAll('.task-types [data-task]').forEach(b=>{const on=b.dataset.task===taskType;b.classList.toggle('selected',on);b.setAttribute('aria-selected',String(on));});
 $('chatInput').placeholder=TASK_TYPES[taskType].placeholder;
 document.querySelector('.conversation').classList.toggle('welcome',!state?.messages?.length);
 renderTaskSteps();renderSendButton();
}
function renderSendButton(){
 const button=$('sendButton'),running=Boolean(state?.running);
 if(button.dataset.mode===(running?'stop':'send'))return;
 button.dataset.mode=running?'stop':'send';button.classList.toggle('stop',running);
 button.innerHTML=running?STOP_ICON:SEND_ICON;button.setAttribute('aria-label',running?'停止':'发送');button.title=running?'停止：当前步骤结束后暂停':'发送';
}
$('chatForm').onsubmit=async e=>{
 e.preventDefault();
 if(state?.running){
  if(state.pause)return toast('已申请停止，正在等待当前步骤结束。');
  await action('pause');toast('已申请停止，当前步骤结束后暂停；进行中的在线请求不会被强制中断。');return;
 }
 const text=$('chatInput').value,step=TASK_STEPS.find(s=>s.key===pendingStep);
 if(step&&text.trim()===step.text()){
  // 数量评审会把清洗结果发给在线模型：确认框中点“是”才开始
  if(step.key==='quantity'&&!await confirmSendToModel())return;
  $('chatInput').value='';pendingStep='';renderTaskSteps();
  const ok=await action('chat',{text:text.trim(),step:step.key,consent:step.key==='quantity'});
  if(!ok){$('chatInput').value=text;pendingStep=step.key;renderTaskSteps();}
  return;
 }
 pendingStep='';$('chatInput').value='';sendText(text,chatRefs);
};
$('chatInput').addEventListener('input',()=>{const step=TASK_STEPS.find(s=>s.key===pendingStep);if(step&&$('chatInput').value.trim()!==step.text()){pendingStep='';renderTaskSteps();}});

// 输入框右侧的模型选择：DeepSeek、Kimi、通义千问各有接口地址、模型名与密钥；未填密钥或测试连接失败的显示为灰色，
// 点模型名后的“⋯”打开该模型的设置与测试连接
const MODEL_LOGOS={
 deepseek:'<svg class="model-logo" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#4D6BFE"/><path fill="#fff" d="M19.6 8.2c-.2-.1-.3.1-.4.2l-.2.2c-.4.4-.8.7-1.4.6-.8 0-1.5.2-2.1.8-.1-.7-.5-1.2-1.2-1.5-.3-.2-.7-.3-.9-.6-.2-.2-.2-.5-.3-.7 0-.2-.1-.3-.3-.3-.2 0-.3.2-.3.3-.4.8-.5 1.7-.4 2.5.1 1.9 1 3.4 2.6 4.4.2.1.2.2.2.4l-.3.9c-.1.3-.2.3-.5.2-.9-.4-1.7-1-2.4-1.7-1.2-1.1-2.3-2.4-3.6-3.4l-.9-.6c-1.3-1.3.2-2.3.5-2.5.4-.1.1-.6-1-.6S6 8.2 5.6 8.4c-.5-.1-1.1-.1-1.6-.1C2 8.6.7 10 .5 11.3c-.4 2.2.3 4.1 1.8 5.6 1.6 1.6 3.5 2.4 5.6 2.2 1.3-.1 2.7-.2 4.3-1.5.4.2.8.3 1.5.4.5 0 1 0 1.4-.1.6-.1.6-.7.4-.8-1.9-.9-1.5-.5-1.9-.8.9-1.1 2.4-2.3 3-6.2v-.7c0-.1.1-.2.3-.2.4 0 .8-.1 1.2-.3 1-.6 1.4-1.5 1.5-2.6v-.3zM8.8 15.8c-1.5-1.2-2.2-1.6-2.5-1.6-.3 0-.2.3-.2.6.1.2.2.4.3.6.1.1 0 .3-.2.4-.6.4-1.7-.1-1.8-.2-1.2-.7-2.2-1.6-2.9-2.9-.7-1.2-1-2.5-1-3.9 0-.3.1-.5.4-.5.4-.1.8-.1 1.2-.1 1.6.2 3 1 4.1 2.2.6.7 1.1 1.5 1.6 2.3.6.8 1.2 1.6 1.9 2.3.3.3.6.5 1.4.9-.4.1-1.2-.1-2.3-.1zm1.4-5.3c0-.2.2-.3.3-.3h.1c.1 0 .2.1.2.2v.1c0 .2-.2.3-.3.3s-.3-.1-.3-.3zm1.2 1.3c-.2.1-.3.1-.5.2-.3 0-.6-.1-.7-.3-.3-.5-.5-.7-.5-.7 0 0 .3-.2.6-.1.4.1.7.3 1.1.7v.2z"/></svg>',
 kimi:'<svg class="model-logo" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#111"/><path fill="#fff" d="M6.5 5.5h2.6v5.6l4.9-5.6h3.2l-5.2 5.8 5.5 7.2h-3.2l-4.1-5.5-1.1 1.2v4.3H6.5z"/><circle cx="18.3" cy="6.3" r="1.5" fill="#1783FF"/></svg>',
 qwen:'<svg class="model-logo" viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#615CED"/><path fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round" d="M12 4.5 18.5 8.25v7.5L12 19.5l-6.5-3.75v-7.5z"/><path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M12 9.2v3.6l3 1.7"/></svg>',
};
const MORE_ICON='<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="12.5" cy="8" r="1.3"/></svg>';
const modelReady=m=>m.has_key&&m.ok!==false;
function activeModel(){return (state?.models||[]).find(m=>m.active)||(state?.models||[])[0];}
function renderComposerModel(){
 const m=activeModel(),label=$('composerModel');if(!label||!m)return;
 label.innerHTML=`${MODEL_LOGOS[m.id]||''}<span>${esc(m.label)}</span>`;label.classList.toggle('model-off',!modelReady(m));
 $('modelPicker').title=modelReady(m)?m.label+' · 在线模型':m.label+' · 未设置密钥或测试连接未通过';
}
function closeModelMenu(){$('modelMenu').hidden=true;$('modelPicker').setAttribute('aria-expanded','false');}
function toggleModelMenu(){
 const menu=$('modelMenu');if(!menu.hidden)return closeModelMenu();
 menu.replaceChildren();
 for(const m of state?.models||[]){
  const row=document.createElement('div');row.className='model-row';
  const item=document.createElement('button');item.type='button';item.setAttribute('role','menuitemradio');item.setAttribute('aria-checked',String(m.active));
  item.className='model-option'+(m.active?' selected':'')+(modelReady(m)?'':' model-off');
  item.title=modelReady(m)?m.model+' · '+m.base_url:(m.has_key?'测试连接未通过：'+m.error:'尚未填写 API Key');
  item.innerHTML=`${MODEL_LOGOS[m.id]||''}<span>${esc(m.label)}</span>${m.active?'<b>✓</b>':''}`;
  item.onclick=()=>modelReady(m)?chooseModel(m):(closeModelMenu(),openModelDialog(m.id));
  const more=document.createElement('button');more.type='button';more.className='model-more';more.innerHTML=MORE_ICON;
  more.title=m.label+' 设置与测试连接';more.setAttribute('aria-label',more.title);more.onclick=e=>{e.stopPropagation();closeModelMenu();openModelDialog(m.id);};
  row.append(item,more);menu.append(row);
 }
 menu.hidden=false;$('modelPicker').setAttribute('aria-expanded','true');menu.querySelector('button')?.focus();
}
async function chooseModel(m){
 closeModelMenu();if(m.active)return;
 if(state.running)return toast('当前任务正在执行，请结束后再切换模型。');
 if(state.dataset&&state.stage>=2&&!confirm('切换模型后，当前数量评审结果需按新模型重新执行（原结果保留在检查点）。继续切换？'))return;
 if(await action('models/select',{id:m.id}))toast('已切换为 '+m.label);
}
// 单个模型的设置与测试连接
function openModelDialog(id){
 const m=(state?.models||[]).find(x=>x.id===id);if(!m)return;
 let dialog=$('modelDialog');
 if(!dialog){dialog=document.createElement('dialog');dialog.id='modelDialog';dialog.className='model-dialog';document.body.append(dialog);}
 dialog.innerHTML=`<form onsubmit="saveModel(event,'${id}')"><div class="dialog-head"><h2>${MODEL_LOGOS[id]||''}${esc(m.label)}</h2><button type="button" aria-label="关闭" onclick="closeDialog('modelDialog')">×</button></div>
 <label>API Base URL</label><input id="modelUrl" value="${esc(m.base_url)}"><label>模型名称</label><input id="modelName" value="${esc(m.model)}">
 <label>API Key <small>仅本次运行有效，不写入磁盘</small></label><input id="modelKey" type="password" autocomplete="off" placeholder="${m.has_key?'已填写；留空保留当前密钥':'输入密钥'}">
 <div class="detail-grid model-limits"><div><label>并发上限</label><input id="modelConcurrency" type="number" min="0" max="100" value="${m.concurrency}"></div><div><label>每分钟请求数</label><input id="modelRpm" type="number" min="0" max="1000" value="${m.rpm}"></div></div>
 <p class="hint">按账户限速等级填写，0 表示不限制；超出时平台排队等待，不会让评审失败。Kimi 累计充值不足 50 元为 Tier0（并发 1、每分钟 3 次），一次数量评审约需数十分钟；Tier1（并发 15、每分钟 100 次）及以上可相应调高。</p>
 <p class="model-status ${m.ok===true?'good':m.ok===false?'bad':''}" id="modelStatus">${m.ok===true?'✓ 测试连接成功':m.ok===false?'测试连接未通过：'+esc(m.error):m.has_key?'已填写密钥，尚未测试连接':'尚未填写 API Key'}</p>
 <div class="dialog-actions"><button type="button" class="button" onclick="testModel('${id}')">测试连接</button><button class="primary">保存</button></div></form>`;
 dialog.showModal();
}
const modelForm=id=>({id,base_url:$('modelUrl').value.trim(),model:$('modelName').value.trim(),api_key:$('modelKey').value,concurrency:$('modelConcurrency').value,rpm:$('modelRpm').value});
async function testModel(id){
 const status=$('modelStatus');status.className='model-status';status.textContent='正在测试连接…';
 try{const result=await api('models/test',modelForm(id));status.className='model-status '+(result.ok?'good':'bad');status.textContent=result.ok?'✓ 测试连接成功':'测试连接未通过：'+result.error;$('modelKey').value='';}
 catch(e){status.className='model-status bad';status.textContent=e.message;}
}
async function saveModel(e,id){e.preventDefault();if(await action('models/save',modelForm(id))){closeDialog('modelDialog');toast('模型设置已保存');}}
$('modelPicker').onclick=toggleModelMenu;
document.addEventListener('click',e=>{if(!$('modelMenu').hidden&&!e.target.closest('.model-picker-wrap'))closeModelMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('modelMenu').hidden){closeModelMenu();$('modelPicker').focus();}});

const taskRender=render;
render=function(){taskRender();renderComposerModel();renderTaskUI();};
