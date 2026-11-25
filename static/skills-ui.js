// User skill studio: edits remain local drafts until explicitly saved.
let studioSkill=null,studioDirty=false,studioBusy=false;
const skillTemplate={id:'my_skill',name:'我的技能',description:'处理输入数据并返回结果',instructions:'',code:'def run(data):\n    return {"count": len(data.get("items", []))}\n',tests:[{input:{items:[1,2]},expected:{count:2}}]};
async function skillApi(op,data={}){return (await api('custom-skills/'+op,data)).result;}
function skillDraft(){return {id:$('skillId').value.trim(),name:$('skillName').value.trim(),description:$('skillDescription').value,instructions:$('skillInstructions').value,code:$('skillCode').value,tests:JSON.parse($('skillTests').value||'[]')};}
function setSkillEditor(item){showStudioPane('custom');platformSkill=null;studioSkill=item;studioDirty=false;for(const [id,key] of [['skillId','id'],['skillName','name'],['skillDescription','description'],['skillInstructions','instructions'],['skillCode','code']])$(id).value=item[key]||'';$('skillId').readOnly=Boolean(item.version);$('skillTests').value=JSON.stringify(item.tests||[],null,2);$('skillMeta').textContent=item.version?`v${item.version} · ${item.enabled?'已启用':'未启用'} · ${item.tested?'测试通过':'待测试'}`:'未保存的草稿';$('skillOutput').textContent='保存后运行样例测试，通过后即可启用。';$('skillToggle').textContent=item.enabled?'停用':'启用';$('skillHistory').innerHTML='<option value="">选择历史版本…</option>'+(item.history||[]).slice().reverse().map(h=>`<option value="${h.version}">v${h.version} · ${esc(h.updated)}</option>`).join('');}
async function listCustomSkills(){const items=await skillApi('list');$('customSkillList').innerHTML=items.map(e=>`<button class="skill-list-item ${studioSkill?.id===e.id?'selected':''}" onclick="selectCustomSkill('${e.id}')"><strong>${esc(e.name)}</strong><small>${esc(e.id)} · v${e.version}</small><span>${e.enabled?'已启用':'未启用'}</span></button>`).join('')||'<p class="hint">还没有自定义技能。点击“新建技能”开始。</p>';}
async function selectCustomSkill(id){if(studioBusy)return;if((studioDirty||platformDirty)&&!confirm('放弃当前未保存的修改？'))return;platformDirty=false;try{setSkillEditor(await skillApi('get',{id}));await listCustomSkills();}catch(e){toast(e.message)}}
function newCustomSkill(){if(studioBusy)return;if((studioDirty||platformDirty)&&!confirm('放弃当前未保存的修改？'))return;platformDirty=false;setSkillEditor({...skillTemplate,code:skillTemplate.code.replaceAll('\\n','\n')});}
async function openSkillStudio(){try{if(!$('skillStudio').open)$('skillStudio').showModal();for(const r of await Promise.allSettled([listCustomSkills(),listPlatformSkills()]))if(r.status==='rejected')toast('技能列表加载失败：'+r.reason.message);if(!platformSkill&&!studioSkill)selectPlatformSkill('pipeline','match_flow');}catch(e){toast(e.message)}}
function closeSkillStudio(){if(studioBusy)return toast('操作进行中，请等待结果');if((studioDirty||platformDirty)&&!confirm('关闭并放弃未保存的修改？'))return;$('skillStudio').close();studioSkill=null;studioDirty=false;platformSkill=null;platformDirty=false;}
async function skillJob(fn){if(studioBusy)return;studioBusy=true;$('skillStudio').querySelectorAll('button,input,textarea,select').forEach(e=>e.disabled=true);$('skillStudio').classList.add('busy');$('skillJobStatus').textContent='正在处理…';try{await fn();$('skillJobStatus').textContent='操作完成';}catch(e){$('skillJobStatus').textContent=e.message;toast(e.message);}finally{studioBusy=false;$('skillStudio').querySelectorAll('button,input,textarea,select').forEach(e=>e.disabled=false);$('skillStudio').classList.remove('busy');}}
function requireSaved(){if(!studioSkill?.version||studioDirty)throw Error('请先保存当前草稿');return studioSkill.id;}
async function saveCustomSkill(){return skillJob(async()=>{const item=await skillApi('save',{skill:skillDraft(),base_version:studioSkill?.version||null});setSkillEditor(item);await listCustomSkills();toast('技能已保存为新版本，需重新测试后启用');});}
async function testCustomSkill(){return skillJob(async()=>{const id=requireSaved();const results=await skillApi('test',{id});const item=await skillApi('get',{id});setSkillEditor(item);$('skillOutput').textContent=JSON.stringify(results,null,2);await listCustomSkills();});}
async function toggleCustomSkill(){return skillJob(async()=>{setSkillEditor(await skillApi('toggle',{id:requireSaved(),enabled:!studioSkill.enabled}));await listCustomSkills();});}
async function generateCustomSkill(){return skillJob(async()=>{const instruction=$('skillInstructions').value.trim();if(!instruction)throw Error('请填写技能开发要求');const old=studioSkill;const draft=skillDraft();const result=await skillApi('generate',{instruction,draft});if(old?.version){result.id=old.id;result.version=old.version;result.history=old.history;}setSkillEditor(result);studioDirty=true;$('skillMeta').textContent='AI草稿待检查、保存和测试';$('skillOutput').textContent='已生成技能代码和测试样例。请检查后保存，不会自动启用。';});}
function downloadSkill(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
async function exportCustomSkill(){return skillJob(async()=>{const id=requireSaved();downloadSkill(id+'.skill.json',await skillApi('export',{id}));});}
function importCustomSkill(){if(studioBusy)return;$('skillImport').value='';$('skillImport').click();}
$('skillImport').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;if(file.size>200000)return toast('技能包上限200KB');if(studioDirty&&!confirm('放弃当前未保存的草稿并导入？'))return;await skillJob(async()=>{setSkillEditor(await skillApi('import',{package:JSON.parse(await file.text())}));await listCustomSkills();$('skillOutput').textContent='技能已导入，默认停用。请检查代码并运行测试后启用。';});});
async function rollbackCustomSkill(){return skillJob(async()=>{const id=requireSaved(),version=Number($('skillHistory').value);if(!version)throw Error('请选择历史版本');setSkillEditor(await skillApi('rollback',{id,version}));await listCustomSkills();});}
async function runCustomSkill(){return skillJob(async()=>{$('skillOutput').textContent=JSON.stringify(await skillApi('run',{id:requireSaved(),input:JSON.parse($('skillInput').value||'{}')}),null,2);});}
function useSkillInChat(){try{const id=requireSaved();if(!studioSkill.enabled)throw Error('请先测试并启用技能');const input=JSON.parse($('skillInput').value||'{}');closeSkillStudio();$('chatInput').value='/技能 '+id+' '+JSON.stringify(input);$('chatInput').focus();}catch(e){toast(e.message)}}
$('skillEditor').addEventListener('input',()=>{studioDirty=true;$('skillMeta').textContent='草稿有未保存修改';});
$('skillStudio').addEventListener('cancel',e=>{e.preventDefault();closeSkillStudio();});
async function startVoiceInput(){
 const input=$('chatInput');input.focus();
 if(!window.pywebview?.api?.voice_input){toast('请在Windows桌面客户端使用语音输入，也可在输入框按 Win+H。');return;}
 $('voiceInput').disabled=true;
 try{const r=await window.pywebview.api.voice_input();if(r.error)throw Error(r.error);toast('请在Windows听写面板开始或停止录音。文字会进入输入框，检查后再发送。');}catch(e){toast(e.message||'无法打开系统听写，请按 Win+H 并检查麦克风设置。');}finally{$('voiceInput').disabled=false;}
}

// Platform skills: pipeline skills (the code the cleaning/matching/review steps actually run) and rule skills.
let platformSkill=null,platformDirty=false,platformItems={pipeline:[],rule:[]};
function showStudioPane(which){$('platformPane').hidden=which!=='platform';$('customPane').hidden=which!=='custom';}
async function listPlatformSkills(){
 const [p,r]=await Promise.all([api('pipeline-skills/list'),api('rule-skills/list')]);platformItems={pipeline:p.result,rule:r.result};
 const item=(kind,s,meta)=>`<button class="skill-list-item ${platformSkill?.kind===kind&&platformSkill.id===s.id?'selected':''}" onclick="selectPlatformSkill('${kind}','${s.id}')"><strong>${esc(s.name)}</strong><small>${esc(s.id)}${meta}</small></button>`;
 const stages=[...new Set(platformItems.pipeline.map(s=>s.stage))];
 $('pipelineSkillList').innerHTML=stages.map(st=>`<p class="studio-stage">${esc(st)}</p>`+platformItems.pipeline.filter(s=>s.stage===st).map(s=>item('pipeline',s,` · ${s.lines}行${s.modified?' · <span class="modified">已修改</span>':''}${s.template_updated?' · 内置有更新':''}`)).join('')).join('');
 $('ruleSkillList').innerHTML=platformItems.rule.map(s=>item('rule',s,s.versions.length?` · ${s.versions.length}次修改`:'')).join('');
}
function selectPlatformSkill(kind,id){
 if(studioBusy)return;if((platformDirty||studioDirty)&&!confirm('放弃当前未保存的修改？'))return;
 showPlatformSkill(kind,id);listPlatformSkills().catch(e=>toast(e.message));
}
function showPlatformSkill(kind,id){
 const s=platformItems[kind].find(x=>x.id===id);if(!s)return;
 platformSkill={kind,...s};platformDirty=false;studioDirty=false;studioSkill=null;showStudioPane('platform');
 const pipeline=kind==='pipeline';
 $('platformTitle').textContent=s.name;$('platformBadge').textContent=pipeline?'流程技能 · '+s.stage:'规则技能';
 $('platformDescription').textContent=pipeline?(s.description||''):(s.effect||'');
 $('platformInterface').textContent=`${s.id}.py · 接口函数：`+Object.entries(s.functions).map(([f,n])=>`${f}(${n}个参数)`).join('，')+(s.path?' · '+s.path:'');
 $('platformCode').value=s.code;$('platformDraftBox').hidden=!pipeline;$('platformReset').hidden=!pipeline;$('platformCheck').hidden=!pipeline;
 $('platformMeta').textContent=pipeline?(s.modified?'当前为已修改版本':'当前为内置版本'):'修改后自动执行契约测试';
 $('platformHistory').innerHTML='<option value="">选择历史修改…</option>'+s.versions.map(v=>`<option value="${v.id}">${esc(v.time)} · ${esc(v.summary)}</option>`).join('');
 $('platformDiff').textContent=s.last_diff||'尚无修改记录';$('platformOutput').textContent='';
 $('platformHint').textContent=pipeline?'流程技能可导入 re、math、json 等纯计算模块；文件、网络、模型调用只能通过 ctx 使用。保存前检查语法与接口，并用内置样例把清洗、匹配、评审、导出整条流程跑通；保存后受影响的结果自动失效，需重新执行。这是代码审查约束，不是操作系统级沙箱，请只保存经过审查的代码。':'规则技能是纯函数：不能导入模块、访问文件或网络；保存前执行契约测试。也可在对话中用自然语言要求修改。';
}
function platformResult(lines){$('platformOutput').textContent=lines.filter(Boolean).join('\n');}
async function reloadPlatform(kind,id){await listPlatformSkills();showPlatformSkill(kind,id);await listPlatformSkills();}
async function checkPlatformSkill(){return skillJob(async()=>{const s=platformSkill;if(!s)throw Error('请先选择技能');const r=await api('pipeline-skills/check',{id:s.id,code:$('platformCode').value});platformResult(['校验通过：',...r.result.map(x=>'· '+x)]);});}
async function savePlatformSkill(){return skillJob(async()=>{const s=platformSkill;if(!s)throw Error('请先选择技能');const code=$('platformCode').value;if(code.trim()===s.code.trim())throw Error('代码没有变化');
 const summary=prompt('本次修改说明（写入版本记录）',$('platformInstruction').value.trim()||'修改技能代码');if(summary===null)return;
 const r=s.kind==='pipeline'?await api('pipeline-skills/save',{id:s.id,code,summary,instruction:$('platformInstruction').value.trim()||summary}):await api('rule-skills/save',{id:s.id,code,summary});
 await reloadPlatform(s.kind,s.id);platformResult(['已保存新版本：'+r.result.time,...r.result.tests.map(x=>'· '+x),'相关结果已失效，请在工作区重新执行受影响的步骤。']);toast('技能已保存为新版本');});}
async function resetPlatformSkill(){return skillJob(async()=>{const s=platformSkill;if(!s||s.kind!=='pipeline')return;if(!s.modified)throw Error('当前已是内置版本');if(!confirm('恢复为平台内置版本？当前修改会保留在历史中，可再回退。'))return;
 const r=await api('pipeline-skills/reset',{id:s.id});await reloadPlatform('pipeline',s.id);platformResult(['已恢复内置版本：'+r.result.time,...r.result.tests.map(x=>'· '+x)]);});}
async function rollbackPlatformSkill(){return skillJob(async()=>{const s=platformSkill,version=$('platformHistory').value;if(!s)return;if(!version)throw Error('请选择历史修改');
 if(s.kind==='pipeline'){const r=await api('pipeline-skills/rollback',{version});await reloadPlatform('pipeline',s.id);platformResult(['已回退该次修改（生成新版本）：'+r.result.time,...r.result.tests.map(x=>'· '+x)]);}
 else{await api('skills/rollback',{id:version});toast('正在回退规则技能，完成后在对话中显示结果');setTimeout(()=>reloadPlatform('rule',s.id).catch(()=>{}),1500);}});}
async function draftPlatformSkill(){return skillJob(async()=>{const s=platformSkill,instruction=$('platformInstruction').value.trim();if(!s||s.kind!=='pipeline')return;if(!instruction)throw Error('请先描述修改要求');
 const r=await api('pipeline-skills/generate',{id:s.id,instruction,code:$('platformCode').value});$('platformCode').value=r.result.code;platformDirty=true;$('platformMeta').textContent='AI修改草稿，未保存';
 platformResult(['修改草稿：'+r.result.summary,...r.result.checks.map(x=>'· '+x),'草稿只做了静态检查。请检查代码，点击“校验并保存版本”后才会用样例回放并生效。']);});}
$('platformCode').addEventListener('input',()=>{platformDirty=true;$('platformMeta').textContent='有未保存的修改';});
