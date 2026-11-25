// 土石方数智化算量接入平台外壳：侧栏视图、任务类型与流程步骤、对话路由。三维模块（/earthwork/main.js 及本地 three.js）在首次进入时才加载。
let Earthwork=null,earthworkLoading=null;
TASK_TYPES.earthwork={placeholder:'描述场平方案要求，例如：标高降低 0.5 m、纵坡改为 1.5% 再算；或在坡度限值内找造价最低的方案…',view:'earthwork',action:'打开土石方数智化算量'};
const EARTHWORK_STEPS=[
 {name:'资料清洗',done:()=>Boolean(state?.earthwork?.cleaning?.report),title:'选择资料文件夹，把图纸和勘察报告清洗成三维建模用的成果',run:()=>Earthwork?.showTab('clean')},
 {name:'三维建模',done:()=>Boolean(state?.earthwork?.model),title:'由清洗成果生成三维地形和地层模型',run:()=>Earthwork?.showTab('build')},
 {name:'标高寻优',done:()=>Boolean(Earthwork?.optimized?.()),title:'调整场平标高和坡度，或在对话里描述要求让助手寻优',run:()=>{Earthwork?.showTab('model');$('chatInput').value='放坡寻优：在合成坡度限值内同时寻优场地标高和纵横坡，找出造价最低的方案，并说明造价构成。';$('chatInput').focus();}},
];
function openEarthwork(){
 if(Earthwork||earthworkLoading)return earthworkLoading;
 earthworkLoading=import('/earthwork/main.js').then(m=>m.mount($('earthworkView'),{api,toast,esc,onChange:()=>renderTaskSteps()})).then(instance=>{
  Earthwork=instance;Earthwork.sync(state);Earthwork.setVisible(view==='earthwork'&&document.body.classList.contains('panel-open'));renderTaskSteps();
 }).catch(e=>{earthworkLoading=null;$('earthworkView').innerHTML=`<div class="error">三维模块加载失败：${esc(e.message)}</div>`;});
 return earthworkLoading;
}
const earthworkSwitchView=switchView;
switchView=function(v){
 earthworkSwitchView(v);
 if(v==='earthwork'){$('viewTitle').textContent='土石方数智化算量';$('canvasTitle').textContent='土石方算量工作台';if(taskType!=='earthwork'){taskType='earthwork';pendingStep='';renderTaskUI();}openEarthwork();}
 else if(taskType==='earthwork'){taskType='review';renderTaskUI();}
};
const earthworkRenderCanvas=renderCanvas;
renderCanvas=function(){
 const on=view==='earthwork';
 document.body.classList.toggle('ew-view',on);$('earthworkView').hidden=!on;
 Earthwork?.setVisible(on&&document.body.classList.contains('panel-open'));
 if(on){['quantityView','drawingView','databaseView','knowledgeView'].forEach(id=>$(id).hidden=true);return;}
 earthworkRenderCanvas();
};
const earthworkTogglePanel=togglePanel;
togglePanel=function(force){earthworkTogglePanel(force);Earthwork?.setVisible(view==='earthwork'&&document.body.classList.contains('panel-open'));};
// 新建任务回到物资评审对话时，任务类型一并复位
const earthworkNewTask=newTask;
newTask=async function(){await earthworkNewTask();document.body.classList.remove('ew-view');};
const earthworkTaskSteps=renderTaskSteps;
renderTaskSteps=function(){
 if(taskType!=='earthwork')return earthworkTaskSteps();
 const box=$('taskSteps');box.hidden=false;box.replaceChildren();
 EARTHWORK_STEPS.forEach((step,i)=>{
  const done=step.done(),button=document.createElement('button');
  button.type='button';button.className=`task-step ${done?'done':'ready'}`;button.title=step.title;
  button.innerHTML=`<em>${done?'✓':i+1}</em>${step.name}`;
  button.onclick=()=>{if(view!=='earthwork')switchView('earthwork');if(step.run)return step.run();$('chatInput').value=step.text;$('chatInput').focus();};
  if(i)box.append(Object.assign(document.createElement('span'),{className:'task-step-line'}));
  box.append(button);
 });
};
// 土石方任务的对话交给算量智能体：附带当前方案的工程量汇总（不含坐标），动作由计算引擎执行
const earthworkSubmit=$('chatForm').onsubmit;
$('chatForm').onsubmit=async e=>{
 if(taskType!=='earthwork'||state?.running)return earthworkSubmit(e);
 e.preventDefault();
 const text=$('chatInput').value.trim();if(!text)return;
 if(view!=='earthwork')switchView('earthwork');
 await openEarthwork();
 if(!Earthwork)return toast('三维模型尚未加载完成，请稍后再试。');
 $('chatInput').value='';
 const snap=Earthwork.snapshot();
 if(!snap){Earthwork.showTab('build');return toast('还没有三维模型：请先完成资料清洗并生成三维模型，再进行标高寻优。');}
 const ok=await action('earthwork/chat',{text,snapshot:snap});
 if(!ok)$('chatInput').value=text;
 $('chat').scrollTop=$('chat').scrollHeight;
};
// 建了新模型（或切回示例）后重新加载三维模块
let earthworkModelKey=null;
function reloadEarthwork(tab){Earthwork?.dispose();Earthwork=null;earthworkLoading=null;$('earthworkView').innerHTML='';return openEarthwork().then(()=>{if(tab)Earthwork?.showTab(tab);});}
const earthworkRender=render;
render=function(){earthworkRender();const key=state?.earthwork?.model?.stats?.time||'';if(earthworkModelKey===null)earthworkModelKey=key;else if(key!==earthworkModelKey&&!state.running){earthworkModelKey=key;if(Earthwork)reloadEarthwork(key?'build':'clean');}Earthwork?.sync(state);
 // 建模只要几秒：进行中加快刷新，让步骤进度看得见
 const building=state?.earthwork?.building;if(state?.running&&building&&!building.done&&!building.failed){clearTimeout(window.earthworkPoll);window.earthworkPoll=setTimeout(()=>refresh(),350);}};
